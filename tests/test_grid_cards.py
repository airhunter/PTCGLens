import unittest
from unittest.mock import patch

import cv2
import numpy as np

from grid_cards import GridCardFinder, pointed_grid_box, partial_edge_boxes
from battle_multi import visual_score
from test_pointer_recognition import texture


class GridRecognitionTest(unittest.TestCase):
    def scene(self):
        canvas=np.full((650,1100,3),205,dtype=np.uint8)
        refs=[cv2.resize(texture(seed),(160,224)) for seed in (10,20)]
        for row in range(2):
            for col in range(3):
                x,y=100+col*120,100+row*155
                cv2.rectangle(canvas,(x-2,y-2),(x+92,y+128),(65,65,65),2)
                canvas[y:y+126,x:x+90]=cv2.resize(refs[(row+col)%2],(90,126),interpolation=cv2.INTER_AREA)
        return canvas,refs

    def test_grid_card_selects_correct_picture_at_two_sizes(self):
        canvas,refs=self.scene()
        cards={"first":{"name_en":"First","card_text_en":"Draw 1 card."},
               "second":{"name_en":"Second","card_text_en":"Draw 2 cards."}}
        finder=GridCardFinder(list(zip(cards,refs)),cards)
        for scale in (.8,1.):
            image=cv2.resize(canvas,None,fx=scale,fy=scale,interpolation=cv2.INTER_AREA)
            result=finder.find_at(image,(round(265*scale),round(145*scale)))
            self.assertIsNotNone(result)
            self.assertEqual(result["status"],"matched")
            self.assertEqual(result["card_id"],"second")
            self.assertGreaterEqual(result["sift_inliers"],25)
            self.assertAlmostEqual(result["box"][0],220*scale,delta=5)

    def test_shared_art_with_different_effects_is_unconfirmed(self):
        canvas,refs=self.scene()
        cards={"first":{"name_en":"First","card_text_en":"Draw 1 card."},
               "reprint":{"name_en":"First","card_text_en":"Draw 2 cards."}}
        finder=GridCardFinder([("first",refs[0]),("reprint",refs[0])],cards)
        result=finder.find_at(canvas,(145,145))
        self.assertEqual(result["status"],"tentative")
        self.assertEqual(result["possible_printings"],["first","reprint"])

    def test_background_or_isolated_rectangle_is_not_a_grid_card(self):
        canvas,refs=self.scene()
        self.assertIsNone(pointed_grid_box(canvas,(700,500)))
        isolated=np.full_like(canvas,205)
        isolated[100:226,100:190]=cv2.resize(refs[0],(90,126))
        self.assertIsNone(pointed_grid_box(isolated,(145,145)))

    def test_broken_outline_uses_observed_grid_axes(self):
        canvas,_=self.scene()
        boxes=[(100,100,90,126),(220,100,90,126),(340,100,90,126),
               (100,255,90,126),(340,255,90,126)]
        with patch("grid_cards.card_rectangles",return_value=boxes):
            self.assertEqual(pointed_grid_box(canvas,(265,300)),[220,255,310,381])
            self.assertIsNone(pointed_grid_box(canvas,(480,300)))

    def test_single_visible_grid_row_restores_width_joined_to_scroll_control(self):
        canvas,_ = self.scene()
        boxes = [(100,100,90,126),(220,100,90,126),(340,100,90,126),(460,100,103,126)]
        with patch("grid_cards.card_rectangles",return_value=boxes):
            self.assertEqual(pointed_grid_box(canvas,(505,145)),[460,100,550,226])
            self.assertIsNone(pointed_grid_box(canvas,(558,145)))

    def test_partial_card_in_known_box_retains_full_card_geometry(self):
        canvas,refs=self.scene()
        cards={"first":{"name_en":"First"},"second":{"name_en":"Second"}}
        finder=GridCardFinder(list(zip(cards,refs)),cards)
        result=finder.verify_box(canvas[:318],(145,290),[100,255,190,381])
        self.assertIsNotNone(result)
        self.assertEqual(result["status"],"matched")
        self.assertEqual(result["card_id"],"second")
        self.assertGreater(result["box"][3],318)

    def test_partial_card_without_grid_recovers_bottom_edge_box(self):
        _, refs = self.scene()
        canvas = np.full((300, 600, 3), 65, np.uint8)
        canvas[220:, 250:340] = cv2.resize(refs[1], (90, 126))[:80]
        finder = GridCardFinder([("second", refs[1])], {"second": {"name_en": "Second"}})
        result = finder.find_partial(canvas, (290, 250))
        self.assertIsNotNone(result)
        self.assertEqual(result["card_id"], "second")
        self.assertGreater(result["box"][3], canvas.shape[0])

    def test_bottom_edge_geometry_does_not_confirm_blank_card(self):
        _, refs = self.scene()
        canvas = np.full((300, 600, 3), 65, np.uint8)
        canvas[220:, 250:340] = 230
        self.assertTrue(partial_edge_boxes(canvas, (290, 250)))
        finder = GridCardFinder([("second", refs[1])], {"second": {"name_en": "Second"}})
        self.assertIsNone(finder.find_partial(canvas, (290, 250)))

    def test_shared_frame_and_text_cannot_confirm_other_illustration(self):
        refs = [texture(seed) for seed in (10,20)]
        other = refs[0].copy()
        other[40:152,15:165] = refs[1][40:152,15:165]
        canvas = np.full((300,600,3), 65, np.uint8)
        canvas[40:264,200:360] = cv2.resize(refs[0],(160,224))
        cards = {"first":{"name_en":"First"},"other":{"name_en":"Other"}}
        finder = GridCardFinder([("first",refs[0]),("other",other)],cards)
        result = finder.verify_box(canvas,(280,100),[200,40,360,264])
        self.assertEqual(result["status"],"matched")
        self.assertEqual(result["possible_printings"],["first"])

    def test_large_isolated_preview_is_verified_at_bounded_sampling_size(self):
        ref = texture(10)
        canvas = np.full((800,1100,3),65,np.uint8)
        canvas[60:732,250:730] = cv2.resize(ref,(480,672))
        finder = GridCardFinder([("first",ref)],{"first":{"name_en":"First"}})
        result = finder.find_at(canvas,(480,280))
        self.assertIsNotNone(result)
        self.assertEqual(result["status"],"matched")
        self.assertAlmostEqual(result["box"][0],250,delta=8)
        self.assertTrue(all(key[1]<=220 for key in finder.features))

    def test_cached_ranking_matches_color_correlation_for_partial_and_full_cards(self):
        refs=[texture(seed) for seed in (10,20,30)]
        finder=GridCardFinder([(str(i),ref) for i,ref in enumerate(refs)],{})
        for width,height in ((96,134),(64,70),(96,100),(75,55),(96,120)):
            observed=cv2.resize(refs[1],(width,round(width*1.4)))[:height]
            actual=dict((cid,score) for score,cid in finder.rank_candidates(observed,width))
            for i,ref in enumerate(refs):
                self.assertAlmostEqual(actual[str(i)],visual_score(observed,ref,[0.,1.]),delta=2e-5)
            self.assertEqual(max(actual,key=actual.get),'1')
        self.assertLessEqual(len(finder.ranking_templates),4)
        # 模板可复用，但同一个区域的新卡面必须产生新的候选排序。
        new=cv2.resize(refs[0],(96,134))[:120]
        self.assertEqual(finder.rank_candidates(new,96)[0][1],'0')

    def test_missing_header_outline_restores_card_from_observed_bottom(self):
        canvas,refs=self.scene()
        finder=GridCardFinder([('first',refs[0])],{'first':{'name_en':'First'}})
        with patch('grid_cards.card_rectangles',return_value=[(100,121,90,105)]):
            result=finder.find_at(canvas,(145,150))
        self.assertIsNotNone(result)
        self.assertEqual(result['status'],'matched')
        self.assertEqual(result['card_id'],'first')
        self.assertAlmostEqual(result['box'][1],100,delta=5)
        self.assertGreaterEqual(result['sift_inliers'],25)

    def test_restored_header_geometry_does_not_confirm_plain_rectangle(self):
        canvas,refs=self.scene()
        canvas[100:226,100:190]=230
        finder=GridCardFinder([('first',refs[0])],{'first':{'name_en':'First'}})
        with patch('grid_cards.card_rectangles',return_value=[(100,121,90,105)]):
            self.assertIsNone(finder.find_at(canvas,(145,150)))
