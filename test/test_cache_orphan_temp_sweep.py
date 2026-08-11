"""Tests that abandoned cache temp files get collected.

DiskCache.set() writes through tempfile.mkstemp and os.replace, removing its
own temp file in a finally. That covers a failed write, but not a process that
dies between the two -- a SIGKILL, a lost restart race, a power cut, all
ordinary on a Pi. Nothing collected what was left behind: the temp names are
".<key>.json.<random>", and the expiry sweep only listed names ending in
.json, so they accumulated for as long as the card had been in service.

Measured on a live rig before this fix: 76 orphans totalling 1,050 MB -- 81%
of the entire cache directory -- the oldest six months old.

The predicate that decides what to delete is tested harder than the sweep
itself, because a false positive here destroys real data.
"""

import os
import time

import pytest

from src.cache.disk_cache import DiskCache, _ORPHAN_TEMP_MAX_AGE_SECONDS


class FakeStrategy:
    @staticmethod
    def get_data_type_from_key(key):
        return 'default'


POLICIES = {'default': 30}


@pytest.fixture
def cache(tmp_path):
    return DiskCache(str(tmp_path))


def _age(path, seconds):
    old = time.time() - seconds
    os.utime(path, (old, old))


def _write(tmp_path, name, body='{}'):
    p = tmp_path / name
    p.write_text(body, encoding='utf-8')
    return p


class TestWhatCountsAsAnOrphan:
    @pytest.mark.parametrize('name', [
        '.weather.json.a1b2c3d4',
        '.odds_espn_football_nfl_401.json.xyz00000',
        '.a.json.b',
    ])
    def test_our_temp_files_are_orphans(self, name):
        assert DiskCache._is_orphaned_temp(name)

    @pytest.mark.parametrize('name', [
        'weather.json',            # real data
        '.weather.json',           # a dotted key that completed
        '.gitignore',              # not ours
        '.hidden',                 # not ours
        'weather.json.bak',        # no leading dot: someone else's
        '.json.abc',               # no key between the dot and .json.
        '.weather.json.',          # no random component
        'notes.txt',
    ])
    def test_everything_else_is_left_alone(self, name):
        assert not DiskCache._is_orphaned_temp(name)

    def test_the_names_set_actually_creates_are_matched(self, cache, tmp_path):
        """Guard against the predicate and the writer drifting apart."""
        created = []
        real = os.replace

        def capture(src, dst):
            created.append(os.path.basename(src))
            return real(src, dst)

        import src.cache.disk_cache as mod
        mod.os.replace = capture
        try:
            cache.set('weather', {'v': 1})
        finally:
            mod.os.replace = real

        assert created, "set() did not go through the temp-file path"
        assert all(DiskCache._is_orphaned_temp(n) for n in created), created


class TestTheSweep:
    def test_an_old_orphan_is_removed(self, cache, tmp_path):
        p = _write(tmp_path, '.weather.json.a1b2c3d4', 'x' * 5000)
        _age(p, _ORPHAN_TEMP_MAX_AGE_SECONDS + 60)

        stats = cache.cleanup_expired_files(FakeStrategy(), POLICIES)

        assert not p.exists()
        assert stats['orphan_temp_files_deleted'] == 1
        assert stats['space_freed_bytes'] >= 5000

    def test_an_in_flight_write_is_not_snatched_away(self, cache, tmp_path):
        # The whole risk of this sweep: deleting a temp file another thread is
        # about to os.replace into place.
        p = _write(tmp_path, '.weather.json.inflight')

        cache.cleanup_expired_files(FakeStrategy(), POLICIES)

        assert p.exists()

    def test_real_cache_files_survive(self, cache, tmp_path):
        fresh = _write(tmp_path, 'weather.json')
        dotted = _write(tmp_path, '.weather.json')
        _age(dotted, _ORPHAN_TEMP_MAX_AGE_SECONDS + 60)

        cache.cleanup_expired_files(FakeStrategy(), POLICIES)

        assert fresh.exists()
        assert dotted.exists(), "a completed .json was treated as a temp file"

    def test_unrelated_dotfiles_survive(self, cache, tmp_path):
        keep = _write(tmp_path, '.gitignore')
        _age(keep, 400 * 86400)

        cache.cleanup_expired_files(FakeStrategy(), POLICIES)

        assert keep.exists()

    def test_expiry_still_works_alongside_it(self, cache, tmp_path):
        stale = _write(tmp_path, 'old.json')
        _age(stale, 40 * 86400)          # past the 30-day default
        orphan = _write(tmp_path, '.old.json.zz999999')
        _age(orphan, _ORPHAN_TEMP_MAX_AGE_SECONDS + 60)

        stats = cache.cleanup_expired_files(FakeStrategy(), POLICIES)

        assert not stale.exists()
        assert not orphan.exists()
        assert stats['files_deleted'] == 2
        assert stats['orphan_temp_files_deleted'] == 1

    def test_the_rig_scenario(self, cache, tmp_path):
        """76 orphans of assorted ages, none of them reachable before."""
        for i in range(76):
            p = _write(tmp_path, '.sched_%d.json.r%06d' % (i, i), 'x' * 1000)
            _age(p, (i + 2) * 86400)
        keep = _write(tmp_path, 'sched.json')

        stats = cache.cleanup_expired_files(FakeStrategy(), POLICIES)

        assert stats['orphan_temp_files_deleted'] == 76
        assert keep.exists()
        assert not list(tmp_path.glob('.sched_*'))
        # The summary line is "<deleted>/<scanned>", so an orphan that is
        # deleted but never counted as scanned renders as "76/1".
        assert stats['files_scanned'] == 77
        assert stats['files_deleted'] <= stats['files_scanned']

    def test_deleted_never_exceeds_scanned(self, cache, tmp_path):
        p = _write(tmp_path, '.only.json.a1b2c3d4')
        _age(p, _ORPHAN_TEMP_MAX_AGE_SECONDS + 60)

        stats = cache.cleanup_expired_files(FakeStrategy(), POLICIES)

        assert stats['files_deleted'] == 1
        assert stats['files_scanned'] == 1

    def test_a_missing_file_mid_sweep_is_not_an_error(self, cache, tmp_path):
        p = _write(tmp_path, '.weather.json.a1b2c3d4')
        _age(p, _ORPHAN_TEMP_MAX_AGE_SECONDS + 60)

        import src.cache.disk_cache as mod
        real = mod.os.path.getsize

        def vanish(path):
            if path.endswith('.a1b2c3d4'):
                os.remove(path)
                raise FileNotFoundError(path)
            return real(path)

        mod.os.path.getsize = vanish
        try:
            stats = cache.cleanup_expired_files(FakeStrategy(), POLICIES)
        finally:
            mod.os.path.getsize = real

        assert stats['errors'] == 0
