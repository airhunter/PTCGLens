"""基础能量共享边框，使用卡面颜色排除不同属性的几何匹配。"""

import cv2
import numpy as np


def basic_energy_color_matches(image, polygon, reference, card):
    if not str(card.get("name_en", "")).startswith("Basic {"):
        return True
    width, height = 96, 134
    target = np.float32([[0, 0], [width, 0], [width, height], [0, height]])
    matrix = cv2.getPerspectiveTransform(np.float32(polygon), target)
    observed = cv2.warpPerspective(image, matrix, (width, height))
    visible = cv2.warpPerspective(np.full(image.shape[:2], 255, np.uint8), matrix, (width, height)) > 250
    central = np.zeros((height, width), bool)
    central[round(height*.2):round(height*.8), round(width*.2):round(width*.8)] = True
    region = central & visible
    reference = cv2.resize(reference, (width, height), interpolation=cv2.INTER_AREA)

    def histogram(picture):
        hsv = cv2.cvtColor(picture, cv2.COLOR_BGR2HSV)
        colored = region & (hsv[:, :, 1] > 90) & (hsv[:, :, 2] > 60)
        if colored.sum() < 50:
            return None
        hist = np.bincount(hsv[:, :, 0][colored], minlength=180).astype(np.float32)
        # 色相首尾相接；轻微光照和重采样差异不应改变属性判断。
        kernel = np.exp(-np.arange(-4, 5, dtype=np.float32)**2/8)
        hist = sum(np.roll(hist, shift)*weight for shift,weight in zip(range(-4,5),kernel))
        return hist / hist.sum()

    observed_hist, reference_hist = histogram(observed), histogram(reference)
    return (observed_hist is not None and reference_hist is not None
            and cv2.compareHist(observed_hist, reference_hist, cv2.HISTCMP_BHATTACHARYYA) <= .45)
