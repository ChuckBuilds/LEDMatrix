"""An unchanged CacheManager.set() does not rewrite the file, and the skip
never changes how old the record looks.

DiskCache.set already skipped a payload identical to the last one it wrote,
but CacheManager.set stamps every record with time.time(), so for set() the
payload was never identical and every unchanged re-save was a full rewrite on
the SD card. The digest now leaves the header timestamp out, and the newer
timestamp lives in the file's mtime instead ("UNCHANGED RE-SAVES" in
src/cache/disk_cache.py). These tests pin both halves: the write is skipped,
and every reader still ages the record from its newest save -- across a
restart, and with a bound on what a foreign mtime can claim.
"""

import os
import shutil
import tempfile
import time
from unittest.mock import patch

import pytest

from src.cache import disk_cache as disk_cache_module
from src.cache.disk_cache import (
    DiskCache,
    _MAX_TIMESTAMP_LIFT,
    _effective_timestamp,
)
from src.cache_manager import CacheManager


@pytest.fixture
def writes(monkeypatch):
    """Count real writes: every atomic write starts with mkstemp."""
    calls = []
    real = tempfile.mkstemp

    def counting(*args, **kwargs):
        calls.append(kwargs.get("prefix"))
        return real(*args, **kwargs)

    monkeypatch.setattr(disk_cache_module.tempfile, "mkstemp", counting)
    return calls


@pytest.fixture
def clock(monkeypatch):
    """time.time() for the cache modules, advanced by hand."""
    now = [time.time()]
    fake = type("FakeTime", (), {"time": staticmethod(lambda: now[0])})
    monkeypatch.setattr(disk_cache_module, "time", fake)
    import src.cache_manager as cache_manager_module
    monkeypatch.setattr(cache_manager_module, "time", fake)
    return now


@pytest.fixture
def cm(tmp_path):
    with patch('src.cache_manager.CacheManager._get_writable_cache_dir',
               return_value=str(tmp_path)):
        manager = CacheManager()
    manager.stop_cleanup_thread()
    yield manager


def _record(ts, data=None, ttl=None):
    """A record laid out the way CacheManager.set writes it."""
    rec = {"timestamp": ts}
    if ttl is not None:
        rec["ttl"] = ttl
    rec["data"] = data if data is not None else {"games": [1, 2, 3]}
    return rec


class TestTheWriteIsSkipped:
    def test_repeated_identical_set_writes_once(self, cm, writes):
        path = cm._get_cache_path("scores")
        for _ in range(20):
            cm.set("scores", {"games": [1, 2, 3]}, ttl=60)
        assert len(writes) == 1
        # The data is the same and the file is the same file.
        assert cm.get("scores", max_age=60, memory_ttl=0) == {"games": [1, 2, 3]}
        assert os.stat(path).st_nlink == 1

    def test_the_file_is_not_replaced(self, tmp_path, clock):
        disk = DiskCache(str(tmp_path))
        disk.set("k", _record(clock[0]))
        before = os.stat(disk.get_cache_path("k"))
        clock[0] += 30
        disk.set("k", _record(clock[0]))
        after = os.stat(disk.get_cache_path("k"))
        assert after.st_ino == before.st_ino
        assert after.st_mtime == pytest.approx(clock[0], abs=1e-3)

    def test_changed_data_rewrites(self, cm, writes):
        cm.set("scores", {"games": [1]})
        cm.set("scores", {"games": [2]})
        assert len(writes) == 2
        assert cm.get("scores", memory_ttl=0) == {"games": [2]}

    def test_a_changed_ttl_rewrites(self, cm, writes):
        cm.set("scores", {"games": [1]}, ttl=60)
        cm.set("scores", {"games": [1]}, ttl=600)
        cm.set("scores", {"games": [1]})
        assert len(writes) == 3

    def test_a_timestamp_going_backwards_rewrites(self, tmp_path, writes, clock):
        disk = DiskCache(str(tmp_path))
        disk.set("k", _record(clock[0]))
        disk.set("k", _record(clock[0] - 100))
        assert len(writes) == 2
        assert disk.get("k", max_age=None)["timestamp"] == pytest.approx(clock[0] - 100)

    def test_unchanged_data_is_still_rewritten_once_the_lift_runs_out(
            self, tmp_path, writes, clock):
        disk = DiskCache(str(tmp_path))
        start = clock[0]
        disk.set("k", _record(start))
        clock[0] = start + _MAX_TIMESTAMP_LIFT - 1
        disk.set("k", _record(clock[0]))
        assert len(writes) == 1
        clock[0] = start + _MAX_TIMESTAMP_LIFT + 1
        disk.set("k", _record(clock[0]))
        assert len(writes) == 2
        # ...and the embedded timestamp caught up.
        with open(disk.get_cache_path("k"), "rb") as f:
            assert disk_cache_module._loads(f.read())["timestamp"] == clock[0]

    def test_another_writer_replacing_the_file_forces_a_rewrite(self, tmp_path, writes):
        mine, theirs = DiskCache(str(tmp_path)), DiskCache(str(tmp_path))
        now = time.time()
        mine.set("k", _record(now, {"v": "mine"}))
        theirs.set("k", _record(now + 1, {"v": "theirs"}))
        mine.set("k", _record(now + 2, {"v": "mine"}))
        assert len(writes) == 3
        assert mine.get("k", max_age=None)["data"] == {"v": "mine"}

    def test_a_record_without_a_timestamp_still_skips(self, tmp_path, writes):
        disk = DiskCache(str(tmp_path))
        disk.set("k", {"plain": True})
        disk.set("k", {"plain": True})
        assert len(writes) == 1


class TestAgeAfterSkippedWrites:
    def test_a_skipped_save_keeps_the_record_fresh(self, tmp_path, clock):
        disk = DiskCache(str(tmp_path))
        disk.set("k", _record(clock[0], ttl=60))
        for _ in range(10):          # ten minutes of unchanged 50 s re-saves
            clock[0] += 50
            disk.set("k", _record(clock[0], ttl=60))
        clock[0] += 50
        record = disk.get("k", max_age=300)
        assert record is not None
        # Handed back as a rewrite would have left it.
        assert record["timestamp"] == pytest.approx(clock[0] - 50, abs=1e-3)

    def test_and_it_expires_on_time_once_the_saves_stop(self, tmp_path, clock, monkeypatch):
        disk = DiskCache(str(tmp_path))
        disk.set("k", _record(clock[0]))
        clock[0] += 200
        disk.set("k", _record(clock[0]))
        clock[0] += 59
        assert disk.get("k", max_age=60) is not None
        clock[0] += 2
        parses = []
        real = disk_cache_module._loads
        monkeypatch.setattr(disk_cache_module, "_loads",
                            lambda raw: parses.append(1) or real(raw))
        assert disk.get("k", max_age=60) is None
        assert parses == []      # still decided from the header

    def test_the_ttl_is_honoured_the_same_way(self, tmp_path, clock):
        disk = DiskCache(str(tmp_path))
        disk.set("k", _record(clock[0], ttl=30))
        clock[0] += 100
        disk.set("k", _record(clock[0], ttl=30))
        clock[0] += 20
        assert disk.get("k", max_age=5) is not None     # ttl wins, 20 < 30
        clock[0] += 20
        assert disk.get("k", max_age=3600) is None      # 40 > 30

    def test_a_restart_sees_the_newest_save(self, tmp_path, clock, writes):
        disk = DiskCache(str(tmp_path))
        disk.set("k", _record(clock[0]))
        clock[0] += 250
        disk.set("k", _record(clock[0]))
        restarted = DiskCache(str(tmp_path))     # empty digest map
        assert restarted.get("k", max_age=60) is not None
        # It rewrites once (it cannot know what is on disk), then skips.
        clock[0] += 10
        restarted.set("k", _record(clock[0]))
        clock[0] += 10
        restarted.set("k", _record(clock[0]))
        assert len(writes) == 2

    def test_the_cache_manager_reads_it_across_processes(self, cm, tmp_path, clock):
        cm.set("display_state", {"mode": "clock"})
        clock[0] += 100
        cm.set("display_state", {"mode": "clock"})
        # The web interface: its own manager, memory tier bypassed.
        with patch('src.cache_manager.CacheManager._get_writable_cache_dir',
                   return_value=str(tmp_path)):
            web = CacheManager()
        web.stop_cleanup_thread()
        clock[0] += 60
        assert web.get("display_state", max_age=120, memory_ttl=0) == {"mode": "clock"}
        clock[0] += 70
        assert web.get("display_state", max_age=120, memory_ttl=0) is None

    def test_retention_sees_the_newest_save(self, cm, clock):
        cm.set("odds_x", {"line": 1})
        path = cm._get_cache_path("odds_x")
        clock[0] += 3000
        cm.set("odds_x", {"line": 1})
        assert os.path.getmtime(path) == pytest.approx(clock[0], abs=1e-3)


class TestFreshnessCannotBeBorrowed:
    def test_a_record_written_with_an_old_timestamp_reads_old(self, tmp_path):
        disk = DiskCache(str(tmp_path))
        old = time.time() - 600
        disk.set("k", _record(old))
        assert os.path.getmtime(disk.get_cache_path("k")) == pytest.approx(old, abs=1e-3)
        assert disk.get("k", max_age=300) is None

    def test_a_copy_without_mtime_is_bounded(self, tmp_path):
        disk = DiskCache(str(tmp_path))
        day_old = time.time() - 86400
        disk.set("k", _record(day_old))
        copy_dir = tmp_path / "restored"
        copy_dir.mkdir()
        shutil.copyfile(disk.get_cache_path("k"), copy_dir / "k.json")  # mtime = now
        restored = DiskCache(str(copy_dir))
        assert restored.get("k", max_age=300) is None
        record = restored.get("k", max_age=None)
        assert record["timestamp"] == pytest.approx(day_old + _MAX_TIMESTAMP_LIFT)

    def test_effective_timestamp(self):
        assert _effective_timestamp(1000.0, None) == 1000.0
        assert _effective_timestamp(1000.0, 900.0) == 1000.0       # mtime older
        assert _effective_timestamp(1000.0, 1500.0) == 1500.0      # a skipped save
        assert _effective_timestamp(1000.0, 10 ** 9) == 1000.0 + _MAX_TIMESTAMP_LIFT
