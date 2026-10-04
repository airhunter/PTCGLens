"""验证后台采集帧的复制、裁剪与会话关闭。"""

import unittest
from unittest.mock import patch

import numpy as np

import window_capture


class FakeControl:
    def __init__(self) -> None:
        self.stops = 0

    def stop(self) -> None:
        self.stops += 1


class FakeCapture:
    def __init__(self, **kwargs) -> None:
        self.options = kwargs
        self.handlers = {}
        self.control = FakeControl()

    def event(self, handler):
        self.handlers[handler.__name__] = handler
        return handler

    def start_free_threaded(self):
        return self.control


class WindowCaptureTests(unittest.TestCase):
    def test_frame_is_copied_before_native_buffer_changes(self) -> None:
        with patch.object(window_capture, "WindowsCapture", FakeCapture):
            session = window_capture.WindowCaptureSession(123, 1)
        native = np.zeros((3, 4, 4), dtype=np.uint8)
        native[:, :, 0] = 17
        frame = type("Frame", (), {"frame_buffer": native})()
        session._capture.handlers["on_frame_arrived"](frame, None)
        native[:, :, 0] = 99
        sequence, image = session.newest(0)
        self.assertEqual(sequence, 1)
        self.assertEqual(image.shape, (3, 4, 3))
        self.assertEqual(int(image[0, 0, 0]), 17)
        self.assertIsNone(session.newest(sequence))
        session.stop()
        session.stop()
        self.assertEqual(session._capture.control.stops, 1)

    def test_client_area_excludes_window_frame(self) -> None:
        frame = np.zeros((140, 220, 3), dtype=np.uint8)
        frame[30:130, 10:210] = 42
        with patch.object(window_capture, "client_bbox", return_value=(110, 130, 310, 230)), \
                patch.object(window_capture, "window_bounds", return_value=[(100, 100, 320, 240)]):
            image = window_capture.crop_client_frame(frame, 123)
        self.assertEqual(image.shape, (100, 200, 3))
        self.assertTrue(np.all(image == 42))

    def test_client_sized_frame_is_not_cropped_again(self) -> None:
        frame = np.zeros((100, 200, 3), dtype=np.uint8)
        with patch.object(window_capture, "client_bbox", return_value=(110, 130, 310, 230)), \
                patch.object(window_capture, "window_bounds") as bounds:
            image = window_capture.crop_client_frame(frame, 123)
        self.assertIs(image, frame)
        bounds.assert_not_called()

    def test_old_frame_after_resize_is_rejected_in_pointer_query(self):
        old = np.zeros((1080, 1920, 3), dtype=np.uint8)
        with patch.object(window_capture, "client_bbox", return_value=(600, 300, 1880, 1020)), \
                patch.object(window_capture, "window_bounds", return_value=[(592, 270, 1888, 1028)]):
            with self.assertRaisesRegex(RuntimeError, "尺寸已改变"):
                window_capture.crop_client_frame(old, 123, strict=True)

    def test_closed_session_never_returns_previous_game_frame(self):
        with patch.object(window_capture, "WindowsCapture", FakeCapture):
            session = window_capture.WindowCaptureSession(123, 1)
        session._capture.handlers["on_frame_arrived"](type("Frame", (), {"frame_buffer": np.zeros((3,4,4), np.uint8)})(), None)
        self.assertIsNotNone(session.newest(0))
        session._capture.handlers["on_closed"]()
        self.assertIsNone(session.newest(0))


if __name__ == "__main__":
    unittest.main()
