import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest

from PySide6.QtWidgets import QApplication

from app_instance import AppInstance, send_command
from qt_runtime import prepare_qt_plugins


class AppInstanceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.plugins = prepare_qt_plugins()
        cls.app = QApplication.instance() or QApplication([])

    def test_second_instance_reuses_server_and_quit_releases_lock_for_restart(self):
        with TemporaryDirectory() as folder:
            data = Path(folder)/"profile with spaces"
            for attempt in range(2):
                process = subprocess.Popen([sys.executable, str(Path(__file__).with_name("instance_fixture.py")), str(data)],
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8")
                try:
                    ready = process.stdout.readline().strip()
                    self.assertEqual(ready, "ready", process.stderr.read() if not ready else "")
                    duplicate = AppInstance(data)
                    self.assertFalse(duplicate.start())
                    self.assertTrue(send_command(data, "show"))
                    self.assertEqual(process.stdout.readline().strip(), "shown")
                    env = dict(os.environ, PTCGLENS_DATA_DIR=str(data))
                    completed = subprocess.run([sys.executable, str(Path(__file__).resolve().parents[1]/"ptcglens.py"), "--quit"],
                        env=env, capture_output=True, text=True, encoding="utf-8", timeout=5)
                    self.assertEqual(completed.returncode, 0, completed.stderr)
                    self.assertEqual(process.wait(timeout=5), 0)
                    duplicate.close()
                finally:
                    if process.poll() is None:
                        process.kill()
                    process.communicate(timeout=5)
            self.assertFalse((data/"overlay.lock").exists())

    def test_crash_leaves_recoverable_lock_and_socket(self):
        with TemporaryDirectory() as folder:
            process = subprocess.Popen([sys.executable, str(Path(__file__).with_name("instance_fixture.py")), folder],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8")
            replacement = AppInstance(folder)
            try:
                ready = process.stdout.readline().strip()
                self.assertEqual(ready, "ready", process.stderr.read() if not ready else "")
                process.kill()
                process.wait(timeout=5)
                self.assertTrue(replacement.start())
            finally:
                if process.poll() is None:
                    process.kill()
                process.communicate(timeout=5)
                replacement.close()

    def test_no_instance_is_a_bounded_failure_and_other_profiles_are_independent(self):
        with TemporaryDirectory() as folder:
            data = Path(folder)/"profile"
            self.assertFalse(send_command(data, "quit", timeout=100))
            instance, other = AppInstance(data), AppInstance(Path(folder)/"other")
            try:
                self.assertTrue(instance.start())
                self.assertTrue(other.start())
            finally:
                other.close()
                instance.close()
