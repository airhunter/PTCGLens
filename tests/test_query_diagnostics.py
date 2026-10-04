import hashlib
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np

from query_diagnostics import QueryFailureRecorder


class QueryDiagnosticsTest(unittest.TestCase):
    def test_failure_snapshots_are_bounded_and_bound_to_their_metadata(self):
        with TemporaryDirectory() as directory:
            root = Path(directory) / "查询失败"
            recorder = QueryFailureRecorder(root, limit=3)
            for request_id in range(8):
                recorder.save(np.full((20,30,3), request_id, np.uint8), {"request_id":request_id})
            self.assertEqual(len(list(root.glob("*.png"))), 3)
            self.assertEqual(len(list(root.glob("*.json"))), 3)
            self.assertEqual(list(root.glob("*.tmp")), [])
            ids = []
            for path in root.glob("*.json"):
                details = json.loads(path.read_text())
                ids.append(details["request_id"])
                self.assertEqual(details["image_sha256"], hashlib.sha256(path.with_suffix(".png").read_bytes()).hexdigest())
            self.assertEqual(sorted(ids), [5,6,7])
