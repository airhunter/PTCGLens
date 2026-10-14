"""平台无关的浮卡摆放入口。"""
from platform_backend import get_backend


def placement_geometry(game_box, card_box, keep_in_game=True):
    return get_backend().placement_geometry(game_box, card_box, keep_in_game)
