import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

from recognition_index import FeatureIndex, compact_records
from test_pointer_recognition import texture


class RecognitionIndexTest(unittest.TestCase):
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
