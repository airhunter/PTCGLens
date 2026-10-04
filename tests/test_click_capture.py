import unittest
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from overlay_app import Overlay


class ClickCaptureTest(unittest.TestCase):
    def overlay(self):
        emitted = []
        frame = np.zeros((720,1280,3), np.uint8)
        session = SimpleNamespace(hwnd=123, closed=False, newest=lambda seq: (1, frame))
        return SimpleNamespace(hook=SimpleNamespace(hwnd=123, bbox=(600,300,1880,1020)),
                               worker=SimpleNamespace(session=session),
                               events=SimpleNamespace(clicked=SimpleNamespace(emit=emitted.append))), emitted, frame

    def test_click_freezes_frame_and_client_coordinates_together(self):
        overlay, emitted, frame = self.overlay()
        with patch("overlay_app.client_bbox", return_value=overlay.hook.bbox):
            Overlay.freeze_click(overlay, 900, 600)
        self.assertEqual(emitted[0]["bbox"], overlay.hook.bbox)
        self.assertIs(emitted[0]["screenshot"], frame)
        self.assertIsNone(emitted[0]["capture_error"])

    def test_resize_and_capture_errors_do_not_escape_mouse_hook(self):
        overlay, emitted, _ = self.overlay()
        with patch("overlay_app.client_bbox", side_effect=[overlay.hook.bbox, (600,300,2520,1380)]):
            Overlay.freeze_click(overlay, 900, 600)
        self.assertIsNone(emitted[0]["screenshot"])
        self.assertIn("尺寸已改变", emitted[0]["capture_error"])
        with patch("overlay_app.client_bbox", side_effect=OSError("window closed")):
            Overlay.freeze_click(overlay, 900, 600)
        self.assertEqual(emitted[1]["capture_error"], "window closed")
