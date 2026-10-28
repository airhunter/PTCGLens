import json
import threading
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import Mock, patch

from overlay_app import Overlay, Recognizer
from overlay_input import HoverTrigger


class QueryRecoveryTest(unittest.TestCase):
    def test_delayed_first_frame_rearms_current_query_once_after_capture_retry_exhaustion(self):
        hook = SimpleNamespace(valid=Mock(return_value=True),retry=Mock(return_value=True))
        overlay = SimpleNamespace(waiting_for_capture=True,wanted=True,mode='toggle',hold_epoch=7,
            hook=hook,current_status='查询未完成：暂未收到游戏画面',status_dialog=None,
            capture_retries=2,capture_retry_at=None,status=Mock())
        Overlay.capture_state(overlay,'正在接收游戏画面')
        Overlay.capture_state(overlay,'正在接收游戏画面')
        hook.retry.assert_called_once_with(7)
        self.assertFalse(overlay.waiting_for_capture)
        self.assertEqual(overlay.capture_retries,0)

    def test_delayed_first_frame_cannot_reopen_closed_or_invalid_hover(self):
        for wanted,valid in [(False,True),(True,False)]:
            overlay = SimpleNamespace(waiting_for_capture=True,wanted=wanted,mode='toggle',hold_epoch=7,
                hook=SimpleNamespace(valid=Mock(return_value=valid),retry=Mock()),
                current_status='waiting',status_dialog=None,status=Mock())
            Overlay.capture_state(overlay,'正在接收游戏画面')
            overlay.hook.retry.assert_not_called()

    def test_cache_retry_rearms_same_point_but_cannot_rearm_released_hold(self):
        hook = HoverTrigger(Mock(), Mock())
        hook.state.step(0, True, True, (100, 100))
        self.assertEqual(hook.state.step(.7, True, True, (100, 100)), "query")
        epoch = hook.state.epoch
        hook.set_target((80, 80, 140, 140))
        self.assertTrue(hook.retry(epoch))
        self.assertIsNone(hook.state.step(1, True, True, (100, 100)))
        self.assertEqual(hook.state.step(1.7, True, True, (100, 100)), "query")
        hook.state.step(2, False, True, (100, 100))
        self.assertFalse(hook.retry(epoch))

    def test_new_index_retries_only_current_visible_unconfirmed_query_once(self):
        hook = SimpleNamespace(valid=Mock(return_value=True), retry=Mock(return_value=True))
        overlay = SimpleNamespace(waiting_for_cache=True, wanted=True, mode="hover", hold_epoch=7,
                                  request_id=3, hook=hook, popup=Mock())
        Overlay.cache_checked(overlay, {"added": 1})
        Overlay.cache_checked(overlay, {"added": 1})
        hook.retry.assert_called_once_with(7)
        self.assertFalse(overlay.waiting_for_cache)
        overlay.popup.loading.assert_called_once_with(3, True)
        overlay.waiting_for_cache = True
        overlay.wanted = False
        Overlay.cache_checked(overlay, {"added": 2})
        hook.retry.assert_called_once()

    def test_cache_does_not_reopen_popup_when_key_release_has_not_reached_ui(self):
        overlay = SimpleNamespace(waiting_for_cache=True, wanted=True, mode="hover", hold_epoch=7,
            request_id=3, hook=SimpleNamespace(valid=Mock(return_value=False), retry=Mock()), popup=Mock())
        Overlay.cache_checked(overlay, {"added": 1})
        overlay.hook.retry.assert_not_called()
        overlay.popup.loading.assert_not_called()

    def test_initial_capture_errors_have_bounded_automatic_retries(self):
        overlay = SimpleNamespace(request_id=3, wanted=True, mode="hover", hold_epoch=7,
            capture_retries=0, worker=SimpleNamespace(index_revision=0),
            hook=SimpleNamespace(valid=Mock(return_value=True)), popup=Mock(), reposition=Mock(), status=Mock())
        for attempt in range(3):
            overlay.capture_retry_at = None
            Overlay.answer(overlay, {"id": 3, "error": "暂未收到游戏画面", "retry_capture": True})
            self.assertEqual(overlay.capture_retries, min(attempt+1, 2))
            self.assertEqual(overlay.capture_retry_at is not None, attempt < 2)
            self.assertIn("暂未收到游戏画面",overlay.status.call_args.args[0])

    def test_first_start_builds_index_automatically(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            worker = Recognizer(SimpleNamespace(status=Mock()), SimpleNamespace(
                cache_root=root/"cache", game_cache=root/"game", translation_root=root/"zh"))
            def refresh(*args):
                (root/"output/index").mkdir(parents=True)
                (root/"output/index/manifest.json").write_text(json.dumps({"cards": [{"card_id": "test"}]}))
                (root/"output/card-data.json").write_text(json.dumps({"cards": {"test": {}}}))
                return {"cards": 1}
            with patch("overlay_app.ROOT", root), patch("overlay_app.refresh_once", side_effect=refresh) as build, \
                 patch("overlay_app.metadata_is_stale", return_value=False):
                worker.prepare_metadata()
            build.assert_called_once()
            self.assertTrue((root/"output/card-data.json").is_file())

    def test_first_start_without_cache_waits_with_actionable_status_and_can_exit(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            worker = Recognizer(SimpleNamespace(status=Mock()), SimpleNamespace(
                cache_root=root/"cache", game_cache=root/"game", translation_root=root/"zh"))
            with patch("overlay_app.ROOT", root), \
                 patch("overlay_app.refresh_once", side_effect=ValueError("cache missing")), \
                 patch.object(worker.stop_event, "wait", return_value=True):
                worker.prepare_metadata()
            messages = [call.args[0] for call in worker.events.status.emit.call_args_list]
            self.assertTrue(any("请启动游戏" in text and "缓存目录" in text for text in messages))
            self.assertFalse((root/"output/index/manifest.json").exists())

    def test_first_start_does_not_treat_empty_english_metadata_as_ready(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            (root/"output/index").mkdir(parents=True)
            (root/"output/index/manifest.json").write_text(json.dumps({"cards": [{"card_id": "test"}]}))
            data = root/"output/card-data.json"
            data.write_text(json.dumps({"cards": {}}))
            worker = Recognizer(SimpleNamespace(status=Mock()), SimpleNamespace(
                cache_root=root/"cache", game_cache=root/"game", translation_root=root/"zh"))
            def refresh(*args):
                data.write_text(json.dumps({"cards": {"test": {"name_en": "Test card"}}}))
                return {"cards": 1}
            with patch("overlay_app.ROOT", root), patch("overlay_app.refresh_once", side_effect=refresh) as build, \
                 patch("overlay_app.metadata_is_stale", return_value=False):
                worker.prepare_metadata()
            build.assert_called_once()
