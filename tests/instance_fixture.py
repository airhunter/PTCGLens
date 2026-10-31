"""测试用独立控制服务，不加载游戏或采集后端。"""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QCoreApplication, QTimer
from app_instance import AppInstance

app = QCoreApplication([])
instance = AppInstance(Path(sys.argv[1]))
if not instance.start():
    raise SystemExit(3)
instance.show_requested.connect(lambda: print("shown", flush=True))
instance.quit_requested.connect(app.quit)
QTimer.singleShot(15000, app.quit)
print("ready", flush=True)
result = app.exec()
instance.close()
raise SystemExit(result)
