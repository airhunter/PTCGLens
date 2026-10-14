"""跨平台窗口与热键入口；原生 API 只存在于平台后端。"""
from __future__ import annotations
import argparse
import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace
import cv2
from PIL import Image
from cache_search import search_index
from platform_backend import get_backend


def visible_windows(): return get_backend().visible_windows()
def game_window(title_filter=None): return get_backend().game_window(title_filter)
def client_bbox(hwnd): return get_backend().client_bbox(hwnd)
def set_dpi_awareness(): return get_backend().set_dpi_awareness()
def is_foreground(hwnd): return get_backend().is_foreground(hwnd)
def pointer_position(): return get_backend().pointer_position()
def capture_screen(hwnd): return get_backend().capture_screen(hwnd)
def check_permissions(**options): return get_backend().check_permissions(**options)

def card_text(card: dict | None) -> str:
    if card is None:
        return "未找到这张卡的本地资料。\n"
    lines = [f"{card['name_zh'] or card['name_en']} ({card['name_en']})  HP {card['hp']}"]
    lines.append(f"卡牌 ID：{card['card_id']}")
    for attack in card["attacks"]:
        kind = "特性" if attack["kind"] == "ability" else "招式"
        title = attack["name_zh"] or attack["name_en"]
        damage = f"  {attack['damage']}" if attack["damage"] else ""
        lines.append(f"{kind}：{title}{damage}")
        if attack["text_zh"]:
            lines.append(attack["text_zh"])
        elif attack["text_en"]:
            lines.append(f"[中文效果待补] {attack['text_en']}")
    return "\n".join(lines) + "\n"


def attach_card_data(result: dict, card_data_path: Path) -> dict:
    if card_data_path.is_file():
        card_database = json.loads(card_data_path.read_text(encoding="utf-8"))
        result["card"] = card_database["cards"].get(result["card_id"])
    else:
        result["card"] = None
    return result


def capture_once(index_dir: Path, output_dir: Path, title_filter: str | None, card_data_path: Path) -> dict:
    if not (index_dir / "manifest.json").is_file():
        raise RuntimeError(f"Card index not found in {index_dir}. Run cache_search.py build first.")
    hwnd, title = game_window(title_filter)
    if not is_foreground(hwnd):
        raise RuntimeError("Bring the PTCG Live window to the foreground before capturing")
    bbox = client_bbox(hwnd)
    x, y = pointer_position()
    cursor = SimpleNamespace(x=x, y=y)
    if not (bbox[0] <= cursor.x < bbox[2] and bbox[1] <= cursor.y < bbox[3]):
        raise RuntimeError("Move the pointer over a card inside the game window before capturing")

    output_dir.mkdir(parents=True, exist_ok=True)
    screenshot_path = output_dir / "screenshot.png"
    result_path = output_dir / "result.json"
    screenshot = Image.fromarray(cv2.cvtColor(capture_screen(hwnd), cv2.COLOR_BGR2RGB))
    screenshot.save(screenshot_path)
    result = search_index(index_dir, screenshot_path, result_path, verbose=False)
    attach_card_data(result, card_data_path)
    result["capture"] = {
        "window_title": title,
        "window_client_bbox": list(bbox),
        "cursor_screen": [cursor.x, cursor.y],
        "cursor_client": [cursor.x - bbox[0], cursor.y - bbox[1]],
        "screenshot_size": list(screenshot.size),
        "cursor_image": [
            round((cursor.x - bbox[0]) * screenshot.width / (bbox[2] - bbox[0])),
            round((cursor.y - bbox[1]) * screenshot.height / (bbox[3] - bbox[1])),
        ],
        "screenshot": str(screenshot_path.resolve()),
    }
    result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    (output_dir / "card.txt").write_text(card_text(result["card"]), encoding="utf-8")
    card_name = result["card"]["name_zh"] if result["card"] else None
    print(f"Card: {result['card_id'] or 'not found'} | Chinese: {card_name or 'unavailable'} | Result: {result_path.resolve()}", flush=True)
    return result



def watch(index_dir, output_dir, title_filter, card_data_path):
    backend = get_backend()
    print("Ctrl+Alt/Option+L: capture card; Ctrl+Alt/Option+Shift+Q: quit", flush=True)
    if sys.platform == "win32":
        backend.watch_capture(lambda: capture_once(index_dir, output_dir, title_filter, card_data_path))
        return
    previous = False
    while True:
        if backend.shortcut_pressed("Ctrl+Alt+Shift+Q"):
            return
        pressed = backend.shortcut_pressed("Ctrl+Alt+L")
        if pressed and not previous:
            try:
                capture_once(index_dir, output_dir, title_filter, card_data_path)
            except (RuntimeError, OSError) as exc:
                print(str(exc), flush=True)
        previous = pressed
        time.sleep(.05)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("once", "watch", "windows", "check-hotkeys", "check-permissions"))
    parser.add_argument("--title")
    parser.add_argument("--index-dir", type=Path, default=Path("output/index"))
    parser.add_argument("--output-dir", type=Path, default=Path("output/live"))
    parser.add_argument("--card-data", type=Path, default=Path("output/card-data.json"))
    args = parser.parse_args()
    set_dpi_awareness()
    missing = check_permissions(input_required=args.mode in ("watch", "check-hotkeys"),
                                request=args.mode in ("once", "watch"))
    if missing:
        print("\n".join(missing), flush=True)
        return 1
    if args.mode == "windows":
        for window, title in visible_windows():
            print(f"{window}: {title}")
    elif args.mode in ("check-hotkeys", "check-permissions"):
        if args.mode == "check-hotkeys" and sys.platform == "win32":
            get_backend().check_hotkeys()
        print("权限检查通过", flush=True)
    elif args.mode == "once":
        capture_once(args.index_dir, args.output_dir, args.title, args.card_data)
    else:
        watch(args.index_dir, args.output_dir, args.title, args.card_data)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
