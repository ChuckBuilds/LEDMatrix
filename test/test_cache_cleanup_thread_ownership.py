"""Tests that one cache directory gets one cleanup thread per process.

The sweep lists a directory and deletes from it, so a second thread over the
same directory only duplicates the scan. Nothing enforced that: every
CacheManager started its own, and since the loop closes over `self`, a
discarded manager could never be collected -- its thread stayed alive and
re-scanned the same directory every 24 hours for the life of the process.

On the dev rig a display process carried three, for one cache directory:

    14:22:59.954  display_controller       (the real one)
    14:22:59.973  startup validation, run 1  (discarded)
    14:23:01.055  startup validation, run 2  (discarded)

Startup validation runs twice and built a throwaway manager each time, purely
to read a directory path.
"""

import threading

import pytest

from src.cache_manager import CacheManager


@pytest.fixture(autouse=True)
def _clean_registry():
    CacheManager._cleanup_owners.clear()
    yield
    for owner in list(CacheManager._cleanup_owners.values()):
        owner.stop_cleanup_thread()
    CacheManager._cleanup_owners.clear()


def _live_cleanup_threads():
    return [t for t in threading.enumerate()
            if t.name == 'DiskCacheCleanup' and t.is_alive()]


@pytest.fixture
def manager(tmp_path, monkeypatch):
    """A CacheManager pinned to a temp dir, so tests never touch the real one."""
    monkeypatch.setattr(CacheManager, '_get_writable_cache_dir',
                        lambda self: str(tmp_path))
    return CacheManager


class TestOneThreadPerDirectory:
    def test_a_single_manager_starts_one(self, manager):
        before = len(_live_cleanup_threads())
        m = manager()
        try:
            assert len(_live_cleanup_threads()) == before + 1
        finally:
            m.stop_cleanup_thread()

    def test_three_managers_still_start_one(self, manager):
        # Exactly the rig's shape: the real manager plus two throwaways.
        before = len(_live_cleanup_threads())
        managers = [manager() for _ in range(3)]
        try:
            assert len(_live_cleanup_threads()) == before + 1
        finally:
            for m in managers:
                m.stop_cleanup_thread()

    def test_the_first_one_owns_it(self, manager):
        first, second = manager(), manager()
        try:
            assert CacheManager._cleanup_owners[first.cache_dir] is first
            assert second._cleanup_thread is None
        finally:
            first.stop_cleanup_thread()
            second.stop_cleanup_thread()

    def test_the_survivor_can_take_over(self, manager):
        first = manager()
        first.stop_cleanup_thread()
        assert not _live_cleanup_threads()

        second = manager()
        try:
            # Ownership was released, so the directory is swept again rather
            # than being left permanently unclaimed by a dead owner.
            assert len(_live_cleanup_threads()) == 1
            assert CacheManager._cleanup_owners[second.cache_dir] is second
        finally:
            second.stop_cleanup_thread()

    def test_stopping_a_non_owner_does_not_unclaim_the_directory(self, manager):
        first, second = manager(), manager()
        try:
            second.stop_cleanup_thread()      # never owned it
            assert CacheManager._cleanup_owners[first.cache_dir] is first
            assert len(_live_cleanup_threads()) == 1
        finally:
            first.stop_cleanup_thread()

    def test_separate_directories_get_separate_threads(self, tmp_path, monkeypatch):
        a, b = tmp_path / 'a', tmp_path / 'b'
        a.mkdir()
        b.mkdir()
        dirs = iter([str(a), str(b)])
        monkeypatch.setattr(CacheManager, '_get_writable_cache_dir',
                            lambda self: next(dirs))
        first, second = CacheManager(), CacheManager()
        try:
            assert first.cache_dir != second.cache_dir
            assert len(_live_cleanup_threads()) == 2
        finally:
            first.stop_cleanup_thread()
            second.stop_cleanup_thread()

    def test_no_thread_leaks_across_many_constructions(self, manager):
        before = len(_live_cleanup_threads())
        made = [manager() for _ in range(12)]
        try:
            assert len(_live_cleanup_threads()) == before + 1
        finally:
            for m in made:
                m.stop_cleanup_thread()
        assert len(_live_cleanup_threads()) == before


class TestValidatorDoesNotBuildItsOwn:
    def test_it_uses_the_cache_manager_it_is_given(self, manager):
        from src.startup_validator import StartupValidator

        shared = manager()
        try:
            before = len(_live_cleanup_threads())
            v = StartupValidator(config_manager=object(), cache_manager=shared)
            v._validate_cache_directory()
            assert len(_live_cleanup_threads()) == before, (
                "validation started another cleanup thread")
        finally:
            shared.stop_cleanup_thread()

    def test_without_one_it_cleans_up_after_itself(self, manager):
        from src.startup_validator import StartupValidator

        before = len(_live_cleanup_threads())
        v = StartupValidator(config_manager=object())
        v._validate_cache_directory()
        assert len(_live_cleanup_threads()) == before, (
            "the fallback manager left its cleanup thread running")
