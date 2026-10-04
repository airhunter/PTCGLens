"""连续读取 PTCG Live 窗口，只重识别画面发生变化的卡位。"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import cv2
import numpy as np
from PIL import ImageGrab

from battle_multi import adaptive_layout, annotate, recognise_slot, scaled_box, suppress_occluded_slots
from cache_search import load_index, read_image
from cache_watch import DEFAULT_CARD_CACHE, DEFAULT_DATABASE_CACHE, refresh_once
from global_cards import LargeCardFinder
from live_capture import client_bbox, game_window, set_dpi_awareness, user32
from window_capture import WindowCaptureSession, crop_client_frame
from atomic_json import read_json_retry


def load_resources(index_dir: Path, visual_dir: Path, card_data_path: Path) -> tuple[list, dict, dict, LargeCardFinder]:
    records = load_index(index_dir.resolve())
    index = {record["card_id"]: record for record in records}
    visuals = [(path.stem, read_image(path)) for path in sorted(visual_dir.glob("*.png")) if path.stem in index]
    if not visuals:
        raise ValueError("视觉索引为空，请先运行 python cache_watch.py --once")
    cards = read_json_retry(card_data_path)["cards"]
    return visuals, cards, index, LargeCardFinder(records)


def signature(screenshot: np.ndarray, slot: dict, reference_size: list[int]) -> np.ndarray:
    x0, y0, x1, y1 = scaled_box(slot["box"], reference_size, screenshot.shape[:2])
    crop = screenshot[y0:y1, x0:x1]
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    return cv2.GaussianBlur(cv2.resize(gray, (32, 45), interpolation=cv2.INTER_AREA), (5, 5), 0)


def is_battle_screen(screenshot: np.ndarray) -> bool:
    """用双方卡位边框识别牌桌，兼容不同颜色的桌面主题。"""
    height, width = screenshot.shape[:2]
    hsv = cv2.cvtColor(screenshot, cv2.COLOR_BGR2HSV)
    upper = hsv[round(height * .045):round(height * .065), round(width * .34):round(width * .66)]
    lower = hsv[round(height * .685):round(height * .705), round(width * .34):round(width * .66)]
    center = hsv[round(height * .3):round(height * .7), round(width * .3):round(width * .7)]
    upper_red = ((upper[:, :, 0] < 15) | (upper[:, :, 0] > 170)) & (upper[:, :, 1] > 75) & (upper[:, :, 2] > 50)
    lower_blue = (lower[:, :, 0] > 85) & (lower[:, :, 0] < 115) & (lower[:, :, 1] > 65) & (lower[:, :, 2] > 50)
    modal_white = (center[:, :, 1] < 40) & (center[:, :, 2] > 160)
    return float(upper_red.mean()) >= .05 and float(lower_blue.mean()) >= .08 and float(modal_white.mean()) < .35


def scan_frame(
    screenshot: np.ndarray, layout: dict, visuals: list, cards: dict, index: dict,
    previous: dict, now: float, change_threshold: float, force_after: float,
) -> tuple[list[dict], dict, int]:
    current = {}
    results = []
    reused = 0
    for slot in layout["slots"]:
        sample = signature(screenshot, slot, layout["reference_size"])
        old = previous.get(slot["key"])
        can_reuse = False
        if old and old["result"]["box"] == scaled_box(slot["box"], layout["reference_size"], screenshot.shape[:2]) \
                and old["result"]["status"] in ("matched", "empty") and now - old["scanned_at"] < force_after:
            difference = float(np.mean(np.abs(sample.astype(np.int16) - old["signature"].astype(np.int16))))
            can_reuse = difference < change_threshold
        if can_reuse:
            result = {**old["result"], "reused": True}
            current[slot["key"]] = old
            reused += 1
        else:
            result = recognise_slot(screenshot, slot, layout["reference_size"], visuals, cards, index)
            result["reused"] = False
            current[slot["key"]] = {"signature": sample, "scanned_at": now, "result": result}
        results.append(result)
    return results, current, reused


def replace_when_available(source: Path, destination: Path, timeout: float = 2) -> None:
    """等待 Windows 上正在读取目标文件的浏览器请求释放文件句柄。"""
    deadline = time.monotonic() + timeout
    while True:
        try:
            source.replace(destination)
            return
        except PermissionError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(.025)


def save_frame(output_dir: Path, screenshot: np.ndarray, results: list[dict], metadata: dict) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    screenshot_temp = output_dir / "screenshot.tmp.png"
    screenshot_path = output_dir / "screenshot.png"
    cv2.imwrite(str(screenshot_temp), screenshot)
    replace_when_available(screenshot_temp, screenshot_path)
    annotated_temp = output_dir / "annotated.tmp.png"
    annotate(screenshot, results, annotated_temp)
    replace_when_available(annotated_temp, output_dir / "annotated.png")
    document = {
        **metadata,
        "slots": results,
        "summary": {
            state: sum(slot["status"] == state for slot in results)
            for state in ("matched", "tentative", "unknown", "empty", "occluded")
        },
    }
    temporary = output_dir / "latest.json.tmp"
    temporary.write_text(json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8")
    replace_when_available(temporary, output_dir / "latest.json")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--frames", type=int, default=0, help="采集帧数；0 表示持续运行")
    parser.add_argument("--interval", type=float, default=1, help="采样间隔（秒）")
    parser.add_argument("--change-threshold", type=float, default=8, help="卡位重识别的像素变化门槛")
    parser.add_argument("--force-after", type=float, default=10, help="卡位最长复用时间（秒）")
    parser.add_argument("--cache-interval", type=float, default=15, help="缓存增量检查间隔（秒）")
    parser.add_argument("--max-idle", type=float, default=30, help="有限帧模式下等待游戏画面的最长秒数")
    parser.add_argument("--capture-mode", choices=("window", "screen"), default="window",
                        help="window 可在游戏被浏览器遮挡时继续采集；screen 为旧的前台截屏模式")
    parser.add_argument("--layout", type=Path, default=Path("battle_layout.sample.json"))
    parser.add_argument("--index-dir", type=Path, default=Path("output/index"))
    parser.add_argument("--visual-dir", type=Path, default=Path("output/card-thumbnails"))
    parser.add_argument("--card-data", type=Path, default=Path("output/card-data.json"))
    parser.add_argument("--cache-root", type=Path, default=DEFAULT_CARD_CACHE)
    parser.add_argument("--game-cache", type=Path, default=DEFAULT_DATABASE_CACHE)
    parser.add_argument("--translation-root", type=Path, default=Path(".tmp/ptcg-live-zh-mod/databases_zh-CN"))
    parser.add_argument("--output-dir", type=Path, default=Path("output/battle-live"))
    args = parser.parse_args()
    if args.frames < 0 or args.interval < 0 or args.force_after <= 0 or args.cache_interval < 1:
        parser.error("采集帧数、间隔或刷新周期无效")
    layout = json.loads(args.layout.read_text(encoding="utf-8"))
    set_dpi_awareness()
    previous = {}
    visuals, cards, index, preview_finder = load_resources(args.index_dir, args.visual_dir, args.card_data)
    last_cache_check = 0.0
    number = 0
    idle_since = None
    capture_session: WindowCaptureSession | None = None
    capture_sequence = 0
    try:
        while args.frames == 0 or number < args.frames:
            started = time.monotonic()
            if started - last_cache_check >= args.cache_interval:
                refresh = refresh_once(
                    args.cache_root, args.index_dir, args.visual_dir,
                    args.game_cache, args.translation_root, args.card_data,
                )
                last_cache_check = time.monotonic()
                if refresh["added"] or refresh["updated"] or refresh["visual_rebuilt"] or refresh["card_data_refreshed"]:
                    visuals, cards, index, preview_finder = load_resources(args.index_dir, args.visual_dir, args.card_data)
                    previous.clear()
                    print(f"缓存已更新：{refresh['cards']} 张卡", flush=True)
            try:
                hwnd, title = game_window(None)
            except RuntimeError:
                if capture_session is not None:
                    capture_session.stop()
                    capture_session = None
                if idle_since is None:
                    idle_since = time.monotonic()
                    print("游戏窗口未找到，等待启动", flush=True)
                if args.frames and time.monotonic() - idle_since >= args.max_idle:
                    print("等待游戏启动超时，结束采样", flush=True)
                    break
                time.sleep(max(args.interval, .5))
                continue
            if args.capture_mode == "screen":
                if hwnd != user32.GetForegroundWindow():
                    if idle_since is None:
                        idle_since = time.monotonic()
                        print("游戏未在前台，暂停截屏", flush=True)
                    if args.frames and time.monotonic() - idle_since >= args.max_idle:
                        print("等待游戏前台超时，结束采样", flush=True)
                        break
                    time.sleep(max(args.interval, .5))
                    continue
                captured = ImageGrab.grab(bbox=client_bbox(hwnd), all_screens=True)
                screenshot = cv2.cvtColor(np.asarray(captured), cv2.COLOR_RGB2BGR)
            else:
                if capture_session is not None and (capture_session.hwnd != hwnd or capture_session.closed):
                    capture_session.stop()
                    capture_session = None
                if capture_session is None:
                    try:
                        capture_session = WindowCaptureSession(hwnd, args.interval)
                    except Exception as exc:
                        raise RuntimeError(
                            "无法按窗口采集游戏画面；可用 --capture-mode screen 暂时恢复前台截屏"
                        ) from exc
                    capture_sequence = 0
                    print("已连接游戏窗口采集；切到浏览器仍会继续接收游戏画面", flush=True)
                latest = capture_session.newest(capture_sequence)
                if latest is None:
                    if idle_since is None:
                        idle_since = time.monotonic()
                        print("等待游戏窗口输出画面", flush=True)
                    if args.frames and time.monotonic() - idle_since >= args.max_idle:
                        print("等待游戏画面超时，结束采样", flush=True)
                        break
                    time.sleep(max(args.interval, .25))
                    continue
                capture_sequence, frame = latest
                screenshot = crop_client_frame(frame, hwnd)
            idle_since = None
            bbox = client_bbox(hwnd)
            scene = "battle" if is_battle_screen(screenshot) else "other"
            preview = preview_finder.find(screenshot, cards)
            if scene == "battle":
                frame_layout = adaptive_layout(screenshot, layout)
                now = time.monotonic()
                results, previous, reused = scan_frame(
                    screenshot, frame_layout, visuals, cards, index, previous,
                    now, args.change_threshold, args.force_after,
                )
                if preview:
                    results = [preview if result["key"] == "preview" else result for result in results]
                    results = suppress_occluded_slots(results, preview)
            else:
                if preview:
                    preview["label"] = "卡牌特写"
                results, reused = ([preview] if preview else []), 0
                previous.clear()
            number += 1
            save_frame(args.output_dir, screenshot, results, {
                "captured_at": time.time(), "frame": number, "window_title": title,
                "window_client_bbox": list(bbox), "reference_cards": len(visuals),
                "scene": scene, "capture_mode": args.capture_mode,
                "reused_slots": reused, "recomputed_slots": len(results) - reused,
            })
            print(f"第 {number} 帧：" + (
                f"重识别 {len(results)-reused} 个卡位，复用 {reused} 个"
                if scene == "battle" else
                ("非对局画面，识别到卡牌特写" if preview else "非对局画面，等待卡牌特写")), flush=True)
            if args.frames == 0 or number < args.frames:
                time.sleep(max(0, args.interval - (time.monotonic() - started)))
    except KeyboardInterrupt:
        pass
    finally:
        if capture_session is not None:
            capture_session.stop()


if __name__ == "__main__":
    main()
