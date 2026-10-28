"""实时查询使用统一尺度特征，原尺寸特征只按需读取并限量缓存。"""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Mapping
import hashlib
import json
from pathlib import Path
import tempfile
from zipfile import BadZipFile

import cv2
import numpy as np

from atomic_json import read_json_retry


def compact_records(visuals, previous_visuals=(), previous_records=(), *, cache_dir=None, source_metadata=None):
    sift = cv2.SIFT_create(nfeatures=600)
    old_images, old_records = dict(previous_visuals), {r["card_id"]:r for r in previous_records}
    if cache_dir is not None:
        cache_dir = Path(cache_dir)
        try:
            cache_dir.mkdir(parents=True,exist_ok=True)
        except OSError:
            cache_dir = None
    records = []
    for card_id, reference in visuals:
        if old_images.get(card_id) is reference and card_id in old_records:
            records.append(old_records[card_id])
            continue
        signature = None
        cache_path = cache_dir/f'{card_id}.npz' if cache_dir is not None else None
        if cache_path is not None:
            version = ('compact-v1',cv2.__version__,reference.shape,str(reference.dtype),
                       (source_metadata or {}).get(card_id))
            digest = hashlib.sha256(json.dumps(version,sort_keys=True).encode())
            digest.update(reference.tobytes())
            signature = digest.hexdigest()
            try:
                with np.load(cache_path,allow_pickle=False) as cached:
                    if cached['signature'].item() == signature:
                        points, descriptors = cached['points'], cached['descriptors']
                        if (points.ndim == 2 and points.shape[1] == 2 and descriptors.ndim == 2
                                and descriptors.shape == (len(points),128) and len(points)>0
                                and points.dtype == np.float32 and descriptors.dtype == np.float32
                                and np.isfinite(points).all() and np.isfinite(descriptors).all()
                                and (points >= 0).all() and (points < (220,308)).all()):
                            records.append({'card_id':card_id,'points':points,
                                            'descriptors':descriptors,'dimensions':(220,308)})
                            continue
            except (OSError,ValueError,KeyError,EOFError,BadZipFile):
                pass
        image = cv2.resize(reference,(220,308),interpolation=cv2.INTER_CUBIC)
        points, descriptors = sift.detectAndCompute(cv2.cvtColor(image,cv2.COLOR_BGR2GRAY),None)
        if descriptors is not None:
            record = {"card_id":card_id,"points":np.float32([p.pt for p in points]),
                      "descriptors":descriptors,"dimensions":(220,308)}
            records.append(record)
            if cache_path is not None:
                temporary = None
                try:
                    with tempfile.NamedTemporaryFile(dir=cache_dir,prefix='.compact-',suffix='.npz',delete=False) as output:
                        temporary = Path(output.name)
                        np.savez_compressed(output,signature=signature,points=record['points'],descriptors=descriptors)
                    temporary.replace(cache_path)
                except OSError:
                    pass  # 缓存不可写不影响本轮已经计算出的特征。
                finally:
                    if temporary is not None:
                        try:
                            temporary.unlink(missing_ok=True)
                        except OSError:
                            pass
    if not records:
        raise ValueError("实时特征索引为空")
    return records


class FeatureIndex(Mapping):
    """用于既有卡位复核；最多持有 16 张原尺寸卡牌的特征。"""

    def __init__(self, root: Path, limit=16):
        self.root, self.limit = root, limit
        self.metadata = {r["card_id"]:r for r in read_json_retry(root / "manifest.json")["cards"]}
        self.cached = OrderedDict()
        self.visual_stamps = {}

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
