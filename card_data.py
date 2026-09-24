"""合并本地 PTCG Live 卡牌资料与社区中文对照。"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import re
import struct
import subprocess
from pathlib import Path


RESOURCE_ID = re.compile(r"^(.+)_en_(\d{3})$")
NUMBER_TYPES = {
    "System.Int32": "<i",
    "System.UInt32": "<I",
    "System.Byte": "<B",
    "System.Boolean": "<?",
    "System.Int64": "<q",
    "System.Double": "<d",
}


def read_7bit_length(blob: bytes, offset: int) -> tuple[int, int]:
    value = 0
    for shift in range(0, 35, 7):
        byte = blob[offset]
        offset += 1
        value |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return value, offset
    raise ValueError("Invalid 7-bit string length")


def read_string(blob: bytes, offset: int) -> tuple[str, int]:
    length, offset = read_7bit_length(blob, offset)
    end = offset + length
    if end > len(blob):
        raise ValueError("String exceeds table length")
    return blob[offset:end].decode("utf-8"), end


def parse_table(blob: bytes) -> list[dict]:
    if not blob or blob[0] != 0:
        raise ValueError("Unsupported card table header")
    _, offset = read_string(blob, 1)
    columns_count = struct.unpack_from("<I", blob, offset)[0]
    offset += 4
    if columns_count > 256:
        raise ValueError("Unexpected card table column count")
    columns = []
    for _ in range(columns_count):
        name, offset = read_string(blob, offset)
        kind, offset = read_string(blob, offset)
        if kind != "System.String" and kind not in NUMBER_TYPES:
            raise ValueError(f"Unsupported card field type: {kind}")
        columns.append((name, kind))

    rows_count = struct.unpack_from("<I", blob, offset)[0]
    offset += 4
    if rows_count > 100_000:
        raise ValueError("Unexpected card table row count")
    rows = []
    for _ in range(rows_count):
        row = {}
        for name, kind in columns:
            presence = blob[offset]
            offset += 1
            if presence == 1:
                value = None
            elif presence == 0 and kind == "System.String":
                value, offset = read_string(blob, offset)
            elif presence == 0:
                fmt = NUMBER_TYPES[kind]
                value = struct.unpack_from(fmt, blob, offset)[0]
                offset += struct.calcsize(fmt)
            else:
                raise ValueError(f"Invalid card field marker: {presence}")
            row[name] = value
        rows.append(row)
    if offset != len(blob):
        raise ValueError(f"Card table has {len(blob) - offset} trailing bytes")
    return rows


def read_game_database(path: Path) -> list[dict]:
    wrapper = json.loads(path.read_text(encoding="utf-8"))
    binary = wrapper["keys"]["table"]["contentBinary"]
    return parse_table(base64.b64decode(binary))


def game_id(resource_id: str) -> tuple[str, str]:
    match = RESOURCE_ID.fullmatch(resource_id)
    if not match:
        raise ValueError(f"Invalid indexed resource ID: {resource_id}")
    set_code, number = match.groups()
    return set_code, f"{set_code}_{int(number)}"


def translation(text: str, table: dict[str, str]) -> str | None:
    if not text:
        return None
    return table.get(hashlib.md5(text.encode("utf-8")).hexdigest())


def make_card(resource_id: str, row: dict, translations: dict[str, dict]) -> dict:
    name_en = row.get("EN Card Name") or ""
    attacks = []
    for number in range(1, 5):
        suffix = "" if number == 1 else f" {number}"
        raw_name = row.get(f"EN Attack Name{suffix}") or ""
        if not raw_name:
            continue
        is_ability = raw_name.startswith("[Ability] ")
        attack_name = raw_name.removeprefix("[Ability] ")
        effect_en = row.get(f"EN Attack Text{suffix}") or ""
        attacks.append({
            "kind": "ability" if is_ability else "attack",
            "name_en": attack_name,
            "name_zh": translation(raw_name, translations["attks-name"])
                or translation(attack_name, translations["attks-name"]),
            "damage": row.get("Damage" + suffix) or None,
            "text_en": effect_en or None,
            "text_zh": translation(effect_en, translations["attks-text"]),
        })
    card_text_en = (row.get("EN Attack Text") or "") if not attacks else ""
    return {
        "card_id": resource_id,
        "game_card_id": row.get("cardID"),
        "name_en": name_en,
        "name_zh": translation(name_en, translations["names"]),
        "hp": row.get("HP"),
        "set_code": row.get("setCode"),
        "number": row.get("EN Card #"),
        "attacks": attacks,
        "card_text_en": card_text_en or None,
        "card_text_zh": translation(card_text_en, translations["attks-text"]),
    }


def build_card_data(index_dir: Path, game_cache: Path, translation_root: Path, output: Path) -> dict:
    manifest = json.loads((index_dir / "manifest.json").read_text(encoding="utf-8"))
    indexed_ids = [record["card_id"] for record in manifest["cards"]]
    requested = {resource_id: game_id(resource_id) for resource_id in indexed_ids}
    set_codes = {set_code for set_code, _ in requested.values()}
    rows_by_id = {}
    database_files = []
    for set_code in sorted(set_codes):
        files = sorted(game_cache.glob(f"card-database-{set_code}_*_en_*.json"))
        database_files.extend(files)
        for path in files:
            for row in read_game_database(path):
                card_id = row.get("cardID")
                if card_id:
                    rows_by_id[card_id] = row

    translations = {
        name: json.loads((translation_root / f"{name}.json").read_text(encoding="utf-8"))
        for name in ("names", "attks-name", "attks-text")
    }
    cards = {}
    missing = []
    for resource_id, (_, internal_id) in requested.items():
        row = rows_by_id.get(internal_id)
        if row is None:
            missing.append(resource_id)
            continue
        cards[resource_id] = make_card(resource_id, row, translations)

    attacks = [attack for card in cards.values() for attack in card["attacks"]]
    effects = [attack for attack in attacks if attack["text_en"]]
    card_effects = [card for card in cards.values() if card["card_text_en"]]
    stats = {
        "indexed_cards": len(indexed_ids),
        "english_cards_found": len(cards),
        "missing_english_card_ids": missing,
        "chinese_card_names": sum(card["name_zh"] is not None for card in cards.values()),
        "attack_names": len(attacks),
        "chinese_attack_names": sum(attack["name_zh"] is not None for attack in attacks),
        "effect_texts": len(effects),
        "chinese_effect_texts": sum(attack["text_zh"] is not None for attack in effects),
        "trainer_and_energy_effect_texts": len(card_effects),
        "chinese_trainer_and_energy_effect_texts": sum(card["card_text_zh"] is not None for card in card_effects),
        "game_database_files": len(database_files),
    }
    source_repo = translation_root.parent
    revision = subprocess.run(
        ["git", "-C", str(source_repo), "rev-parse", "HEAD"],
        capture_output=True, text=True, check=False,
    )
    document = {
        "source": {
            "translation_repository": "https://github.com/Hill-98/ptcg-live-zh-mod",
            "translation_revision": revision.stdout.strip() if revision.returncode == 0 else None,
            "translation_license": "GPL-3.0",
            "game_cache": str(game_cache),
        },
        "stats": stats,
        "cards": cards,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8")
    return document


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--index-dir", type=Path, default=Path("output/index"))
    parser.add_argument("--game-cache", type=Path, default=Path.home() / "AppData/LocalLow/pokemon/Pokemon TCG Live/config-cache")
    parser.add_argument("--translation-root", type=Path, default=Path(".tmp/ptcg-live-zh-mod/databases_zh-CN"))
    parser.add_argument("--output", type=Path, default=Path("output/card-data.json"))
    args = parser.parse_args()
    document = build_card_data(args.index_dir, args.game_cache, args.translation_root, args.output)
    print(json.dumps(document["stats"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
