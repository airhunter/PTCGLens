import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np

from overlay_app import Overlay


class ClickCaptureTest(unittest.TestCase):
    def overlay(self):
        emitted = []
        frame = np.zeros((720,1280,3), np.uint8)
        session = SimpleNamespace(hwnd=123, closed=False, newest=lambda seq: (1, frame))
        return SimpleNamespace(hook=SimpleNamespace(hwnd=123, bbox=(600,300,1880,1020)),
                               window_monitor=Mock(snapshot=Mock(return_value=(123,(600,300,1880,1020),True))),
                               worker=SimpleNamespace(session=session),
                               events=SimpleNamespace(clicked=SimpleNamespace(emit=emitted.append))), emitted, frame

    def test_click_freezes_frame_and_client_coordinates_together(self):
        overlay, emitted, frame = self.overlay()
        with patch("overlay_app.client_bbox", side_effect=AssertionError("输入回调不得同步查询窗口")), \
                patch("overlay_app.crop_client_frame", side_effect=lambda image, *a, **k: image):
            Overlay.freeze_click(overlay, 900, 600)
        self.assertEqual(emitted[0]["bbox"], overlay.hook.bbox)
        self.assertIs(emitted[0]["screenshot"], frame)
        self.assertIsNone(emitted[0]["capture_error"])

    def test_resize_and_capture_errors_do_not_escape_mouse_hook(self):
        overlay, emitted, _ = self.overlay()
        overlay.window_monitor.snapshot.side_effect = [(123,overlay.hook.bbox,True),
                                                       (123,(600,300,2520,1380),True)]
        with patch("overlay_app.client_bbox", side_effect=AssertionError("输入回调不得同步查询窗口")), \
                patch("overlay_app.crop_client_frame", side_effect=lambda image, *a, **k: image):
            Overlay.freeze_click(overlay, 900, 600)
        self.assertIsNone(emitted[0]["screenshot"])
        self.assertIn("尺寸已改变", emitted[0]["capture_error"])
        overlay.window_monitor.snapshot.side_effect = None
        with patch("overlay_app.crop_client_frame", side_effect=OSError("window closed")):
            Overlay.freeze_click(overlay, 900, 600)
        self.assertEqual(emitted[1]["capture_error"], "window closed")

    def test_stale_window_snapshot_does_not_start_query(self):
        overlay, emitted, _ = self.overlay()
        overlay.window_monitor.snapshot.return_value = (None, None, False)
        Overlay.freeze_click(overlay, 900, 600)
        self.assertFalse(emitted)
