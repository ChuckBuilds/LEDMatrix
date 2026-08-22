"""Tests for PluginManager.discovered_plugin_ids().

The config-watcher thread needs the set of discovered plugin ids while the
render thread may be rebuilding plugin_manifests. Iterating that dict directly
can observe a half-populated mapping or raise "dictionary changed size during
iteration", so the accessor snapshots it under the discovery lock.
"""

import tempfile
import threading
from pathlib import Path

import pytest

from src.plugin_system.plugin_manager import PluginManager


@pytest.fixture
def pm():
    with tempfile.TemporaryDirectory() as tmp:
        yield PluginManager(plugins_dir=str(Path(tmp) / "plugins"))


def test_returns_the_discovered_ids(pm):
    pm.plugin_manifests = {"clock-simple": {}, "hockey-scoreboard": {}}
    assert pm.discovered_plugin_ids() == {"clock-simple", "hockey-scoreboard"}


def test_empty_when_nothing_discovered(pm):
    pm.plugin_manifests = {}
    assert pm.discovered_plugin_ids() == set()


def test_is_a_snapshot_not_a_live_view(pm):
    """The caller iterates the result on another thread; it must not alias
    the mapping discovery is still writing to."""
    pm.plugin_manifests = {"clock-simple": {}}
    snapshot = pm.discovered_plugin_ids()
    pm.plugin_manifests["hockey-scoreboard"] = {}
    assert snapshot == {"clock-simple"}


def test_takes_the_discovery_lock(pm):
    """Guards against the lock being dropped in a later refactor: with the
    lock held by another thread the call must block rather than read."""
    pm.plugin_manifests = {"clock-simple": {}}
    finished = threading.Event()

    def call():
        pm.discovered_plugin_ids()
        finished.set()

    pm._discovery_lock.acquire()
    try:
        # RLock is reentrant per-thread, so use a *different* thread to prove
        # the accessor actually waits on it.
        t = threading.Thread(target=call, daemon=True)
        t.start()
        assert not finished.wait(timeout=0.3), "accessor did not take the discovery lock"
    finally:
        pm._discovery_lock.release()
    t.join(timeout=2)
    assert finished.is_set()
