"""The render loop's liveness signals: systemd watchdog pings and the heartbeat.

A panel can freeze while ledmatrix.service stays "active" -- a render thread
stuck inside a plugin's display(). src/display_watchdog.py lets only the
render thread vouch for itself, to systemd (sd_notify WATCHDOG=1) and to the
web interface (a heartbeat file under /run/ledmatrix). These tests pin:

* the sd_notify wire format, including abstract-namespace sockets;
* that nothing is armed until the first frame, so start-up keeps its allowance;
* that beats from any other thread are ignored, so a stuck render thread
  goes quiet even while the update worker and Vegas's tick thread carry on;
* the places the render loop checks in from (dwell sleeps, per-frame
  display, Vegas's own loop, a plugin load's longer allowance).
"""
import json
import os
import socket
import sys
import threading
import time
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

os.environ.setdefault("EMULATOR", "true")  # display_controller imports without hardware

from src import display_watchdog  # noqa: E402
from src.display_watchdog import (  # noqa: E402
    RenderWatchdog, heartbeat_age, notify, read_heartbeat, watchdog_usec)

WATCHDOG_120 = {'NOTIFY_SOCKET': '/run/systemd/notify', 'WATCHDOG_USEC': '120000000'}


class FakeSocket:
    """Records what notify() does with the socket it creates."""

    def __init__(self, record, fail_connect=False):
        self.record = record
        self.fail_connect = fail_connect
        self.closed = False

    def connect(self, address):
        self.record['address'] = address
        if self.fail_connect:
            raise ConnectionRefusedError('nobody listening')

    def sendall(self, data):
        self.record.setdefault('sent', []).append(data)

    def close(self):
        self.closed = True
        self.record['closed'] = True


def fake_factory(record, fail_connect=False):
    def factory(family, kind):
        record['family'], record['type'] = family, kind
        return FakeSocket(record, fail_connect)
    return factory


@pytest.fixture
def af_unix(monkeypatch):
    """AF_UNIX for the fake-socket tests, even on a Python built without it."""
    monkeypatch.setattr(socket, 'AF_UNIX', getattr(socket, 'AF_UNIX', 1), raising=False)
    return socket.AF_UNIX


# -- sd_notify -------------------------------------------------------------

class TestNotify:
    def test_sends_one_datagram_to_the_socket_path(self, af_unix):
        record = {}
        assert notify('WATCHDOG=1', {'NOTIFY_SOCKET': '/run/systemd/notify'},
                      socket_factory=fake_factory(record)) is True
        assert record['family'] == af_unix
        assert record['type'] & socket.SOCK_DGRAM == socket.SOCK_DGRAM
        assert record['address'] == '/run/systemd/notify'
        assert record['sent'] == [b'WATCHDOG=1']
        assert record['closed']

    def test_an_at_sign_means_the_abstract_namespace(self, af_unix):
        record = {}
        notify('READY=1\nSTATUS=Rendering', {'NOTIFY_SOCKET': '@/org/freedesktop/systemd1/notify'},
               socket_factory=fake_factory(record))
        assert record['address'] == '\0/org/freedesktop/systemd1/notify'
        assert record['sent'] == [b'READY=1\nSTATUS=Rendering']

    @pytest.mark.parametrize('address', [None, '', 'relative/path', 'vsock:2:1234'])
    def test_no_usable_socket_sends_nothing(self, af_unix, address):
        record = {}
        env = {} if address is None else {'NOTIFY_SOCKET': address}
        assert notify('WATCHDOG=1', env, socket_factory=fake_factory(record)) is False
        assert record == {}

    def test_a_failed_send_is_false_not_an_exception(self, af_unix):
        record = {}
        assert notify('WATCHDOG=1', {'NOTIFY_SOCKET': '/nope'},
                      socket_factory=fake_factory(record, fail_connect=True)) is False
        assert record['closed']

    @pytest.mark.skipif(not hasattr(socket, 'AF_UNIX') or os.name != 'posix',
                        reason='needs AF_UNIX datagram sockets')
    def test_a_real_socket_receives_the_message(self, tmp_path):
        path = str(tmp_path / 'notify')
        server = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        try:
            server.bind(path)
            server.settimeout(2)
            assert notify('WATCHDOG=1', {'NOTIFY_SOCKET': path}) is True
            assert server.recv(4096) == b'WATCHDOG=1'
        finally:
            server.close()

    @pytest.mark.skipif(not sys.platform.startswith('linux'),
                        reason='abstract sockets are Linux-only')
    def test_a_real_abstract_socket_receives_the_message(self):
        name = f'ledmatrix-test-{os.getpid()}-{time.monotonic_ns()}'
        server = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        try:
            server.bind('\0' + name)
            server.settimeout(2)
            assert notify('READY=1', {'NOTIFY_SOCKET': '@' + name}) is True
            assert server.recv(4096) == b'READY=1'
        finally:
            server.close()


class TestWatchdogUsec:
    def test_reads_the_units_value(self):
        assert watchdog_usec({'WATCHDOG_USEC': '120000000'}) == 120_000_000

    def test_meant_for_another_process(self):
        assert watchdog_usec({'WATCHDOG_USEC': '120000000',
                              'WATCHDOG_PID': str(os.getpid() + 1)}) is None

    def test_meant_for_this_process(self):
        assert watchdog_usec({'WATCHDOG_USEC': '5000000',
                              'WATCHDOG_PID': str(os.getpid())}) == 5_000_000

    @pytest.mark.parametrize('value', [None, '', 'abc', '0', '-5'])
    def test_no_watchdog(self, value):
        env = {} if value is None else {'WATCHDOG_USEC': value}
        assert watchdog_usec(env) is None


# -- the render loop's side ----------------------------------------------------

class Clock:
    def __init__(self, now=1000.0):
        self.now = now

    def __call__(self):
        return self.now


def make(environ=WATCHDOG_120, heartbeat_dir=None, clock=None):
    sent = []
    wd = RenderWatchdog(environ=environ, send=lambda m: sent.append(m) or True,
                        clock=clock or Clock(), wall_clock=lambda: 1_700_000_000.0,
                        heartbeat_dir=heartbeat_dir, enable_faulthandler=False)
    return wd, sent


def pings(sent):
    return [m for m in sent if 'WATCHDOG=1' in m.split('\n')]


def on_other_thread(fn):
    t = threading.Thread(target=fn)
    t.start()
    t.join()


class TestStartup:
    def test_start_up_widens_the_limit(self):
        wd, sent = make()
        wd.begin_startup()
        assert sent == [f'WATCHDOG_USEC={int(display_watchdog.STARTUP_ALLOWANCE_SECONDS * 1e6)}'
                        '\nSTATUS=Starting: loading plugins']

    def test_start_up_never_shortens_a_longer_unit_limit(self):
        wd, sent = make({'NOTIFY_SOCKET': '/x', 'WATCHDOG_USEC': str(3600 * 10**6)})
        wd.begin_startup()
        assert sent[0].startswith(f'WATCHDOG_USEC={3600 * 10**6}\n')

    def test_without_a_watchdog_start_up_sends_nothing(self):
        wd, sent = make({'NOTIFY_SOCKET': '/x'})
        wd.begin_startup()
        assert sent == []


class TestArming:
    def test_nothing_before_the_render_loop_starts(self, tmp_path):
        wd, sent = make(heartbeat_dir=str(tmp_path))
        wd.note_frame()  # a start-up screen
        wd.beat()
        wd.loop_pass()
        assert sent == [] and not wd.armed
        assert not (tmp_path / display_watchdog.HEARTBEAT_NAME).exists()

    def test_nothing_before_the_first_frame(self, tmp_path):
        wd, sent = make(heartbeat_dir=str(tmp_path))
        wd.bind_render_thread()
        wd.loop_pass()   # the first pass has begun...
        wd.beat()        # ...and is, say, composing Vegas content
        assert sent == [] and not wd.armed
        assert not (tmp_path / display_watchdog.HEARTBEAT_NAME).exists()

    def test_the_first_frame_arms_it(self, tmp_path):
        wd, sent = make(heartbeat_dir=str(tmp_path))
        wd.bind_render_thread()
        wd.note_frame()
        assert wd.armed
        assert sent == ['READY=1\nWATCHDOG_USEC=120000000\nWATCHDOG=1\nSTATUS=Rendering']
        heartbeat = json.loads((tmp_path / display_watchdog.HEARTBEAT_NAME).read_text())
        assert heartbeat == {'pid': os.getpid(), 'mono': 1000.0, 'wall': 1_700_000_000.0}

    def test_a_first_frame_pushed_by_another_thread_arms_on_the_next_render_beat(self):
        """A screen's first display() runs on PluginExecutor's thread."""
        wd, sent = make()
        wd.bind_render_thread()
        on_other_thread(wd.note_frame)
        assert not wd.armed and sent == []
        wd.beat()
        assert wd.armed and sent[0].startswith('READY=1\n')

    def test_a_full_pass_with_nothing_drawn_arms_it(self):
        """No plugins enabled, or every screen empty: the loop is still alive."""
        wd, sent = make()
        wd.bind_render_thread()
        wd.loop_pass()
        assert not wd.armed
        wd.loop_pass()
        assert wd.armed and sent[0].startswith('READY=1\n')

    def test_without_a_watchdog_it_still_says_ready_and_writes_the_heartbeat(self, tmp_path):
        wd, sent = make({'NOTIFY_SOCKET': '/x'}, heartbeat_dir=str(tmp_path))
        wd.bind_render_thread()
        wd.note_frame()
        assert sent == ['READY=1\nSTATUS=Rendering']
        assert (tmp_path / display_watchdog.HEARTBEAT_NAME).exists()
        sent_before = len(sent)
        wd.beat()
        assert len(sent) == sent_before  # no WATCHDOG=1 without a watchdog


class TestBeats:
    def test_pings_are_rate_limited(self, tmp_path):
        clock = Clock()
        wd, sent = make(heartbeat_dir=str(tmp_path), clock=clock)
        wd.bind_render_thread()
        wd.note_frame()
        del sent[:]
        for _ in range(100):  # a burst of frames
            wd.beat()
        assert sent == []
        clock.now += display_watchdog.BEAT_INTERVAL_SECONDS
        wd.beat()
        assert sent == ['WATCHDOG=1']
        heartbeat = read_heartbeat(str(tmp_path / display_watchdog.HEARTBEAT_NAME))
        assert heartbeat['mono'] == clock.now

    def test_a_short_unit_limit_pings_more_often(self):
        clock = Clock()
        wd, sent = make({'NOTIFY_SOCKET': '/x', 'WATCHDOG_USEC': '3000000'}, clock=clock)
        wd.bind_render_thread()
        wd.note_frame()
        del sent[:]
        clock.now += 1.0  # a third of 3s
        wd.beat()
        assert sent == ['WATCHDOG=1']

    def test_beats_from_other_threads_are_ignored(self, tmp_path):
        """The update worker, Vegas's tick thread and the prefetcher keep
        running while the render thread is stuck; they must not keep the
        watchdog fed on its behalf."""
        clock = Clock()
        wd, sent = make(heartbeat_dir=str(tmp_path), clock=clock)
        wd.bind_render_thread()
        wd.note_frame()
        del sent[:]
        before = (tmp_path / display_watchdog.HEARTBEAT_NAME).read_text()
        clock.now += 60
        on_other_thread(wd.beat)
        on_other_thread(wd.note_frame)
        on_other_thread(wd.loop_pass)
        assert sent == []
        assert (tmp_path / display_watchdog.HEARTBEAT_NAME).read_text() == before

    def test_the_module_shortcuts_reach_the_process_instance(self, monkeypatch):
        wd, sent = make()
        monkeypatch.setattr(display_watchdog, 'watchdog', wd)
        wd.bind_render_thread()
        display_watchdog.note_frame()
        assert wd.armed
        with display_watchdog.extended(600, 'x'):
            pass
        display_watchdog.beat()
        assert any('WATCHDOG_USEC=600000000' in m for m in sent)


class TestExtended:
    def test_a_long_job_gets_the_longer_limit_then_the_units_back(self):
        wd, sent = make()
        wd.bind_render_thread()
        wd.note_frame()
        del sent[:]
        with wd.extended(900, 'loading plugin weather'):
            assert sent == ['WATCHDOG_USEC=900000000\nWATCHDOG=1\nSTATUS=Busy: loading plugin weather']
        assert sent[-1] == 'WATCHDOG_USEC=120000000\nWATCHDOG=1\nSTATUS=Rendering'

    def test_nested_jobs_restore_once(self):
        wd, sent = make()
        wd.bind_render_thread()
        wd.note_frame()
        del sent[:]
        with wd.extended(900):
            with wd.extended(900):
                pass
            assert len(sent) == 1  # the inner one neither re-extends nor restores
        assert len(sent) == 2 and sent[-1].startswith('WATCHDOG_USEC=120000000\n')

    def test_restored_even_when_the_job_fails(self):
        wd, sent = make()
        wd.bind_render_thread()
        wd.note_frame()
        with pytest.raises(RuntimeError):
            with wd.extended(900):
                raise RuntimeError('pip failed')
        assert sent[-1].startswith('WATCHDOG_USEC=120000000\n')

    def test_off_the_render_thread_or_before_arming_it_does_nothing(self):
        """Start-up loads run on a thread pool under the start-up allowance."""
        wd, sent = make()
        wd.bind_render_thread()
        with wd.extended(900):
            pass
        wd.note_frame()
        del sent[:]
        on_other_thread(lambda: wd.extended(900).__enter__())
        assert sent == []


class TestStopping:
    def test_a_clean_stop_removes_the_heartbeat(self, tmp_path):
        wd, sent = make(heartbeat_dir=str(tmp_path))
        wd.bind_render_thread()
        wd.note_frame()
        wd.stopping()
        assert sent[-1] == 'STOPPING=1'
        assert not (tmp_path / display_watchdog.HEARTBEAT_NAME).exists()

    def test_stopping_outside_systemd_is_harmless(self):
        wd, sent = make({})
        wd.stopping()
        assert sent == []


class TestHeartbeatFile:
    def test_the_directory_is_created_when_missing(self, tmp_path):
        """An install whose unit predates RuntimeDirectory=; the display is root."""
        target = tmp_path / 'run' / 'ledmatrix'
        wd, _ = make(heartbeat_dir=str(target))
        wd.bind_render_thread()
        wd.note_frame()
        assert (target / display_watchdog.HEARTBEAT_NAME).is_file()
        assert [p.name for p in target.iterdir()] == [display_watchdog.HEARTBEAT_NAME]

    @pytest.mark.skipif(os.name != 'posix', reason='POSIX permissions')
    def test_other_users_can_read_it(self, tmp_path):
        wd, _ = make(heartbeat_dir=str(tmp_path))
        wd.bind_render_thread()
        wd.note_frame()
        mode = (tmp_path / display_watchdog.HEARTBEAT_NAME).stat().st_mode & 0o777
        assert mode == 0o644

    def test_nowhere_to_write_is_not_an_error(self, tmp_path):
        blocker = tmp_path / 'not-a-dir'
        blocker.write_text('x')
        clock = Clock()
        wd, sent = make(heartbeat_dir=str(blocker / 'ledmatrix'), clock=clock)
        wd.bind_render_thread()
        wd.note_frame()
        clock.now += 10
        wd.beat()
        assert wd.armed and pings(sent)  # the watchdog works regardless

    def test_windows_gets_no_heartbeat_by_default(self, monkeypatch):
        monkeypatch.setattr(display_watchdog.os, 'name', 'nt')
        wd = RenderWatchdog(environ={}, send=lambda m: True)
        assert wd._heartbeat_path() is None


class TestHeartbeatAge:
    def test_monotonic_is_preferred(self):
        assert heartbeat_age({'mono': 100.0, 'wall': 0.0}, now_mono=112.5, now_wall=9e9) == 12.5

    def test_the_wall_clock_is_the_fallback(self):
        assert heartbeat_age({'wall': 50.0}, now_mono=1.0, now_wall=80.0) == 30.0

    def test_a_monotonic_stamp_from_the_future_is_not_trusted(self):
        """Not the same clock -- fall back rather than report a fresh heartbeat."""
        assert heartbeat_age({'mono': 500.0, 'wall': 50.0}, now_mono=100.0, now_wall=170.0) == 120.0

    def test_no_time_at_all(self):
        assert heartbeat_age({'pid': 1}) is None
        assert heartbeat_age({'mono': True}) is None

    def test_reading_a_missing_or_broken_file(self, tmp_path):
        assert read_heartbeat(str(tmp_path / 'absent.json')) is None
        (tmp_path / 'broken.json').write_text('{not json')
        assert read_heartbeat(str(tmp_path / 'broken.json')) is None
        (tmp_path / 'list.json').write_text('[1, 2]')
        assert read_heartbeat(str(tmp_path / 'list.json')) is None


# -- where the render loop checks in -------------------------------------------

@pytest.fixture
def armed(monkeypatch):
    """A process watchdog bound to this thread and armed, with a clock to advance."""
    clock = Clock()
    wd, sent = make(clock=clock)
    monkeypatch.setattr(display_watchdog, 'watchdog', wd)
    wd.bind_render_thread()
    wd.note_frame()
    del sent[:]
    return SimpleNamespace(wd=wd, sent=sent, clock=clock)


def _tick_clock(armed):
    """Advance the watchdog's clock past the rate limit on every beat check."""
    original = armed.wd._clock

    def advancing():
        armed.clock.now += display_watchdog.BEAT_INTERVAL_SECONDS
        return original()
    armed.wd._clock = advancing


class TestCheckInPoints:
    def test_the_dwell_sleep_checks_in(self, armed):
        from src.display_controller import DisplayController
        dc = object.__new__(DisplayController)
        dc.current_display_mode = 'm'
        dc.is_display_active = True
        dc.on_demand_active = False
        dc._tick_plugin_updates = lambda: None
        dc._service_pending_changes = lambda: None
        _tick_clock(armed)
        dc._sleep_with_plugin_updates(0.05, tick_interval=0.01)
        assert len(pings(armed.sent)) >= 3

    def test_every_frame_of_a_screen_checks_in(self, armed):
        from src.display_controller import DisplayController
        dc = object.__new__(DisplayController)
        dc.plugin_manager = None
        plugin = MagicMock(plugin_id='p')
        _tick_clock(armed)
        for _ in range(3):
            dc._display_once(plugin, 'm', accepts_display_mode=False)
        assert len(pings(armed.sent)) == 3

    def test_vegas_checks_in_every_frame_of_its_own_loop(self, armed):
        """An iteration runs for minutes without returning to run()."""
        import threading as _threading
        from src.vegas_mode.config import VegasModeConfig
        from src.vegas_mode.coordinator import VegasModeCoordinator
        coord = VegasModeCoordinator.__new__(VegasModeCoordinator)
        coord.vegas_config = VegasModeConfig.from_config({'display': {'vegas_scroll': {
            'enabled': True, 'max_cycle_duration': 60}}})
        coord.render_pipeline = MagicMock(frame_interval=0.0, target_fps=90)
        coord.display_manager = MagicMock()
        coord._state_lock = _threading.Lock()
        coord._is_active = True
        coord._is_paused = False
        coord._should_stop = False
        coord._live_priority_active = False
        coord._fps_last_health_log = 0.0
        coord._fps_was_degraded = False
        coord._interrupt_check = None
        coord._interrupt_check_interval = 10
        coord._update_callback = None
        coord._update_tick_running = False
        coord._check_static_plugin_trigger = lambda: None
        frames = []

        def run_frame():
            frames.append(1)
            if len(frames) == 5:
                coord._should_stop = True
                return False
            return True
        coord.run_frame = run_frame
        _tick_clock(armed)
        coord.run_iteration()
        assert len(pings(armed.sent)) == 5

    def test_each_plugin_fetched_for_a_vegas_cycle_checks_in(self, armed):
        from src.vegas_mode.stream_manager import StreamManager
        sm = StreamManager.__new__(StreamManager)
        sm.plugin_manager = SimpleNamespace(plugins={})
        _tick_clock(armed)
        for plugin_id in ('a', 'b'):
            sm._fetch_plugin_content(plugin_id)
        assert len(pings(armed.sent)) == 2

    def test_loading_a_plugin_gets_the_longer_limit(self, armed):
        from src.plugin_system.plugin_manager import PluginManager
        pm = PluginManager.__new__(PluginManager)
        seen = []
        pm._load_plugin = lambda plugin_id, force_enabled=False: seen.append(list(armed.sent)) or True
        assert pm.load_plugin('weather') is True
        allowance = int(display_watchdog.PLUGIN_LOAD_ALLOWANCE_SECONDS * 1e6)
        assert seen[0] and seen[0][-1].startswith(f'WATCHDOG_USEC={allowance}\n')
        assert armed.sent[-1].startswith('WATCHDOG_USEC=120000000\n')


class TestStuckRenderThread:
    def test_a_render_thread_stuck_in_display_stops_the_pings(self, test_display_controller,
                                                             monkeypatch):
        """End to end through DisplayController.run(): pings flow while frames
        do, stop while display() is stuck even though other threads keep
        calling beat(), and nothing else in the process keeps them alive."""
        sent = []
        lock = threading.Lock()

        def record(message):
            with lock:
                sent.append(message)
            return True
        # 0.3s limit -> a ping at most every 0.1s.
        wd = RenderWatchdog(environ={'NOTIFY_SOCKET': '/x', 'WATCHDOG_USEC': '300000'},
                            send=record, heartbeat_dir=None, enable_faulthandler=False)
        monkeypatch.setattr(display_watchdog, 'watchdog', wd)

        stuck, release = threading.Event(), threading.Event()

        class Plugin:
            plugin_id = 'stuck-plugin'
            needs_high_fps = True
            enabled = True
            calls = 0

            def display(self, force_clear=False):
                Plugin.calls += 1
                display_watchdog.note_frame()  # DisplayManager is mocked here
                if Plugin.calls >= 60:  # about half a second of frames
                    stuck.set()
                    release.wait(10)
                    raise KeyboardInterrupt  # ends run() the way SIGTERM does

        controller = test_display_controller
        controller.available_modes = ['stuck-mode']
        controller.plugin_modes = {'stuck-mode': Plugin()}
        controller.mode_to_plugin_id = {'stuck-mode': 'stuck-plugin'}
        controller.current_mode_index = 0

        runner = threading.Thread(target=controller.run, daemon=True)
        runner.start()
        assert stuck.wait(10), 'the render loop never reached the plugin'
        with lock:
            before = len(pings(sent))
        assert wd.armed and any(m.startswith('READY=1') for m in sent)
        assert before >= 2, sent

        # Other threads carry on while the render thread is stuck.
        stop_others = threading.Event()

        def busy_other_thread():
            while not stop_others.is_set():
                display_watchdog.beat()
                display_watchdog.note_frame()
                time.sleep(0.01)
        other = threading.Thread(target=busy_other_thread, daemon=True)
        other.start()
        time.sleep(0.8)  # well past the 0.3s limit
        with lock:
            after = len(pings(sent))
        stop_others.set()
        release.set()
        other.join(5)
        runner.join(10)
        assert after == before, 'something other than the render thread fed the watchdog'
        assert not runner.is_alive()
        assert sent[-1] == 'STOPPING=1'


# -- the unit and the entry point ----------------------------------------------

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _service_directives():
    with open(os.path.join(ROOT, 'systemd', 'ledmatrix.service'), encoding='utf-8') as f:
        lines = [line.strip() for line in f]
    return dict(line.split('=', 1) for line in lines
                if line and not line.startswith(('#', '[')) and '=' in line
                and not line.startswith('Environment='))


def _seconds(value):
    value = value.strip()
    for suffix, factor in (('min', 60), ('s', 1)):
        if value.endswith(suffix):
            return float(value[:-len(suffix)]) * factor
    return float(value)


class TestUnit:
    def test_the_display_unit_has_a_watchdog_the_process_can_feed(self):
        d = _service_directives()
        # Type=notify would block "systemctl start/restart" until READY=1 --
        # after plugins load -- and the web UI and the update check call those
        # with short timeouts.
        assert d['Type'] == 'simple'
        assert d['NotifyAccess'] == 'main'
        assert 'WatchdogSec' in d

    def test_the_watchdog_outlasts_the_loops_longest_healthy_gap(self):
        from src.plugin_system.plugin_executor import PluginExecutor
        limit = _seconds(_service_directives()['WatchdogSec'])
        executor_timeout = PluginExecutor().default_timeout
        assert limit >= 3 * executor_timeout, (
            "a screen's first display() may legitimately take the executor's "
            f"{executor_timeout}s timeout; WatchdogSec={limit:.0f}s leaves too little margin")
        assert limit < display_watchdog.STARTUP_ALLOWANCE_SECONDS
        assert limit < display_watchdog.PLUGIN_LOAD_ALLOWANCE_SECONDS
        assert display_watchdog.BEAT_INTERVAL_SECONDS * 4 <= limit
        assert limit > display_watchdog.HEARTBEAT_STALE_SECONDS >= 2 * executor_timeout

    def test_the_heartbeat_directory_is_created_readable_by_the_web_user(self):
        d = _service_directives()
        assert d['RuntimeDirectory'] == 'ledmatrix'
        assert display_watchdog.HEARTBEAT_DIR == '/run/' + d['RuntimeDirectory']
        assert d['RuntimeDirectoryMode'] == '0755'

    def test_a_crash_loop_backs_off_instead_of_stopping_for_good(self):
        """A tripped start limit leaves the panel dark and refuses the web UI's
        Start button and the update rollback's restart."""
        d = _service_directives()
        assert d['Restart'] == 'always'
        assert 'StartLimitBurst' not in d
        assert int(d['RestartSteps']) > 0
        assert _seconds(d['RestartMaxDelaySec']) > _seconds(d['RestartSec'])

    def test_run_py_widens_the_watchdog_before_importing_anything_heavy(self):
        with open(os.path.join(ROOT, 'run.py'), encoding='utf-8') as f:
            text = f.read()
        call = text.index('display_watchdog.watchdog.begin_startup()')
        assert call < text.index('from src.logging_config')
        assert call < text.index('from src.display_controller')

    def test_the_module_imports_nothing_else_from_src(self):
        """run.py loads it first thing; importing it must stay cheap."""
        with open(os.path.join(ROOT, 'src', 'display_watchdog.py'), encoding='utf-8') as f:
            imports = [line for line in f if line.startswith(('import ', 'from '))]
        assert not [line for line in imports if 'src' in line], imports
