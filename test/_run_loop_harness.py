"""Drive the real DisplayController.run() on a fake clock and record a trace.

The golden trace tests (test_run_loop_golden.py) use this to pin down what
run() does today -- which mode is on the panel, for how long, and why it
left -- so that the loop can be restructured (docs/RUN_LOOP_REDESIGN.md)
without changing any of it.

What is real and what is fake
-----------------------------
Real: DisplayController itself (constructed through __init__, then run()),
PluginExecutor (each screen's first frame still goes through its thread),
the per-plugin display locks, and every controller method run() calls.

Fake, so the run is deterministic and takes milliseconds:

* the clock -- ``src.display_controller.time`` and ``datetime`` are replaced
  by one FakeClock; sleeping only advances it. Scripted events (an on-demand
  request, a WiFi notice, live content starting) fire as it passes them.
* plugins -- FakePlugin, whose content, liveness and dynamic-duration answers
  are functions of the fake clock.
* the plugin manager, cache, config service, display manager and sync
  manager -- in-memory stand-ins with no threads.
* the Vegas coordinator -- FakeVegas implements only the contract the
  controller relies on (run_iteration() returning True when it ran its
  duration and False when interrupted, the interrupt and live checks it
  calls back into). The real coordinator spawns threads and renders a strip;
  driving it on the fake clock is part of stage 4 (Vegas as a Source).

The run ends when the fake clock passes the scenario's horizon: the clock
raises StopRun, a BaseException, which run()'s ``except Exception`` lets
through after its ``finally`` has run cleanup().

How the trace is read
---------------------
Everything observable is appended to one ordered event log. reduce_trace()
folds it into screens: a screen starts at the first display() call of a
loop pass (a "pass" is one call of the watchdog's loop_pass(), at the top of
run()'s loop), or at the first follower / Vegas / WiFi / blank frame. Its
exit reason is the first reason-bearing event logged before the next screen
starts, else ``duration``.
"""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Dict, List, Optional, Tuple
from unittest.mock import MagicMock, patch

from src.common.sync_manager import SyncRole
from src.plugin_system.plugin_executor import PluginExecutor

GOLDEN_DIR = Path(__file__).parent / "fixtures" / "run_loop_golden"

#: Monday 2026-01-05 22:59:30 UTC. The schedule scenario's windows are set
#: around 23:00; every other scenario has no schedule, so the date is moot.
T0 = datetime(2026, 1, 5, 22, 59, 30, tzinfo=timezone.utc).timestamp()

#: Loop passes allowed without the clock moving before the run is called a
#: spin. run() must sleep somewhere on every few passes.
SPIN_LIMIT = 500


class StopRun(BaseException):
    """Ends a harness run. A BaseException so run()'s handlers pass it on."""


class SpinError(BaseException):
    """run() went round SPIN_LIMIT times without the clock moving.

    A BaseException for the same reason as StopRun: run() would log and
    swallow anything less, and the test would see a short trace."""


# ---------------------------------------------------------------------------
# Clock
# ---------------------------------------------------------------------------

class FakeClock:
    """time.time/monotonic/perf_counter all read ``now``; sleep() advances it.

    Alarms are (time, callback) pairs fired, in time order, by the sleep that
    carries the clock past them. Reaching the horizon raises StopRun.
    """

    def __init__(self, start: float, horizon: float):
        self.start = start
        self.now = start
        self.horizon = start + horizon
        self._alarms: List[Tuple[float, int, Callable[[], None]]] = []
        self._seq = 0
        self.passes_since_advance = 0

    def rel(self) -> float:
        return self.now - self.start

    def at(self, t: float, callback: Callable[[], None]) -> None:
        self._alarms.append((self.start + t, self._seq, callback))
        self._seq += 1
        self._alarms.sort()

    def time(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        target = self.now + max(0.0, seconds)
        while self._alarms and self._alarms[0][0] <= target:
            when, _, callback = self._alarms.pop(0)
            self.now = max(self.now, when)
            callback()
        self.now = target
        if seconds > 0:
            self.passes_since_advance = 0
        if self.now >= self.horizon:
            raise StopRun()

    def time_module(self) -> SimpleNamespace:
        return SimpleNamespace(time=self.time, monotonic=self.time,
                               perf_counter=self.time, sleep=self.sleep)

    def datetime_class(self):
        clock = self

        class FakeDateTime(datetime):
            @classmethod
            def now(cls, tz=None):  # type: ignore[override]
                return datetime.fromtimestamp(clock.now, tz or timezone.utc)

        return FakeDateTime


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

class FakeCache:
    """The in-memory slice of CacheManager that run() and its helpers use."""

    def __init__(self):
        self.data: Dict[str, Any] = {}
        self.cache_dir = "/nonexistent/run-loop-harness"

    def get(self, key, max_age=None, memory_ttl=None):
        return self.data.get(key)

    def set(self, key, data, ttl=None):
        self.data[key] = data

    def delete(self, key):
        self.data.pop(key, None)

    def clear_cache(self, key=None):
        if key is None:
            self.data.clear()
        else:
            self.data.pop(key, None)

    def __getattr__(self, name):
        # Anything else (stats, cleanup hooks) is a no-op.
        return lambda *a, **k: None


class FakeConfigService:
    def __init__(self, config):
        self.config = config

    def get_config(self):
        return self.config

    def subscribe(self, *a, **k):
        pass

    def unsubscribe(self, *a, **k):
        pass

    def shutdown(self):
        pass


class FakeSync:
    """A standalone sync manager whose follower state follows the script."""

    role = SyncRole.STANDALONE

    def __init__(self, harness: "RunLoopHarness"):
        self._h = harness
        self.follower_windows: List[Tuple[float, float]] = []

    def is_follower_active(self) -> bool:
        t = self._h.clock.rel()
        return any(a <= t < b for a, b in self.follower_windows)

    def get_latest_scroll_x(self):
        return None

    def get_latest_frame(self):
        return "leader-frame"

    def stop(self):
        pass

    def __getattr__(self, name):
        return lambda *a, **k: None


class FakeHealthTracker:
    """Circuit breaker stand-in: opens after two consecutive failures and
    stays open (no wall-clock cooldown, which would not be deterministic)."""

    def __init__(self, harness: "RunLoopHarness"):
        self._h = harness
        self.failures: Dict[str, int] = {}

    def should_skip_plugin(self, plugin_id):
        skip = self.failures.get(plugin_id, 0) >= 2
        if skip:
            self._h.log("breaker-open", plugin_id, quiet=True)
        return skip

    def record_success(self, plugin_id):
        self.failures[plugin_id] = 0

    def record_failure(self, plugin_id, exc=None):
        self.failures[plugin_id] = self.failures.get(plugin_id, 0) + 1
        self._h.log("health-failure", plugin_id)


class FakePluginManager:
    def __init__(self):
        self.plugins: Dict[str, Any] = {}
        self.plugin_manifests: Dict[str, Any] = {}
        self.plugin_last_update: Dict[str, float] = {}
        self.health_tracker = None
        self.resource_monitor = None
        self.state_manager = None
        self.plugin_executor = PluginExecutor()
        self.no_lock: set = set()
        self._locks: Dict[str, threading.Lock] = {}
        self.hangs: List[str] = []

    def discover_plugins(self):
        return []

    def discovered_plugin_ids(self):
        return set(self.plugins)

    def load_plugin(self, plugin_id, force_enabled=False):
        return False

    def get_plugin(self, plugin_id):
        return self.plugins.get(plugin_id)

    def unload_plugin(self, plugin_id):
        self.plugins.pop(plugin_id, None)
        return True

    def get_plugin_lock(self, plugin_id):
        if plugin_id in self.no_lock:
            return None  # as when loading failed part-way
        return self._locks.setdefault(plugin_id, threading.Lock())

    def record_display_hang(self, plugin_id, seconds):
        self.hangs.append(plugin_id)

    def note_display_duration(self, plugin_id, seconds):
        pass

    def run_scheduled_updates(self):
        pass

    def run_scheduled_updates_with_changes(self):
        return []

    def stop_update_worker(self):
        pass


class FakePlugin:
    """A plugin whose answers are functions of the harness clock.

    Args:
        plugin_id: The plugin id.
        modes: Its display modes, registered in this order.
        duration: get_display_duration().
        content: ``content(t, mode) -> bool``: what display() returns.
            Defaults to always True.
        live: ``(start, end)`` seconds during which has_live_content() is
            True; get_live_modes() then names its modes ending in ``_live``.
        live_priority: has_live_priority().
        dynamic: Enables dynamic duration. Keys: ``cap`` (the plugin's cap),
            ``cycle`` (get_cycle_duration()), ``complete_after`` (seconds
            after reset_cycle_state() that is_cycle_complete() turns True;
            None means never).
        needs_high_fps / enable_scrolling: Set as attributes only when given,
            since run() tests for their presence.
        raises: display() raises RuntimeError.
        first_frame_only: display() returns True on a screen's first frame
            and False on every later one.
    """

    def __init__(self, plugin_id: str, modes: List[str], duration: float = 30,
                 content: Optional[Callable[[float, str], bool]] = None,
                 live: Optional[Tuple[float, float]] = None,
                 live_priority: bool = False,
                 dynamic: Optional[Dict[str, Any]] = None,
                 needs_high_fps: Optional[bool] = None,
                 enable_scrolling: Optional[bool] = None,
                 raises: bool = False,
                 first_frame_only: bool = False):
        self.plugin_id = plugin_id
        self.modes = list(modes)
        self.duration = duration
        self.content = content
        self.live = live
        self.live_priority = live_priority
        self.dynamic = dynamic
        self.raises = raises
        self.first_frame_only = first_frame_only
        if needs_high_fps is not None:
            self.needs_high_fps = needs_high_fps
        if enable_scrolling is not None:
            self.enable_scrolling = enable_scrolling
        self._h: Optional["RunLoopHarness"] = None
        self._reset_at: Optional[float] = None

    # -- display -----------------------------------------------------------
    def display(self, display_mode=None, force_clear=False):
        assert self._h is not None
        return self._h.on_display(self, display_mode or self.modes[0], force_clear)

    def get_display_duration(self):
        return self.duration

    # -- live --------------------------------------------------------------
    def _is_live(self) -> bool:
        if not self.live or self._h is None:
            return False
        t = self._h.clock.rel()
        return self.live[0] <= t < self.live[1]

    def has_live_priority(self):
        return self.live_priority

    def has_live_content(self):
        return self._is_live()

    def get_live_modes(self):
        return [m for m in self.modes if m.endswith("_live")]

    # -- dynamic duration ----------------------------------------------------
    def supports_dynamic_duration(self):
        return bool(self.dynamic)

    def get_dynamic_duration_cap(self):
        return (self.dynamic or {}).get("cap")

    def get_cycle_duration(self, display_mode=None):
        return (self.dynamic or {}).get("cycle")

    def reset_cycle_state(self):
        assert self._h is not None
        self._reset_at = self._h.clock.rel()
        self._h.log("cycle-reset", self.plugin_id)

    def is_cycle_complete(self):
        if not self.dynamic:
            return True
        after = self.dynamic.get("complete_after")
        if after is None or self._reset_at is None or self._h is None:
            return False
        done = self._h.clock.rel() - self._reset_at >= after
        if done:
            self._h.log("cycle-complete", self.plugin_id, quiet=True)
        return done


class LegacyFakePlugin(FakePlugin):
    """display() without a display_mode parameter, as older plugins have."""

    def display(self, force_clear=False):  # type: ignore[override]
        assert self._h is not None
        return self._h.on_display(self, self.modes[0], force_clear)


class FakeVegas:
    """The coordinator contract DisplayController relies on, nothing more.

    run_iteration() renders frames at 125 Hz on the fake clock for
    ``cycle`` seconds and returns True, or returns False as soon as the
    interrupt checker (every 10 frames) or the live-priority checker (every
    0.25 s) asks it to yield -- the same cadence the real coordinator uses.
    A live-priority pause is lifted by the next call, as in the real one.
    """

    FRAME = 1.0 / 125
    INTERRUPT_EVERY = 10
    LIVE_EVERY = 0.25

    def __init__(self, harness: "RunLoopHarness", cycle: float = 30.0,
                 live_in_ticker: bool = False):
        self._h = harness
        self.cycle = cycle
        self.is_enabled = True
        self.vegas_config = SimpleNamespace(live_in_ticker=live_in_ticker)
        self.render_pipeline = None
        self._interrupt: Optional[Callable[[], bool]] = None
        self._live: Optional[Callable[[], Any]] = None
        self._paused_for_live = False

    def set_live_priority_checker(self, fn):
        self._live = fn

    def set_interrupt_checker(self, fn, check_interval=10):
        self._interrupt = fn

    def apply_pending_config_if_idle(self):
        pass

    def cleanup(self):
        pass

    def run_iteration(self) -> bool:
        h = self._h
        clock = h.clock
        if self._paused_for_live:
            self._paused_for_live = False
        h.log("vegas-start", None, quiet=True)
        start = clock.now
        last_live = None
        frames = 0
        while True:
            now = clock.now
            if (self._live and not self.vegas_config.live_in_ticker
                    and (last_live is None or now - last_live >= self.LIVE_EVERY)):
                last_live = now
                if self._live():
                    self._paused_for_live = True
                    h.log("vegas-live")
                    return False
            h.log("vegas-frame", None, quiet=True)
            clock.sleep(self.FRAME)
            frames += 1
            if self._interrupt and frames % self.INTERRUPT_EVERY == 0 and self._interrupt():
                h.log("vegas-interrupt")
                return False
            if clock.now - start >= self.cycle:
                return True


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------

#: Events that can end a screen, as they appear in the trace.
REASON_EVENTS = {
    "schedule-off", "schedule-on", "live", "live-ended", "on-demand-start",
    "on-demand-requested-stop", "on-demand-expired",
    "on-demand-no-modes-available", "vegas-live", "vegas-interrupt",
    "cycle-complete", "display-false",
}

_SEGMENT_FOR = {
    "follower-frame": "<follower>",
    "vegas-frame": "<vegas>",
    "wifi": "<wifi>",
    "blank": "<off>",
}


class RunLoopHarness:
    """Build a DisplayController on fakes, run it, and return its trace."""

    def __init__(self, tmp_path: Path, horizon: float):
        self.clock = FakeClock(T0, horizon)
        self.events: List[Tuple[float, str, Any, Dict[str, Any]]] = []
        self.tmp_path = tmp_path
        # The controller keeps this very dict as self.config, so a scenario
        # can edit what run() reads live (durations, schedules). Values
        # __init__ copies out (global_dynamic_config) are set on the
        # controller instead.
        self.config: Dict[str, Any] = {
            "timezone": "UTC",
            "display": {"hardware": {"brightness": 90}},
        }
        self.cache = FakeCache()
        self.pm = FakePluginManager()
        self.sync = FakeSync(self)
        self.dm = self._display_manager()
        self._displayed_this_pass = False
        self.controller = self._build()

    # -- event log -----------------------------------------------------------
    def log(self, kind: str, subject: Any = None, quiet: bool = False, **data):
        data["quiet"] = quiet
        self.events.append((round(self.clock.rel(), 3), kind, subject, data))

    def on_display(self, plugin: FakePlugin, mode: str, force_clear: bool):
        first = not self._displayed_this_pass
        self._displayed_this_pass = True
        if plugin.raises:
            self.log("first" if first else "frame", mode, quiet=True,
                     clear=bool(force_clear), result="raised")
            raise RuntimeError(f"{plugin.plugin_id} display() failed")
        if plugin.first_frame_only:
            result = first
        elif plugin.content is None:
            result = True
        else:
            result = bool(plugin.content(self.clock.rel(), mode))
        self.log("first" if first else "frame", mode, quiet=True,
                 clear=bool(force_clear), result=result)
        return result

    # -- construction ----------------------------------------------------------
    def _display_manager(self):
        dm = MagicMock(name="DisplayManager")
        dm.width = 128
        dm.height = 32
        dm._sync_render_allowed = False
        dm.set_brightness = MagicMock(side_effect=self._on_set_brightness)
        dm.update_display = MagicMock(side_effect=self._on_update_display)
        dm.get_font_height = MagicMock(return_value=8)
        return dm

    def _on_set_brightness(self, value):
        self.log("brightness", value)
        return True

    def _on_update_display(self):
        if getattr(self.dm, "_sync_render_allowed", False):
            self.log("follower-frame", None, quiet=True)
        elif not self.controller.is_display_active:
            self.log("blank", None, quiet=True)

    def _build(self):
        from src import display_controller as dc_mod

        clock = self.clock
        env = {"LEDMATRIX_HOT_RELOAD": "false", "EMULATOR": "true"}
        with patch.dict(os.environ, env), \
                patch.object(dc_mod, "time", clock.time_module()), \
                patch.object(dc_mod, "datetime", clock.datetime_class()), \
                patch.object(dc_mod, "ConfigManager", MagicMock()), \
                patch.object(dc_mod, "ConfigService", lambda **kw: FakeConfigService(self.config)), \
                patch.object(dc_mod, "CacheManager", lambda: self.cache), \
                patch.object(dc_mod, "DisplayManager", lambda config: self.dm), \
                patch.object(dc_mod, "FontManager", MagicMock()), \
                patch.object(dc_mod, "DisplaySyncManager", lambda **kw: self.sync), \
                patch("src.plugin_system.PluginManager", lambda **kw: self.pm), \
                patch("src.error_aggregator.start_error_snapshot_publisher", lambda cm: None), \
                patch("src.font_usage.start_font_usage_publisher", lambda *a, **k: None), \
                patch("src.plugin_system.plugin_runtime.start_plugin_runtime_publisher",
                      lambda *a, **k: None), \
                patch("src.auto_update_setup.ensure_update_helper", lambda config: None):
            controller = dc_mod.DisplayController()

        # __init__ wires real health/resource monitors; swap in the fake
        # breaker so failures and skips are deterministic.
        self.pm.health_tracker = FakeHealthTracker(self)
        self.pm.resource_monitor = None
        controller.wifi_status_file = self.tmp_path / "wifi_status.json"
        self._instrument(controller)
        return controller

    def _instrument(self, dc) -> None:
        """Log the controller's decisions without changing any of them.

        Each wrapper calls straight through to the real method; only methods
        that exist both before and after the stage-1 extraction are wrapped,
        so the same harness records the same trace from either.
        """
        h = self

        def wrap(name, before, after):
            real = getattr(dc, name)

            def wrapper(*args, **kwargs):
                token = before(*args, **kwargs)
                result = real(*args, **kwargs)
                after(token, *args, **kwargs)
                return result
            setattr(dc, name, wrapper)

        wrap("_evaluate_schedule",
             lambda: dc.is_display_active,
             lambda was: (h.log("schedule-off") if was and not dc.is_display_active
                          else h.log("schedule-on") if not was and dc.is_display_active
                          else None))
        wrap("_activate_on_demand",
             lambda request: None,
             lambda _, request: h.log("on-demand-start", request.get("plugin_id"))
             if dc.on_demand_active else h.log("on-demand-error", dc.on_demand_last_error))
        wrap("_clear_on_demand",
             lambda reason=None: dc.on_demand_active,
             lambda was, reason=None: h.log(f"on-demand-{reason}") if was else None)
        wrap("_apply_live_priority",
             lambda mode: dc.current_display_mode,
             lambda prev, mode: (None if dc.current_display_mode == prev
                                 else h.log("live" if mode else "live-ended",
                                            dc.current_display_mode)))
        real_note = dc._note_empty_pass

        def note_empty_pass():
            h.log("empty", dc.current_display_mode, quiet=True)
            return real_note()
        dc._note_empty_pass = note_empty_pass

        real_wifi = dc._display_wifi_status_message

        def display_wifi(status):
            shown = real_wifi(status)
            if shown:
                h.log("wifi", status.get("message"), quiet=True)
            return shown
        dc._display_wifi_status_message = display_wifi

    # -- scenario setup ------------------------------------------------------
    def add_plugin(self, plugin: FakePlugin, lock: bool = True) -> FakePlugin:
        """Register a plugin the way _register_loaded_plugin leaves things."""
        dc = self.controller
        plugin._h = self
        self.pm.plugins[plugin.plugin_id] = plugin
        if not lock:
            self.pm.no_lock.add(plugin.plugin_id)
        dc.plugin_display_modes[plugin.plugin_id] = list(plugin.modes)
        for mode in plugin.modes:
            if mode not in dc.available_modes:
                dc.available_modes.append(mode)
            dc.plugin_modes[mode] = plugin
            dc.mode_to_plugin_id[mode] = plugin.plugin_id
        return plugin

    def add_mode_without_plugin(self, mode: str) -> None:
        self.controller.available_modes.append(mode)

    def on_demand_request(self, t: float, request_id: str, action: str = "start", **fields):
        def post():
            self.log("request", f"{action}:{request_id}")
            self.cache.set("display_on_demand_request",
                           {"request_id": request_id, "action": action, **fields})
        self.clock.at(t, post)

    def restore_on_demand(self, plugin_id: str, mode: Optional[str] = None,
                          duration: Optional[float] = None, pinned: bool = False):
        """Start with an on-demand session resumed from the cache, as after
        a restart: the state _select_startup_plugins restores, then
        _populate_on_demand_modes_from_plugin, as __init__ calls it."""
        dc = self.controller
        dc.on_demand_active = True
        dc.on_demand_plugin_id = plugin_id
        dc.on_demand_mode = mode
        dc.on_demand_duration = duration
        dc.on_demand_pinned = pinned
        dc.on_demand_requested_at = self.clock.now
        dc.on_demand_expires_at = self.clock.now + duration if duration else None
        dc.on_demand_status = 'active'
        dc.on_demand_schedule_override = True
        dc._populate_on_demand_modes_from_plugin()

    def wifi_message(self, t: float, message: str, duration: float = 5):
        def write():
            self.log("wifi-file", message)
            self.controller.wifi_status_file.write_text(json.dumps(
                {"message": message, "timestamp": self.clock.now, "duration": duration}),
                encoding="utf-8")
        self.clock.at(t, write)

    def enable_vegas(self, cycle: float = 30.0, live_in_ticker: bool = False) -> FakeVegas:
        """Install FakeVegas, wired up as _initialize_vegas_mode wires the real one."""
        dc = self.controller
        vegas = FakeVegas(self, cycle=cycle, live_in_ticker=live_in_ticker)
        vegas.set_live_priority_checker(dc._check_live_priority)
        vegas.set_interrupt_checker(
            lambda: dc._check_vegas_interrupt() or dc.sync_manager.is_follower_active(),
            check_interval=10)
        dc.vegas_coordinator = vegas
        return vegas

    # -- running -------------------------------------------------------------
    def run(self) -> Dict[str, Any]:
        from src import display_controller as dc_mod
        from src import display_watchdog

        clock = self.clock
        watchdog = display_watchdog.watchdog
        real_loop_pass = watchdog.loop_pass

        def loop_pass():
            self._displayed_this_pass = False
            self.log("pass", None, quiet=True)
            clock.passes_since_advance += 1
            if clock.passes_since_advance > SPIN_LIMIT:
                raise SpinError(f"run() spun {SPIN_LIMIT} passes at t={clock.rel():.3f}")
            return real_loop_pass()

        with patch.object(dc_mod, "time", clock.time_module()), \
                patch.object(dc_mod, "datetime", clock.datetime_class()), \
                patch.object(watchdog, "loop_pass", loop_pass):
            try:
                self.controller.run()
            except StopRun:
                pass
            else:
                # run() only returns after catching something itself.
                raise AssertionError(
                    f"run() returned at t={clock.rel():.3f} before the horizon")
        return reduce_trace(self.events, round(clock.horizon - clock.start, 3))


def reduce_trace(events, horizon: float) -> Dict[str, Any]:
    """Fold the event log into screens and the notable events."""
    screens: List[Dict[str, Any]] = []
    notable: List[List[Any]] = []
    cur: Optional[Dict[str, Any]] = None
    # What happened in the current loop pass, for attributing an empty pass.
    shown_this_pass = False
    failed_this_pass = False
    breaker_this_pass = False

    def start(t, mode, clear=None):
        nonlocal cur
        cur = {"t": t, "mode": mode, "frames": 0, "clear": clear, "exit": None}
        screens.append(cur)

    for t, kind, subject, data in events:
        if not data.get("quiet"):
            notable.append([t, kind] + ([subject] if subject is not None else []))
        if kind == "pass":
            shown_this_pass = failed_this_pass = breaker_this_pass = False
        elif kind in ("first", "frame"):
            if kind == "first" or cur is None:
                start(t, subject, data["clear"])
                shown_this_pass = True
                cur["result"] = data["result"]
            cur["frames"] += 1
            if kind == "frame" and data["result"] is False and cur["exit"] is None:
                cur["exit"] = "display-false"
        elif kind in _SEGMENT_FOR:
            segment = _SEGMENT_FOR[kind]
            if cur is None or cur["mode"] != segment or cur["exit"] is not None:
                start(t, segment)
            cur["frames"] += 1
        elif kind == "vegas-start":
            start(t, "<vegas>")
        elif kind == "health-failure":
            failed_this_pass = True
        elif kind == "breaker-open":
            breaker_this_pass = True
        elif kind == "empty":
            if shown_this_pass and cur is not None and cur["exit"] is None:
                # display() ran and had nothing (False) or raised.
                cur["exit"] = "raised" if cur.get("result") == "raised" else "empty"
            else:
                # Never reached display(): no plugin, the breaker is open, or
                # the dispatch itself raised.
                start(t, subject)
                cur["exit"] = ("error" if failed_this_pass
                               else "breaker" if breaker_this_pass else "no-plugin")
        elif kind in REASON_EVENTS and cur is not None and cur["exit"] is None:
            cur["exit"] = kind

    rows = []
    for i, screen in enumerate(screens):
        end = screens[i + 1]["t"] if i + 1 < len(screens) else horizon
        nxt = screens[i + 1] if i + 1 < len(screens) else None
        # A WiFi notice logs no event at the moment it takes the panel (the
        # file is written earlier), so a screen followed by one is labelled
        # "wifi". Its duration column shows whether it was cut short.
        exit_reason = screen["exit"] or (
            "horizon" if nxt is None
            else "wifi" if nxt["mode"] == "<wifi>" and screen["mode"] != "<wifi>"
            else "duration")
        rows.append([screen["t"], screen["mode"], round(end - screen["t"], 3),
                     exit_reason, screen["frames"], screen["clear"]])
    return {"screens": rows, "events": notable}


# ---------------------------------------------------------------------------
# Golden files
# ---------------------------------------------------------------------------

def dump_golden(trace: Dict[str, Any]) -> str:
    """One screen or event per line, so a diff points at the row that moved."""
    def block(name, rows, last=False):
        end = "" if last else ","
        if not rows:
            return [f'  "{name}": []{end}']
        return [f'  "{name}": [',
                ",\n".join("    " + json.dumps(row) for row in rows),
                f"  ]{end}"]

    lines = (["{"] + block("screens", trace["screens"])
             + block("events", trace["events"], last=True) + ["}"])
    return "\n".join(lines) + "\n"


def check_golden(name: str, trace: Dict[str, Any]) -> None:
    """Compare against test/fixtures/run_loop_golden/<name>.json.

    LEDMATRIX_REGEN_GOLDEN=1 rewrites the file instead. Only do that for a
    deliberate behaviour change, and say why in the commit.
    """
    path = GOLDEN_DIR / f"{name}.json"
    text = dump_golden(trace)
    if os.environ.get("LEDMATRIX_REGEN_GOLDEN") == "1":
        GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")
        return
    assert path.exists(), f"no golden trace {path}; run with LEDMATRIX_REGEN_GOLDEN=1"
    expected = json.loads(path.read_text(encoding="utf-8"))
    actual = json.loads(text)
    if actual != expected:
        import difflib
        diff = "\n".join(difflib.unified_diff(
            dump_golden(expected).splitlines(), text.splitlines(),
            "golden", "actual", lineterm="", n=2))
        raise AssertionError(f"run() trace for {name!r} changed:\n{diff}")
