"""将游戏窗口物理坐标映射到对应显示器的 Qt 坐标。"""

import ctypes
from ctypes import wintypes

from PySide6.QtWidgets import QApplication

from live_capture import user32
from overlay_model import intersect_rect, map_rect


class MonitorInfo(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("monitor", wintypes.RECT),
                ("work", wintypes.RECT), ("flags", wintypes.DWORD), ("device", wintypes.WCHAR*32)]


user32.MonitorFromPoint.argtypes = (wintypes.POINT, wintypes.DWORD)
user32.MonitorFromPoint.restype = wintypes.HANDLE
user32.GetMonitorInfoW.argtypes = (wintypes.HANDLE, ctypes.POINTER(MonitorInfo))
user32.GetMonitorInfoW.restype = wintypes.BOOL


def edges(rect):
    return rect.left(), rect.top(), rect.right()+1, rect.bottom()+1


def placement_geometry(game_box, card_box, keep_in_game=True):
    point = wintypes.POINT(round((card_box[0]+card_box[2])/2), round((card_box[1]+card_box[3])/2))
    monitor = user32.MonitorFromPoint(point, 2)
    info = MonitorInfo()
    info.cbSize = ctypes.sizeof(info)
    if not user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
        raise ctypes.WinError(ctypes.get_last_error())
    screens = QApplication.screens()
    screen = next((screen for screen in screens if screen.name().casefold() == info.device.casefold()), None)
    if screen is None:
        screen = min(screens, key=lambda candidate: abs(candidate.geometry().x()-info.monitor.left)
                     + abs(candidate.geometry().y()-info.monitor.top))
    physical = (info.monitor.left, info.monitor.top, info.monitor.right, info.monitor.bottom)
    logical = edges(screen.geometry())
    game = map_rect(game_box, physical, logical)
    card = map_rect(card_box, physical, logical)
    available = edges(screen.availableGeometry())
    bounds = intersect_rect(game if keep_in_game else available, available, margin=8)
    if bounds is None:
        return None
    return card, bounds
