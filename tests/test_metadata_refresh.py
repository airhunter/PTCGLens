import json
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from card_data import build_card_data
from cache_watch import metadata_is_stale
from local_chinese import LOCAL_CHINESE_PATH


class MetadataRefreshTest(unittest.TestCase):
    def test_failed_game_database_refresh_preserves_last_complete_data(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            index, cache, chinese = (root / name for name in ("index", "cache", "chinese"))
            for folder in (index, cache, chinese):
                folder.mkdir()
            (index / "manifest.json").write_text(json.dumps({"cards":[{"card_id":"test_en_001"}]}))
            database = cache / "card-database-test_0_en_0.json"
            database.write_text("{}")
            for kind in ("names", "attks-name", "attks-text"):
                (chinese / f"{kind}.json").write_text("{}")
            output = root / "card-data.json"
            with patch("card_data.read_game_database", return_value=[{"cardID":"test_1", "EN Card Name":"Fuecoco ex"}]):
                result = build_card_data(index, cache, chinese, output)
            self.assertEqual(result["cards"]["test_en_001"]["name_zh"], "呆火鳄ex")
            previous = output.read_bytes()
            with patch("card_data.read_game_database", side_effect=ValueError("partial cache")):
                with self.assertRaises(ValueError):
                    build_card_data(index, cache, chinese, output)
            self.assertEqual(output.read_bytes(), previous)
            with patch("card_data.read_game_database", return_value=[]):
                with self.assertRaisesRegex(RuntimeError, "保留上次资料"):
                    build_card_data(index, cache, chinese, output)
            self.assertEqual(output.read_bytes(), previous)

    def test_local_translation_update_marks_metadata_stale(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            output, supplement = root / "cards.json", root / "local.json"
            supplement.write_bytes(LOCAL_CHINESE_PATH.read_bytes())
            output.write_text("{}")
            with patch("cache_watch.LOCAL_CHINESE_PATH", supplement):
                self.assertFalse(metadata_is_stale(output, root / "game", root / "upstream"))
                future = output.stat().st_mtime_ns + 1_000_000_000
                os.utime(supplement, ns=(future, future))
                self.assertTrue(metadata_is_stale(output, root / "game", root / "upstream"))
