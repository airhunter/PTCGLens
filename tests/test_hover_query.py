import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from overlay_input import HoverState
from overlay_app import Overlay, Recognizer
from latest_jobs import LatestJobQueue


class HoverStateTest(unittest.TestCase):
    def setUp(self):
        self.state = HoverState(.6)

    def step(self, now, point=(100, 100), pressed=True, active=True, **kwargs):
        return self.state.step(now, pressed, active, point, **kwargs)

    def test_dwell_tolerates_jitter_and_fires_only_once(self):
        self.assertIsNone(self.step(0))
        self.assertIsNone(self.step(.4, (102, 101)))
        self.assertEqual(self.step(.61, (101, 102)), "query")
        self.assertIsNone(self.step(2))

    def test_moving_to_another_card_restarts_timer(self):
        self.step(0)
        self.assertIsNone(self.step(.5, (200, 100)))
        self.assertIsNone(self.step(1, (201, 101)))
        self.assertEqual(self.step(1.11, (200, 100)), "query")

    def test_release_before_dwell_cancels_and_invalidates_epoch(self):
        self.step(0)
        epoch = self.state.epoch
        self.assertEqual(self.step(.3, pressed=False), "dismiss")
        self.assertGreater(self.state.epoch, epoch)
        self.assertIsNone(self.step(1, pressed=False))
        self.step(2)
        self.assertEqual(self.step(2.61), "query")

    def test_focus_loss_requires_release_before_reentering(self):
        self.step(0)
        self.assertEqual(self.step(.7), "query")
        self.assertEqual(self.step(.8, active=False), "dismiss")
        self.assertIsNone(self.step(2, active=True))
        self.assertFalse(self.state.held)
        self.step(2.1, pressed=False)
        self.step(2.2)
        self.assertEqual(self.step(2.81), "query")

    def test_hold_started_outside_game_requires_fresh_press(self):
        self.step(0, active=False)
        self.assertIsNone(self.step(1))
        self.assertFalse(self.state.held)

    def test_known_card_bounds_avoid_repeated_queries_and_allow_switching(self):
        self.step(0)
        self.step(.61)
        self.state.target = (80, 80, 160, 180)
        self.assertIsNone(self.step(2, (150, 170)))
        self.step(2.1, (200, 170))
        self.assertEqual(self.step(2.71, (200, 170)), "query")

    def test_popup_scroll_does_not_start_a_new_query_but_release_closes(self):
        self.step(0)
        self.step(.61)
        self.assertIsNone(self.step(2, (300, 200), over_game=False, over_popup=True))
        self.assertIsNone(self.step(4, (300, 200), over_game=False, over_popup=True))
        self.assertEqual(self.step(4.1, pressed=False, over_game=False, over_popup=True), "dismiss")

    def test_leaving_game_resets_dwell(self):
        self.step(0)
        self.step(.4, over_game=False)
        self.assertIsNone(self.step(1))
        self.assertEqual(self.step(1.61), "query")


class QueryDismissalTest(unittest.TestCase):
    def overlay(self):
        return SimpleNamespace(request_id=3, wanted=True, hold_epoch=4, mode="hover", popup_box=(0,0,1,1),
            worker=SimpleNamespace(cancel=Mock()), hook=SimpleNamespace(invalidate=Mock(), valid=Mock(return_value=False)),
            popup=SimpleNamespace(request_id=3, hide=Mock(), show_result=Mock()))

    def test_release_invalidates_recognition_translation_and_image(self):
        overlay = self.overlay()
        Overlay.dismiss(overlay)
        self.assertFalse(overlay.wanted)
        self.assertEqual(overlay.request_id, 4)
        self.assertEqual(overlay.popup.request_id, 4)
        overlay.worker.cancel.assert_called_once_with(4)
        overlay.hook.invalidate.assert_called_once()
        overlay.popup.hide.assert_called_once()
        Overlay.answer(overlay, {"id":3,"card":{}})
        overlay.popup.show_result.assert_not_called()

    def test_queued_hover_query_is_dropped_after_release(self):
        overlay = self.overlay()
        Overlay.query(overlay, {"hold_epoch":4})
        self.assertEqual(overlay.request_id, 3)
        overlay.hook.valid.assert_called_once_with(4)

    def test_late_answer_is_rejected_even_before_release_signal_reaches_ui(self):
        overlay = self.overlay()
        overlay.dismiss = Mock()
        Overlay.answer(overlay, {"id":3,"card":{}})
        overlay.dismiss.assert_called_once()
        overlay.popup.show_result.assert_not_called()

    def test_cancel_releases_pending_frames_and_background_jobs(self):
        import queue
        worker = SimpleNamespace(latest_request=1, query_card=(1, {}), jobs=LatestJobQueue(),
                                  art_jobs=LatestJobQueue(), translation_jobs=queue.Queue())
        worker.jobs.put((1, {"frame": object()}))
        worker.art_jobs.put((1, "old"))
        worker.translation_jobs.put((1, 0, "old"))
        Recognizer.cancel(worker, 2)
        self.assertEqual(worker.latest_request, 2)
        self.assertIsNone(worker.query_card)
        self.assertTrue(worker.jobs.empty())
        self.assertTrue(worker.art_jobs.empty())
        self.assertTrue(worker.translation_jobs.empty())
        worker.art_jobs.put((1, "late"))
        self.assertTrue(worker.art_jobs.empty())
