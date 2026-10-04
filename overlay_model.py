"""快捷查询的坐标、触发键和浮卡摆放规则。"""

from __future__ import annotations


MODIFIERS = {"ctrl": 0x11, "alt": 0x12, "shift": 0x10}


def shortcut_keys(text: str) -> tuple[int, ...]:
    parts = [part.strip().lower() for part in text.split("+")]
    keys = []
    for part in parts:
        if part in MODIFIERS:
            keys.append(MODIFIERS[part])
        elif len(part) == 1 and part.isascii() and part.isalnum():
            keys.append(ord(part.upper()))
        elif part.startswith("f") and part[1:].isdigit() and 1 <= int(part[1:]) <= 24:
            keys.append(0x70 + int(part[1:]) - 1)
        else:
            raise ValueError("使用 Ctrl、Alt、Shift、字母、数字或 F1–F24，以 + 分隔")
    if not any(key in MODIFIERS.values() for key in keys):
        raise ValueError("至少包含 Ctrl、Alt 或 Shift，避免普通点击误触发")
    if len(set(keys)) != len(keys):
        raise ValueError("快捷键中有重复按键")
    return tuple(keys)


def frame_point(point: tuple[int, int], bbox: tuple[int, int, int, int],
                shape: tuple[int, int]) -> tuple[int, int] | None:
    left, top, right, bottom = bbox
    x, y = point
    if not left <= x < right or not top <= y < bottom:
        return None
    height, width = shape
    return min(width-1, int((x-left)*width/(right-left))), min(height-1, int((y-top)*height/(bottom-top)))


def popup_position(card: tuple[int, int, int, int], size: tuple[int, int],
                   screen: tuple[int, int, int, int], gap: int = 12) -> tuple[int, int]:
    """优先放右侧，其次左侧、下方、上方；屏幕不足时选遮挡最少的位置。"""
    left, top, right, bottom = card
    width, height = size
    sx, sy, sr, sb = screen
    candidates = [(right+gap, top), (left-width-gap, top), (left, bottom+gap), (left, top-height-gap)]
    clamped = [(max(sx, min(x, sr-width)), max(sy, min(y, sb-height))) for x, y in candidates]
    def overlap(pos):
        x, y = pos
        return max(0, min(right, x+width)-max(left, x))*max(0, min(bottom, y+height)-max(top, y))
    return min(clamped, key=overlap)


def map_rect(rect: tuple[float, float, float, float],
             source: tuple[int, int, int, int], target: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
    """按显示器原点与尺寸，将物理像素坐标转换为 Qt 逻辑坐标。"""
    sx, sy, sr, sb = source
    tx, ty, tr, tb = target
    return tuple(round((value-source[i%2]) * ((tr-tx)/(sr-sx) if i%2 == 0 else (tb-ty)/(sb-sy))
                       + target[i%2]) for i, value in enumerate(rect))


def intersect_rect(first, second, margin=0):
    left, top = max(first[0], second[0])+margin, max(first[1], second[1])+margin
    right, bottom = min(first[2], second[2])-margin, min(first[3], second[3])-margin
    return (left, top, right, bottom) if right > left and bottom > top else None


def relative_rect(box, reference):
    left, top, right, bottom = reference
    return tuple((value-reference[i%2])/(right-left if i%2 == 0 else bottom-top)
                 for i, value in enumerate(box))


def restore_rect(relative, reference):
    left, top, right, bottom = reference
    return tuple(round(value*(right-left if i%2 == 0 else bottom-top)+reference[i%2])
                 for i, value in enumerate(relative))
