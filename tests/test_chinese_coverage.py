import hashlib
import unittest

from card_data import make_card
from local_chinese import coverage_report, load_local_chinese, resolve_chinese


class ChineseCoverageTest(unittest.TestCase):
    def setUp(self):
        self.local = load_local_chinese()
        self.community = {kind: {} for kind in ("names", "attks-name", "attks-text")}

    def test_greninja_effect_and_title_are_bound_to_original_rule(self):
        original = "This attack does 30 damage to 1 of your opponent's Pokémon for each damage counter on that Pokémon. <i>(Don't apply Weakness and Resistance for Benched Pokémon.)</i>"
        row = {"EN Card Name": "Greninja ex", "EN Attack Name": "Stealthy Slash", "EN Attack Text": original}
        card = make_card("test_en_001", row, self.community, self.local)
        attack = card["attacks"][0]
        self.assertEqual(attack["name_zh"], "隐秘斩击")
        self.assertEqual(attack["text_zh_source"], "local")
        self.assertIn("伤害指示物数量×30", attack["text_zh"])
        for changed in (original.replace("30", "60"), original.replace("opponent's", "your"),
                        original.replace("damage counter", "Energy")):
            self.assertEqual(resolve_chinese(changed, "attks-text", self.community, self.local), (None, None))

    def test_community_wins_and_same_attack_title_cannot_reuse_effect(self):
        rule = "This attack does 70 damage for each Prize card you have taken."
        self.community["attks-text"][hashlib.md5(rule.encode()).hexdigest()] = "已有中文规则"
        self.assertEqual(resolve_chinese(rule, "attks-text", self.community, self.local), ("已有中文规则", "community"))
        changed = {"EN Attack Name": "Cheerful Flame", "EN Attack Text": "An entirely different rule."}
        attack = make_card("test_en_001", changed, self.community, self.local)["attacks"][0]
        self.assertEqual(attack["name_zh"], "欢乐火焰")
        self.assertIsNone(attack["text_zh"])

    def test_energy_type_and_prize_condition_are_not_shared(self):
        original = "This attack does 20 more damage for each <sprite name=\"fire\" tint=1> Energy attached to this Pokémon."
        self.assertIn("[火]", resolve_chinese(original, "attks-text", self.community, self.local)[0])
        self.assertEqual(resolve_chinese(original.replace('"fire"', '"water"'), "attks-text", self.community, self.local), (None, None))
        self.assertEqual(resolve_chinese("This attack does 70 damage for each Prize card your opponent has taken.", "attks-text", self.community, self.local), (None, None))

    def test_coverage_reports_new_untranslated_cards_and_provenance(self):
        card = make_card("test_en_001", {"EN Card Name": "Fuecoco ex", "EN Attack Name": "Cheerful Flame",
                         "EN Attack Text": "This attack does 70 damage for each Prize card you have taken."}, self.community, self.local)
        unknown = make_card("test_en_002", {"EN Card Name": "Unseen card", "EN Attack Name": "Unknown attack",
                            "EN Attack Text": "Unknown rule"}, self.community, self.local)
        report = coverage_report({"a": card, "b": unknown})
        self.assertEqual(report["scope"], "currently_indexed_cards")
        for kind in ("card_names", "attack_names", "effects"):
            field = report["fields"][kind]
            self.assertEqual((field["total"], field["chinese"], field["sources"]), (2, 1, {"local": 1}))
            self.assertEqual(field["missing"][0]["card_id"], "b")
