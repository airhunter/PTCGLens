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

    def find(self, screenshot: np.ndarray, cards: dict) -> dict | None:
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
            source = np.float32([record["points"][match.trainIdx - self.offsets[owner]]
                                 for match in matches]).reshape(-1, 1, 2)
            target = np.float32([points[match.queryIdx].pt for match in matches]).reshape(-1, 1, 2)
            transform, mask = cv2.findHomography(source, target, cv2.RANSAC, 5.0)
            if transform is None or mask is None:
                continue
            inliers = int(mask.sum())
            if inliers < 18 or inliers / len(matches) < .5:
                continue
            card_width, card_height = record["dimensions"]
            corners = cv2.perspectiveTransform(
                np.float32([[0, 0], [card_width, 0], [card_width, card_height], [0, card_height]])
                .reshape(-1, 1, 2), transform,
            ).reshape(-1, 2)
            area = abs(float(cv2.contourArea(corners)))
            if not .03 <= area / (width * height) <= .75:
                continue
            left, top = corners.min(axis=0)
            right, bottom = corners.max(axis=0)
            if left < 0 or top < 0 or right > width or bottom > height:
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
