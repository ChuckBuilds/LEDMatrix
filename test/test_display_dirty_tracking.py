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
def dm(tmp_path_factory):
    """One real DisplayManager on the emulator (it's a process singleton)."""
    from src.display_manager import DisplayManager
    DisplayManager._instance = None
    manager = DisplayManager({
        "display": {
            "hardware": {"rows": 32, "cols": 64, "chain_length": 2,
                         "parallel": 1, "brightness": 90},
            "runtime": {"gpio_slowdown": 0},
        },
    }, suppress_test_pattern=True)
    # DisplayManager defaults _snapshot_path to the fixed /tmp/led_matrix_preview.png
    # that the web UI reads. That path is shared by every pytest process on the
    # machine, so two concurrent runs -- CI shards, a second worktree, a agent
    # running the suite alongside -- write over each other's snapshot and the
    # mtime assertions below stop meaning anything. Point it somewhere unique to
    # this session; the individual tests that care still override it further.
    manager._snapshot_path = str(
        tmp_path_factory.mktemp("dirty_tracking") / "led_matrix_preview.png")
    # _setup_matrix() swallows every construction failure and falls back to
    # matrix=None, so a broken environment reaches the tests as fifteen
    # identical "'NoneType' object has no attribute 'SwapOnVSync'" errors that
    # name neither this fixture nor the real cause. Fail here instead, once,
    # and say where to look.
    if manager.matrix is None:
        pytest.fail(
            "DisplayManager fell back to matrix=None: RGBMatrix construction "
            "raised (the 'Failed to initialize RGB Matrix' log line above "
            "carries the reason). Known causes: the emulator adapter losing a "
            "fixed TCP port to another process -- see pytest_configure in "
            "test/conftest.py, which pins the port-free 'raw' adapter -- or a "
            "patch('src.display_manager.RGBMatrix') leaked from an earlier "
            "test module.")
    yield manager
    DisplayManager._instance = None


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


class TestFrameHoldLifetime:
    """The hold must last exactly as long as the scroll that asked for it.

    Plugins share one display manager. A hold applied at plugin construction is
    wiped the moment any *other* plugin finishes scrolling, so by the time the
    first plugin renders it is back to one pixel per refresh -- the speed reads
    correct in the log and is wrong on the panel.
    """

    def test_scrolling_state_carries_the_hold(self, dm):
        dm.set_scrolling_state(True, frame_hold=4)
        try:
            dm.draw.rectangle([0, 0, 7, 7], fill=(10, 200, 10))
            with _SwapSpy(dm.matrix) as spy:
                dm.update_display()
            assert spy.last_frame_hold == 4
        finally:
            dm.set_scrolling_state(False)

    def test_a_scroll_that_times_out_does_not_strand_a_hold(self, dm):
        """The hold must go when the state does, however the scroll ended.

        set_scrolling_state(False) is the polite exit. The other one is
        is_currently_scrolling() deciding, after scroll_inactivity_threshold
        of silence, that the scroll is over -- which is what happens when the
        rotation moves on mid-scroll or a plugin is torn down. That path used
        to clear the flag and keep the hold, so every later plugin, scrolling
        or static, was presented at refresh/N by whoever scrolled last.
        """
        dm.set_scrolling_state(True, frame_hold=5)
        # Age the scroll past the inactivity threshold rather than sleeping.
        dm._scrolling_state['last_scroll_activity'] -= (
            dm._scrolling_state['scroll_inactivity_threshold'] + 1.0)

        assert dm.is_currently_scrolling() is False
        dm.draw.rectangle([0, 0, 5, 5], fill=(10, 10, 200))
        with _SwapSpy(dm.matrix) as spy:
            dm.update_display()
        assert spy.last_frame_hold == 1, (
            "a timed-out scroll left its frame hold behind; the next plugin "
            "is being presented at a fraction of the refresh rate")

    def test_another_plugin_stopping_does_not_strand_a_hold(self, dm):
        dm.set_scrolling_state(True, frame_hold=3)
        dm.set_scrolling_state(False)          # some other plugin finishes
        dm.set_scrolling_state(True)           # a plugin that wants no hold
        try:
            dm.draw.rectangle([0, 0, 6, 6], fill=(200, 10, 10))
            with _SwapSpy(dm.matrix) as spy:
                dm.update_display()
            assert spy.last_frame_hold == 1
        finally:
            dm.set_scrolling_state(False)

    def test_default_keeps_previous_behaviour(self, dm):
        """Callers that never heard of frame holds get one frame per refresh."""
        dm.set_scrolling_state(True)
        try:
            assert dm._frame_hold == 1
        finally:
            dm.set_scrolling_state(False)


class TestSnapshotOffRenderThread:
    """Mid-scroll, the preview PNG is encoded off the render thread.

    At 512x64 the encode takes 12-14ms on a Pi 4 -- longer than a refresh --
    so doing it inline made the next swap miss its vsync five times a second
    whenever the web preview was open.
    """

    def _record_saves(self, dm, monkeypatch):
        import threading
        threads = []
        done = threading.Event()
        real = dm._save_snapshot

        def recording(image):
            threads.append(threading.current_thread().name)
            real(image)
            done.set()

        monkeypatch.setattr(dm, "_save_snapshot", recording)
        return threads, done

    def _due(self, dm, tmp_path, colour):
        dm._snapshot_path = str(tmp_path / "snap.png")
        dm._last_snapshot_ts = 0.0
        dm._last_snapshot_touch_ts = 0.0
        dm._last_snapshot_digest = None
        dm.draw.rectangle([0, 0, 10, 4], fill=colour)

    def test_scrolling_frames_are_encoded_on_the_writer_thread(
            self, dm, tmp_path, monkeypatch):
        import threading
        threads, done = self._record_saves(dm, monkeypatch)
        self._due(dm, tmp_path, (0, 255, 255))
        dm.set_scrolling_state(True)
        try:
            dm.update_display()
            assert done.wait(5), "the snapshot writer never wrote the frame"
        finally:
            dm.set_scrolling_state(False)
        assert threads == ["snapshot-writer"]
        assert threads[0] != threading.current_thread().name
        assert os.path.exists(dm._snapshot_path)

    def test_a_failed_background_write_is_retried_not_touched(
            self, dm, tmp_path, monkeypatch):
        # Queuing records the frame as written. If the writer then fails, an
        # unchanged frame must be written again, not mtime-touched: touching
        # would make a stale preview look healthy.
        import threading
        import time
        failed = threading.Event()

        def failing(image):
            failed.set()
            raise OSError("disk full")

        monkeypatch.setattr(dm, "_save_snapshot", failing)
        self._due(dm, tmp_path, (0, 255, 0))
        dm.set_scrolling_state(True)
        try:
            dm.update_display()
            assert failed.wait(5)
            deadline = time.time() + 5
            while dm._last_snapshot_digest is not None and time.time() < deadline:
                time.sleep(0.01)
        finally:
            dm.set_scrolling_state(False)
        assert dm._last_snapshot_digest is None

    def test_a_frame_not_yet_on_disk_is_written_not_touched(
            self, dm, tmp_path, monkeypatch):
        # The digest is recorded when a frame is queued. Until the writer has
        # saved it, an unchanged frame must not mtime-touch the older file on
        # disk into looking current.
        import zlib
        from src.common import snapshot_policy
        touched, saved = [], []
        self._due(dm, tmp_path, (9, 9, 9))
        digest = zlib.adler32(dm.image.tobytes())
        dm._last_snapshot_digest = digest        # queued earlier...
        dm._saved_snapshot_digest = 12345        # ...but an older frame is on disk
        monkeypatch.setattr(snapshot_policy, "decide",
                            lambda *a, **k: snapshot_policy.SnapshotAction.TOUCH)
        monkeypatch.setattr(os, "utime", lambda *a, **k: touched.append(a))
        monkeypatch.setattr(dm, "_save_snapshot", lambda image: saved.append(image))
        dm.set_scrolling_state(False)
        dm._write_snapshot_if_due(digest)
        assert touched == []
        assert len(saved) == 1
        assert dm._saved_snapshot_digest == digest

        # Once it is on disk, the same frame is only touched.
        dm._write_snapshot_if_due(digest)
        assert len(touched) == 1 and len(saved) == 1

    def test_a_static_frame_lands_after_a_queued_one_still_being_written(
            self, dm, tmp_path, monkeypatch):
        # The last frame of a scroll can still be encoding when the first
        # static frame is due; the older one must not land on top.
        import threading
        written, started, release = [], threading.Event(), threading.Event()
        real = dm._save_snapshot

        def slow_then_record(image):
            if threading.current_thread().name == "snapshot-writer":
                started.set()
                release.wait(5)
            written.append((threading.current_thread().name, image.getpixel((0, 0))))
            real(image)

        monkeypatch.setattr(dm, "_save_snapshot", slow_then_record)
        self._due(dm, tmp_path, (0, 0, 255))
        dm.set_scrolling_state(True)
        dm.update_display()                       # queued: the writer blocks mid-write
        assert started.wait(5)
        dm.set_scrolling_state(False)
        self._due(dm, tmp_path, (255, 0, 0))
        static = threading.Thread(target=dm.update_display)
        static.start()
        static.join(0.2)
        assert static.is_alive(), "the static save must wait for the write in flight"
        release.set()
        static.join(5)
        assert [colour for _, colour in written] == [(0, 0, 255), (255, 0, 0)]

    def test_cleanup_stops_the_writer(self, dm, tmp_path, monkeypatch):
        threads, done = self._record_saves(dm, monkeypatch)
        self._due(dm, tmp_path, (0, 255, 255))
        dm.set_scrolling_state(True)
        dm.update_display()
        assert done.wait(5)
        writer = dm._snapshot_thread
        dm.set_scrolling_state(False)
        dm._stop_snapshot_writer()
        writer.join(2)
        assert not writer.is_alive()

    def test_static_frames_are_still_written_inline(
            self, dm, tmp_path, monkeypatch):
        import threading
        threads, _ = self._record_saves(dm, monkeypatch)
        self._due(dm, tmp_path, (255, 0, 255))
        dm.set_scrolling_state(False)
        dm.update_display()
        assert threads == [threading.current_thread().name]


class TestLazyDigest:
    """Mid-scroll the frame is hashed only when the preview snapshot is due.

    The digest feeds two things: the dirty-tracking skip, which is never
    taken while scrolling, and the snapshot's changed-frame check, which acts
    at most once a second. Hashing every scrolled frame for them cost a
    tobytes() plus adler32 over the whole framebuffer on every frame (~0.3ms
    at 512x64 on a Pi 4). update_display() now leaves the digest out while
    scrolling, and _write_snapshot_if_due() hashes only when the policy could
    act on the answer.
    """

    @pytest.fixture
    def hashes(self, dm, tmp_path, monkeypatch):
        """Every adler32 display_manager takes, while no viewer is watching."""
        import types
        import zlib
        import src.display_manager as display_manager_module
        calls = []

        def counting(data, *args):
            calls.append(len(data))
            return zlib.adler32(data, *args)

        monkeypatch.setattr(display_manager_module, "zlib",
                            types.SimpleNamespace(adler32=counting))
        # The fixture's marker is the real /tmp one, which a preview open on
        # this machine would keep fresh. Point it somewhere nobody touches.
        monkeypatch.setattr(dm, "_viewer_marker_path", str(tmp_path / "no-viewer"))
        monkeypatch.setattr(dm, "_snapshot_path", str(tmp_path / "snap.png"))
        dm._viewer_check_ts = 0.0
        dm._viewer_fresh = False
        dm._viewer_was_fresh = False
        # TestSnapshotOffRenderThread's cleanup test stops this shared
        # manager's snapshot writer for good; queued writes here need one.
        with dm._snapshot_cond:
            dm._snapshot_stop = False
            dm._snapshot_pending = None
        yield calls
        dm.set_scrolling_state(False)
        dm._viewer_check_ts = 0.0
        dm._viewer_fresh = False
        dm._viewer_was_fresh = False

    def _scroll(self, dm, frames):
        """Present ``frames`` scrolled frames, each different from the last."""
        for shade in range(frames):
            dm.set_scrolling_state(True)       # as a scroller does every frame
            dm.draw.rectangle([0, 0, 10, 10], fill=(shade * 10 % 256, 40, 0))
            dm.update_display()

    def test_scrolled_frames_are_not_hashed_when_no_snapshot_is_due(
            self, dm, hashes):
        now = time.time()
        dm._last_snapshot_ts = now             # written a moment ago
        dm._last_snapshot_touch_ts = now
        with _SwapSpy(dm.matrix) as spy:
            self._scroll(dm, 20)
        assert spy.count == 20, "every scrolled frame must still be pushed"
        assert hashes == []

    def test_a_due_snapshot_hashes_the_frame_once(
            self, dm, hashes, monkeypatch):
        import threading
        wrote = threading.Event()
        monkeypatch.setattr(dm, "_save_snapshot", lambda image: wrote.set())
        dm._last_snapshot_ts = 0.0             # a write is due (no viewer:
        dm._last_snapshot_touch_ts = 0.0       # the idle keepalive)
        dm._last_snapshot_digest = None
        self._scroll(dm, 20)
        assert wrote.wait(5), "the due snapshot was never written"
        assert len(hashes) == 1

    def test_with_a_viewer_a_scroll_is_hashed_once_per_write(
            self, dm, hashes, tmp_path, monkeypatch):
        import threading
        from src.common import snapshot_policy
        marker = tmp_path / "viewer"
        marker.touch()
        monkeypatch.setattr(dm, "_viewer_marker_path", str(marker))
        writes = []
        wrote = threading.Event()

        def save(image):
            writes.append(image)
            wrote.set()

        monkeypatch.setattr(dm, "_save_snapshot", save)
        dm._last_snapshot_ts = time.time()     # the viewer opening resets it
        dm._last_snapshot_touch_ts = time.time()
        dm._last_snapshot_digest = None        # every frame here is new
        started = time.time()
        self._scroll(dm, 30)
        elapsed = time.time() - started
        assert wrote.wait(5)
        # One write as the viewer appears, then at most one per interval.
        assert 1 <= len(hashes) <= 1 + int(elapsed / snapshot_policy.VIEWER_INTERVAL)
        assert len(hashes) < 30

    def test_an_unchanged_scrolled_frame_is_not_re_encoded(
            self, dm, hashes, monkeypatch):
        # A write would be due for a changed frame, but this one is already
        # on disk: the lazy hash must re-ask the policy for an unchanged frame
        # (SKIP here) rather than act on the changed-frame answer (WRITE).
        import zlib
        saved, touched = [], []
        monkeypatch.setattr(dm, "_save_snapshot", lambda image: saved.append(image))
        monkeypatch.setattr(os, "utime", lambda *a, **k: touched.append(a))
        dm.set_scrolling_state(True)
        dm.draw.rectangle([0, 0, 10, 10], fill=(3, 3, 3))
        digest = zlib.adler32(dm.image.tobytes())
        dm._last_snapshot_digest = digest
        dm._saved_snapshot_digest = digest
        last_write = time.time() - 1000             # a changed frame would write
        dm._last_snapshot_ts = last_write
        dm._last_snapshot_touch_ts = time.time()    # no touch due
        dm.update_display()
        # A write, even one only queued, would have moved the bookkeeping.
        assert dm._last_snapshot_ts == last_write
        assert dm._snapshot_pending is None
        assert saved == [] and touched == []
        assert len(hashes) == 1

    def test_an_unchanged_scrolled_frame_is_touched_only_once_on_disk(
            self, dm, hashes, monkeypatch):
        # A touch vouches for the file on disk, so through the lazy hash too it
        # must only happen once the frame has actually been saved.
        import threading
        import zlib
        from src.common import snapshot_policy
        saved, touched = [], []
        wrote = threading.Event()

        def save(image):
            saved.append(image)
            wrote.set()

        monkeypatch.setattr(dm, "_save_snapshot", save)
        monkeypatch.setattr(os, "utime", lambda *a, **k: touched.append(a))
        dm.set_scrolling_state(True)
        dm.draw.rectangle([0, 0, 10, 10], fill=(5, 5, 5))
        digest = zlib.adler32(dm.image.tobytes())
        dm._last_snapshot_digest = digest      # queued earlier...
        dm._saved_snapshot_digest = 12345      # ...but an older frame is on disk
        # A touch is due; a write is not (no viewer: the idle interval).
        stale = time.time() - snapshot_policy.TOUCH_INTERVAL - 1.0
        dm._last_snapshot_ts = stale
        dm._last_snapshot_touch_ts = stale
        dm.update_display()
        assert wrote.wait(5), "a frame not yet on disk must be written"
        deadline = time.time() + 5
        while dm._saved_snapshot_digest != digest and time.time() < deadline:
            time.sleep(0.01)
        assert touched == [] and len(saved) == 1

        # Once it is on disk, the same frame is only touched.
        dm._last_snapshot_ts = stale
        dm._last_snapshot_touch_ts = stale
        dm.set_scrolling_state(True)
        dm.update_display()
        assert len(touched) == 1 and len(saved) == 1

    def test_the_first_static_frame_after_a_scroll_is_pushed_once(
            self, dm, hashes):
        # No digest is kept mid-scroll, so a static frame identical to the
        # scroll's last one is pushed once (one swap), and then dirty
        # tracking skips its repeats as before.
        dm.set_scrolling_state(True)
        dm.draw.rectangle([0, 0, 18, 18], fill=(90, 90, 0))
        dm.update_display()                    # the scroll's last frame
        dm.set_scrolling_state(False)
        with _SwapSpy(dm.matrix) as spy:
            dm.update_display()
            dm.update_display()
            dm.update_display()
        assert spy.count == 1


class TestOneScrollingAnswerPerFrame:
    """update_display() asks is_currently_scrolling() once per frame and uses
    that answer throughout. The call has side effects -- a scroll that has
    gone quiet past its threshold is expired there and its frame hold
    dropped -- so asking again partway through let one frame be presented at
    the scroll's hold and recorded as static."""

    def test_a_scroll_that_times_out_is_presented_without_its_hold(
            self, dm, monkeypatch):
        recorded = []
        monkeypatch.setattr(
            dm.frame_timing, "record",
            lambda blit, wait, hold, scrolling, at: recorded.append(
                (hold, scrolling)))
        dm.set_scrolling_state(True, frame_hold=3)
        # Quiet past the inactivity threshold, and nothing has asked since.
        dm._scrolling_state['last_scroll_activity'] -= (
            dm._scrolling_state['scroll_inactivity_threshold'] + 1.0)
        dm.draw.rectangle([0, 0, 8, 8], fill=(31, 62, 93))
        try:
            with _SwapSpy(dm.matrix) as spy:
                dm.update_display()
        finally:
            dm.set_scrolling_state(False)
        assert spy.count == 1
        assert spy.last_frame_hold == 1
        assert recorded == [(1, False)]

    def test_scan_compensation_uses_the_frames_answer(
            self, dm, tmp_path, monkeypatch):
        from collections import deque
        monkeypatch.setattr(dm, "_scan_lag_bands", [(16, 32, 1)])
        monkeypatch.setattr(dm, "_scan_history", deque(maxlen=1))
        # No preview and no snapshot due, so only the frame itself asks.
        monkeypatch.setattr(dm, "_viewer_marker_path", str(tmp_path / "no-viewer"))
        dm._viewer_check_ts = 0.0
        dm._last_snapshot_ts = dm._last_snapshot_touch_ts = time.time()
        asked = []
        real = dm.is_currently_scrolling

        def counting():
            asked.append(1)
            return real()

        monkeypatch.setattr(dm, "is_currently_scrolling", counting)
        dm.set_scrolling_state(True)
        try:
            dm.draw.rectangle([0, 0, 8, 8], fill=(17, 34, 51))
            dm.update_display()
        finally:
            dm.set_scrolling_state(False)
            dm._viewer_check_ts = 0.0
        assert len(asked) == 1


class TestSnapshotEncoding:
    def test_the_snapshot_is_written_at_the_fastest_level_and_lossless(
            self, dm, tmp_path, monkeypatch):
        # The encode is paid on the render thread for a static screen, and
        # the file is read once; level 1 cuts the encode, and PNG is lossless
        # at any level, so the preview shows exactly the frame.
        from PIL import Image, ImageChops
        levels = []
        real_save = Image.Image.save

        def recording(image, fp, format=None, **params):
            levels.append(params.get("compress_level"))
            return real_save(image, fp, format, **params)

        monkeypatch.setattr(Image.Image, "save", recording)
        monkeypatch.setattr(dm, "_snapshot_path", str(tmp_path / "snap.png"))
        frame = Image.new("RGB", (dm.width, dm.height))
        frame.paste((200, 10, 90), (3, 4, 40, 20))
        frame.putpixel((0, 0), (1, 2, 3))
        dm._save_snapshot(frame)
        assert levels == [1]
        with Image.open(dm._snapshot_path) as saved:
            assert saved.format == "PNG"
            assert ImageChops.difference(
                saved.convert("RGB"), frame).getbbox() is None
