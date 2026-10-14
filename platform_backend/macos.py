"""macOS 14+ 后端：ScreenCaptureKit 窗口采集、Quartz 输入、Qt 逻辑坐标。

桌面坐标使用屏幕点，截图使用像素；Retina 倍率仅作用于帧，不能乘到指针上。
"""

from __future__ import annotations

import threading
import time
import unicodedata

import AppKit
import ApplicationServices as AX
import Quartz as CG
import ScreenCaptureKit as SC
import objc
import cv2
import numpy as np

from overlay_model import intersect_rect, parse_shortcut
from platform_backend.contracts import FrameImage


KEY_CODES = dict(zip("abcdefghijklmnopqrstuvwxyz", (0, 11, 8, 2, 14, 3, 5, 4, 34, 38, 40, 37,
                     46, 45, 31, 35, 12, 15, 1, 17, 32, 9, 13, 7, 16, 6)))
KEY_CODES.update(dict(zip("0123456789", (29, 18, 19, 20, 21, 23, 22, 26, 28, 25))))
KEY_CODES.update({f"f{i+1}": code for i, code in enumerate((122,120,99,118,96,97,98,100,101,109,103,111,105,107,113,106,64,79,80,90))})
FLAGS = {"ctrl": CG.kCGEventFlagMaskControl, "alt": CG.kCGEventFlagMaskAlternate,
         "shift": CG.kCGEventFlagMaskShift, "cmd": CG.kCGEventFlagMaskCommand}


def set_dpi_awareness():
    pass  # Cocoa 和 Qt 默认使用逻辑坐标。


def check_permissions(*, input_required=False, request=False):
    missing = []
    if not CG.CGPreflightScreenCaptureAccess():
        if request:
            CG.CGRequestScreenCaptureAccess()
        if not CG.CGPreflightScreenCaptureAccess():
            missing.append("请在系统设置 → 隐私与安全性中授予屏幕录制权限；授权后重新启动程序")
    if input_required:
        if not AX.AXIsProcessTrusted():
            if request:
                AX.AXIsProcessTrustedWithOptions({AX.kAXTrustedCheckOptionPrompt: True})
            if not AX.AXIsProcessTrusted():
                missing.append("请授予辅助功能权限（开发运行时授权实际承载 Python 的终端或应用）")
        if hasattr(CG, "CGPreflightListenEventAccess") and not CG.CGPreflightListenEventAccess():
            if request:
                CG.CGRequestListenEventAccess()
            if not CG.CGPreflightListenEventAccess():
                missing.append("请授予输入监控权限，随后重新启动程序")
    if not hasattr(SC, "SCScreenshotManager"):
        missing.append("窗口采集需要 macOS 14 或更新版本")
    return missing


def _windows():
    return CG.CGWindowListCopyWindowInfo(CG.kCGWindowListOptionOnScreenOnly |
                                        CG.kCGWindowListExcludeDesktopElements, CG.kCGNullWindowID) or []


def _info(hwnd):
    for info in _windows():
        if info[CG.kCGWindowNumber] == hwnd:
            return info
    raise RuntimeError("游戏窗口已关闭或最小化")


def visible_windows():
    return [(int(w[CG.kCGWindowNumber]), str(w.get(CG.kCGWindowName) or w.get(CG.kCGWindowOwnerName) or ""))
            for w in _windows() if w.get(CG.kCGWindowLayer, 0) == 0]


def game_window(title_filter=None):
    def normalize(value):
        return "".join(c for c in unicodedata.normalize("NFKD", value).casefold() if c.isalnum())
    token = normalize(title_filter) if title_filter else None
    matches = [(w, t) for w, t in visible_windows() if
               (token in normalize(t) if token else "pokemon" in normalize(t) and "live" in normalize(t))]
    if not matches:
        raise RuntimeError("未找到 PTCG Live 窗口，请启动游戏或使用 --title 指定标题")
    return next((item for item in matches if is_foreground(item[0])), matches[0])


def _rect(info):
    b = info[CG.kCGWindowBounds]
    return (round(b["X"]), round(b["Y"]), round(b["X"]+b["Width"]), round(b["Y"]+b["Height"]))


def _ax_attr(element, attribute):
    error, value = AX.AXUIElementCopyAttributeValue(element, attribute, None)
    return value if error == 0 else None


def client_bbox(hwnd):
    info = _info(hwnd)
    left, top, right, bottom = _rect(info)
    # Unity 的标准标题栏不属于游戏画面；无边框/全屏窗口不裁剪。
    if AX.AXIsProcessTrusted():
        app = AX.AXUIElementCreateApplication(info[CG.kCGWindowOwnerPID])
        windows = _ax_attr(app, "AXWindows") or []
        title = str(info.get(CG.kCGWindowName) or "")
        matches = [w for w in windows if str(_ax_attr(w, "AXTitle") or "") == title]
        if len(matches) == 1 and not _ax_attr(matches[0], "AXFullScreen"):
            if _ax_attr(matches[0], "AXMinimizeButton") is not None:
                content = AppKit.NSWindow.contentRectForFrameRect_styleMask_(
                    ((0, 0), (right-left, bottom-top)), AppKit.NSWindowStyleMaskTitled)
                top += round((bottom-top)-content.size.height)
    if right <= left or bottom <= top:
        raise RuntimeError("游戏窗口内容区域为空")
    return left, top, right, bottom


def is_foreground(hwnd):
    if not hwnd:
        return False
    try:
        front = AppKit.NSWorkspace.sharedWorkspace().frontmostApplication()
        return front is not None and front.processIdentifier() == _info(hwnd)[CG.kCGWindowOwnerPID]
    except RuntimeError:
        return False


def pointer_position():
    p = CG.CGEventGetLocation(CG.CGEventCreate(None))
    return round(p.x), round(p.y)


def pointer_over_window(hwnd, point):
    x, y = point
    # CGWindowList 为前到后的顺序，排除被浮卡或其他应用遮住的区域。
    for info in _windows():
        if info.get(CG.kCGWindowAlpha, 1) <= 0 or info.get(CG.kCGWindowLayer, 0) < 0:
            continue
        l, t, r, b = _rect(info)
        if l <= x < r and t <= y < b:
            return info[CG.kCGWindowNumber] == hwnd
    return False


def shortcut_pressed(shortcut):
    tokens = parse_shortcut(shortcut)
    flags = CG.CGEventSourceFlagsState(CG.kCGEventSourceStateCombinedSessionState)
    if any(bool(flags & mask) != (key in tokens) for key, mask in FLAGS.items()):
        return False
    return all(CG.CGEventSourceKeyState(CG.kCGEventSourceStateCombinedSessionState, KEY_CODES[key])
               for key in tokens if key not in FLAGS)



def validate_shortcut(shortcut):
    tokens = parse_shortcut(shortcut)
    if any(key not in FLAGS and key not in KEY_CODES for key in tokens):
        raise ValueError("Mac 快捷键支持修饰键、字母、数字和 F1–F20")


def escape_pressed():
    return bool(CG.CGEventSourceKeyState(CG.kCGEventSourceStateCombinedSessionState, 53))


def _await_call(start, timeout=5):
    ready, values = threading.Event(), []
    def completed(value, error):
        values.extend((value, error))
        ready.set()
    start(completed)
    if not ready.wait(timeout):
        raise RuntimeError("窗口采集超时，请检查屏幕录制权限并重新启动")
    if values[1] is not None or values[0] is None:
        raise RuntimeError(f"窗口采集失败：{values[1] or '未返回画面'}")
    return values[0]


def _image_array(image):
    # 通过 Cocoa 的 PNG 编码固定颜色和行跨度；不能假定 CGImage 是紧密 BGRA。
    bitmap = AppKit.NSBitmapImageRep.alloc().initWithCGImage_(image)
    data = bitmap.representationUsingType_properties_(AppKit.NSBitmapImageFileTypePNG, {})
    array = cv2.imdecode(np.frombuffer(bytes(data), np.uint8), cv2.IMREAD_COLOR)
    if array is None:
        raise RuntimeError("无法解码窗口画面")
    return array


def _filter(hwnd):
    content = _await_call(SC.SCShareableContent.getShareableContentWithCompletionHandler_)
    window = next((w for w in content.windows() if w.windowID() == hwnd), None)
    if window is None:
        raise RuntimeError("游戏窗口已关闭或最小化")
    return SC.SCContentFilter.alloc().initWithDesktopIndependentWindow_(window)


def _capture(content_filter, bounds):
    rect = content_filter.contentRect()
    scale = content_filter.pointPixelScale()
    config = SC.SCStreamConfiguration.alloc().init()
    config.setWidth_(max(1, round(rect.size.width*scale)))
    config.setHeight_(max(1, round(rect.size.height*scale)))
    config.setShowsCursor_(False)
    config.setIgnoreShadowsSingleWindow_(True)
    image = _await_call(lambda done: SC.SCScreenshotManager.captureImageWithFilter_configuration_completionHandler_(
        content_filter, config, done))
    return _image_array(image)


def crop_client_frame(frame, hwnd, *, bbox=None, strict=False):
    client = bbox or client_bbox(hwnd)
    full = getattr(frame, "desktop_bounds", None) or _rect(_info(hwnd))
    captured_client = getattr(frame, "client_bounds", None)
    if strict and captured_client and captured_client != client:
        raise RuntimeError("窗口位置或尺寸已改变，请稍后再查询")
    h, w = frame.shape[:2]
    # 原生采集倍率可以是 1 或 2，必须保持纵横比例并拒绝尺寸变化的旧帧。
    fw, fh = full[2]-full[0], full[3]-full[1]
    if strict and abs(w/fw-h/fh) > .02:
        raise RuntimeError("窗口尺寸已改变，请稍后再查询")
    x0, y0 = round((client[0]-full[0])*w/fw), round((client[1]-full[1])*h/fh)
    x1, y1 = round((client[2]-full[0])*w/fw), round((client[3]-full[1])*h/fh)
    return frame[max(0,y0):min(h,y1), max(0,x0):min(w,x1)]


class WindowCaptureSession:
    """限制帧率并只保留最新窗口帧；被其他窗口遮挡时仍采集目标窗口。"""
    def __init__(self, hwnd, interval):
        missing = check_permissions()
        if missing:
            raise RuntimeError("；".join(missing))
        self.hwnd, self.error = hwnd, None
        self._closed, self._lock = threading.Event(), threading.Lock()
        self._sequence, self._frame = 0, None
        self._thread = threading.Thread(target=self._run, args=(max(.1, interval),), daemon=True,
                                        name="PTCGLens ScreenCaptureKit")
        self._thread.start()

    def _run(self, interval):
        try:
            with objc.autorelease_pool():
                content_filter = _filter(self.hwnd)
                filter_bounds = _rect(_info(self.hwnd))
            while not self.closed:
                started = time.monotonic()
                with objc.autorelease_pool():
                    before = client_bbox(self.hwnd)
                    full = _rect(_info(self.hwnd))
                    if full != filter_bounds:
                        content_filter = _filter(self.hwnd)
                        filter_bounds = full
                    frame = _capture(content_filter, before)
                    after = client_bbox(self.hwnd)
                if before == after:
                    with self._lock:
                        self._frame = FrameImage(frame, full, before, time.monotonic())
                        self._sequence += 1
                # 更新过滤器，以免窗口缩放后沿用旧内容区域。
                if self._closed.wait(max(0, interval-(time.monotonic()-started))):
                    break
                if before != after:
                    with objc.autorelease_pool():
                        content_filter = _filter(self.hwnd)
        except Exception as exc:
            self.error = str(exc)
            self._closed.set()

    @property
    def closed(self):
        return self._closed.is_set()

    def newest(self, after_sequence):
        with self._lock:
            if self.closed or self._sequence <= after_sequence or self._frame is None:
                return None
            return self._sequence, self._frame

    def stop(self):
        self._closed.set()
        self._thread.join(timeout=1)


def capture_screen(hwnd):
    # 兼容模式也按目标窗口采集，防止把阅读浮卡或其他应用写进识别画面。
    return crop_client_frame(_capture(_filter(hwnd), client_bbox(hwnd)), hwnd)


def placement_geometry(game_box, card_box, keep_in_game=True):
    from PySide6.QtWidgets import QApplication
    from PySide6.QtCore import QPoint
    center = QPoint(round((card_box[0]+card_box[2])/2), round((card_box[1]+card_box[3])/2))
    screen = QApplication.screenAt(center) or QApplication.primaryScreen()
    r = screen.availableGeometry()
    available = (r.x(), r.y(), r.x()+r.width(), r.y()+r.height())
    bounds = intersect_rect(game_box if keep_in_game else available, available, margin=8)
    return (card_box, bounds) if bounds else None


def popup_bounds(native_id, logical_rect):
    return logical_rect


class MouseTrigger(threading.Thread):
    """可选组合点击模式；只过滤目标游戏的查询点击，保留普通点击。"""
    def __init__(self, callback, error_callback, shortcut="Ctrl+Alt", button="right", dismiss_callback=None):
        super().__init__(daemon=True, name="PTCGLens event tap")
        self.callback, self.error_callback = callback, error_callback
        self.dismiss_callback = dismiss_callback
        self.hwnd = self.bbox = self.popup_hwnd = None
        self.dismiss_enabled = self.outside_dismiss_enabled = False
        self.last_click = None
        self.stop_event = threading.Event()
        self.run_loop = None
        self.query_enabled = True
        self.configure(shortcut, button)

    def configure(self, shortcut, button):
        parse_shortcut(shortcut)
        self.shortcut, self.button = shortcut, button

    def dismiss_outside_click(self, message, hovered_root):
        if message == 0x0201 and self.outside_dismiss_enabled and hovered_root != self.popup_hwnd and self.dismiss_callback:
            self.dismiss_callback()

    def run(self):
        swallowed = set()
        def handle(proxy, event_type, event, context):
            try:
                if event_type in (CG.kCGEventTapDisabledByTimeout, CG.kCGEventTapDisabledByUserInput):
                    CG.CGEventTapEnable(tap, True)
                    return event
                key = CG.CGEventGetIntegerValueField(event, CG.kCGKeyboardEventKeycode) if event_type == CG.kCGEventKeyUp else None
                swallowed_key = (event_type, key)
                if swallowed_key in swallowed:
                    swallowed.remove(swallowed_key)
                    return None
                down, up = ((CG.kCGEventLeftMouseDown, CG.kCGEventLeftMouseUp) if self.button == "left" else
                            (CG.kCGEventRightMouseDown, CG.kCGEventRightMouseUp))
                point = CG.CGEventGetLocation(event)
                if self.query_enabled and event_type == down and is_foreground(self.hwnd) and shortcut_pressed(self.shortcut):
                    if pointer_over_window(self.hwnd, (round(point.x), round(point.y))):
                        self.callback(round(point.x), round(point.y))
                        swallowed.add((up, None))
                        return None
                if event_type == CG.kCGEventKeyDown and CG.CGEventGetIntegerValueField(event, CG.kCGKeyboardEventKeycode) == 53:
                    if self.dismiss_enabled and is_foreground(self.hwnd):
                        self.dismiss_callback()
                        swallowed.add((CG.kCGEventKeyUp, 53))
                        return None
                if event_type == CG.kCGEventLeftMouseDown and self.outside_dismiss_enabled:
                    # Qt 的 winId 在 Cocoa 中不是 CGWindowID；用浮卡几何判定由公共层提供。
                    if not getattr(self, "popup_contains", lambda p: False)((round(point.x), round(point.y))):
                        self.dismiss_callback()
            except Exception as exc:
                self.error_callback(f"查询输入暂不可用：{exc}")
            return event
        mask = sum(1 << value for value in (CG.kCGEventLeftMouseDown, CG.kCGEventLeftMouseUp,
                   CG.kCGEventRightMouseDown, CG.kCGEventRightMouseUp, CG.kCGEventKeyDown, CG.kCGEventKeyUp))
        tap = CG.CGEventTapCreate(CG.kCGSessionEventTap, CG.kCGHeadInsertEventTap,
                                 CG.kCGEventTapOptionDefault, mask, handle, None)
        if tap is None:
            self.error_callback("无法监听查询输入，请检查辅助功能和输入监控权限")
            return
        source = CG.CFMachPortCreateRunLoopSource(None, tap, 0)
        self.run_loop = CG.CFRunLoopGetCurrent()
        CG.CFRunLoopAddSource(self.run_loop, source, CG.kCFRunLoopCommonModes)
        CG.CGEventTapEnable(tap, True)
        try:
            while not self.stop_event.is_set():
                CG.CFRunLoopRunInMode(CG.kCFRunLoopDefaultMode, .1, False)
        finally:
            CG.CGEventTapEnable(tap, False)
            CG.CFRunLoopRemoveSource(self.run_loop, source, CG.kCFRunLoopCommonModes)
            CG.CFMachPortInvalidate(tap)

    def stop(self):
        self.stop_event.set()
        if self.run_loop is not None:
            CG.CFRunLoopStop(self.run_loop)
