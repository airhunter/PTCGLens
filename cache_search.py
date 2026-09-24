"""建立本地 PTCGL 卡图特征索引，并在截图中检索卡牌。"""

from __future__ import annotations

import argparse
import json
import re
import time
from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np

from verify_card import extract_card


CARD_DIRECTORY = re.compile(r"^[a-z0-9-]+_en_\d{3}(?:_t)?$")


def read_image(path: Path) -> np.ndarray:
    image = cv2.imdecode(np.frombuffer(path.read_bytes(), dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError(f"Cannot read image: {path}")
    return image


def cache_cards(cache_root: Path) -> list[tuple[str, Path]]:
    # 同一张卡同时缓存原图和缩略图时，优先使用原图。
    selected: dict[str, Path] = {}
    for directory in sorted(cache_root.iterdir()):
        if not directory.is_dir() or not CARD_DIRECTORY.fullmatch(directory.name):
            continue
        card_id = directory.name.removesuffix("_t")
        if card_id not in selected or not directory.name.endswith("_t"):
            selected[card_id] = directory
    return sorted(selected.items())


def reference_features(image: np.ndarray) -> tuple[np.ndarray, np.ndarray, tuple[int, int]]:
    height, width = image.shape[:2]
    x0, x1 = round(width * 0.16), round(width * 0.83)
    y0, y1 = round(height * 0.03), round(height * 0.97)
    gray = cv2.cvtColor(image[y0:y1, x0:x1], cv2.COLOR_BGR2GRAY)
    points, descriptors = cv2.SIFT_create(nfeatures=1800).detectAndCompute(gray, None)
    if descriptors is None:
        raise ValueError("No card image features found")
    coordinates = np.float32([point.pt for point in points])
    return coordinates, descriptors, (x1 - x0, y1 - y0)


def build_index(cache_root: Path, index_dir: Path) -> None:
    if not cache_root.is_dir():
        raise ValueError(f"Cache root does not exist: {cache_root}")
    index_dir.mkdir(parents=True, exist_ok=True)
    records = []
    failures = []
    cards = cache_cards(cache_root)
    for position, (card_id, directory) in enumerate(cards, 1):
        texture_name = directory.name
        bundles = sorted(directory.rglob("__data"), key=lambda path: path.stat().st_mtime, reverse=True)
        if not bundles:
            failures.append({"card_id": card_id, "reason": "No __data bundle"})
            continue
        try:
            card = extract_card(bundles[0], texture_name)
            points, descriptors, dimensions = reference_features(card)
            file_name = f"{card_id}.npz"
            np.savez_compressed(
                index_dir / file_name,
                points=points,
                descriptors=descriptors,
                width=dimensions[0],
                height=dimensions[1],
            )
            records.append({
                "card_id": card_id,
                "variant": "thumbnail" if texture_name.endswith("_t") else "full",
                "features": len(points),
                "file": file_name,
            })
        except Exception as exc:
            failures.append({"card_id": card_id, "reason": str(exc)})
        if position % 10 == 0 or position == len(cards):
            print(f"Indexed {position}/{len(cards)} directories; {len(records)} cards ready", flush=True)
    (index_dir / "manifest.json").write_text(
        json.dumps({"cards": records, "failures": failures}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps({"cards": len(records), "failures": failures}, ensure_ascii=False, indent=2))


def update_index(cache_root: Path, index_dir: Path) -> dict:
    """只为新增或已变化的缓存卡图更新特征索引。"""
    if not cache_root.is_dir():
        raise ValueError(f"Cache root does not exist: {cache_root}")
    index_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = index_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.is_file() else {"cards": []}
    records = {record["card_id"]: record for record in manifest["cards"]}
    added = 0
    updated = 0
    pending = []
    failures = []
    for card_id, directory in cache_cards(cache_root):
        bundles = sorted(directory.rglob("__data"), key=lambda path: path.stat().st_mtime_ns, reverse=True)
        if not bundles:
            failures.append({"card_id": card_id, "reason": "No __data bundle"})
            continue
        bundle = bundles[0]
        stat = bundle.stat()
        old = records.get(card_id)
        variant = "thumbnail" if directory.name.endswith("_t") else "full"
        source = str(bundle.resolve())
        fingerprint = {"bundle_path": source, "bundle_size": stat.st_size, "bundle_mtime_ns": stat.st_mtime_ns}
        old_path = index_dir / old["file"] if old else None
        unchanged = (
            old is not None and old_path.is_file() and old["variant"] == variant
            and ("bundle_path" not in old or all(old.get(key) == value for key, value in fingerprint.items()))
        )
        if unchanged:
            records[card_id] = {**old, **fingerprint}
            continue
        # 游戏刚写入的缓存可能尚未完整，留待下一轮重试。
        if time.time_ns() - stat.st_mtime_ns < 2_000_000_000:
            pending.append(card_id)
            continue
        temporary = index_dir / f".{card_id}.tmp.npz"
        destination = index_dir / f"{card_id}.npz"
        try:
            card = extract_card(bundle, directory.name)
            points, descriptors, dimensions = reference_features(card)
            np.savez_compressed(
                temporary,
                points=points,
                descriptors=descriptors,
                width=dimensions[0],
                height=dimensions[1],
            )
            temporary.replace(destination)
            records[card_id] = {
                "card_id": card_id, "variant": variant, "features": len(points),
                "file": destination.name, **fingerprint,
            }
            if old is None:
                added += 1
            else:
                updated += 1
        except Exception as exc:
            failures.append({"card_id": card_id, "reason": str(exc)})
            temporary.unlink(missing_ok=True)
    document = {"cards": sorted(records.values(), key=lambda item: item["card_id"]), "failures": failures}
    temporary_manifest = index_dir / "manifest.json.tmp"
    temporary_manifest.write_text(json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary_manifest.replace(manifest_path)
    load_index.cache_clear()
    return {"cards": len(records), "added": added, "updated": updated, "pending": pending, "failures": failures}


def candidate_result(
    reference_points: np.ndarray,
    reference_descriptors: np.ndarray,
    dimensions: tuple[int, int],
    screenshot_points: list[cv2.KeyPoint],
    screenshot_descriptors: np.ndarray,
    screenshot_shape: tuple[int, int],
) -> dict:
    pairs = cv2.BFMatcher(cv2.NORM_L2).knnMatch(reference_descriptors, screenshot_descriptors, k=2)
    good = [pair[0] for pair in pairs if len(pair) == 2
            and pair[0].distance < 0.72 * pair[1].distance]
    if len(good) < 12:
        return {"matched": False, "good_matches": len(good), "inliers": 0}
    source = np.float32([reference_points[m.queryIdx] for m in good]).reshape(-1, 1, 2)
    target = np.float32([screenshot_points[m.trainIdx].pt for m in good]).reshape(-1, 1, 2)
    transform, mask = cv2.findHomography(source, target, cv2.RANSAC, 5.0)
    if transform is None or mask is None:
        return {"matched": False, "good_matches": len(good), "inliers": 0}
    inliers = int(mask.sum())
    ratio = inliers / len(good)
    width, height = dimensions
    corners = np.float32([[0, 0], [width, 0], [width, height], [0, height]])
    corners = cv2.perspectiveTransform(corners.reshape(-1, 1, 2), transform).reshape(-1, 2)
    polygon = [[round(float(x)), round(float(y))] for x, y in corners]
    area = abs(float(cv2.contourArea(corners)))
    screenshot_area = screenshot_shape[0] * screenshot_shape[1]
    area_ratio = area / screenshot_area
    screen_height, screen_width = screenshot_shape
    fully_visible = all(0 <= x < screen_width and 0 <= y < screen_height for x, y in polygon)
    # 当前原型主要检索悬停后放大的卡牌预览。
    matched = inliers >= 30 and ratio >= 0.45 and area_ratio >= 0.03 and fully_visible
    return {
        "matched": matched,
        "good_matches": len(good),
        "inliers": inliers,
        "inlier_ratio": round(ratio, 3),
        "area_ratio": round(area_ratio, 4),
        "fully_visible": fully_visible,
        "polygon": polygon,
    }


@lru_cache(maxsize=2)
def load_index(index_dir: Path) -> list[dict]:
    manifest = json.loads((index_dir / "manifest.json").read_text(encoding="utf-8"))
    records = []
    for record in manifest["cards"]:
        with np.load(index_dir / record["file"]) as data:
            records.append({
                **record,
                "points": data["points"],
                "descriptors": data["descriptors"],
                "dimensions": (int(data["width"]), int(data["height"])),
            })
    return records


def search_index(index_dir: Path, screenshot_path: Path, result_path: Path, verbose: bool = True) -> dict:
    records = load_index(index_dir.resolve())
    screenshot = read_image(screenshot_path)
    screenshot_points, screenshot_descriptors = cv2.SIFT_create(nfeatures=4000).detectAndCompute(
        cv2.cvtColor(screenshot, cv2.COLOR_BGR2GRAY), None
    )
    if screenshot_descriptors is None:
        raise ValueError("No screenshot features found")

    candidates = []
    for position, record in enumerate(records, 1):
        result = candidate_result(
            record["points"],
            record["descriptors"],
            record["dimensions"],
            screenshot_points,
            screenshot_descriptors,
            screenshot.shape[:2],
        )
        result.update(card_id=record["card_id"], variant=record["variant"])
        candidates.append(result)
        if verbose and (position % 20 == 0 or position == len(records)):
            print(f"Compared {position}/{len(records)} cards", flush=True)

    candidates.sort(key=lambda entry: (entry["matched"], entry["inliers"], entry["inlier_ratio"] if "inlier_ratio" in entry else 0), reverse=True)
    winner = next((entry for entry in candidates if entry["matched"]), None)
    result = {"card_id": winner["card_id"] if winner else None, "searched_cards": len(records), "top_candidates": candidates[:10]}
    result_path.parent.mkdir(parents=True, exist_ok=True)
    result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    preview = screenshot.copy()
    if winner:
        cv2.polylines(preview, [np.array(winner["polygon"], np.int32)], True, (0, 255, 0), 4)
    cv2.imwrite(str(result_path.with_suffix(".png")), preview)
    if verbose:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    build = subparsers.add_parser("build")
    build.add_argument("--cache-root", type=Path, required=True)
    build.add_argument("--index-dir", type=Path, default=Path("output/index"))
    update = subparsers.add_parser("update")
    update.add_argument("--cache-root", type=Path, default=Path.home() / "AppData/LocalLow/Unity/pokemon_Pokemon TCG Live")
    update.add_argument("--index-dir", type=Path, default=Path("output/index"))
    search = subparsers.add_parser("search")
    search.add_argument("--index-dir", type=Path, default=Path("output/index"))
    search.add_argument("--screenshot", type=Path, required=True)
    search.add_argument("--result", type=Path, default=Path("output/search-result.json"))
    args = parser.parse_args()
    if args.command == "build":
        build_index(args.cache_root, args.index_dir)
    elif args.command == "update":
        print(json.dumps(update_index(args.cache_root, args.index_dir), ensure_ascii=False, indent=2))
    else:
        search_index(args.index_dir, args.screenshot, args.result)


if __name__ == "__main__":
    main()
