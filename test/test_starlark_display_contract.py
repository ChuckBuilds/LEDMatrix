"""starlark-apps: display() must report what actually reached the panel.

The display controller skips a mode only on a boolean False. Returning True
after the frame update failed told it the mode had rendered, so it held a dead
frame for the whole display_duration instead of rotating on -- the same class
of defect as a display() that returns None.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

PLUGIN_DIR = Path(__file__).resolve().parent.parent / "plugin-repos" / "starlark-apps"


@pytest.fixture(scope="module")
def manager_module():
    if not PLUGIN_DIR.exists():
        pytest.skip("starlark-apps plugin is not checked out")
    sys.path.insert(0, str(PLUGIN_DIR))
    try:
        import importlib
        spec = importlib.util.spec_from_file_location(
            "starlark_manager_under_test", PLUGIN_DIR / "manager.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    except Exception as e:  # noqa: BLE001 - optional deps (pixlet, fcntl) may be absent
        pytest.skip(f"starlark-apps manager is not importable here: {e}")
    finally:
        sys.path.remove(str(PLUGIN_DIR))


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
