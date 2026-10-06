"""从整张对局画面定位随鼠标移动的放大卡牌。"""

from __future__ import annotations

import json

import cv2
import numpy as np


class LargeCardFinder:
    def __init__(self, records: list[dict]):
        self.records = records
        lengths = [len(record["descriptors"]) for record in records]
        self.offsets = np.r_[0, np.cumsum(lengths)]
        self.owners = np.repeat(np.arange(len(records), dtype=np.int32), lengths)
        descriptors = np.vstack([record["descriptors"] for record in records]).astype(np.float32, copy=False)
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
        result = max(found, key=lambda result: result["sift_inliers"], default=None)
        if result and point is not None and result["status"] == "tentative":
            result = self.refine_pointed(result, points, descriptors, groups, cards, point)
        if result is None and point is not None:
            result = self.pointed_geometry(points, descriptors, groups, screenshot.shape[:2], point)
        return result

    def pointed_geometry(self, points, descriptors, groups, shape, point):
        """弱全库证据只用于提议校正区域，不能直接确认卡牌或显示规则。"""
        owners = sorted((owner for owner in groups if len(groups[owner]) >= 4),
                        key=lambda owner:len(groups[owner]), reverse=True)[:8]
        proposals = []
        for owner in owners:
            record = self.records[owner]
            pairs = cv2.BFMatcher(cv2.NORM_L2).knnMatch(descriptors, record["descriptors"], k=2)
            matches = [pair[0] for pair in pairs if len(pair)==2
                       and pair[0].distance < .72*pair[1].distance]
            for _ in range(4):
                if len(matches) < 18:
                    break
                source = np.float32([record["points"][m.trainIdx] for m in matches]).reshape(-1,1,2)
                target = np.float32([points[m.queryIdx].pt for m in matches]).reshape(-1,1,2)
                matrix, mask = cv2.findHomography(source, target, cv2.RANSAC, 3.)
                if matrix is None or mask is None:
                    break
                valid = mask.ravel().astype(bool)
                count, ratio = int(valid.sum()), float(valid.mean())
                w, h = record["dimensions"]
                corners = cv2.perspectiveTransform(np.float32(
                    [[0,0],[w,0],[w,h],[0,h]]).reshape(-1,1,2), matrix).reshape(-1,2)
                if (not np.isfinite(corners).all() or not cv2.isContourConvex(corners)
                        or count < 18 or ratio < .6):
                    break
                if cv2.pointPolygonTest(corners, point, False) < 0:
                    matches = [m for m, keep in zip(matches, valid) if not keep]
                    continue
                spread = np.ptp(source[valid].reshape(-1,2),axis=0)/(w,h)
                widths = np.linalg.norm(corners[[1,2]]-corners[[0,3]],axis=1)
                heights = np.linalg.norm(corners[[3,2]]-corners[[0,1]],axis=1)
                area = abs(float(cv2.contourArea(corners)))/(shape[0]*shape[1])
                if (spread[0] < .35 or spread[1] < .12 or not .0015 <= area <= .95
                        or not .5 <= widths.mean()/max(heights.mean(),1) <= .9
                        or min(*widths,*heights) < 15
                        or max(widths)/min(widths) > 1.5 or max(heights)/min(heights) > 1.5):
                    break
                proposals.append({"key":"preview", "label":"卡牌", "status":"tentative",
                    "verification":"geometry-only", "sift_inliers":count,
                    "inlier_ratio":round(ratio,3), "candidates":[],
                    "box":[round(float(v)) for v in (*corners.min(axis=0),*corners.max(axis=0))],
                    "polygon":corners.tolist()})
                break
        return max(proposals,key=lambda p:p["sift_inliers"],default=None)

    @staticmethod
    def rules_signature(card):
        """只比较规则原文，避免中文覆盖率或系列编号造成相同规则被判为不同。"""
        def clean(value):
            # 空格、换行属于排版差异，数字、标点和效果措辞保持原样。
            return " ".join(value.split()) if isinstance(value,str) else value
        return json.dumps({"name":clean(card.get("name_en")),"hp":card.get("hp"), "text":clean(card.get("card_text_en")),
            "attacks":[{key:clean(attack.get(key)) for key in
                        ("kind","name_en","damage","cost","text_en")}
                       for attack in card.get("attacks",[])]},sort_keys=True)

    def refine_pointed(self, seed, points, descriptors, groups, cards, point):
        """全库检索用于找候选；在指向实例内逐卡验证，避免相似印次分走特征。"""
        polygon = np.float32(seed["polygon"])
        selected = [i for i,keypoint in enumerate(points)
                    if cv2.pointPolygonTest(polygon,keypoint.pt,False) >= 0]
        if len(selected) < 25:
            return seed
        local = descriptors[selected]
        # 只验证有全库匹配证据的少量候选，不把附近特写的特征移到手牌上。
        owners = sorted((owner for owner in groups if len(groups[owner]) >= 12),
                        key=lambda owner:len(groups[owner]),reverse=True)[:8]
        seed_owner = next(owner for owner,record in enumerate(self.records)
                          if record["card_id"] == seed["card_id"])
        if seed_owner not in owners:
            owners.append(seed_owner)
        verified = []
        for owner in owners:
            record = self.records[owner]
            pairs = cv2.BFMatcher(cv2.NORM_L2).knnMatch(local,record["descriptors"],k=2)
            good = [pair[0] for pair in pairs if len(pair)==2
                    and pair[0].distance < .72*pair[1].distance]
            if len(good)<25:
                continue
            source = np.float32([record["points"][match.trainIdx] for match in good]).reshape(-1,1,2)
            target = np.float32([points[selected[match.queryIdx]].pt for match in good]).reshape(-1,1,2)
            matrix, mask = cv2.findHomography(source,target,cv2.RANSAC,3.)
            if matrix is None or mask is None:
                continue
            valid = mask.ravel().astype(bool)
            count, ratio = int(valid.sum()),float(valid.mean())
            if count<25 or ratio<.6:
                continue
            card_width,card_height = record["dimensions"]
            # 匹配点须分布于卡面，集中在一个图标或短文字上不足以确认。
            spread = np.ptp(source[valid].reshape(-1,2),axis=0)/(card_width,card_height)
            if spread[0]<.35 or spread[1]<.12:
                continue
            corners = cv2.perspectiveTransform(np.float32(
                [[0,0],[card_width,0],[card_width,card_height],[0,card_height]]).reshape(-1,1,2),matrix).reshape(-1,2)
            if (not np.isfinite(corners).all() or not cv2.isContourConvex(corners)
                    or cv2.pointPolygonTest(corners,point,False)<0):
                continue
            # 复核几何位置必须与鼠标下的候选一致，不能重新跳到大特写。
            distance = np.max(np.linalg.norm(corners-polygon,axis=1))
            if distance > max(8.,np.linalg.norm(polygon[2]-polygon[0])*.08):
                continue
            card = cards.get(record["card_id"],{})
            verified.append({**seed,"status":"matched","card_id":record["card_id"],
                "name_en":card.get("name_en",record["card_id"]),"name_zh":card.get("name_zh"),
                "sift_inliers":count,"inlier_ratio":round(ratio,3),"verification":"pointed-template",
                "box":[round(float(value)) for value in (*corners.min(axis=0),*corners.max(axis=0))],
                "polygon":np.round(corners).astype(int).tolist()})
        if not verified:
            return seed
        best = max(verified,key=lambda result:result["sift_inliers"])
        best["possible_printings"] = sorted({result["card_id"] for result in verified})
        signatures = {self.rules_signature(cards.get(result["card_id"],{})) for result in verified}
        if len(signatures)>1:
            best["status"] = "tentative"
        return best

    def find_at(self, screenshot: np.ndarray, cards: dict, point: tuple[int, int]) -> dict | None:
        """只匹配覆盖鼠标点的卡牌，局部裁剪减少无关卡牌和计算量。"""
        height, width = screenshot.shape[:2]
        x, y = point
        left, top = max(0, x - round(width * .23)), max(0, y - round(height * .5))
        right, bottom = min(width, x + round(width * .23)), min(height, y + round(height * .5))
        if right <= left or bottom <= top:
            return None
        result = self.find(screenshot[top:bottom, left:right], cards, (float(x-left), float(y-top)))
        if result is None or result["status"]=="tentative":
            # 小图上的原始特征不足时，局部重采样；仍沿用内点数和规则确认门槛。
            near_left,near_top=max(0,x-round(width*.085)),max(0,y-round(height*.18))
            near_right,near_bottom=min(width,x+round(width*.085)),min(height,y+round(height*.18))
            enlarged=cv2.resize(screenshot[near_top:near_bottom,near_left:near_right],None,
                                fx=2,fy=2,interpolation=cv2.INTER_CUBIC)
            refined=self.find(enlarged,cards,(float((x-near_left)*2),float((y-near_top)*2)))
            if refined and (refined["status"]=="matched" or result is None):
                refined["box"]=[round(value/2)+(near_left if i%2==0 else near_top)
                                for i,value in enumerate(refined["box"])]
                refined["polygon"]=[[round(px/2)+near_left,round(py/2)+near_top] for px,py in refined["polygon"]]
                return refined
        if result:
            result["box"] = [value + (left if i % 2 == 0 else top)
                             for i, value in enumerate(result["box"])]
            result["polygon"] = [[px + left, py + top] for px, py in result["polygon"]]
        return result
