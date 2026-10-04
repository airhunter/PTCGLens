"""只保留最新查询，避免快速点击时排队持有大量截图。"""

from __future__ import annotations

import queue
import threading


class LatestJobQueue:
    def __init__(self):
        self.condition = threading.Condition()
        self.pending = None
        self.latest_id = -1

    def put(self, job):
        with self.condition:
            if job[0] < self.latest_id:
                return
            self.latest_id = job[0]
            self.pending = job
            self.condition.notify()

    def get(self, timeout=None):
        with self.condition:
            if not self.condition.wait_for(lambda: self.pending is not None, timeout):
                raise queue.Empty
            result, self.pending = self.pending, None
            return result

    def get_nowait(self):
        return self.get(timeout=0)

    def qsize(self):
        with self.condition:
            return int(self.pending is not None)

    def empty(self):
        return self.qsize() == 0
