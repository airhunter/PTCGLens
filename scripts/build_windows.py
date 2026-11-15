"""从锁定的 Windows x64 环境构建便携测试包，不打包用户数据或游戏素材。"""

import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import sysconfig
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from release_version import project_version
from scripts.build_common import build_info, source_digest


def prepare_assets(generated, version):
    from PIL import Image, ImageDraw
    from PyInstaller.utils.win32.versioninfo import (
        FixedFileInfo, StringFileInfo, StringStruct, StringTable, VarFileInfo, VarStruct, VSVersionInfo,
    )

    generated.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGBA", (1024, 1024))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((25, 25, 999, 999), radius=210, fill="#18232e")
    draw.rounded_rectangle((215, 160, 715, 845), radius=55, fill="#b8f4df")
    draw.rounded_rectangle((255, 205, 675, 505), radius=25, fill="#247b72")
    for y, right in ((575, 620), (650, 570), (725, 530)):
        draw.rounded_rectangle((275, y, right, y + 28), radius=14, fill="#247b72")
    draw.ellipse((555, 495, 860, 800), fill="#18232e", outline="#7de5bd", width=48)
    draw.line((805, 752, 920, 875), fill="#7de5bd", width=65)
    image.save(generated / "PTCGLens.ico", sizes=[(size, size) for size in (16, 24, 32, 48, 64, 128, 256)])
    numbers = version.windows
    fields = {
        "CompanyName": "PTCGLens contributors", "ProductName": "PTCGLens",
        "FileDescription": "PTCGLens 卡牌识别与双语浮卡（测试版）",
        "FileVersion": ".".join(map(str, numbers)), "ProductVersion": version.public, "OriginalFilename": "PTCGLens.exe",
        "InternalName": "PTCGLens", "LegalCopyright": "PTCGLens contributors · GPL-3.0-only",
    }
    resource = VSVersionInfo(
        ffi=FixedFileInfo(filevers=numbers, prodvers=numbers, mask=0x3F, flags=0,
                          OS=0x40004, fileType=1, subtype=0, date=(0, 0)),
        kids=[StringFileInfo([StringTable("040904B0", [StringStruct(k, v) for k, v in fields.items()])]),
              VarFileInfo([VarStruct("Translation", [0x0409, 1200])])],
    )
    (generated / "version-info.txt").write_text(str(resource), encoding="utf-8")
    licenses = generated / "third-party-licenses"
    inventory = []
    for distribution in importlib.metadata.distributions():
        copied = []
        for file in distribution.files or ():
            if ".." in file.parts or not ("license" in file.name.casefold()
                    or any(part.casefold() == "licenses" for part in file.parts)):
                continue
            source = Path(distribution.locate_file(file))
            if source.is_file():
                relative = Path(distribution.metadata["Name"], *file.parts)
                destination = licenses / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, destination)
                copied.append(relative.as_posix())
        inventory.append({"name": distribution.metadata["Name"], "version": distribution.version,
                          "license_files": copied})
    licenses.mkdir(parents=True, exist_ok=True)
    (licenses / "inventory.json").write_text(json.dumps(inventory, ensure_ascii=False, indent=2), encoding="utf-8")
    info = build_info(version, "x64", "Windows")
    info["packages"] = {item["name"]: item["version"] for item in inventory}
    (generated / "build-info.json").write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")


def finish_package(app, generated, version):
    shutil.copy2(ROOT / "LICENSE", app / "LICENSE.txt")
    shutil.copy2(generated / "build-info.json", app / "build-info.json")
    shutil.copy2(ROOT / "docs/windows-portable-guide.txt", app / "使用说明.txt")
    # 保持批处理内容为 ASCII，避免受系统默认代码页影响；路径变量仍使用 Unicode。
    (app / "Self-Check.cmd").write_bytes(
        b'@echo off\r\nsetlocal\r\nset "PTCGLENS_REPORT=%LOCALAPPDATA%\\PTCGLens\\diagnostics\\portable-self-check.json"\r\n'
        b'start "" /wait "%~dp0PTCGLens.exe" --self-check "%PTCGLENS_REPORT%"\r\n'
        b'if errorlevel 1 (echo Self-check FAILED.) else (echo Self-check passed.)\r\n'
        b'echo Report: %PTCGLENS_REPORT%\r\npause\r\n')
    (app / "Exit-PTCGLens.cmd").write_bytes(
        b'@echo off\r\nstart "" /wait "%~dp0PTCGLens.exe" --quit\r\n')
    name = f"PTCGLens-{version.public}-windows-x64"
    distribution = ROOT / "dist"
    distribution.mkdir(exist_ok=True)
    expanded = distribution / name / "PTCGLens"
    expanded.parent.mkdir()  # 不覆盖已有交付物。
    shutil.copytree(app, expanded)
    archive = distribution / f"{name}.zip"
    with zipfile.ZipFile(archive, "x", zipfile.ZIP_DEFLATED, compresslevel=6) as output:
        for source in sorted(expanded.rglob("*")):
            if source.is_file():
                output.write(source, source.relative_to(expanded.parent).as_posix())
    with archive.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    archive.with_suffix(".zip.sha256").write_text(f"{digest}  {archive.name}\n", encoding="utf-8")
    result = {"archive": str(archive), "executable": str(expanded / "PTCGLens.exe"),
              "sha256": digest, "archive_bytes": archive.stat().st_size}
    info = json.loads((generated / "build-info.json").read_text(encoding="utf-8"))
    result.update({key: info[key] for key in ("version", "tag", "base_revision", "source_sha256")})
    (distribution / "windows-latest-build.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)


def main():
    if sys.platform != "win32" or sysconfig.get_platform() != "win-amd64":
        raise SystemExit("本脚本需要在 Windows x64 的 64 位 Python 环境中运行。")
    version = project_version(ROOT)
    generated = ROOT / "build/windows-assets"
    prepare_assets(generated, version)
    environment = dict(os.environ, PYINSTALLER_CONFIG_DIR=str(ROOT / "build/pyinstaller-cache"))
    windows = Path(environment.get("SystemRoot", "C:/Windows"))
    # 防止继承宿主工具的 DLL 搜索路径，例如 Poppler 的同名 ICU DLL。
    # Qt 依赖 Windows 的非版本化 ICU 接口，错误收集宿主 ICU 会在解压后报缺失入口点。
    environment["PATH"] = os.pathsep.join(map(str, (
        Path(sys.executable).parent, Path(sys.base_prefix), windows / "System32", windows,
    )))
    for key in ("PYTHONPATH", "PYTHONHOME", "__PYVENV_LAUNCHER__", "QT_PLUGIN_PATH",
                "QT_QPA_PLATFORM_PLUGIN_PATH", "QML2_IMPORT_PATH"):
        environment.pop(key, None)
    # 临时构建输出始终位于本项目 build/ 内；不清理用户选择的外部目录。
    with tempfile.TemporaryDirectory(prefix="windows-dist-", dir=ROOT / "build") as folder:
        distribution = Path(folder).resolve()
        if not distribution.is_relative_to((ROOT / "build").resolve()):
            raise ValueError("构建目录越界")
        subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
                        "--distpath", str(distribution), "--workpath", str(ROOT / "build/pyinstaller-windows"),
                        str(ROOT / "packaging/windows.spec")], cwd=ROOT, env=environment, check=True)
        finish_package(distribution / "PTCGLens", generated, version)


if __name__ == "__main__":
    main()
