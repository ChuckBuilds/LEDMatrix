"""CacheManager's memory tier is MemoryCache's, not a copy of it.

CacheManager used to re-implement MemoryCache.cleanup() line for line and
read the component's private dicts and lock through aliases bound at
construction. Those aliases went stale the moment the component was replaced
(tests do exactly that), and the listing of the cache *directory* held the
memory tier's lock for the whole scan.
"""

import os
import time
from unittest.mock import patch

import pytest

from src.cache.memory_cache import MemoryCache
from src.cache_manager import CacheManager


@pytest.fixture
def cm(tmp_path):
    with patch('src.cache_manager.CacheManager._get_writable_cache_dir',
               return_value=str(tmp_path)):
        manager = CacheManager()
    # The disk sweep thread stats this directory too; keep it out of the
    # os.stat spies below.
    manager.stop_cleanup_thread()
    yield manager


def test_cleanup_and_stats_follow_a_replaced_component(cm):
    """Replace the component the way test_cache_ttl_honoured does; cleanup and
    stats must act on the new one, not on dicts captured at construction."""
    cm._memory_cache_component = MemoryCache(max_size=7, cleanup_interval=11.0)
    cm._memory_cache_component.set("fresh", {"v": 1})
    cm._memory_cache_component.set("stale", {"v": 2})
    cm._memory_cache_component._timestamps["stale"] = time.time() - 4000

    assert cm._cleanup_memory_cache(force=True) == 1
    assert cm._memory_cache_component.get("stale") is None
    assert cm._memory_cache_component.get("fresh") == {"v": 1}

    stats = cm.get_memory_cache_stats()
    assert stats["size"] == 1
    assert stats["max_size"] == 7
    assert stats["cleanup_interval"] == 11.0
    assert stats["usage_percent"] == pytest.approx(100 / 7)


def test_periodic_cleanup_is_throttled_and_records_its_run(cm):
    mem = cm._memory_cache_component
    mem.set("stale", {"v": 1})
    mem._timestamps["stale"] = time.time() - 4000

    # Within the interval: nothing runs, even through the get path.
    assert cm._cleanup_memory_cache() == 0
    assert mem.size() == 1

    mem._last_cleanup = time.time() - mem._cleanup_interval - 1
    before = time.time()
    cm.get_cached_data("missing")          # triggers the periodic sweep
    assert mem.size() == 0
    assert cm.get_memory_cache_stats()["last_cleanup"] >= before


def test_stats_have_the_documented_shape(cm):
    cm.set("k", {"v": 1})
    stats = cm.get_memory_cache_stats()
    assert set(stats) == {"size", "max_size", "usage_percent",
                          "last_cleanup", "cleanup_interval"}
    assert stats["size"] == 1
    assert stats["max_size"] == cm._memory_cache_component.max_size()


def test_listing_the_cache_dir_does_not_hold_the_memory_lock(cm, tmp_path):
    """8,864 files on a real rig: every get/set used to wait out the scan."""
    for name in ("a", "b"):
        (tmp_path / f"{name}.json").write_text("{}")
    (tmp_path / "notes.txt").write_text("x")

    lock = cm._memory_cache_component._lock
    held_during_stat = []
    real_stat = os.stat

    def spying_stat(path, *args, **kwargs):
        held_during_stat.append(lock.locked())
        return real_stat(path, *args, **kwargs)

    with patch('src.cache_manager.os.stat', side_effect=spying_stat):
        files = cm.list_cache_files()

    assert held_during_stat and not any(held_during_stat)
    assert sorted(f["key"] for f in files) == ["a", "b"]


def test_listing_skips_a_file_deleted_mid_scan(cm, tmp_path):
    for name in ("a", "b"):
        (tmp_path / f"{name}.json").write_text("{}")
    real_stat = os.stat

    def vanishing_stat(path, *args, **kwargs):
        if str(path).endswith("a.json"):
            raise FileNotFoundError(path)
        return real_stat(path, *args, **kwargs)

    with patch('src.cache_manager.os.stat', side_effect=vanishing_stat):
        files = cm.list_cache_files()

    assert [f["key"] for f in files] == ["b"]


def test_listing_is_newest_first(cm, tmp_path):
    now = time.time()
    for i, name in enumerate(("old", "mid", "new")):
        p = tmp_path / f"{name}.json"
        p.write_text("{}")
        os.utime(p, (now - 300 + i * 100, now - 300 + i * 100))
    assert [f["key"] for f in cm.list_cache_files()] == ["new", "mid", "old"]
