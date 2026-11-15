"""仅用命令行和日志验证 Windows ZIP；不截图、不注入输入、不启动游戏采集。"""

import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from release_version import project_version
from repository_revision import repository_revision
from scripts.build_common import source_digest


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def directory_digest(root):
    return {p.relative_to(root).as_posix(): digest(p) for p in sorted(root.rglob("*")) if p.is_file()}


def clean_environment(profile):
    environment = {key: value for key, value in os.environ.items()
                   if not key.upper().startswith(("PYTHON", "UV_", "_PYI"))
                   and key.upper() not in ("VIRTUAL_ENV", "__PYVENV_LAUNCHER__", "QT_PLUGIN_PATH",
                                           "QT_QPA_PLATFORM_PLUGIN_PATH", "QT_QPA_PLATFORM")}
    windows = Path(environment.get("SystemRoot", "C:/Windows"))
    environment["PATH"] = os.pathsep.join(str(p) for p in (windows / "System32", windows))
    environment["PTCGLENS_DATA_DIR"] = str(profile.resolve())
    return environment


def execute(exe, arguments, cwd, environment, timeout=45):
    result = subprocess.run([str(exe), *map(str, arguments)], cwd=cwd, env=environment,
                            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            creationflags=subprocess.CREATE_NO_WINDOW, timeout=timeout)
    if result.returncode:
        raise RuntimeError(f"{exe.name} {arguments}: exit {result.returncode}; "
                           + result.stderr.decode("utf-8", errors="replace")[-2000:])
    return result.returncode


def lifecycle(exe, profile, cwd, empty_cache):
    environment = clean_environment(profile)
    # 使用真实 qwindows 插件，但空缓存使识别线程停在准备阶段，不会启动采集或输入钩子。
    arguments = ["--cache-root", str(empty_cache), "--game-cache", str(empty_cache),
                 "--translation-root", str(empty_cache)]
    log_path = profile / "logs/overlay.log"
    previous = log_path.read_text(encoding="utf-8") if log_path.exists() else ""
    process = subprocess.Popen([str(exe), *arguments], cwd=cwd, env=environment,
                               stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               creationflags=subprocess.CREATE_NO_WINDOW)
    try:
        deadline = time.monotonic() + 30
        while True:
            if process.poll() is not None:
                raise RuntimeError(f"Frozen startup exited early: {process.returncode}")
            full_log = log_path.read_text(encoding="utf-8") if log_path.exists() else ""
            new_log = full_log[len(previous):]
            if "首次准备：" in new_log and "Qt 平台：windows" in new_log:
                break
            if "启动失败" in new_log or "识别启动失败" in new_log:
                raise RuntimeError(new_log)
            if time.monotonic() >= deadline:
                raise TimeoutError("Startup did not enter missing-cache preparation: " + new_log[-3000:])
            time.sleep(.1)
        lock = profile / "overlay.lock"
        owner = int(lock.read_text(encoding="utf-8").splitlines()[0])
        if owner != process.pid:
            raise AssertionError(f"Expected frozen process {process.pid}, lock belongs to {owner}")
        execute(exe, arguments, cwd, environment, timeout=15)
        if process.poll() is not None or int(lock.read_text(encoding="utf-8").splitlines()[0]) != owner:
            raise AssertionError("Repeated launch replaced the original instance")
        execute(exe, ["--quit"], cwd, environment, timeout=15)
        code = process.wait(timeout=15)
        if code != 0 or lock.exists():
            raise AssertionError(f"Exit did not cleanly release lock: {code}")
        full_log = log_path.read_text(encoding="utf-8")
        if "PTCGLens 退出清理完成。" not in full_log[len(previous):]:
            raise AssertionError("Missing normal shutdown log")
        if "鼠标查询已注册" in full_log[len(previous):] or "浮卡关闭键已注册" in full_log[len(previous):]:
            raise AssertionError("Empty-cache lifecycle unexpectedly enabled input hooks")
        return {"pid": owner, "exit_code": code, "qt_platform": "windows", "missing_cache_wait": True,
                "duplicate_reused_instance": True, "lock_released": True, "log": str(log_path)}
    finally:
        if process.poll() is None:
            try:
                execute(exe, ["--quit"], cwd, environment, timeout=5)
                process.wait(timeout=8)
            except (subprocess.TimeoutExpired, RuntimeError):
                process.kill()
                process.wait(timeout=5)


def verify(archive, work, source_data=None):
    import pefile

    report = {"archive": str(archive), "archive_sha256": digest(archive), "checks": {}, "ok": False}
    checks = report["checks"]
    try:
        expected_hash = archive.with_suffix(".zip.sha256").read_text(encoding="utf-8").split()[0]
        assert report["archive_sha256"] == expected_hash, "ZIP checksum mismatch"
        install = work / "中文 路径" / "第一次解压"
        install.mkdir(parents=True)
        forbidden = {".git", "local-resources", "output", "query-failures", "card-thumbnails",
                     "card-sources", "card-large", "overlay-settings.json", "translation-cache.json", "card-data.json"}
        with zipfile.ZipFile(archive) as package:
            for entry in package.infolist():
                destination = (install / entry.filename).resolve()
                assert destination.is_relative_to(install.resolve()), "ZIP path escapes extraction directory"
                assert not forbidden.intersection(Path(entry.filename).parts), "Private runtime data in ZIP"
                assert not Path(entry.filename).name.startswith(".env"), "Environment configuration in ZIP"
            assert package.testzip() is None, "Corrupt ZIP member"
            package.extractall(install)
        app = install / "PTCGLens"
        exe = app / "PTCGLens.exe"
        assert exe.is_file() and (app / "_internal").is_dir()
        binary = pefile.PE(str(exe), fast_load=True)
        try:
            assert binary.FILE_HEADER.Machine == 0x8664, "Not an AMD64 executable"
            assert binary.OPTIONAL_HEADER.Subsystem == 2, "Not a windowed executable"
        finally:
            binary.close()
        checks["archive"] = {"ok": True, "no_user_data": True, "pe_machine": "AMD64", "subsystem": "WINDOWS_GUI"}
        before_install = directory_digest(app)
        cwd = work / "空 工作目录"
        cwd.mkdir()
        profile = work / "隔离自检数据"
        if source_data and (source_data / "index/manifest.json").is_file():
            (profile / "index").mkdir(parents=True)
            shutil.copy2(source_data / "index/manifest.json", profile / "index/manifest.json")
        self_report_path = work / "frozen-self-check.json"
        environment = clean_environment(profile)
        execute(exe, ["--self-check", self_report_path], cwd, environment)
        native_report = json.loads(self_report_path.read_text(encoding="utf-8"))
        assert native_report["ok"] and native_report["frozen"], "Frozen self-check failed"
        build = native_report["checks"]["resources"]["detail"]["build"]
        version = project_version()
        assert build["version"] == version.public and build["tag"] == version.tag
        assert build["base_revision"] == repository_revision(ROOT), "Package commit differs from verification source"
        assert build["source_sha256"] == source_digest(), "Package source digest differs from verification source"
        assert archive.name == f"PTCGLens-{version.public}-windows-x64.zip"
        report.update({"version": build["version"], "base_revision": build["base_revision"],
                       "source_sha256": build["source_sha256"]})
        assert Path(native_report["executable"]).resolve() == exe.resolve()
        assert Path(native_report["data"]).resolve() == profile.resolve()
        for name in ("opencv", "unity_texture", "native_backend"):
            module = native_report["checks"][name]["detail"]["module"]
            assert Path(module).resolve().is_relative_to(app.resolve()), f"External module dependency: {module}"
        capture_module = native_report["checks"]["native_backend"]["detail"]["capture_module"]
        assert Path(capture_module).resolve().is_relative_to(app.resolve())
        checks["self_check"] = {"ok": True, "report": str(self_report_path),
                                "checks": native_report["checks"], "python_not_on_path": True}
        empty_cache = work / "没有游戏缓存"
        empty_cache.mkdir()
        life_profile = work / "独立用户 设置"
        life_profile.mkdir()
        settings = {"shortcut": "Ctrl+Alt+Q", "button": "right", "keep_in_game": True,
                    "image_preference": "local", "mode": "toggle", "hover_delay": 1.0}
        sentinel = {hashlib.sha256(b"Portable cache persistence").hexdigest(): "便携包缓存保留测试"}
        for filename, contents in (("overlay-settings.json", settings), ("translation-cache.json", sentinel)):
            (life_profile / filename).write_text(json.dumps(contents, ensure_ascii=False, indent=2), encoding="utf-8")
        saved = {filename: digest(life_profile / filename)
                 for filename in ("overlay-settings.json", "translation-cache.json")}
        checks["launch_duplicate_exit"] = {"ok": True, **lifecycle(exe, life_profile, cwd, empty_cache)}
        checks["install_directory"] = {"ok": True, "unchanged": directory_digest(app) == before_install}
        assert checks["install_directory"]["unchanged"], "Runtime wrote inside its installation folder"
        relocated = work / "移动后的中文目录" / "PTCGLens"
        relocated.parent.mkdir()
        # 只在本次验证目录内移动本次解压产物，不接收任意外部移动目标。
        assert app.resolve().is_relative_to(work.resolve()) and relocated.resolve().is_relative_to(work.resolve())
        app.rename(relocated)
        moved_exe = relocated / "PTCGLens.exe"
        checks["relocate_restart"] = {"ok": True, **lifecycle(moved_exe, life_profile, cwd, empty_cache)}
        assert saved == {filename: digest(life_profile / filename) for filename in saved}, "Settings or translation cache changed"
        assert directory_digest(relocated) == before_install, "Moved installation was modified"
        checks["settings_cache_preserved"] = {"ok": True, "settings": settings, "translation_sentinel_preserved": True}
        final_log = (life_profile / "logs/overlay.log").read_text(encoding="utf-8")
        assert not any(marker in final_log for marker in ("Traceback", " ERROR ", "启动失败")), final_log
        report["ok"] = True
    except Exception as error:
        report["error"] = f"{type(error).__name__}: {error}"
    finally:
        report["finished_at"] = datetime.datetime.now().astimezone().isoformat()
        (work / "verification.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def main():
    if sys.platform != "win32":
        raise SystemExit("Windows 独立包验证需要 Windows。")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    parser.add_argument("--source-data", type=Path, help="只读复制其索引清单，额外验证本机真实游戏纹理解码")
    args = parser.parse_args()
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    work = ROOT / "output" / f"windows-portable-verification-{stamp}"
    work.mkdir(parents=True)
    report = verify(args.archive.resolve(), work.resolve(), args.source_data)
    print(json.dumps({"ok": report["ok"], "report": str(work / "verification.json"),
                      "checks": list(report["checks"]), "error": report.get("error")}, ensure_ascii=False, indent=2))
    raise SystemExit(0 if report["ok"] else 1)


if __name__ == "__main__":
    main()
