"""Mac 冻结包命令行自检、架构与签名检查；不请求屏幕／输入权限。"""

import datetime
import hashlib
import json
import os
from pathlib import Path
import plistlib
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from release_version import project_version
from repository_revision import repository_revision
from scripts.build_common import source_digest


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def command(arguments, **kwargs):
    return subprocess.run(list(map(str, arguments)), check=True, capture_output=True, text=True,
                          encoding="utf-8", timeout=60, **kwargs)


def main():
    if sys.platform != "darwin":
        raise SystemExit("Mac 包必须在 macOS 上验证。")
    version = project_version()
    archive = ROOT / f"dist/PTCGLens-{version.public}-macos-arm64.zip"
    work = ROOT / "output" / ("macos-package-verification-" + datetime.datetime.now().strftime("%Y%m%d-%H%M%S"))
    work.mkdir(parents=True)
    report = {"ok": False, "archive": str(archive), "checks": {}}
    try:
        checksum = digest(archive)
        assert checksum == archive.with_suffix(".zip.sha256").read_text(encoding="utf-8").split()[0]
        forbidden = {".git", "local-resources", "output", "query-failures", "card-thumbnails",
                     "card-sources", "card-large", "overlay-settings.json", "translation-cache.json", "card-data.json"}
        with zipfile.ZipFile(archive) as package:
            assert package.testzip() is None
            for name in package.namelist():
                assert not Path(name).is_absolute() and ".." not in Path(name).parts
                assert not forbidden.intersection(Path(name).parts)
        install = work / "中文 路径" / "应用程序"
        install.mkdir(parents=True)
        command(["/usr/bin/ditto", "-x", "-k", archive, install])
        app = install / "PTCGLens.app"
        exe = app / "Contents/MacOS/PTCGLens"
        assert command(["/usr/bin/lipo", "-archs", exe]).stdout.strip() == "arm64"
        command(["/usr/bin/codesign", "--verify", "--deep", "--strict", app])
        with (app / "Contents/Info.plist").open("rb") as stream:
            info = plistlib.load(stream)
        assert info["CFBundleShortVersionString"] == version.base
        assert info["CFBundleVersion"] == str(version.build_number)
        assert info["PTCGLensReleaseVersion"] == version.public
        report["checks"]["archive_architecture_signature"] = {"ok": True, "architecture": "arm64",
            "ad_hoc_signature_verified": True, "bundle_version": info["CFBundleVersion"]}
        environment = {key: value for key, value in os.environ.items()
                       if not key.startswith(("PYTHON", "UV_", "_PYI", "DYLD_"))
                       and key not in ("VIRTUAL_ENV", "__PYVENV_LAUNCHER__", "QT_PLUGIN_PATH", "QT_QPA_PLATFORM_PLUGIN_PATH")}
        profile = work / "隔离用户数据"
        environment.update(PATH="/usr/bin:/bin:/usr/sbin:/sbin", PTCGLENS_DATA_DIR=str(profile))
        cwd = work / "空 工作目录"
        cwd.mkdir()
        check_path = work / "frozen-self-check.json"
        command([exe, "--self-check", check_path], cwd=cwd, env=environment)
        check = json.loads(check_path.read_text(encoding="utf-8"))
        assert check["ok"] and check["frozen"]
        build = check["checks"]["resources"]["detail"]["build"]
        assert build["version"] == version.public and build["tag"] == version.tag
        assert build["base_revision"] == repository_revision(ROOT)
        assert build["source_sha256"] == source_digest()
        for name in ("opencv", "unity_texture", "native_backend"):
            module = check["checks"][name]["detail"]["module"]
            assert Path(module).resolve().is_relative_to(app.resolve()), module
        command(["/usr/bin/codesign", "--verify", "--deep", "--strict", app])
        report["checks"]["self_check"] = {"ok": True, "report": str(check_path), "checks": check["checks"],
                                           "signature_valid_after_run": True}
        absent = subprocess.run([str(exe), "--quit"], cwd=cwd, env=environment, capture_output=True, timeout=15)
        assert absent.returncode == 1, "No-instance quit should be a bounded failure"
        report["checks"]["no_instance_quit"] = {"ok": True, "exit_code": absent.returncode}
        report.update(ok=True, version=version.public, base_revision=build["base_revision"],
                      source_sha256=build["source_sha256"], archive_sha256=checksum)
    except Exception as error:
        report["error"] = f"{type(error).__name__}: {error}"
        if isinstance(error, subprocess.CalledProcessError):
            report["stdout"] = error.stdout
            report["stderr"] = error.stderr
    finally:
        report["finished_at"] = datetime.datetime.now().astimezone().isoformat()
        destination = work / "verification.json"
        destination.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"ok": report["ok"], "report": str(destination), "error": report.get("error")}, ensure_ascii=False))
    raise SystemExit(0 if report["ok"] else 1)


if __name__ == "__main__":
    main()
