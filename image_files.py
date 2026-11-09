"""使用 Python 打开路径，兼容 Windows 中文目录中的图片写入。"""

from pathlib import Path

import cv2
import numpy as np


def write_image(path: Path, image: np.ndarray) -> None:
    ok, encoded = cv2.imencode(path.suffix, image)
    if not ok:
        raise OSError(f"Cannot encode image: {path}")
    path.write_bytes(encoded.tobytes())
