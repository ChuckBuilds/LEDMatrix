"""Display-controller fixes found testing on a real Pi (ledpi).

- systemd stops the service with SIGTERM; it must run the same cleanup as Ctrl-C.
- The current mode is republished while it stays on screen, so the web UI's
  "Now showing" does not turn into "unknown" after its 120 s max_age.
- Switching Vegas on in the web UI works when Vegas was off at startup.
"""

import os
import signal
from unittest.mock import MagicMock, patch

import pytest

os.environ.setdefault("EMULATOR", "true")

from src import display_controller as dc_module
from src.display_controller import DisplayController


# --- SIGTERM ----------------------------------------------------------------

def test_sigterm_handler_raises_keyboard_interrupt():
    with pytest.raises(KeyboardInterrupt):
        dc_module._raise_keyboard_interrupt(signal.SIGTERM, None)


def test_main_routes_sigterm_through_run_cleanup():
    """main() installs the handler before run(); SIGTERM then ends run() the
    way Ctrl-C does, so its finally-cleanup runs."""
    events = []

    class FakeController:
        def run(self):
            try:
                handler = signal.getsignal(signal.SIGTERM)
                handler(signal.SIGTERM, None)  # what the signal would do
            except KeyboardInterrupt:
                events.append("interrupted")
            finally:
                events.append("cleanup")

    previous = signal.getsignal(signal.SIGTERM)
    try:
        with patch.object(dc_module, "DisplayController", FakeController):
            dc_module.main()
    finally:
        signal.signal(signal.SIGTERM, previous)
    assert events == ["interrupted", "cleanup"]


# --- current-state heartbeat ------------------------------------------------

def _publisher():
    dc = object.__new__(DisplayController)
    dc.cache_manager = MagicMock()
    dc.current_display_mode = "mlb_live"
    dc.mode_to_plugin_id = {"mlb_live": "baseball-scoreboard"}
    dc.current_mode_index = 0
    dc.available_modes = ["mlb_live"]
    dc.on_demand_active = False
    dc.is_display_active = True
    dc._last_published_mode = None
    dc._last_published_at = 0.0
    return dc


def test_unchanged_mode_is_republished_after_the_refresh_interval():
    dc = _publisher()
    now = [1000.0]
    with patch.object(dc_module.time, "monotonic", lambda: now[0]):
        dc._publish_current_mode_state_if_changed()          # first publish
        now[0] += 5
        dc._publish_current_mode_state_if_changed()          # unchanged, recent
        assert dc.cache_manager.set.call_count == 1
        now[0] += dc_module.CURRENT_STATE_REFRESH_SECONDS    # same mode, stale
        dc._publish_current_mode_state_if_changed()
    assert dc.cache_manager.set.call_count == 2


def test_refresh_interval_is_well_inside_the_web_max_age():
    # api_v3/display.py reads display_current_state with max_age=120.
    assert dc_module.CURRENT_STATE_REFRESH_SECONDS < 120 / 2


# --- Vegas switched on after startup ----------------------------------------

def _vegas_controller(enabled_at_start):
    dc = object.__new__(DisplayController)
    dc.config = {"display": {"vegas_scroll": {"enabled": enabled_at_start}}}
    dc.vegas_coordinator = None
    dc._pending_vegas_init = False
    dc.on_demand_active = False
    dc._refresh_config_cache = MagicMock()
    dc._enabled_set_changed = MagicMock(return_value=False)
    dc._enabled_plugin_not_running = MagicMock(return_value=False)
    return dc


def test_enabling_vegas_live_creates_the_coordinator_on_the_render_thread():
    dc = _vegas_controller(enabled_at_start=False)
    old = {"display": {"vegas_scroll": {"enabled": False}}}
    new = {"display": {"vegas_scroll": {"enabled": True}}}

    dc._controller_config_change(old, new)       # watcher thread: flag only
    assert dc._pending_vegas_init is True

    coordinator = MagicMock(is_enabled=True)

    def init():
        dc.vegas_coordinator = coordinator
    dc._initialize_vegas_mode = MagicMock(side_effect=init)

    assert dc._is_vegas_mode_active() is True    # render thread: created here
    dc._initialize_vegas_mode.assert_called_once_with()
    assert dc._pending_vegas_init is False
    dc._is_vegas_mode_active()
    dc._initialize_vegas_mode.assert_called_once_with()  # not re-created


def test_config_changes_without_vegas_enabled_do_not_flag_init():
    dc = _vegas_controller(enabled_at_start=False)
    same = {"display": {"vegas_scroll": {"enabled": False}}}
    dc._controller_config_change(same, same)
    assert dc._pending_vegas_init is False


def test_sigterm_handler_is_installed_only_after_construction():
    """A SIGTERM while plugins load in __init__ keeps the default exit."""
    seen = {}

    class FakeController:
        def __init__(self):
            seen["during_init"] = signal.getsignal(signal.SIGTERM)

        def run(self):
            seen["during_run"] = signal.getsignal(signal.SIGTERM)

    previous = signal.getsignal(signal.SIGTERM)
    try:
        signal.signal(signal.SIGTERM, signal.SIG_DFL)
        with patch.object(dc_module, "DisplayController", FakeController):
            dc_module.main()
    finally:
        signal.signal(signal.SIGTERM, previous)
    assert seen["during_init"] is not dc_module._raise_keyboard_interrupt
    assert seen["during_run"] is dc_module._raise_keyboard_interrupt


def test_service_pending_changes_keeps_the_state_fresh_during_long_renders():
    """Vegas iterations and long screens call _service_pending_changes, not
    the main loop, for minutes at a time; it republishes a stale state."""
    dc = _publisher()
    dc._last_pending_service = None
    dc.PENDING_CHANGES_INTERVAL = 0.25
    for name in ("_poll_on_demand_requests", "_check_on_demand_expiration",
                 "_evaluate_schedule", "_apply_brightness_target"):
        setattr(dc, name, MagicMock())
    now = [5000.0]
    with patch.object(dc_module.time, "monotonic", lambda: now[0]):
        dc._last_published_mode = "mlb_live"
        dc._last_published_at = now[0] - dc_module.CURRENT_STATE_REFRESH_SECONDS - 1
        dc._service_pending_changes()
    dc.cache_manager.set.assert_called_once()


def test_pending_vegas_init_is_applied_by_the_helper_the_main_loop_calls():
    dc = _vegas_controller(enabled_at_start=False)
    dc._pending_vegas_init = True
    dc._initialize_vegas_mode = MagicMock()
    dc._apply_pending_vegas_init()
    dc._initialize_vegas_mode.assert_called_once_with()
    assert dc._pending_vegas_init is False


def test_main_loop_applies_pending_vegas_init_before_the_follower_branch():
    import inspect
    src = inspect.getsource(DisplayController.run)
    assert src.index("self._apply_pending_vegas_init()") < src.index("self.sync_manager.is_follower_active()")
