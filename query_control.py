"""线程内查询预算：旧查询和超时查询在识别阶段之间协作退出。"""

from contextlib import contextmanager
from contextvars import ContextVar
import time


QUERY_TIMEOUT = 5.0
TIMEOUT_MESSAGE = "本次识别用时过长，已取消。可移到卡牌插画中央重新查询，或打开卡牌特写。"
_control = ContextVar("recognition_control", default=None)


class QueryCancelled(RuntimeError):
    pass


class QueryTimedOut(RuntimeError):
    pass


def check_query():
    control = _control.get()
    if control is None:
        return
    cancelled, deadline = control
    if cancelled():
        raise QueryCancelled("查询已取消")
    if time.perf_counter() >= deadline:
        raise QueryTimedOut(TIMEOUT_MESSAGE)


@contextmanager
def query_scope(cancelled, deadline):
    token = _control.set((cancelled, deadline))
    try:
        check_query()
        yield
    finally:
        _control.reset(token)
