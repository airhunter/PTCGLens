import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from types import SimpleNamespace
from unittest.mock import Mock

from app_paths import (copy_if_missing, migrate_legacy_cache, migrate_preferences,
                       resource_directory, user_data_directory)


class AppPathsTest(unittest.TestCase):
    def test_system_data_is_independent_of_install_and_working_directory(self):
        with TemporaryDirectory() as folder:
            home = Path(folder)
            self.assertEqual(user_data_directory(system="darwin", home=home, environ={}),
                             home / "Library/Application Support/PTCGLens")
            self.assertEqual(user_data_directory(system="win32", home=home, environ={}),
                             home / "AppData/Local/PTCGLens")
            local = home / "localized app data"
            self.assertEqual(user_data_directory(system="win32", home=home, environ={"LOCALAPPDATA": str(local)}),
                             local / "PTCGLens")
            data = home / "isolated profile"
            self.assertEqual(user_data_directory(environ={"PTCGLENS_DATA_DIR": str(data)}), data)
            self.assertFalse(data.exists())  # 查询路径不产生文件。
            with self.assertRaises(ValueError):
                user_data_directory(environ={"PTCGLENS_DATA_DIR": "relative/output"})

    def test_bundle_assets_are_read_separately_from_user_data(self):
        with TemporaryDirectory() as folder, patch("app_paths.sys._MEIPASS", folder, create=True):
            self.assertEqual(resource_directory(), Path(folder))

    def test_constructing_worker_does_not_preempt_import_of_downloaded_card_cache(self):
        from overlay_app import Recognizer
        with TemporaryDirectory() as folder:
            data, source = Path(folder)/"data", Path(folder)/"project"
            (source/"output/card-sources").mkdir(parents=True)
            (source/"output/card-sources/cached.png").write_bytes(b"cached art")
            with patch("overlay_app.DATA_DIR", data):
                Recognizer(SimpleNamespace(status=Mock()), SimpleNamespace(cache_root=Path(folder)/"game"))
            self.assertFalse((data/"card-sources").exists())
            migrate_legacy_cache(data, source)
            self.assertEqual((data/"card-sources/cached.png").read_bytes(), b"cached art")

    def test_import_preserves_preferences_and_cache_without_copying_logs_or_screenshots(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            source, data = root / "project", root / "user data"
            old = source / "output"
            (old / "index/compact").mkdir(parents=True)
            (old / "index/manifest.json").write_text('{"cards": []}')
            (old / "index/compact/test.npz").write_bytes(b"cached feature")
            (old / "overlay-settings.json").write_text('{"mode": "hover"}')
            (old / "translation-cache.json").write_text('{"source": "translation"}')
            (old / "overlay.lock").write_text("old process lock")
            (old / "overlay.log").write_text("private log")
            (old / "query-failures").mkdir()
            (old / "query-failures/scene.png").write_bytes(b"private scene")
            translations = source / ".tmp/ptcg-live-zh-mod/databases_zh-CN"
            translations.mkdir(parents=True)
            (translations / "names.json").write_text('{}')
            migrate_preferences(data, source)
            self.assertEqual(json.loads((data/"overlay-settings.json").read_text()), {"mode": "hover"})
            self.assertTrue((data/"translations/databases_zh-CN/names.json").is_file())
            migrate_legacy_cache(data, source)
            self.assertEqual((data/"index/compact/test.npz").read_bytes(), b"cached feature")
            self.assertEqual((old/"index/manifest.json").stat().st_mtime_ns,
                             (data/"index/manifest.json").stat().st_mtime_ns)
            self.assertTrue((old/"overlay-settings.json").exists())
            for name in ("overlay.lock", "overlay.log", "query-failures"):
                self.assertFalse((data/name).exists())
            # 修改后的用户设置不能被旧设置覆盖；清掉可重建索引后也不反复导入。
            (data/"overlay-settings.json").write_text('{"mode": "toggle"}')
            (data/"index/compact/test.npz").unlink()
            migrate_preferences(data, source)
            migrate_legacy_cache(data, source)
            self.assertEqual(json.loads((data/"overlay-settings.json").read_text()), {"mode": "toggle"})
            self.assertFalse((data/"index/compact/test.npz").exists())

    def test_failed_copy_never_publishes_partial_index(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            source, destination = root/"old", root/"data/index"
            source.mkdir()
            def partial_copy(src, staged, **kwargs):
                staged.mkdir()
                (staged/"half.npz").write_bytes(b"partial")
                raise OSError("disk full")
            with patch("app_paths.shutil.copytree", side_effect=partial_copy):
                with self.assertRaises(OSError):
                    copy_if_missing(source, destination)
            self.assertFalse(destination.exists())
            self.assertEqual(list(destination.parent.iterdir()), [])

    def test_frozen_app_and_cancelled_import_do_not_copy_developer_data(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            (root/"project/output").mkdir(parents=True)
            (root/"project/output/overlay-settings.json").write_text('{}')
            with patch("app_paths.sys.frozen", True, create=True):
                migrate_preferences(root/"data", root/"project")
                migrate_legacy_cache(root/"data", root/"project")
            self.assertFalse((root/"data").exists())
            migrate_legacy_cache(root/"data", root/"project", cancelled=lambda: True)
            self.assertFalse((root/"data").exists())
