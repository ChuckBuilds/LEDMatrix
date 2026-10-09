"""The display publishes its plugin runtime state; the web reads it back.

Only the display runs plugins, so only it knows which ones are loaded, where
each is in its lifecycle, why one failed and which version it runs. It
publishes that as one snapshot in the shared cache (plugin_runtime), and the
web interface fills /api/v3/plugins/installed's ``loaded`` / ``state`` /
``error_info`` from it -- but only while the snapshot is live. A stale,
stopped or missing snapshot is reported as such, with those fields null.

The cross-process tests use two CacheManagers over one temporary directory,
the arrangement of the real services (which share /var/cache/ledmatrix).
"""
import json
import os
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src import display_watchdog  # noqa: E402
from src.cache_manager import CacheManager  # noqa: E402
from src.plugin_system import plugin_runtime as rt  # noqa: E402
from src.plugin_system.plugin_manager import PluginManager  # noqa: E402
from src.plugin_system.plugin_runtime import (  # noqa: E402
    PLUGIN_RUNTIME_KEY, PluginRuntimePublisher, read_plugin_runtime,
    view_from_snapshot,
)
from src.plugin_system.plugin_state import PluginState, PluginStateManager  # noqa: E402
from test._api_v3_test_helpers import api_v3_client, api_v3_module  # noqa: F401,E402


class FakeClock:
    def __init__(self, now=1000.0):
        self.now = now

    def __call__(self):
        return self.now


@pytest.fixture
def shared_cache(tmp_path, monkeypatch):
    """Two cache managers over one directory: the display's and the web's."""
    monkeypatch.setattr(CacheManager, "_get_writable_cache_dir", lambda self: str(tmp_path))
    display_cache, web_cache = CacheManager(), CacheManager()
    yield display_cache, web_cache
    display_cache.stop_cleanup_thread()
    web_cache.stop_cleanup_thread()


def _publisher(cache, states, wall=None, mono=None):
    return PluginRuntimePublisher(cache, states, clock=mono or FakeClock(),
                                  wall_clock=wall or FakeClock(1_800_000_000.0))


# --- The state machine: what counts as a change -----------------------------

class TestChangeCount:
    def test_an_ordinary_update_is_not_a_change(self):
        """ENABLED -> RUNNING -> ENABLED is every update() call. Publishing it
        would be an SD-card write per plugin update."""
        states = PluginStateManager()
        states.set_state("clock", PluginState.ENABLED)
        before = states.change_count
        for _ in range(50):
            states.set_state("clock", PluginState.RUNNING)
            states.set_state("clock", PluginState.ENABLED)
        assert states.change_count == before
        assert states.runtime_records()["clock"]["state"] == "enabled"

    def test_running_is_published_as_enabled(self):
        states = PluginStateManager()
        states.set_state("clock", PluginState.RUNNING)
        assert states.runtime_records()["clock"]["state"] == "enabled"

    @pytest.mark.parametrize("change", [
        lambda s: s.set_state("clock", PluginState.ERROR, error=ValueError("x")),
        lambda s: s.set_state_with_error("clock", PluginState.ENABLED, {"error": "x"}),
        lambda s: s.record_loaded("clock", "1.0.0"),
        lambda s: s.set_state("clock", PluginState.DISABLED),
        lambda s: s.clear_state("clock"),
        lambda s: s.set_state("weather", PluginState.LOADED),
    ])
    def test_reader_visible_changes_move_it(self, change):
        states = PluginStateManager()
        states.set_state("clock", PluginState.ENABLED)
        before = states.change_count
        change(states)
        assert states.change_count > before

    def test_recovering_from_an_error_is_a_change(self):
        states = PluginStateManager()
        states.set_state_with_error("clock", PluginState.ENABLED, {"error": "timeout"})
        before = states.change_count
        states.set_state("clock", PluginState.ENABLED)  # next update succeeded
        assert states.change_count > before
        assert states.runtime_records()["clock"]["error_info"] is None

    def test_clear_state_forgets_the_loaded_record(self):
        states = PluginStateManager()
        states.set_state("clock", PluginState.ENABLED)
        states.record_loaded("clock", "1.0.0", loaded_at=5.0)
        assert states.runtime_records()["clock"]["loaded"] is True
        states.clear_state("clock")
        assert states.runtime_records() == {}


class TestPluginManagerRecordsWhatItLoaded:
    @pytest.fixture
    def pm(self, tmp_path):
        plugins_dir = tmp_path / "plugins"
        (plugins_dir / "demo").mkdir(parents=True)
        manager = PluginManager(plugins_dir=str(plugins_dir))
        manager.plugin_manifests["demo"] = {"id": "demo", "name": "Demo", "version": "2.1.0"}
        manager.schema_manager = MagicMock()
        manager.schema_manager.get_schema_path.return_value = None
        manager.plugin_loader = MagicMock()
        manager.plugin_loader.find_plugin_directory.return_value = plugins_dir / "demo"
        manager.plugin_loader.load_plugin.return_value = (MagicMock(spec=["on_enable"]), None)
        return manager

    def test_load_records_the_manifest_version(self, pm):
        assert pm.load_plugin("demo") is True
        record = pm.state_manager.runtime_records()["demo"]
        assert record["loaded"] is True
        assert record["state"] == "enabled"
        assert record["version"] == "2.1.0"
        assert isinstance(record["loaded_at"], float)

    def test_unload_forgets_it(self, pm):
        pm.load_plugin("demo")
        pm.unload_plugin("demo")
        assert "demo" not in pm.state_manager.runtime_records()

    def test_a_failed_load_is_an_error_and_not_loaded(self, pm):
        pm.plugin_loader.load_plugin.side_effect = ImportError("No module named 'requests'")
        assert pm.load_plugin("demo") is False
        record = pm.state_manager.runtime_records()["demo"]
        assert record["loaded"] is False
        assert record["state"] == "error"
        assert "requests" in record["error_info"]["error"]


# --- Publishing -------------------------------------------------------------

class TestPublisher:
    def test_first_tick_publishes(self):
        cache = MagicMock()
        states = PluginStateManager()
        states.set_state("clock", PluginState.ENABLED)
        states.record_loaded("clock", "1.0.0", loaded_at=10.0)

        assert _publisher(cache, states).tick() is True

        key, snapshot = cache.set.call_args.args
        assert key == PLUGIN_RUNTIME_KEY
        assert snapshot["running"] is True
        assert snapshot["published_at"] == 1_800_000_000.0
        assert snapshot["stale_after"] == rt.STALE_AFTER
        assert snapshot["plugins"] == {"clock": {
            "loaded": True, "state": "enabled", "error": None,
            "version": "1.0.0", "loaded_at": 10.0, "modes": None}}

    def test_changes_are_throttled_and_quiet_displays_refresh(self):
        cache = MagicMock()
        states = PluginStateManager()
        mono = FakeClock()
        publisher = _publisher(cache, states, mono=mono)
        publisher.tick()
        assert cache.set.call_count == 1

        # Nothing changed: no write until the refresh is due.
        mono.now += rt.REFRESH_INTERVAL - 1
        assert publisher.tick() is False
        mono.now += 1
        assert publisher.tick() is True
        assert cache.set.call_count == 2

        # A burst of changes is written at most once per MIN_INTERVAL.
        states.set_state("a", PluginState.LOADED)
        mono.now += 1
        assert publisher.tick() is False
        mono.now += rt.MIN_INTERVAL
        assert publisher.tick() is True
        assert cache.set.call_count == 3

    def test_updates_alone_cause_no_writes(self):
        """A display running plugins but changing nothing writes once a
        minute, however many update() calls it makes."""
        cache = MagicMock()
        states = PluginStateManager()
        states.set_state("clock", PluginState.ENABLED)
        mono = FakeClock()
        publisher = _publisher(cache, states, mono=mono)
        publisher.tick()
        for _ in range(11):  # 55 s of 5 s ticks
            states.set_state("clock", PluginState.RUNNING)
            states.set_state("clock", PluginState.ENABLED)
            mono.now += rt.TICK_INTERVAL
            publisher.tick()
        assert cache.set.call_count == 1

    def test_errors_are_redacted_and_short(self):
        cache = MagicMock()
        states = PluginStateManager()
        secret = "https://api.example.com/v1?api_key=SUPERSECRET123&q=" + "x" * 500
        states.set_state("weather", PluginState.ERROR, error=ConnectionError(secret))

        _publisher(cache, states).tick()

        error = cache.set.call_args.args[1]["plugins"]["weather"]["error"]
        assert "SUPERSECRET123" not in json.dumps(error)
        assert len(error["message"]) <= 200
        assert error["type"] == "ConnectionError"
        assert isinstance(error["at"], float)
        assert error["recoverable"] is False

    def test_stop_publishes_stopped(self):
        cache = MagicMock()
        states = PluginStateManager()
        states.record_loaded("clock", "1.0.0")
        publisher = _publisher(cache, states)
        publisher.tick()

        publisher.stop()

        snapshot = cache.set.call_args.args[1]
        assert snapshot["running"] is False
        assert snapshot["plugins"] == {}

    def test_a_failing_cache_never_raises(self):
        cache = MagicMock()
        cache.set.side_effect = OSError("read-only file system")
        publisher = _publisher(cache, PluginStateManager())
        assert publisher.tick() is False
        publisher.stop()  # also swallowed

    def test_the_thread_starts_and_stops(self):
        cache = MagicMock()
        publisher = rt.start_plugin_runtime_publisher(cache, PluginStateManager())
        try:
            assert publisher is not None
        finally:
            publisher.stop()
        assert cache.set.call_args.args[1]["running"] is False


# --- Reading: live, stale, stopped, unknown ---------------------------------

def _snapshot(published_at, running=True, plugins=None, **extra):
    base = {"schema": rt.SNAPSHOT_SCHEMA, "running": running,
            "published_at": published_at, "stale_after": 180.0,
            "plugins": plugins or {}}
    base.update(extra)
    return base


class TestReader:
    NOW = 1_800_000_000.0
    CLOCK = {"loaded": True, "state": "enabled", "error": None,
             "version": "1.0.0", "loaded_at": 5.0}

    def test_live(self):
        view = view_from_snapshot(_snapshot(self.NOW - 30, plugins={"clock": self.CLOCK}),
                                  now=self.NOW)
        assert view.status == "live"
        assert view.plugin("clock") == {"loaded": True, "state": "enabled",
                                        "error_info": None, "loaded_version": "1.0.0",
                                        "loaded_at": 5.0}
        # Listed nowhere in a live snapshot: not loaded.
        assert view.plugin("weather")["loaded"] is False
        assert view.plugin("weather")["state"] == "unloaded"
        assert view.describe()["age_seconds"] == 30.0

    def test_stale_reports_nothing(self):
        view = view_from_snapshot(_snapshot(self.NOW - 181, plugins={"clock": self.CLOCK}),
                                  now=self.NOW)
        assert view.status == "stale"
        assert view.plugin("clock") == {"loaded": None, "state": None, "error_info": None,
                                        "loaded_version": None, "loaded_at": None}
        assert view.describe()["status"] == "stale"

    def test_stopped_reports_nothing(self):
        view = view_from_snapshot(_snapshot(self.NOW - 1, running=False), now=self.NOW)
        assert view.status == "stopped"
        assert view.plugin("clock")["loaded"] is None

    @pytest.mark.parametrize("snapshot", [
        None, "junk", [], {}, {"schema": 99, "running": True, "published_at": NOW},
        _snapshot(None), _snapshot("yesterday"), _snapshot(float("nan")),
    ])
    def test_unknown(self, snapshot):
        view = view_from_snapshot(snapshot, now=self.NOW)
        assert view.status == "unknown"
        assert view.plugin("clock")["state"] is None

    def test_a_little_in_the_future_is_live_far_is_stale(self):
        """The Pi has no RTC: its clock steps at NTP sync."""
        assert view_from_snapshot(_snapshot(self.NOW + 60), now=self.NOW).status == "live"
        assert view_from_snapshot(_snapshot(self.NOW + 3600), now=self.NOW).status == "stale"

    def test_a_corrupt_stale_after_is_bounded(self):
        forever = _snapshot(self.NOW - 7200, stale_after=1e12)
        assert view_from_snapshot(forever, now=self.NOW).status == "stale"
        eager = _snapshot(self.NOW - 20, stale_after=0)
        assert view_from_snapshot(eager, now=self.NOW).status == "live"

    # -- render-loop liveness (the heartbeat /api/v3/health reads) --

    MONO = 50_000.0

    def _beat(self, age, pid=4242):
        return {"pid": pid, "mono": self.MONO - age, "wall": self.NOW - age}

    def test_a_stale_heartbeat_with_a_live_snapshot_is_stalled(self):
        """The publisher thread keeps writing while the render loop is
        hung; the heartbeat says so, and the runtime status must agree with
        /api/v3/health's display_loop: stalled."""
        snapshot = _snapshot(self.NOW - 5, plugins={"clock": self.CLOCK}, pid=4242)
        view = view_from_snapshot(
            snapshot, now=self.NOW, now_mono=self.MONO,
            heartbeat=self._beat(display_watchdog.HEARTBEAT_STALE_SECONDS + 1))
        assert view.status == "stalled"
        assert view.plugin("clock")["loaded"] is None  # no frozen truth passed on
        described = view.describe()
        assert described["status"] == "stalled"
        assert described["heartbeat_age_seconds"] == display_watchdog.HEARTBEAT_STALE_SECONDS + 1

    def test_the_threshold_is_the_health_checks(self):
        snapshot = _snapshot(self.NOW - 5, pid=4242)
        limit = display_watchdog.HEARTBEAT_STALE_SECONDS
        fresh = view_from_snapshot(snapshot, now=self.NOW, now_mono=self.MONO,
                                   heartbeat=self._beat(limit - 0.5))
        assert fresh.status == "live"
        assert fresh.describe()["heartbeat_age_seconds"] == limit - 0.5
        assert view_from_snapshot(snapshot, now=self.NOW, now_mono=self.MONO,
                                  heartbeat=self._beat(limit)).status == "stalled"

    @pytest.mark.parametrize("heartbeat", [
        None,                                   # dev server, Windows, starting up
        {"pid": 9999, "mono": 0.0},             # another process: a restarted display
        {"pid": "4242", "mono": 0.0},           # unparseable pid
        {"pid": 4242},                          # no time in it
    ])
    def test_a_heartbeat_that_says_nothing_leaves_it_live(self, heartbeat):
        snapshot = _snapshot(self.NOW - 5, plugins={"clock": self.CLOCK}, pid=4242)
        view = view_from_snapshot(snapshot, now=self.NOW, now_mono=self.MONO,
                                  heartbeat=heartbeat)
        assert view.status == "live"
        assert view.plugin("clock")["loaded"] is True

    def test_a_stale_snapshot_stays_stale_whatever_the_heartbeat(self):
        snapshot = _snapshot(self.NOW - 181, pid=4242)
        assert view_from_snapshot(snapshot, now=self.NOW, now_mono=self.MONO,
                                  heartbeat=self._beat(500)).status == "stale"

    def test_a_snapshot_from_a_dead_process_is_stale_at_once(self):
        """After a watchdog kill systemd removes the heartbeat's directory, so
        nothing goes stale; the publisher's pid being gone is the signal."""
        snapshot = _snapshot(self.NOW - 5, plugins={"clock": self.CLOCK}, pid=4242)
        dead = view_from_snapshot(snapshot, now=self.NOW,
                                  process_alive=lambda pid: False)
        assert dead.status == "stale"
        assert dead.plugin("clock")["loaded"] is None
        for answer in (True, None):  # alive, or this platform cannot tell
            assert view_from_snapshot(snapshot, now=self.NOW,
                                      process_alive=lambda pid, a=answer: a).status == "live"

    def test_process_exists(self):
        if os.name != "posix":
            assert rt.process_exists(os.getpid()) is None
        else:
            assert rt.process_exists(os.getpid()) is True
        assert rt.process_exists(0) is None

    def test_no_cache_manager_or_a_failing_one_is_unknown(self):
        assert read_plugin_runtime(None).status == "unknown"
        cache = MagicMock()
        cache.get.side_effect = OSError("gone")
        assert read_plugin_runtime(cache).status == "unknown"


# --- Across processes, through the real cache and the real routes -----------

class TestAcrossProcesses:
    def test_what_the_display_publishes_the_web_reads(self, shared_cache):
        display_cache, web_cache = shared_cache
        states = PluginStateManager()
        states.set_state("clock", PluginState.ENABLED)
        states.record_loaded("clock", "1.0.0")
        publisher = PluginRuntimePublisher(display_cache, states)

        assert read_plugin_runtime(web_cache).status == "unknown"
        publisher.tick()
        view = read_plugin_runtime(web_cache)
        assert view.status == "live"
        assert view.plugin("clock")["loaded"] is True

        # memory_ttl=0 on the reading side: a later write is seen at once.
        publisher.stop()
        assert read_plugin_runtime(web_cache).status == "stopped"

    def test_a_snapshot_left_by_a_dead_display_goes_stale(self, shared_cache):
        display_cache, web_cache = shared_cache
        states = PluginStateManager()
        states.record_loaded("clock", "1.0.0")
        old = FakeClock(1_000_000.0)  # long ago
        PluginRuntimePublisher(display_cache, states, wall_clock=old).tick()
        assert read_plugin_runtime(web_cache).status == "stale"


@pytest.fixture
def web_listing(api_v3_module, api_v3_client, shared_cache, tmp_path):  # noqa: F811
    """GET /api/v3/plugins/installed over one real plugin and the shared cache."""
    display_cache, web_cache = shared_cache
    api = api_v3_module.api_v3
    api.cache_manager = web_cache
    api.plugin_catalog.plugins_dir = str(tmp_path / "plugins")
    api.plugin_catalog.get_all_plugin_info = MagicMock(return_value=[
        {"id": "clock", "name": "Clock", "version": "1.1.0"},
        {"id": "weather", "name": "Weather", "version": "3.0.0"},
    ])
    api.plugin_store_manager.get_cached_registry_info = MagicMock(return_value=None)
    api.plugin_store_manager._get_local_git_info = MagicMock(return_value=None)
    api.config_manager.load_config = MagicMock(return_value={
        "clock": {"enabled": True}, "weather": {"enabled": True}})

    def get():
        response = api_v3_client.get("/api/v3/plugins/installed")
        assert response.status_code == 200, response.get_data(as_text=True)
        data = response.get_json()["data"]
        return {p["id"]: p for p in data["plugins"]}, data["runtime"]

    return display_cache, get


class TestInstalledPluginsRoute:
    def _display(self, display_cache):
        states = PluginStateManager()
        states.set_state("clock", PluginState.ENABLED)
        states.record_loaded("clock", "1.0.0", loaded_at=123.0)
        states.set_state("weather", PluginState.ERROR,
                         error=RuntimeError("token=abc123 rejected"))
        return PluginRuntimePublisher(display_cache, states)

    def test_restores_loaded_state_and_error_info(self, web_listing):
        display_cache, get = web_listing
        self._display(display_cache).tick()

        plugins, runtime = get()

        assert runtime["status"] == "live"
        clock, weather = plugins["clock"], plugins["weather"]
        assert (clock["loaded"], clock["state"], clock["error_info"]) == (True, "enabled", None)
        # The display runs 1.0.0; 1.1.0 is on disk (an update awaiting restart).
        assert clock["version"] == "1.1.0" and clock["loaded_version"] == "1.0.0"
        assert clock["loaded_at"] == 123.0
        assert weather["loaded"] is False and weather["state"] == "error"
        assert weather["error_info"]["type"] == "RuntimeError"
        assert "abc123" not in weather["error_info"]["message"]

    def test_unknown_before_the_display_has_published(self, web_listing):
        _, get = web_listing
        plugins, runtime = get()
        assert runtime["status"] == "unknown"
        assert all(p["loaded"] is None and p["state"] is None and p["error_info"] is None
                   for p in plugins.values())
        # enabled still comes from config.json.
        assert plugins["clock"]["enabled"] is True

    def test_stopped_display_is_not_reported_as_running(self, web_listing):
        display_cache, get = web_listing
        publisher = self._display(display_cache)
        publisher.tick()
        publisher.stop()

        plugins, runtime = get()

        assert runtime["status"] == "stopped"
        assert plugins["clock"]["loaded"] is None

    def test_stale_snapshot_is_not_reported_as_truth(self, web_listing):
        display_cache, get = web_listing
        states = PluginStateManager()
        states.record_loaded("clock", "1.0.0")
        PluginRuntimePublisher(display_cache, states,
                               wall_clock=FakeClock(1_000_000.0)).tick()

        plugins, runtime = get()

        assert runtime["status"] == "stale"
        assert runtime["age_seconds"] > rt.STALE_AFTER
        assert plugins["clock"]["loaded"] is None

    def _write_heartbeat(self, tmp_path, monkeypatch, age):
        path = tmp_path / "display-heartbeat.json"
        path.write_text(json.dumps({"pid": os.getpid(), "mono": time.monotonic() - age,
                                    "wall": time.time() - age}), encoding="utf-8")
        monkeypatch.setattr(display_watchdog, "HEARTBEAT_PATH", str(path))

    def test_hung_render_loop_is_stalled_not_live(self, web_listing, tmp_path, monkeypatch):
        """The publisher's own thread still ticks while the render loop is
        stuck; the heartbeat it shares a process with has gone stale."""
        display_cache, get = web_listing
        self._display(display_cache).tick()   # a fresh snapshot, this pid
        self._write_heartbeat(tmp_path, monkeypatch,
                              display_watchdog.HEARTBEAT_STALE_SECONDS + 30)

        plugins, runtime = get()

        assert runtime["status"] == "stalled"
        assert runtime["heartbeat_age_seconds"] >= display_watchdog.HEARTBEAT_STALE_SECONDS
        assert plugins["clock"]["loaded"] is None and plugins["clock"]["state"] is None

    def test_fresh_heartbeat_keeps_it_live(self, web_listing, tmp_path, monkeypatch):
        display_cache, get = web_listing
        self._display(display_cache).tick()
        self._write_heartbeat(tmp_path, monkeypatch, 1)

        plugins, runtime = get()

        assert runtime["status"] == "live"
        assert plugins["clock"]["loaded"] is True


class TestDisplayControllerStopsThePublisher:
    def test_cleanup_publishes_stopped(self, monkeypatch):
        # display_manager binds the hardware rgbmatrix module unless
        # EMULATOR=true is set before import (test_initial_update_budget.py).
        monkeypatch.setenv("EMULATOR", "true")
        from src.display_controller import DisplayController
        controller = DisplayController.__new__(DisplayController)
        publisher = MagicMock()
        controller._plugin_runtime_publisher = publisher
        controller.plugin_manager = None
        controller.vegas_coordinator = None
        controller.sync_manager = None
        controller.display_manager = MagicMock()
        controller.cleanup()
        publisher.stop.assert_called_once_with()
