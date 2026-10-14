"""后端坐标为桌面坐标，帧为 BGR 像素；用区域比例映射，不能假定 1:1。"""

from typing import Protocol
import numpy as np

Rect = tuple[int, int, int, int]


class FrameImage(np.ndarray):
    """带采集时坐标快照的 BGR 数组，防止旧帧套用新窗口尺寸。"""
    def __new__(cls, pixels, desktop_bounds, client_bounds, captured_at):
        obj = pixels.view(cls)
        obj.desktop_bounds = desktop_bounds
        obj.client_bounds = client_bounds
        obj.captured_at = captured_at
        return obj

    def __array_finalize__(self, source):
        for name in ("desktop_bounds", "client_bounds", "captured_at"):
            setattr(self, name, getattr(source, name, None))


class CaptureSession(Protocol):
    hwnd: int  # 不透明窗口 ID；仅后端解释其含义。
    error: str | None

    @property
    def closed(self) -> bool: ...
    def newest(self, after_sequence: int) -> tuple[int, np.ndarray] | None: ...
    def stop(self) -> None: ...


class DesktopBackend(Protocol):
    def visible_windows(self) -> list[tuple[int, str]]: ...
    def client_bbox(self, window: int) -> Rect: ...
    def is_foreground(self, window: int) -> bool: ...
    def pointer_position(self) -> tuple[int, int]: ...
    def pointer_over_window(self, window: int, point: tuple[int, int]) -> bool: ...
    def shortcut_pressed(self, shortcut: str) -> bool: ...
    def capture_screen(self, window: int) -> np.ndarray: ...
    def popup_bounds(self, native_id: int, logical_rect: Rect) -> Rect | None: ...
    def check_permissions(self, *, input_required: bool = False, request: bool = False) -> list[str]: ...
