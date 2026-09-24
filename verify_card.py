"""验证 PTCG Live 截图中是否包含本地缓存文件里的卡牌。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import cv2
import numpy as np
import UnityPy


def extract_card(bundle: Path, card_id: str) -> np.ndarray:
    environment = UnityPy.load(str(bundle))
    for asset in environment.objects:
        if asset.type.name != "Texture2D":
            continue
        texture = asset.read()
        if texture.m_Name == card_id:
            rgba = np.asarray(texture.image.convert("RGB"))
            return cv2.cvtColor(rgba, cv2.COLOR_RGB2BGR)
    raise ValueError(f"Texture2D {card_id!r} was not found in {bundle}")


def identify_location(card: np.ndarray, screenshot: np.ndarray) -> dict:
    # 排除缓存纹理周围的透明或灰色边缘。
    height, width = card.shape[:2]
    x0, x1 = round(width * 0.16), round(width * 0.83)
    y0, y1 = round(height * 0.03), round(height * 0.97)
    reference = card[y0:y1, x0:x1]

    sift = cv2.SIFT_create(nfeatures=2500)
    ref_points, ref_descriptors = sift.detectAndCompute(
        cv2.cvtColor(reference, cv2.COLOR_BGR2GRAY), None
    )
    screen_points, screen_descriptors = sift.detectAndCompute(
        cv2.cvtColor(screenshot, cv2.COLOR_BGR2GRAY), None
    )
    if ref_descriptors is None or screen_descriptors is None:
        return {"matched": False, "reason": "No image features found"}

    pairs = cv2.BFMatcher(cv2.NORM_L2).knnMatch(ref_descriptors, screen_descriptors, k=2)
    good = [first for first, second in pairs if first.distance < 0.72 * second.distance]
    if len(good) < 12:
        return {"matched": False, "good_matches": len(good), "reason": "Too few feature matches"}

    source = np.float32([ref_points[m.queryIdx].pt for m in good]).reshape(-1, 1, 2)
    target = np.float32([screen_points[m.trainIdx].pt for m in good]).reshape(-1, 1, 2)
    transform, mask = cv2.findHomography(source, target, cv2.RANSAC, 5.0)
    if transform is None or mask is None:
        return {"matched": False, "good_matches": len(good), "reason": "No consistent card location"}

    inliers = int(mask.sum())
    ratio = inliers / len(good)
    corners = np.float32([[0, 0], [x1 - x0, 0], [x1 - x0, y1 - y0], [0, y1 - y0]])
    corners = cv2.perspectiveTransform(corners.reshape(-1, 1, 2), transform).reshape(-1, 2)
    polygon = [[round(float(x)), round(float(y))] for x, y in corners]
    area = abs(float(cv2.contourArea(corners.astype(np.float32))))
    screenshot_area = screenshot.shape[0] * screenshot.shape[1]
    matched = inliers >= 12 and ratio >= 0.45 and area >= 0.002 * screenshot_area
    return {
        "matched": matched,
        "good_matches": len(good),
        "inliers": inliers,
        "inlier_ratio": round(ratio, 3),
        "polygon": polygon,
        "reason": None if matched else "Insufficient consistent matches or card area",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True, help="Path to the cache __data file")
    parser.add_argument("--card-id", required=True, help="Texture name, e.g. me1_en_073")
    parser.add_argument("--screenshot", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=Path("output"))
    args = parser.parse_args()

    card = extract_card(args.bundle, args.card_id)
    screenshot = cv2.imdecode(np.frombuffer(args.screenshot.read_bytes(), dtype=np.uint8), cv2.IMREAD_COLOR)
    if screenshot is None:
        raise ValueError(f"Cannot read screenshot: {args.screenshot}")
    result = identify_location(card, screenshot)
    result["card_id"] = args.card_id if result["matched"] else None

    args.output.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(args.output / f"{args.card_id}.png"), card)
    if result["matched"]:
        annotated = screenshot.copy()
        cv2.polylines(annotated, [np.array(result["polygon"], np.int32)], True, (0, 255, 0), 4)
        cv2.imwrite(str(args.output / "match.png"), annotated)
    (args.output / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["matched"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
