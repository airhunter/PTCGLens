# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path
import json
import platform

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules

root = Path(SPECPATH).parent
generated = root / "build/macos-assets"
version = json.loads((generated / "build-info.json").read_text())["version"]
native = ("objc", "Foundation", "AppKit", "CoreFoundation", "Quartz",
          "ApplicationServices", "ScreenCaptureKit")
hidden = ["platform_backend.macos", "certifi"]
for package in native:
    hidden += collect_submodules(package)
hidden += collect_submodules("UnityPy")

a = Analysis(
    [str(root / "ptcglens.py")],
    pathex=[str(root)],
    binaries=collect_dynamic_libs("fmod_toolkit"),
    datas=[(str(root / "battle_layout.sample.json"), "."),
           (str(root / "card-image-rules.json"), "."),
           (str(root / "translations/local-zh.json"), "translations"),
           (str(root / "LICENSE"), "."),
           (str(generated / "third-party-licenses"), "third-party-licenses"),
           (str(generated / "build-info.json"), ".")]
          # archspec 通过 cpu/../json 读取资料，需要 cpu 实体目录，不能仅有 PYZ 中的模块。
          + collect_data_files("UnityPy") + collect_data_files("archspec", include_py_files=True)
          + collect_data_files("certifi"),
    hiddenimports=hidden,
    hookspath=[],
    runtime_hooks=[str(root / "packaging/runtime_macos.py")],
    excludes=["PyQt5", "PyQt6", "PySide2", "tkinter", "matplotlib", "platform_backend.windows"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="PTCGLens",
          debug=False, strip=False, upx=False, console=False,
          argv_emulation=False, target_arch=platform.machine(), codesign_identity=None)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="PTCGLens")
app = BUNDLE(coll, name="PTCGLens.app", icon=str(generated / "PTCGLens.icns"),
             bundle_identifier="io.github.airhunter.ptcglens", version=version,
             info_plist={
                 "CFBundleDisplayName": "PTCGLens",
                 "CFBundleVersion": version,
                 "LSUIElement": True,
                 "LSMinimumSystemVersion": "14.0",
                 "NSPrincipalClass": "NSApplication",
                 "NSHighResolutionCapable": True,
                 "NSHumanReadableCopyright": "PTCGLens · GPL-3.0-only",
             })
