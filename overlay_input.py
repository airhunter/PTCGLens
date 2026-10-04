"""Windows 鼠标钩子：只拦截游戏内按住查询键的指定点击。"""

from __future__ import annotations

import ctypes
import threading
from ctypes import wintypes

from live_capture import set_dpi_awareness, user32
from overlay_model import MODIFIERS, shortcut_keys


class MouseData(ctypes.Structure):
    _fields_ = [("pt", wintypes.POINT), ("mouseData", wintypes.DWORD),
                ("flags", wintypes.DWORD), ("time", wintypes.DWORD), ("extra", ctypes.c_size_t)]


class KeyData(ctypes.Structure):
    _fields_ = [("vkCode", wintypes.DWORD), ("scanCode", wintypes.DWORD),
                ("flags", wintypes.DWORD), ("time", wintypes.DWORD), ("extra", ctypes.c_size_t)]


PROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
user32.SetWindowsHookExW.argtypes = (ctypes.c_int, PROC, wintypes.HINSTANCE, wintypes.DWORD)
user32.SetWindowsHookExW.restype = wintypes.HANDLE
user32.CallNextHookEx.argtypes = (wintypes.HANDLE, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
user32.CallNextHookEx.restype = ctypes.c_ssize_t
user32.UnhookWindowsHookEx.argtypes = (wintypes.HANDLE,)
user32.GetAsyncKeyState.argtypes = (ctypes.c_int,)
user32.GetAsyncKeyState.restype = ctypes.c_short
user32.PostThreadMessageW.argtypes = (wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
user32.WindowFromPoint.argtypes = (wintypes.POINT,)
user32.WindowFromPoint.restype = wintypes.HWND
user32.GetAncestor.argtypes = (wintypes.HWND, wintypes.UINT)
user32.GetAncestor.restype = wintypes.HWND


class MouseTrigger(threading.Thread):
    def __init__(self, callback, error_callback, shortcut="Ctrl+Alt", button="right", dismiss_callback=None):
        super().__init__(daemon=True, name="PTCGLens mouse hook")
        self.callback, self.error_callback = callback, error_callback
        self.hwnd, self.bbox = None, None
        self.thread_id = 0
        self.config = (shortcut_keys(shortcut), button)
        self.swallowed_up = None
        self.stop_event = threading.Event()
        self.last_click = None
        self.dismiss_callback = dismiss_callback
        self.dismiss_enabled = False
        self.outside_dismiss_enabled = False
        self.popup_hwnd = None
        self.escape_swallowed = False

    def configure(self, shortcut, button):
        self.config = (shortcut_keys(shortcut), button)

    def dismiss_outside_click(self, message, hovered_root):
        """只通知收起；普通左键始终继续传给原窗口，浮卡内按钮仍可操作。"""
        if (message == 0x0201 and self.outside_dismiss_enabled and self.popup_hwnd
                and hovered_root != self.popup_hwnd and self.dismiss_callback):
            self.dismiss_callback()

    def run(self):
        set_dpi_awareness()
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self.thread_id = kernel32.GetCurrentThreadId()
        message = wintypes.MSG()
        user32.PeekMessageW(ctypes.byref(message), None, 0, 0, 0)

        @PROC
        def handle(code, message, data):
            if code >= 0:
                if message == self.swallowed_up:
                    self.swallowed_up = None
                    return 1
                keys, button = self.config
                down, up = (0x0201, 0x0202) if button == "left" else (0x0204, 0x0205)
                if message == down:
                    point = ctypes.cast(data, ctypes.POINTER(MouseData)).contents.pt
                    pressed = tuple(key for key in MODIFIERS.values() if user32.GetAsyncKeyState(key) & 0x8000)
                    self.last_click = (self.hwnd, user32.GetForegroundWindow(), pressed, point.x, point.y)
                if message == down and self.hwnd and user32.GetForegroundWindow() == self.hwnd:
                    if all(user32.GetAsyncKeyState(key) & 0x8000 for key in keys) and all(
                        not user32.GetAsyncKeyState(key) & 0x8000
                        for key in MODIFIERS.values() if key not in keys
                    ):
                        point = ctypes.cast(data, ctypes.POINTER(MouseData)).contents.pt
                        box = self.bbox
                        hovered = user32.WindowFromPoint(point)
                        if box and box[0] <= point.x < box[2] and box[1] <= point.y < box[3] \
                                and user32.GetAncestor(hovered, 2) == self.hwnd:
                            try:
                                self.callback(point.x, point.y)
                            except Exception as exc:
                                self.error_callback(f"查询画面暂不可用：{exc}")
                            else:
                                self.swallowed_up = up
                                return 1
                # 查询点击优先；鼠标左键查询不会先被当作关闭动作。
                if message == 0x0201 and self.outside_dismiss_enabled:
                    point = ctypes.cast(data, ctypes.POINTER(MouseData)).contents.pt
                    hovered = user32.WindowFromPoint(point)
                    self.dismiss_outside_click(message, user32.GetAncestor(hovered, 2))
            return user32.CallNextHookEx(None, code, message, data)

        @PROC
        def keyboard(code, message, data):
            if code >= 0 and ctypes.cast(data, ctypes.POINTER(KeyData)).contents.vkCode == 0x1B:
                if message in (0x0100, 0x0104) and (
                    self.escape_swallowed or self.dismiss_enabled and self.hwnd == user32.GetForegroundWindow()
                ):
                    self.escape_swallowed = True
                    if self.dismiss_callback:
                        self.dismiss_callback()
                    return 1
                if message in (0x0101, 0x0105) and self.escape_swallowed:
                    self.escape_swallowed = False
                    return 1
            return user32.CallNextHookEx(None, code, message, data)

        hook = user32.SetWindowsHookExW(14, handle, None, 0)
        if not hook:
            self.error_callback(f"无法注册鼠标查询：{ctypes.WinError(ctypes.get_last_error())}")
            return
        self.error_callback("鼠标查询已注册")
        keyboard_hook = user32.SetWindowsHookExW(13, keyboard, None, 0)
        if not keyboard_hook:
            self.error_callback("Esc 关闭键注册失败，请使用浮卡关闭按钮")
        try:
            while not self.stop_event.is_set() and user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
                pass
        finally:
            user32.UnhookWindowsHookEx(hook)
            if keyboard_hook:
                user32.UnhookWindowsHookEx(keyboard_hook)

    def stop(self):
        self.stop_event.set()
        if self.thread_id:
            user32.PostThreadMessageW(self.thread_id, 0x0012, 0, 0)
