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
