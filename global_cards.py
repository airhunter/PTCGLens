"""从整张对局画面定位随鼠标移动的放大卡牌。"""

from __future__ import annotations

import cv2
import numpy as np


class LargeCardFinder:
    def __init__(self, records: list[dict]):
        self.records = records
        lengths = [len(record["descriptors"]) for record in records]
        self.offsets = np.r_[0, np.cumsum(lengths)]
        self.owners = np.repeat(np.arange(len(records), dtype=np.int32), lengths)
        descriptors = np.vstack([record["descriptors"] for record in records]).astype(np.float32)
        self.matcher = cv2.FlannBasedMatcher(dict(algorithm=1, trees=4), dict(checks=64))
        self.matcher.add([descriptors])
        self.matcher.train()
        self.sift = cv2.SIFT_create(nfeatures=9000)

    def find(self, screenshot: np.ndarray, cards: dict,
             point: tuple[float, float] | None = None) -> dict | None:
        points, descriptors = self.sift.detectAndCompute(cv2.cvtColor(screenshot, cv2.COLOR_BGR2GRAY), None)
        if descriptors is None:
            return None
        groups: dict[int, list] = {}
        for pair in self.matcher.knnMatch(descriptors, k=2):
            if len(pair) != 2:
                continue
            first, second = pair
            if first.distance < .8 * second.distance:
                groups.setdefault(int(self.owners[first.trainIdx]), []).append(first)
        height, width = screenshot.shape[:2]
        found = []
        for owner, matches in groups.items():
            if len(matches) < 18:
                continue
            record = self.records[owner]
            card_width, card_height = record["dimensions"]
            # 同一卡图可同时出现在特写与手牌中；移除其他位置的内点后再寻找指向的实例。
            corners = None
            for _ in range(4 if point is not None else 1):
                if len(matches) < 18:
                    break
                source = np.float32([record["points"][match.trainIdx - self.offsets[owner]]
                                     for match in matches]).reshape(-1, 1, 2)
                target = np.float32([points[match.queryIdx].pt for match in matches]).reshape(-1, 1, 2)
                transform, mask = cv2.findHomography(source, target, cv2.RANSAC, 5.0)
                if transform is None or mask is None:
                    break
                projected = cv2.perspectiveTransform(
                    np.float32([[0, 0], [card_width, 0], [card_width, card_height], [0, card_height]])
                    .reshape(-1, 1, 2), transform,
                ).reshape(-1, 2)
                if not np.isfinite(projected).all() or not cv2.isContourConvex(projected):
                    break
                if point is None or cv2.pointPolygonTest(projected, point, False) >= 0:
                    corners = projected
                    break
                matches = [match for match, is_inlier in zip(matches, mask.ravel()) if not is_inlier]
            if corners is None:
                continue
            inliers = int(mask.sum())
            if inliers < 18 or inliers / len(matches) < .5:
                continue
            area = abs(float(cv2.contourArea(corners)))
            if not (.0015 if point is not None else .03) <= area / (width * height) <= (.95 if point is not None else .75):
                continue
            left, top = corners.min(axis=0)
            right, bottom = corners.max(axis=0)
            if point is None and (left < 0 or top < 0 or right > width or bottom > height):
                continue
            if point is not None and cv2.pointPolygonTest(corners, point, False) < 0:
                continue
            # 放大预览大致保持实体卡的纵横比。
            if not .55 <= (right - left) / max(bottom - top, 1) <= .9:
                continue
            card = cards.get(record["card_id"], {})
            found.append({
                "key": "preview", "label": "放大预览", "status": "matched" if inliers >= 25 else "tentative",
                "box": [round(float(left)), round(float(top)), round(float(right)), round(float(bottom))],
                "polygon": np.round(corners).astype(int).tolist(),
                "card_id": record["card_id"], "name_en": card.get("name_en", record["card_id"]),
                "name_zh": card.get("name_zh"), "sift_inliers": inliers,
                "inlier_ratio": round(inliers / len(matches), 3),
                "candidates": [], "possible_printings": [record["card_id"]],
            })
        return max(found, key=lambda result: result["sift_inliers"], default=None)

    def find_at(self, screenshot: np.ndarray, cards: dict, point: tuple[int, int]) -> dict | None:
        """只匹配覆盖鼠标点的卡牌，局部裁剪减少无关卡牌和计算量。"""
        height, width = screenshot.shape[:2]
        x, y = point
        left, top = max(0, x - round(width * .23)), max(0, y - round(height * .5))
        right, bottom = min(width, x + round(width * .23)), min(height, y + round(height * .5))
        if right <= left or bottom <= top:
            return None
        result = self.find(screenshot[top:bottom, left:right], cards, (float(x-left), float(y-top)))
        if result:
            result["box"] = [value + (left if i % 2 == 0 else top)
                             for i, value in enumerate(result["box"])]
            result["polygon"] = [[px + left, py + top] for px, py in result["polygon"]]
        return result
