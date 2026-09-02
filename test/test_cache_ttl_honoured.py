"""Tests that a per-entry ttl actually controls expiry.

Regression under test: `CacheManager.set(key, data, ttl=...)` stored the value
and no read path ever consulted it. Expiry came from a `max_age` inferred from
substrings in the key ("live", "odds", "stock"), so every caller passing `ttl=`
-- 48 sites across the plugins and 4 in the core -- was writing a number that
did nothing. The old docstring admitted as much: "stored for compatibility but
expiration is still controlled via max_age when reading".

Measured against a real device's cache (8,873 entries carrying a ttl), the
inferred value and the intended one disagreed almost everywhere:

    stocks    max_age  600  vs ttl   1800   4903 entries
    news      max_age 3600  vs ttl    600   1770 entries
    odds      max_age 1800  vs ttl   3600   1301 entries
    images    max_age  300  vs ttl 2592000    20 entries

No `sports_live` entry carries a ttl, so live scores keep their inferred
30-second freshness either way.
"""

import time

import pytest

from src.cache.memory_cache import MemoryCache
from src.cache.disk_cache import DiskCache


@pytest.fixture
def disk(tmp_path):
    return DiskCache(cache_dir=str(tmp_path))


def _record(ttl=None, age=0.0):
    rec = {"data": {"v": 1}, "timestamp": time.time() - age}
    if ttl is not None:
        rec["ttl"] = ttl
    return rec


class TestDiskCacheHonoursTtl:
    def test_ttl_longer_than_max_age_keeps_the_entry(self, disk):
        # The odds case: written wanting an hour, expired at 30 minutes.
        disk.set("odds_espn_football_nfl_401", _record(ttl=3600, age=1900))
        assert disk.get("odds_espn_football_nfl_401", max_age=1800) is not None

    def test_ttl_shorter_than_max_age_expires_the_entry(self, disk):
        # The news case: written wanting 10 minutes, kept for an hour.
        disk.set("news_NHL_1", _record(ttl=600, age=900))
        assert disk.get("news_NHL_1", max_age=3600) is None

    def test_without_a_ttl_max_age_still_applies(self, disk):
        disk.set("plain_key", _record(age=400))
        assert disk.get("plain_key", max_age=300) is None
        disk.set("plain_key2", _record(age=100))
        assert disk.get("plain_key2", max_age=300) is not None

    def test_a_fresh_entry_within_its_ttl_survives(self, disk):
        disk.set("k", _record(ttl=600, age=10))
        assert disk.get("k", max_age=30) is not None

    def test_ttl_zero_expires_immediately(self, disk):
        # 0 means zero seconds, not "forever" -- max_age=None is how a caller
        # asks for no expiry.
        disk.set("k", _record(ttl=0, age=1))
        assert disk.get("k", max_age=99999) is None

    @pytest.mark.parametrize("bad", ["600", None, True, False, -5, {"a": 1}])
    def test_a_nonsense_ttl_falls_back_to_max_age(self, disk, bad):
        # Including bools: True is an int in Python and must not become a 1s ttl.
        rec = _record(age=400)
        rec["ttl"] = bad
        disk.set("k_%s" % type(bad).__name__, rec)
        assert disk.get("k_%s" % type(bad).__name__, max_age=300) is None


class TestMemoryCacheHonoursTtl:
    def test_ttl_longer_than_max_age_keeps_the_entry(self):
        m = MemoryCache()
        m.set("k", _record(ttl=3600))
        m._timestamps["k"] = time.time() - 1900
        assert m.get("k", max_age=1800) is not None

    def test_ttl_shorter_than_max_age_expires_the_entry(self):
        m = MemoryCache()
        m.set("k", _record(ttl=600))
        m._timestamps["k"] = time.time() - 900
        assert m.get("k", max_age=3600) is None

    def test_without_a_ttl_max_age_still_applies(self):
        m = MemoryCache()
        m.set("k", _record())
        m._timestamps["k"] = time.time() - 400
        assert m.get("k", max_age=300) is None

    def test_both_layers_agree(self, tmp_path):
        """A record must not be live in one layer and expired in the other."""
        rec = _record(ttl=3600, age=1900)
        d = DiskCache(cache_dir=str(tmp_path))
        d.set("k", rec)
        m = MemoryCache()
        m.set("k", rec)
        m._timestamps["k"] = rec["timestamp"]
        assert (d.get("k", max_age=1800) is not None) == (m.get("k", max_age=1800) is not None)


class TestEndToEnd:
    def test_set_then_get_respects_the_ttl(self, tmp_path, monkeypatch):
        """The behaviour a caller of CacheManager.set(ttl=...) expects."""
        from src.cache_manager import CacheManager

        cm = CacheManager()
        cm._disk_cache_component = DiskCache(cache_dir=str(tmp_path))
        cm._memory_cache_component = MemoryCache()

        cm.set("odds_espn_football_nfl_401", {"spread": 6.5}, ttl=3600)

        # Age the stored record past the inferred max_age for odds (1800s) but
        # within the ttl the caller asked for.
        path = cm._disk_cache_component.get_cache_path("odds_espn_football_nfl_401")
        import json
        rec = json.load(open(path))
        rec["timestamp"] = time.time() - 1900
        json.dump(rec, open(path, "w"))
        cm._memory_cache_component.clear() if hasattr(
            cm._memory_cache_component, "clear") else None

        got = cm.get_with_auto_strategy("odds_espn_football_nfl_401")
        assert got is not None, "the ttl the caller asked for was ignored"
