import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import numpy as np

from cache_search import FAILED_SOURCE_RETRY_NS, update_index


class FailedCacheRetryTest(unittest.TestCase):
    def test_unchanged_failure_is_retried_after_delay_and_can_recover(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            bundle = root/'cache'/'test_en_001'/'version'/'__data'
            bundle.parent.mkdir(parents=True)
            bundle.write_bytes(b'incomplete')
            os.utime(bundle, ns=(1, 1))
            now = 100_000_000_000
            with patch('cache_search.time.time_ns', return_value=now), \
                 patch('cache_search.extract_card', side_effect=ValueError('missing texture')) as extract:
                first = update_index(root/'cache', root/'index')
                original = (root/'index'/'manifest.json').read_bytes()
                second = update_index(root/'cache', root/'index')
            self.assertEqual(extract.call_count, 1)
            self.assertEqual(first['failures'], second['failures'])
            self.assertEqual((root/'index'/'manifest.json').read_bytes(), original)
            with patch('cache_search.time.time_ns', return_value=now+FAILED_SOURCE_RETRY_NS), \
                 patch('cache_search.extract_card', return_value=np.zeros((10,10,3), np.uint8)) as extract, \
                 patch('cache_search.reference_features', return_value=(
                     np.zeros((1,2)), np.zeros((1,128)), (10,10))):
                recovered = update_index(root/'cache', root/'index')
            extract.assert_called_once()
            self.assertEqual(recovered['added'], 1)
            self.assertEqual(recovered['failures'], [])
            self.assertTrue((root/'index'/'test_en_001.npz').is_file())

    def test_source_change_retries_immediately_and_keeps_last_good_record_on_failure(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            bundle = root/'cache'/'test_en_001'/'version'/'__data'
            bundle.parent.mkdir(parents=True)
            bundle.write_bytes(b'first')
            os.utime(bundle, ns=(1, 1))
            now = 100_000_000_000
            index = root/'index'
            index.mkdir()
            (index/'test_en_001.npz').write_bytes(b'last good features')
            good = dict(card_id='test_en_001', variant='full', file='test_en_001.npz',
                        bundle_path=str(bundle.resolve()), bundle_size=1, bundle_mtime_ns=0)
            (index/'manifest.json').write_text(json.dumps({'cards':[good]}))
            with patch('cache_search.time.time_ns', return_value=now), \
                 patch('cache_search.extract_card', side_effect=ValueError('missing texture')) as extract:
                update_index(root/'cache', index)
                update_index(root/'cache', index)
                bundle.write_bytes(b'changed source')
                os.utime(bundle, ns=(2, 2))
                result = update_index(root/'cache', index)
            self.assertEqual(extract.call_count, 2)
            self.assertEqual(result['updated'], 0)
            self.assertEqual(json.loads((index/'manifest.json').read_text())['cards'], [good])
            self.assertEqual((index/'test_en_001.npz').read_bytes(), b'last good features')


if __name__ == '__main__':
    unittest.main()
