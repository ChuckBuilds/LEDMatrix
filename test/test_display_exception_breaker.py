"""A plugin whose display() raises must count as a circuit-breaker failure.

The first frame of every screen goes through PluginExecutor.execute_display,
which catches whatever display() raises and reports False. run() read that
False as "no content" and called record_success() on it, so a plugin that
raised on every screen reset its own failure streak each time and the breaker
never opened. It stayed in rotation, logging a traceback per screen, forever.

These tests drive the real run() on a fake clock with the real executor and
the real health tracker.
"""

import copy
import threading
import types
from unittest.mock import MagicMock

import pytest

from src.exceptions import PluginError
from src.plugin_system.plugin_executor import PluginExecutor
from src.plugin_system.plugin_health import CircuitState, PluginHealthTracker

THRESHOLD = 3
COOLDOWN = 300.0
SCREEN_SECONDS = 10


class FakeClock:
    """Moves only when the code under test sleeps; runs events as it passes them."""

    def __init__(self, start=10_000.0):
        self.t = start
        self._events = []

    def now(self):
        return self.t

    def sleep(self, seconds):
        self.t += max(seconds, 0.0005)
        while self._events and self._events[0][0] <= self.t:
            _, fn = self._events.pop(0)
            fn()

    def after(self, seconds, fn):
        self._events.append((self.t + seconds, fn))
        self._events.sort(key=lambda e: e[0])

    def module(self):
        return types.SimpleNamespace(time=self.now, monotonic=self.now,
                                     perf_counter=self.now, sleep=self.sleep)


class _Stop(KeyboardInterrupt):
    """Ends run(): it catches KeyboardInterrupt and cleans up."""


class _Cache:
    def __init__(self):
        self.store = {}

    def set(self, key, data, ttl=None, **kwargs):
        self.store[key] = copy.deepcopy(data)

    def get(self, key, max_age=None, memory_ttl=None, **kwargs):
        return copy.deepcopy(self.store.get(key))


class _Plugin:
    """A static plugin. ``outcomes`` scripts each screen's first frame in
    turn: True/False is returned, an exception instance is raised; once the
    script runs out it returns True. Later frames of a screen return True.

    A screen's first frame is the one PluginExecutor dispatches, on its own
    thread; the render loop's later frames run on the calling thread.
    """

    needs_high_fps = False

    def __init__(self, plugin_id, clock, outcomes=()):
        self.plugin_id = plugin_id
        self._clock = clock
        self._outcomes = list(outcomes)
        self._caller = threading.current_thread()
        self.first_frames = []  # (time, outcome) of each executor dispatch
        self.calls = 0

    def display(self, force_clear=False):
        if threading.current_thread() is self._caller:
            return True  # a later frame of a screen that started fine
        self.calls += 1
        outcome = self._outcomes.pop(0) if self._outcomes else True
        self.first_frames.append((self._clock.t, outcome))
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


@pytest.fixture
def clock(monkeypatch):
    c = FakeClock()
    fake_time = c.module()
    monkeypatch.setattr('src.display_controller.time', fake_time)
    # The breaker's cooldown is wall-clock; put it on the same clock.
    monkeypatch.setattr('src.plugin_system.plugin_health.time', fake_time)
    return c


@pytest.fixture
def tracker():
    return PluginHealthTracker(_Cache(), failure_threshold=THRESHOLD,
                               cooldown_period=COOLDOWN)


@pytest.fixture
def controller(test_display_controller, clock, tracker):
    c = test_display_controller
    c._refresh_config_cache({'display': {'hardware': {'brightness': 90}}})
    c.current_brightness = 90
    c.is_display_active = True
    c._check_wifi_status_message = MagicMock(return_value=None)
    c._cleanup_expired_wifi_status = MagicMock()
    c.cache_manager.get = MagicMock(return_value=None)
    c.cache_manager.set = MagicMock()
    c.cache_manager.delete = MagicMock()
    c.display_manager.set_brightness = MagicMock(return_value=True)
    c.display_manager.update_display = MagicMock()

    pm = c.plugin_manager
    # The real executor: its exception handling is what is under test.
    pm.plugin_executor = PluginExecutor(default_timeout=5.0)
    pm.health_tracker = tracker
    locks = {}
    pm.get_plugin_lock = lambda pid: locks.setdefault(pid, threading.Lock())
    pm.record_display_hang = MagicMock()
    pm.note_display_duration = MagicMock()
    return c


def _install(c, *plugins):
    c.plugin_modes.clear()
    c.mode_to_plugin_id.clear()
    c.plugin_display_modes.clear()
    for plugin in plugins:
        c.plugin_modes[plugin.plugin_id] = plugin
        c.mode_to_plugin_id[plugin.plugin_id] = plugin.plugin_id
        c.plugin_display_modes[plugin.plugin_id] = [plugin.plugin_id]
    c.available_modes = [p.plugin_id for p in plugins]
    c.current_mode_index = 0
    c.current_display_mode = c.available_modes[0]
    c.config.setdefault('display', {})['display_durations'] = {
        p.plugin_id: SCREEN_SECONDS for p in plugins}


def _run_for(c, clock, seconds):
    def stop():
        raise _Stop()
    clock.after(seconds, stop)
    c.run()


def _boom():
    return RuntimeError("display() failed")


class TestRaisingDisplayOpensTheBreaker:
    def test_opens_at_the_threshold_and_leaves_rotation(self, controller, clock, tracker):
        c = controller
        crashy = _Plugin('crashy', clock, [_boom() for _ in range(100)])
        good = _Plugin('good', clock)
        _install(c, good, crashy)

        _run_for(c, clock, 200)

        state = tracker.get_health_state('crashy')
        assert state['circuit_state'] == CircuitState.OPEN.value
        assert state['consecutive_failures'] == THRESHOLD
        assert state['last_error'].endswith("display() failed")
        # Exactly THRESHOLD raises reached display(); the open breaker kept
        # it out of every later pass, well inside the cooldown.
        assert crashy.calls == THRESHOLD
        # The display kept moving: the healthy plugin went on being shown.
        assert len(good.first_frames) > THRESHOLD + 2
        assert tracker.get_health_state('good')['consecutive_failures'] == 0

    def test_back_in_rotation_after_the_cooldown(self, controller, clock, tracker):
        c = controller
        crashy = _Plugin('crashy', clock, [_boom() for _ in range(THRESHOLD)])
        good = _Plugin('good', clock)
        _install(c, good, crashy)

        _run_for(c, clock, COOLDOWN + 100)

        # Half-open after the cooldown, one attempt succeeded, circuit closed.
        assert crashy.calls > THRESHOLD
        state = tracker.get_health_state('crashy')
        assert state['circuit_state'] == CircuitState.CLOSED.value
        assert state['consecutive_failures'] == 0
        opened_at = crashy.first_frames[THRESHOLD - 1][0]
        retried_at = crashy.first_frames[THRESHOLD][0]
        assert retried_at - opened_at >= COOLDOWN

    def test_one_success_resets_the_streak(self, controller, clock, tracker):
        c = controller
        script = [_boom(), _boom(), True, _boom(), _boom(), True]
        flaky = _Plugin('flaky', clock, script)
        good = _Plugin('good', clock)
        _install(c, good, flaky)

        _run_for(c, clock, 6 * 2 * SCREEN_SECONDS + 5)

        assert flaky.calls >= len(script)
        assert [o if o is True else 'raised' for _, o in flaky.first_frames[:6]] == [
            'raised', 'raised', True, 'raised', 'raised', True]
        state = tracker.get_health_state('flaky')
        assert state['circuit_state'] == CircuitState.CLOSED.value
        assert state['consecutive_failures'] == 0
        assert state['total_failures'] == 4

    def test_no_content_is_still_not_a_failure(self, controller, clock, tracker):
        c = controller
        empty = _Plugin('empty', clock, [False] * 100)
        good = _Plugin('good', clock)
        _install(c, good, empty)

        _run_for(c, clock, 200)

        state = tracker.get_health_state('empty')
        assert state['circuit_state'] == CircuitState.CLOSED.value
        assert state.get('total_failures', 0) == 0
        assert empty.calls > THRESHOLD


class TestHangIsNotCountedTwice:
    def test_a_timed_out_display_records_only_the_hang(self, controller, clock, tracker):
        c = controller
        c.plugin_manager.plugin_executor = PluginExecutor(default_timeout=0.05)
        release = threading.Event()

        class _Hung(_Plugin):
            def display(self, force_clear=False):
                self.calls += 1
                release.wait(2.0)  # real time: outlives the executor's timeout
                return True

        hung = _Hung('hung', clock)
        good = _Plugin('good', clock)
        _install(c, hung, good)
        failures = []
        real_record_failure = tracker.record_failure
        tracker.record_failure = lambda pid, err=None: (
            failures.append(pid), real_record_failure(pid, err))
        try:
            _run_for(c, clock, SCREEN_SECONDS - 1)
        finally:
            release.set()

        c.plugin_manager.record_display_hang.assert_called_once()
        assert c.plugin_manager.record_display_hang.call_args.args[0] == 'hung'
        # The hang path records the failure (PluginManager._record_hang); the
        # dispatch adds neither a failure nor a success on top.
        assert failures == []
        assert tracker.get_health_state('hung').get('total_successes', 0) == 0


class TestExecutorRaiseErrors:
    def _plugin(self, display):
        return types.SimpleNamespace(display=display)

    def test_default_still_returns_false(self):
        def display(force_clear=False):
            raise ValueError("bad")
        assert PluginExecutor().execute_display(
            self._plugin(display), 'p', accepts_display_mode=False) is False

    def test_raise_errors_surfaces_the_plugin_error(self):
        def display(force_clear=False):
            raise ValueError("bad")
        with pytest.raises(PluginError) as info:
            PluginExecutor().execute_display(
                self._plugin(display), 'p', accepts_display_mode=False,
                raise_errors=True)
        assert isinstance(info.value.__cause__, ValueError)

    def test_raise_errors_leaves_a_timeout_as_false(self):
        done = threading.Event()

        def display(force_clear=False):
            done.wait(1.0)
            return True
        try:
            assert PluginExecutor(default_timeout=0.05).execute_display(
                self._plugin(display), 'p', accepts_display_mode=False,
                raise_errors=True) is False
        finally:
            done.set()

    def test_raise_errors_passes_results_through(self):
        executor = PluginExecutor()
        for value, expected in ((True, True), (False, False), (None, True)):
            assert executor.execute_display(
                self._plugin(lambda force_clear=False, v=value: v), 'p',
                accepts_display_mode=False, raise_errors=True) is expected
