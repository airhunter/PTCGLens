import threading
import time
import unittest
from unittest.mock import Mock, patch

from window_monitor import GameWindowMonitor


class WindowMonitorTest(unittest.TestCase):
    def test_slow_native_window_call_cannot_block_snapshot_or_reuse_stale_bounds(self):
        monitor = GameWindowMonitor()
        entered, release = threading.Event(), threading.Event()
        def slow_window():
            entered.set()
            release.wait(3)
            return 123, (0, 0, 1280, 720), True
        backend = Mock()
        backend.window_state.side_effect = slow_window
        with patch("window_monitor.get_backend", return_value=backend):
            monitor.start()
            try:
                self.assertTrue(entered.wait(2))
                monitor.current = (123, (0, 0, 1280, 720), True, time.monotonic()-2)
                started = time.perf_counter()
                self.assertEqual(monitor.snapshot(), (None, None, False))
                self.assertLess(time.perf_counter()-started, .1)
                self.assertTrue(monitor.is_alive())
            finally:
                monitor.stop(); release.set(); monitor.join(2)
        self.assertFalse(monitor.is_alive())

    def test_fresh_snapshot_preserves_window_and_content_coordinates(self):
        monitor = GameWindowMonitor()
        monitor.current = (123, (20, 50, 1300, 770), True, time.monotonic())
        self.assertEqual(monitor.snapshot(), (123, (20, 50, 1300, 770), True))
