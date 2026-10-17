import io
import json
import threading
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError

from card_translation import TranslationCache, TranslationCancelled, readable_text
from viewer import make_handler


class AutoTranslationTest(unittest.TestCase):
    def test_transient_failures_retry_and_cache_only_complete_translation(self):
        with TemporaryDirectory() as folder:
            cache = TranslationCache(Path(folder)/"cache.json")
            with patch("card_translation.translate_segment", side_effect=[URLError("offline"),
                       HTTPError("https://example.test", 503, "busy", {}, None), "成功译文"]) as fetch, \
                 patch("card_translation.wait_for_retry"):
                self.assertEqual(cache.translate("Original rule"), ("成功译文", False))
                self.assertEqual(fetch.call_count, 3)
            self.assertEqual(cache.cached("Original rule"), "成功译文")

    def test_permanent_errors_and_exhausted_retries_do_not_cache_failure(self):
        with TemporaryDirectory() as folder:
            cache = TranslationCache(Path(folder)/"cache.json")
            for code, attempts in ((400, 1), (429, 3)):
                with self.subTest(code=code), \
                     patch("card_translation.translate_segment", side_effect=HTTPError(
                         "https://example.test", code, "failed", {}, None)) as fetch, \
                     patch("card_translation.wait_for_retry"):
                    with self.assertRaises(HTTPError):
                        cache.translate("Original rule")
                    self.assertEqual(fetch.call_count, attempts)
                    self.assertIsNone(cache.cached("Original rule"))

    def test_cancelled_query_does_not_continue_retrying(self):
        with TemporaryDirectory() as folder:
            cache = TranslationCache(Path(folder)/"cache.json")
            cancelled = threading.Event()
            def fetch(source):
                cancelled.set()
                raise URLError("offline")
            with patch("card_translation.translate_segment", side_effect=fetch) as fetch:
                with self.assertRaises(TranslationCancelled):
                    cache.translate("Original rule", cancelled=cancelled.is_set)
                self.assertEqual(fetch.call_count, 1)
            self.assertIsNone(cache.cached("Original rule"))

    def test_cached_read_is_not_blocked_by_network_translation(self):
        with TemporaryDirectory() as folder:
            cache = TranslationCache(Path(folder)/"cache.json")
            entered, release = threading.Event(), threading.Event()
            def fetch(source):
                entered.set()
                release.wait(2)
                return "测试译文"
            with patch("card_translation.translate_segment", side_effect=fetch):
                worker = threading.Thread(target=cache.translate, args=("Original rule",))
                worker.start()
                self.assertTrue(entered.wait(1))
                checked = threading.Event()
                reader = threading.Thread(target=lambda: (cache.cached("Original rule"), checked.set()))
                reader.start()
                try:
                    self.assertTrue(checked.wait(.5), "缓存读取不应等待网络")
                finally:
                    release.set()
                    worker.join(2)
                    reader.join(2)
            self.assertEqual(cache.cached("Original rule"), "测试译文")
            with patch("card_translation.translate_segment") as fetch:
                self.assertEqual(cache.translate("Original rule"), ("测试译文", True))
                fetch.assert_not_called()

    def test_original_english_energy_names_are_preserved(self):
        source = 'Attach <sprite name="fire"> Energy.<br>Then draw.'
        self.assertEqual(readable_text(source, language="en"), "Attach [fire] Energy.\nThen draw.")
        self.assertIn("[火]", readable_text(source))

    def test_web_translates_names_effects_and_rejects_stale_source(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            card = {"name_en":"Test card", "name_zh":None, "card_text_en":"Original rule", "attacks":[
                {"name_en":"Attack", "text_en":"Original attack", "name_zh":"本地名称"}]}
            data = root/"cards.json"
            data.write_text(json.dumps({"cards":{"test_en_001":card}}))
            with patch("viewer.TranslationCache") as cache:
                cache.return_value.translate.return_value = ("机器译文", False)
                Handler = make_handler(root, root, data)
                def post(field, index=None, expected_source=None):
                    request = {"card_id":"test_en_001", "field":field, "index":index}
                    if expected_source is not None:
                        request["expected_source"] = expected_source
                    body = json.dumps(request).encode()
                    handler = object.__new__(Handler)
                    handler.path = "/api/translate"
                    handler.headers = {"Content-Type":"application/json", "Content-Length":str(len(body))}
                    handler.rfile = io.BytesIO(body)
                    handler.send_json = Mock()
                    handler.do_POST()
                    return handler.send_json.call_args
                for field, index, source in (("card_name",None,"Test card"), ("card_text",None,"Original rule"),
                                             ("attack_text",0,"Original attack")):
                    self.assertEqual(post(field,index).args[0]["translation"], "机器译文")
                    cache.return_value.translate.assert_called_with(source)
                self.assertEqual(post("attack_name",0).args[0]["translation"], "本地名称")
                cache.return_value.translate.reset_mock()
                self.assertEqual(post("card_text", expected_source="Old rule").args[1], 409)
                cache.return_value.translate.assert_not_called()
                card["card_text_en"] = "A long English rule. "*100
                data.write_text(json.dumps({"cards":{"test_en_001":card}}))
                self.assertEqual(post("card_text", expected_source=card["card_text_en"]).args[0]["translation"], "机器译文")
