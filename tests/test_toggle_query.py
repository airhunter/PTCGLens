import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from overlay_input import HoverTrigger, ToggleHoverState
from overlay_app import Overlay


class ToggleQueryTest(unittest.TestCase):
    def test_release_keeps_query_enabled_and_second_press_closes(self):
        state = ToggleHoverState(.6)
        self.assertIsNone(state.step(0, True, True, (100,100)))
        self.assertIsNone(state.step(.1, False, True, (100,100)))
        self.assertTrue(state.enabled)
        self.assertEqual(state.step(.61, False, True, (100,100)), "query")
        self.assertEqual(state.step(.7, True, True, (100,100)), "dismiss")
        self.assertFalse(state.enabled)
        self.assertIsNone(state.step(1, True, True, (100,100)))
        self.assertFalse(state.enabled)

    def test_holding_key_does_not_repeat_toggle_and_can_switch_cards_after_release(self):
        state = ToggleHoverState(.6)
        state.step(0, True, True, (100,100))
        self.assertEqual(state.step(.61, True, True, (100,100)), "query")
        self.assertIsNone(state.step(1, True, True, (100,100)))
        state.target = (80,80,150,180)
        self.assertIsNone(state.step(2, False, True, (110,110)))
        self.assertIsNone(state.step(3, False, True, (200,100)))
        self.assertEqual(state.step(3.61, False, True, (200,100)), "query")

    def test_focus_loss_turns_mode_off_and_key_held_during_return_does_not_reenable(self):
        state = ToggleHoverState()
        state.step(0, True, True, (100,100))
        self.assertEqual(state.step(.1, True, False, (100,100)), "dismiss")
        self.assertFalse(state.enabled)
        state.step(1, True, True, (100,100))
        self.assertFalse(state.enabled)
        state.step(2, False, True, (100,100))
        state.step(3, True, True, (100,100))
        self.assertTrue(state.enabled)

    def test_released_keys_allow_reading_popup_without_starting_other_queries(self):
        state = ToggleHoverState(.6)
        state.step(0, True, True, (100,100))
        self.assertEqual(state.step(.61, False, True, (100,100)), "query")
        epoch = state.epoch
        self.assertIsNone(state.step(2, False, True, (300,200), over_game=False, over_popup=True))
        self.assertIsNone(state.step(4, False, True, (300,200), over_game=False, over_popup=True))
        self.assertTrue(state.enabled)
        self.assertEqual(state.epoch, epoch)

    def test_close_invalidates_epoch_and_fresh_press_can_enable_without_waiting_poll(self):
        hook = HoverTrigger(Mock(), Mock(), toggle=True)
        hook.state.step(0, True, True, (100,100))
        hook.state.step(.1, False, True, (100,100))
        epoch = hook.state.epoch
        hook.invalidate()
        self.assertFalse(hook.enabled)
        self.assertFalse(hook.retry(epoch))
        hook.state.step(1, True, True, (100,100))
        self.assertTrue(hook.enabled)
        self.assertEqual(hook.state.step(1.61, False, True, (100,100)), "query")

    def test_valid_query_survives_release_but_not_close_or_focus_loss(self):
        hook = HoverTrigger(Mock(), Mock(), toggle=True)
        hook.hwnd = 123
        backend = Mock()
        backend.shortcut_pressed.return_value = False
        backend.is_foreground.return_value = True
        hook.state.step(0, True, True, (100,100))
        hook.state.step(.1, False, True, (100,100))
        epoch = hook.state.epoch
        with patch("overlay_input.get_backend", return_value=backend):
            self.assertTrue(hook.valid(epoch))
            backend.shortcut_pressed.assert_not_called()
            backend.is_foreground.return_value = False
            self.assertFalse(hook.valid(epoch))
            backend.is_foreground.return_value = True
            hook.invalidate()
            self.assertFalse(hook.valid(epoch))

    def test_toggle_dismiss_cancels_running_and_late_queries(self):
        overlay = SimpleNamespace(mode="toggle", request_id=3, wanted=True, hold_epoch=7,
            worker=Mock(), hook=Mock(), popup=Mock())
        Overlay.dismiss(overlay)
        overlay.hook.invalidate.assert_called_once()
        overlay.worker.cancel.assert_called_once_with(4)
        Overlay.answer(overlay, {"id":3,"card":{"name_en":"late"}})
        overlay.popup.show_result.assert_not_called()

    def test_valid_uses_published_foreground_state_without_native_window_read(self):
        hook = HoverTrigger(Mock(), Mock(), toggle=True)
        hook.state.step(0, True, True, (100,100))
        epoch = hook.state.epoch
        hook.foreground = lambda: True
        backend = Mock()
        backend.is_foreground.side_effect = AssertionError('native window read on UI thread')
        with patch('overlay_input.get_backend', return_value=backend):
            self.assertTrue(hook.valid(epoch))
            hook.foreground = lambda: False
            self.assertFalse(hook.valid(epoch))
        backend.is_foreground.assert_not_called()

    def test_trigger_still_checks_point_under_card_when_no_keys_are_down(self):
        physical = {"pressed": True, "point": (100,100)}
        first, second, closed = threading.Event(), threading.Event(), threading.Event()
        points = []
        def query(x,y,epoch):
            points.append((x,y))
            (first if len(points)==1 else second).set()
        hook = HoverTrigger(query, lambda error: self.fail(error), delay=.01,
                            dismiss_callback=closed.set, toggle=True)
        hook.hwnd, hook.bbox = 123, (0,0,500,500)
        backend = Mock()
        backend.shortcut_pressed.side_effect = lambda key: physical["pressed"]
        backend.is_foreground.return_value = True
        backend.pointer_position.side_effect = lambda: physical["point"]
        backend.pointer_over_window.return_value = True
        backend.escape_pressed.return_value = False
        with patch("overlay_input.get_backend", return_value=backend):
            hook.start()
            try:
                self.assertTrue(first.wait(2))
                physical.update(pressed=False, point=(200,100))
                self.assertTrue(second.wait(2))
                physical["pressed"] = True
                self.assertTrue(closed.wait(2))
            finally:
                hook.stop(); hook.join(2)
        self.assertFalse(hook.is_alive())
        self.assertEqual(points, [(100,100),(200,100)])
