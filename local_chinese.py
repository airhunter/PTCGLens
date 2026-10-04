"""优先使用社区对照；缺项只接受绑定完整英文原文的本地参考译文。"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path


LOCAL_CHINESE_PATH = Path(__file__).parent / "translations/local-zh.json"


def load_local_chinese(path: Path = LOCAL_CHINESE_PATH) -> dict:
    document = json.loads(path.read_text(encoding="utf-8"))
    if document.get("version") != 1:
        raise ValueError("本地中文对照版本不受支持")
    tables = document["tables"]
    for kind in ("names", "attks-name", "attks-text"):
        for key, text in tables[kind].items():
            if len(key) != 64 or any(c not in "0123456789abcdef" for c in key) or not isinstance(text, str) or not text.strip():
                raise ValueError(f"本地中文对照条目无效：{kind}/{key}")
    return tables


def resolve_chinese(text: str, kind: str, community: dict, local: dict) -> tuple[str | None, str | None]:
    if not text:
        return None, None
    raw = text.encode("utf-8")
    translated = community[kind].get(hashlib.md5(raw).hexdigest())
    if translated:
        return translated, "community"
    translated = local.get(kind, {}).get(hashlib.sha256(raw).hexdigest())
    return (translated, "local") if translated else (None, None)


def coverage_report(cards: dict) -> dict:
    """以当前索引为边界输出字段覆盖和缺项，不推算全卡库覆盖率。"""
    fields = {kind: [] for kind in ("card_names", "attack_names", "effects", "trainer_energy_effects")}
    for card_id, card in cards.items():
        fields["card_names"].append((card_id, "name", card["name_en"], card.get("name_zh"), card.get("name_zh_source")))
        if card.get("card_text_en"):
            fields["trainer_energy_effects"].append((card_id, "card_text", card["card_text_en"], card.get("card_text_zh"), card.get("card_text_zh_source")))
        for number, attack in enumerate(card.get("attacks", []), 1):
            fields["attack_names"].append((card_id, f"attack-{number}", attack["name_en"], attack.get("name_zh"), attack.get("name_zh_source")))
            if attack.get("text_en"):
                fields["effects"].append((card_id, f"attack-{number}", attack["text_en"], attack.get("text_zh"), attack.get("text_zh_source")))
    result = {"scope": "currently_indexed_cards", "cards": len(cards), "fields": {}}
    for kind, entries in fields.items():
        sources = Counter(source or "community" for _, _, _, zh, source in entries if zh)
        missing = [{"card_id": cid, "field": field, "english": en} for cid, field, en, zh, _ in entries if not zh]
        result["fields"][kind] = {"total": len(entries), "chinese": len(entries)-len(missing),
                                  "sources": dict(sources), "missing": missing}
    return result
