"""CacheStrategy intervals, pinned across the whole input grid.

The strategy table used to carry a per-sport defaults dict whose every value
was 60, and a soccer branch identical to its else. These tests pin the
returned strategy for every data type x sport key x config shape, so
simplifying the lookup cannot change what any caller gets back. They were
written against the pre-cleanup code and pass on it unchanged.
"""

import pytest

from src.cache.cache_strategy import CacheStrategy


class _Cfg:
    def __init__(self, config):
        self.config = config


class _NoConfigAttr:
    pass


# Plugin config sections are keyed by plugin id. Their intervals belong to the
# plugin, and the strategy table has never read them.
_PLUGIN_ID_CONFIG = {
    pid: {"live_update_interval": 5, "recent_update_interval": 7,
          "upcoming_update_interval": 9}
    for pid in ("football-scoreboard", "basketball-scoreboard",
                "baseball-scoreboard", "hockey-scoreboard", "soccer-scoreboard")
}

CONFIG_MANAGERS = {
    "no_config_manager": None,
    "empty_config": _Cfg({}),
    "plugin_id_config": _Cfg(_PLUGIN_ID_CONFIG),
    "config_is_none": _Cfg(None),
    "config_is_not_a_dict": _Cfg("x"),
    "config_manager_without_config": _NoConfigAttr(),
}

SPORT_KEYS = [None, "", "nfl", "nba", "mlb", "nhl", "soccer", "ncaa_fb",
              "ncaa_baseball", "ncaam_basketball", "milb",
              "football-scoreboard", "curling"]


def _fixed(max_age, memory_ttl, **extra):
    return {"max_age": max_age, "memory_ttl": memory_ttl,
            "force_refresh": False, **extra}


DEFAULT = _fixed(300, 600)
FIXED = {
    "weather_current": _fixed(300, 600),
    "stocks": _fixed(600, 1200, market_hours_only=True),
    "crypto": _fixed(300, 600),
    "sports_recent": _fixed(1800, 3600),
    "sports_upcoming": _fixed(10800, 21600),
    "sports_schedules": _fixed(86400, 172800),
    "leaderboard": _fixed(604800, 1209600),
    "news": _fixed(3600, 7200),
    "odds": _fixed(1800, 3600),
    "odds_live": _fixed(120, 240),
    "team_info": _fixed(604800, 1209600),
    "logos": _fixed(2592000, 5184000),
    "default": DEFAULT,
}


def _expected(data_type, sport_key):
    if data_type in ("live_scores", "sports_live"):
        if sport_key:
            interval = 60
        else:
            interval = 15 if data_type == "live_scores" else 30
        return {"max_age": interval, "memory_ttl": interval * 2,
                "force_refresh": True}
    return FIXED.get(data_type, DEFAULT)


@pytest.mark.parametrize("cm_name", sorted(CONFIG_MANAGERS))
def test_live_interval_is_60_for_every_sport(cm_name):
    strategy = CacheStrategy(config_manager=CONFIG_MANAGERS[cm_name])
    for sport_key in SPORT_KEYS:
        assert strategy.get_sport_live_interval(sport_key) == 60, sport_key


@pytest.mark.parametrize("cm_name", sorted(CONFIG_MANAGERS))
def test_strategy_table_for_every_data_type_and_sport(cm_name):
    strategy = CacheStrategy(config_manager=CONFIG_MANAGERS[cm_name])
    data_types = ["live_scores", "sports_live", *FIXED, "unknown", ""]
    for data_type in data_types:
        for sport_key in SPORT_KEYS:
            got = strategy.get_cache_strategy(data_type, sport_key)
            assert got == _expected(data_type, sport_key), (data_type, sport_key)


@pytest.mark.parametrize("key", [
    "soccer_live", "soccer_current", "soccer_scoreboard", "SOCCER_LIVE",
    "nfl_live", "live", "hockey_current", "nba_live_scores",
])
def test_live_keys_including_soccer_are_sports_live(key):
    assert CacheStrategy().get_data_type_from_key(key) == "sports_live"


@pytest.mark.parametrize("key,data_type", [
    ("odds_soccer_live", "odds_live"),
    ("odds_x", "odds"),
    ("weather", "weather_current"),
    ("crypto_stock", "crypto"),
    ("stock", "stocks"),
    ("news_soccer", "news"),
    ("soccer_schedule", "sports_schedules"),
    ("soccer_recent", "sports_recent"),
    ("soccer_upcoming", "sports_upcoming"),
    ("soccer_logo", "team_info"),
    ("soccer", "default"),
    ("", "default"),
])
def test_non_live_keys_keep_their_data_type(key, data_type):
    assert CacheStrategy().get_data_type_from_key(key) == data_type
