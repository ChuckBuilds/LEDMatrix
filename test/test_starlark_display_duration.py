"""Starlark per-app rotation duration and animation timing regression tests."""

import importlib.util
import sys
from pathlib import Path
from unittest.mock import MagicMock


ROOT = Path(__file__).resolve().parents[1]
PLUGIN_DIR = ROOT / "plugin-repos/starlark-apps"
sys.path.insert(0, str(PLUGIN_DIR))
spec = importlib.util.spec_from_file_location(
    "test_starlark_duration_manager", PLUGIN_DIR / "manager.py")
manager_module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = manager_module
spec.loader.exec_module(manager_module)


def _app(app_id, duration, frames=None):
    app = manager_module.StarlarkApp.__new__(manager_module.StarlarkApp)
    app.app_id = app_id
    app.manifest = {"enabled": True, "display_duration": duration}
    app.frames = frames or [(f"{app_id}-frame", 100)]
    app.current_frame_index = 0
    app.last_frame_time = 0.0
    return app


def _manager(*apps):
    manager = manager_module.StarlarkAppsPlugin.__new__(manager_module.StarlarkAppsPlugin)
    manager.apps = {app.app_id: app for app in apps}
    manager.current_app = None
    manager.config = {"display_duration": 15}
    manager.display_manager = MagicMock()
    manager.logger = MagicMock()
    return manager


def test_aquarium_advances_at_its_15_second_rotation_boundary(test_display_controller):
    manager = _manager(_app("aquarium", 15), _app("clock", 30))
    test_display_controller.plugin_modes = {"aquarium": manager, "clock": manager}

    duration = test_display_controller._get_display_duration("aquarium")

    assert duration == 15
    assert (14.999 >= duration) is False
    assert 15.0 >= duration


def test_second_starlark_app_advances_at_30_seconds(test_display_controller):
    manager = _manager(_app("aquarium", 15), _app("clock", 30))
    test_display_controller.plugin_modes = {"aquarium": manager, "clock": manager}

    duration = test_display_controller._get_display_duration("clock")

    assert duration == 30
    assert (29.999 >= duration) is False
    assert 30.0 >= duration


def test_non_starlark_plugin_duration_contract_is_unchanged(test_display_controller):
    plugin = MagicMock(spec=["get_display_duration"])
    plugin.get_display_duration.return_value = 42
    test_display_controller.plugin_modes = {"legacy-mode": plugin}

    assert test_display_controller._get_display_duration("legacy-mode") == 42
    plugin.get_display_duration.assert_called_once_with()


def test_webp_frames_continue_advancing_within_mode_duration(monkeypatch):
    aquarium = _app("aquarium", 15, [("frame-1", 100), ("frame-2", 100)])
    manager = _manager(aquarium)
    times = iter((0.05, 0.11, 0.22))
    monkeypatch.setattr(manager_module.time, "time", lambda: next(times))

    assert manager.display(display_mode="aquarium") is True
    assert aquarium.current_frame_index == 0
    assert manager.display(display_mode="aquarium") is True
    assert aquarium.current_frame_index == 1
    assert manager.display(display_mode="aquarium") is True
    assert aquarium.current_frame_index == 0
    assert manager.get_mode_display_duration("aquarium") == 15


def test_manual_mode_switch_selects_requested_app_immediately():
    aquarium = _app("aquarium", 15)
    clock = _app("clock", 30)
    manager = _manager(aquarium, clock)

    assert manager.display(display_mode="aquarium") is True
    assert manager.current_app is aquarium

    assert manager.display(display_mode="clock", force_clear=True) is True
    assert manager.current_app is clock
    manager.display_manager.clear.assert_called_once_with()
    assert manager.display_manager.image == "clock-frame"
