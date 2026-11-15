from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from scripts import build_common


class SourceDigestTest(unittest.TestCase):
    def populate(self, root, newline):
        for name in ("app.py", "platform_backend/example.py", "scripts/example.py", "packaging/example.spec",
                     ".github/workflows/example.yml", "battle_layout.sample.json", "card-image-rules.json",
                     "translations/local-zh.json", "docs/windows-portable-guide.txt", "pyproject.toml", "uv.lock", "LICENSE"):
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(("相同的源码" + newline).encode("utf-8"))

    def test_windows_and_unix_checkouts_have_the_same_digest(self):
        with TemporaryDirectory() as directory:
            windows, mac = Path(directory) / "windows", Path(directory) / "mac"
            self.populate(windows, "\r\n")
            self.populate(mac, "\n")
            with patch.object(build_common, "ROOT", windows):
                first = build_common.source_digest()
            with patch.object(build_common, "ROOT", mac):
                self.assertEqual(first, build_common.source_digest())

    def test_source_changes_count_but_private_output_does_not(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            self.populate(root, "\n")
            with patch.object(build_common, "ROOT", root):
                before = build_common.source_digest()
                (root / "output").mkdir()
                (root / "output/private.py").write_text("private", encoding="utf-8")
                self.assertEqual(before, build_common.source_digest())
                (root / "app.py").write_text("changed", encoding="utf-8")
                self.assertNotEqual(before, build_common.source_digest())
