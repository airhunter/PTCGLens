"""后台轮询游戏卡图缓存，并增量更新识别索引与中文资料。"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from battle_multi import build_visual_index
from cache_search import update_index
from card_data import build_card_data


DEFAULT_CARD_CACHE = Path.home() / "AppData/LocalLow/Unity/pokemon_Pokemon TCG Live"
DEFAULT_DATABASE_CACHE = Path.home() / "AppData/LocalLow/pokemon/Pokemon TCG Live/config-cache"


def metadata_is_stale(card_data_path: Path, game_cache: Path, translation_root: Path) -> bool:
    if not card_data_path.is_file():
        return True
    generated_at = card_data_path.stat().st_mtime_ns
    sources = list(game_cache.glob("card-database-*_en_*.json"))
    sources.extend(translation_root.glob("*.json"))
    return any(path.stat().st_mtime_ns > generated_at for path in sources)


def refresh_once(
    cache_root: Path, index_dir: Path, visual_dir: Path,
    game_cache: Path, translation_root: Path, card_data_path: Path,
) -> dict:
    index_result = update_index(cache_root, index_dir)
    visual_result = build_visual_index(index_dir, cache_root, visual_dir)
    refresh_metadata = (
        index_result["added"] > 0 or index_result["updated"] > 0
        or metadata_is_stale(card_data_path, game_cache, translation_root)
    )
    metadata_stats = None
    if refresh_metadata:
        metadata_stats = build_card_data(index_dir, game_cache, translation_root, card_data_path)["stats"]
    return {
        "cards": index_result["cards"],
        "added": index_result["added"],
        "updated": index_result["updated"],
        "pending": index_result["pending"],
        "index_failures": index_result["failures"],
        "visual_rebuilt": visual_result["rebuilt"],
        "visual_missing": visual_result["missing"],
        "card_data_refreshed": refresh_metadata,
        "card_data_stats": metadata_stats,
    }


def write_status(path: Path, status: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true", help="只扫描一次")
    parser.add_argument("--interval", type=float, default=5, help="轮询间隔（秒）")
    parser.add_argument("--cache-root", type=Path, default=DEFAULT_CARD_CACHE)
    parser.add_argument("--index-dir", type=Path, default=Path("output/index"))
    parser.add_argument("--visual-dir", type=Path, default=Path("output/card-thumbnails"))
    parser.add_argument("--game-cache", type=Path, default=DEFAULT_DATABASE_CACHE)
    parser.add_argument("--translation-root", type=Path, default=Path(".tmp/ptcg-live-zh-mod/databases_zh-CN"))
    parser.add_argument("--card-data", type=Path, default=Path("output/card-data.json"))
    parser.add_argument("--status", type=Path, default=Path("output/cache-watch-status.json"))
    args = parser.parse_args()
    if args.interval < 1:
        parser.error("--interval must be at least 1 second")
    first = True
    try:
        while True:
            status = refresh_once(
                args.cache_root, args.index_dir, args.visual_dir,
                args.game_cache, args.translation_root, args.card_data,
            )
            status["checked_at"] = time.time()
            write_status(args.status, status)
            changed = (
                status["added"] or status["updated"] or status["visual_rebuilt"]
                or status["card_data_refreshed"] or status["pending"]
                or status["index_failures"] or status["visual_missing"]
            )
            if first or changed:
                print(json.dumps(status, ensure_ascii=False), flush=True)
            if args.once:
                break
            first = False
            time.sleep(args.interval)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
