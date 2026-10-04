import unittest

import cv2
import numpy as np

from card_color import basic_energy_color_matches


class EnergyColorTest(unittest.TestCase):
    def picture(self, hue):
        hsv = np.full((140, 100, 3), (hue, 170, 180), np.uint8)
        return cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR)

    def test_partial_energy_rejects_shared_frame_with_other_color(self):
        reference = self.picture(14)
        canvas = np.zeros((180, 300, 3), np.uint8)
        canvas[100:, 50:150] = reference[:80]
        polygon = [[50,100], [150,100], [150,240], [50,240]]
        card = {"name_en": "Basic {F} Energy"}
        self.assertTrue(basic_energy_color_matches(canvas, polygon, reference, card))
        self.assertFalse(basic_energy_color_matches(canvas, polygon, self.picture(5), card))
        self.assertFalse(basic_energy_color_matches(canvas, polygon, self.picture(105), card))

    def test_small_color_variation_and_non_energy_cards(self):
        polygon = [[0,0], [100,0], [100,140], [0,140]]
        self.assertTrue(basic_energy_color_matches(self.picture(15), polygon, self.picture(14),
                                                 {"name_en": "Basic {F} Energy"}))
        self.assertTrue(basic_energy_color_matches(self.picture(15), polygon, self.picture(105),
                                                 {"name_en": "Special Energy"}))

    def test_no_visible_chromatic_evidence_remains_unconfirmed(self):
        polygon = [[0,0], [100,0], [100,140], [0,140]]
        self.assertFalse(basic_energy_color_matches(np.zeros((140,100,3), np.uint8), polygon,
                                                  self.picture(14), {"name_en":"Basic {F} Energy"}))
