"""The /stream/display generator's pace (web_interface/app.py).

display_preview_generator() used to sleep a flat second between passes, while
the display wrote a snapshot up to five times a second for it: four encodes
in five were overwritten unread. The display now writes about once a second
(snapshot_policy.VIEWER_INTERVAL), so the generator checks the snapshot's
mtime every snapshot_policy.VIEWER_POLL_INTERVAL instead -- a stat -- and
sends each frame soon after it lands. Polling equal to the write period would
alias the two clocks into stale frames and the odd two-second gap.

What must not change with it: the viewer marker is touched about once a
second (more would only be extra writes to /tmp), and a pass with no snapshot
or an error still sends its message once a second, not four times.
"""

import os
import sys
import types
from contextlib import nullcontext
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.common import snapshot_policy  # noqa: E402
from web_interface import display_preview  # noqa: E402

MARKER = "/tmp/led_matrix_preview_viewer"  # nosec B108 - the path the generator touches


class _Stop(Exception):
    """Raised from the fake sleep to end the generator's endless loop."""


class _Stream:
    """Drives display_preview_generator() on a fake clock.

    The marker is a fixed /tmp path shared with a display service that may be
    running on this machine, so touches are recorded rather than made.
    """

    def __init__(self, monkeypatch, snapshot_path, os_path=None):
        import web_interface.app as web_app
        self.now = 1000.0
        self.sleeps = []
        self.touches = []
        self.at_sleep = {}
        self.stop_after = 0
        monkeypatch.setattr(web_app, "time", types.SimpleNamespace(
            sleep=self._sleep, monotonic=lambda: self.now, time=lambda: self.now))
        monkeypatch.setattr(web_app, "open", self._open, raising=False)
        monkeypatch.setattr(web_app, "os", types.SimpleNamespace(
            path=os_path or os.path, utime=lambda *a, **k: None))
        monkeypatch.setattr(web_app, "config_manager",
                            MagicMock(**{"load_config.return_value": {}}))
        monkeypatch.setattr(display_preview, "SNAPSHOT_PATH", str(snapshot_path))
        self._generator = web_app.display_preview_generator

    def _open(self, path, mode="r", *args, **kwargs):
        assert path == MARKER and mode == "a"
        self.touches.append(self.now)
        return nullcontext()

    def _sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds
        if len(self.sleeps) >= self.stop_after:
            raise _Stop
        action = self.at_sleep.get(len(self.sleeps))
        if action:
            action()

    def run(self, passes):
        """The messages sent over ``passes`` passes, each with the pass number."""
        self.stop_after = passes
        sent = []
        try:
            for message in self._generator():
                sent.append((len(self.sleeps), message))
        except _Stop:
            pass
        return sent


def _write(path, mtime):
    path.write_bytes(b"\x89PNG not really")
    os.utime(path, (mtime, mtime))


def test_frames_go_out_on_the_next_poll_and_the_marker_is_touched_once_a_second(
        monkeypatch, tmp_path):
    snapshot = tmp_path / "led_matrix_preview.png"
    _write(snapshot, 500.0)
    stream = _Stream(monkeypatch, snapshot)
    # The display writes a new frame once a second: every fourth poll.
    for n, sleep_no in enumerate((4, 8, 12)):
        stream.at_sleep[sleep_no] = lambda m=501.0 + n: _write(snapshot, m)

    sent = stream.run(16)

    assert stream.sleeps == [snapshot_policy.VIEWER_POLL_INTERVAL] * 16
    # The first pass sends what is there; each write goes out on the poll
    # straight after it, not up to a second later.
    assert [pass_no for pass_no, _ in sent] == [0, 4, 8, 12]
    assert all(message["image"] for _, message in sent)
    # Sixteen quarter-second polls are four seconds: four touches, a second
    # apart, well inside VIEWER_MARKER_FRESH_SEC.
    assert stream.touches == [1000.0, 1001.0, 1002.0, 1003.0]


def test_without_a_snapshot_the_placeholder_still_goes_out_once_a_second(
        monkeypatch, tmp_path):
    stream = _Stream(monkeypatch, tmp_path / "missing.png")

    sent = stream.run(5)

    assert stream.sleeps == [1.0] * 5
    assert [pass_no for pass_no, _ in sent] == [0, 1, 2, 3, 4]
    assert all(message["image"] is None for _, message in sent)
    assert stream.touches == [1000.0, 1001.0, 1002.0, 1003.0, 1004.0]


def test_an_error_keeps_the_one_second_pace(monkeypatch, tmp_path):
    def broken(_path):
        raise RuntimeError("stat failed")

    stream = _Stream(monkeypatch, tmp_path / "snap.png",
                     os_path=types.SimpleNamespace(exists=lambda _p: True,
                                                   getmtime=broken))

    sent = stream.run(3)

    assert stream.sleeps == [1.0] * 3
    assert [message for _, message in sent] == [
        {"error": "An error occurred; see server logs"}] * 3
