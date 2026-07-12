"""Snapshot write policy for the display preview mirror.

The display service mirrors frames to /tmp/led_matrix_preview.png, which
serves two consumers with different needs:

- The web UI's live preview (SSE reader in web_interface/app.py) wants
  fresh frames — but only while a browser is actually watching.
- The health check (web_interface/blueprints/api_v3.py, hardware status)
  uses the file's AGE as a liveness proxy: age >= 60s reads as degraded.

PNG-encoding every frame at 5 fps forever — identical frames, no viewers —
was one of the biggest fixed CPU costs on the Pi. This module is the pure
decision logic (extracted so it's unit-testable off-Pi; display_manager
imports rgbmatrix unconditionally and can't be):

    WRITE  — encode + atomically replace the snapshot file
    TOUCH  — os.utime only: keeps the health-check mtime fresh and lets
             the SSE reader (mtime-gated) resend at a low rate, without
             paying for a PNG encode of an unchanged frame
    SKIP   — do nothing

Policy:
- With a fresh viewer marker: changed frames write at up to 1/VIEWER_INTERVAL.
- Without viewers: changed frames still write at 1/IDLE_INTERVAL so the
  preview page shows something recent on open.
- Unchanged frames are never re-encoded; the mtime is touched every
  TOUCH_INTERVAL so the health check (60s threshold) never degrades.

If any constant here changes, re-check the health threshold in
api_v3.py (get_hardware_status) — TOUCH_INTERVAL must stay well under it.
"""

from enum import Enum

# Snapshot cadence with a browser preview open (seconds).
VIEWER_INTERVAL = 0.2
# Snapshot cadence with no viewers — cheap freshness for page-open (seconds).
IDLE_INTERVAL = 30.0
# Max age of the last write/touch before bumping mtime for the health
# check. MUST stay well under api_v3's 60s degraded threshold.
TOUCH_INTERVAL = 20.0
# A viewer marker older than this no longer counts as a live viewer.
VIEWER_MARKER_FRESH_SEC = 5.0


class SnapshotAction(Enum):
    WRITE = "write"
    TOUCH = "touch"
    SKIP = "skip"


def decide(now: float, last_write_ts: float, last_touch_ts: float,
           viewer_fresh: bool, frame_changed: bool) -> SnapshotAction:
    """Decide what to do with the current frame.

    Args:
        now: current monotonic-ish timestamp (same clock as the ts args)
        last_write_ts: when a frame was last actually encoded+written
        last_touch_ts: when the file mtime was last bumped (write or touch)
        viewer_fresh: a browser preview is currently watching
        frame_changed: the frame differs from the last WRITTEN frame
    """
    interval = VIEWER_INTERVAL if viewer_fresh else IDLE_INTERVAL
    if frame_changed and (now - last_write_ts) >= interval:
        return SnapshotAction.WRITE
    if (now - max(last_write_ts, last_touch_ts)) >= TOUCH_INTERVAL:
        return SnapshotAction.TOUCH
    return SnapshotAction.SKIP
