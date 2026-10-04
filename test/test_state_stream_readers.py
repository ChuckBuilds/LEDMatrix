"""Control socket stage 3, both ends: what the display publishes into the
state stream, what it stops writing to the SD card, and the web readers
that use the stream and fall back to the cache keys.

* The display: DisplayController's publish points feed the StateHub; while
  the socket serves readers, display_current_state and the plugin runtime
  snapshot are written less often (measured below, in cache writes per
  minute of a simulated rotation).
* The readers: /display/current-status, /display/on-demand/status,
  /plugins/installed's runtime and /health's display_loop take the socket's
  answer when there is one, judged by the same stale / stalled rules as the
  cache (#726), and the cache keys and heartbeat file when there is not.
* End to end (AF_UNIX only): a real server, the web routes reading it, and
  the fallback once it is gone.
"""

import json
import os
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

os.environ.setdefault("EMULATOR", "true")

from src import display_watchdog  # noqa: E402
from src.ipc import contract as c  # noqa: E402
from src.ipc.server import ControlServer, StateHub  # noqa: E402
from src.plugin_system import plugin_runtime as rt  # noqa: E402
from src.plugin_system.plugin_runtime import (  # noqa: E402
    PluginRuntimePublisher, view_from_socket_state,
)
from src.plugin_system.plugin_state import PluginState, PluginStateManager  # noqa: E402
from test._api_v3_test_helpers import api_v3_client, api_v3_module  # noqa: F401,E402
from web_interface import display_state  # noqa: E402

needs_unix_sockets = pytest.mark.skipif(not c.socket_supported(),
                                        reason='AF_UNIX sockets are Linux/macOS only')


class FakeClock:
    def __init__(self, now=1000.0):
        self.now = now

    def __call__(self):
        return self.now


def _loop(age):
    return lambda: {'heartbeat_age_seconds': age, 'armed': age is not None,
                    'stale_after': display_watchdog.HEARTBEAT_STALE_SECONDS}


def _states():
    states = PluginStateManager()
    states.set_state("clock", PluginState.ENABLED)
    states.record_loaded("clock", "1.0.0", loaded_at=10.0)
    return states


def _hub_with_everything(loop_age=1.0, display_updated=None, plugins_published=None):
    hub = StateHub(loop_probe=_loop(loop_age), epoch='e1')
    hub.publish('display', {
        'mode': 'weather', 'plugin_id': 'weather', 'mode_index': 1, 'total_modes': 3,
        'on_demand_active': False, 'is_display_active': True,
        'last_updated': time.time() if display_updated is None else display_updated,
    }, volatile=('last_updated',))
    hub.publish('on_demand', {'active': True, 'status': 'active', 'plugin_id': 'clock',
                              'expires_at': time.time() + 30, 'remaining': 999.0,
                              'last_updated': time.time()},
                volatile=('last_updated', 'remaining'))
    snapshot = rt.build_runtime_snapshot(_states(), started_at=1.0,
                                         now=time.time() if plugins_published is None
                                         else plugins_published)
    hub.publish('plugins', snapshot, volatile=('published_at',))
    hub.publish('brightness', {'brightness': 80, 'panel_brightness': 40, 'dimmed': True})
    return hub


@pytest.fixture(autouse=True)
def _no_live_subscription():
    yield
    display_state.stop_subscription()


# --- the display's side ---------------------------------------------------------

def _controller(hub=None):
    from src.display_controller import DisplayController
    dc = object.__new__(DisplayController)
    dc.cache_manager = MagicMock()
    dc.current_display_mode = "clock"
    dc.mode_to_plugin_id = {"clock": "clock", "weather": "weather"}
    dc.current_mode_index = 0
    dc.available_modes = ["clock", "weather"]
    dc.on_demand_active = False
    dc.is_display_active = True
    dc._last_published_mode = None
    dc._last_published_at = 0.0
    dc._normal_brightness = 80
    dc.current_brightness = 80
    dc.is_dimmed = False
    for name in ("on_demand_mode", "on_demand_plugin_id", "on_demand_requested_at",
                 "on_demand_expires_at", "on_demand_duration", "on_demand_last_error",
                 "on_demand_last_event"):
        setattr(dc, name, None)
    dc.on_demand_pinned = False
    dc.on_demand_status = 'idle'
    dc._state_hub = hub
    return dc


def _writes(dc, key):
    return sum(1 for call in dc.cache_manager.set.call_args_list if call.args[0] == key)


class TestDisplayPublishes:
    def test_the_publish_points_feed_the_hub(self):
        from src import display_controller as dc_module
        hub = StateHub(epoch='e1')
        dc = _controller(hub)
        with patch.object(dc_module.time, "monotonic", lambda: 1000.0):
            dc._publish_current_mode_state_if_changed()
        state = hub.snapshot()['state']
        assert state['display']['mode'] == 'clock'
        assert state['brightness'] == {'brightness': 80, 'panel_brightness': 80, 'dimmed': False}
        dc.current_brightness, dc.is_dimmed = 30, True
        with patch.object(dc_module.time, "monotonic", lambda: 1001.0):
            dc._publish_current_mode_state_if_changed()   # no cache write due...
        assert hub.snapshot()['state']['brightness']['panel_brightness'] == 30   # ...hub updated

    def test_an_on_demand_outcome_reaches_the_hub(self):
        hub = StateHub(epoch='e1')
        dc = _controller(hub)
        dc.on_demand_status, dc.on_demand_last_error = 'error', 'Plugin nope not found'
        dc._publish_on_demand_state()
        assert hub.snapshot()['state']['on_demand']['error'] == 'Plugin nope not found'
        version = hub.version
        dc._publish_on_demand_state()        # only last_updated moved
        assert hub.version == version

    def test_without_a_socket_nothing_changes(self):
        """No hub (Windows, socket off, the golden traces): every write as before."""
        from src import display_controller as dc_module
        dc = _controller(None)
        now = [1000.0]
        with patch.object(dc_module.time, "monotonic", lambda: now[0]):
            dc._publish_current_mode_state_if_changed()
            now[0] += 1
            dc.current_display_mode = "weather"
            dc._publish_current_mode_state_if_changed()
        assert _writes(dc, 'display_current_state') == 2

    def test_with_readers_on_the_socket_a_mode_change_waits_for_the_refresh(self):
        from src import display_controller as dc_module
        hub = StateHub(epoch='e1')
        hub.subscriber_joined()
        dc = _controller(hub)
        now = [1000.0]
        with patch.object(dc_module.time, "monotonic", lambda: now[0]):
            dc._publish_current_mode_state_if_changed()                 # first: written
            now[0] += 1
            dc.current_display_mode = "weather"
            dc._publish_current_mode_state_if_changed()                 # mode: hub only
            assert _writes(dc, 'display_current_state') == 1
            assert hub.snapshot()['state']['display']['mode'] == 'weather'
            dc.is_display_active = False
            dc._publish_current_mode_state_if_changed()                 # a flag: written
            assert _writes(dc, 'display_current_state') == 2
            now[0] += dc_module.CURRENT_STATE_RELAXED_REFRESH_SECONDS
            dc._publish_current_mode_state_if_changed()                 # refresh
            assert _writes(dc, 'display_current_state') == 3

    def test_when_the_readers_leave_a_changed_mode_is_written_at_once(self):
        from src import display_controller as dc_module
        hub = StateHub(epoch='e1', reader_window=60.0)
        hub.subscriber_joined()
        dc = _controller(hub)
        now = [1000.0]
        with patch.object(dc_module.time, "monotonic", lambda: now[0]), \
                patch.object(hub, "_clock", lambda: now[0]):
            dc._publish_current_mode_state_if_changed()
            dc.current_display_mode = "weather"
            now[0] += 1
            dc._publish_current_mode_state_if_changed()
            assert _writes(dc, 'display_current_state') == 1
            hub.subscriber_left()
            now[0] += 61                                  # past the reader window
            dc._publish_current_mode_state_if_changed()
        assert _writes(dc, 'display_current_state') == 2
        assert dc.cache_manager.set.call_args.args[1]['mode'] == 'weather'

    def test_relaxed_refresh_is_inside_the_readers_max_age(self):
        from src import display_controller as dc_module
        assert dc_module.CURRENT_STATE_RELAXED_REFRESH_SECONDS < \
            display_state.CURRENT_STATE_MAX_AGE_SECONDS

    def test_start_state_stream_publishes_everything(self):
        hub = StateHub(epoch='e1')
        dc = _controller()
        publisher = MagicMock()
        dc._plugin_runtime_publisher = publisher
        dc._start_state_stream(hub)
        state = hub.snapshot()['state']
        assert state['display']['mode'] == 'clock' and state['on_demand']['status'] == 'idle'
        publisher.attach_hub.assert_called_once_with(hub)
        assert dc._state_hub is hub


class TestRuntimePublisherHub:
    def test_attach_publishes_and_ticks_keep_it_fresh_without_new_versions(self):
        wall = FakeClock(1_800_000_000.0)
        hub = StateHub(epoch='e1')
        publisher = PluginRuntimePublisher(MagicMock(), _states(), clock=FakeClock(),
                                           wall_clock=wall)
        publisher.attach_hub(hub)
        assert hub.snapshot()['state']['plugins']['plugins']['clock']['version'] == '1.0.0'
        version = hub.version
        wall.now += 5
        publisher.tick()
        assert hub.version == version
        assert hub.snapshot()['state']['plugins']['published_at'] == wall.now

    def test_a_change_is_a_new_version_at_once_unthrottled(self):
        states = _states()
        hub = StateHub(epoch='e1')
        mono = FakeClock()
        publisher = PluginRuntimePublisher(MagicMock(), states, clock=mono)
        publisher.attach_hub(hub)
        publisher.tick()
        version = hub.version
        states.set_state("clock", PluginState.ERROR, error=RuntimeError("boom"))
        mono.now += 1                       # inside MIN_INTERVAL: no cache write
        assert publisher.tick() is False
        assert hub.version == version + 1
        assert hub.snapshot()['state']['plugins']['plugins']['clock']['state'] == 'error'

    def test_the_cache_refresh_relaxes_while_the_socket_has_readers(self):
        cache = MagicMock()
        mono = FakeClock()
        hub = StateHub(epoch='e1')
        publisher = PluginRuntimePublisher(cache, _states(), clock=mono)
        publisher.attach_hub(hub)
        publisher.tick()
        assert cache.set.call_count == 1
        hub.subscriber_joined()
        mono.now += rt.REFRESH_INTERVAL
        publisher.tick()
        assert cache.set.call_count == 1                # relaxed: not yet
        mono.now += rt.RELAXED_REFRESH_INTERVAL - rt.REFRESH_INTERVAL
        publisher.tick()
        assert cache.set.call_count == 2
        snapshot = cache.set.call_args.args[1]
        # The snapshot says how long it may be trusted.
        assert snapshot['refresh_interval'] == rt.RELAXED_REFRESH_INTERVAL
        assert snapshot['stale_after'] == 3 * rt.RELAXED_REFRESH_INTERVAL


def test_watchdog_liveness_is_the_heartbeat_in_memory():
    clock = FakeClock()
    wd = display_watchdog.RenderWatchdog(environ={}, clock=clock, heartbeat_dir=None,
                                         send=lambda message: True)
    assert wd.liveness()['heartbeat_age_seconds'] is None      # no frame yet
    wd.bind_render_thread()
    wd.note_frame()                                             # arms on the render thread
    clock.now += 7
    live = wd.liveness()
    assert live['heartbeat_age_seconds'] == 7 and live['armed'] is True
    assert live['stale_after'] == display_watchdog.HEARTBEAT_STALE_SECONDS


# --- SD writes saved ------------------------------------------------------------

def _simulate(readers_on_socket, minutes=10, screen_seconds=15, step=0.25):
    """A rotation of two modes, a screen every ``screen_seconds``: the
    publish point runs every ``step`` and the runtime publisher ticks every
    5 s, on fake clocks. Returns cache writes per minute for each key."""
    from src import display_controller as dc_module
    now = [1000.0]
    hub = StateHub(epoch='e1', clock=lambda: now[0])
    if readers_on_socket:
        hub.subscriber_joined()
    dc = _controller(hub)
    cache = dc.cache_manager
    publisher = PluginRuntimePublisher(cache, _states(), clock=lambda: now[0])
    publisher.attach_hub(hub)
    next_tick = now[0]
    end = now[0] + minutes * 60
    with patch.object(dc_module.time, "monotonic", lambda: now[0]):
        while now[0] < end:
            dc.current_display_mode = (
                "clock" if int((now[0] - 1000.0) // screen_seconds) % 2 == 0 else "weather")
            dc._publish_current_mode_state_if_changed()
            if now[0] >= next_tick:
                publisher.tick()
                next_tick += rt.TICK_INTERVAL
            now[0] += step
    return {key: _writes(dc, key) / minutes
            for key in ('display_current_state', rt.PLUGIN_RUNTIME_KEY)}


def test_cache_writes_per_minute_with_and_without_socket_readers():
    before = _simulate(readers_on_socket=False)
    after = _simulate(readers_on_socket=True)
    print(f"\ncache writes/min, rotation of 15 s screens: without socket readers "
          f"{before}, with {after}")
    # A mode change every 15 s is 4 writes a minute; with readers on the
    # socket the key is refreshed once a minute.
    assert before['display_current_state'] == pytest.approx(4, abs=0.2)
    assert after['display_current_state'] == pytest.approx(1, abs=0.2)
    assert before[rt.PLUGIN_RUNTIME_KEY] == pytest.approx(1, abs=0.2)
    assert after[rt.PLUGIN_RUNTIME_KEY] == pytest.approx(0.5, abs=0.2)
    assert sum(after.values()) < sum(before.values()) / 3


# --- the readers' rules ------------------------------------------------------------

class TestSocketRuntimeView:
    def test_live(self):
        view = view_from_socket_state(_hub_with_everything().snapshot())
        assert view.status == rt.LIVE and view.source == 'socket'
        assert view.plugin('clock')['loaded_version'] == '1.0.0'
        assert view.describe()['source'] == 'socket'

    def test_a_stalled_loop_is_stalled_like_the_heartbeat_file(self):
        """#726: a fresh snapshot with a heartbeat at the health check's
        threshold or older is stalled and reports no plugin facts."""
        view = view_from_socket_state(_hub_with_everything(
            loop_age=display_watchdog.HEARTBEAT_STALE_SECONDS).snapshot())
        assert view.status == rt.STALLED
        assert view.plugin('clock')['loaded'] is None
        fresh = view_from_socket_state(_hub_with_everything(
            loop_age=display_watchdog.HEARTBEAT_STALE_SECONDS - 0.5).snapshot())
        assert fresh.status == rt.LIVE

    def test_the_time_since_it_arrived_counts(self):
        snap = _hub_with_everything(loop_age=50.0).snapshot()
        snap['received_mono'] = 100.0
        assert view_from_socket_state(snap, now_mono=105.0).status == rt.LIVE
        assert view_from_socket_state(snap, now_mono=111.0).status == rt.STALLED

    def test_no_beat_yet_is_judged_alone(self):
        view = view_from_socket_state(_hub_with_everything(loop_age=None).snapshot())
        assert view.status == rt.LIVE and view.heartbeat_age_seconds is None

    def test_a_publisher_that_stopped_ticking_goes_stale(self):
        view = view_from_socket_state(_hub_with_everything(
            plugins_published=time.time() - rt.STALE_AFTER - 5).snapshot())
        assert view.status == rt.STALE

    @pytest.mark.parametrize('snapshot', [None, {}, {'state': {'plugins': None}},
                                          {'state': 'x'}])
    def test_no_plugins_section_means_read_the_cache(self, snapshot):
        assert view_from_socket_state(snapshot) is None


class TestDisplayStateHelpers:
    def test_current_status(self):
        status = display_state.current_status(_hub_with_everything().snapshot())
        assert status['mode'] == 'weather' and status['is_display_active'] is True

    def test_a_display_section_the_loop_stopped_refreshing_is_unknown(self):
        """The cache key reads as unknown after its 120 s max_age; so does this."""
        snap = _hub_with_everything(display_updated=time.time() - 121).snapshot()
        assert display_state.current_status(snap) == {
            'mode': None, 'plugin_id': None, 'last_updated': None}

    def test_on_demand_remaining_is_recomputed(self):
        state = display_state.on_demand_state(_hub_with_everything().snapshot())
        assert 25 < state['remaining'] <= 30

    def test_no_snapshot_means_read_the_cache(self):
        assert display_state.current_status(None) is None
        assert display_state.on_demand_state(None) is None
        assert display_state.loop_heartbeat_age(None) is None

    def test_with_the_socket_off_there_is_no_snapshot(self):
        # test/conftest.py turns the socket off for the suite.
        assert display_state.read_state() is None


# --- the routes -----------------------------------------------------------------------

@pytest.fixture
def socket_state(monkeypatch):
    """Make the routes' read_state return this snapshot (None: socket gone)."""
    holder = {'snapshot': None}
    monkeypatch.setattr(display_state, 'read_state', lambda: holder['snapshot'])
    return holder


@pytest.fixture
def web(api_v3_module, api_v3_client, monkeypatch):  # noqa: F811
    cache = api_v3_module.api_v3.cache_manager
    cached = {}
    cache.get.side_effect = lambda key, *a, **kw: cached.get(key)
    monkeypatch.setattr("web_interface.blueprints.api_v3.display._get_display_service_status",
                        lambda: {"active": True})
    monkeypatch.setattr("web_interface.blueprints.api_v3.misc._get_display_service_status",
                        lambda: {"active": True})
    return api_v3_client, cached


def _data(client, url):
    response = client.get(url)
    assert response.status_code == 200, response.get_data(as_text=True)
    return response.get_json()['data']


class TestRoutes:
    def test_current_status_from_the_socket(self, web, socket_state):
        client, cached = web
        cached['display_current_state'] = {'mode': 'stale-cache', 'last_updated': 1}
        socket_state['snapshot'] = _hub_with_everything().snapshot()
        data = _data(client, '/api/v3/display/current-status')
        assert data['mode'] == 'weather' and data['source'] == 'socket'

    def test_current_status_falls_back_to_the_cache(self, web, socket_state):
        client, cached = web
        cached['display_current_state'] = {'mode': 'clock', 'last_updated': 1}
        data = _data(client, '/api/v3/display/current-status')
        assert data['mode'] == 'clock' and data['source'] == 'cache'

    def test_on_demand_status_from_the_socket_and_back(self, web, socket_state):
        client, cached = web
        cached['display_on_demand_state'] = {'active': False, 'status': 'idle'}
        socket_state['snapshot'] = _hub_with_everything().snapshot()
        data = _data(client, '/api/v3/display/on-demand/status')
        assert data['state']['status'] == 'active' and data['source'] == 'socket'
        socket_state['snapshot'] = None
        data = _data(client, '/api/v3/display/on-demand/status')
        assert data['state']['status'] == 'idle' and data['source'] == 'cache'

    @pytest.mark.parametrize('age, status', [
        (3.0, 'running'),
        (display_watchdog.HEARTBEAT_STALE_SECONDS + 1, 'stalled'),
        (None, 'not_reported'),
    ])
    def test_health_display_loop_from_the_socket(self, web, socket_state, age, status):
        client, _ = web
        socket_state['snapshot'] = _hub_with_everything(loop_age=age).snapshot()
        check = _data(client, '/api/v3/health')['checks']['display_loop']
        assert check['status'] == status and check['source'] == 'socket'

    def test_health_falls_back_to_the_heartbeat_file(self, web, socket_state, tmp_path,
                                                      monkeypatch):
        client, _ = web
        path = tmp_path / 'display-heartbeat.json'
        path.write_text(json.dumps({'pid': 1, 'mono': time.monotonic() - 300,
                                    'wall': time.time() - 300}))
        monkeypatch.setattr(display_watchdog, 'HEARTBEAT_PATH', str(path))
        check = _data(client, '/api/v3/health')['checks']['display_loop']
        assert check['status'] == 'stalled' and check['source'] == 'heartbeat_file'

    def test_plugin_runtime_view_prefers_the_socket(self, web, socket_state):
        import web_interface.blueprints.api_v3 as pkg
        socket_state['snapshot'] = _hub_with_everything().snapshot()
        view = pkg._plugin_runtime_view()
        assert view.source == 'socket' and view.live
        socket_state['snapshot'] = _hub_with_everything(
            loop_age=display_watchdog.HEARTBEAT_STALE_SECONDS + 1).snapshot()
        assert pkg._plugin_runtime_view().status == rt.STALLED
        socket_state['snapshot'] = None
        assert pkg._plugin_runtime_view().source == 'cache'


# --- freshness over a long-lived subscription ---------------------------------------------

class _Rig:
    """A display and one web subscription on fake clocks, with no socket.

    Each second the render thread runs its publish point (unless stopped);
    every 5 s the plugin runtime publisher ticks and the subscription gets
    what the server would push it: a ``state`` event when the version moved,
    else a tick (the short ``changed: false`` answer). The loop's heartbeat
    is the time since the render thread last went round.
    """

    MODE = 'ncaa_fb_live'

    def __init__(self):
        from src.ipc import client as control_client
        self.wall = FakeClock(1_800_000_000.0)
        self.mono = FakeClock(5000.0)
        self.last_render = self.mono.now
        self.hub = StateHub(loop_probe=lambda: {
            'heartbeat_age_seconds': self.mono.now - self.last_render, 'armed': True,
            'stale_after': display_watchdog.HEARTBEAT_STALE_SECONDS},
            epoch='e1', clock=self.mono, wall_clock=self.wall)
        self.dc = _controller(self.hub)
        self.dc.current_display_mode = self.MODE
        self.dc.mode_to_plugin_id[self.MODE] = 'football-scoreboard'
        self.publisher = PluginRuntimePublisher(MagicMock(), _states(), clock=self.mono,
                                                wall_clock=self.wall)
        self.publisher.attach_hub(self.hub)
        self.sub = control_client.StateSubscription(paths=['/nowhere'], clock=self.mono)
        self.render = self.plugins = self.ticks = True
        self.sent_version = None
        self._publish()
        self._push()

    def _publish(self):
        from src import display_controller as dc_module
        with patch.object(dc_module.time, 'monotonic', self.mono), \
                patch.object(dc_module.time, 'time', self.wall):
            self.dc._publish_current_mode_state_if_changed()
        self.last_render = self.mono.now

    def _push(self):
        snap = self.hub.snapshot(since=self.sent_version, epoch='e1')
        self.sub._store(snap, full=bool(snap.get('changed')))
        self.sent_version = snap['version']

    def run(self, seconds):
        for _ in range(int(seconds)):
            self.wall.now += 1
            self.mono.now += 1
            if self.render:
                self._publish()
            if int(self.mono.now) % 5 == 0:
                if self.plugins:
                    self.publisher.tick()
                if self.ticks:
                    self._push()

    def status(self):
        return display_state.current_status(self.sub.latest(), now=self.wall.now)

    def runtime(self):
        return view_from_socket_state(self.sub.latest(), now=self.wall.now,
                                      now_mono=self.mono.now)


class TestFreshnessOverTicks:
    """#735 on the ledpi rig: with one mode on screen for more than two
    minutes (a live game, Vegas, a single plugin) current-status read
    ``mode: null`` from the socket. The version stayed put, so the
    subscription's copy kept the ``last_updated`` of the last real change."""

    def test_a_mode_on_screen_for_fifteen_minutes_stays_known(self):
        rig = _Rig()
        for _ in range(15):
            rig.run(60)
            status = rig.status()
            assert status['mode'] == rig.MODE, status
            assert rig.wall.now - status['last_updated'] <= 5
            # The plugin section, unchanged as long, stays live too.
            assert rig.runtime().status == rt.LIVE
        assert rig.sub.snapshots == 1          # all of it from ticks

    def test_the_route_keeps_answering_from_the_socket(self, web, monkeypatch):
        client, cached = web
        cached['display_current_state'] = {'mode': 'from-cache', 'last_updated': 1}
        rig = _Rig()
        rig.run(10 * 60)
        monkeypatch.setattr(display_state, 'read_state', rig.sub.latest)
        with patch.object(display_state.time, 'time', rig.wall):
            data = _data(client, '/api/v3/display/current-status')
        assert (data['mode'], data['source']) == (rig.MODE, 'socket')

    def test_a_render_thread_that_stops_still_reads_stalled_then_unknown(self):
        """#726's verdicts are unchanged: the socket thread keeps ticking,
        but nothing refreshes last_updated and the heartbeat ages."""
        rig = _Rig()
        rig.run(5 * 60)
        rig.render = False
        rig.run(display_watchdog.HEARTBEAT_STALE_SECONDS + 5)
        assert rig.runtime().status == rt.STALLED
        assert display_state.loop_heartbeat_age(rig.sub.latest()) >= \
            display_watchdog.HEARTBEAT_STALE_SECONDS
        assert rig.status()['mode'] == rig.MODE              # not 120 s yet
        rig.run(display_state.CURRENT_STATE_MAX_AGE_SECONDS)
        assert rig.status() == {'mode': None, 'plugin_id': None, 'last_updated': None}

    def test_a_runtime_publisher_that_stops_goes_stale(self):
        rig = _Rig()
        rig.run(60)
        rig.plugins = False
        rig.run(rt.STALE_AFTER + 10)
        assert rig.runtime().status == rt.STALE
        assert rig.status()['mode'] == rig.MODE

    def test_when_the_ticks_stop_the_route_falls_back_to_the_cache(self, web, monkeypatch):
        from src.ipc import client as control_client
        client, cached = web
        cached['display_current_state'] = {'mode': 'from-cache', 'last_updated': 1}
        rig = _Rig()
        rig.run(5 * 60)
        rig.ticks = False
        rig.run(control_client.SUBSCRIPTION_SILENCE_SECONDS + 1)
        assert rig.sub.latest() is None

        def no_socket(*args, **kwargs):
            raise control_client.ControlError('no_socket')
        monkeypatch.setattr(display_state, '_subscription', lambda: rig.sub)
        monkeypatch.setattr(control_client, 'state_get', no_socket)
        data = _data(client, '/api/v3/display/current-status')
        assert (data['mode'], data['source']) == ('from-cache', 'cache')


# --- end to end over a real socket -------------------------------------------------------

@needs_unix_sockets
class TestEndToEnd:
    def test_routes_read_the_socket_then_fall_back_when_it_goes(self, web, tmp_path,
                                                                 monkeypatch):
        client, cached = web
        cached['display_current_state'] = {'mode': 'from-cache', 'last_updated': 1}
        path = str(tmp_path / 'control.sock')
        monkeypatch.setenv(c.SOCKET_PATH_ENV, path)
        hub = _hub_with_everything()
        server = ControlServer(path, state_hub=hub, keepalive=0.2)
        assert server.start()
        try:
            data = _data(client, '/api/v3/display/current-status')
            assert (data['mode'], data['source']) == ('weather', 'socket')
            # The subscription picks up a change the display publishes.
            hub.publish('display', dict(hub.snapshot()['state']['display'], mode='stocks'),
                        volatile=('last_updated',))
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                if _data(client, '/api/v3/display/current-status')['mode'] == 'stocks':
                    break
                time.sleep(0.05)
            assert _data(client, '/api/v3/display/current-status')['mode'] == 'stocks'
            assert hub.subscribers == 1 and hub.readers_active()
        finally:
            server.close()
        # The subscription sees the hang-up, and no socket answers a one-shot.
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            data = _data(client, '/api/v3/display/current-status')
            if data['source'] == 'cache':
                break
            time.sleep(0.05)
        assert (data['mode'], data['source']) == ('from-cache', 'cache')
