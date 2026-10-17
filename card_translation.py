"""按需翻译本地卡牌资料中尚无中文对照的英文效果文本。"""

from __future__ import annotations

import hashlib
import html
import json
import re
import threading
import time
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
from atomic_json import read_json_retry, write_json_atomic


API_URL = "https://api.mymemory.translated.net/get"
ENERGY = {
    "grass": "草", "fire": "火", "water": "水", "lightning": "雷", "psychic": "超",
    "fighting": "斗", "darkness": "恶", "metal": "钢", "colorless": "无色", "dragon": "龙",
}


class TranslationCancelled(RuntimeError):
    pass


class TranslationServiceError(RuntimeError):
    def __init__(self, message, retryable=False):
        super().__init__(message)
        self.retryable = retryable


def transient_error(error):
    if isinstance(error, HTTPError):
        return error.code in (408, 429) or 500 <= error.code <= 599
    if isinstance(error, TranslationServiceError):
        return error.retryable
    return isinstance(error, (URLError, TimeoutError, ConnectionError))


def wait_for_retry(seconds, cancelled):
    end = time.monotonic()+seconds
    while time.monotonic() < end:
        if cancelled():
            raise TranslationCancelled("查询已结束")
        time.sleep(min(.1, max(0, end-time.monotonic())))


def readable_text(source: str, language: str = "zh") -> str:
    """把游戏文字里的能量图标转换成可供翻译的文字。"""
    source = re.sub(
        r'<sprite name="([^"]+)"[^>]*>',
        lambda match: f"[{ENERGY.get(match.group(1), match.group(1)) if language == 'zh' else match.group(1)}]", source,
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
        status = str(document.get("responseStatus"))
        raise TranslationServiceError(str(document.get("responseDetails") or "翻译服务暂不可用"),
                                      status in ("408", "429") or status.startswith("5"))
    translated = html.unescape(str(document.get("responseData", {}).get("translatedText") or "")).strip()
    if not translated:
        raise RuntimeError("翻译服务没有返回内容")
    return translated


class TranslationCache:
    def __init__(self, path: Path):
        self.path = path
        self.lock = threading.Lock()
        self.network_lock = threading.Lock()
        try:
            self.entries = read_json_retry(path)
        except (FileNotFoundError, json.JSONDecodeError):
            self.entries = {}

    def cached(self, source: str) -> str | None:
        key = hashlib.sha256(readable_text(source).encode("utf-8")).hexdigest()
        with self.lock:
            return self.entries.get(key)

    def translate(self, source: str, *, cancelled=None) -> tuple[str, bool]:
        cancelled = cancelled or (lambda: False)
        normalized = readable_text(source)
        if not normalized:
            raise ValueError("没有可翻译的英文文本")
        key = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
        # 缓存读取不会被网络请求阻塞；串行请求仍避免同一文本重复下载。
        while not self.network_lock.acquire(timeout=.1):
            if cancelled():
                raise TranslationCancelled("查询已结束")
        try:
            if cancelled():
                raise TranslationCancelled("查询已结束")
            with self.lock:
                if key in self.entries:
                    return self.entries[key], True
            translated_parts = []
            for part in segments(normalized):
                for attempt in range(3):
                    if cancelled():
                        raise TranslationCancelled("查询已结束")
                    try:
                        translated_parts.append(translate_segment(part))
                        break
                    except Exception as error:
                        if attempt == 2 or not transient_error(error):
                            raise
                        wait_for_retry((.5, 1.5)[attempt], cancelled)
            if cancelled():
                raise TranslationCancelled("查询已结束")
            translated = "\n".join(translated_parts)
            with self.lock:
                self.entries[key] = translated
                write_json_atomic(self.path, self.entries)
            return translated, False
        finally:
            self.network_lock.release()
