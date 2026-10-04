"""按窗口句柄采集游戏画面，不依赖窗口处于前台。"""

from __future__ import annotations

import ctypes
import threading
from ctypes import wintypes

import numpy as np
from windows_capture import WindowsCapture

from live_capture import client_bbox, user32


DWMWA_EXTENDED_FRAME_BOUNDS = 9
user32.GetWindowRect.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.RECT))
user32.GetWindowRect.restype = wintypes.BOOL
dwmapi = ctypes.WinDLL("dwmapi", use_last_error=True)
dwmapi.DwmGetWindowAttribute.argtypes = (
    wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD,
)
dwmapi.DwmGetWindowAttribute.restype = ctypes.HRESULT


def window_bounds(hwnd: int) -> list[tuple[int, int, int, int]]:
    """返回可能与采集帧对应的可见窗口边界。"""
    candidates = []
    visible = wintypes.RECT()
    if dwmapi.DwmGetWindowAttribute(
        hwnd, DWMWA_EXTENDED_FRAME_BOUNDS, ctypes.byref(visible), ctypes.sizeof(visible)
    ) == 0:
        candidates.append((visible.left, visible.top, visible.right, visible.bottom))
    full = wintypes.RECT()
    if user32.GetWindowRect(hwnd, ctypes.byref(full)):
        candidates.append((full.left, full.top, full.right, full.bottom))
    return candidates


def crop_client_frame(frame: np.ndarray, hwnd: int, *, bbox=None, strict=False) -> np.ndarray:
    """窗口采集通常含边框；根据客户区坐标裁去标题栏与边框。"""
    left, top, right, bottom = bbox if bbox is not None else client_bbox(hwnd)
    height, width = frame.shape[:2]
    client_width, client_height = right - left, bottom - top
    if abs(width - client_width) <= 2 and abs(height - client_height) <= 2:
        return frame
    bounds = window_bounds(hwnd)
    if not bounds:
        raise RuntimeError("无法确定游戏窗口边界")
    frame_left, frame_top, frame_right, frame_bottom = min(
        bounds, key=lambda box: abs((box[2] - box[0]) - width) + abs((box[3] - box[1]) - height)
    )
    frame_width, frame_height = frame_right - frame_left, frame_bottom - frame_top
    if frame_width <= 0 or frame_height <= 0:
        raise RuntimeError("游戏窗口边界无效")
    if strict and (abs(frame_width-width) > 2 or abs(frame_height-height) > 2):
        raise RuntimeError("窗口尺寸已改变，请稍后再查询")
    x0 = max(0, round((left - frame_left) * width / frame_width))
    y0 = max(0, round((top - frame_top) * height / frame_height))
    x1 = min(width, round((right - frame_left) * width / frame_width))
    y1 = min(height, round((bottom - frame_top) * height / frame_height))
    if x1 <= x0 or y1 <= y0:
        raise RuntimeError("采集画面与游戏客户区不重合")
    return frame[y0:y1, x0:x1]


class WindowCaptureSession:
    """保留最新一帧；回调里复制像素以免原生帧被释放。"""

    def __init__(self, hwnd: int, interval: float) -> None:
        self.hwnd = hwnd
        self._lock = threading.Lock()
        self._closed = threading.Event()
        self._sequence = 0
        self._frame: np.ndarray | None = None
        self._capture = WindowsCapture(
            window_hwnd=hwnd,
            cursor_capture=False,
            draw_border=False,
            minimum_update_interval=max(1, round(interval * 1000)),
        )

        @self._capture.event
        def on_frame_arrived(frame, _control) -> None:
            image = frame.frame_buffer[:, :, :3].copy()
            with self._lock:
                self._frame = image
                self._sequence += 1

        @self._capture.event
        def on_closed() -> None:
            self._closed.set()

        self._control = self._capture.start_free_threaded()

    @property
    def closed(self) -> bool:
        return self._closed.is_set()

    def newest(self, after_sequence: int) -> tuple[int, np.ndarray] | None:
        with self._lock:
            if self.closed or self._sequence <= after_sequence or self._frame is None:
                return None
            return self._sequence, self._frame

    def stop(self) -> None:
        if not self._closed.is_set():
            self._closed.set()
            self._control.stop()
