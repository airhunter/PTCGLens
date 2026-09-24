"""按固定对局布局逐个识别可见卡位的多卡原型。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from cache_search import cache_cards, candidate_result, load_index, read_image
from global_cards import LargeCardFinder
from verify_card import extract_card


THUMB_SIZE = (160, 224)


def build_visual_index(index_dir: Path, cache_root: Path, visual_dir: Path) -> dict:
    """为特征索引中已有的卡牌提取缩略图。"""
    manifest = json.loads((index_dir / "manifest.json").read_text(encoding="utf-8"))
    directories = dict(cache_cards(cache_root))
    visual_dir.mkdir(parents=True, exist_ok=True)
    built = []
    missing = []
    for record in manifest["cards"]:
        card_id = record["card_id"]
        directory = directories.get(card_id)
        if directory is None:
            missing.append(card_id)
            continue
        bundles = sorted(directory.rglob("__data"), key=lambda path: path.stat().st_mtime, reverse=True)
        if not bundles:
            missing.append(card_id)
            continue
        destination = visual_dir / f"{card_id}.png"
        if destination.is_file() and destination.stat().st_mtime >= bundles[0].stat().st_mtime:
            continue
        try:
            card = extract_card(bundles[0], directory.name)
            height, width = card.shape[:2]
            crop = card[round(height * .03):round(height * .97), round(width * .16):round(width * .83)]
            thumbnail = cv2.resize(crop, THUMB_SIZE, interpolation=cv2.INTER_AREA)
            if not cv2.imwrite(str(destination), thumbnail):
                raise OSError(f"Cannot write {destination}")
            built.append(card_id)
        except Exception as exc:
            missing.append(f"{card_id}: {exc}")
    return {"indexed": len(manifest["cards"]), "rebuilt": len(built), "missing": missing}


def scaled_box(box: list[int], reference_size: list[int], image_shape: tuple[int, int]) -> list[int]:
    screen_height, screen_width = image_shape
    ref_width, ref_height = reference_size
    x0, y0, x1, y1 = box
    return [
        max(0, min(screen_width, round(x0 * screen_width / ref_width))),
        max(0, min(screen_height, round(y0 * screen_height / ref_height))),
        max(0, min(screen_width, round(x1 * screen_width / ref_width))),
        max(0, min(screen_height, round(y1 * screen_height / ref_height))),
    ]


def detect_hand_slots(screenshot: np.ndarray, reference_size: list[int]) -> list[dict]:
    """从手牌上沿的浅色卡框推断当前手牌数量和横向位置。"""
    height, width = screenshot.shape[:2]
    left, right = round(width * .1), round(width * .9)
    top, bottom = round(height * .87), round(height * .93)
    band = screenshot[top:bottom, left:right]
    if band.size == 0:
        return []
    hsv = cv2.cvtColor(band, cv2.COLOR_BGR2HSV)
    pale = ((hsv[:, :, 1] < 65) & (hsv[:, :, 2] > 130)).astype(np.float32)
    projection = np.convolve(pale.mean(axis=0), np.ones(9) / 9, mode="same")
    active = projection > .15
    changes = np.diff(np.r_[False, active, False].astype(np.int8))
    runs = [[int(start + left), int(end + left)] for start, end in
            zip(np.flatnonzero(changes == 1), np.flatnonzero(changes == -1))
            if end - start >= round(width * .01)]

    # 卡牌上的徽标有时会把同一条白色边框截成两段；限制合并宽度，避免吞入相邻卡牌的碎片。
    merged = []
    for start, end in runs:
        if merged and start - merged[-1][1] <= round(width * .015) \
                and end - merged[-1][0] <= round(width * .105):
            merged[-1][1] = end
        else:
            merged.append([start, end])
    cards = [(start, end) for start, end in merged if end - start >= round(width * .06)]
    if not 1 <= len(cards) <= 10:
        return []
    widths = [end - start for start, end in cards if end - start >= round(width * .085)]
    card_width = int(np.median(widths)) if widths else round(width * .096)
    spacing = np.diff([start for start, _ in cards])
    if len(spacing) and spacing.min() < width * .07:
        return []
    usual_spacing = spacing[spacing < width * .14]
    step = int(np.median(usual_spacing)) if len(usual_spacing) else round(width * .098)
    starts = [start for start, _ in cards]
    # 手牌围绕屏幕中心等间距排布；悬停导致某张白边消失时，可据此补齐。
    for count in range(len(starts), 11):
        first = round((width - card_width - (count - 1) * step) / 2)
        expected = [first + position * step for position in range(count)]
        nearest = [min(range(count), key=lambda position: abs(start - expected[position])) for start in starts]
        if len(set(nearest)) == len(starts) and all(
            abs(start - expected[position]) <= width * .018
            for start, position in zip(starts, nearest)
        ):
            starts = [starts[nearest.index(position)] if position in nearest else expected[position]
                      for position in range(count)]
            break
    ref_width, ref_height = reference_size
    hand_top = round(height * .875)
    return [
        {
            "key": f"hand_{number}", "label": f"手牌 {number}",
            "box": [round(start * ref_width / width), round(hand_top * ref_height / height),
                    round((start + card_width) * ref_width / width), ref_height],
        }
        for number, start in enumerate(starts, 1)
    ]


def adaptive_layout(screenshot: np.ndarray, layout: dict) -> dict:
    """有可靠手牌边框时，以画面检测位置替换样例布局中的固定手牌位。"""
    hands = detect_hand_slots(screenshot, layout["reference_size"])
    return {**layout, "slots": [slot for slot in layout["slots"]
                                  if not slot["key"].startswith("hand_")] + hands}


def visual_score(observed: np.ndarray, reference: np.ndarray, visible_rows: list[float]) -> float:
    """缩小图像后比较卡牌可见区域，降低文字和特效偏移的影响。"""
    height, width = observed.shape[:2]
    projected = cv2.resize(reference, (width, round(width * THUMB_SIZE[1] / THUMB_SIZE[0])), interpolation=cv2.INTER_AREA)
    start = max(0, round(visible_rows[0] * height))
    end = min(height, len(projected), round(visible_rows[1] * height))
    if end - start < 18:
        return -1.0
    target_height = max(8, round(32 * (end - start) / width))
    observed_small = cv2.resize(observed[start:end], (32, target_height), interpolation=cv2.INTER_AREA)
    reference_small = cv2.resize(projected[start:end], (32, target_height), interpolation=cv2.INTER_AREA)
    observed_small = cv2.GaussianBlur(observed_small, (5, 5), 0)
    reference_small = cv2.GaussianBlur(reference_small, (5, 5), 0)
    return float(cv2.matchTemplate(observed_small, reference_small, cv2.TM_CCOEFF_NORMED)[0, 0])


def sift_tiebreak(observed: np.ndarray, candidates: list[dict], index: dict[str, dict]) -> tuple[str | None, int]:
    enlarged = cv2.resize(observed, None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC)
    points, descriptors = cv2.SIFT_create(nfeatures=3000).detectAndCompute(
        cv2.cvtColor(enlarged, cv2.COLOR_BGR2GRAY), None
    )
    if descriptors is None:
        return None, 0
    scores = []
    for candidate in candidates:
        record = index.get(candidate["card_id"])
        if record is None:
            continue
        match = candidate_result(
            record["points"], record["descriptors"], record["dimensions"],
            points, descriptors, enlarged.shape[:2],
        )
        if match.get("fully_visible") and .4 <= match["area_ratio"] <= 1.4:
            scores.append((candidate["card_id"], match["inliers"]))
    scores.sort(key=lambda item: item[1], reverse=True)
    if scores and scores[0][1] >= 15 and (len(scores) == 1 or scores[0][1] >= 1.5 * max(scores[1][1], 1)):
        return scores[0]
    return None, scores[0][1] if scores else 0


def is_empty_slot(observed: np.ndarray, std_max: float = 25, edge_max: float = .09) -> bool:
    gray = cv2.cvtColor(observed, cv2.COLOR_BGR2GRAY)
    height, width = gray.shape
    interior = gray[round(.12 * height):round(.88 * height), round(.08 * width):round(.92 * width)]
    edges = cv2.Canny(gray, 60, 150)
    return float(interior.std()) < std_max and float((edges > 0).mean()) < edge_max


def recognise_slot(
    screenshot: np.ndarray, slot: dict, reference_size: list[int],
    visuals: list[tuple[str, np.ndarray]], cards: dict, index: dict[str, dict],
) -> dict:
    box = scaled_box(slot["box"], reference_size, screenshot.shape[:2])
    x0, y0, x1, y1 = box
    result = {"key": slot["key"], "label": slot["label"], "box": box, "status": "unknown", "candidates": []}
    if x1 <= x0 or y1 <= y0:
        return result
    observed = screenshot[y0:y1, x0:x1]
    if slot.get("may_be_empty") and is_empty_slot(
        observed, slot.get("empty_std_max", 25), slot.get("empty_edge_max", .09)
    ):
        result["status"] = "empty"
        return result
    visible_rows = slot.get("visible_rows", [0.0, 1.0])
    ranked = [
        {"card_id": card_id, "name_en": cards.get(card_id, {}).get("name_en", card_id),
         "score": visual_score(observed, reference, visible_rows)}
        for card_id, reference in visuals
    ]
    ranked.sort(key=lambda item: item["score"], reverse=True)
    if not ranked:
        return result
    # 不同印次可能共用卡图，先按卡名合并，再判断候选差距。
    by_name = {}
    for item in ranked:
        by_name.setdefault(item["name_en"], item)
    names = sorted(by_name.values(), key=lambda item: item["score"], reverse=True)
    winner = names[0]
    margin = winner["score"] - (names[1]["score"] if len(names) > 1 else -1.0)
    sift_inliers = 0
    if margin < .08 and visible_rows == [0.0, 1.0]:
        tiebreak_id, sift_inliers = sift_tiebreak(observed, ranked[:8], index)
        if tiebreak_id is not None:
            winner = next(item for item in ranked if item["card_id"] == tiebreak_id)
            margin = max(margin, .08)
    result["candidates"] = [
        {**item, "score": round(item["score"], 3)} for item in names[:3]
    ]
    result["score"] = round(winner["score"], 3)
    result["margin_to_other_name"] = round(margin, 3)
    result["sift_inliers"] = sift_inliers
    if winner["score"] < .35 or (winner["score"] < .4 and margin < .04 and sift_inliers < 15) \
            or (winner["score"] < .52 and margin < .03 and sift_inliers < 15):
        return result
    card = cards.get(winner["card_id"], {})
    result["card_id"] = winner["card_id"]
    result["name_en"] = winner["name_en"]
    result["name_zh"] = card.get("name_zh")
    result["possible_printings"] = [
        item["card_id"] for item in ranked
        if item["name_en"] == winner["name_en"] and winner["score"] - item["score"] <= .015
    ][:5]
    result["status"] = "matched" if winner["score"] >= .4 and margin >= .07 else "tentative"
    return result


def suppress_occluded_slots(slots: list[dict], preview: dict) -> list[dict]:
    """放大卡遮住底下的卡位时，暂不报告底层卡牌身份。"""
    left, top, right, bottom = preview["box"]
    cleaned = []
    for slot in slots:
        if slot["key"] == "preview":
            cleaned.append(slot)
            continue
        x0, y0, x1, y1 = slot["box"]
        intersection = max(0, min(right, x1) - max(left, x0)) * max(0, min(bottom, y1) - max(top, y0))
        area = max(1, (x1 - x0) * (y1 - y0))
        if intersection / area >= .25:
            cleaned.append({"key": slot["key"], "label": slot["label"], "box": slot["box"],
                            "status": "occluded", "occluded_by": "preview", "candidates": []})
        else:
            cleaned.append(slot)
    return cleaned


def annotate(screenshot: np.ndarray, slots: list[dict], destination: Path) -> None:
    canvas = Image.fromarray(cv2.cvtColor(screenshot, cv2.COLOR_BGR2RGB))
    draw = ImageDraw.Draw(canvas)
    try:
        font = ImageFont.truetype("C:/Windows/Fonts/msyh.ttc", 19)
    except OSError:
        font = ImageFont.load_default()
    colors = {"matched": "#31e68a", "tentative": "#ffcf58", "unknown": "#ff6868",
              "empty": "#738097", "occluded": "#90a9d9"}
    for number, slot in enumerate(slots, 1):
        x0, y0, x1, y1 = slot["box"]
        color = colors[slot["status"]]
        draw.rectangle((x0, y0, x1, y1), outline=color, width=3)
        badge = f"{number:02d}"
        draw.rectangle((x0, y0, x0 + 38, y0 + 25), fill="#202633")
        draw.text((x0 + 3, y0), badge, font=font, fill=color)
    legend_width = 440
    legend = Image.new("RGB", (canvas.width + legend_width, canvas.height), "#18202c")
    legend.paste(canvas, (0, 0))
    draw = ImageDraw.Draw(legend)
    draw.text((canvas.width + 15, 18), "多卡识别 · 单帧原型", font=font, fill="white")
    row_height = min(48, max(38, (canvas.height - 80) // max(len(slots), 1)))
    for number, slot in enumerate(slots, 1):
        y = 60 + (number - 1) * row_height
        color = colors[slot["status"]]
        name = slot.get("name_zh") or slot.get("name_en") or (
            "空位" if slot["status"] == "empty" else "被遮挡" if slot["status"] == "occluded" else "待确认")
        draw.text((canvas.width + 15, y), f"{number:02d}  {slot['label']}", font=font, fill=color)
        draw.text((canvas.width + 55, y + 23), name[:27], font=font, fill="#e7ecf3")
    destination.parent.mkdir(parents=True, exist_ok=True)
    legend.save(destination)


def search(screenshot_path: Path, layout_path: Path, index_dir: Path,
           visual_dir: Path, card_data_path: Path, output: Path,
           adapt_hand: bool = False, adapt_preview: bool = False) -> dict:
    screenshot = read_image(screenshot_path)
    layout = json.loads(layout_path.read_text(encoding="utf-8"))
    ref_width, ref_height = layout["reference_size"]
    height, width = screenshot.shape[:2]
    if abs(width / height - ref_width / ref_height) > .04:
        raise ValueError("Screenshot aspect ratio does not fit this experimental layout")
    if adapt_hand:
        layout = adaptive_layout(screenshot, layout)
    records = load_index(index_dir.resolve())
    index = {record["card_id"]: record for record in records}
    visuals = [(path.stem, read_image(path)) for path in sorted(visual_dir.glob("*.png")) if path.stem in index]
    if not visuals:
        raise ValueError(f"No visual card index in {visual_dir}; run build-visual first")
    card_data = json.loads(card_data_path.read_text(encoding="utf-8"))["cards"]
    slots = [recognise_slot(screenshot, slot, layout["reference_size"], visuals, card_data, index)
             for slot in layout["slots"]]
    if adapt_preview:
        preview = LargeCardFinder(records).find(screenshot, card_data)
        if preview:
            slots = [preview if slot["key"] == "preview" else slot for slot in slots]
            slots = suppress_occluded_slots(slots, preview)
    document = {
        "method": "fixed-layout, visible-region image comparison, SIFT tiebreak",
        "screenshot": str(screenshot_path.resolve()),
        "layout": str(layout_path.resolve()),
        "reference_cards": len(visuals),
        "slots": slots,
        "summary": {
            "matched": sum(slot["status"] == "matched" for slot in slots),
            "tentative": sum(slot["status"] == "tentative" for slot in slots),
            "unknown": sum(slot["status"] == "unknown" for slot in slots),
            "empty": sum(slot["status"] == "empty" for slot in slots),
            "occluded": sum(slot["status"] == "occluded" for slot in slots),
        },
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8")
    annotate(screenshot, slots, output.with_suffix(".png"))
    return document


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    subcommands = parser.add_subparsers(dest="command", required=True)
    build = subcommands.add_parser("build-visual", help="Extract card thumbnails from the local Unity cache")
    build.add_argument("--index-dir", type=Path, default=Path("output/index"))
    build.add_argument("--cache-root", type=Path, default=Path.home() / "AppData/LocalLow/Unity/pokemon_Pokemon TCG Live")
    build.add_argument("--visual-dir", type=Path, default=Path("output/card-thumbnails"))
    find = subcommands.add_parser("search", help="Recognise cards in each configured battle slot")
    find.add_argument("--screenshot", type=Path, required=True)
    find.add_argument("--layout", type=Path, default=Path("battle_layout.sample.json"))
    find.add_argument("--index-dir", type=Path, default=Path("output/index"))
    find.add_argument("--visual-dir", type=Path, default=Path("output/card-thumbnails"))
    find.add_argument("--card-data", type=Path, default=Path("output/card-data.json"))
    find.add_argument("--output", type=Path, default=Path("output/battle-multi.json"))
    find.add_argument("--adaptive-hand", action="store_true", help="按截图自动定位手牌")
    find.add_argument("--adaptive-preview", action="store_true", help="按截图定位放大预览")
    args = parser.parse_args()
    if args.command == "build-visual":
        print(json.dumps(build_visual_index(args.index_dir, args.cache_root, args.visual_dir), ensure_ascii=False))
    else:
        document = search(args.screenshot, args.layout, args.index_dir, args.visual_dir, args.card_data, args.output,
                          args.adaptive_hand, args.adaptive_preview)
        print(json.dumps(document["summary"], ensure_ascii=False))


if __name__ == "__main__":
    main()
