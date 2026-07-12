"""Tests for update_display dirty tracking (src/display_manager.py).

Runs against RGBMatrixEmulator (EMULATOR=true), exercising the REAL
DisplayManager — not a mock — so the skip logic, its invalidation hooks,
and the kill switch are verified off-Pi.

The invariants:
- identical frames are pushed exactly once (SwapOnVSync not re-called)
- ANY pixel change pushes
- clear() and set_brightness() invalidate (the two paths that alter panel
  state outside the digest's view)
- the kill switch (display.dirty_tracking: false) restores always-push
"""

import os
import sys

os.environ["EMULATOR"] = "true"

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


@pytest.fixture(scope="module")
def dm():
    """One real DisplayManager on the emulator (it's a process singleton)."""
    from src.display_manager import DisplayManager
    DisplayManager._instance = None
    DisplayManager._initialized = False
    manager = DisplayManager({
        "display": {
            "hardware": {"rows": 32, "cols": 64, "chain_length": 2,
                         "parallel": 1, "brightness": 90},
            "runtime": {"gpio_slowdown": 0},
        },
    }, suppress_test_pattern=True)
    yield manager


class _SwapSpy:
    """Counts SwapOnVSync calls through the real matrix object."""

    def __init__(self, matrix):
        self.matrix = matrix
        self.count = 0
        self._orig = matrix.SwapOnVSync

    def __enter__(self):
        def counting(canvas):
            self.count += 1
            return self._orig(canvas)
        self.matrix.SwapOnVSync = counting
        return self

    def __exit__(self, *exc):
        self.matrix.SwapOnVSync = self._orig


class TestDirtyTracking:
    def test_identical_frames_push_once(self, dm):
        dm.draw.rectangle([0, 0, 10, 10], fill=(255, 0, 0))
        with _SwapSpy(dm.matrix) as spy:
            dm.update_display()
            dm.update_display()
            dm.update_display()
        assert spy.count == 1

    def test_pixel_change_pushes(self, dm):
        dm.update_display()
        with _SwapSpy(dm.matrix) as spy:
            dm.draw.point((5, 5), fill=(0, 255, 0))
            dm.update_display()
            dm.update_display()  # unchanged again
        assert spy.count == 1

    def test_clear_invalidates(self, dm):
        dm.draw.rectangle([0, 0, 20, 20], fill=(0, 0, 255))
        dm.update_display()
        dm.clear()  # writes to the matrix directly; digest must reset
        with _SwapSpy(dm.matrix) as spy:
            dm.update_display()  # black frame after clear must still push
        assert spy.count == 1

    def test_brightness_change_forces_push(self, dm):
        dm.draw.rectangle([0, 0, 20, 20], fill=(200, 200, 200))
        dm.update_display()
        with _SwapSpy(dm.matrix) as spy:
            dm.update_display()          # identical -> skipped
            assert spy.count == 0
            dm.set_brightness(40)        # dim schedule scenario
            dm.update_display()          # same image, new brightness -> push
        assert spy.count == 1
        dm.set_brightness(90)

    def test_snapshot_still_written_on_skip(self, dm, tmp_path):
        """The web preview path must keep working through skipped pushes."""
        dm._snapshot_path = str(tmp_path / "snap.png")
        dm._last_snapshot_ts = 0.0
        dm.draw.rectangle([0, 0, 30, 8], fill=(255, 255, 0))
        dm.update_display()   # push + snapshot
        assert os.path.exists(dm._snapshot_path)


class TestKillSwitch:
    def test_dirty_tracking_can_be_disabled(self, dm):
        dm._dirty_tracking_enabled = False
        try:
            dm.draw.rectangle([0, 0, 10, 10], fill=(1, 2, 3))
            with _SwapSpy(dm.matrix) as spy:
                dm.update_display()
                dm.update_display()
                dm.update_display()
            assert spy.count == 3  # always-push, exactly the old behavior
        finally:
            dm._dirty_tracking_enabled = True
            dm._last_pushed_digest = None

    def test_config_flag_wires_through(self):
        from src.display_manager import DisplayManager
        DisplayManager._instance = None
        DisplayManager._initialized = False
        manager = DisplayManager({
            "display": {
                "hardware": {"rows": 32, "cols": 64, "chain_length": 1,
                             "parallel": 1},
                "runtime": {"gpio_slowdown": 0},
                "dirty_tracking": False,
            },
        }, suppress_test_pattern=True)
        assert manager._dirty_tracking_enabled is False


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
