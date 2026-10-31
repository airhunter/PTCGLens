"""控制台日志不能影响识别和界面状态。"""

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import sys


_logger = logging.getLogger("ptcglens")
_logger.addHandler(logging.NullHandler())
_logger.propagate = False


def configure_file_logging(path):
    """双击启动没有控制台时仍有有界日志；只由持有单实例锁的进程配置。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(path, maxBytes=2*1024*1024, backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    for previous in _logger.handlers[:]:
        _logger.removeHandler(previous)
        previous.close()
    _logger.addHandler(handler)
    _logger.setLevel(logging.INFO)


def log_exception(message):
    _logger.exception(message)


def log(message):
    _logger.info(str(message))
    stream = sys.stdout
    if stream is None:  # Windows 无控制台启动时可能没有标准输出。
        return
    try:
        text = str(message)
        encoding = getattr(stream, "encoding", None)
        if encoding:
            text = text.encode(encoding, errors="backslashreplace").decode(encoding)
        print(text, file=stream, flush=True)
    except (OSError, ValueError):
        # 关闭的输出流、断开的管道或磁盘错误都不能变成一次查询失败。
        pass
