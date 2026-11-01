from contextlib import redirect_stderr
import io
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

import ptcglens


class AppEntryTest(unittest.TestCase):
    def test_package_check_is_explicit_and_does_not_request_capture_permissions(self):
        with patch("package_check.run", return_value=0) as check, \
             patch("live_capture.check_permissions") as permissions, patch("overlay_app.main") as start:
            self.assertEqual(ptcglens.main(["--self-check", "report.json", "--check-network"]), 0)
        check.assert_called_once_with(Path("report.json"), network=True)
        permissions.assert_not_called()
        start.assert_not_called()

    def test_explicit_source_web_mode_keeps_services_and_stops_both(self):
        with TemporaryDirectory() as folder:
            data = Path(folder)
            (data/"index").mkdir()
            (data/"index/manifest.json").write_text('{}')
            (data/"card-data.json").write_text('{}')
            viewer, recognizer = Mock(), Mock()
            viewer.poll.return_value = recognizer.poll.return_value = None
            with patch("ptcglens.DATA_DIR", data), \
                 patch("ptcglens.subprocess.Popen", side_effect=[viewer, recognizer]) as spawn, \
                 patch("ptcglens.time.sleep", side_effect=KeyboardInterrupt):
                self.assertEqual(ptcglens.main(["--web", "--port", "9876", "--capture-mode", "screen"]), 0)
            commands = [call.args[0] for call in spawn.call_args_list]
            self.assertEqual(commands[0][1:], [str(ptcglens.ROOT/"viewer.py"), "--port", "9876"])
            self.assertEqual(commands[1][1:], [str(ptcglens.ROOT/"battle_live.py"), "--capture-mode", "screen"])
            for service in (viewer, recognizer):
                service.terminate.assert_called_once()
                service.wait.assert_called_once_with(timeout=5)

    def test_no_arguments_and_old_overlay_argument_launch_same_desktop_mode(self):
        with patch("overlay_app.main", return_value=7) as start:
            self.assertEqual(ptcglens.main([]), 7)
            self.assertEqual(ptcglens.main(["--overlay"]), 7)
        self.assertEqual([call.args for call in start.call_args_list], [([],), ([],)])

    def test_desktop_options_are_forwarded(self):
        with patch("overlay_app.main", return_value=0) as start:
            ptcglens.main(["--cache-root", "custom cache", "--translation-root", "custom zh", "--diagnose-input"])
        start.assert_called_once_with(["--cache-root", "custom cache", "--translation-root", "custom zh", "--diagnose-input"])

    def test_web_options_are_not_silently_ignored_and_frozen_web_does_not_spawn_itself(self):
        for options in (["--port", "9000"], ["--capture-mode", "screen"], ["--quit", "--check-permissions"],
                        ["--check-network"], ["--self-check", "report.json", "--check-permissions"]):
            with self.subTest(options=options), redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as error:
                    ptcglens.main(options)
                self.assertEqual(error.exception.code, 2)
        with patch("ptcglens.sys.frozen", True, create=True), patch("ptcglens.subprocess.Popen") as spawn, \
             redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                ptcglens.main(["--web"])
        spawn.assert_not_called()

    def test_quit_never_checks_capture_permissions_or_starts_overlay(self):
        with patch("PySide6.QtCore.QCoreApplication.instance", return_value=Mock()), \
             patch("app_instance.send_command", return_value=True) as send, \
             patch("overlay_app.main") as start, patch("live_capture.check_permissions") as permissions:
            self.assertEqual(ptcglens.main(["--quit"]), 0)
        send.assert_called_once_with(ptcglens.DATA_DIR, "quit")
        permissions.assert_not_called()
        start.assert_not_called()

    def test_permission_check_defaults_to_desktop_but_web_only_needs_capture(self):
        with patch("live_capture.check_permissions", return_value=[]) as check:
            self.assertEqual(ptcglens.main(["--check-permissions"]), 0)
            check.assert_called_with(input_required=True)
            self.assertEqual(ptcglens.main(["--web", "--check-permissions"]), 0)
            check.assert_called_with(input_required=False)
