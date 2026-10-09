"""CacheStrategy intervals, pinned across the whole input grid.

The strategy table used to carry a per-sport defaults dict whose every value
was 60, a soccer branch identical to its else, and a config lookup of
`<sport>_scoreboard` sections that only the replaced built-in scoreboards
had. These tests pin the returned strategy for every data type x sport key,
so simplifying the lookup cannot change what any caller gets back.
"""

import pytest

from src.cache.cache_strategy import CacheStrategy


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


def test_strategy_table_for_every_data_type_and_sport():
    strategy = CacheStrategy()
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
