import unittest
from unittest.mock import Mock, patch

import cv2
import numpy as np

from grid_cards import GridCardFinder
from overlay_app import Recognizer
from test_pointer_recognition import texture


class QueryFastPathTest(unittest.TestCase):
    def worker(self, verified, global_result=None):
        worker = Recognizer.__new__(Recognizer)
        visuals = [("known",texture(10))]
        cards = {"known":{"name_en":"Known","card_text_en":"Draw 1 card."}}
        finder = Mock()
        finder.find_at.return_value = global_result
        finder.find_in_box.return_value = None
        grid = Mock()
        grid.find_at.return_value = None
        grid.verify_box.return_value = verified
        grid.find_partial.return_value = None
        grid.check_energy_result.side_effect = lambda image,result: result
        worker.resources = (visuals,cards,{},finder)
        worker.grid_visuals = visuals
        worker.grid_finder = grid
        worker.layout = {"reference_size":[200,200],"slots":[
            {"key":"own_active","box":[50,40,150,180]}]}
        return worker,finder,grid

    def recognize(self, worker):
        frame = np.full((200,200,3),80,np.uint8)
        with patch("overlay_app.adaptive_layout",return_value=worker.layout), \
             patch("overlay_app.is_battle_screen",return_value=False):
            return worker.recognize(frame,(100,90))

    def test_strictly_verified_slot_avoids_broad_feature_search(self):
        verified = {"status":"matched","card_id":"known","verification":"grid-template"}
        worker,finder,grid = self.worker(verified)
        finder.find_at.side_effect = AssertionError("unnecessary broad feature search")
        with patch("overlay_app.recognise_slot",side_effect=AssertionError("unverified layout answer")):
            result = self.recognize(worker)
        self.assertIs(result,verified)
        grid.verify_box.assert_called_once()

    def test_ambiguous_slot_still_uses_feature_search_and_is_not_promoted(self):
        ambiguous = {"status":"tentative","possible_printings":["one","two"]}
        confirmed = {"status":"matched","card_id":"known"}
        for fallback in (None,confirmed):
            with self.subTest(fallback=fallback):
                worker,finder,grid = self.worker(ambiguous,fallback)
                result = self.recognize(worker)
                finder.find_at.assert_called_once()
                self.assertIs(result,ambiguous if fallback is None else confirmed)
                grid.verify_box.assert_called_once()

    def test_rejected_slot_continues_original_feature_search(self):
        confirmed = {"status":"matched","card_id":"known"}
        worker,finder,grid = self.worker(None,confirmed)
        result = self.recognize(worker)
        self.assertIs(result,confirmed)
        finder.find_at.assert_called_once()

    def test_layout_box_alone_cannot_identify_a_blank_card(self):
        worker,finder,_ = self.worker(None)
        worker.grid_finder = GridCardFinder(worker.resources[0],worker.resources[1])
        frame = np.full((200,200,3),80,np.uint8)
        frame[40:180,50:150] = 235
        with patch("overlay_app.adaptive_layout",return_value=worker.layout), \
             patch("overlay_app.is_battle_screen",return_value=False):
            result = worker.recognize(frame,(100,90))
        self.assertEqual(result["status"],"unknown")
        self.assertNotIn("card_id",result)
        finder.find_at.assert_called_once()

    def test_two_feature_scales_restore_the_same_card_coordinates(self):
        reference = texture(20)
        frame = np.full((500,700,3),65,np.uint8)
        frame[60:396,200:440] = cv2.resize(reference,(240,336))
        cards = {"known":{"name_en":"Known"}}
        for scale in (2,3):
            with self.subTest(scale=scale):
                finder = GridCardFinder([("known",reference)],cards)
                finder.feature_scale = scale
                result = finder.verify_box(frame,(320,180),[200,60,440,396])
                self.assertEqual(result["status"],"matched")
                self.assertEqual(result["card_id"],"known")
                for actual,expected in zip(result["box"],[200,60,440,396]):
                    self.assertAlmostEqual(actual,expected,delta=5)

    def test_mac_sampling_change_keeps_original_windows_sampling(self):
        for platform,expected in (("darwin",2),("win32",3)):
            with self.subTest(platform=platform),patch("grid_cards.sys.platform",platform):
                finder = GridCardFinder([], {})
                self.assertEqual(finder.feature_scale,expected)
