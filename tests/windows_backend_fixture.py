"""在非 Windows 主机验证原生适配器的 Python 行为，系统调用使用替身。"""
import ctypes
import importlib.util
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock, patch


def load_windows_module(name):
    package = "_ptcglens_test_windows"
    full_name = f"{package}.{name}"
    if full_name in sys.modules:
        return sys.modules[full_name]
    root = Path(__file__).resolve().parents[1] / "platform_backend/windows"
    if package not in sys.modules:
        module = types.ModuleType(package)
        module.__path__ = [str(root)]
        sys.modules[package] = module
    if name != "window":
        load_windows_module("window")
    spec = importlib.util.spec_from_file_location(full_name, root / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[full_name] = module
    with patch.object(ctypes, "WinDLL", MagicMock(), create=True), \
         patch.object(ctypes, "WINFUNCTYPE", ctypes.CFUNCTYPE, create=True), \
         patch.object(ctypes, "HRESULT", ctypes.c_long, create=True):
        spec.loader.exec_module(module)
    return module
