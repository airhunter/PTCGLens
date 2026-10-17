"""按热键截取可见的 PTCG Live 窗口，并检索本地卡图索引。"""

from __future__ import annotations

import ctypes
import unicodedata
from ctypes import wintypes

from overlay_model import shortcut_keys, MODIFIERS


user32 = ctypes.WinDLL("user32", use_last_error=True)
WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
WM_HOTKEY = 0x0312
MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_NOREPEAT = 0x4000
CAPTURE_HOTKEY = 1
QUIT_HOTKEY = 2

user32.EnumWindows.argtypes = (WNDENUMPROC, wintypes.LPARAM)
user32.EnumWindows.restype = wintypes.BOOL
user32.IsWindowVisible.argtypes = (wintypes.HWND,)
user32.IsWindowVisible.restype = wintypes.BOOL
user32.IsIconic.argtypes = (wintypes.HWND,)
user32.IsIconic.restype = wintypes.BOOL
user32.GetWindowTextLengthW.argtypes = (wintypes.HWND,)
user32.GetWindowTextLengthW.restype = ctypes.c_int
user32.GetWindowTextW.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
user32.GetWindowTextW.restype = ctypes.c_int
user32.GetClientRect.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.RECT))
user32.GetClientRect.restype = wintypes.BOOL
user32.ClientToScreen.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.POINT))
user32.ClientToScreen.restype = wintypes.BOOL
user32.GetCursorPos.argtypes = (ctypes.POINTER(wintypes.POINT),)
user32.GetCursorPos.restype = wintypes.BOOL
user32.GetForegroundWindow.restype = wintypes.HWND
user32.RegisterHotKey.argtypes = (wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT)
user32.RegisterHotKey.restype = wintypes.BOOL
user32.UnregisterHotKey.argtypes = (wintypes.HWND, ctypes.c_int)
user32.UnregisterHotKey.restype = wintypes.BOOL
user32.GetMessageW.argtypes = (ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT)
user32.GetMessageW.restype = ctypes.c_int


def set_dpi_awareness() -> None:
    try:
        user32.SetProcessDpiAwarenessContext.argtypes = (ctypes.c_void_p,)
        user32.SetProcessDpiAwarenessContext.restype = wintypes.BOOL
        user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    except AttributeError:
        pass
    # 其他库可能已设置进程 DPI；覆盖当前线程设置，让窗口和鼠标坐标使用物理像素。
    try:
        user32.SetThreadDpiAwarenessContext.argtypes = (ctypes.c_void_p,)
        user32.SetThreadDpiAwarenessContext.restype = ctypes.c_void_p
        user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))
    except AttributeError:
        user32.SetProcessDPIAware()


def normalise(text: str) -> str:
    return "".join(
        char for char in unicodedata.normalize("NFKD", text.casefold())
        if not unicodedata.combining(char)
    )


def visible_windows() -> list[tuple[int, str]]:
    found = []

    @WNDENUMPROC
    def callback(hwnd: int, _unused: int) -> bool:
        if not user32.IsWindowVisible(hwnd) or user32.IsIconic(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length:
            title = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, title, length + 1)
            if title.value.strip():
                found.append((hwnd, title.value))
        return True

    if not user32.EnumWindows(callback, 0):
        error = ctypes.get_last_error()
        if error:
            raise ctypes.WinError(error)
    return found


def game_window(title_filter: str | None) -> tuple[int, str]:
    windows = visible_windows()
    if title_filter:
        token = normalise(title_filter)
        matches = [(hwnd, title) for hwnd, title in windows if token in normalise(title)]
    else:
        matches = [
            (hwnd, title) for hwnd, title in windows
            if "pokemon" in normalise(title) and "live" in normalise(title)
        ]
    if not matches:
        raise RuntimeError("PTCG Live window not found. Start the game or use --title to specify its window title.")
    foreground = user32.GetForegroundWindow()
    return next((item for item in matches if item[0] == foreground), matches[0])


def client_bbox(hwnd: int) -> tuple[int, int, int, int]:
    rect = wintypes.RECT()
    origin = wintypes.POINT(0, 0)
    if not user32.GetClientRect(hwnd, ctypes.byref(rect)):
        raise ctypes.WinError(ctypes.get_last_error())
    if not user32.ClientToScreen(hwnd, ctypes.byref(origin)):
        raise ctypes.WinError(ctypes.get_last_error())
    if rect.right <= 0 or rect.bottom <= 0:
        raise RuntimeError("The game window has an empty client area")
    return origin.x, origin.y, origin.x + rect.right, origin.y + rect.bottom



def is_foreground(hwnd):
    return bool(hwnd and user32.GetForegroundWindow() == hwnd)


def window_state():
    hwnd, _ = game_window(None)
    return hwnd, client_bbox(hwnd), is_foreground(hwnd)


def pointer_position():
    point = wintypes.POINT()
    if not user32.GetCursorPos(ctypes.byref(point)):
        raise ctypes.WinError(ctypes.get_last_error())
    return point.x, point.y


def popup_bounds(hwnd, logical_rect):
    rect = wintypes.RECT()
    if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        return None
    return rect.left, rect.top, rect.right, rect.bottom


user32.WindowFromPoint.argtypes = (wintypes.POINT,)
user32.WindowFromPoint.restype = wintypes.HWND
user32.GetAncestor.argtypes = (wintypes.HWND, wintypes.UINT)
user32.GetAncestor.restype = wintypes.HWND
user32.GetAsyncKeyState.argtypes = (ctypes.c_int,)
user32.GetAsyncKeyState.restype = ctypes.c_short


def pointer_over_window(hwnd, point):
    hovered = user32.WindowFromPoint(wintypes.POINT(*point))
    return user32.GetAncestor(hovered, 2) == hwnd


def shortcut_pressed(shortcut):
    keys = shortcut_keys(shortcut)
    return all(user32.GetAsyncKeyState(key) & 0x8000 for key in keys) and not any(
        user32.GetAsyncKeyState(key) & 0x8000 for key in MODIFIERS.values() if key not in keys)


def capture_screen(hwnd):
    import cv2
    import numpy as np
    from PIL import ImageGrab
    return cv2.cvtColor(np.asarray(ImageGrab.grab(bbox=client_bbox(hwnd), all_screens=True)), cv2.COLOR_RGB2BGR)


def check_permissions(*, input_required=False, request=False):
    return []


def validate_shortcut(shortcut):
    shortcut_keys(shortcut)


def escape_pressed():
    return bool(user32.GetAsyncKeyState(0x1B) & 0x8000)


def register_hotkeys() -> None:
    if not user32.RegisterHotKey(None, CAPTURE_HOTKEY, MOD_CONTROL | MOD_ALT | MOD_NOREPEAT, ord("L")):
        raise ctypes.WinError(ctypes.get_last_error())
    if not user32.RegisterHotKey(None, QUIT_HOTKEY, MOD_CONTROL | MOD_ALT | MOD_SHIFT | MOD_NOREPEAT, ord("Q")):
        user32.UnregisterHotKey(None, CAPTURE_HOTKEY)
        raise ctypes.WinError(ctypes.get_last_error())


def check_hotkeys():
    register_hotkeys()
    user32.UnregisterHotKey(None, CAPTURE_HOTKEY)
    user32.UnregisterHotKey(None, QUIT_HOTKEY)


def watch_capture(callback) -> None:
    register_hotkeys()
    print("Ctrl+Alt+L: capture card; Ctrl+Alt+Shift+Q: quit", flush=True)
    try:
        message = wintypes.MSG()
        while True:
            status = user32.GetMessageW(ctypes.byref(message), None, 0, 0)
            if status == -1:
                raise ctypes.WinError(ctypes.get_last_error())
            if status == 0 or (message.message == WM_HOTKEY and message.wParam == QUIT_HOTKEY):
                break
            if message.message == WM_HOTKEY and message.wParam == CAPTURE_HOTKEY:
                try:
                    callback()
                except Exception as exc:
                    print(f"Capture failed: {exc}", flush=True)
    finally:
        user32.UnregisterHotKey(None, CAPTURE_HOTKEY)
        user32.UnregisterHotKey(None, QUIT_HOTKEY)
