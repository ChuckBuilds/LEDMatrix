"""Changes made from the web UI must reach the panel while a screen is showing.

The main loop applies on-demand requests, the display schedule and brightness
once per pass -- once per screen. Screens are long: a dwell can be a minute and
a Vegas iteration runs for vegas_scroll.max_cycle_duration (240s here). On a
real Pi on 2026-09-23:

  * an on-demand request posted at 10:54:27 was activated at 10:57:24, when the
    Vegas iteration it arrived during finally ended -- nothing read the mailbox
    in between, because _check_vegas_interrupt only looked at a flag that the
    main-loop read sets;
  * two brightness saves 12s apart inside one 30s screen never showed at all.

_service_pending_changes is the fix: a throttled pass the dwell sleep, the
render loops and the Vegas interrupt check all call. These tests drive those
long stretches on a fake clock and check a change lands within one throttle
interval -- and that the throttle holds, since the callers run at frame rate.
"""

import threading
import types
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from src.vegas_mode.config import VegasModeConfig
from src.vegas_mode.coordinator import VegasModeCoordinator

VEGAS_ITERATION_SECONDS = 240
REQUEST_KEY = 'display_on_demand_request'


class FakeClock:
    """A clock that only moves when the code under test sleeps.

    Installed as the ``time`` module of display_controller and the Vegas
    coordinator only -- patching the real module would also speed up every
    background thread in the process.
    """

    def __init__(self, start=10_000.0):
        self.t = start
        self._events = []

    def now(self):
        return self.t

    def sleep(self, seconds):
        # Never zero, so a loop that sleeps "the rest of the frame" still
        # advances when its fake frame took no time.
        self.t += max(seconds, 0.0005)
        while self._events and self._events[0][0] <= self.t:
            _, fn = self._events.pop(0)
            fn()

    def after(self, seconds, fn):
        """Run fn once the clock is `seconds` past now."""
        self._events.append((self.t + seconds, fn))
        self._events.sort(key=lambda e: e[0])

    def module(self):
        return types.SimpleNamespace(time=self.now, monotonic=self.now,
                                     perf_counter=self.now, sleep=self.sleep)


@pytest.fixture
def clock(monkeypatch):
    c = FakeClock()
    fake_time = c.module()
    monkeypatch.setattr('src.display_controller.time', fake_time)
    monkeypatch.setattr('src.vegas_mode.coordinator.time', fake_time)
    return c


@pytest.fixture
def controller(test_display_controller, clock):
    """A controller at rest: no schedule, full brightness, empty mailbox."""
    c = test_display_controller
    c._refresh_config_cache({'display': {'hardware': {'brightness': 90}}})
    c.current_brightness = 90
    c.is_display_active = True
    c._check_wifi_status_message = MagicMock(return_value=None)

    c.mailbox = {}  # what the web process has written

    def cache_get(key, *args, **kwargs):
        if key == REQUEST_KEY:
            return c.mailbox.get('request')
        return None

    def cache_delete(key):
        if key == REQUEST_KEY:
            c.mailbox.pop('request', None)

    c.cache_manager.get = MagicMock(side_effect=cache_get)
    c.cache_manager.delete = MagicMock(side_effect=cache_delete)
    c.cache_manager.set = MagicMock()
    c.display_manager.set_brightness = MagicMock(return_value=True)
    c.display_manager.update_display = MagicMock()

    # Two loaded plugins: the rotation is on one, on-demand can ask for either.
    for mode in ('clock', 'weather'):
        c.plugin_display_modes[mode] = [mode]
        c.plugin_modes[mode] = MagicMock(spec=[])
        c.mode_to_plugin_id[mode] = mode
    c.available_modes = ['clock', 'weather']
    c.current_mode_index = 0
    c.current_display_mode = 'clock'
    return c


def post_request(controller, request_id='r1', mode='clock'):
    controller.mailbox['request'] = {
        'request_id': request_id, 'action': 'start',
        'plugin_id': mode, 'mode': mode,
    }


def save_brightness(controller, brightness):
    """What the config watcher thread does when the web UI saves."""
    controller._controller_config_change(
        controller.config,
        {'display': {'hardware': {'brightness': brightness}}})


def mailbox_reads(controller):
    return sum(1 for call in controller.cache_manager.get.call_args_list
               if call.args and call.args[0] == REQUEST_KEY)


def vegas_coordinator(controller):
    """A coordinator that runs real run_iteration() over a stub renderer.

    Wired to the controller's interrupt checker the way _initialize_vegas_mode
    wires it (every 10 frames).
    """
    coord = VegasModeCoordinator.__new__(VegasModeCoordinator)
    coord.vegas_config = VegasModeConfig.from_config({'display': {'vegas_scroll': {
        'enabled': True, 'max_cycle_duration': VEGAS_ITERATION_SECONDS}}})
    assert coord.vegas_config.continuous_scroll
    coord.render_pipeline = MagicMock()
    coord.stream_manager = MagicMock()
    coord.display_manager = controller.display_manager
    coord.stats = {'cycles_completed': 0, 'interruptions': 0}
    coord._state_lock = threading.Lock()
    coord._is_active = True
    coord._is_paused = False
    coord._should_stop = False
    coord._fps_last_health_log = 0.0
    coord._fps_was_degraded = False
    coord._live_priority_check = None
    coord._live_priority_active = False
    coord._update_callback = None
    coord._update_tick_running = False
    coord.sync_manager = None
    coord._check_static_plugin_trigger = lambda: None
    coord.frames = 0

    def run_frame():
        coord.frames += 1
        return True

    coord.run_frame = run_frame
    coord.set_interrupt_checker(controller._check_vegas_interrupt, check_interval=10)
    return coord


def frame_budget(coord):
    """Longest a change can wait for the next interrupt check."""
    return coord._interrupt_check_interval * coord.vegas_config.get_frame_interval()


class TestOnDemandDuringVegas:
    def test_a_request_mid_iteration_ends_the_iteration_promptly(self, controller, clock):
        coord = vegas_coordinator(controller)
        start = clock.t
        posted = {}

        def post():
            posted['at'] = clock.t
            post_request(controller, mode='weather')

        clock.after(30, post)
        completed = coord.run_iteration()
        latency = clock.t - posted['at']

        assert completed is False, "Vegas ran the whole iteration past the request"
        assert controller.on_demand_active
        assert controller.current_display_mode == 'weather'
        assert latency <= controller.PENDING_CHANGES_INTERVAL + frame_budget(coord) + 0.01, (
            f"request took {latency:.2f}s to interrupt Vegas")
        assert clock.t - start < VEGAS_ITERATION_SECONDS

    def test_the_interrupt_checker_answers_within_one_interval(self, controller, clock):
        controller._service_pending_changes()  # the main loop's last pass
        post_request(controller)
        waited = 0.0
        step = 0.01
        while not controller._check_vegas_interrupt():
            clock.t += step
            waited += step
            assert waited <= controller.PENDING_CHANGES_INTERVAL + step, (
                "interrupt checker never saw the request")
        assert controller.on_demand_active

    def test_an_empty_mailbox_lets_the_iteration_run_out(self, controller, clock):
        coord = vegas_coordinator(controller)
        assert coord.run_iteration() is True
        assert not controller.on_demand_active
        # And the read is throttled: at most one per interval, not per check.
        max_reads = VEGAS_ITERATION_SECONDS / controller.PENDING_CHANGES_INTERVAL + 1
        assert 0 < mailbox_reads(controller) <= max_reads
        checks = coord.frames // coord._interrupt_check_interval
        assert mailbox_reads(controller) < checks / 2


class TestBrightnessIsAppliedMidScreen:
    def _applied_at(self, controller, clock, brightness):
        """Clock time set_brightness(brightness) was first called, or None."""
        return controller.applied.get(brightness)

    def _record_brightness(self, controller, clock):
        controller.applied = {}

        def set_brightness(value):
            controller.applied.setdefault(value, clock.t)
            return True

        controller.display_manager.set_brightness = MagicMock(side_effect=set_brightness)

    def test_during_a_long_dwell(self, controller, clock):
        """The observed failure: two saves 12s apart inside one screen."""
        self._record_brightness(controller, clock)
        saves = {}

        def save(value):
            saves[value] = clock.t
            save_brightness(controller, value)

        clock.after(5, lambda: save(40))
        clock.after(17, lambda: save(90))
        controller._sleep_with_plugin_updates(30)

        interval = controller.PENDING_CHANGES_INTERVAL
        assert 40 in controller.applied, "brightness 40 never reached the panel"
        assert controller.applied[40] - saves[40] <= interval + 0.01
        assert controller.applied[90] - saves[90] <= interval + 0.01
        assert controller.current_brightness == 90

    def test_the_frame_is_repushed_so_the_change_shows(self, controller, clock):
        clock.after(1, lambda: save_brightness(controller, 40))
        controller._sleep_with_plugin_updates(5)
        calls = controller.display_manager.method_calls
        names = [c[0] for c in calls]
        assert 'set_brightness' in names
        assert 'update_display' in names[names.index('set_brightness'):], (
            "nothing pushed a frame after the brightness change")

    def test_during_a_vegas_iteration(self, controller, clock):
        self._record_brightness(controller, clock)
        coord = vegas_coordinator(controller)
        saved = {}

        def save():
            saved['at'] = clock.t
            save_brightness(controller, 25)

        clock.after(60, save)
        assert coord.run_iteration() is True  # brightness does not end the scroll
        assert 25 in controller.applied, "brightness waited for the iteration to end"
        assert controller.applied[25] - saved['at'] <= (
            controller.PENDING_CHANGES_INTERVAL + frame_budget(coord) + 0.01)

    def test_a_dim_schedule_transition_mid_dwell(self, controller, clock):
        self._record_brightness(controller, clock)
        controller._refresh_config_cache({
            'display': {'hardware': {'brightness': 90}},
            'dim_schedule': {'enabled': True, 'start_time': '20:00',
                             'end_time': '07:00', 'dim_brightness': 20},
        })
        wall_start = datetime(2026, 9, 21, 19, 59, 30)
        t0 = clock.t
        with patch('src.display_controller.datetime') as mock_dt:
            mock_dt.strptime = datetime.strptime
            mock_dt.now.side_effect = lambda tz=None: wall_start + timedelta(seconds=clock.t - t0)
            controller._sleep_with_plugin_updates(60)

        assert 20 in controller.applied, "the dim period started mid-screen and was missed"
        dimmed_at = t0 + 30  # 20:00:00
        assert controller.applied[20] - dimmed_at <= controller.PENDING_CHANGES_INTERVAL + 0.01


class TestThrottle:
    def test_no_reads_or_brightness_calls_between_passes(self, controller, clock):
        controller._service_pending_changes()
        reads = mailbox_reads(controller)
        save_brightness(controller, 40)
        post_request(controller)
        for _ in range(500):  # a few seconds of frames, all inside one interval
            controller._service_pending_changes()
            clock.t += controller.PENDING_CHANGES_INTERVAL / 1000
        assert mailbox_reads(controller) == reads
        controller.display_manager.set_brightness.assert_not_called()
        assert not controller.on_demand_active

        clock.t += controller.PENDING_CHANGES_INTERVAL
        controller._service_pending_changes()
        # The poll, plus the consume step's re-read of the request it acted on.
        assert mailbox_reads(controller) > reads
        controller.display_manager.set_brightness.assert_called_once_with(40)
        assert controller.on_demand_active

    def test_between_passes_it_does_no_work_at_all(self, controller, clock):
        """The high-FPS and Vegas loops call this every frame."""
        controller._service_pending_changes()
        controller._poll_on_demand_requests = MagicMock()
        controller._check_dim_schedule = MagicMock(return_value=90)
        controller._check_schedule = MagicMock()
        for _ in range(100):
            controller._service_pending_changes()
            controller._check_vegas_interrupt()
        controller._poll_on_demand_requests.assert_not_called()
        controller._check_dim_schedule.assert_not_called()
        controller._check_schedule.assert_not_called()

    def test_an_unchanged_brightness_is_not_re_sent(self, controller, clock):
        for _ in range(20):
            controller._service_pending_changes()
            clock.t += controller.PENDING_CHANGES_INTERVAL
        controller.display_manager.set_brightness.assert_not_called()

    def test_a_refused_brightness_is_not_retried_every_pass(self, controller, clock):
        controller.display_manager.set_brightness = MagicMock(return_value=False)
        save_brightness(controller, 40)
        for _ in range(20):
            controller._service_pending_changes()
            clock.t += controller.PENDING_CHANGES_INTERVAL
        controller.display_manager.set_brightness.assert_called_once_with(40)
        assert controller.current_brightness == 90
        # A different target is still tried.
        save_brightness(controller, 50)
        controller._service_pending_changes()
        controller.display_manager.set_brightness.assert_called_with(50)

    def test_a_refused_brightness_is_tried_again_after_going_back(self, controller, clock):
        controller.display_manager.set_brightness = MagicMock(return_value=False)
        for value in (40, 90, 40):
            save_brightness(controller, value)
            controller._service_pending_changes()
            clock.t += controller.PENDING_CHANGES_INTERVAL
        assert controller.display_manager.set_brightness.call_count == 2


class TestScheduleAndDwells:
    def _schedule_until(self, controller, end_time):
        controller._refresh_config_cache({
            'display': {'hardware': {'brightness': 90}},
            'schedule': {'enabled': True, 'start_time': '07:00', 'end_time': end_time},
        })

    def test_vegas_hands_back_when_the_display_is_scheduled_off(self, controller, clock):
        self._schedule_until(controller, '22:59')
        coord = vegas_coordinator(controller)
        wall_start = datetime(2026, 9, 21, 22, 59, 0)
        t0 = clock.t
        with patch('src.display_controller.datetime') as mock_dt:
            mock_dt.strptime = datetime.strptime
            mock_dt.now.side_effect = lambda tz=None: wall_start + timedelta(seconds=clock.t - t0)
            completed = coord.run_iteration()
        assert completed is False
        assert not controller.is_display_active
        off_at = t0 + 60  # 23:00:00
        assert clock.t - off_at <= controller.PENDING_CHANGES_INTERVAL + frame_budget(coord) + 0.01

    def test_on_demand_wakes_a_scheduled_off_display_within_an_interval(self, controller, clock):
        """The display-off branch sleeps 60s at a time between checks."""
        controller.is_display_active = False
        controller._evaluate_schedule = MagicMock()  # hold the schedule "off"
        posted = {}

        def post():
            posted['at'] = clock.t
            post_request(controller)

        clock.after(3, post)
        start = clock.t
        controller._sleep_with_plugin_updates(60)
        assert controller.on_demand_active
        assert clock.t - start < 60, "the off-schedule sleep ran its full minute"
        assert clock.t - posted['at'] <= controller.PENDING_CHANGES_INTERVAL + 0.01

    def test_the_dwell_returns_early_on_an_on_demand_stop(self, controller, clock):
        post_request(controller)
        controller._service_pending_changes()
        assert controller.on_demand_active
        clock.t += controller.PENDING_CHANGES_INTERVAL
        controller.mailbox['request'] = {'request_id': 'r2', 'action': 'stop'}
        start = clock.t
        controller._sleep_with_plugin_updates(30)
        assert not controller.on_demand_active
        assert clock.t - start <= controller.PENDING_CHANGES_INTERVAL + 0.01

    def test_a_quiet_dwell_runs_its_full_length(self, controller, clock):
        start = clock.t
        controller._sleep_with_plugin_updates(30)
        assert clock.t - start >= 30


class _Plugin:
    """A static (non-scrolling) plugin mode that records what was shown."""

    needs_high_fps = False

    def __init__(self, mode, plugin_id, shown, results=()):
        self.mode = mode
        self.plugin_id = plugin_id
        self._shown = shown
        self._results = iter(results)

    def display(self, force_clear=False):
        self._shown.append(self.mode)
        if len(self._shown) > 6:
            raise KeyboardInterrupt  # ends run(); it catches this and cleans up
        return next(self._results, True)


class TestRunLoopDoesNotSkipTheRequestedMode:
    def test_on_demand_during_the_minimum_duration_sleep(self, controller, clock):
        """A dwell sleep that ends early on an on-demand start must not rotate.

        The first screen shows once, then reports no content, so run() sleeps
        out the rest of its minimum duration. The request lands during that
        sleep. Falling through to "move to next mode" advanced the new
        on-demand rotation past the mode that was asked for.
        """
        c = controller
        shown = []
        c.plugin_modes.clear()
        c.mode_to_plugin_id.clear()
        c.plugin_display_modes.clear()
        modes = {'clock': ('clock', [True, False]), 'news': ('news', []),
                 'weather': ('weather', []), 'weather_hourly': ('weather', [])}
        for mode, (pid, results) in modes.items():
            c.plugin_modes[mode] = _Plugin(mode, pid, shown, results)
            c.mode_to_plugin_id[mode] = pid
            c.plugin_display_modes.setdefault(pid, []).append(mode)
        c.available_modes = list(modes)
        c.current_mode_index = 0
        c._cleanup_expired_wifi_status = MagicMock()
        c.config.setdefault('display', {})['display_durations'] = {'clock': 30}
        c.plugin_manager.plugin_executor.execute_display.side_effect = (
            lambda target, plugin_id, force_clear=False, display_mode=None, **kw:
            target.display(force_clear=force_clear))
        clock.after(10, lambda: post_request(c, mode='weather'))

        c.run()

        assert c.on_demand_modes[:2] == ['weather', 'weather_hourly']
        assert shown[:2] == ['clock', 'clock']
        assert shown[2] == 'weather', f"on-demand opened on {shown[2]}, not the requested mode"


class _Stop(KeyboardInterrupt):
    """Ends run(): it catches KeyboardInterrupt and cleans up."""


class TestRunLoopBlanksWhenVegasHandsBack:
    def test_scheduled_off_mid_iteration_blanks_instead_of_rendering(self, controller, clock):
        c = controller
        shown = []
        c.plugin_modes['clock'] = _Plugin('clock', 'clock', shown)
        c.plugin_modes['weather'] = _Plugin('weather', 'weather', shown)
        c._cleanup_expired_wifi_status = MagicMock()
        c._refresh_config_cache({
            'display': {'hardware': {'brightness': 90}},
            'schedule': {'enabled': True, 'start_time': '07:00', 'end_time': '22:59'},
        })
        c.vegas_coordinator = vegas_coordinator(c)
        c.vegas_coordinator._pending_config_update = False

        def stop():
            raise _Stop()

        wall_start = datetime(2026, 9, 21, 22, 59, 0)
        t0 = clock.t
        clock.after(90, stop)
        with patch('src.display_controller.datetime') as mock_dt:
            mock_dt.strptime = datetime.strptime
            mock_dt.now.side_effect = lambda tz=None: wall_start + timedelta(seconds=clock.t - t0)
            c.run()

        assert c.vegas_coordinator.frames > 0  # Vegas was running first
        assert not c.is_display_active
        assert shown == [], f"rendered {shown} after the display was scheduled off"
        c.display_manager.clear.assert_called()

    @pytest.mark.parametrize('needs_high_fps', [True, False])
    def test_a_screen_scheduled_off_midway_stops_rendering(self, controller, clock,
                                                           needs_high_fps):
        c = controller
        shown_at = []
        plugin = _Plugin('ticker', 'ticker', [])
        plugin.needs_high_fps = needs_high_fps
        plugin.display = lambda force_clear=False: shown_at.append(clock.t) or True
        c.plugin_modes.clear()
        c.mode_to_plugin_id.clear()
        c.plugin_display_modes.clear()
        c.plugin_modes['ticker'] = plugin
        c.mode_to_plugin_id['ticker'] = 'ticker'
        c.plugin_display_modes['ticker'] = ['ticker']
        c.available_modes = ['ticker']
        c.current_mode_index = 0
        c._cleanup_expired_wifi_status = MagicMock()
        c._refresh_config_cache({
            'display': {'hardware': {'brightness': 90},
                        'display_durations': {'ticker': 120}},
            'schedule': {'enabled': True, 'start_time': '07:00', 'end_time': '22:59'},
        })
        c.plugin_manager.plugin_executor.execute_display.side_effect = (
            lambda target, plugin_id, force_clear=False, display_mode=None, **kw:
            target.display(force_clear=force_clear))

        def stop():
            raise _Stop()

        wall_start = datetime(2026, 9, 21, 22, 59, 0)
        t0 = clock.t
        clock.after(150, stop)
        with patch('src.display_controller.datetime') as mock_dt:
            mock_dt.strptime = datetime.strptime
            mock_dt.now.side_effect = lambda tz=None: wall_start + timedelta(seconds=clock.t - t0)
            c.run()

        off_at = t0 + 60  # 23:00:00, halfway through a 120s screen
        assert shown_at and shown_at[0] < off_at
        assert shown_at[-1] - off_at <= 1.0 + c.PENDING_CHANGES_INTERVAL, (
            f"kept rendering {shown_at[-1] - off_at:.1f}s after the display was scheduled off")
        assert not c.is_display_active


class TestRunLoopAppliesBrightnessMidScreen:
    """The render loops of run() itself, not just the helpers they call."""

    def _run_screen(self, c, clock, needs_high_fps):
        shown = []
        plugin = _Plugin('ticker', 'ticker', shown)
        plugin.needs_high_fps = needs_high_fps
        plugin.display = lambda force_clear=False: shown.append('ticker') or True
        c.plugin_modes.clear()
        c.mode_to_plugin_id.clear()
        c.plugin_display_modes.clear()
        c.plugin_modes['ticker'] = plugin
        c.mode_to_plugin_id['ticker'] = 'ticker'
        c.plugin_display_modes['ticker'] = ['ticker']
        c.available_modes = ['ticker']
        c.current_mode_index = 0
        c._cleanup_expired_wifi_status = MagicMock()
        c.config.setdefault('display', {})['display_durations'] = {'ticker': 60}
        c.plugin_manager.plugin_executor.execute_display.side_effect = (
            lambda target, plugin_id, force_clear=False, display_mode=None, **kw:
            target.display(force_clear=force_clear))
        applied = {}

        def set_brightness(value):
            applied.setdefault(value, clock.t)
            return True

        c.display_manager.set_brightness = MagicMock(side_effect=set_brightness)
        start = clock.t
        clock.after(5, lambda: save_brightness(c, 30))

        def stop():
            raise _Stop()

        clock.after(20, stop)
        c.run()
        return start, applied, shown

    def test_a_scrolling_screen(self, controller, clock):
        start, applied, shown = self._run_screen(controller, clock, needs_high_fps=True)
        assert len(shown) > 100  # really was the high-FPS loop
        assert 30 in applied, "brightness waited for the scrolling screen to end"
        assert applied[30] - (start + 5) <= controller.PENDING_CHANGES_INTERVAL + 0.02

    def test_a_static_screen(self, controller, clock):
        start, applied, shown = self._run_screen(controller, clock, needs_high_fps=False)
        assert len(shown) < 30  # really was the once-a-second loop
        assert 30 in applied, "brightness waited for the static screen to end"
        # This loop redraws once a second, and services changes after each redraw.
        assert applied[30] - (start + 5) <= 1.0 + controller.PENDING_CHANGES_INTERVAL
