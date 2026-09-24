"""用一个命令启动并管理实时识别与本地网页服务。"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parent


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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765, help="本地网页端口，默认 8765")
    parser.add_argument("--cache-root", type=Path, help="游戏卡图缓存目录")
    parser.add_argument("--game-cache", type=Path, help="游戏卡牌数据库目录")
    parser.add_argument("--translation-root", type=Path, help="本地简中对照表目录")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("端口必须在 1 到 65535 之间")
    if not (ROOT / "output/index/manifest.json").is_file() or not (ROOT / "output/card-data.json").is_file():
        parser.error("尚未建立卡牌索引；请先运行 uv run python cache_watch.py --once")

    viewer = [sys.executable, str(ROOT / "viewer.py"), "--port", str(args.port)]
    recognizer = [sys.executable, str(ROOT / "battle_live.py")]
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
        print(f"已启动 PTCGLens：http://127.0.0.1:{args.port}（按 Ctrl+C 停止）", flush=True)
        while True:
            for name, process in processes:
                code = process.poll()
                if code is not None:
                    print(f"{name}已退出（代码 {code}），正在关闭其他服务。", file=sys.stderr, flush=True)
                    return code or 1
            time.sleep(.25)
    except KeyboardInterrupt:
        print("正在停止 PTCGLens……", flush=True)
        return 0
    except OSError as exc:
        print(f"无法启动 PTCGLens：{exc}", file=sys.stderr, flush=True)
        return 1
    finally:
        stop_processes(processes)


if __name__ == "__main__":
    raise SystemExit(main())
