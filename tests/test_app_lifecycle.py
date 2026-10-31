from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from PySide6.QtWidgets import QApplication, QLabel, QPushButton

from overlay_app import Overlay, StatusDialog
from qt_runtime import prepare_qt_plugins


class AppLifecycleTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.plugins = prepare_qt_plugins()
        cls.app = QApplication.instance() or QApplication([])

    def test_status_window_close_exits_only_when_tray_unavailable(self):
        for available in (True, False):
            quit_app = Mock()
            dialog = StatusDialog(quit_app)
            with patch("overlay_app.QSystemTrayIcon.isSystemTrayAvailable", return_value=available):
                dialog.show()
                dialog.close()
            self.assertEqual(quit_app.call_count, 0 if available else 1)
            dialog.deleteLater()

    def test_status_always_provides_exit_and_missing_tray_has_no_background_button(self):
        with TemporaryDirectory() as folder:
            controller = SimpleNamespace(stopped=False, status_dialog=None, current_status="ready",
                current_capture_state="paused", args=SimpleNamespace(cache_root=Path(folder)/"game",
                game_cache=Path(folder)/"db", translation_root=Path(folder)/"zh"),
                instruction=lambda: "Ctrl+Alt", worker=Mock(), quit=Mock(),
                open_data_directory=Mock(), open_log_directory=Mock())
            with patch("overlay_app.DATA_DIR", Path(folder)), \
                 patch("overlay_app.QSystemTrayIcon.isSystemTrayAvailable", return_value=False):
                Overlay.show_status(controller)
                self.assertFalse(controller.background_button.isVisible())
                buttons = controller.status_dialog.findChildren(QPushButton)
                quit_button = next(button for button in buttons if button.text() == "退出 PTCGLens")
                quit_button.click()
                controller.quit.assert_called_once()
                self.assertTrue(any(str(Path(folder)) in label.text()
                    for label in controller.status_dialog.findChildren(QLabel)))
                controller.status_dialog.hide()
                controller.status_dialog.deleteLater()

    def test_shutdown_cancels_active_work_hides_ui_and_is_idempotent(self):
        controller = SimpleNamespace(stopped=False, timer=Mock(), popup=Mock(), request_id=7, mode="toggle",
            status_dialog=Mock(), window_monitor=Mock(), hook=Mock(ident=123), worker=Mock(), tray=Mock())
        controller.dismiss = lambda: Overlay.dismiss(controller)
        Overlay.stop(controller)
        Overlay.stop(controller)
        controller.worker.cancel.assert_called_once_with(8)
        controller.popup.hide.assert_called_once()
        self.assertFalse(controller.wanted)
        self.assertEqual(controller.request_id, 8)
        controller.timer.stop.assert_called_once()
        controller.hook.stop.assert_called_once()
        controller.worker.stop_event.set.assert_called_once()
        controller.worker.refresh_requested.set.assert_called_once()
        controller.worker.join.assert_called_once_with(timeout=3)
        controller.status_dialog.hide.assert_called_once()
        controller.tray.hide.assert_called_once()
