"""The memory tier never serves a record older than the reader asked for.

A record read from disk went into the memory tier timed from the read, not
from when it was written, so get(max_age=300) could hand out data up to twice
that old: after a restart, after the memory sweep, or in a second process that
loaded a record once and kept serving it.
"""

import time
from unittest.mock import patch

import pytest

from src.cache_manager import CacheManager


class Clock:
    def __init__(self, now):
        self.now = now

    def __call__(self):
        return self.now


@pytest.fixture
def clock(monkeypatch):
    fake = Clock(1_800_000_000.0)
    monkeypatch.setattr(time, "time", fake)
    return fake


def _manager(path):
    # No disk sweep: it judges files by their real mtime against the fake
    # clock and would delete them as months old.
    with patch('src.cache_manager.CacheManager._get_writable_cache_dir',
               return_value=str(path)), \
            patch('src.cache_manager.CacheManager.start_cleanup_thread'):
        return CacheManager()


def test_a_record_loaded_late_expires_on_its_own_timestamp(tmp_path, clock):
    writer = _manager(tmp_path)
    writer.set("weather_current", {"t": 1})

    reader = _manager(tmp_path)  # a restart, or the other process
    clock.now += 250
    assert reader.get("weather_current", max_age=300) == {"t": 1}

    clock.now += 100  # the data is 350 s old; it sat in memory for 100 s
    assert reader.get("weather_current", max_age=300) is None


def test_a_stored_ttl_bounds_the_memory_copy_too(tmp_path, clock):
    writer = _manager(tmp_path)
    writer.set("odds_espn_football_nfl_401", {"spread": 6.5}, ttl=60)

    reader = _manager(tmp_path)
    clock.now += 55
    assert reader.get("odds_espn_football_nfl_401", max_age=3600) == {"spread": 6.5}

    clock.now += 60
    assert reader.get("odds_espn_football_nfl_401", max_age=3600) is None


def test_a_stale_memory_copy_gives_way_to_a_newer_write_on_disk(tmp_path, clock):
    writer = _manager(tmp_path)
    reader = _manager(tmp_path)
    writer.set("stocks_AAPL", {"price": 1})
    assert reader.get("stocks_AAPL", max_age=300) == {"price": 1}

    clock.now += 280
    writer.set("stocks_AAPL", {"price": 2})
    clock.now += 40  # reader's copy: 40 s in memory, 320 s old

    assert reader.get("stocks_AAPL", max_age=300) == {"price": 2}


def test_fresh_records_are_still_served_from_memory(tmp_path, clock):
    manager = _manager(tmp_path)
    manager.set("news_NFL", {"items": []})
    clock.now += 100

    with patch.object(manager._disk_cache_component, "get") as disk_get:
        assert manager.get("news_NFL", max_age=300) == {"items": []}
    disk_get.assert_not_called()


def test_max_age_none_and_records_without_a_timestamp_never_expire(tmp_path, clock):
    manager = _manager(tmp_path)
    manager.set("plugin_health_x", {"ok": True})
    manager.save_cache("raw_record", {"no": "timestamp"})
    clock.now += 10 ** 6

    assert manager.get("plugin_health_x", max_age=None) == {"ok": True}
    assert manager.get_cached_data("raw_record", max_age=None) == {"no": "timestamp"}
