"""按热键截取可见的 PTCG Live 窗口，并检索本地卡图索引。"""

from __future__ import annotations

import argparse
import ctypes
import json
import sys
import unicodedata
from ctypes import wintypes
from pathlib import Path

from cache_search import search_index


if sys.platform != "win32":
    raise SystemExit("Live capture currently supports Windows only")


user32 = ctypes.WinDLL("user32", use_last_error=True)
WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
WM_HOTKEY = 0x0312
MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_NOREPEAT = 0x4000
CAPTURE_HOTKEY = 1
QUIT_HOTKEY = 2

user32.EnumWindows.argtypes = (WNDENUMPROC, wintypes.LPARAM)
user32.EnumWindows.restype = wintypes.BOOL
user32.IsWindowVisible.argtypes = (wintypes.HWND,)
user32.IsWindowVisible.restype = wintypes.BOOL
user32.IsIconic.argtypes = (wintypes.HWND,)
user32.IsIconic.restype = wintypes.BOOL
user32.GetWindowTextLengthW.argtypes = (wintypes.HWND,)
user32.GetWindowTextLengthW.restype = ctypes.c_int
user32.GetWindowTextW.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
user32.GetWindowTextW.restype = ctypes.c_int
user32.GetClientRect.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.RECT))
user32.GetClientRect.restype = wintypes.BOOL
user32.ClientToScreen.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.POINT))
user32.ClientToScreen.restype = wintypes.BOOL
user32.GetCursorPos.argtypes = (ctypes.POINTER(wintypes.POINT),)
user32.GetCursorPos.restype = wintypes.BOOL
user32.GetForegroundWindow.restype = wintypes.HWND
user32.RegisterHotKey.argtypes = (wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT)
user32.RegisterHotKey.restype = wintypes.BOOL
user32.UnregisterHotKey.argtypes = (wintypes.HWND, ctypes.c_int)
user32.UnregisterHotKey.restype = wintypes.BOOL
user32.GetMessageW.argtypes = (ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT)
user32.GetMessageW.restype = ctypes.c_int


def set_dpi_awareness() -> None:
    try:
        user32.SetProcessDpiAwarenessContext.argtypes = (ctypes.c_void_p,)
        user32.SetProcessDpiAwarenessContext.restype = wintypes.BOOL
        user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    except AttributeError:
        pass
    # 其他库可能已设置进程 DPI；覆盖当前线程设置，让窗口和鼠标坐标使用物理像素。
    try:
        user32.SetThreadDpiAwarenessContext.argtypes = (ctypes.c_void_p,)
        user32.SetThreadDpiAwarenessContext.restype = ctypes.c_void_p
        user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))
    except AttributeError:
        user32.SetProcessDPIAware()


def normalise(text: str) -> str:
    return "".join(
        char for char in unicodedata.normalize("NFKD", text.casefold())
        if not unicodedata.combining(char)
    )


def visible_windows() -> list[tuple[int, str]]:
    found = []

    @WNDENUMPROC
    def callback(hwnd: int, _unused: int) -> bool:
        if not user32.IsWindowVisible(hwnd) or user32.IsIconic(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length:
            title = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, title, length + 1)
            if title.value.strip():
                found.append((hwnd, title.value))
        return True

    if not user32.EnumWindows(callback, 0):
        raise ctypes.WinError(ctypes.get_last_error())
    return found


def game_window(title_filter: str | None) -> tuple[int, str]:
    windows = visible_windows()
    if title_filter:
        token = normalise(title_filter)
        matches = [(hwnd, title) for hwnd, title in windows if token in normalise(title)]
    else:
        matches = [
            (hwnd, title) for hwnd, title in windows
            if "pokemon" in normalise(title) and "live" in normalise(title)
        ]
    if not matches:
        raise RuntimeError("PTCG Live window not found. Start the game or use --title to specify its window title.")
    foreground = user32.GetForegroundWindow()
    return next((item for item in matches if item[0] == foreground), matches[0])


def client_bbox(hwnd: int) -> tuple[int, int, int, int]:
    rect = wintypes.RECT()
    origin = wintypes.POINT(0, 0)
    if not user32.GetClientRect(hwnd, ctypes.byref(rect)):
        raise ctypes.WinError(ctypes.get_last_error())
    if not user32.ClientToScreen(hwnd, ctypes.byref(origin)):
        raise ctypes.WinError(ctypes.get_last_error())
    if rect.right <= 0 or rect.bottom <= 0:
        raise RuntimeError("The game window has an empty client area")
    return origin.x, origin.y, origin.x + rect.right, origin.y + rect.bottom


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
    from PIL import ImageGrab

    if not (index_dir / "manifest.json").is_file():
        raise RuntimeError(f"Card index not found in {index_dir}. Run cache_search.py build first.")
    hwnd, title = game_window(title_filter)
    if hwnd != user32.GetForegroundWindow():
        raise RuntimeError("Bring the PTCG Live window to the foreground before capturing")
    bbox = client_bbox(hwnd)
    cursor = wintypes.POINT()
    if not user32.GetCursorPos(ctypes.byref(cursor)):
        raise ctypes.WinError(ctypes.get_last_error())
    if not (bbox[0] <= cursor.x < bbox[2] and bbox[1] <= cursor.y < bbox[3]):
        raise RuntimeError("Move the pointer over a card inside the game window before capturing")

    output_dir.mkdir(parents=True, exist_ok=True)
    screenshot_path = output_dir / "screenshot.png"
    result_path = output_dir / "result.json"
    screenshot = ImageGrab.grab(bbox=bbox, all_screens=True)
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


def register_hotkeys() -> None:
    if not user32.RegisterHotKey(None, CAPTURE_HOTKEY, MOD_CONTROL | MOD_ALT | MOD_NOREPEAT, ord("L")):
        raise ctypes.WinError(ctypes.get_last_error())
    if not user32.RegisterHotKey(None, QUIT_HOTKEY, MOD_CONTROL | MOD_ALT | MOD_SHIFT | MOD_NOREPEAT, ord("Q")):
        user32.UnregisterHotKey(None, CAPTURE_HOTKEY)
        raise ctypes.WinError(ctypes.get_last_error())


def watch(index_dir: Path, output_dir: Path, title_filter: str | None, card_data_path: Path) -> None:
    register_hotkeys()
    print("Ctrl+Alt+L: capture card; Ctrl+Alt+Shift+Q: quit", flush=True)
    try:
        message = wintypes.MSG()
        while True:
            status = user32.GetMessageW(ctypes.byref(message), None, 0, 0)
            if status == -1:
                raise ctypes.WinError(ctypes.get_last_error())
            if status == 0 or (message.message == WM_HOTKEY and message.wParam == QUIT_HOTKEY):
                break
            if message.message == WM_HOTKEY and message.wParam == CAPTURE_HOTKEY:
                try:
                    capture_once(index_dir, output_dir, title_filter, card_data_path)
                except Exception as exc:
                    print(f"Capture failed: {exc}", flush=True)
    finally:
        user32.UnregisterHotKey(None, CAPTURE_HOTKEY)
        user32.UnregisterHotKey(None, QUIT_HOTKEY)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("once", "watch", "windows", "check-hotkeys"))
    parser.add_argument("--title", help="Case-insensitive window title substring; default finds Pokémon + Live")
    parser.add_argument("--index-dir", type=Path, default=Path("output/index"))
    parser.add_argument("--output-dir", type=Path, default=Path("output/live"))
    parser.add_argument("--card-data", type=Path, default=Path("output/card-data.json"))
    args = parser.parse_args()
    set_dpi_awareness()
    try:
        if args.mode == "windows":
            for hwnd, title in visible_windows():
                print(f"{hwnd}: {title}")
        elif args.mode == "check-hotkeys":
            register_hotkeys()
            user32.UnregisterHotKey(None, CAPTURE_HOTKEY)
            user32.UnregisterHotKey(None, QUIT_HOTKEY)
            print("Hotkeys registered and released successfully")
        elif args.mode == "once":
            capture_once(args.index_dir, args.output_dir, args.title, args.card_data)
        else:
            watch(args.index_dir, args.output_dir, args.title, args.card_data)
    except (RuntimeError, OSError) as exc:
        parser.exit(1, f"{exc}\n")


if __name__ == "__main__":
    main()
