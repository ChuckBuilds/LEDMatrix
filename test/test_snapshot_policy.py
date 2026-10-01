"""Tests for the snapshot write policy (src/common/snapshot_policy.py).

The invariants that matter:
- unchanged frames are NEVER re-encoded (the old code PNG-encoded identical
  frames at 5 fps, 24/7)
- the file mtime never goes stale enough to trip the health check's 60s
  degraded threshold (api_v3 get_hardware_status)
- a viewer gets the viewer cadence; no viewer drops to the idle keepalive
- decide() is monotone in frame_changed, which DisplayManager's lazy
  frame hash relies on
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.common.snapshot_policy import (  # noqa: E402
    IDLE_INTERVAL,
    TOUCH_INTERVAL,
    VIEWER_INTERVAL,
    VIEWER_MARKER_FRESH_SEC,
    VIEWER_POLL_INTERVAL,
    SnapshotAction,
    decide,
)


class TestViewerCadence:
    def test_changed_frame_with_viewer_writes_at_full_rate(self):
        assert decide(now=100.0, last_write_ts=100.0 - VIEWER_INTERVAL,
                      last_touch_ts=0, viewer_fresh=True,
                      frame_changed=True) is SnapshotAction.WRITE

    def test_changed_frame_with_viewer_respects_min_interval(self):
        assert decide(now=100.0, last_write_ts=100.0 - VIEWER_INTERVAL / 2,
                      last_touch_ts=100.0, viewer_fresh=True,
                      frame_changed=True) is SnapshotAction.SKIP

    def test_unchanged_frame_with_viewer_never_writes(self):
        """A static screen with a viewer must not burn PNG encodes."""
        assert decide(now=100.0, last_write_ts=90.0, last_touch_ts=90.0,
                      viewer_fresh=True,
                      frame_changed=False) is SnapshotAction.SKIP


class TestIdleCadence:
    def test_changed_frame_without_viewer_waits_for_idle_interval(self):
        assert decide(now=100.0, last_write_ts=100.0 - IDLE_INTERVAL / 2,
                      last_touch_ts=100.0, viewer_fresh=False,
                      frame_changed=True) is SnapshotAction.SKIP

    def test_changed_frame_without_viewer_writes_at_idle_rate(self):
        assert decide(now=100.0, last_write_ts=100.0 - IDLE_INTERVAL,
                      last_touch_ts=0, viewer_fresh=False,
                      frame_changed=True) is SnapshotAction.WRITE


class TestHealthKeepalive:
    def test_stale_mtime_gets_touched(self):
        """Whatever else happens, mtime must be bumped within TOUCH_INTERVAL
        so the health check (60s threshold) never reads the display as dead."""
        assert decide(now=100.0, last_write_ts=100.0 - TOUCH_INTERVAL,
                      last_touch_ts=100.0 - TOUCH_INTERVAL, viewer_fresh=False,
                      frame_changed=False) is SnapshotAction.TOUCH

    def test_touch_applies_with_viewer_too(self):
        """Viewer watching a static screen: no writes, but health stays green."""
        assert decide(now=100.0, last_write_ts=100.0 - TOUCH_INTERVAL - 1,
                      last_touch_ts=100.0 - TOUCH_INTERVAL - 1, viewer_fresh=True,
                      frame_changed=False) is SnapshotAction.TOUCH

    def test_recent_touch_suppresses_another(self):
        assert decide(now=100.0, last_write_ts=0.0,
                      last_touch_ts=100.0 - TOUCH_INTERVAL / 2, viewer_fresh=False,
                      frame_changed=False) is SnapshotAction.SKIP

    def test_touch_interval_stays_under_health_threshold(self):
        """api_v3's hardware status treats snapshot age >= 60s as degraded.
        Keep a 2x margin so scheduling jitter can't trip it."""
        assert TOUCH_INTERVAL <= 30

    def test_worst_case_mtime_age_is_bounded(self):
        """Simulate any interleaving: from any state, within one policy call
        after TOUCH_INTERVAL elapses, mtime gets refreshed (WRITE or TOUCH)."""
        for viewer in (True, False):
            for changed in (True, False):
                action = decide(now=1000.0, last_write_ts=900.0,
                                last_touch_ts=900.0, viewer_fresh=viewer,
                                frame_changed=changed)
                assert action in (SnapshotAction.WRITE, SnapshotAction.TOUCH)


class TestMonotoneInFrameChanged:
    """DisplayManager asks decide() as if the frame had changed, and only
    hashes the frame when that answer is not SKIP. That is exact only while
    a SKIP for a changed frame is also a SKIP for an unchanged one."""

    def test_skip_for_a_changed_frame_is_skip_for_an_unchanged_one(self):
        # Ages that straddle every threshold decide() compares against, plus
        # a timestamp in the future (a wall-clock step back) and "never".
        eps = 1e-6
        ages = sorted({0.0, -1.0, 1000.0, 100.0} | {
            base + delta
            for base in (VIEWER_INTERVAL, IDLE_INTERVAL, TOUCH_INTERVAL)
            for delta in (-eps, 0.0, eps)
        })
        now = 1000.0
        checked = 0
        for write_age in ages:
            for touch_age in ages:
                for viewer in (True, False):
                    args = (now, now - write_age, now - touch_age, viewer)
                    if decide(*args, frame_changed=True) is SnapshotAction.SKIP:
                        checked += 1
                        assert decide(*args, frame_changed=False) is SnapshotAction.SKIP, args
        assert checked, "the grid never reached a SKIP; it tests nothing"


class TestPreviewPolling:
    def test_the_reader_polls_faster_than_the_writer_writes(self):
        """Equal periods alias: two unsynchronised clocks of the same period
        leave the preview up to a period stale, with the odd double gap.
        Polling at least twice per interval keeps each frame at most half an
        interval late."""
        assert VIEWER_POLL_INTERVAL <= VIEWER_INTERVAL / 2

    def test_polling_keeps_the_viewer_marker_fresh(self):
        # The SSE reader touches the marker about once a second however fast
        # it polls; the display must still count that as a live viewer.
        assert max(VIEWER_POLL_INTERVAL, 1.0) < VIEWER_MARKER_FRESH_SEC


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
