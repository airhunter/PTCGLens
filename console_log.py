"""控制台日志不能影响识别和界面状态。"""

import sys


def log(message):
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
