"""starlark-apps: display() must report what actually reached the panel.

The display controller skips a mode only on a boolean False. Returning True
after the frame update failed told it the mode had rendered, so it held a dead
frame for the whole display_duration instead of rotating on -- the same class
of defect as a display() that returns None.
"""

import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest

PLUGIN_DIR = Path(__file__).resolve().parent.parent / "plugin-repos" / "starlark-apps"


@pytest.fixture(scope="module")
def manager_module():
    if not PLUGIN_DIR.exists():
        pytest.skip("starlark-apps plugin is not checked out")
    sys.path.insert(0, str(PLUGIN_DIR))
    # fcntl is POSIX-only and the manager imports it at module scope for
    # manifest locking. None of the display paths under test touch it, so a
    # stub is what keeps these assertions running on a developer's machine
    # rather than skipping everywhere but the Pi and CI.
    injected_fcntl = "fcntl" not in sys.modules
    if injected_fcntl:
        stub = types.ModuleType("fcntl")
        stub.LOCK_EX = 2
        stub.LOCK_UN = 8
        stub.flock = lambda *a, **kw: None
        sys.modules["fcntl"] = stub
    try:
        import importlib
        spec = importlib.util.spec_from_file_location(
            "starlark_manager_under_test", PLUGIN_DIR / "manager.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    except Exception as e:  # noqa: BLE001 - optional deps (Pillow, pixlet) may be absent
        pytest.skip(f"starlark-apps manager is not importable here: {e}")
    finally:
        sys.path.remove(str(PLUGIN_DIR))
        if injected_fcntl:
            sys.modules.pop("fcntl", None)


def _plugin(manager_module):
    """A manager with __init__ bypassed -- only display paths are under test."""
    cls = manager_module.StarlarkAppsPlugin
    inst = cls.__new__(cls)
    inst.logger = MagicMock()
    inst.display_manager = MagicMock()
    inst.current_app = None
    return inst


class _App:
    def __init__(self, frames):
        self.frames = frames
        self.current_frame_index = 0
        self.last_frame_time = 0.0
        self.app_id = "app"


class TestDisplayFramePropagates:
    def test_a_failed_update_returns_false(self, manager_module):
        p = _plugin(manager_module)
        p.current_app = _App([("frame", 100)])
        p.display_manager.update_display.side_effect = RuntimeError("panel gone")

        assert p._display_frame() is False

    def test_a_good_update_returns_true(self, manager_module):
        p = _plugin(manager_module)
        p.current_app = _App([("frame", 100)])

        assert p._display_frame() is True

    def test_no_frames_returns_false(self, manager_module):
        p = _plugin(manager_module)
        p.current_app = _App([])

        assert p._display_frame() is False

    def test_display_reports_the_frame_failure(self, manager_module):
        p = _plugin(manager_module)
        p.current_app = _App([("frame", 100)])
        p.display_manager.update_display.side_effect = RuntimeError("panel gone")

        result = p.display()

        assert result is False, "display() claimed success over a failed frame update"
        assert isinstance(result, bool), "the controller only skips on a real bool"


class _SelectableApp(_App):
    """An app that display() can pick, render and show."""

    def __init__(self, app_id, enabled=True):
        super().__init__(frames=[("frame", 100)])
        self.app_id = app_id
        self._enabled = enabled

    def is_enabled(self):
        return self._enabled


def _plugin_with_apps(manager_module, *app_ids):
    p = _plugin(manager_module)
    p.apps = {app_id: _SelectableApp(app_id) for app_id in app_ids}
    p._display_frame = lambda: True
    return p


class TestDisplayModeSelectsTheApp:
    """The controller passes the mode it is rotating to; the plugin must use it.

    It inspects display()'s signature to decide whether to pass display_mode at
    all, so the parameter is the whole mechanism -- without it there is no way
    to address one specific app, including from an on-demand request pinned to
    one.
    """

    def test_display_accepts_display_mode(self, manager_module):
        import inspect
        params = inspect.signature(manager_module.StarlarkAppsPlugin.display).parameters
        assert "display_mode" in params, \
            "the controller only passes display_mode to plugins whose signature accepts it"

    def test_a_named_app_is_the_one_shown(self, manager_module):
        p = _plugin_with_apps(manager_module, "aquarium", "printer", "nowplaying")
        assert p.display(display_mode="printer") is True
        assert p.current_app.app_id == "printer"

    def test_a_named_app_wins_over_the_one_already_showing(self, manager_module):
        p = _plugin_with_apps(manager_module, "aquarium", "printer")
        p.display(display_mode="aquarium")
        p.display(display_mode="printer")
        assert p.current_app.app_id == "printer"

    def test_an_unknown_mode_falls_back_to_rotation(self, manager_module):
        """The plugin id arrives here when no per-app modes are exposed."""
        p = _plugin_with_apps(manager_module, "aquarium")
        assert p.display(display_mode="starlark-apps") is True
        assert p.current_app.app_id == "aquarium"

    def test_force_clear_does_not_override_a_named_app(self, manager_module):
        p = _plugin_with_apps(manager_module, "aquarium", "printer")
        p.display(display_mode="printer", force_clear=True)
        assert p.current_app.app_id == "printer"


class TestInstalledAppsTakeTurns:
    """Every installed app gets shown, not just whichever was picked first.

    _select_next_app ran only while current_app was unset, so the first enabled
    app was chosen once and displayed forever; the rest were rendered on
    schedule and never reached the panel.
    """

    def test_each_entry_to_the_mode_advances(self, manager_module):
        p = _plugin_with_apps(manager_module, "a", "b", "c")
        seen = []
        for _ in range(3):
            p.display(force_clear=True)          # force_clear = "you are up"
            seen.append(p.current_app.app_id)
        assert seen == ["a", "b", "c"]

    def test_the_rotation_wraps(self, manager_module):
        p = _plugin_with_apps(manager_module, "a", "b")
        seen = []
        for _ in range(4):
            p.display(force_clear=True)
            seen.append(p.current_app.app_id)
        assert seen == ["a", "b", "a", "b"]

    def test_frames_within_one_slot_do_not_advance_the_app(self, manager_module):
        """Only entry to the mode rotates -- the high-FPS loop must not."""
        p = _plugin_with_apps(manager_module, "a", "b", "c")
        p.display(force_clear=True)
        for _ in range(20):
            p.display(force_clear=False)
        assert p.current_app.app_id == "a"

    def test_a_disabled_app_is_skipped(self, manager_module):
        p = _plugin_with_apps(manager_module, "a", "b")
        p.apps["a"]._enabled = False
        p.display(force_clear=True)
        assert p.current_app.app_id == "b"

    def test_no_enabled_apps_reports_nothing_displayed(self, manager_module):
        p = _plugin_with_apps(manager_module, "a")
        p.apps["a"]._enabled = False
        assert p.display(force_clear=True) is False


class TestAnimationsRunAtFrameRate:
    def test_the_plugin_asks_for_the_high_fps_loop(self, manager_module):
        """The controller reads this attribute; a multi-frame app is otherwise
        called once per rotation slot and never advances past frame one."""
        plugin = manager_module.StarlarkAppsPlugin
        assert getattr(plugin, "enable_scrolling", False) is True
