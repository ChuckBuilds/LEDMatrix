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
import time

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
    DisplayManager._instance = None
    DisplayManager._initialized = False


class _SwapSpy:
    """Counts SwapOnVSync calls through the real matrix object."""

    def __init__(self, matrix):
        self.matrix = matrix
        self.count = 0
        self.last_frame_hold = None
        self._orig = matrix.SwapOnVSync

    def __enter__(self):
        def counting(canvas, *args):
            # *args carries framerate_fraction, which display_manager passes so
            # a frame can be held for several refreshes. Signature must match
            # the real binding or the spy hides a TypeError as a failed push.
            self.count += 1
            self.last_frame_hold = args[0] if args else 1
            return self._orig(canvas, *args)
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
        """The web preview mirror must keep working through skipped panel
        pushes: _write_snapshot_if_due() still runs on the dirty-tracking
        skip path and applies its own write/touch policy rather than being
        bypassed entirely (see src/common/snapshot_policy.py — an unchanged
        frame is touched, not re-encoded, once TOUCH_INTERVAL elapses)."""
        dm._snapshot_path = str(tmp_path / "snap.png")
        dm._last_snapshot_ts = 0.0
        dm._last_snapshot_touch_ts = 0.0
        dm._last_snapshot_digest = None
        dm.draw.rectangle([0, 0, 30, 8], fill=(255, 255, 0))
        dm.update_display()   # push + snapshot write (first frame)
        assert os.path.exists(dm._snapshot_path)
        # Backdate the file so the "was it bumped?" check below cannot be
        # defeated by filesystem mtime granularity -- on Windows two writes in
        # the same tick get identical timestamps, which made this test fail
        # roughly two runs in three regardless of the code under test.
        os.utime(dm._snapshot_path, (time.time() - 60, time.time() - 60))
        first_mtime = os.path.getmtime(dm._snapshot_path)

        # Age the write/touch bookkeeping past TOUCH_INTERVAL so the next
        # identical frame is due for a touch, then push it again: dirty
        # tracking must skip the panel write, but the snapshot mirror must
        # still get its mtime bumped so the health check doesn't go stale.
        from src.common import snapshot_policy
        stale_ts = time.time() - snapshot_policy.TOUCH_INTERVAL - 1.0
        dm._last_snapshot_ts = stale_ts
        dm._last_snapshot_touch_ts = stale_ts
        with _SwapSpy(dm.matrix) as spy:
            dm.update_display()  # identical frame -> panel push skipped
        assert spy.count == 0
        assert os.path.getmtime(dm._snapshot_path) > first_mtime


class TestScrollLock:
    """Dirty tracking must not skip the panel push while a scroll is running.

    SwapOnVSync is what paces the render loop, so skipping it also skips the
    wait for the panel. A duplicate frame therefore returns early -- ~8ms
    instead of ~10ms on a 100Hz panel -- which advances the strip only 0.8px
    instead of 1.0px, which makes the NEXT frame more likely to be a duplicate
    too. That is self-sustaining: measured at ~20% duplicate frames mid-scroll
    on the odds ticker against essentially zero on a lighter plugin with
    identical scroll settings. Pushing an identical frame costs one canvas
    copy; falling out of vsync lock costs smooth motion.
    """

    def test_identical_frames_still_push_while_scrolling(self, dm):
        dm.draw.rectangle([0, 0, 12, 12], fill=(0, 0, 255))
        dm.update_display()
        dm.set_scrolling_state(True)
        try:
            with _SwapSpy(dm.matrix) as spy:
                dm.update_display()
                dm.update_display()
                dm.update_display()
            assert spy.count == 3, "scrolling must stay locked to the panel"
        finally:
            dm.set_scrolling_state(False)

    def test_identical_frames_are_skipped_when_not_scrolling(self, dm):
        """The optimisation still applies to static content."""
        dm.set_scrolling_state(False)
        dm.draw.rectangle([0, 0, 14, 14], fill=(255, 0, 255))
        dm.update_display()
        with _SwapSpy(dm.matrix) as spy:
            dm.update_display()
            dm.update_display()
        assert spy.count == 0

    def test_stale_scrolling_state_stops_forcing_pushes(self, dm):
        """A plugin that stops scrolling without saying so must not pin the
        panel into always-push forever. is_currently_scrolling() expires on
        its own inactivity threshold, and the skip has to come back with it."""
        dm.draw.rectangle([0, 0, 16, 16], fill=(0, 255, 255))
        dm.update_display()
        dm.set_scrolling_state(True)
        try:
            # Backdate the activity marker past the inactivity threshold.
            dm._scrolling_state['last_scroll_activity'] = (
                time.time() - dm._scrolling_state['scroll_inactivity_threshold'] - 1.0)
            assert dm.is_currently_scrolling() is False
            with _SwapSpy(dm.matrix) as spy:
                dm.update_display()
                dm.update_display()
            assert spy.count == 0
        finally:
            dm.set_scrolling_state(False)


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
        try:
            manager = DisplayManager({
                "display": {
                    "hardware": {"rows": 32, "cols": 64, "chain_length": 1,
                                 "parallel": 1},
                    "runtime": {"gpio_slowdown": 0},
                    "dirty_tracking": False,
                },
            }, suppress_test_pattern=True)
            assert manager._dirty_tracking_enabled is False
        finally:
            DisplayManager._instance = None
            DisplayManager._initialized = False


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))


class TestFrameHold:
    """Holding a frame for N refreshes is how a scroll runs slower than one
    pixel per refresh without fractional pixel positions."""

    def test_hold_reaches_swap_on_vsync(self, dm):
        dm.set_scrolling_state(True)
        dm.set_frame_hold(3)
        try:
            dm.draw.rectangle([0, 0, 9, 9], fill=(120, 0, 200))
            with _SwapSpy(dm.matrix) as spy:
                dm.update_display()
            assert spy.count == 1
            assert spy.last_frame_hold == 3
        finally:
            dm.set_scrolling_state(False)

    def test_default_is_every_refresh(self, dm):
        dm.set_scrolling_state(True)
        try:
            dm.draw.rectangle([0, 0, 11, 11], fill=(0, 120, 200))
            with _SwapSpy(dm.matrix) as spy:
                dm.update_display()
            assert spy.last_frame_hold == 1
        finally:
            dm.set_scrolling_state(False)

    def test_hold_resets_when_scrolling_stops(self, dm):
        """One plugin's pacing must not leak into whatever is on screen next."""
        dm.set_scrolling_state(True)
        dm.set_frame_hold(5)
        dm.set_scrolling_state(False)
        dm.set_scrolling_state(True)
        try:
            dm.draw.rectangle([0, 0, 13, 13], fill=(200, 120, 0))
            with _SwapSpy(dm.matrix) as spy:
                dm.update_display()
            assert spy.last_frame_hold == 1
        finally:
            dm.set_scrolling_state(False)

    @pytest.mark.parametrize("bad,expected", [(0, 1), (-4, 1), (None, 1), ("x", 1)])
    def test_unusable_holds_are_ignored_or_floored(self, dm, bad, expected):
        dm.set_frame_hold(bad)
        assert dm._frame_hold == expected
