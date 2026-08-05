"""Plugin update scheduling must be atomic (issue #401).

`run_scheduled_updates()` decided whether to update a plugin with a
check-then-act sequence: `can_execute()` and `set_state(RUNNING)` were separate
calls with nothing between them, so two scheduler threads could both observe
ENABLED and both go on to call the same plugin's `update()`.

Two schedulers really do run at once. The render loop calls
`_tick_plugin_updates()`, and Vegas mode fires its own `vegas-plugin-tick`
daemon thread every few seconds; that thread is never joined when
`VegasModeCoordinator.play()` returns, so a slow `update()` still in flight can
overlap the next tick from the main loop.

A plugin whose `update()` runs twice at once is unsafe unless it happens to be
reentrant — shared mutable state, a non-thread-safe HTTP session or cache all
break. These tests pin the reservation that closes it, and the state
bookkeeping that has to survive it: a plugin reserved but never dispatched must
not be stranded in RUNNING, because `can_execute()` would then refuse it
forever.
"""

import os
import sys
import threading
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.plugin_system.plugin_manager import PluginManager  # noqa: E402
from src.plugin_system.plugin_state import PluginState  # noqa: E402


class OverlapDetectingPlugin:
    """Records the high-water mark of concurrent update() calls."""

    def __init__(self, update_seconds=0.25):
        self.enabled = True
        self.update_seconds = update_seconds
        self.update_calls = 0
        self.max_concurrent = 0
        self._active = 0
        self._guard = threading.Lock()

    def update(self):
        with self._guard:
            self._active += 1
            self.update_calls += 1
            self.max_concurrent = max(self.max_concurrent, self._active)
        try:
            time.sleep(self.update_seconds)
        finally:
            with self._guard:
                self._active -= 1
        return True

    def display(self, force_clear=False):
        return True


@pytest.fixture
def pm(tmp_path):
    manager = PluginManager(plugins_dir=str(tmp_path), config_manager=None,
                            display_manager=None, cache_manager=None)
    yield manager
    manager.stop_update_worker()


def _install(pm, plugin, plugin_id="racy-plugin", interval=0.01):
    pm.plugins[plugin_id] = plugin
    pm._update_interval_cache[plugin_id] = interval
    pm.state_manager.set_state(plugin_id, PluginState.ENABLED)
    return plugin_id


def _widen_check_then_act_window(pm, delay=0.02):
    """Hold every scheduler thread inside the eligibility check at once.

    The real gap between can_execute() and the RUNNING transition is a couple
    of bytecodes wide, so a plain thread race under the GIL almost never lands
    in it — an unfixed scheduler looks correct in a test that just hammers it.
    Delaying the check reproduces the interleaving that Vegas's tick thread and
    the render loop actually produce when a slow update() overlaps the next
    tick, and it is what makes these tests fail without the reservation.

    Once the check and the transition are under one lock, the delay only
    serializes the schedulers: the losers observe RUNNING and back off.
    """
    real_can_execute = pm.state_manager.can_execute

    def slow_can_execute(plugin_id):
        result = real_can_execute(plugin_id)
        time.sleep(delay)
        return result

    pm.state_manager.can_execute = slow_can_execute


def _hammer(target, threads=8, rounds=1):
    """Run `target` on N threads released simultaneously by a barrier."""
    errors = []
    barrier = threading.Barrier(threads)

    def runner():
        try:
            barrier.wait(timeout=5)
            for _ in range(rounds):
                target()
        except Exception as exc:  # noqa: BLE001 - surfaced by the assertion
            errors.append(exc)

    workers = [threading.Thread(target=runner) for _ in range(threads)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=30)
    assert not errors, f"worker raised: {errors[0]!r}"


class TestReservationAtomicity:
    """The check-and-claim itself, independent of any dispatch path."""

    def test_only_one_caller_wins_the_reservation(self, pm):
        plugin_id = _install(pm, OverlapDetectingPlugin())
        _widen_check_then_act_window(pm)
        wins = []
        lock = threading.Lock()

        def claim():
            if pm._reserve_for_update(plugin_id):
                with lock:
                    wins.append(threading.current_thread().name)

        _hammer(claim, threads=16)
        assert len(wins) == 1, (
            f"{len(wins)} threads reserved the same plugin concurrently; "
            "the eligibility check and the RUNNING transition are not atomic")
        assert pm.state_manager.get_state(plugin_id) == PluginState.RUNNING

    def test_reservation_refused_while_running(self, pm):
        plugin_id = _install(pm, OverlapDetectingPlugin())
        assert pm._reserve_for_update(plugin_id) is True
        assert pm._reserve_for_update(plugin_id) is False

    def test_reservation_can_be_handed_back(self, pm):
        plugin_id = _install(pm, OverlapDetectingPlugin())
        assert pm._reserve_for_update(plugin_id) is True
        pm._release_reservation(plugin_id)
        assert pm.state_manager.get_state(plugin_id) == PluginState.ENABLED
        assert pm._reserve_for_update(plugin_id) is True, \
            "a released reservation must be claimable again"

    def test_due_check_is_inside_the_reservation(self, pm):
        """Two threads that both decided 'due' must not both get a turn.

        With the due check outside the lock, the loser of the race could claim
        the plugin the moment the winner finished, running update() twice
        inside one interval.
        """
        plugin_id = _install(pm, OverlapDetectingPlugin(), interval=60.0)
        now = time.time()
        assert pm._reserve_for_update(plugin_id, now, 60.0) is True
        pm.plugin_last_update[plugin_id] = now
        pm._release_reservation(plugin_id)
        assert pm._reserve_for_update(plugin_id, now, 60.0) is False, \
            "plugin updated just now must not be due again"


class TestNoConcurrentUpdate:
    """The end-to-end invariant the issue is actually about."""

    def test_synchronous_path_never_overlaps(self, pm):
        """The kill-switch path ran update() inline with no dedup at all."""
        pm._synchronous_updates = True
        plugin = OverlapDetectingPlugin(update_seconds=0.25)
        _install(pm, plugin)
        _widen_check_then_act_window(pm)

        _hammer(pm.run_scheduled_updates, threads=8)

        assert plugin.max_concurrent == 1, (
            f"update() ran {plugin.max_concurrent}x concurrently on the "
            "synchronous path")

    def test_update_all_plugins_never_overlaps(self, pm):
        plugin = OverlapDetectingPlugin(update_seconds=0.25)
        _install(pm, plugin)
        _widen_check_then_act_window(pm)

        _hammer(pm.update_all_plugins, threads=8)

        assert plugin.max_concurrent == 1, (
            f"update() ran {plugin.max_concurrent}x concurrently via "
            "update_all_plugins()")

    def test_async_path_never_overlaps(self, pm):
        plugin = OverlapDetectingPlugin(update_seconds=0.2)
        plugin_id = _install(pm, plugin)

        _hammer(pm.run_scheduled_updates, threads=8, rounds=3)

        # Wait for an update to have both started and finished. Polling only
        # `_active` races the worker: before it picks the item up nothing is
        # active yet, so the loop would fall straight through and assert on a
        # plugin that never ran.
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if plugin.update_calls >= 1 and plugin._active == 0:
                break
            time.sleep(0.05)

        assert plugin.update_calls >= 1, "no update ran on the async path"
        assert plugin.max_concurrent == 1, (
            f"update() ran {plugin.max_concurrent}x concurrently on the "
            "async path")
        assert plugin_id in pm.plugins


class TestNoStrandedState:
    """A reservation that is never dispatched must not wedge the plugin."""

    def test_plugin_returns_to_enabled_after_async_updates(self, pm):
        plugin = OverlapDetectingPlugin(update_seconds=0.1)
        plugin_id = _install(pm, plugin)

        _hammer(pm.run_scheduled_updates, threads=6, rounds=2)

        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if (plugin.update_calls >= 1
                    and pm.state_manager.get_state(plugin_id) == PluginState.ENABLED
                    and not pm._pending_updates):
                break
            time.sleep(0.05)

        # ENABLED is also the starting state, so without this the assertion
        # below would pass on a plugin that never got scheduled at all.
        assert plugin.update_calls >= 1, "no update ran; the state assertion would be vacuous"
        assert pm.state_manager.get_state(plugin_id) == PluginState.ENABLED, \
            "plugin stranded in RUNNING; can_execute() would refuse it forever"
        assert not pm._pending_updates, "pending set not drained"

    def test_pending_cleared_before_state_reset(self, pm):
        """Invariant: never-RUNNING implies never-pending.

        _finish() used to clear the pending entry *after* flipping the state
        back to ENABLED. In that window a scheduler could reserve the plugin
        and then have its enqueue dropped by the pending-dedup.
        """
        plugin = OverlapDetectingPlugin(update_seconds=0.05)
        plugin_id = _install(pm, plugin)

        violations = []
        stop = threading.Event()

        def watcher():
            while not stop.is_set():
                state = pm.state_manager.get_state(plugin_id)
                if state != PluginState.RUNNING and plugin_id in pm._pending_updates:
                    violations.append(state)
                time.sleep(0.001)

        thread = threading.Thread(target=watcher, daemon=True)
        thread.start()
        try:
            for _ in range(15):
                pm.run_scheduled_updates()
                time.sleep(0.05)
        finally:
            stop.set()
            thread.join(timeout=5)

        assert not violations, (
            f"{len(violations)} sample(s) saw a non-RUNNING plugin still in "
            "_pending_updates")


class TestDispatchFailure:
    """A reservation must survive the dispatch itself failing.

    _enqueue_update() claims the plugin, adds it to the pending set, and only
    then starts the worker and queues the item. threading.Thread.start() raises
    RuntimeError when the OS refuses a new thread — not hypothetical on a Pi
    under memory or thread pressure. Nothing is queued to release the plugin at
    that point, so without an explicit rollback it stays RUNNING with a stale
    pending entry and can_execute() refuses it for the rest of the process.
    """

    def test_reservation_released_when_the_worker_cannot_start(self, pm):
        plugin_id = _install(pm, OverlapDetectingPlugin())

        def refuse_to_start():
            raise RuntimeError("can't start new thread")

        pm._ensure_update_worker = refuse_to_start

        assert pm._reserve_for_update(plugin_id) is True
        pm._enqueue_update(plugin_id, time.time())

        assert pm.state_manager.get_state(plugin_id) == PluginState.ENABLED, \
            "plugin left in RUNNING after a failed dispatch; can_execute() " \
            "would refuse it forever"
        assert plugin_id not in pm._pending_updates, \
            "stale pending entry would make the next enqueue hit the dedup"
        assert pm._reserve_for_update(plugin_id) is True, \
            "plugin must be claimable again on the next tick"

    def test_dispatch_failure_does_not_abort_the_rest_of_the_tick(self, pm):
        """One plugin failing to queue must not skip the others in that tick."""
        first = OverlapDetectingPlugin()
        second = OverlapDetectingPlugin()
        _install(pm, first, plugin_id="plugin-a")
        _install(pm, second, plugin_id="plugin-b")

        calls = []
        real_ensure = pm._ensure_update_worker

        def fail_first_only():
            calls.append(1)
            if len(calls) == 1:
                raise RuntimeError("can't start new thread")
            return real_ensure()

        pm._ensure_update_worker = fail_first_only

        pm.run_scheduled_updates()  # must not propagate the RuntimeError

        assert len(calls) == 2, (
            "run_scheduled_updates() stopped after the failing plugin; the "
            "exception escaped the enqueue")
        for plugin_id in ("plugin-a", "plugin-b"):
            assert pm.state_manager.get_state(plugin_id) in (
                PluginState.ENABLED, PluginState.RUNNING), \
                f"{plugin_id} left in an unexpected state"
