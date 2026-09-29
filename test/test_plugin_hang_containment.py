"""One hung plugin must not stop every other plugin from updating.

The update worker is a single thread. It took each plugin's lock with a
blocking ``acquire()``, and the render thread holds that same lock while it
runs the plugin's display(). A display() that never returned -- or a first
frame that outlived PluginExecutor's timeout and kept running on its lingering
thread -- parked the worker on that acquire for good, and from then on no
plugin updated at all: scores, weather and clocks all froze while the panel
kept scrolling stale data.

Now:
- the worker waits PLUGIN_LOCK_TIMEOUT at most, skips the busy plugin and
  records the skip as a hang, so repeats open that plugin's circuit breaker;
- display() calls are timed, slow ones recorded, overlong ones counted as hangs;
- on_config_change runs under the plugin's lock, deferred to the worker if the
  lock stays busy, so it never interleaves with update().

Timeouts here are fractions of a second so the suite stays fast.
"""

import copy
import threading
import time
import types
from unittest.mock import MagicMock

import pytest

from src.plugin_system.plugin_health import CircuitState, PluginHealthTracker
from src.plugin_system.plugin_manager import PluginManager
from src.plugin_system.plugin_state import PluginState


class _Cache:
    """Serialising stand-in for CacheManager; counts writes."""

    def __init__(self):
        self.store = {}
        self.writes = 0

    def set(self, key, data, ttl=None, **kwargs):
        self.writes += 1
        self.store[key] = copy.deepcopy(data)

    def get(self, key, max_age=None, memory_ttl=None, **kwargs):
        return copy.deepcopy(self.store.get(key))


class _Plugin:
    """Counts update() calls; display() can be made to block on an Event."""

    def __init__(self, plugin_id, update_seconds=0.0):
        self.plugin_id = plugin_id
        self.enabled = True
        self.update_seconds = update_seconds
        self.update_calls = 0
        self.in_update = False
        self.display_gate = None  # threading.Event: display() blocks on it
        self.config_changes = []
        self.overlap = False
        self.events = []

    def update(self):
        self.in_update = True
        self.update_calls += 1
        self.events.append('update')
        time.sleep(self.update_seconds)
        self.in_update = False

    def display(self, force_clear=False):
        if self.display_gate is not None:
            self.display_gate.wait(timeout=10)
        return True

    def on_config_change(self, new_config):
        if self.in_update:
            self.overlap = True
        self.events.append('config')
        self.config_changes.append(new_config)


@pytest.fixture
def pm(tmp_path):
    manager = PluginManager(plugins_dir=str(tmp_path), config_manager=None,
                            display_manager=None, cache_manager=None)
    manager.PLUGIN_LOCK_TIMEOUT = 0.15
    yield manager
    manager.stop_update_worker()


@pytest.fixture
def tracker(pm):
    t = PluginHealthTracker(cache_manager=_Cache())
    pm.health_tracker = t
    return t


def _install(pm, plugin, interval=0.01):
    pm.plugins[plugin.plugin_id] = plugin
    pm._update_interval_cache[plugin.plugin_id] = interval
    pm.state_manager.set_state(plugin.plugin_id, PluginState.ENABLED)
    return plugin.plugin_id


def _hang_display(pm, plugin):
    """Run the plugin's display() the way the render loop does, on its own
    thread, holding the plugin's lock while display() blocks on its gate."""
    plugin.display_gate = threading.Event()
    entered = threading.Event()

    def render():
        lock = pm.get_plugin_lock(plugin.plugin_id)
        with lock:
            entered.set()
            plugin.display()

    thread = threading.Thread(target=render, daemon=True)
    thread.start()
    assert entered.wait(timeout=2)
    return plugin.display_gate, thread


def _wait_for(predicate, timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


class TestHungDisplayDoesNotStallTheWorker:
    def test_other_plugins_keep_updating_on_schedule(self, pm):
        hung = _Plugin('hung')
        healthy = _Plugin('healthy')
        _install(pm, hung)
        _install(pm, healthy)
        gate, render_thread = _hang_display(pm, hung)
        try:
            # 'hung' is dict-ordered first, so its item is dequeued ahead of
            # 'healthy' every round. With a blocking acquire the worker parks
            # on it forever and 'healthy' never updates.
            for _ in range(8):
                pm.run_scheduled_updates()
                time.sleep(0.05)
            assert _wait_for(lambda: healthy.update_calls >= 2), (
                f"healthy plugin updated {healthy.update_calls} time(s) while "
                "another plugin's display() was hung")
            assert hung.update_calls == 0
            assert pm._update_worker.is_alive()
        finally:
            gate.set()
            render_thread.join(timeout=2)

    def test_hung_plugin_is_retried_once_its_display_returns(self, pm):
        plugin = _Plugin('hung')
        _install(pm, plugin)
        gate, render_thread = _hang_display(pm, plugin)
        pm.run_scheduled_updates()
        # Skipped, and handed back rather than left RUNNING.
        assert _wait_for(lambda: pm.state_manager.can_execute('hung'))
        assert 'hung' not in pm._pending_updates
        gate.set()
        render_thread.join(timeout=2)
        pm.plugin_last_update.pop('hung', None)  # due again now
        pm.run_scheduled_updates()
        assert _wait_for(lambda: plugin.update_calls == 1)


class TestLockTimeoutIsRecordedAsAHang:
    def test_skip_lands_in_health_and_state(self, pm, tracker):
        plugin = _Plugin('hung')
        _install(pm, plugin)
        gate, render_thread = _hang_display(pm, plugin)
        try:
            pm.run_scheduled_updates()
            assert _wait_for(lambda: tracker.get_health_summary('hung')['hang_count'] == 1)
            summary = tracker.get_health_summary('hung')
            assert summary['consecutive_failures'] == 1
            assert summary['last_hang']['operation'] == 'update lock wait'
            assert 'busy' in summary['last_error']
            error_info = pm.state_manager.get_error_info('hung')
            assert error_info['error_type'] == 'PluginBusyError'
            # Stamped like any failed update, so the retry waits an interval.
            assert pm.plugin_last_update.get('hung', 0) > 0
        finally:
            gate.set()
            render_thread.join(timeout=2)

    def test_repeated_hangs_open_the_circuit_breaker(self, pm, tracker):
        plugin = _Plugin('hung')
        _install(pm, plugin)
        gate, render_thread = _hang_display(pm, plugin)
        try:
            for attempt in range(1, tracker.failure_threshold + 1):
                pm.run_scheduled_updates()
                assert _wait_for(
                    lambda: pm.state_manager.can_execute('hung')
                    and tracker.get_health_summary('hung')['hang_count'] == attempt)
                time.sleep(0.02)  # past the 0.01s interval
            summary = tracker.get_health_summary('hung')
            assert summary['circuit_state'] == CircuitState.OPEN.value
            assert tracker.should_skip_plugin('hung') is True
            # Circuit open: the scheduler no longer queues it at all.
            pm.run_scheduled_updates()
            assert 'hung' not in pm._pending_updates
            assert pm.state_manager.can_execute('hung')
        finally:
            gate.set()
            render_thread.join(timeout=2)

    def test_skip_is_logged_once_per_interval(self, pm):
        pm.logger = MagicMock()
        plugin = _Plugin('hung')
        _install(pm, plugin)
        gate, render_thread = _hang_display(pm, plugin)
        try:
            for _ in range(3):
                pm.run_scheduled_updates()
                assert _wait_for(lambda: pm.state_manager.can_execute('hung'))
                time.sleep(0.02)
        finally:
            gate.set()
            render_thread.join(timeout=2)
        busy_warnings = [c for c in pm.logger.warning.call_args_list
                         if 'update skipped' in c.args[0]]
        assert len(busy_warnings) == 1

    def test_unloaded_while_waiting_is_not_resurrected(self, pm, tracker):
        plugin = _Plugin('hung')
        _install(pm, plugin)
        gate, render_thread = _hang_display(pm, plugin)
        try:
            pm.PLUGIN_LOCK_TIMEOUT = 0.3
            pm.UNLOAD_LOCK_TIMEOUT = 0.05
            pm.run_scheduled_updates()
            time.sleep(0.05)  # worker is now waiting on the lock
            assert pm.unload_plugin('hung') is True
            assert _wait_for(lambda: 'hung' not in pm._pending_updates)
            time.sleep(0.35)
            assert pm.state_manager.get_state('hung') == PluginState.UNLOADED
            assert tracker.get_health_summary('hung')['hang_count'] == 0
        finally:
            gate.set()
            render_thread.join(timeout=2)


class TestUpdateOutlivingTheExecutorTimeout:
    def test_recorded_as_a_hang_then_cleared_when_it_returns(self, pm, tracker):
        plugin = _Plugin('slow', update_seconds=0.4)
        _install(pm, plugin)
        pm.plugin_executor.default_timeout = 0.05
        pm.run_scheduled_updates()
        assert _wait_for(lambda: tracker.get_health_summary('slow')['hang_count'] == 1)
        summary = tracker.get_health_summary('slow')
        assert summary['last_hang']['operation'] == 'update'
        assert summary['consecutive_failures'] == 1
        # The real update() then returns: success resets the streak.
        assert _wait_for(lambda: pm.state_manager.can_execute('slow'))
        assert _wait_for(lambda: tracker.get_health_summary('slow')['consecutive_failures'] == 0)


class TestDisplayTiming:
    def test_fast_frames_record_nothing(self, pm, tracker):
        pm.logger = MagicMock()
        for _ in range(100):
            pm.note_display_duration('p', 0.004)
        assert tracker.get_health_summary('p')['slow_call_count'] == 0
        pm.logger.warning.assert_not_called()

    def test_slow_display_is_recorded_and_warned_once(self, pm, tracker):
        pm.logger = MagicMock()
        for _ in range(5):
            pm.note_display_duration('p', 2.5)
        summary = tracker.get_health_summary('p')
        assert summary['slow_call_count'] == 5
        assert summary['last_slow_call']['operation'] == 'display'
        assert summary['last_slow_call']['seconds'] == 2.5
        # Slow is not failing: the circuit breaker is untouched.
        assert summary['consecutive_failures'] == 0
        assert summary['circuit_state'] == CircuitState.CLOSED.value
        assert pm.logger.warning.call_count == 1

    def test_suppressed_repeats_are_counted_in_the_next_warning(self, pm):
        pm.logger = MagicMock()
        for _ in range(4):
            pm.note_display_duration('p', 2.5)
        pm.HANG_LOG_INTERVAL = 0.0
        pm.note_display_duration('p', 2.5)
        assert pm.logger.warning.call_count == 2
        last = pm.logger.warning.call_args
        assert '3 more since the last warning' in (last.args[0] % last.args[1:])

    def test_display_past_the_executor_timeout_is_a_hang(self, pm, tracker):
        pm.plugin_executor.default_timeout = 3.0
        for _ in range(tracker.failure_threshold):
            pm.note_display_duration('p', 3.5)
        summary = tracker.get_health_summary('p')
        assert summary['hang_count'] == tracker.failure_threshold
        assert summary['last_hang']['operation'] == 'display'
        assert summary['circuit_state'] == CircuitState.OPEN.value

    def test_render_loop_times_each_frame(self, monkeypatch, emulator_mode):
        """DisplayController._display_once hands every frame's duration on."""
        from src import display_controller as dc_module
        ticks = iter([100.0, 103.25])
        monkeypatch.setattr(dc_module, 'time', types.SimpleNamespace(
            monotonic=lambda: next(ticks)))
        controller = dc_module.DisplayController.__new__(dc_module.DisplayController)
        controller.plugin_manager = MagicMock()
        lock = threading.Lock()
        controller.plugin_manager.get_plugin_lock.return_value = lock
        plugin = _Plugin('ticker')

        assert controller._display_once(plugin, 'ticker', False) is True

        controller.plugin_manager.note_display_duration.assert_called_once_with('ticker', 3.25)
        assert not lock.locked()

    def test_skipped_frame_is_not_timed(self, emulator_mode):
        from src import display_controller as dc_module
        controller = dc_module.DisplayController.__new__(dc_module.DisplayController)
        controller.plugin_manager = MagicMock()
        lock = threading.Lock()
        lock.acquire()  # update() in flight
        controller.plugin_manager.get_plugin_lock.return_value = lock
        try:
            assert controller._display_once(_Plugin('ticker'), 'ticker', False) is True
        finally:
            lock.release()
        controller.plugin_manager.note_display_duration.assert_not_called()


class TestConfigChangeIsSerialised:
    def test_does_not_run_concurrently_with_update(self, pm):
        pm.PLUGIN_LOCK_TIMEOUT = 2.0
        plugin = _Plugin('p', update_seconds=0.3)
        _install(pm, plugin)
        pm.run_scheduled_updates()
        assert _wait_for(lambda: plugin.in_update)

        applied = pm.apply_config_change('p', {'enabled': True, 'n': 1})

        assert applied is True
        assert plugin.overlap is False
        assert plugin.events == ['update', 'config']
        assert plugin.config_changes == [{'enabled': True, 'n': 1}]

    def test_runs_holding_the_plugin_lock(self, pm):
        plugin = _Plugin('p')
        _install(pm, plugin)
        held = {}
        plugin.on_config_change = lambda cfg: held.setdefault(
            'locked', pm.get_plugin_lock('p').locked())
        assert pm.apply_config_change('p', {}) is True
        assert held == {'locked': True}
        assert not pm.get_plugin_lock('p').locked()

    def test_busy_lock_defers_the_latest_change_to_before_the_next_update(self, pm):
        plugin = _Plugin('p')
        _install(pm, plugin)
        gate, render_thread = _hang_display(pm, plugin)
        try:
            start = time.monotonic()
            assert pm.apply_config_change('p', {'n': 1}) is False
            assert time.monotonic() - start < 1.0, "the wait must be bounded"
            assert pm.apply_config_change('p', {'n': 2}) is False
            time.sleep(0.4)  # the worker's own bounded retry also finds it busy
            assert plugin.config_changes == []
        finally:
            gate.set()
            render_thread.join(timeout=2)

        pm.run_scheduled_updates()
        assert _wait_for(lambda: plugin.update_calls == 1)
        # Only the latest parked change, applied before the update ran.
        assert plugin.config_changes == [{'n': 2}]
        assert plugin.events == ['config', 'update']
        assert plugin.overlap is False

    def test_deferred_change_applied_by_worker_once_lock_frees(self, pm):
        """No update due to piggyback on: the worker's own queued retry
        applies it. Timeline: the watcher gives up at 0.2s, the worker waits
        from 0.2s to 0.4s, the holder lets go at 0.3s."""
        pm.PLUGIN_LOCK_TIMEOUT = 0.2
        plugin = _Plugin('p')
        _install(pm, plugin, interval=3600)
        pm.plugin_last_update['p'] = time.time()  # not due
        lock = pm.get_plugin_lock('p')
        lock.acquire()
        releaser = threading.Timer(0.3, lock.release)
        releaser.start()
        try:
            assert pm.apply_config_change('p', {'n': 1}) is False
            assert _wait_for(lambda: plugin.config_changes == [{'n': 1}])
        finally:
            releaser.join(timeout=2)
        assert plugin.update_calls == 0
        assert 'p' not in pm._deferred_config_changes

    def test_stale_deferred_change_is_dropped_after_reload(self, pm):
        plugin = _Plugin('p')
        _install(pm, plugin)
        pm._deferred_config_changes['p'] = (plugin, {'n': 1})
        replacement = _Plugin('p')
        pm.plugins['p'] = replacement
        pm._apply_deferred_config_change('p')
        assert plugin.config_changes == []
        assert replacement.config_changes == []
        assert 'p' not in pm._deferred_config_changes

    def test_exceptions_still_reach_the_caller(self, pm):
        plugin = _Plugin('p')
        _install(pm, plugin)

        def boom(cfg):
            raise ValueError("bad config")

        plugin.on_config_change = boom
        with pytest.raises(ValueError):
            pm.apply_config_change('p', {})
        assert not pm.get_plugin_lock('p').locked()


class TestHealthRecords:
    def test_slow_call_persistence_is_rate_limited(self):
        cache = _Cache()
        t = PluginHealthTracker(cache_manager=cache)
        t.record_slow_call('p', 'display', 2.5)
        writes = cache.writes
        for _ in range(50):
            t.record_slow_call('p', 'display', 2.5)
        assert cache.writes == writes
        assert t.get_health_summary('p')['slow_call_count'] == 51

    def test_hang_survives_a_restart(self):
        cache = _Cache()
        PluginHealthTracker(cache_manager=cache).record_hang('p', 'display', 31.0)
        summary = PluginHealthTracker(cache_manager=cache).get_health_summary('p')
        assert summary['hang_count'] == 1
        assert summary['last_hang']['operation'] == 'display'
        assert summary['consecutive_failures'] == 1
