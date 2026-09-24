"""在本机浏览器展示最新的对局识别结果和卡牌资料。"""

from __future__ import annotations

import argparse
import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from card_translation import TranslationCache


ROOT = Path(__file__).resolve().parent
CARD_ID = re.compile(r"^[a-z0-9-]+_en_\d{3}$")
STATIC = {"/": "index.html", "/app.css": "app.css", "/app.js": "app.js"}
MIME = {".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8",
        ".js": "text/javascript; charset=utf-8", ".json": "application/json; charset=utf-8",
        ".png": "image/png"}


def state_payload(output_dir: Path, card_data_path: Path) -> dict:
    latest = json.loads((output_dir / "latest.json").read_text(encoding="utf-8"))
    cards = json.loads(card_data_path.read_text(encoding="utf-8"))["cards"]
    ids = {slot.get("card_id") for slot in latest["slots"] if slot.get("card_id")}
    return {**latest, "cards": {card_id: cards[card_id] for card_id in ids if card_id in cards}}


def large_card_bytes(card_id: str, cache_root: Path, large_dir: Path, visual_dir: Path) -> bytes:
    """从本机游戏缓存按需提取完整卡图，并复用提取结果。"""
    if not CARD_ID.fullmatch(card_id):
        raise FileNotFoundError(card_id)
    directory = next((path for path in (cache_root / card_id, cache_root / f"{card_id}_t")
                      if path.is_dir()), None)
    if directory is None:
        return (visual_dir / f"{card_id}.png").read_bytes()
    bundles = sorted(directory.rglob("__data"), key=lambda path: path.stat().st_mtime_ns, reverse=True)
    if not bundles:
        return (visual_dir / f"{card_id}.png").read_bytes()
    bundle = bundles[0]
    destination = large_dir / f"{card_id}.png"
    if destination.is_file() and destination.stat().st_mtime_ns >= bundle.stat().st_mtime_ns:
        return destination.read_bytes()
    import cv2
    from verify_card import extract_card

    card = extract_card(bundle, directory.name)
    height, width = card.shape[:2]
    cropped = card[round(height * .03):round(height * .97),
                   round(width * .16):round(width * .83)]
    success, encoded = cv2.imencode(".png", cropped)
    if not success:
        raise OSError(f"无法编码卡图：{card_id}")
    large_dir.mkdir(parents=True, exist_ok=True)
    temporary = large_dir / f".{card_id}.{threading.get_ident()}.tmp.png"
    body = encoded.tobytes()
    temporary.write_bytes(body)
    temporary.replace(destination)
    return body


def card_art_bytes(card_id: str, cache_root: Path, large_dir: Path,
                   visual_dir: Path, card_data_path: Path) -> bytes:
    """为中文阅读版提取卡牌插画区域，保留原始画质。"""
    import cv2
    import numpy as np

    body = large_card_bytes(card_id, cache_root, large_dir, visual_dir)
    image = cv2.imdecode(np.frombuffer(body, dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise OSError(f"无法读取卡图：{card_id}")
    cards = json.loads(card_data_path.read_text(encoding="utf-8"))["cards"]
    card = cards.get(card_id, {})
    height, width = image.shape[:2]
    first_row, last_row = (.07, .48) if card.get("hp") else (.115, .52)
    art = image[round(height * first_row):round(height * last_row),
                round(width * .05):round(width * .95)]
    success, encoded = cv2.imencode(".png", art)
    if not success:
        raise OSError(f"无法编码插画：{card_id}")
    return encoded.tobytes()


def make_handler(output_dir: Path, visual_dir: Path, card_data_path: Path,
                 cache_root: Path | None = None, large_dir: Path | None = None,
                 translation_cache_path: Path | None = None):
    cache_root = cache_root or Path.home() / "AppData/LocalLow/Unity/pokemon_Pokemon TCG Live"
    large_dir = large_dir or Path("output/card-large")
    translator = TranslationCache(translation_cache_path or Path("output/translation-cache.json"))
    class Handler(BaseHTTPRequestHandler):
        def send_bytes(self, body: bytes, mime: str, status: int = 200) -> None:
            self.send_response(status)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def send_json(self, payload: dict, status: int = 200) -> None:
            self.send_bytes(json.dumps(payload, ensure_ascii=False).encode("utf-8"), MIME[".json"], status)

        def do_POST(self) -> None:
            if urlsplit(self.path).path != "/api/translate":
                self.send_error(404)
                return
            if self.headers.get("Content-Type", "").split(";")[0].strip() != "application/json":
                self.send_json({"error": "请求格式不正确"}, 415)
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 1024:
                    raise ValueError("请求长度无效")
                request = json.loads(self.rfile.read(length))
                card_id = request.get("card_id")
                field = request.get("field")
                index = request.get("index")
                if not isinstance(card_id, str) or not CARD_ID.fullmatch(card_id):
                    raise ValueError("卡牌编号无效")
                cards = json.loads(card_data_path.read_text(encoding="utf-8"))["cards"]
                card = cards.get(card_id)
                if card is None:
                    self.send_json({"error": "本地资料中没有这张卡"}, 404)
                    return
                if field == "card_text":
                    source, local = card.get("card_text_en"), card.get("card_text_zh")
                elif field == "attack_text" and type(index) is int and 0 <= index < len(card.get("attacks", [])):
                    attack = card["attacks"][index]
                    source, local = attack.get("text_en"), attack.get("text_zh")
                else:
                    raise ValueError("翻译字段无效")
                if local:
                    self.send_json({"translation": local, "source": "local", "cached": True})
                    return
                if not source:
                    raise ValueError("没有可翻译的英文文本")
            except (ValueError, AttributeError, json.JSONDecodeError):
                self.send_json({"error": "请求的卡牌或字段无效"}, 400)
                return
            except (FileNotFoundError, KeyError):
                self.send_json({"error": "卡牌资料暂不可用"}, 503)
                return
            try:
                translated, cached = translator.translate(source)
                self.send_json({"translation": translated, "source": "MyMemory", "cached": cached})
            except (OSError, RuntimeError, ValueError) as exc:
                self.send_json({"error": f"翻译服务暂不可用：{exc}"}, 502)

        def do_GET(self) -> None:
            path = urlsplit(self.path).path
            try:
                if path in STATIC:
                    source = ROOT / "viewer" / STATIC[path]
                    self.send_bytes(source.read_bytes(), MIME[source.suffix])
                elif path == "/api/state":
                    body = json.dumps(state_payload(output_dir, card_data_path), ensure_ascii=False).encode("utf-8")
                    self.send_bytes(body, MIME[".json"])
                elif path == "/api/screenshot":
                    self.send_bytes((output_dir / "screenshot.png").read_bytes(), MIME[".png"])
                elif path.startswith("/api/thumb/"):
                    card_id = path.removeprefix("/api/thumb/")
                    if not CARD_ID.fullmatch(card_id):
                        self.send_error(404)
                        return
                    self.send_bytes((visual_dir / f"{card_id}.png").read_bytes(), MIME[".png"])
                elif path.startswith("/api/card-image/"):
                    card_id = path.removeprefix("/api/card-image/")
                    self.send_bytes(large_card_bytes(card_id, cache_root, large_dir, visual_dir), MIME[".png"])
                elif path.startswith("/api/card-art/"):
                    card_id = path.removeprefix("/api/card-art/")
                    self.send_bytes(card_art_bytes(card_id, cache_root, large_dir, visual_dir, card_data_path), MIME[".png"])
                else:
                    self.send_error(404)
            except FileNotFoundError:
                self.send_error(404, "等待第一帧识别结果")
            except (json.JSONDecodeError, KeyError) as exc:
                self.send_error(503, f"识别数据暂不可用: {exc}")

    return Handler


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--output-dir", type=Path, default=Path("output/battle-live"))
    parser.add_argument("--visual-dir", type=Path, default=Path("output/card-thumbnails"))
    parser.add_argument("--large-dir", type=Path, default=Path("output/card-large"))
    parser.add_argument("--cache-root", type=Path,
                        default=Path.home() / "AppData/LocalLow/Unity/pokemon_Pokemon TCG Live")
    parser.add_argument("--card-data", type=Path, default=Path("output/card-data.json"))
    parser.add_argument("--translation-cache", type=Path, default=Path("output/translation-cache.json"))
    args = parser.parse_args()
    server = ThreadingHTTPServer(
        (args.host, args.port),
        make_handler(args.output_dir, args.visual_dir, args.card_data, args.cache_root,
                     args.large_dir, args.translation_cache),
    )
    print(f"PTCGLens 界面：http://{args.host}:{args.port}", flush=True)
    try:
        server.serve_forever(poll_interval=.5)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
