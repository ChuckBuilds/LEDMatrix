"""Tests for asynchronous plugin updates (plugin_manager background worker).

The invariants that keep this change safe:
1. run_scheduled_updates returns immediately — a slow update() can never
   again freeze the render loop (the original defect: 30s scroll freezes).
2. A plugin's update() and display() are NEVER concurrent — the per-plugin
   lock makes the old implicit no-overlap guarantee explicit, and it stays
   held through PluginExecutor's own timeout: a lingering, still-running
   update() keeps the lock even after PluginExecutor gives up waiting on it.
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

    def test_lock_held_through_timeout_until_real_update_finishes(self, pm):
        """PluginExecutor's join(timeout) can return before the real
        update() call does -- the lingering daemon thread keeps running it.
        The plugin's lock must stay held for that whole real duration, not
        just PluginExecutor's bounded wait, or display() could run
        concurrently with a still-executing update()."""
        plugin = SlowPlugin(update_seconds=0.3)
        plugin_id = _install(pm, plugin)
        pm.plugin_executor.default_timeout = 0.05  # times out well before
                                                    # update_seconds elapses

        pm.run_scheduled_updates()

        deadline = time.monotonic() + 2
        while not plugin.in_update and time.monotonic() < deadline:
            time.sleep(0.01)
        assert plugin.in_update is True

        # PluginExecutor's own timeout has now elapsed, but the real
        # update() (0.3s) is still running in its lingering daemon thread.
        time.sleep(0.15)
        assert plugin.in_update is True, "test setup: update should still be running"
        lock = pm.get_plugin_lock(plugin_id)
        assert lock.acquire(blocking=False) is False, \
            "lock must stay held through PluginExecutor's timeout while the real update() runs"

        # Once the real update() genuinely finishes, the lock is released.
        deadline = time.monotonic() + 2
        while plugin.in_update and time.monotonic() < deadline:
            time.sleep(0.02)
        time.sleep(0.1)
        assert lock.acquire(blocking=False) is True
        lock.release()

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
        """Exercise the public unload_plugin() lifecycle rather than
        deleting pm.plugins directly: queue the target's update behind a
        deterministic blocker (occupying the single worker), unload the
        target while its item still sits queued, then release the blocker
        and confirm the target's update never ran and its state stayed
        unloaded rather than being resurrected to ENABLED."""
        blocker_event = threading.Event()

        class BlockerPlugin(SlowPlugin):
            def update(self):
                self.update_calls += 1
                blocker_event.wait(timeout=5)
                return True

        blocker_id = _install(pm, BlockerPlugin(), plugin_id="blocker-plugin")
        target = SlowPlugin(update_seconds=0.05)
        target_id = _install(pm, target, plugin_id="slow-plugin")

        # Dispatch the blocker first so it occupies the single worker
        # thread, then enqueue the target behind it -- deterministically
        # queued, not yet started.
        pm._enqueue_update(blocker_id, time.time())
        deadline = time.monotonic() + 2
        while pm.plugins[blocker_id].update_calls == 0 and time.monotonic() < deadline:
            time.sleep(0.01)
        assert pm.plugins[blocker_id].update_calls == 1

        pm._enqueue_update(target_id, time.time())
        assert target_id in pm._pending_updates

        assert pm.unload_plugin(target_id) is True
        assert target_id not in pm.plugins

        blocker_event.set()  # let the blocker finish; worker moves on to
                              # the target's queued item

        deadline = time.monotonic() + 3
        while target_id in pm._pending_updates and time.monotonic() < deadline:
            time.sleep(0.02)

        assert target.update_calls == 0, "update() must not run for an unloaded plugin"
        assert target_id not in pm._pending_updates
        assert pm.state_manager.get_state(target_id) == PluginState.UNLOADED


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
