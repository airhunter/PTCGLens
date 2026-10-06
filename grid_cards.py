"""从可见网格推断鼠标下的小卡框，并用等尺度卡图复核候选。"""

from __future__ import annotations

from collections import OrderedDict

import cv2
import numpy as np

from card_color import basic_energy_color_matches
from global_cards import LargeCardFinder


def card_rectangles(image):
    height,width=image.shape[:2]
    gray=cv2.cvtColor(image,cv2.COLOR_BGR2GRAY)
    rectangles=[]
    for low,high in ((30,90),(45,120),(60,160)):
        edges=cv2.morphologyEx(cv2.Canny(gray,low,high),cv2.MORPH_CLOSE,np.ones((3,3),np.uint8))
        contours,_=cv2.findContours(edges,cv2.RETR_LIST,cv2.CHAIN_APPROX_SIMPLE)
        for contour in contours:
            x,y,w,h=cv2.boundingRect(contour)
            if (height*.07<h<height*.95 and w>width*.025 and .55<w/h<.9
                    and cv2.contourArea(contour)/(w*h)>.75):
                box=(x,y,w,h)
                if not any(abs(x-a)<4 and abs(y-b)<4 and abs(w-c)<4 for a,b,c,_ in rectangles):
                    rectangles.append(box)
    return rectangles


def pointed_grid_box(image,point,rectangles=None):
    px,py=point
    if rectangles is None:
        rectangles=card_rectangles(image)
    rectangles = list(rectangles)
    direct=[box for box in rectangles if box[0]<=px<box[0]+box[2] and box[1]<=py<box[1]+box[3]]
    candidates=list(direct)
    for x,y,w,h in direct:
        row = [b for b in rectangles if abs(b[1]-y)<max(6,h*.04)
               and abs(b[3]-h)<max(6,h*.08)]
        if len(row)>=4:
            row_width = round(float(np.median([b[2] for b in row])))
            # 翻页箭头等控件可能与最右侧卡框相连，宽度仍由同一行其他牌提供。
            if row_width*.8<=w<=row_width*1.3:
                candidates.append((x,y,row_width,h))
                if abs(w-row_width)>=max(4,row_width*.08):
                    rectangles.append((x,y,row_width,h))
    # 某张牌的轮廓断开时，只从同尺寸、已观测到的网格行列交点推断卡框。
    horizontal=[b for b in rectangles if b[0]<=px<b[0]+b[2]]
    vertical=[b for b in rectangles if b[1]<=py<b[1]+b[3]]
    for a in horizontal:
        for b in vertical:
            if abs(a[2]-b[2])<max(4,a[2]*.08) and abs(a[3]-b[3])<max(6,a[3]*.08):
                candidates.append((a[0],b[1],a[2],b[3]))
    for x,y,w,h in sorted(candidates,key=lambda b:b[2]*b[3]):
        nearby=[b for b in rectangles if abs(b[2]-w)<max(4,w*.08)
                and abs(b[3]-h)<max(6,h*.08) and abs(b[0]-x)<=w*4 and abs(b[1]-y)<h*2.5]
        columns={round(b[0]/max(w*.25,1)) for b in nearby}
        rows={round(b[1]/max(h*.25,1)) for b in nearby}
        if len(nearby)<4 or len(columns)<2 or (len(rows)<2 and len(columns)<3):
            continue
        # 数量圆标可能延长轮廓下沿；完整卡面的比例由宽度恢复。
        bottom=y+round(w*1.4)
        if bottom<=image.shape[0] and x+w<=image.shape[1] and x<=px<x+w and y<=py<bottom:
            return [x,y,x+w,bottom]
    return None


def partial_edge_boxes(image, point):
    """从靠近画面下沿的轮廓或两条竖边恢复截断卡框，仅提供复核候选。"""
    height, width = image.shape[:2]
    px, py = point
    if py < height*.5:
        return []
    edges = np.maximum.reduce([cv2.Canny(channel, 30, 90) for channel in cv2.split(image)])
    boxes = []
    padded = cv2.copyMakeBorder(image, 0, 4, 0, 0, cv2.BORDER_CONSTANT, value=(15, 15, 15))
    outline = cv2.Canny(cv2.cvtColor(padded, cv2.COLOR_BGR2GRAY), 30, 90)
    outline = cv2.morphologyEx(outline, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
    contours, _ = cv2.findContours(outline, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    for contour in contours:
        x, y, w, h = cv2.boundingRect(contour)
        if cv2.contourArea(contour)/max(w*h, 1) > .75 and y+h >= height*.96:
            boxes.append((x, y, x+w, y+round(w*1.4)))
    vertical = cv2.morphologyEx(edges, cv2.MORPH_OPEN, np.ones((max(20, round(height*.06)), 1), np.uint8))
    contours, _ = cv2.findContours(vertical, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    sides = [cv2.boundingRect(c) for c in contours]
    sides = [(x, y) for x, y, w, h in sides if y+h >= height*.96]
    for left, top_left in sides:
        for right, top_right in sides:
            if left < px < right:
                top = min(top_left, top_right)
                boxes.append((left, top, right, top+round((right-left)*1.4)))
    valid = []
    for box in sorted(boxes, key=lambda b: b[2]-b[0]):
        left, top, right, bottom = box
        w = right-left
        if (width*.03 <= w <= width*.22 and left <= px < right and top <= py < bottom
                and bottom > height and height-top >= (bottom-top)*.25
                and not any(max(abs(a-b) for a,b in zip(box, prior)) < 5 for prior in valid)):
            valid.append(list(box))
    return valid[:8]


class GridCardFinder:
    def __init__(self,visuals,cards):
        self.visuals,self.cards=visuals,cards
        self.references=dict(visuals)
        self.sift=cv2.SIFT_create(nfeatures=600)
        self.features={}
        self.ranking_templates=OrderedDict()

    def rank_candidates(self, observed, width):
        """复用小图参考，以同一逐通道相关系数筛选；不缓存截图或识别答案。"""
        sample_width = min(width, 96)
        sample = cv2.resize(observed,
            (sample_width, max(1, round(observed.shape[0]*sample_width/width))),
            interpolation=cv2.INTER_AREA)
        visible_height = min(sample.shape[0], round(sample_width*1.4))
        if visible_height < 18:
            return []
        small_height = max(8, round(32*visible_height/sample_width))
        key = (sample_width, visible_height, small_height)
        matrix = self.ranking_templates.get(key)
        if matrix is None:
            rows=[]
            for _,reference in self.visuals:
                projected=cv2.resize(reference,(sample_width,round(sample_width*1.4)),
                    interpolation=cv2.INTER_AREA)
                small=cv2.resize(projected[:visible_height],(32,small_height),interpolation=cv2.INTER_AREA)
                small=cv2.GaussianBlur(small,(5,5),0).astype(np.float32)
                small-=small.mean(axis=(0,1),keepdims=True)
                row=small.ravel()
                rows.append(row/max(float(np.linalg.norm(row)),1e-12))
            matrix=np.asarray(rows,np.float32)
            self.ranking_templates[key]=matrix
            while len(self.ranking_templates)>4:
                self.ranking_templates.popitem(last=False)
        self.ranking_templates.move_to_end(key)
        small=cv2.resize(sample[:visible_height],(32,small_height),interpolation=cv2.INTER_AREA)
        small=cv2.GaussianBlur(small,(5,5),0).astype(np.float32)
        small-=small.mean(axis=(0,1),keepdims=True)
        vector=small.ravel()
        scores=matrix @ (vector/max(float(np.linalg.norm(vector)),1e-12))
        return sorted([(float(score),cid) for score,(cid,_) in zip(scores,self.visuals)],reverse=True)

    def reference(self,card_id,width,height):
        key=(card_id,width,height)
        if key not in self.features:
            image=cv2.resize(self.references[card_id],(width,height),interpolation=cv2.INTER_AREA)
            image=cv2.resize(image,None,fx=3,fy=3,interpolation=cv2.INTER_CUBIC)
            points,descriptors=self.sift.detectAndCompute(cv2.cvtColor(image,cv2.COLOR_BGR2GRAY),None)
            # 控制连续改变窗口尺寸时的内存增长。
            if len(self.features)>=128:
                self.features.clear()
            self.features[key]=(np.float32([p.pt for p in points]),descriptors)
        return self.features[key]

    def find_at(self,image,point):
        rectangles = card_rectangles(image)
        box=pointed_grid_box(image,point,rectangles)
        if box is not None:
            result = self.verify_box(image,point,box)
            if result:
                return result
        # 孤立特写也有完整轮廓；同样必须通过卡图复核才能返回。
        px,py = point
        direct = [b for b in rectangles if b[0]<=px<b[0]+b[2] and b[1]<=py<b[1]+b[3]]
        for x,y,w,h in sorted(direct,key=lambda b:b[2]*b[3])[:4]:
            result = self.verify_box(image,point,[x,y,x+w,y+round(w*1.4)])
            if result:
                return result
        return None

    def find_partial(self, image, point):
        for box in partial_edge_boxes(image, point):
            result = self.verify_box(image, point, box)
            if result:
                return result
        return None

    def verify_projected(self, image, point, polygon):
        """倾斜卡面先校正，再以相同尺度和既有确认条件比较完整卡库。"""
        corners = np.float32(polygon)
        if (corners.shape != (4,2) or not np.isfinite(corners).all()
                or not cv2.isContourConvex(corners)
                or cv2.pointPolygonTest(corners,tuple(map(float,point)),False) < 0):
            return None
        width, height = 220, 308
        target = np.float32([[0,0],[width,0],[width,height],[0,height]])
        matrix = cv2.getPerspectiveTransform(corners, target)
        rectified = cv2.warpPerspective(image, matrix, (width,height))
        local = cv2.perspectiveTransform(np.float32([[point]]),matrix).reshape(2)
        result = self.verify_box(rectified,tuple(map(float,local)),[0,0,width,height])
        if result is None:
            return None
        restored = cv2.perspectiveTransform(np.float32(result["polygon"]).reshape(-1,1,2),
                                           np.linalg.inv(matrix)).reshape(-1,2)
        if (not np.isfinite(restored).all() or not cv2.isContourConvex(restored)
                or cv2.pointPolygonTest(restored,tuple(map(float,point)),False)<0):
            return None
        return {**result,"verification":"perspective-template",
            "box":[round(float(v)) for v in (*restored.min(axis=0),*restored.max(axis=0))],
            "polygon":np.round(restored).astype(int).tolist()}

    def check_energy_result(self, image, result):
        """只排除颜色不符的候选；弱几何证据不会因此被提升。"""
        if not result or not str(result.get("name_en", "")).startswith("Basic {"):
            return result
        retained = [cid for cid in result.get("possible_printings", [result["card_id"]])
                    if cid in self.references and basic_energy_color_matches(
                        image, result["polygon"], self.references[cid], self.cards.get(cid, {}))]
        if not retained:
            return None
        card_id = result["card_id"] if result["card_id"] in retained else retained[0]
        card = self.cards[card_id]
        signatures = {LargeCardFinder.rules_signature(self.cards[cid]) for cid in retained}
        return {**result, "card_id": card_id, "name_en": card.get("name_en"), "name_zh": card.get("name_zh"),
                "possible_printings": retained,
                "status": "matched" if len(signatures)==1 and result["sift_inliers"]>=25 else "tentative"}

    def verify_box(self,image,point,box):
        """卡框可来自网格或已有对局定位；允许完整卡框延伸到画面以外。"""
        x,y,right,bottom=box
        width,height=right-x,bottom-y
        if width<24 or height<40 or x<0 or y<0 or right>image.shape[1]:
            return None
        observed=image[y:min(bottom,image.shape[0]),x:right]
        if observed.shape[0]<height*.2:
            return None
        # 排名最终只比较 32 像素宽的图，不把全库参考先放大到特写尺寸。
        # 卡面细节仍在下面的 SIFT 验证中按完整观察尺度复核。
        ranked=self.rank_candidates(observed,width)
        if not ranked or ranked[0][0]<.4:
            return None
        sample_width = min(width, 220)
        sample_height = round(height*sample_width/width)
        sample = cv2.resize(observed, (sample_width, round(observed.shape[0]*sample_width/width)),
                            interpolation=cv2.INTER_AREA)
        enlarged=cv2.resize(sample,None,fx=3,fy=3,interpolation=cv2.INTER_CUBIC)
        points,descriptors=self.sift.detectAndCompute(cv2.cvtColor(enlarged,cv2.COLOR_BGR2GRAY),None)
        if descriptors is None:
            return None
        verified=[]
        for score,card_id in ranked[:8]:
            if score<ranked[0][0]-.15 or card_id not in self.cards:
                continue
            reference,rd=self.reference(card_id,sample_width,sample_height)
            if rd is None:
                continue
            pairs=cv2.BFMatcher().knnMatch(rd,descriptors,k=2)
            good=[p[0] for p in pairs if len(p)==2 and p[0].distance<.72*p[1].distance]
            if len(good)<25:
                continue
            source=np.float32([reference[m.queryIdx] for m in good]).reshape(-1,1,2)
            target=np.float32([points[m.trainIdx].pt for m in good]).reshape(-1,1,2)
            matrix,mask=cv2.findHomography(source,target,cv2.RANSAC,3.)
            if matrix is None or mask is None:
                continue
            valid=mask.ravel().astype(bool)
            count,ratio=int(valid.sum()),float(valid.mean())
            if count<25 or ratio<.65:
                continue
            normalized = source[valid].reshape(-1,2)/(sample_width*3,sample_height*3)
            spread=np.ptp(normalized,axis=0)
            if spread[0]<.35 or spread[1]<.2:
                continue
            if not str(self.cards[card_id].get("name_en", "")).startswith("Basic {"):
                art = normalized[(normalized[:,0]>.08)&(normalized[:,0]<.92)
                                 &(normalized[:,1]>.16)&(normalized[:,1]<.6)]
                # 共享边框和规则区文字不足以确认插画不同的卡。
                if len(art)<8 or np.ptp(art,axis=0)[0]<.2 or np.ptp(art,axis=0)[1]<.12:
                    continue
            original=np.float32([[0,0],[sample_width*3,0],[sample_width*3,sample_height*3],[0,sample_height*3]])
            corners=(cv2.perspectiveTransform(original.reshape(-1,1,2),matrix).reshape(-1,2)
                     /(sample_width*3,sample_height*3)*(width,height)+(x,y)).astype(np.float32)
            expected=np.float32([[x,y],[right,y],[right,bottom],[x,bottom]])
            if (not np.isfinite(corners).all() or not cv2.isContourConvex(corners)
                    or np.max(np.linalg.norm(corners-expected,axis=1))>max(8,np.hypot(width,height)*.12)
                    or cv2.pointPolygonTest(corners.astype(np.float32),tuple(map(float,point)),False)<0):
                continue
            if not basic_energy_color_matches(image, corners, self.references[card_id], self.cards[card_id]):
                continue
            verified.append((count,ratio,score,card_id,corners))
        if not verified:
            return None
        count,ratio,score,card_id,corners=max(verified,key=lambda row:row[0])
        card=self.cards[card_id]
        signatures={LargeCardFinder.rules_signature(self.cards[row[3]]) for row in verified}
        return {"key":"pointed-grid","label":"卡牌","status":"matched" if len(signatures)==1 else "tentative",
            "card_id":card_id,"name_en":card.get("name_en",card_id),"name_zh":card.get("name_zh"),
            "box":[round(float(v)) for v in (*corners.min(axis=0),*corners.max(axis=0))],
            "polygon":np.round(corners).astype(int).tolist(),"sift_inliers":count,"inlier_ratio":round(ratio,3),
            "score":round(score,3),"possible_printings":sorted({row[3] for row in verified}),
            "verification":"grid-template","candidates":[]}
