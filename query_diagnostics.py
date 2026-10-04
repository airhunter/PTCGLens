"""保留有限数量的本地查询失败画面，便于重现局部卡面和动画问题。"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

import cv2

from atomic_json import write_json_atomic


class QueryFailureRecorder:
    def __init__(self, root: Path, limit=10):
        self.root, self.limit, self.cursor = root, limit, 0

    def save(self, image, details):
        if image is None:
            return
        self.root.mkdir(parents=True, exist_ok=True)
        slot = self.root / f"slot-{self.cursor % self.limit:02}"
        ok, encoded = cv2.imencode(".png", image)
        if not ok:
            raise ValueError("查询画面编码失败")
        body = encoded.tobytes()
        temporary = slot.with_suffix(".png.tmp")
        try:
            temporary.write_bytes(body)
            os.replace(temporary, slot.with_suffix(".png"))
            write_json_atomic(slot.with_suffix(".json"), {**details, "image_sha256":hashlib.sha256(body).hexdigest()})
            self.cursor += 1
        finally:
            temporary.unlink(missing_ok=True)
