import unittest

import cv2
import numpy as np

from battle_multi import detect_hand_slots


class HandScaleTest(unittest.TestCase):
    def test_five_cards_keep_separate_slots_at_720p(self):
        image=np.full((1080,1920,3),35,dtype=np.uint8)
        for x in (491,680,869,1058,1247):
            image[950:1080,x:x+177]=210
        for scale in (.667,1.):
            scaled=cv2.resize(image,None,fx=scale,fy=scale,interpolation=cv2.INTER_AREA)
            slots=detect_hand_slots(scaled,[1910,1075])
            self.assertEqual(len(slots),5)
            self.assertTrue(all(slot['box'][2]-slot['box'][0]<210 for slot in slots))
