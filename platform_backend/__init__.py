"""按需加载系统后端；识别与界面不导入任何原生系统 API。"""

from functools import lru_cache
import importlib
import sys


@lru_cache(maxsize=1)
def get_backend():
    name = {"win32": "windows", "darwin": "macos"}.get(sys.platform)
    if name is None:
        raise RuntimeError("实时采集目前支持 Windows 和 macOS")
    return importlib.import_module(f"platform_backend.{name}")
