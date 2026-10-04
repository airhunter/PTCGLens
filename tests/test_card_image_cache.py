import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import cv2
import numpy as np

from viewer import large_card_bytes


class CardImageCacheTest(unittest.TestCase):
    def test_new_full_source_replaces_thumbnail_even_with_older_timestamp(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            card_id = "test_en_001"
            thumb = root / "cache" / f"{card_id}_t" / "__data"
            thumb.parent.mkdir(parents=True)
            thumb.write_bytes(b"thumbnail")
            large_dir = root / "large"
            def extract(bundle, name):
                size = 256 if name.endswith("_t") else 1024
                return np.full((size,size,3),80,dtype=np.uint8)
            with patch("verify_card.extract_card",side_effect=extract) as extract_mock:
                original = large_card_bytes(card_id,root / "cache",large_dir,root / "visual")
                decoded = cv2.imdecode(np.frombuffer(original,np.uint8),cv2.IMREAD_COLOR)
                self.assertEqual(decoded.shape[:2],(240,171))
                self.assertEqual(large_card_bytes(card_id,root / "cache",large_dir,root / "visual"),original)
                self.assertEqual(extract_mock.call_count,1)
                full = root / "cache" / card_id / "__data"
                full.parent.mkdir()
                full.write_bytes(b"full")
                os.utime(full,(1,1))  # 下载源时间戳可能早于已生成的缩略图缓存。
                upgraded = large_card_bytes(card_id,root / "cache",large_dir,root / "visual")
                decoded = cv2.imdecode(np.frombuffer(upgraded,np.uint8),cv2.IMREAD_COLOR)
                self.assertEqual(decoded.shape[:2],(962,686))
                self.assertEqual(extract_mock.call_count,2)

    def test_empty_full_directory_does_not_hide_existing_thumbnail_bundle(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            card_id = "test_en_001"
            (root / "cache" / card_id).mkdir(parents=True)
            thumb = root / "cache" / f"{card_id}_t" / "__data"
            thumb.parent.mkdir()
            thumb.write_bytes(b"thumbnail")
            with patch("verify_card.extract_card",return_value=np.zeros((256,256,3),np.uint8)) as extract:
                large_card_bytes(card_id,root / "cache",root / "large",root / "visual")
                self.assertEqual(extract.call_args.args[1],f"{card_id}_t")


if __name__ == "__main__":
    unittest.main()
