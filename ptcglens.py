"""默认启动游戏浮卡；--web 使用开发用网页模式，--quit 退出后台浮卡。"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path
from app_paths import DATA_DIR, RESOURCE_DIR
from console_log import log


ROOT = RESOURCE_DIR


def stop_processes(processes: list[tuple[str, subprocess.Popen]]) -> None:
    """退出时关闭由本入口启动的两个子进程。"""
    for _, process in processes:
        if process.poll() is None:
            try:
                process.terminate()
            except OSError:
                pass
    for _, process in processes:
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--overlay", action="store_true", help="启动浮卡（默认；兼容原启动命令）")
    mode.add_argument("--web", action="store_true", help="启动实时识别与本地网页服务（源码运行）")
    mode.add_argument("--quit", action="store_true", help="退出当前用户数据目录下正在运行的浮卡")
    mode.add_argument("--self-check", type=Path, metavar="REPORT.json", help="检查打包依赖并保存报告，不采集画面或请求权限")
    parser.add_argument("--check-network", action="store_true", help="自检时验证在线翻译；需搭配 --self-check")
    parser.add_argument("--check-permissions", action="store_true", help="只检查权限；默认包含浮卡输入权限")
    parser.add_argument("--port", type=int, help="网页模式端口，默认 8765；需搭配 --web")
    parser.add_argument("--cache-root", type=Path, help="游戏卡图缓存目录")
    parser.add_argument("--game-cache", type=Path, help="游戏卡牌数据库目录")
    parser.add_argument("--translation-root", type=Path, help="本地简中对照表目录")
    parser.add_argument("--capture-mode", choices=("window", "screen"), help="网页采集模式；需搭配 --web")
    parser.add_argument("--diagnose-input", action="store_true", help="浮卡输入诊断")
    args = parser.parse_args(argv)
    if args.self_check:
        if args.check_permissions or args.diagnose_input or any(getattr(args, name) is not None for name in
                ("port", "capture_mode", "cache_root", "game_cache", "translation_root")):
            parser.error("--self-check 只可搭配 --check-network")
        from package_check import run
        return run(args.self_check, network=args.check_network)
    if args.check_network:
        parser.error("--check-network 需搭配 --self-check")
    if args.quit:
        if args.check_permissions or any(getattr(args, name) is not None for name in
                ("port", "capture_mode", "cache_root", "game_cache", "translation_root")) or args.diagnose_input:
            parser.error("--quit 请单独使用")
        from PySide6.QtCore import QCoreApplication
        from app_instance import send_command
        app = QCoreApplication.instance() or QCoreApplication(sys.argv[:1])
        accepted = send_command(DATA_DIR, "quit")
        log("已发送退出请求。" if accepted else "未找到可响应的 PTCGLens 浮卡。")
        return 0 if accepted else 1
    if not args.web and (args.port is not None or args.capture_mode is not None):
        parser.error("--port 和 --capture-mode 需搭配 --web；默认启动浮卡")
    if args.check_permissions:
        from live_capture import check_permissions
        missing = check_permissions(input_required=not args.web)
        log("\n".join(missing) if missing else "权限检查通过")
        return 1 if missing else 0
    if not args.web:
        from overlay_app import main as overlay_main
        options = []
        for name in ("cache_root", "game_cache", "translation_root"):
            if getattr(args, name) is not None:
                options.extend(("--" + name.replace("_", "-"), str(getattr(args, name))))
        if args.diagnose_input:
            options.append("--diagnose-input")
        return overlay_main(options)
    if getattr(sys, "frozen", False):
        parser.error("独立应用仅提供浮卡模式；网页模式请从源码运行")
    if args.diagnose_input:
        parser.error("--diagnose-input 仅用于浮卡模式")
    args.port = args.port if args.port is not None else 8765
    args.capture_mode = args.capture_mode or "window"
    if not 1 <= args.port <= 65535:
        parser.error("端口必须在 1 到 65535 之间")
    if not (DATA_DIR / "index/manifest.json").is_file() or not (DATA_DIR / "card-data.json").is_file():
        parser.error("尚未建立卡牌索引；请先运行 uv run python cache_watch.py --once")

    viewer = [sys.executable, str(ROOT / "viewer.py"), "--port", str(args.port)]
    recognizer = [sys.executable, str(ROOT / "battle_live.py"), "--capture-mode", args.capture_mode]
    if args.cache_root is not None:
        viewer.extend(("--cache-root", str(args.cache_root)))
        recognizer.extend(("--cache-root", str(args.cache_root)))
    if args.game_cache is not None:
        recognizer.extend(("--game-cache", str(args.game_cache)))
    if args.translation_root is not None:
        recognizer.extend(("--translation-root", str(args.translation_root)))

    processes: list[tuple[str, subprocess.Popen]] = []
    try:
        for name, command in (("网页服务", viewer), ("实时识别", recognizer)):
            process = subprocess.Popen(command, cwd=ROOT)
            processes.append((name, process))
        log(f"已启动 PTCGLens：http://127.0.0.1:{args.port}（按 Ctrl+C 停止）")
        while True:
            for name, process in processes:
                code = process.poll()
                if code is not None:
                    log(f"{name}已退出（代码 {code}），正在关闭其他服务。")
                    return code or 1
            time.sleep(.25)
    except KeyboardInterrupt:
        log("正在停止 PTCGLens……")
        return 0
    except OSError as exc:
        log(f"无法启动 PTCGLens：{exc}")
        return 1
    finally:
        stop_processes(processes)


def launch():
    """无控制台启动时，启动异常也能被看到，而不是静默消失。"""
    try:
        return main()
    except Exception as exc:
        from console_log import configure_file_logging, log, log_exception
        try:
            configure_file_logging(DATA_DIR / "logs/overlay.log")
            log_exception("PTCGLens 启动失败")
        except OSError:
            pass
        log(f"PTCGLens 启动失败：{exc}")
        from PySide6.QtWidgets import QApplication, QMessageBox
        from qt_runtime import prepare_qt_plugins
        plugin_directory = prepare_qt_plugins()
        app = QApplication.instance() or QApplication(sys.argv[:1])
        QMessageBox.critical(None, "PTCGLens · 启动失败", f"{exc}\n\n日志目录：{DATA_DIR / 'logs'}")
        return 1


if __name__ == "__main__":
    raise SystemExit(launch())
