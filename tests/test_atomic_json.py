import json
import threading
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from atomic_json import read_json_retry, write_json_atomic


class AtomicJsonTest(unittest.TestCase):
    def test_failed_replacement_preserves_previous_data_and_cleans_temporary_file(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "cards.json"
            write_json_atomic(path, {"cards": ["old"]})
            with patch("atomic_json.os.replace", side_effect=PermissionError("shared reader")), patch("atomic_json.time.sleep"):
                with self.assertRaises(PermissionError):
                    write_json_atomic(path, {"cards": ["new"]})
            self.assertEqual(json.loads(path.read_text()), {"cards": ["old"]})
            self.assertEqual(list(path.parent.iterdir()), [path])

    def test_concurrent_reader_only_sees_complete_revisions(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "cards.json"
            write_json_atomic(path, {"revision": 0, "cards": [0] * 200})
            stop, observed, errors = threading.Event(), [], []
            def reader():
                while not stop.is_set():
                    try:
                        data = read_json_retry(path)
                        if data["cards"] != [data["revision"]] * 200:
                            errors.append("mixed revision")
                        observed.append(data["revision"])
                    except Exception as exc:
                        errors.append(str(exc))
                    stop.wait(.001)
            thread = threading.Thread(target=reader)
            thread.start()
            try:
                for revision in range(1, 20):
                    write_json_atomic(path, {"revision": revision, "cards": [revision] * 200})
            finally:
                stop.set()
                thread.join(3)
            self.assertTrue(observed)
            self.assertEqual(errors, [])

    def test_reader_retries_sharing_conflict_without_hiding_invalid_json(self):
        path = Path("unused.json")
        with patch.object(Path, "read_text", side_effect=[PermissionError("sharing"), '{"revision":2}']), \
                patch("atomic_json.time.sleep"):
            self.assertEqual(read_json_retry(path), {"revision":2})
        with patch.object(Path, "read_text", return_value="incomplete"):
            with self.assertRaises(json.JSONDecodeError):
                read_json_retry(path)
