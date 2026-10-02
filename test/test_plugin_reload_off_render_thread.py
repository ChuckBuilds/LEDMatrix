"""A ``plugin.reload`` never stalls the render thread (#720 follow-up).

On ledpi a reload of football-scoreboard (a store update, 3.17.0 to 3.18.1)
arrived while Vegas mode was running, and the panel froze for 3.0 s
("Render stall over: no frame for 3043ms"). Vegas yielded at once, but the
reload ran on the render thread, and its first step, unload_plugin(), waited
for the plugin's lock. The strip's prefetch thread held that lock: it was
rebuilding the old instance's Vegas content, which takes seconds for a
scoreboard. The new instance was then loaded on the render thread too
(0.47 s on ledpi).

Now the render thread only takes the plugin out of the rotation and out of
the plugin manager (detach_plugin), and a plugin-reload thread waits for the
lock, tears the old instance down and loads the new one. The new instance
joins the rotation between two frames.

* Real threads, real PluginManager and PluginAdapter: frames keep coming
  (no gap over 100 ms) while a slow Vegas render holds the old instance's
  lock, and the reload still reports the real outcome. Before the fix the
  gap was the whole render.
* The fake-clock run loop (test/_run_loop_harness.py): Vegas frames flow
  through a reload whose thread takes 3 s, the ticker never leaves, and
  the reload is answered when the new instance joins.
* Thread safety: the old instance is torn down only after its render let
  go of the lock, is never asked for content again, and never gets an
  update() once the reload starts; the new one is loaded once.
* The other reload cases still answer as before: a plugin that is on
  screen, one that is not, one that fails to load, and a second reload of
  a plugin that is still loading.
"""

import json
import os
import threading
import time
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from PIL import Image

os.environ.setdefault("EMULATOR", "true")

from src.ipc.contract import Command, ErrorCode, PluginReloadArgs  # noqa: E402
from src.ipc.server import CommandOutcome, QueuedCommand  # noqa: E402
from src.plugin_system.base_plugin import BasePlugin  # noqa: E402
from src.plugin_system.plugin_manager import PluginManager  # noqa: E402
from src.vegas_mode.config import VegasModeConfig  # noqa: E402
from src.vegas_mode.plugin_adapter import PluginAdapter  # noqa: E402
from test._run_loop_harness import FakePlugin, RunLoopHarness  # noqa: E402
from test.test_vegas_elements_api import _DM  # noqa: E402

#: How long the old instance's Vegas render holds its lock. ledpi's was 3 s
#: (scripts in the PR body measure that); the gap scales with it.
RENDER_SECONDS = 1.5
FRAME = 0.008
#: "A frame or two", with room for a slow CI runner.
MAX_GAP = 0.1


def _reload(plugin_id, request_id="r1"):
    return QueuedCommand(request_id=request_id, cmd=Command.PLUGIN_RELOAD,
                         args=PluginReloadArgs(plugin_id), received_at=time.time(),
                         outcome=CommandOutcome())


class _Server:
    """The ControlServer surface the controller drains."""

    def __init__(self):
        self.commands = []

    @property
    def has_pending(self):
        return bool(self.commands)

    def drain(self):
        out, self.commands = self.commands, []
        return out

    def close(self):
        pass


class _Rig:
    """A real PluginManager running one slow-rendering plugin, 'slow'."""

    def __init__(self, tmp_path, render_seconds):
        self.events = []
        self.lock = threading.Lock()
        self.render_started = threading.Event()
        self.render_seconds = render_seconds
        self.generation = 0
        rig = self

        class SlowVegas(BasePlugin):
            """Draws its Vegas content slowly, as a scoreboard does on a Pi."""

            def __init__(self, generation):  # pylint: disable=super-init-not-called
                self.plugin_id = "slow"
                self.config = {"enabled": True}
                self.enabled = True
                self.modes = ["slow"]
                self.plugin_manager = None
                self.generation = generation

            def get_update_interval(self):
                return 0.05

            def update(self):
                rig.log("update", self.generation)

            def display(self, force_clear=False):
                pass

            def validate_config(self):
                return True

            def get_vegas_content(self):
                rig.log("render-start", self.generation)
                rig.render_started.set()
                time.sleep(rig.render_seconds if self.generation == 1 else 0)
                rig.log("render-end", self.generation)
                return [Image.new("RGB", (64, 16), (255, 0, 0))]

            def cleanup(self):
                rig.log("cleanup", self.generation)

            def on_enable(self):
                pass

            def on_disable(self):
                pass

        def construct(**_kw):
            rig.generation += 1
            return SlowVegas(rig.generation), None

        plugin_dir = tmp_path / "plugins" / "slow"
        plugin_dir.mkdir(parents=True)
        self.manifest_path = plugin_dir / "manifest.json"
        manifest = {"id": "slow", "version": "1.0.0", "display_modes": ["slow"]}
        self.manifest_path.write_text(json.dumps(manifest))
        pm = PluginManager(plugins_dir=str(tmp_path / "plugins"))
        pm.plugin_manifests["slow"] = manifest
        pm.schema_manager = MagicMock()
        pm.schema_manager.get_schema_path.return_value = None
        pm.schema_manager.prepare_plugin_config.side_effect = (
            lambda pid, cfg, schema=None, changed_paths=None: cfg)
        pm.plugin_loader = MagicMock()
        pm.plugin_loader.find_plugin_directory.return_value = plugin_dir
        pm.plugin_loader.load_plugin.side_effect = construct
        assert pm.load_plugin("slow")
        self.pm = pm
        self.adapter = PluginAdapter(_DM(), VegasModeConfig(), plugin_manager=pm)
        self.prefetched = []

    def log(self, kind, generation):
        with self.lock:
            self.events.append((kind, generation))

    def index(self, kind, generation):
        return self.events.index((kind, generation))

    def start_vegas_render(self):
        """The strip's prefetch thread rebuilding the old instance's content."""
        plugin = self.pm.plugins["slow"]

        def fetch():
            self.prefetched.append(self.adapter.get_content(plugin, "slow", offscreen_only=True))
        thread = threading.Thread(target=fetch, name="vegas-strip-prefetch", daemon=True)
        thread.start()
        assert self.render_started.wait(2)
        return thread


@pytest.fixture
def dc(test_display_controller):
    c = test_display_controller
    c.display_manager.set_brightness = MagicMock(return_value=True)
    c.display_manager.update_display = MagicMock()
    c.config = {"timezone": "UTC", "display": {"hardware": {"brightness": 90}}}
    c._normal_brightness = 90
    c.current_brightness = 90
    c.is_display_active = True
    c._tz = None
    c.vegas_coordinator = MagicMock()
    return c


def _with_rig(dc, tmp_path, render_seconds=None):
    rig = _Rig(tmp_path, RENDER_SECONDS if render_seconds is None else render_seconds)
    clock = SimpleNamespace(modes=["clock"])
    dc.plugin_manager = rig.pm
    dc.plugin_display_modes = {"clock": ["clock"]}
    dc.available_modes = ["clock"]
    dc.plugin_modes = {"clock": clock}
    dc.mode_to_plugin_id = {"clock": "clock"}
    dc._register_loaded_plugin("slow")
    dc.current_mode_index = 0
    dc.current_display_mode = "clock"
    dc._control_server = _Server()
    return rig


def _render_frames(dc, rig, until, limit):
    """What the render thread does while Vegas runs: one frame every 8 ms,
    servicing pending changes between frames (Vegas's interrupt check) and,
    when a screen-ending command is pending, the top of the loop pass.
    Returns the frame times."""
    frames = [time.perf_counter()]
    deadline = frames[0] + limit
    while not until():
        dc._service_pending_changes()
        if dc._plugin_reload_pending:
            dc._apply_pending_plugin_reloads()     # Vegas yields; resumes next pass
        if len(frames) % 5 == 0:
            rig.pm.run_scheduled_updates()          # the update tick
        time.sleep(FRAME)
        frames.append(time.perf_counter())
        assert frames[-1] < deadline, "the reload never finished"
    return frames


def _max_gap(frames):
    return max(b - a for a, b in zip(frames, frames[1:]))


class TestFramesKeepFlowing:
    def test_a_reload_during_a_slow_vegas_render(self, dc, tmp_path):
        rig = _with_rig(dc, tmp_path)
        old = rig.pm.plugins["slow"]
        prefetch = rig.start_vegas_render()
        rig.manifest_path.write_text(json.dumps(
            {"id": "slow", "version": "2.0.0", "display_modes": ["slow"]}))

        command = _reload("slow")
        posted = time.perf_counter()
        dc._control_server.commands.append(command)
        frames = _render_frames(dc, rig, lambda: command.outcome.done, limit=10)
        answered = time.perf_counter() - posted
        prefetch.join(2)
        rig.pm.stop_update_worker()

        gap = _max_gap(frames)
        print(f"\nlongest frame gap {gap * 1000:.0f} ms, answered after {answered:.2f} s "
              f"(the old instance's render held its lock {RENDER_SECONDS:.1f} s)")
        assert gap < MAX_GAP
        # The real outcome, once the new instance is in the rotation.
        assert command.outcome.result == {"plugin_id": "slow", "reloaded": True,
                                          "version": "2.0.0", "modes": ["slow"]}
        new = rig.pm.plugins["slow"]
        assert new is not old and new.generation == 2
        assert dc.plugin_modes["slow"] is new
        assert dc.available_modes == ["clock", "slow"]
        assert dc.current_display_mode == "clock"
        dc.vegas_coordinator.mark_plugin_updated.assert_called_once_with("slow")
        # The old instance finished its render before it was torn down, and
        # the strip got that render.
        assert rig.index("render-end", 1) < rig.index("cleanup", 1)
        assert rig.prefetched and rig.prefetched[0]
        # Loaded once; no update() of the old instance once it was torn
        # down, and none of the new one before.
        assert rig.generation == 2
        cleanup = rig.index("cleanup", 1)
        assert all(i < cleanup for i, e in enumerate(rig.events) if e == ("update", 1))
        assert all(i > cleanup for i, e in enumerate(rig.events) if e == ("update", 2))

    def test_a_fetch_that_waited_out_the_reload_skips_the_old_instance(self, tmp_path):
        """A Vegas fetch that looked the old instance up before the reload,
        and got its lock only after the teardown, does not run it."""
        rig = _Rig(tmp_path, 0)
        old = rig.pm.detach_plugin("slow")
        assert rig.adapter.get_content(old, "slow", offscreen_only=True) is None
        rig.pm.unload_detached_plugin("slow", old)
        assert rig.pm.reload_plugin("slow")
        assert rig.adapter.get_content(old, "slow", offscreen_only=True) is None
        assert ("render-start", 1) not in rig.events
        # The new instance is fetched as usual.
        assert rig.adapter.get_content(rig.pm.plugins["slow"], "slow", offscreen_only=True)
        assert ("render-start", 2) in rig.events


class TestPluginManagerDetach:
    def test_teardown_waits_for_the_lock_and_leaves_plugins_alone(self, tmp_path):
        rig = _Rig(tmp_path, 0)
        pm = rig.pm
        old = pm.detach_plugin("slow")
        assert "slow" not in pm.plugins and old.generation == 1
        lock = pm.get_plugin_lock("slow")
        lock.acquire()
        done = threading.Event()
        threading.Thread(target=lambda: (pm.unload_detached_plugin("slow", old), done.set()),
                         daemon=True).start()
        assert not done.wait(0.2)            # a render of the old instance holds it
        assert ("cleanup", 1) not in rig.events
        lock.release()
        assert done.wait(5)
        assert ("cleanup", 1) in rig.events
        assert pm.reload_plugin("slow")
        assert pm.plugins["slow"].generation == 2

    def test_detaching_a_plugin_that_is_not_loaded(self, tmp_path):
        pm = _Rig(tmp_path, 0).pm
        assert pm.detach_plugin("nope") is None


# -- the run loop on the fake clock ----------------------------------------

POSTED = 10.3


def _with_reloadable(h, version="2.0.0", loads=True):
    new = FakePlugin("clock", ["clock"], duration=30)

    def reload_plugin(plugin_id):
        h.log("reload", plugin_id)
        if not loads:
            return False
        new._h = h
        h.pm.plugins[plugin_id] = new
        h.pm.plugin_manifests[plugin_id] = {"version": version, "display_modes": ["clock"]}
        return True

    h.pm.reload_plugin = reload_plugin
    return new


def _vegas(h):
    h.add_plugin(FakePlugin("clock", ["clock"], duration=30))
    h.add_plugin(FakePlugin("weather", ["weather"], duration=30))
    h.enable_vegas(cycle=30)


def _hold_lock(h, plugin_id, seconds):
    """A Vegas render of ``plugin_id`` holds its lock for ``seconds`` from
    when the reload is asked for: the render thread would block that long in
    unload_plugin(), and the plugin-reload thread does instead."""
    real_unload = h.pm.unload_plugin

    def unload_plugin(pid):
        if pid == plugin_id:
            h.clock.sleep(max(0.0, POSTED + seconds - h.clock.rel()))
        return real_unload(pid)
    h.pm.unload_plugin = unload_plugin
    h.reload_seconds = seconds


def _vegas_frame_gaps(h):
    times = [e[0] for e in h.events if e[1] == "vegas-frame"]
    return max(b - a for a, b in zip(times, times[1:]))


def test_vegas_keeps_scrolling_through_a_slow_reload(tmp_path):
    h = RunLoopHarness(tmp_path, horizon=40)
    _vegas(h)
    new = _with_reloadable(h)
    _hold_lock(h, "clock", 3.0)
    command = h.control_socket().post(POSTED, Command.PLUGIN_RELOAD, {"plugin_id": "clock"})
    trace = h.run()

    # Frames every 8 ms throughout: before the fix, none for 3 s.
    assert _vegas_frame_gaps(h) <= 0.1
    # The new instance loaded 3 s later, on its own thread, and joined then.
    reload_at = next(e[0] for e in h.events if e[1] == "reload")
    assert reload_at == pytest.approx(POSTED + 3.0, abs=0.01)
    assert command.outcome.result == {"plugin_id": "clock", "reloaded": True,
                                      "version": "2.0.0", "modes": ["clock"]}
    assert h.controller.plugin_modes["clock"] is new
    assert h.controller.available_modes == ["clock", "weather"]
    # Only the start of the reload ended a Vegas iteration; the ticker
    # carried on through it.
    assert all(r[1] == "<vegas>" for r in trace["screens"] if r[0] >= POSTED)


def test_a_reload_while_on_screen_lets_the_rotation_carry_on(tmp_path):
    h = RunLoopHarness(tmp_path, horizon=70)
    h.add_plugin(FakePlugin("clock", ["clock"], duration=30))
    h.add_plugin(FakePlugin("weather", ["weather"], duration=30))
    new = _with_reloadable(h)
    _hold_lock(h, "clock", 3.0)
    command = h.control_socket().post(POSTED, Command.PLUGIN_RELOAD, {"plugin_id": "clock"})
    trace = h.run()

    # The clock screen ends when the reload is asked for, weather shows
    # while the clock loads, and the reloaded clock comes round next.
    assert trace["screens"][0][:3] == [0.0, "clock", POSTED]
    assert trace["screens"][1][:2] == [POSTED, "weather"]
    assert trace["screens"][2][1] == "clock"
    assert command.outcome.result["reloaded"] is True
    assert h.controller.plugin_modes["clock"] is new


def test_a_reload_of_a_plugin_not_on_screen_keeps_its_place(tmp_path):
    h = RunLoopHarness(tmp_path, horizon=70)
    h.add_plugin(FakePlugin("clock", ["clock"], duration=30))
    h.add_plugin(FakePlugin("weather", ["weather"], duration=30))
    h.add_plugin(FakePlugin("news", ["news"], duration=30))
    new_weather = FakePlugin("weather", ["weather"], duration=30)

    def reload_plugin(plugin_id):
        h.log("reload", plugin_id)
        new_weather._h = h
        h.pm.plugins[plugin_id] = new_weather
        h.pm.plugin_manifests[plugin_id] = {"version": "2.0.0"}
        return True
    h.pm.reload_plugin = reload_plugin
    h.reload_seconds = 3.0
    command = h.control_socket().post(POSTED, Command.PLUGIN_RELOAD, {"plugin_id": "weather"})
    h.run()

    assert command.outcome.result["reloaded"] is True
    assert h.controller.available_modes == ["clock", "weather", "news"]
    assert h.controller.plugin_modes["weather"] is new_weather


def test_a_plugin_that_fails_to_load_after_a_slow_teardown(tmp_path):
    h = RunLoopHarness(tmp_path, horizon=40)
    _vegas(h)
    _with_reloadable(h, loads=False)
    _hold_lock(h, "clock", 3.0)
    command = h.control_socket().post(POSTED, Command.PLUGIN_RELOAD, {"plugin_id": "clock"})
    h.run()

    assert _vegas_frame_gaps(h) <= 0.1
    assert command.outcome.error_code == ErrorCode.FAILED
    assert "clock" not in h.controller.available_modes
    assert "clock" not in h.pm.plugins


def test_a_failed_teardown_stops_the_reload(tmp_path):
    """Loading over a half-unloaded plugin could reuse its old module and
    report a reload that never happened; report the failure instead."""
    h = RunLoopHarness(tmp_path, horizon=40)
    _vegas(h)
    reloads = []
    h.pm.unload_detached_plugin = lambda plugin_id, instance: False
    h.pm.reload_plugin = lambda plugin_id: reloads.append(plugin_id) or True
    command = h.control_socket().post(POSTED, Command.PLUGIN_RELOAD, {"plugin_id": "clock"})
    h.run()

    assert reloads == []
    assert command.outcome.error_code == ErrorCode.FAILED
    assert "restart the display" in command.outcome.error_message
    assert "clock" not in h.controller.available_modes
    assert _vegas_frame_gaps(h) <= 0.1


def test_a_second_reload_while_loading_runs_after_the_first(tmp_path):
    h = RunLoopHarness(tmp_path, horizon=40)
    _vegas(h)
    loads = []

    def reload_plugin(plugin_id):
        plugin = FakePlugin("clock", ["clock"], duration=30)
        plugin._h = h
        loads.append(plugin)
        h.log("reload", plugin_id)
        h.pm.plugins[plugin_id] = plugin
        h.pm.plugin_manifests[plugin_id] = {"version": f"{len(loads)}.0.0"}
        return True
    h.pm.reload_plugin = reload_plugin
    h.reload_seconds = 3.0
    server = h.control_socket()
    first = server.post(POSTED, Command.PLUGIN_RELOAD, {"plugin_id": "clock"}, "a")
    second = server.post(POSTED + 1, Command.PLUGIN_RELOAD, {"plugin_id": "clock"}, "b")
    h.run()

    # The second starts once the first has joined the rotation (within a
    # quarter second of loading), and takes its own 3 s.
    loaded = [e[0] for e in h.events if e[1] == "reload"]
    assert len(loaded) == 2
    assert loaded[0] == pytest.approx(POSTED + 3.0, abs=0.05)
    assert 3.0 <= loaded[1] - loaded[0] <= 3.5
    assert first.outcome.result["version"] == "1.0.0"
    assert second.outcome.result["version"] == "2.0.0"
    assert h.controller.plugin_modes["clock"] is loads[-1]
    assert _vegas_frame_gaps(h) <= 0.1


def test_a_reconcile_during_a_reload_does_not_load_it_twice(dc):
    """The config watcher sees an enabled plugin missing from the rotation
    while it reloads; the reconcile must not load it beside the reload."""
    pm = MagicMock()
    pm.discover_plugins.return_value = ["slow"]
    dc.plugin_manager = pm
    dc.config_service = MagicMock()
    dc.config_service.get_config.return_value = {"slow": {"enabled": True}}
    dc.plugin_display_modes = {}
    dc._plugin_reload_jobs = (SimpleNamespace(plugin_id="slow"),)
    assert dc._reconcile_enabled_plugins() is True
    pm.load_plugin.assert_not_called()
    assert not dc._enabled_plugin_not_running({"slow": {"enabled": True}})

    # Disabled while it reloads: unloaded by a reconcile after the reload.
    dc.config_service.get_config.return_value = {"slow": {"enabled": False}}
    assert dc._reconcile_enabled_plugins() is True
    pm.unload_plugin.assert_not_called()
    assert dc._reconcile_after_reload is True


def test_on_demand_for_a_reloading_plugin_is_refused(dc):
    dc._plugin_reload_jobs = (SimpleNamespace(plugin_id="slow"),)
    dc._load_plugin_for_on_demand = MagicMock()
    dc._activate_on_demand({"plugin_id": "slow"})
    dc._load_plugin_for_on_demand.assert_not_called()
    assert dc.on_demand_last_error == "plugin-reloading"
