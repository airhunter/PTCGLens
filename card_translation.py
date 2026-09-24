"""按需翻译本地卡牌资料中尚无中文对照的英文效果文本。"""

from __future__ import annotations

import hashlib
import html
import json
import re
import threading
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen


API_URL = "https://api.mymemory.translated.net/get"
ENERGY = {
    "grass": "草", "fire": "火", "water": "水", "lightning": "雷", "psychic": "超",
    "fighting": "斗", "darkness": "恶", "metal": "钢", "colorless": "无色", "dragon": "龙",
}


def readable_text(source: str) -> str:
    """把游戏文字里的能量图标转换成可供翻译的文字。"""
    source = re.sub(
        r'<sprite name="([^"]+)"[^>]*>',
        lambda match: f"[{ENERGY.get(match.group(1), match.group(1))}]", source,
    )
    source = re.sub(r"<br\s*/?>", "\n", source, flags=re.IGNORECASE)
    source = re.sub(r"<[^>]+>", "", source)
    return html.unescape(source).strip()


def segments(source: str, byte_limit: int = 450) -> list[str]:
    """按接口的 500 字节上限切分，优先在句末或空格处分段。"""
    result = []
    for line in source.splitlines():
        remaining = line.strip()
        while remaining:
            if len(remaining.encode("utf-8")) <= byte_limit:
                result.append(remaining)
                break
            cut = 0
            for position, character in enumerate(remaining, 1):
                if len(remaining[:position].encode("utf-8")) > byte_limit:
                    break
                cut = position
            boundary = max(remaining.rfind(mark, 0, cut) for mark in (". ", "! ", "? ", "; ", " "))
            if boundary >= cut // 2:
                cut = boundary + (2 if remaining[boundary:boundary + 2] in (". ", "! ", "? ", "; ") else 1)
            result.append(remaining[:cut].strip())
            remaining = remaining[cut:].strip()
    return result


def translate_segment(source: str) -> str:
    query = urlencode({"q": source, "langpair": "en|zh-CN"})
    request = Request(f"{API_URL}?{query}", headers={"User-Agent": "PTCGLens/1.0"})
    with urlopen(request, timeout=12) as response:
        document = json.load(response)
    if document.get("responseStatus") != 200:
        raise RuntimeError(str(document.get("responseDetails") or "翻译服务暂不可用"))
    translated = html.unescape(str(document.get("responseData", {}).get("translatedText") or "")).strip()
    if not translated:
        raise RuntimeError("翻译服务没有返回内容")
    return translated


class TranslationCache:
    def __init__(self, path: Path):
        self.path = path
        self.lock = threading.Lock()
        try:
            self.entries = json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            self.entries = {}

    def translate(self, source: str) -> tuple[str, bool]:
        normalized = readable_text(source)
        if not normalized:
            raise ValueError("没有可翻译的英文文本")
        key = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
        with self.lock:
            if key in self.entries:
                return self.entries[key], True
            translated = "\n".join(translate_segment(part) for part in segments(normalized))
            self.entries[key] = translated
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_name(f".{self.path.name}.{threading.get_ident()}.tmp")
            temporary.write_text(json.dumps(self.entries, ensure_ascii=False, indent=2), encoding="utf-8")
            temporary.replace(self.path)
            return translated, False
