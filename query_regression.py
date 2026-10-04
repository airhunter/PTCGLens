"""用本地真实截图复测快捷查询，输出未确认、错误规则和识别耗时。"""

from __future__ import annotations

import argparse
import hashlib
import json
import time
import unicodedata
from pathlib import Path
from types import SimpleNamespace

import cv2
import numpy as np

from battle_live import load_resources
from cache_search import read_image
from cache_watch import DEFAULT_CARD_CACHE
from global_cards import LargeCardFinder
from overlay_app import Events, Recognizer


ROOT=Path(__file__).resolve().parent


def normal_name(text):
    return ''.join(c for c in unicodedata.normalize('NFKD',str(text or '')).casefold() if c.isalnum())


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cases',type=Path,default=ROOT/'tests/scenes/query-cases.json')
    parser.add_argument('--sample-root',type=Path,default=ROOT/'output/query-scenes')
    parser.add_argument('--output',type=Path,default=ROOT/'output/query-regression.json')
    parser.add_argument('--scene',action='append',help='仅测指定场景，可重复使用')
    parser.add_argument('--scales',type=float,nargs='+',help='覆盖所有场景的缩放比例；默认使用场景设定')
    args=parser.parse_args()
    if any(not np.isfinite(s) or s<.25 or s>3 for s in args.scales or []):
        parser.error('缩放比例须在 0.25 至 3 之间')
    document=json.loads(args.cases.read_text(encoding='utf-8'))
    scenes=[s for s in document['scenes'] if not args.scene or s['id'] in args.scene]
    if not scenes:
        parser.error('没有选中的场景')
    for scene in scenes:
        if not (args.sample_root/scene['image']).is_file():
            parser.error(f"缺少本地样本：{args.sample_root/scene['image']}；截图不会随源码分发")
    worker=Recognizer(Events(),SimpleNamespace(cache_root=DEFAULT_CARD_CACHE))
    worker.resources=load_resources(ROOT/'output/index',ROOT/'output/card-thumbnails',ROOT/'output/card-data.json')
    worker.layout=json.loads((ROOT/'battle_layout.sample.json').read_text(encoding='utf-8'))
    cards=worker.resources[1]
    rows=[]
    samples=[]
    for scene in scenes:
        path=args.sample_root/scene['image']
        original=read_image(path)
        samples.append({'scene':scene['id'],'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
                        'size':[original.shape[1],original.shape[0]]})
        scales = args.scales or scene.get('scales', [.667,.8,1.,1.333])
        if not scales or any(not np.isfinite(s) or s<.25 or s>3 for s in scales):
            raise ValueError(f"场景缩放比例须在 0.25 至 3 之间：{scene['id']}")
        for scale in scales:
            image=cv2.resize(original,None,fx=scale,fy=scale,
                interpolation=cv2.INTER_AREA if scale<1 else cv2.INTER_LINEAR)
            for case in scene['points']:
                point=tuple(round(v*scale) for v in case['point'])
                if not (0<=point[0]<image.shape[1] and 0<=point[1]<image.shape[0]):
                    raise ValueError(f"样本点超出画面：{scene['id']} {case['id']}")
                started=time.perf_counter()
                result=worker.recognize(image,point)
                elapsed=round((time.perf_counter()-started)*1000,1)
                actual=cards.get(result.get('card_id')) if result.get('status')=='matched' else None
                expected_name=case.get('name_en')
                expected_card=cards.get(case.get('rules_card_id'))
                if case.get('rules_card_id') and expected_card is None:
                    raise ValueError(f"本地卡牌资料缺少期望规则：{case['rules_card_id']}")
                wrong=actual is not None and (expected_name is None
                    or normal_name(actual.get('name_en'))!=normal_name(expected_name)
                    or (expected_card is not None and LargeCardFinder.rules_signature(actual)
                        !=LargeCardFinder.rules_signature(expected_card)))
                passed=not wrong and (actual is not None if expected_name is not None else True)
                row={'scene':scene['id'],'case':case['id'],'scale':scale,'point':point,
                    'expected_name':expected_name,'expected_rules_id':case.get('rules_card_id'),
                    'actual_name':actual.get('name_en') if actual else None,'passed':passed,
                    'wrong_answer':wrong,'elapsed_ms':elapsed,'result':result}
                rows.append(row)
                if not passed:
                    print(f"{'错误' if wrong else '未确认'}：{scene['id']}/{case['id']} ×{scale}",flush=True)
        print(f"已完成：{scene['id']}",flush=True)
    summary={'total':len(rows),'passed':sum(r['passed'] for r in rows),
             'wrong_answers':sum(r['wrong_answer'] for r in rows),
             'unconfirmed':sum(not r['passed'] and not r['wrong_answer'] for r in rows),
             'recognition_ms':dict(zip(['p50','p95'],np.percentile([r['elapsed_ms'] for r in rows],[50,95]).round(1).tolist()))}
    report={'summary':summary,'samples':samples,'cases_sha256':hashlib.sha256(args.cases.read_bytes()).hexdigest(),
        'index_sha256':hashlib.sha256((ROOT/'output/index/manifest.json').read_bytes()).hexdigest(),
        'card_data_sha256':hashlib.sha256((ROOT/'output/card-data.json').read_bytes()).hexdigest(),'rows':rows}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(summary,ensure_ascii=False),flush=True)
    raise SystemExit(0 if summary['passed']==summary['total'] else 1)


if __name__=='__main__':
    main()
