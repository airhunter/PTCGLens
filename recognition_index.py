"""实时查询使用统一尺度特征，原尺寸特征只按需读取并限量缓存。"""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Mapping
from pathlib import Path

import cv2
import numpy as np

from atomic_json import read_json_retry


def compact_records(visuals):
    sift = cv2.SIFT_create(nfeatures=600)
    records = []
    for card_id, reference in visuals:
        image = cv2.resize(reference,(220,308),interpolation=cv2.INTER_CUBIC)
        points, descriptors = sift.detectAndCompute(cv2.cvtColor(image,cv2.COLOR_BGR2GRAY),None)
        if descriptors is not None:
            records.append({"card_id":card_id,"points":np.float32([p.pt for p in points]),
                            "descriptors":descriptors,"dimensions":(220,308)})
    if not records:
        raise ValueError("实时特征索引为空")
    return records


class FeatureIndex(Mapping):
    """用于既有卡位复核；最多持有 16 张原尺寸卡牌的特征。"""

    def __init__(self, root: Path, limit=16):
        self.root, self.limit = root, limit
        self.metadata = {r["card_id"]:r for r in read_json_retry(root / "manifest.json")["cards"]}
        self.cached = OrderedDict()

    def __len__(self):
        return len(self.metadata)

    def __iter__(self):
        return iter(self.metadata)

    def __contains__(self, card_id):
        return card_id in self.metadata

    def __getitem__(self, card_id):
        if card_id in self.cached:
            self.cached.move_to_end(card_id)
            return self.cached[card_id]
        record = self.metadata[card_id]
        with np.load(self.root / record["file"]) as data:
            loaded = {**record,"points":data["points"],"descriptors":data["descriptors"],
                      "dimensions":(int(data["width"]),int(data["height"]))}
        self.cached[card_id] = loaded
        while len(self.cached)>self.limit:
            self.cached.popitem(last=False)
        return loaded
