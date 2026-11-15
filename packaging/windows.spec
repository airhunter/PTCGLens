# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path
import os
import sys

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules

root = Path(SPECPATH).parent
generated = root / "build/windows-assets"
hidden = ["certifi"] + collect_submodules("platform_backend.windows")
hidden += collect_submodules("windows_capture") + collect_submodules("UnityPy")

a = Analysis(
    [str(root / "ptcglens.py")],
    pathex=[str(root)],
    binaries=collect_dynamic_libs("fmod_toolkit"),
    datas=[
        (str(root / "battle_layout.sample.json"), "."),
        (str(root / "card-image-rules.json"), "."),
        (str(root / "translations/local-zh.json"), "translations"),
        (str(root / "LICENSE"), "."),
        (str(generated / "third-party-licenses"), "third-party-licenses"),
        (str(generated / "build-info.json"), "."),
    ] + collect_data_files("UnityPy")
      + collect_data_files("archspec", include_py_files=True)
      + collect_data_files("certifi"),
    hiddenimports=hidden,
    hookspath=[],
    runtime_hooks=[str(root / "packaging/runtime_windows.py")],
    excludes=["PyQt5", "PyQt6", "PySide2", "tkinter", "matplotlib", "platform_backend.macos"],
    noarchive=False,
)
# 不允许将宿主应用或其他开发工具的同名 DLL 静默带进便携包。
allowed = [Path(sys.prefix).resolve(), Path(sys.base_prefix).resolve(),
           (root / "build").resolve(), Path(os.environ["SystemRoot"]).resolve()]
foreign = [source for _, source, _ in a.binaries
           if not any(Path(source).resolve().is_relative_to(base) for base in allowed)]
if foreign:
    raise RuntimeError(f"发现构建环境之外的二进制依赖，请检查 DLL 搜索路径：{foreign}")
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [], exclude_binaries=True, name="PTCGLens",
    debug=False, strip=False, upx=False, console=False,
    icon=str(generated / "PTCGLens.ico"),
    version=str(generated / "version-info.txt"),
    uac_admin=False, uac_uiaccess=False,
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="PTCGLens")
