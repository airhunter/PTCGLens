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

    def refine_shared_printings(self,cards):
        # 全库阶段只有弱候选；逐卡复核能找回被相似印次分走的匹配点。
        original = self.finder.records[0]
        records = [original,{**original,"card_id":"reprint"}]
        finder = LargeCardFinder(records)
        canvas = np.full((650,1100,3),70,dtype=np.uint8)
        canvas[30:534,80:440] = cv2.resize(self.images[0],(360,504))
        canvas[550:650,150:330] = self.images[0][:100]
        points,descriptors = finder.sift.detectAndCompute(cv2.cvtColor(canvas,cv2.COLOR_BGR2GRAY),None)
        seed = {"status":"tentative","card_id":"0","sift_inliers":21,
                "box":[150,550,330,802],"polygon":[[150,550],[330,550],[330,802],[150,802]]}
        return finder.refine_pointed(seed,points,descriptors,
                                   {0:[None]*21,1:[None]*14},cards,(240.,590.))

    def test_weak_hand_candidate_is_verified_despite_shared_printings(self):
        card = {"name_en":"Test","hp":100,"card_text_en":"Same rule","attacks":[]}
        result = self.refine_shared_printings({"0":card,"reprint":dict(card)})
        self.assertEqual(result["status"],"matched")
        self.assertEqual(result["possible_printings"],["0","reprint"])
        self.assertGreaterEqual(result["sift_inliers"],25)
        self.assertGreater(result["box"][1],540)  # 没有跳到更大的特写。

    def test_shared_art_with_different_rules_remains_unconfirmed(self):
        result = self.refine_shared_printings({"0":{"card_text_en":"Draw one card"},
                                               "reprint":{"card_text_en":"Draw three cards"}})
        self.assertEqual(result["status"],"tentative")

    def test_local_chinese_coverage_does_not_change_rule_identity(self):
        english = {"hp":100,"attacks":[{"name_en":"Attack","text_en":"Rule","damage":"30"}]}
        chinese = {"hp":100,"name_zh":"测试卡","attacks":[{"name_en":"Attack","text_en":"Rule",
                              "damage":"30","name_zh":"招式","text_zh":"效果"}]}
        self.assertEqual(LargeCardFinder.rules_signature(english),LargeCardFinder.rules_signature(chinese))

    def test_rule_identity_ignores_layout_but_preserves_damage_and_cost(self):
        card = {"card_text_en":"Discard 2 cards.\n\nDraw 3 cards.","attacks":[
            {"kind":"attack","name_en":"Attack","text_en":"Draw 1 card.","damage":"30+","cost":"FC"}]}
        reprint = {**card,"card_text_en":"Discard 2 cards.\nDraw 3 cards."}
        self.assertEqual(LargeCardFinder.rules_signature(card),LargeCardFinder.rules_signature(reprint))
        for replacement in ({"damage":"30"},{"cost":"F"},{"text_en":"Draw 2 cards."}):
            different = {**card,"attacks":[{**card["attacks"][0],**replacement}]}
            self.assertNotEqual(LargeCardFinder.rules_signature(card),LargeCardFinder.rules_signature(different))

    def test_basic_energy_types_are_not_equivalent_without_effect_text(self):
        fighting={"name_en":"Basic {F} Energy","hp":0,"card_text_en":None,"attacks":[]}
        water={**fighting,"name_en":"Basic {W} Energy"}
        self.assertNotEqual(LargeCardFinder.rules_signature(fighting),LargeCardFinder.rules_signature(water))


if __name__ == "__main__":
    unittest.main()
