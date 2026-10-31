"""只读随程序资源与用户运行数据分离；导入模块时不创建目录。"""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import sys
import tempfile

from atomic_json import write_json_atomic


def resource_directory() -> Path:
    # PyInstaller 的附带资源位于 _MEIPASS，源码运行时位于本模块旁。
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))


def user_data_directory(*, system=None, home=None, environ=None) -> Path:
    system = system or sys.platform
    home = Path(home) if home is not None else Path.home()
    environ = os.environ if environ is None else environ
    override = environ.get("PTCGLENS_DATA_DIR")
    if override:
        path = Path(override).expanduser()
        if not path.is_absolute():
            raise ValueError("PTCGLENS_DATA_DIR 必须是绝对路径")
        return path
    if system == "darwin":
        return home / "Library/Application Support/PTCGLens"
    if system == "win32":
        return Path(environ.get("LOCALAPPDATA") or home / "AppData/Local") / "PTCGLens"
    return Path(environ.get("XDG_DATA_HOME") or home / ".local/share") / "PTCGLens"


RESOURCE_DIR = resource_directory()
DATA_DIR = user_data_directory()


def default_translation_directory():
    development = RESOURCE_DIR / ".tmp/ptcg-live-zh-mod/databases_zh-CN"
    if not getattr(sys, "frozen", False) and development.is_dir():
        return development
    return DATA_DIR / "translations/databases_zh-CN"


DEFAULT_TRANSLATION_ROOT = default_translation_directory()


def copy_if_missing(source: Path, destination: Path) -> bool:
    """先完整复制到临时位置再发布；中断不留下半份索引，不覆盖新设置。"""
    if destination.exists() or not source.exists() or source.is_symlink():
        return False
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".import-", dir=destination.parent) as folder:
        staged = Path(folder) / destination.name
        if source.is_dir():
            shutil.copytree(source, staged, copy_function=shutil.copy2,
                            ignore=lambda root, names: [name for name in names if (Path(root)/name).is_symlink()])
        else:
            shutil.copy2(source, staged)
        staged.replace(destination)
    return True


def migrate_preferences(data_dir=DATA_DIR, resource_dir=RESOURCE_DIR):
    """源码升级保留快捷键与译文；发布包不扫描外部项目或复制游戏数据。"""
    if getattr(sys, "frozen", False) or data_dir == resource_dir / "output":
        return
    for name in ("overlay-settings.json", "translation-cache.json"):
        copy_if_missing(resource_dir / "output" / name, data_dir / name)
    old_translations = resource_dir / ".tmp/ptcg-live-zh-mod/databases_zh-CN"
    for name in ("names.json", "attks-name.json", "attks-text.json"):
        copy_if_missing(old_translations / name, data_dir / "translations/databases_zh-CN" / name)


def migrate_legacy_cache(data_dir=DATA_DIR, resource_dir=RESOURCE_DIR, *, cancelled=lambda: False):
    """在识别后台一次性导入开发版已生成的缓存；日志、截图、锁不导入。"""
    marker = data_dir / ".legacy-cache-imported.json"
    if (getattr(sys, "frozen", False) or data_dir == resource_dir / "output"
            or marker.exists() or not (resource_dir / "output").is_dir()):
        return
    for name in ("index", "card-thumbnails", "card-data.json", "card-sources", "card-large"):
        if cancelled():
            return
        copy_if_missing(resource_dir / "output" / name, data_dir / name)
    write_json_atomic(marker, {"source": str(resource_dir / "output"), "version": 1})
