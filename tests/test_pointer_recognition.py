import unittest

import cv2
import numpy as np

from global_cards import LargeCardFinder


def texture(seed):
    rng = np.random.default_rng(seed)
    image = np.full((252, 180, 3), 235, dtype=np.uint8)
    for _ in range(160):
        center = tuple(int(v) for v in rng.integers([5, 5], [175, 247]))
        color = tuple(int(v) for v in rng.integers(0, 210, 3))
        cv2.circle(image, center, int(rng.integers(2, 9)), color, -1)
    return image


class PointerRecognitionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.images = [texture(10), texture(20)]
        records = []
        for number, image in enumerate(cls.images):
            points, descriptors = cv2.SIFT_create().detectAndCompute(cv2.cvtColor(image, cv2.COLOR_BGR2GRAY), None)
            records.append({"card_id": str(number), "points": np.float32([point.pt for point in points]),
                            "descriptors": descriptors, "dimensions": (180, 252)})
        cls.finder = LargeCardFinder(records)

    def scene(self):
        canvas = np.full((650, 1100, 3), 70, dtype=np.uint8)
        canvas[100:352, 80:260] = self.images[0]
        canvas[10:514, 500:860] = cv2.resize(self.images[1], (360, 504))
        return canvas

    def test_selects_pointed_card_instead_of_larger_nearby_card(self):
        result = self.finder.find_at(self.scene(), {}, (160, 180))
        self.assertEqual(result["status"], "matched")
        self.assertEqual(result["card_id"], "0")
        self.assertLess(abs(result["box"][0]-80), 3)

    def test_clicking_background_does_not_select_nearby_card(self):
        self.assertIsNone(self.finder.find_at(self.scene(), {}, (440, 180)))

    def test_partially_visible_card_can_match_without_complete_bottom(self):
        canvas = self.scene()
        canvas[550:650, 80:260] = self.images[0][:100]
        result = self.finder.find_at(canvas, {}, (160, 590))
        self.assertEqual(result["card_id"], "0")
        self.assertGreater(result["box"][3], canvas.shape[0])

    def test_same_card_in_preview_and_hand_selects_hand_instance(self):
        canvas = np.full((650, 1100, 3), 70, dtype=np.uint8)
        canvas[30:534, 80:440] = cv2.resize(self.images[0], (360, 504))
        canvas[550:650, 150:330] = self.images[0][:100]
        result = self.finder.find_at(canvas, {}, (240, 590))
        self.assertEqual(result["card_id"], "0")
        self.assertGreater(result["box"][1], 540)


if __name__ == "__main__":
    unittest.main()
