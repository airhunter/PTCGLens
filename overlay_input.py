"""公共悬停状态机；原生组合点击由各平台后端实现。"""
import math
import threading
import time
from platform_backend import get_backend
from overlay_model import parse_shortcut


class HoverState:
    def __init__(self, delay=.6, tolerance=6):
        self.delay, self.tolerance = delay, tolerance
        self.epoch = 0
        self.held = False
        self.blocked = False
        self.anchor = self.since = self.target = None
        self.fired = False

    def reset(self):
        self.epoch += 1
        self.held = False
        self.anchor = self.since = self.target = None
        self.fired = False

    def step(self, now, pressed, active, point, over_game=True, over_popup=False):
        if not pressed:
            was_held = self.held
            if was_held:
                self.reset()
            self.blocked = False
            return "dismiss" if was_held else None
        if not active:
            was_held = self.held
            if was_held:
                self.reset()
            self.blocked = True
            return "dismiss" if was_held else None
        if self.blocked:
            return None
        if not self.held:
            self.held = True
            self.epoch += 1
        if over_popup or not over_game:
            self.anchor = self.since = None
            return None
        if self.target:
            l, t, r, b = self.target
            if l <= point[0] < r and t <= point[1] < b:
                return None
        if self.anchor is None or math.dist(self.anchor, point) > self.tolerance:
            self.anchor, self.since, self.fired = point, now, False
        if not self.fired and now-self.since >= self.delay:
            self.fired = True
            return "query"
        return None


class ToggleHoverState(HoverState):
    """快捷键只在按下沿切换；松开后保留查询会话。"""
    def __init__(self, delay=.6, tolerance=6):
        super().__init__(delay, tolerance)
        self.enabled = False
        self.key_down = False

    def reset(self):
        super().reset()
        self.enabled = False

    def step(self, now, pressed, active, point, over_game=True, over_popup=False):
        rising = pressed and not self.key_down
        self.key_down = pressed
        if not active:
            self.enabled = False
        elif rising:
            self.enabled = not self.enabled
            if self.enabled:
                self.blocked = False
        return super().step(now, self.enabled, active, point, over_game, over_popup)


class HoverTrigger(threading.Thread):
    def __init__(self, callback, error_callback, shortcut="Ctrl+Alt", delay=.6, dismiss_callback=None,
                 toggle=False):
        super().__init__(daemon=True, name="PTCGLens hover")
        parse_shortcut(shortcut)
        self.callback, self.error_callback, self.dismiss_callback = callback, error_callback, dismiss_callback
        self.shortcut = shortcut
        self.toggle = toggle
        self.state = ToggleHoverState(delay) if toggle else HoverState(delay)
        self.hwnd = self.bbox = self.popup_hwnd = None
        self.foreground = lambda: get_backend().is_foreground(self.hwnd)
        self.popup_contains = lambda p: False
        self.dismiss_enabled = self.outside_dismiss_enabled = False
        self.last_click = None
        self.last_window = None
        self.last_bounds = None
        self.stop_event, self.lock = threading.Event(), threading.RLock()
        self.escape_hook = None

    @property
    def dismiss_enabled(self):
        return getattr(self, "_dismiss_enabled", False)

    @dismiss_enabled.setter
    def dismiss_enabled(self, enabled):
        self._dismiss_enabled = enabled
        hook = getattr(self, "escape_hook", None)
        if hook is not None:
            hook.dismiss_enabled = enabled

    def valid(self, epoch):
        with self.lock:
            backend = get_backend()
            return (self.state.held and epoch == self.state.epoch and
                    (self.state.enabled if self.toggle else backend.shortcut_pressed(self.shortcut))
                    and self.foreground())

    @property
    def enabled(self):
        with self.lock:
            return self.state.enabled if self.toggle else self.state.held

    def set_target(self, target):
        with self.lock:
            self.state.target = target

    def invalidate(self):
        with self.lock:
            self.state.reset()
            self.state.blocked = True

    def retry(self, epoch):
        """新索引或采集恢复后重新计时，仍受当前查询会话与前台判定约束。"""
        with self.lock:
            if not self.state.held or self.state.blocked or self.state.epoch != epoch:
                return False
            self.state.target = self.state.anchor = self.state.since = None
            self.state.fired = False
            return True

    def run(self):
        backend = get_backend()
        backend.set_dpi_awareness()
        # 只过滤可见浮卡的 Esc；鼠标点击始终传给游戏。
        self.escape_hook = backend.MouseTrigger(lambda *args: None, self.error_callback,
                                                dismiss_callback=self.dismiss_callback)
        self.escape_hook.query_enabled = False
        self.escape_hook.start()
        last_error = 0
        while not self.stop_event.is_set():
            try:
                pressed = backend.shortcut_pressed(self.shortcut)
                active = self.foreground()
                self.escape_hook.hwnd = self.hwnd
                self.escape_hook.dismiss_enabled = self.dismiss_enabled
                point = backend.pointer_position()
                box = self.bbox
                inside = bool(box and box[0] <= point[0] < box[2] and box[1] <= point[1] < box[3])
                over_popup = self.popup_contains(point)
                over_game = bool((pressed or (self.toggle and self.enabled)) and active and inside and not over_popup
                                 and backend.pointer_over_window(self.hwnd, point))
                with self.lock:
                    was_held = self.state.held
                    if self.hwnd != self.last_window and self.state.held:
                        self.state.reset()
                        self.state.blocked = True
                        if self.dismiss_callback:
                            self.dismiss_callback()
                    elif box != self.last_bounds:
                        self.state.target = self.state.anchor = self.state.since = None
                        self.state.fired = False
                    self.last_window, self.last_bounds = self.hwnd, box
                    if self.state.held and active and backend.escape_pressed():
                        self.state.reset()
                        self.state.blocked = True
                        if self.dismiss_callback:
                            self.dismiss_callback()
                    action = self.state.step(time.monotonic(), pressed, active, point,
                        over_game=over_game, over_popup=over_popup)
                    epoch = self.state.epoch
                    if pressed or was_held:
                        self.last_click = {"mode": "toggle" if self.toggle else "hover", "point": point,
                            "pressed": pressed, "enabled": self.enabled,
                            "active": active, "inside": inside, "over_game": over_game,
                            "over_popup": over_popup, "action": action}
                if action == "dismiss" and self.dismiss_callback:
                    self.dismiss_callback()
                elif action == "query":
                    self.callback(*point, epoch)
            except Exception as exc:
                if time.monotonic()-last_error > 5:
                    self.error_callback(f"悬停输入暂不可用：{exc}")
                    last_error = time.monotonic()
                with self.lock:
                    was_held = self.state.held
                    self.state.reset()
                if was_held and self.dismiss_callback:
                    self.dismiss_callback()
            self.stop_event.wait(.03)
        self.escape_hook.stop()
        self.escape_hook.join(timeout=1)

    def stop(self):
        self.stop_event.set()
        if self.escape_hook is not None:
            self.escape_hook.stop()


def MouseTrigger(*args, **kwargs):
    return get_backend().MouseTrigger(*args, **kwargs)
