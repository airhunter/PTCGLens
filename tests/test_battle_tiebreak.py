import unittest
from unittest.mock import patch

import numpy as np

from battle_multi import sift_tiebreak


class ReprintTiebreakTest(unittest.TestCase):
    def run_tiebreak(self,cards):
        image=np.random.default_rng(71).integers(0,256,(150,110,3),dtype=np.uint8)
        candidates=[{"card_id":cid} for cid in ("first","reprint","other")]
        index={c["card_id"]:{"points":None,"descriptors":None,"dimensions":(110,150)} for c in candidates}
        matches=[{"fully_visible":True,"area_ratio":.9,"inliers":n} for n in (86,84,19)]
        with patch("battle_multi.candidate_result",side_effect=matches):
            return sift_tiebreak(image,candidates,index,cards)

    def test_same_rule_reprints_do_not_compete_with_each_other(self):
        first={"name_en":"Boss's Orders","card_text_en":"Switch 1\n\nPokemon."}
        reprint={**first,"card_text_en":"Switch 1\nPokemon."}
        result=self.run_tiebreak({"first":first,"reprint":reprint,"other":{"name_en":"Other","card_text_en":"Draw 2 cards."}})
        self.assertEqual(result,("first",86))

    def test_different_rules_remain_competing_candidates(self):
        first={"name_en":"Boss's Orders","card_text_en":"Switch 1 Pokemon."}
        result=self.run_tiebreak({"first":first,"reprint":{**first,"card_text_en":"Switch 2 Pokemon."}})
        self.assertEqual(result,(None,86))

    def test_missing_metadata_does_not_merge_printings(self):
        self.assertEqual(self.run_tiebreak({}),(None,86))
