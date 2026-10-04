"""在同一目录原子替换 JSON，避免后台读取到写入一半的文件。"""

from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path


def read_json_retry(path: Path):
    """Windows 替换文件的短暂共享冲突可恢复；无效 JSON 仍按错误处理。"""
    for attempt in range(10):
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except PermissionError:
            if attempt == 9:
                raise
            time.sleep(min(.02 * (attempt + 1), .1))


def write_json_atomic(path: Path, document: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, filename = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(filename)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(document, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        for attempt in range(10):
            try:
                os.replace(temporary, path)
                break
            except PermissionError:
                if attempt == 9:
                    raise
                time.sleep(min(.02 * (attempt + 1), .1))
    finally:
        temporary.unlink(missing_ok=True)
