"""处理部分 Mac 虚拟环境中的隐藏插件标志，避免 Qt 漏掉已安装插件。"""

from pathlib import Path
import shutil
import sys
import tempfile

from PySide6.QtCore import QCoreApplication, QFileInfo, QLibraryInfo


def prepare_qt_plugins():
    """仅在发现隐藏插件时复制到私有临时目录；调用者持有目录直到应用退出。"""
    if sys.platform != "darwin":
        return None
    root = Path(QLibraryInfo.path(QLibraryInfo.LibraryPath.PluginsPath))
    groups = ("platforms", "imageformats", "styles")
    files = [(group, path) for group in groups for path in (root/group).glob("*.dylib")]
    if not any(QFileInfo(str(path)).isHidden() for _, path in files):
        return None
    directory = tempfile.TemporaryDirectory(prefix="ptcglens-qt-")
    for group, source in files:
        destination = Path(directory.name)/group/source.name
        destination.parent.mkdir(parents=True, exist_ok=True)
        # copyfile 不复制隐藏标志，也不修改安装目录或上游 uv 缓存。
        shutil.copyfile(source, destination)
    QCoreApplication.addLibraryPath(directory.name)
    return directory
