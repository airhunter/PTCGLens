"""从锁定环境构建 Mac 测试包；中间文件在 build/ 和私有临时目录，归档在 dist/。"""

import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from release_version import project_version
from scripts.build_common import build_info


def main():
    if sys.platform != "darwin":
        raise SystemExit("Mac 应用必须在 macOS 上构建。")
    if platform.machine() != "arm64":
        raise SystemExit("首个 Mac 测试版仅构建 Apple Silicon arm64。")
    from PIL import Image, ImageDraw
    import importlib.metadata

    generated = ROOT / "build/macos-assets"
    generated.mkdir(parents=True, exist_ok=True)
    # 用代码绘制卡牌与放大镜图标，避免依赖外部素材。
    image = Image.new("RGBA", (1024, 1024))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((25,25,999,999), radius=210, fill="#18232e")
    draw.rounded_rectangle((215,160,715,845), radius=55, fill="#b8f4df")
    draw.rounded_rectangle((255,205,675,505), radius=25, fill="#247b72")
    for y, right in ((575,620),(650,570),(725,530)):
        draw.rounded_rectangle((275,y,right,y+28), radius=14, fill="#247b72")
    draw.ellipse((555,495,860,800), fill="#18232e", outline="#7de5bd", width=48)
    draw.line((805,752,920,875), fill="#7de5bd", width=65)
    image.save(generated / "PTCGLens.icns", format="ICNS")

    licenses = generated / "third-party-licenses"
    licenses.mkdir(exist_ok=True)
    for distribution in importlib.metadata.distributions():
        for file in distribution.files or ():
            if "license" in file.name.casefold() or "licenses" in file.parts:
                source = Path(distribution.locate_file(file))
                if source.is_file():
                    destination = licenses / distribution.metadata["Name"] / Path(*file.parts[1:])
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(source, destination)

    version = project_version(ROOT)
    info = build_info(version, platform.machine(), "macOS")
    (generated/"build-info.json").write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
    build_environment = dict(os.environ)
    build_environment["PYINSTALLER_CONFIG_DIR"] = str(ROOT/"build/pyinstaller-cache")
    # 可使用已经安装的独立 Command Line Tools；不切换系统 Xcode，也不自动接受许可。
    tools = Path("/Library/Developer/CommandLineTools")
    if "DEVELOPER_DIR" not in build_environment and (tools/"usr/bin/lipo").is_file():
        build_environment["DEVELOPER_DIR"] = str(tools)
    # Documents 等同步目录可能自动给 framework 添加 FinderInfo，导致签名失效。
    # 原生应用在本机临时目录完成签名，只把归档产物交付到项目 dist/。
    with tempfile.TemporaryDirectory(prefix="ptcglens-macos-dist-") as distribution:
        subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
                        "--distpath", distribution, "--workpath", str(ROOT/"build/pyinstaller-macos"),
                        str(ROOT/"packaging/macos.spec")], check=True, cwd=ROOT, env=build_environment)
        finish_bundle(version, Path(distribution)/"PTCGLens.app")


def finish_bundle(version, app):
    # 只清理本次构建产物中不允许参与签名的 Finder 元数据；不清除隔离等安全属性。
    for name in ("com.apple.FinderInfo", "com.apple.ResourceFork"):
        subprocess.run(["/usr/bin/xattr", "-dr", name, str(app)], check=True)
    subprocess.run(["codesign", "--force", "--deep", "--sign", "-", "--timestamp=none", str(app)], check=True)
    subprocess.run(["codesign", "--verify", "--deep", "--strict", str(app)], check=True)
    (ROOT/"dist").mkdir(exist_ok=True)
    archive = ROOT / f"dist/PTCGLens-{version.public}-macos-arm64.zip"
    if archive.exists():
        raise FileExistsError(f"已有同版归档，不覆盖：{archive}")
    subprocess.run(["ditto", "-c", "-k", "--sequesterRsrc", "--keepParent", str(app), str(archive)], check=True)
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    archive.with_suffix(".zip.sha256").write_text(f"{digest}  {archive.name}\n", encoding="utf-8")
    result = {"archive": str(archive), "sha256": digest, "archive_bytes": archive.stat().st_size}
    info = json.loads((ROOT/"build/macos-assets/build-info.json").read_text(encoding="utf-8"))
    result.update({key: info[key] for key in ("version", "tag", "base_revision", "source_sha256")})
    (ROOT/"dist/macos-latest-build.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
