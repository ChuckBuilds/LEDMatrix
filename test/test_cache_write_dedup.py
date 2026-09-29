"""Re-saving unchanged data through CacheManager.set does not rewrite the file.

Regression under test: DiskCache.set skipped the disk when a payload matched
the last one written for the key, but CacheManager.set stamps every record
with time.time(), so no two payloads ever matched and every plugin rewrote its
unchanged API data to the SD card on every update cycle. The skip now ignores
the timestamp, and a skipped write moves the file's mtime instead -- so the
entry must stay exactly as fresh as the rewrite would have left it.
"""

import os
import time
from unittest.mock import patch

import pytest

from src.cache import disk_cache as disk_cache_module
from src.cache.disk_cache import DiskCache
from src.cache_manager import CacheManager

class Clock:
    def __init__(self):
        self.now = time.time()

    def __call__(self):
        return self.now


@pytest.fixture
def clock(monkeypatch):
    fake = Clock()
    monkeypatch.setattr(time, "time", fake)
    return fake


@pytest.fixture
def writes(monkeypatch):
    """Paths DiskCache.set actually replaced (its atomic write path)."""
    replaced = []
    real = os.replace

    def counting(src, dst, *args, **kwargs):
        replaced.append(os.path.basename(dst))
        return real(src, dst, *args, **kwargs)

    monkeypatch.setattr(disk_cache_module.os, "replace", counting)
    return replaced


def _manager(cache_dir):
    with patch('src.cache_manager.CacheManager._get_writable_cache_dir',
               return_value=str(cache_dir)):
        manager = CacheManager()
    manager.stop_cleanup_thread()
    return manager


@pytest.fixture
def cm(tmp_path):
    return _manager(tmp_path)


DATA = {"events": [{"id": n, "name": "x" * 20} for n in range(50)]}


def test_resaving_unchanged_data_does_not_rewrite_the_file(cm, clock, writes):
    cm.set("scores", DATA)
    path = cm._get_cache_path("scores")
    first = os.stat(path)
    with open(path, "rb") as f:
        first_bytes = f.read()

    for _ in range(5):
        clock.now += 60
        cm.set("scores", DATA)

    assert writes == ["scores.json"]
    after = os.stat(path)
    assert after.st_ino == first.st_ino
    with open(path, "rb") as f:
        assert f.read() == first_bytes
    # The file records when its content was last saved.
    assert after.st_mtime == pytest.approx(clock.now, abs=1e-3)


def test_skipped_writes_keep_the_entry_fresh(cm, clock, tmp_path):
    cm.set("scores", DATA)
    for _ in range(4):          # re-saved unchanged every 200s
        clock.now += 200
        cm.set("scores", DATA)
    # 800s past the only real write, well beyond max_age=300 of it.
    assert cm.get("scores", max_age=300) == DATA

    # A reader with no memory tier -- the web interface, or this service
    # after a restart -- sees the same freshness from disk.
    other = _manager(tmp_path)
    record = other.get_cached_data("scores", max_age=300)
    assert record is not None and record["data"] == DATA
    assert record["timestamp"] == pytest.approx(clock.now, abs=1e-3)
    assert DiskCache(str(tmp_path)).get("scores", max_age=300) is not None

    # Freshness is the last save, not forever.
    clock.now += 301
    assert DiskCache(str(tmp_path)).get("scores", max_age=300) is None
    assert _manager(tmp_path).get("scores", max_age=300) is None


def test_skipped_writes_keep_a_ttl_entry_fresh(cm, clock, tmp_path):
    cm.set("odds", DATA, ttl=120)
    for _ in range(3):
        clock.now += 100
        cm.set("odds", DATA, ttl=120)
    assert _manager(tmp_path).get("odds", max_age=10) == DATA


def test_changed_data_writes(cm, clock, writes, tmp_path):
    cm.set("scores", DATA)
    clock.now += 60
    cm.set("scores", {"events": []})
    assert writes == ["scores.json", "scores.json"]
    assert _manager(tmp_path).get("scores", max_age=300) == {"events": []}


def test_changed_ttl_writes(cm, clock, writes, tmp_path):
    cm.set("scores", DATA, ttl=60)
    clock.now += 10
    cm.set("scores", DATA, ttl=600)
    clock.now += 10
    cm.set("scores", DATA)
    assert writes == ["scores.json"] * 3
    record = _manager(tmp_path).get_cached_data("scores", max_age=300)
    assert "ttl" not in record


def test_a_record_written_old_stays_old(tmp_path, clock):
    # Only a skip may move freshness forward: a record saved with an old
    # timestamp on purpose is not made fresh by the write's own mtime.
    disk = DiskCache(str(tmp_path))
    disk.set("k", {"timestamp": clock.now - 600, "data": DATA})
    assert disk.get("k", max_age=300) is None


def test_a_file_replaced_by_another_process_is_rewritten(tmp_path, clock):
    ours, theirs = DiskCache(str(tmp_path)), DiskCache(str(tmp_path))
    ours.set("k", {"timestamp": clock.now, "data": "ours"})
    clock.now += 10
    theirs.set("k", {"timestamp": clock.now, "data": "theirs"})
    clock.now += 10
    ours.set("k", {"timestamp": clock.now, "data": "ours"})
    assert DiskCache(str(tmp_path)).get("k", max_age=300)["data"] == "ours"


def test_a_cleared_file_is_rewritten(cm, clock, tmp_path):
    cm.set("scores", DATA)
    os.remove(cm._get_cache_path("scores"))   # e.g. the web UI's delete
    clock.now += 10
    cm.set("scores", DATA)
    assert _manager(tmp_path).get("scores", max_age=300) == DATA
