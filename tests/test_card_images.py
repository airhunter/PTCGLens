import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import cv2
import numpy as np

from card_images import (CardImageSources, chinese_printing_rank, chinese_rules_match, digest, english_rules_match,
                         same_art, tcgdex_ids)


class ImageMatchingTest(unittest.TestCase):
    def test_regular_printing_is_preferred_to_stamped_promo(self):
        regular = {"commodityCode":"CSV9.5C","details":{"collectionNumber":"179/208","rarityText":"U","regulationMarkText":"F"}}
        promo = {"commodityCode":"PROMO_26HL","details":{"collectionNumber":"SV-P","rarityText":"无标记","regulationMarkText":"G"}}
        self.assertLess(chinese_printing_rank(regular,"G"),chinese_printing_rank(promo,"G"))

    def test_chinese_name_and_hp_alone_do_not_confirm_card(self):
        game = {"name_zh":"测试卡","hp":80,"attacks":[{"kind":"attack","name_zh":"冲击",
                   "damage":"30","cost":"F","text_en":None,"text_zh":None}]}
        chinese = {"name":"测试卡","cardType":"1","details":{"hp":80,"abilityItemList":[
            {"abilityName":"冲击","abilityDamage":"30","abilityCost":"6","abilityText":"none"}]}}
        self.assertTrue(chinese_rules_match(game,chinese,{}))
        chinese["details"]["abilityItemList"][0]["abilityCost"] = "6,11"
        self.assertFalse(chinese_rules_match(game,chinese,{}))
        chinese["details"]["abilityItemList"][0]["abilityCost"] = "6"
        chinese["details"]["abilityItemList"][0]["abilityText"] = "附带其他效果"
        self.assertFalse(chinese_rules_match(game,chinese,{}))

    def test_untranslated_effect_is_not_guessed_from_same_attack_name(self):
        game = {"name_zh":"测试卡","hp":80,"attacks":[{"kind":"attack","name_zh":"冲击",
                   "damage":"30","cost":"F","text_en":"Draw 3 cards","text_zh":None}]}
        chinese = {"name":"测试卡","cardType":"1","details":{"hp":80,"abilityItemList":[
            {"abilityName":"冲击","abilityDamage":"30","abilityCost":"6","abilityText":"抽3张牌"}]}}
        self.assertFalse(chinese_rules_match(game,chinese,{}))

    def test_reviewed_trainer_bridge_checks_both_rules(self):
        game = {"name_en":"Test","name_zh":"测试","hp":0,"attacks":[],"card_text_en":"Draw 2 cards"}
        chinese = {"name":"测试","cardType":"2","details":{"ruleText":"抽2张牌|通用规则"}}
        bridges = {"Test":{"english":[digest("Draw 2 cards")],"chinese":[digest("抽2张牌")]}}
        self.assertTrue(chinese_rules_match(game,chinese,bridges))
        chinese["details"]["ruleText"] = "抽3张牌"
        self.assertFalse(chinese_rules_match(game,chinese,bridges))

    def test_english_metadata_must_match_number_and_effect(self):
        game = {"name_en":"Ultra Ball","number":"91","hp":0,"card_text_en":"Discard 2 cards"}
        remote = {"name":"Ultra Ball","localId":"091","effect":"Discard 2 cards"}
        self.assertTrue(english_rules_match(game,remote))
        remote["localId"] = "90"
        self.assertFalse(english_rules_match(game,remote))
        remote["localId"] = "091"
        remote["effect"] = "Discard 3 cards"
        self.assertFalse(english_rules_match(game,remote))
        self.assertEqual(tcgdex_ids("sv4-5_en_091"),["sv04.5-091","sv04.5-91"])
        self.assertEqual(tcgdex_ids("me1_en_076"),["me01-076","me01-76"])
        self.assertEqual(tcgdex_ids("unknown_en_001"),[])

    def test_same_art_does_not_accept_unrelated_picture(self):
        rng = np.random.default_rng(55)
        image = rng.integers(0,256,(448,320,3),dtype=np.uint8)
        self.assertTrue(same_art(image,image.copy()))
        self.assertFalse(same_art(image,rng.integers(0,256,(448,320,3),dtype=np.uint8)))


class ImageSourceCacheTest(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.root = Path(self.directory.name)
        visual = self.root / "visual"
        visual.mkdir()
        self.card = {"card_id":"test_en_001","name_en":"Test","name_zh":"测试卡","hp":0,
                     "attacks":[],"number":"1","card_text_en":"Effect"}
        _,png = cv2.imencode(".png",np.full((600,420,3),80,np.uint8))
        self.body = png.tobytes()
        (visual / "test_en_001.png").write_bytes(self.body)
        self.source = CardImageSources(self.root / "sources",self.root / "game",visual,self.root / "large")

    def tearDown(self):
        self.directory.cleanup()

    def test_cached_correspondence_is_reused_offline_and_bound_to_rules(self):
        result = {"bytes":self.body,"language":"简中","provider":"Test","source_id":"123"}
        with patch.object(self.source,"chinese",return_value=result) as chinese:
            first = self.source.resolve(self.card)
            second = self.source.resolve(self.card)
            self.assertEqual(first["bytes"],second["bytes"])
            self.assertEqual(chinese.call_count,1)
            self.card["card_text_en"] = "Different effect"
            with patch.object(self.source,"english",return_value=None),patch("card_images.large_card_bytes",return_value=b"local"):
                changed = self.source.resolve(self.card)
            self.assertEqual(changed["provider"],"游戏缓存")

    def test_network_failure_falls_back_and_does_not_retry_every_poll(self):
        with patch.object(self.source,"chinese",side_effect=OSError("offline")) as chinese,\
             patch.object(self.source,"english",side_effect=OSError("offline")),\
             patch("card_images.large_card_bytes",return_value=b"local"):
            self.assertEqual(self.source.resolve(self.card)["bytes"],b"local")
            self.assertEqual(self.source.resolve(self.card)["bytes"],b"local")
            self.assertEqual(chinese.call_count,1)

    def test_cancelled_download_does_not_delay_next_query_for_same_card(self):
        cancelled = [False]
        def abort(card, reference):
            cancelled[0] = True
            return None
        with patch.object(self.source,"chinese",side_effect=abort),\
             patch.object(self.source,"english",return_value=None),\
             patch("card_images.large_card_bytes",return_value=b"local"):
            self.source.resolve(self.card,lambda:cancelled[0])
        self.assertNotIn((self.card["card_id"],"zh"),self.source.retry_after)

    def test_local_only_mode_never_fetches_online(self):
        self.source.preference = "local"
        with patch.object(self.source,"download") as download,patch("card_images.large_card_bytes",return_value=b"local"):
            self.assertEqual(self.source.resolve(self.card)["bytes"],b"local")
            download.assert_not_called()
