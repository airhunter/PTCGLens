"""在卡图后台任务中解码；界面只转换已就绪的像素。"""

import cv2
import numpy as np
from PySide6.QtGui import QImage


def prepare_art(payload):
    pixels = cv2.imdecode(np.frombuffer(payload['bytes'], np.uint8), cv2.IMREAD_UNCHANGED)
    if pixels is None:
        raise ValueError('卡图解码失败')
    height, width = pixels.shape[:2]
    # 浮卡不需要原始数千像素大图；限制跨线程传递及界面上传的内存。
    if max(height, width) > 2048:
        ratio = 2048/max(height, width)
        pixels = cv2.resize(pixels, (max(1,round(width*ratio)), max(1,round(height*ratio))),
                            interpolation=cv2.INTER_AREA)
    if pixels.dtype == np.uint16:
        pixels = (pixels >> 8).astype(np.uint8)
    if pixels.dtype != np.uint8:
        raise ValueError('卡图像素格式不可用')
    if pixels.ndim == 2:
        rgb = cv2.cvtColor(pixels, cv2.COLOR_GRAY2RGB)
    elif pixels.shape[2] == 4:
        rgb = cv2.cvtColor(pixels, cv2.COLOR_BGRA2RGBA)
    else:
        rgb = cv2.cvtColor(pixels, cv2.COLOR_BGR2RGB)
    height, width = rgb.shape[:2]
    image_format = QImage.Format.Format_RGBA8888 if rgb.shape[2] == 4 else QImage.Format.Format_RGB888
    image = QImage(rgb.data, width, height, rgb.strides[0], image_format).copy()
    if image.isNull():
        raise ValueError('卡图像素转换失败')
    return {**{key:value for key,value in payload.items() if key != 'bytes'}, 'image':image}
