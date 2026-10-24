import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import cv2

from recognition_index import FeatureIndex, compact_records
from battle_live import load_resources
from cache_search import read_image
from test_pointer_recognition import texture


class RecognitionIndexTest(unittest.TestCase):
    def test_reload_reuses_unchanged_images_and_compact_features(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            records=[{"card_id":cid,"file":f"{cid}.npz"} for cid in ("one","two","three")]
            manifest=root/"manifest.json"
            manifest.write_text(json.dumps({"cards":records[:2]}))
            data=root/"cards.json"
            data.write_text(json.dumps({"cards":{r["card_id"]:{} for r in records}}))
            for i,record in enumerate(records):
                cv2.imwrite(str(root/f'{record["card_id"]}.png'),texture(10+i))
            first=load_resources(root,root,data)
            manifest.write_text(json.dumps({"cards":records}))
            with patch("battle_live.read_image",wraps=read_image) as read:
                second=load_resources(root,root,data,previous=first)
            self.assertEqual([call.args[0].stem for call in read.call_args_list],["three"])
            old_images,new_images=dict(first[0]),dict(second[0])
            self.assertIs(new_images["one"],old_images["one"])
            old_features={r["card_id"]:r for r in first[3].records}
            new_features={r["card_id"]:r for r in second[3].records}
            self.assertIs(new_features["one"],old_features["one"])
            # 源资源更新，即使缩略图本身未变，也必须重新加载并计算。
            records[0]["bundle_mtime_ns"]=123
            manifest.write_text(json.dumps({"cards":records}))
            third=load_resources(root,root,data,previous=second)
            changed={r["card_id"]:r for r in third[3].records}
            self.assertIsNot(changed["one"],new_features["one"])
            self.assertIs(changed["two"],new_features["two"])

    def test_membership_does_not_read_missing_feature_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root/'manifest.json').write_text(json.dumps({'cards':[
                {'card_id':'one','file':'one.npz'}]}), encoding='utf8')
            index = FeatureIndex(root)
            with patch('recognition_index.np.load',side_effect=AssertionError('eager read')):
                self.assertIn('one',index)
                self.assertNotIn('other',index)
                self.assertEqual(list(index),['one'])
                self.assertEqual(len(index),1)
            with self.assertRaises(FileNotFoundError):
                index['one']
            self.assertEqual(len(index.cached),0)

    def test_feature_cache_eviction_retains_recently_used_card(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            records = [{'card_id':str(i),'file':f'{i}.npz'} for i in range(3)]
            (root/'manifest.json').write_text(json.dumps({'cards':records}),encoding='utf8')
            for i in range(3):
                np.savez(root/f'{i}.npz',points=np.array([[i,i]],np.float32),
                    descriptors=np.full((1,128),i,np.float32),width=220,height=308)
            index = FeatureIndex(root,limit=2)
            first = index['0']
            index['1']
            self.assertIs(index['0'],first)
            index['2']
            self.assertEqual(list(index.cached),['0','2'])
            self.assertEqual(index['1']['dimensions'],(220,308))
            self.assertEqual(list(index.cached),['2','1'])
            self.assertEqual(index['1']['descriptors'][0,0],1)

    def test_compact_features_use_common_scale_without_changing_visuals(self):
        images = [texture(10),texture(20)]
        copies = [image.copy() for image in images]
        records = compact_records(list(zip(('one','two'),images)))
        self.assertEqual([r['card_id'] for r in records],['one','two'])
        for record,original,copy in zip(records,images,copies):
            self.assertEqual(record['dimensions'],(220,308))
            self.assertLessEqual(len(record['descriptors']),610)  # SIFT 保留同响应值的点。
            self.assertGreater(len(record['descriptors']),25)
            self.assertTrue(np.array_equal(original,copy))
            self.assertTrue((record['points'] >= 0).all())
            self.assertTrue((record['points'] < (220,308)).all())
