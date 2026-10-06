import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import cv2
import numpy as np

from global_cards import LargeCardFinder
from grid_cards import GridCardFinder
from overlay_app import Recognizer
from test_pointer_recognition import texture


class ProjectedCardTest(unittest.TestCase):
    def scene(self, seed=10):
        reference = texture(seed)
        polygon = np.float32([[95,95],[300,70],[360,430],[130,455]])
        source = np.float32([[0,0],[180,0],[180,252],[0,252]])
        transform = cv2.getPerspectiveTransform(source,polygon)
        image = np.full((650,1100,3),70,np.uint8)
        warped = cv2.warpPerspective(reference,transform,(1100,650))
        mask = cv2.warpPerspective(np.full((252,180),255,np.uint8),transform,(1100,650)) > 0
        image[mask] = warped[mask]
        # 遮挡部分规则区，但保留分散的插画证据。
        image[325:390,180:350] = (180,180,180)
        return reference,image,polygon

    def test_perspective_card_with_partial_occlusion_is_verified_and_restored(self):
        reference,image,polygon = self.scene()
        finder = GridCardFinder([("card",reference)],{"card":{"name_en":"Card"}})
        result = finder.verify_projected(image,(210,210),polygon)
        self.assertEqual(result["status"],"matched")
        self.assertEqual(result["card_id"],"card")
        self.assertGreaterEqual(result["sift_inliers"],25)
        self.assertEqual(result["verification"],"perspective-template")
        self.assertLess(np.max(np.linalg.norm(np.float32(result["polygon"])-polygon,axis=1)),5)

    def test_same_picture_with_different_rules_stays_unconfirmed(self):
        reference,image,polygon = self.scene()
        cards = {"first":{"name_en":"Card","card_text_en":"Draw 1 card."},
                 "other":{"name_en":"Card","card_text_en":"Draw 3 cards."}}
        finder = GridCardFinder([(key,reference) for key in cards],cards)
        result = finder.verify_projected(image,(210,210),polygon)
        self.assertEqual(result["status"],"tentative")
        self.assertEqual(result["possible_printings"],["first","other"])

    def test_click_outside_slanted_card_does_not_match_its_bounding_box(self):
        reference,image,polygon = self.scene()
        finder = GridCardFinder([("card",reference)],{"card":{"name_en":"Card"}})
        self.assertIsNone(finder.verify_projected(image,(105,400),polygon))

    def test_wrong_picture_is_not_confirmed_by_geometry(self):
        _,image,polygon = self.scene(seed=20)
        finder = GridCardFinder([("card",texture(10))],{"card":{"name_en":"Card"}})
        self.assertIsNone(finder.verify_projected(image,(210,210),polygon))

    def test_weak_global_candidate_never_exposes_card_identity_without_verification(self):
        reference,image,_ = self.scene()
        sift = cv2.SIFT_create()
        points,descriptors = sift.detectAndCompute(cv2.cvtColor(reference,cv2.COLOR_BGR2GRAY),None)
        record = {"card_id":"card", "dimensions":(180,252),
                  "points":np.float32([p.pt for p in points]), "descriptors":descriptors}
        finder = LargeCardFinder([record])
        points,descriptors = finder.sift.detectAndCompute(cv2.cvtColor(image,cv2.COLOR_BGR2GRAY),None)
        result = finder.pointed_geometry(points,descriptors,{0:[None]*4},image.shape[:2],(210.,210.))
        self.assertIsNotNone(result)
        self.assertEqual(result["status"],"tentative")
        self.assertEqual(result["verification"],"geometry-only")
        self.assertNotIn("card_id",result)

    def test_failed_geometry_proposal_keeps_existing_partial_card_fallback(self):
        worker = object.__new__(Recognizer)
        visuals = []
        proposal = {"status":"tentative","verification":"geometry-only",
                    "polygon":[[10,10],[50,10],[50,70],[10,70]]}
        worker.resources = (visuals,{},None,SimpleNamespace(find_at=lambda *args:proposal))
        worker.layout = {"reference_size":[100,100],"slots":[]}
        worker.grid_visuals = visuals
        worker.grid_finder = Mock()
        worker.grid_finder.find_at.return_value = None
        worker.grid_finder.check_energy_result.side_effect = lambda image,result:result
        worker.grid_finder.verify_projected.return_value = None
        worker.grid_finder.find_partial.return_value = {"status":"matched","card_id":"partial"}
        with patch("overlay_app.is_battle_screen",return_value=False):
            result = worker.recognize(np.zeros((100,100,3),np.uint8),(30,30))
        self.assertEqual(result["card_id"],"partial")
        worker.grid_finder.find_partial.assert_called_once()

    def test_layout_proposal_recovers_banner_occlusion_without_scene_detection(self):
        reference = texture(10)
        image = np.full((400,600,3),70,np.uint8)
        image[150:290,230:330] = cv2.resize(reference,(100,140))
        image[145:166,120:440] = 230
        worker = object.__new__(Recognizer)
        visuals = [('card',reference)]
        cards = {'card':{'name_en':'Card'}}
        worker.resources = (visuals,cards,None,SimpleNamespace(find_at=lambda *args:None))
        worker.layout = {'reference_size':[600,400],'slots':[
            {'key':'own_bench_1','box':[230,150,330,290]}]}
        worker.grid_visuals = visuals
        worker.grid_finder = GridCardFinder(visuals,cards)
        with patch('overlay_app.is_battle_screen',return_value=False):
            result = worker.recognize(image,(280,205))
            self.assertEqual(result['status'],'matched')
            self.assertEqual(result['card_id'],'card')
            self.assertGreaterEqual(result['sift_inliers'],25)
            image[150:290,230:330] = 230
            self.assertEqual(worker.recognize(image,(280,205))['status'],'unknown')


if __name__ == "__main__":
    unittest.main()
