import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from overlay_app import Overlay
from query_control import QueryCancelled, QueryTimedOut, check_query, query_scope


class QueryControlTest(unittest.TestCase):
    def test_cancel_stops_current_query_and_scope_is_not_left_on_next_query(self):
        cancelled = threading.Event()
        with self.assertRaises(QueryCancelled), query_scope(cancelled.is_set, time.perf_counter()+2):
            cancelled.set()
            check_query()
        check_query()
        with query_scope(lambda: False, time.perf_counter()+2):
            check_query()

    def test_budget_expires_and_is_local_to_recognition_thread(self):
        errors = []
        with query_scope(lambda: False, time.perf_counter()+2):
            def background():
                try:
                    with query_scope(lambda: False, time.perf_counter()-1):
                        check_query()
                except QueryTimedOut:
                    errors.append("timed out")
            thread = threading.Thread(target=background)
            thread.start(); thread.join(2)
            self.assertFalse(thread.is_alive())
            check_query()
        self.assertEqual(errors, ["timed out"])

    def test_ui_timeout_cancels_once_and_ignores_late_result(self):
        overlay = SimpleNamespace(wanted=True, recognition_pending=True, query_started=10,
            request_id=7, worker=Mock(), popup=Mock(), waiting_for_cache=True,
            capture_retry_at=20, reposition=Mock(), status=Mock())
        with patch("overlay_app.time.perf_counter", return_value=15.1):
            Overlay.query_progress(overlay)
            Overlay.query_progress(overlay)
        self.assertFalse(overlay.recognition_pending)
        self.assertEqual(overlay.request_id, 8)
        overlay.worker.cancel.assert_called_once_with(8)
        self.assertIn("用时过长", overlay.popup.show_result.call_args.args[0]["error"])
        Overlay.answer(overlay, {"id": 7, "card": {"name_en": "late"}})
        overlay.popup.show_result.assert_called_once()

    def test_loading_progress_does_not_cancel_or_rebuild_contents(self):
        overlay = SimpleNamespace(wanted=True, recognition_pending=True, query_started=10,
                                  popup=Mock(), worker=Mock())
        with patch("overlay_app.time.perf_counter", return_value=11.2):
            Overlay.query_progress(overlay)
        overlay.popup.loading_status.assert_called_once()
        overlay.popup.show_result.assert_not_called()
        overlay.worker.cancel.assert_not_called()
