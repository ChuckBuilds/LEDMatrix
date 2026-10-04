"""DisplayController's side of control socket stage 2.

* ``brightness.set`` is applied on the render thread at once, repainted,
  and answered with what the panel shows -- including when the dim schedule
  holds it lower, when the panel refuses it, and when the schedule has the
  display off;
* ``plugin.reload`` starts at the top of the loop pass and is answered once
  its plugin-reload thread is done (test_plugin_reload_off_render_thread.py
  has the timing), for every outcome: reloaded (new instance registered,
  Vegas told), not running,
  loaded only for on-demand, failed to load, or raised;
* the render thread wakes for a command in real time, not just on the fake
  clock (test_run_loop_socket_wake.py): within milliseconds on a static
  screen and in a dwell, while a command that does not end the screen
  leaves the frame wait to run its course;
* Vegas checks the queue every frame and runs its interrupt check at once.
"""

import threading
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from src.ipc.contract import BrightnessSetArgs, Command, ErrorCode, PluginReloadArgs
from src.ipc.server import CommandOutcome, ControlServer, QueuedCommand


def _command(cmd, args):
    return QueuedCommand(request_id='t1', cmd=cmd, args=args, received_at=time.time(),
                         outcome=CommandOutcome())


def _brightness(value):
    return _command(Command.BRIGHTNESS_SET, BrightnessSetArgs(value))


def _reload(plugin_id):
    return _command(Command.PLUGIN_RELOAD, PluginReloadArgs(plugin_id))


def _screen(mode):
    """A rotation screen of ``mode``, as the ScreenRunner hands it to the
    1 Hz loop's frame wait."""
    from src.display_arbiter import SCREEN_PREEMPTERS, ScreenPlan, Source
    from src.screen_runner import Screen
    plan = ScreenPlan(Source.LEGACY, mode=mode, preemptible_by=SCREEN_PREEMPTERS)
    return Screen(plan, plugin=None, accepts_display_mode=False, start=0.0)


@pytest.fixture
def dc(test_display_controller):
    c = test_display_controller
    c.display_manager.set_brightness = MagicMock(return_value=True)
    c.display_manager.update_display = MagicMock()
    c.config = {'timezone': 'UTC', 'display': {'hardware': {'brightness': 90}}}
    c._normal_brightness = 90
    c.current_brightness = 90
    c.is_display_active = True
    c._tz = None
    return c


class TestBrightness:
    def test_applied_now_and_repainted(self, dc):
        cmd = _brightness(40)
        dc._apply_control_brightness(cmd)
        dc.display_manager.set_brightness.assert_called_once_with(40)
        dc.display_manager.update_display.assert_called()
        assert dc.current_brightness == 40
        assert cmd.outcome.result == {'brightness': 40, 'panel_brightness': 40,
                                      'dimmed': False, 'display_active': True}

    def test_the_dim_schedule_still_applies(self, dc):
        now = datetime.now(timezone.utc)
        dc.config['dim_schedule'] = {
            'enabled': True, 'dim_brightness': 20,
            'start_time': (now - timedelta(hours=1)).strftime('%H:%M'),
            'end_time': (now + timedelta(hours=1)).strftime('%H:%M')}
        # A stale per-minute answer from before the change must not stick.
        dc._dim_checked_minute = (now.hour, now.minute)
        dc._cached_target_brightness = 90
        cmd = _brightness(70)
        dc._apply_control_brightness(cmd)
        assert cmd.outcome.result == {'brightness': 70, 'panel_brightness': 20,
                                      'dimmed': True, 'display_active': True}
        assert dc._normal_brightness == 70

    def test_a_refused_brightness_is_reported(self, dc):
        dc.display_manager.set_brightness.return_value = False
        cmd = _brightness(40)
        dc._apply_control_brightness(cmd)
        assert cmd.outcome.error_code == ErrorCode.FAILED
        assert dc.current_brightness == 90

    def test_scheduled_off_keeps_it_for_later(self, dc):
        dc.is_display_active = False
        cmd = _brightness(40)
        dc._apply_control_brightness(cmd)
        dc.display_manager.set_brightness.assert_not_called()
        assert cmd.outcome.result['display_active'] is False
        assert cmd.outcome.result['panel_brightness'] == 90
        # When the schedule turns the display back on, the new level is used.
        dc.is_display_active = True
        dc._apply_brightness_target()
        dc.display_manager.set_brightness.assert_called_once_with(40)

    def test_the_next_config_reload_wins(self, dc):
        dc._apply_control_brightness(_brightness(40))
        dc._refresh_config_cache({'display': {'hardware': {'brightness': 75}}})
        assert dc._normal_brightness == 75


class FakeServer:
    def __init__(self, *commands):
        self.commands = list(commands)

    @property
    def has_pending(self):
        return bool(self.commands)

    def drain(self):
        out, self.commands = self.commands, []
        return out

    def close(self):
        pass


@pytest.fixture
def running(dc):
    """dc running 'clock' (two modes) and 'weather', with a reloadable manager."""
    old = SimpleNamespace(modes=['clock_a', 'clock_b'])
    new = SimpleNamespace(modes=['clock_a', 'clock_b'])
    weather = SimpleNamespace(modes=['weather'])
    pm = MagicMock()
    pm.plugins = {'clock': old, 'weather': weather}
    pm.plugin_manifests = {'clock': {'version': '1.0.0'}, 'weather': {}}
    pm.get_plugin = lambda pid: pm.plugins.get(pid)
    calls = []

    def unload(pid):
        calls.append(('unload', pid))
        pm.plugins.pop(pid, None)
        return True

    def reload(pid):
        calls.append(('reload', pid))
        pm.plugins[pid] = new
        pm.plugin_manifests[pid] = {'version': '2.0.0'}
        return True

    def detach(pid):
        return pm.plugins.pop(pid, None)

    def unload_detached(pid, plugin):
        calls.append(('unload', pid))
        assert pid not in pm.plugins, "torn down while still loaded"
        return True

    pm.unload_plugin = MagicMock(side_effect=unload)
    pm.detach_plugin = MagicMock(side_effect=detach)
    pm.unload_detached_plugin = MagicMock(side_effect=unload_detached)
    pm.reload_plugin = MagicMock(side_effect=reload)
    dc.plugin_manager = pm
    dc.plugin_display_modes = {'clock': ['clock_a', 'clock_b'], 'weather': ['weather']}
    dc.available_modes = ['clock_a', 'clock_b', 'weather']
    dc.plugin_modes = {'clock_a': old, 'clock_b': old, 'weather': weather}
    dc.mode_to_plugin_id = {'clock_a': 'clock', 'clock_b': 'clock', 'weather': 'weather'}
    dc.current_mode_index = 2
    dc.current_display_mode = 'weather'
    dc.vegas_coordinator = MagicMock()
    return SimpleNamespace(dc=dc, pm=pm, old=old, new=new, calls=calls)


class TestReload:
    def _run(self, dc, *commands):
        dc._control_server = FakeServer(*commands)
        dc._poll_on_demand_requests()          # the drain: queued, not applied
        assert dc._plugin_reload_pending
        dc._apply_pending_plugin_reloads()     # the top of the next pass: it starts
        assert not dc._plugin_reload_pending
        for job in dc._plugin_reload_jobs:     # its plugin-reload thread
            assert job.done.wait(5)
        dc._finish_plugin_reloads()            # between two frames
        assert not dc._plugin_reload_jobs

    def test_reloaded(self, running):
        dc = running.dc
        cmd = _reload('clock')
        self._run(dc, cmd)
        assert running.calls == [('unload', 'clock'), ('reload', 'clock')]
        assert cmd.outcome.result == {'plugin_id': 'clock', 'reloaded': True,
                                      'version': '2.0.0', 'modes': ['clock_a', 'clock_b']}
        assert dc.plugin_modes['clock_a'] is running.new
        # Its modes keep their place in the rotation.
        assert dc.available_modes == ['clock_a', 'clock_b', 'weather']
        # The screen that was showing is still the current one.
        assert dc.current_display_mode == 'weather'
        assert dc.available_modes[dc.current_mode_index] == 'weather'
        dc.vegas_coordinator.mark_plugin_updated.assert_called_once_with('clock')

    def test_not_running(self, running):
        cmd = _reload('nope')
        self._run(running.dc, cmd)
        assert cmd.outcome.error_code == ErrorCode.NOT_LOADED
        assert running.calls == []

    def test_loaded_only_for_on_demand(self, running):
        running.dc._on_demand_loaded_plugins = {'clock'}
        cmd = _reload('clock')
        self._run(running.dc, cmd)
        assert cmd.outcome.error_code == ErrorCode.BUSY
        assert running.calls == []

    def test_does_not_load_again(self, running):
        running.pm.reload_plugin = MagicMock(return_value=False)
        cmd = _reload('clock')
        self._run(running.dc, cmd)
        assert cmd.outcome.error_code == ErrorCode.FAILED
        assert running.dc.available_modes == ['weather']
        assert 'clock' not in running.dc.plugin_display_modes

    def test_raises(self, running):
        running.pm.reload_plugin = MagicMock(side_effect=RuntimeError('boom'))
        cmd = _reload('clock')
        self._run(running.dc, cmd)
        assert cmd.outcome.error_code == ErrorCode.INTERNAL

    def test_a_pending_reload_ends_vegas_and_the_dwell(self, running):
        dc = running.dc
        dc._pending_plugin_reloads = (_reload('clock'),)
        dc._check_wifi_status_message = MagicMock(return_value=None)
        dc._service_pending_changes = MagicMock()
        assert dc._check_vegas_interrupt() is True
        started = time.monotonic()
        dc._sleep_with_plugin_updates(5)
        assert time.monotonic() - started < 0.1

    def test_a_failing_brightness_does_not_stop_the_rest(self, running):
        dc = running.dc
        bad = _brightness(40)
        dc._apply_control_brightness = MagicMock(side_effect=RuntimeError('x'))
        good = _reload('clock')
        self._run(dc, bad, good)
        assert bad.outcome.error_code == ErrorCode.INTERNAL
        assert good.outcome.result['reloaded'] is True


class TestRealTimeWake:
    """With a real ControlServer and real threads: what the fake clock tests
    show, measured. A connection thread queues the command with
    handle_line(), as it does for a socket client."""

    @pytest.fixture
    def server(self, dc, tmp_path):
        server = ControlServer(str(tmp_path / 'unused.sock'))
        dc._control_server = server
        dc.current_display_mode = 'clock'
        dc.on_demand_active = False
        dc._activate_on_demand = MagicMock(
            side_effect=lambda request: setattr(dc, 'current_display_mode', 'weather'))
        dc._wifi_notice_pending = MagicMock(return_value=False)
        dc._tick_plugin_updates = MagicMock()
        dc._check_live_takeover = MagicMock()
        dc.cache_manager.get = MagicMock(return_value=None)
        dc.cache_manager.set = MagicMock()
        return server

    @staticmethod
    def _post_later(server, line, delay, stamps):
        def run():
            time.sleep(delay)
            stamps.append(time.monotonic())
            server.handle_line(line)
        t = threading.Thread(target=run, daemon=True)
        t.start()
        return t

    @staticmethod
    def _start_line(rid):
        import json
        return json.dumps({'v': 1, 'id': rid, 'cmd': Command.ON_DEMAND_START,
                           'args': {'plugin_id': 'weather'}}).encode()

    def test_static_screen(self, dc, server):
        latencies = []
        for i in range(10):
            dc.current_display_mode = 'clock'
            stamps = []
            t = self._post_later(server, self._start_line(f's{i}'), 0.05, stamps)
            ended = dc._wait_frame_interval(1.0, _screen('clock'))
            woke = time.monotonic()
            t.join()
            assert ended is not None
            latencies.append(woke - stamps[0])
        latencies.sort()
        print(f"static-screen wake latency: median {latencies[5] * 1000:.2f} ms, "
              f"max {latencies[-1] * 1000:.2f} ms")
        assert latencies[-1] < 0.05, latencies

    def test_dwell(self, dc, server):
        latencies = []
        for i in range(10):
            dc.current_display_mode = 'clock'
            stamps = []
            t = self._post_later(server, self._start_line(f'd{i}'), 0.05, stamps)
            dc._sleep_with_plugin_updates(5.0)
            woke = time.monotonic()
            t.join()
            assert dc.current_display_mode == 'weather'
            latencies.append(woke - stamps[0])
        latencies.sort()
        print(f"dwell wake latency: median {latencies[5] * 1000:.2f} ms, "
              f"max {latencies[-1] * 1000:.2f} ms")
        assert latencies[-1] < 0.05, latencies

    def test_a_brightness_does_not_cut_the_frame_wait_short(self, dc, server):
        import json
        stamps = []
        line = json.dumps({'v': 1, 'id': 'b1', 'cmd': Command.BRIGHTNESS_SET,
                           'args': {'brightness': 33}}).encode()
        started = time.monotonic()
        t = self._post_later(server, line, 0.1, stamps)
        assert dc._wait_frame_interval(0.5, _screen('clock')) is None
        assert time.monotonic() - started >= 0.49
        t.join()
        dc.display_manager.set_brightness.assert_called_once_with(33)

    def test_without_a_socket_it_is_a_plain_sleep(self, dc):
        dc._control_server = None
        started = time.monotonic()
        assert dc._wait_frame_interval(0.2, _screen('clock')) is None
        assert time.monotonic() - started >= 0.19


class TestVegasChecksEveryFrame:
    def _coordinator(self, urgent_from):
        from src.vegas_mode.config import VegasModeConfig
        from src.vegas_mode.coordinator import VegasModeCoordinator

        coord = VegasModeCoordinator.__new__(VegasModeCoordinator)
        coord.vegas_config = VegasModeConfig.from_config({'display': {'vegas_scroll': {
            'enabled': True, 'max_cycle_duration': 60, 'continuous_scroll': True}}})
        coord.render_pipeline = MagicMock()
        coord.render_pipeline.frame_interval = 0.0
        coord.render_pipeline.target_fps = 90
        coord.display_manager = MagicMock()
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
        coord._check_static_plugin_trigger = lambda: None
        frames = [0]

        def run_frame():
            frames[0] += 1
            return True
        coord.run_frame = run_frame
        checked_at = []

        def checker():
            checked_at.append(frames[0])
            return frames[0] >= urgent_from
        coord.set_interrupt_checker(checker, check_interval=10,
                                    urgent=lambda: frames[0] >= urgent_from)
        return coord, checked_at

    def test_an_urgent_frame_runs_the_check_at_once(self):
        coord, checked_at = self._coordinator(urgent_from=3)
        assert coord.run_iteration() is False
        assert checked_at == [3]

    def test_without_urgency_the_interval_holds(self):
        coord, checked_at = self._coordinator(urgent_from=25)
        coord._interrupt_urgent = None
        assert coord.run_iteration() is False
        assert checked_at == [10, 20, 30]

    def test_a_raising_urgency_test_is_ignored(self):
        coord, checked_at = self._coordinator(urgent_from=10)
        coord._interrupt_urgent = MagicMock(side_effect=RuntimeError('x'))
        assert coord.run_iteration() is False
        assert checked_at == [10]
