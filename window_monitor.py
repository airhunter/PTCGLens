"""后台读取系统窗口状态，界面线程只取不可变快照。"""

import threading
import time
from platform_backend import get_backend


class GameWindowMonitor(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True, name="PTCGLens window monitor")
        self.stop_event = threading.Event()
        self.current = (None, None, False, 0.)

    def snapshot(self):
        hwnd, bounds, active, captured = self.current
        # 系统调用响应慢时，不拿旧窗口位置继续触发查询。
        if time.monotonic()-captured > 1:
            return None, None, False
        return hwnd, bounds, active

    def run(self):
        backend = get_backend()
        backend.set_dpi_awareness()
        while not self.stop_event.is_set():
            try:
                hwnd, bounds, active = backend.window_state()
            except (RuntimeError, OSError):
                hwnd, bounds, active = None, None, False
            self.current = (hwnd, bounds, active, time.monotonic())
            self.stop_event.wait(.1)

    def stop(self):
        self.stop_event.set()
