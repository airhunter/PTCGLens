"""游戏缓存和字体的系统默认值；不扫描无关用户目录。"""

from pathlib import Path
import sys


def cache_paths(system=None, home=None):
    system, home = system or sys.platform, Path(home or Path.home())
    if system == "darwin":
        support = home / "Library/Application Support"
        caches = home / "Library/Caches"
        cards = [caches / "unity.pokemon.Pokemon TCG Live",
                 caches / "Unity/pokemon_Pokemon TCG Live",
                 support / "Unity/pokemon_Pokemon TCG Live"]
        databases = [support / "pokemon/Pokemon TCG Live/config-cache",
                     support / "unity.pokemon.Pokemon TCG Live/config-cache",
                     support / "Pokemon TCG Live/config-cache"]
        return (next((p for p in cards if p.is_dir()), cards[0]),
                next((p for p in databases if p.is_dir()), databases[0]))
    return (home / "AppData/LocalLow/Unity/pokemon_Pokemon TCG Live",
            home / "AppData/LocalLow/pokemon/Pokemon TCG Live/config-cache")


DEFAULT_CARD_CACHE, DEFAULT_DATABASE_CACHE = cache_paths()


def chinese_font_path():
    candidates = ([Path("/System/Library/Fonts/PingFang.ttc"),
                   Path("/System/Library/Fonts/STHeiti Medium.ttc")]
                  if sys.platform == "darwin" else [Path("C:/Windows/Fonts/msyh.ttc")])
    return next((p for p in candidates if p.is_file()), None)
