"""Tests for asynchronous plugin updates (plugin_manager background worker).

The invariants that keep this change safe:
1. run_scheduled_updates returns immediately — a slow update() can never
   again freeze the render loop (the original defect: 30s scroll freezes).
2. A plugin's update() and display() are NEVER concurrent — the per-plugin
   lock makes the old implicit no-overlap guarantee explicit (and, unlike
   before, holds it across the post-timeout window too).
3. Failure/timeout bookkeeping is unchanged (same executor, same
   _record_update_failure path, same last-update stamping).
4. The kill switch (plugin_system.synchronous_updates) restores the
   inline path exactly.
"""

import os
import sys
import threading
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.plugin_system.plugin_manager import PluginManager  # noqa: E402
from src.plugin_system.plugin_state import PluginState  # noqa: E402


class SlowPlugin:
    """Fake plugin whose update() sleeps and records overlap violations."""

    def __init__(self, update_seconds=0.5):
        self.enabled = True
        self.update_seconds = update_seconds
        self.update_calls = 0
        self.display_calls = 0
        self.in_update = False
        self.overlap_detected = False

    def update(self):
        self.in_update = True
        self.update_calls += 1
        time.sleep(self.update_seconds)
        self.in_update = False
        return True

    def display(self, force_clear=False):
        if self.in_update:
            self.overlap_detected = True
        self.display_calls += 1
        return True


@pytest.fixture
def pm(tmp_path):
    manager = PluginManager(plugins_dir=str(tmp_path), config_manager=None,
                            display_manager=None, cache_manager=None)
    yield manager
    manager.stop_update_worker()


def _install(pm, plugin, plugin_id="slow-plugin"):
    pm.plugins[plugin_id] = plugin
    pm._update_interval_cache[plugin_id] = 0.01  # always due
    # load_plugin normally registers state; can_execute() gates on it
    pm.state_manager.set_state(plugin_id, PluginState.ENABLED)
    return plugin_id


class TestSchedulerNonBlocking:
    def test_run_scheduled_updates_returns_immediately(self, pm):
        plugin_id = _install(pm, SlowPlugin(update_seconds=2.0))
        start = time.monotonic()
        pm.run_scheduled_updates()
        elapsed = time.monotonic() - start
        assert elapsed < 0.1, f"scheduler blocked for {elapsed:.2f}s"
        # the update actually runs in the background
        deadline = time.monotonic() + 5
        while pm.plugins[plugin_id].update_calls == 0 and time.monotonic() < deadline:
            time.sleep(0.05)
        assert pm.plugins[plugin_id].update_calls == 1

    def test_no_double_enqueue_while_pending(self, pm):
        plugin_id = _install(pm, SlowPlugin(update_seconds=0.8))
        for _ in range(20):
            pm.run_scheduled_updates()
            time.sleep(0.01)
        time.sleep(1.5)  # let the single queued update finish
        assert pm.plugins[plugin_id].update_calls == 1


class TestUpdateDisplayExclusion:
    def test_display_lock_held_during_update(self, pm):
        plugin_id = _install(pm, SlowPlugin(update_seconds=0.6))
        pm.run_scheduled_updates()
        # give the worker a moment to take the lock and enter update()
        deadline = time.monotonic() + 2
        while not pm.plugins[plugin_id].in_update and time.monotonic() < deadline:
            time.sleep(0.01)
        lock = pm.get_plugin_lock(plugin_id)
        assert lock.acquire(blocking=False) is False, \
            "lock must be held while update() runs"
        # and released afterwards
        deadline = time.monotonic() + 3
        while pm.plugins[plugin_id].in_update and time.monotonic() < deadline:
            time.sleep(0.05)
        time.sleep(0.1)
        assert lock.acquire(blocking=False) is True
        lock.release()

    def test_no_overlap_under_hammering_display_loop(self, pm):
        """Simulate the render loop's try-lock display pattern at high rate
        while updates fire — the plugin itself asserts no overlap."""
        plugin = SlowPlugin(update_seconds=0.15)
        plugin_id = _install(pm, plugin)
        stop = threading.Event()

        def render_loop():
            while not stop.is_set():
                lock = pm.get_plugin_lock(plugin_id)
                if lock.acquire(blocking=False):
                    try:
                        plugin.display()
                    finally:
                        lock.release()
                time.sleep(0.002)

        renderer = threading.Thread(target=render_loop, daemon=True)
        renderer.start()
        try:
            for _ in range(6):
                pm.plugin_last_update.pop(plugin_id, None)  # force due
                pm.run_scheduled_updates()
                time.sleep(0.3)
        finally:
            stop.set()
            renderer.join(timeout=2)
        assert plugin.update_calls >= 3
        assert plugin.display_calls > 10
        assert plugin.overlap_detected is False

    def test_state_returns_to_enabled_after_update(self, pm):
        """RUNNING is set at enqueue (blocks re-entry via can_execute) and
        must return to an executable state once the update finishes."""
        plugin_id = _install(pm, SlowPlugin(update_seconds=0.1))
        pm.run_scheduled_updates()
        # while queued/running, re-entry is blocked
        assert pm.state_manager.can_execute(plugin_id) is False
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            if (pm.plugins[plugin_id].update_calls
                    and pm.state_manager.can_execute(plugin_id)):
                break
            time.sleep(0.05)
        assert pm.plugins[plugin_id].update_calls == 1
        assert pm.state_manager.can_execute(plugin_id) is True


class TestFailurePaths:
    def test_update_failure_routes_through_failure_bookkeeping(self, pm):
        class FailingPlugin(SlowPlugin):
            def update(self):
                self.update_calls += 1
                raise RuntimeError("boom")

        plugin_id = _install(pm, FailingPlugin())
        pm.run_scheduled_updates()
        deadline = time.monotonic() + 3
        while pm.plugins[plugin_id].update_calls == 0 and time.monotonic() < deadline:
            time.sleep(0.05)
        time.sleep(0.2)
        # failure stamped so the interval gate holds (no hot retry loop)
        assert pm.plugin_last_update.get(plugin_id, 0) > 0
        # lock released after failure
        assert pm.get_plugin_lock(plugin_id).acquire(blocking=False) is True
        pm.get_plugin_lock(plugin_id).release()

    def test_unloaded_while_queued_is_harmless(self, pm):
        plugin_id = _install(pm, SlowPlugin(update_seconds=0.1))
        pm._enqueue_update(plugin_id, time.time())
        del pm.plugins[plugin_id]
        time.sleep(0.3)  # worker drains the item without crashing
        assert pm._update_worker is None or pm._update_worker.is_alive()


class TestKillSwitch:
    def test_synchronous_mode_blocks_like_before(self, pm):
        pm._synchronous_updates = True
        plugin_id = _install(pm, SlowPlugin(update_seconds=0.4))
        start = time.monotonic()
        pm.run_scheduled_updates()
        elapsed = time.monotonic() - start
        assert elapsed >= 0.4, "synchronous mode must run inline"
        assert pm.plugins[plugin_id].update_calls == 1


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
