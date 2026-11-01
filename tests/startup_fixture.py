"""模拟首次缺权限的真实 Qt 模态启动流程，验证退出控制不遗留窗口。"""

from pathlib import Path
import sys
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QMessageBox
import overlay_app


def warning(*args):
    dialog = QMessageBox()
    dialog.setText("test missing permission")
    QTimer.singleShot(0, lambda: print("ready", flush=True))
    return dialog.exec()


with patch("overlay_app.set_dpi_awareness"), patch("overlay_app.migrate_preferences"), \
     patch("overlay_app.check_permissions", return_value=["test permission"]), \
     patch("overlay_app.QMessageBox.warning", side_effect=warning):
    raise SystemExit(overlay_app.main([]))
