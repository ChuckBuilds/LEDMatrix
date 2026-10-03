"""Scroller-to-static handovers: the scroll state ends with the scroller.

Nothing ended the scroll state when the rotation moved from a scroller to a
static screen; it expired 2 s after the scroller's last frame. Two things
followed, both seen on ledpi (Pi 4, 96x48, scan-order compensation on):

* the static screen's first frame -- on the panel for a whole second -- went
  out with rows 24-47 taken from the ticker's last frame;
* the 1 Hz loop's second frame, a second later, was timed as a frame of the
  old scroll: a 1-2 s "freeze" in every soak and a "Render stall" in the log
  at every such handover (17 of 31 "Render stall over" lines).

The display controller now calls ``end_scroll_for_static_screen()`` just before
a static screen's first dispatch and ``set_scrolling_state(False)`` once it
returns, and tags that first frame ``handover`` for the frame-timing stats.
"""

import os
import sys
import types
from unittest.mock import MagicMock

os.environ.setdefault("EMULATOR", "true")

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.common.frame_timing import FrameTimingRecorder  # noqa: E402

PERIOD = 0.010  # a 100 Hz panel


@pytest.fixture
def dm(tmp_path):
    """A real DisplayManager on the emulator, sized like ledpi's panel."""
    from src.display_manager import DisplayManager
    DisplayManager._instance = None
    DisplayManager._initialized = False
    manager = DisplayManager({"display": {
        "hardware": {"rows": 48, "cols": 96, "chain_length": 1, "parallel": 1},
        "runtime": {"gpio_slowdown": 0}}}, suppress_test_pattern=True)
    # Not the fixed path the web UI reads, which every pytest run shares.
    manager._snapshot_path = str(tmp_path / "led_matrix_preview.png")
    if manager.matrix is None:
        pytest.fail("DisplayManager fell back to matrix=None; see the "
                    "'Failed to initialize RGB Matrix' log line above.")
    presented = []

    # update_display alternates between two canvases; watch both.
    for canvas in (manager.offscreen_canvas, manager.current_canvas):
        def capture(image, *args, _real=canvas.SetImage, **kwargs):
            presented.append(image.copy())
            return _real(image, *args, **kwargs)
        canvas.SetImage = capture
    manager._presented = presented
    yield manager
    manager.set_scrolling_state(False)
    DisplayManager._instance = None
    DisplayManager._initialized = False


def _push(dm, colour):
    dm.draw.rectangle([0, 0, dm.width - 1, dm.height - 1], fill=colour)
    dm.update_display()
    return dm._presented[-1]


def _watch_swaps(dm):
    """The refreshes each SwapOnVSync from here on holds its frame for."""
    holds = []
    real = dm.matrix.SwapOnVSync

    def swap(canvas, *args, **kwargs):
        holds.append(args[0] if args else kwargs.get("framerate_fraction", 1))
        return real(canvas, *args, **kwargs)
    dm.matrix.SwapOnVSync = swap
    return holds


# -- the first static frame ---------------------------------------------------


class TestTheFirstStaticFrame:
    def test_it_does_not_show_the_tickers_lagging_rows(self, dm):
        dm._scan_lag_bands = [(24, 48, 1)]   # ledpi: rows 24-47 a refresh behind
        dm.set_scrolling_state(True, 1)      # a ticker at one frame per refresh
        _push(dm, (255, 0, 0))               # its last frame
        dm.end_scroll_for_static_screen()    # the controller, before the dispatch
        shown = _push(dm, (0, 0, 0))         # the static screen's first frame
        assert shown.getpixel((10, 10)) == (0, 0, 0)
        assert shown.getpixel((10, 30)) == (0, 0, 0)

    def test_without_it_the_tickers_rows_are_shown(self, dm):
        # What happened before: the frame that stays up for a second is half
        # the old ticker. (Proves the test above can see the leak.)
        dm._scan_lag_bands = [(24, 48, 1)]
        dm.set_scrolling_state(True, 1)
        _push(dm, (255, 0, 0))
        shown = _push(dm, (0, 0, 0))
        assert shown.getpixel((10, 30)) == (255, 0, 0)

    def test_after_a_held_scroll_it_is_one_plain_swap(self, dm):
        # Scan-order compensation covers held frames too: mid-scroll, a frame
        # held 2 refreshes goes out as two swaps, the first with the lagging
        # rows from the frame before. The static screen's first frame is one
        # swap, as drawn, held for the scroll's own 2 refreshes.
        dm._scan_lag_bands = [(24, 48, 1)]
        dm.set_scrolling_state(True, 2)      # a crisp scroll: 1 px every 2 refreshes
        _push(dm, (255, 0, 0))               # its last frame
        holds = _watch_swaps(dm)
        before = len(dm._presented)
        dm._last_blit_seconds = 0.0          # fast enough to split, as on ledpi
        dm.end_scroll_for_static_screen()
        _push(dm, (0, 0, 0))
        assert len(dm._presented) - before == 1
        assert dm._presented[-1].getpixel((10, 30)) == (0, 0, 0)
        assert holds == [2]
        assert len(dm._scan_history) == 0    # nothing of the ticker kept

    def test_without_it_a_held_scroll_flashes_the_tickers_rows(self, dm):
        # What happens without the call: the frame's first refresh shows the
        # ticker's rows. (Proves the test above can see the leak.)
        dm._scan_lag_bands = [(24, 48, 1)]
        dm.set_scrolling_state(True, 2)
        _push(dm, (255, 0, 0))
        holds = _watch_swaps(dm)
        before = len(dm._presented)
        dm._last_blit_seconds = 0.0
        _push(dm, (0, 0, 0))
        first, second = dm._presented[before:]
        assert first.getpixel((10, 30)) == (255, 0, 0)
        assert second.getpixel((10, 30)) == (0, 0, 0)
        assert holds == [1, 1]

    def test_it_is_timed_like_any_frame_of_the_scroll(self, dm, monkeypatch):
        # One record, at the scroll's hold and still "scrolling": the gap to
        # it is judged against the scroller's pacing (see TestTheSoak).
        dm._scan_lag_bands = [(24, 48, 1)]
        dm.set_scrolling_state(True, 3)
        _push(dm, (255, 0, 0))
        records = []
        real = dm.frame_timing.record

        def record(*args, **kwargs):
            records.append(args)
            return real(*args, **kwargs)
        monkeypatch.setattr(dm.frame_timing, "record", record)
        dm.end_scroll_for_static_screen()
        _push(dm, (0, 0, 0))
        [(_blit, _wait, hold, scrolling, _at)] = records
        assert (hold, scrolling) == (3, True)

    def test_it_is_decided_on_the_frames_one_answer_and_not_hashed(
            self, dm, monkeypatch, tmp_path):
        # The handover frame goes through update_display like any frame while
        # the scroll state is set: it asks is_currently_scrolling() once, takes
        # no digest, and that answer and its hold reach the swap and the
        # record -- while the frame itself goes out as drawn.
        import time
        import zlib
        import src.display_manager as display_manager_module
        dm._scan_lag_bands = [(24, 48, 1)]
        # No preview and no snapshot due: only the frame itself asks or hashes.
        dm._viewer_marker_path = str(tmp_path / "no-viewer")
        dm._viewer_check_ts = 0.0
        dm.set_scrolling_state(True, 2)
        _push(dm, (255, 0, 0))               # the ticker's last frame
        dm._last_snapshot_ts = dm._last_snapshot_touch_ts = time.time()
        asked, hashed, records = [], [], []
        real_asked = dm.is_currently_scrolling

        def asking():
            asked.append(1)
            return real_asked()

        def hashing(data, *args):
            hashed.append(len(data))
            return zlib.adler32(data, *args)

        real_record = dm.frame_timing.record

        def record(*args, **kwargs):
            records.append(args[2:4])
            return real_record(*args, **kwargs)
        monkeypatch.setattr(dm, "is_currently_scrolling", asking)
        monkeypatch.setattr(display_manager_module, "zlib",
                            types.SimpleNamespace(adler32=hashing))
        monkeypatch.setattr(dm.frame_timing, "record", record)
        holds = _watch_swaps(dm)
        before = len(dm._presented)
        dm._last_blit_seconds = 0.0          # fast enough to split
        dm.end_scroll_for_static_screen()
        _push(dm, (0, 0, 0))
        assert len(asked) == 1
        assert hashed == []
        assert holds == [2]
        assert records == [(2, True)]
        assert len(dm._presented) - before == 1
        assert dm._presented[-1].getpixel((10, 30)) == (0, 0, 0)

    def test_nor_its_own_first_frame_under_its_second(self, dm):
        # A first display() that pushes two frames: a clear, then the screen.
        # Composed, the second would show the first's rows: a half-black frame
        # up for the whole second until the 1 Hz loop's next redraw.
        dm._scan_lag_bands = [(24, 48, 1)]
        dm.set_scrolling_state(True, 1)
        _push(dm, (255, 0, 0))               # the ticker's last frame
        dm.end_scroll_for_static_screen()
        _push(dm, (0, 0, 0))                 # the static screen clears...
        shown = _push(dm, (0, 0, 255))       # ...then draws
        assert shown.getpixel((10, 30)) == (0, 0, 255)
        dm.set_scrolling_state(False)        # the controller, after the dispatch
        pushed = len(dm._presented)
        # The 1 Hz redraw is pushed once, as drawn: a frame pushed while the
        # scroll state was set leaves dirty tracking no digest to match. The
        # next identical one is skipped.
        assert _push(dm, (0, 0, 255)).getpixel((10, 30)) == (0, 0, 255)
        assert len(dm._presented) == pushed + 1
        _push(dm, (0, 0, 255))
        assert len(dm._presented) == pushed + 1

    def test_the_next_scroll_is_compensated_again(self, dm):
        dm._scan_lag_bands = [(24, 48, 1)]
        dm.set_scrolling_state(True, 1)
        _push(dm, (255, 0, 0))
        dm.end_scroll_for_static_screen()
        _push(dm, (0, 0, 0))
        dm.set_scrolling_state(False)
        dm.set_scrolling_state(True, 1)      # the next ticker
        _push(dm, (0, 255, 0))
        assert _push(dm, (0, 0, 255)).getpixel((10, 30)) == (0, 255, 0)

    def test_it_keeps_the_scroll_state_and_its_hold(self, dm):
        dm.set_scrolling_state(True, 5)      # e.g. 2px every 5 refreshes
        dm.end_scroll_for_static_screen()
        # Both stay until the controller ends the scroll: the gap to the first
        # static frame is timed and watched, and due at the scroll's own
        # pacing (see TestTheSoak).
        assert dm.is_currently_scrolling()
        assert dm._frame_hold == 5
        dm.set_scrolling_state(False)
        assert dm._frame_hold == 1

    def test_a_thread_drawing_off_screen_cannot_end_the_live_scroll(self, dm):
        dm._scan_lag_bands = [(24, 48, 1)]
        dm.set_scrolling_state(True, 1)
        _push(dm, (255, 0, 0))
        with dm.offscreen():
            dm.end_scroll_for_static_screen()
        # Still the ticker's scroll: its next frame is composed as usual.
        assert _push(dm, (0, 0, 0)).getpixel((10, 30)) == (255, 0, 0)


# -- what the frame-timing soak records -------------------------------------


class _FakeTime:
    """display_manager's clock, moved by hand."""

    def __init__(self, t):
        self.t = t

    def time(self):
        return self.t

    perf_counter = monotonic = time


def _handover(dm, monkeypatch, tmp_path, controller_calls, first_frame_after=0.040,
              hold=1):
    """A scroll, then a static screen, presented through update_display.

    The scroll presents a frame every ``hold`` refreshes. The static screen's
    frames land 40 ms, 1.065 s and 2.09 s after the scroller's last frame: a
    handover, then the 1 Hz loop. With ``controller_calls`` the display
    manager is driven the way DisplayController.run() drives it at that
    handover.
    """
    clock = _FakeTime(1000.0)
    monkeypatch.setattr("src.display_manager.time", clock)
    recorder = FrameTimingRecorder(path=str(tmp_path / "stats.json"),
                                   flush_interval=1e9, refresh_hz=100.0)
    monkeypatch.setattr(dm, "frame_timing", recorder)

    for i in range(200):
        dm.set_scrolling_state(True, hold)
        dm.draw.rectangle([0, 0, 4, 4], fill=(i % 256, 0, 0))
        dm.update_display()
        clock.t += hold * PERIOD
    last_scroll_frame = clock.t - hold * PERIOD

    if controller_calls:
        dm.end_scroll_for_static_screen()
        recorder.note_op("handover")
    for n, offset in enumerate((first_frame_after, 1.065, 2.090)):
        clock.t = last_scroll_frame + offset
        # Each static frame differs, as a clock's does, so none is skipped.
        dm.draw.rectangle([0, 0, dm.width - 1, dm.height - 1], fill=(0, 0, 40 + n))
        dm.update_display()
        if controller_calls and n == 0:
            recorder.drop_op("handover")
            dm.set_scrolling_state(False)
    recorder.drain()
    return recorder.totals


class TestTheSoak:
    def test_a_handover_to_a_static_screen_is_not_a_freeze(self, dm, monkeypatch, tmp_path):
        totals = _handover(dm, monkeypatch, tmp_path, controller_calls=True)
        assert totals["freezes"] == 0
        assert totals["freeze_by"]["1-2s"] == 0
        assert totals["handover_freezes"] == 0
        # The handover interval itself is still timed, and carries its tag.
        assert totals["op_frames"] == {"handover": 1}
        assert totals["static_frames"] == 2

    def test_a_handover_on_the_scrollers_schedule_is_not_late(self, dm, monkeypatch,
                                                              tmp_path):
        # A scroll held 3 refreshes a frame, and a static screen whose first
        # frame lands 3 refreshes after its last one: on time, as it always
        # was. Judged at hold 1 it would be 2 refreshes late, and the soak's
        # pass/fail late count would grow with every such handover.
        totals = _handover(dm, monkeypatch, tmp_path, controller_calls=True,
                           first_frame_after=3 * PERIOD, hold=3)
        assert totals["op_frames"] == {"handover": 1}
        assert totals["late_op_frames"] == {}
        assert totals["late_frames"] == totals["missed_refreshes"] == 0

    def test_left_to_expire_it_was_one(self, dm, monkeypatch, tmp_path):
        # The old behaviour, for contrast: the 1 Hz loop's second frame was
        # still "scrolling" and ended a 1.025 s interval.
        totals = _handover(dm, monkeypatch, tmp_path, controller_calls=False)
        assert totals["freezes"] == 1
        assert totals["freeze_by"]["1-2s"] == 1

    def test_a_slow_first_frame_is_a_handover_gap_not_a_freeze(self, dm, monkeypatch,
                                                              tmp_path):
        # The next screen took 400 ms to draw its first frame.
        totals = _handover(dm, monkeypatch, tmp_path, controller_calls=True,
                           first_frame_after=0.400)
        assert totals["handover_freezes"] == 1
        assert totals["freezes"] == 0
        assert totals["freeze_seconds"] == 0.0
        assert totals["freeze_by"] == {"<0.5s": 0, "0.5-1s": 0, "1-2s": 0, "2s+": 0}
        assert totals["op_freezes"] == {"handover": 1}


# -- DisplayController.run() ---------------------------------------------------


class _Clock:
    """display_controller's clock: moves only when run() sleeps."""

    def __init__(self, start=10_000.0):
        self.t = start

    def now(self):
        return self.t

    def sleep(self, seconds):
        self.t += max(seconds, 0.0005)

    def module(self):
        return types.SimpleNamespace(time=self.now, monotonic=self.now,
                                     perf_counter=self.now, sleep=self.sleep)


class _Screen:
    """A plugin mode that records each display() call into ``events``."""

    def __init__(self, plugin_id, events, needs_high_fps, scrolls=False,
                 results=(), stop_after=None):
        self.plugin_id = plugin_id
        self.needs_high_fps = needs_high_fps
        self._events = events
        self._scrolls = scrolls
        self._results = iter(results)
        self._stop_after = stop_after
        self.display_manager = None
        self.calls = 0

    def display(self, force_clear=False):
        self.calls += 1
        if self._stop_after is not None and self.calls > self._stop_after:
            raise KeyboardInterrupt  # ends run(); it catches this and cleans up
        self._events.append(("display", self.plugin_id))
        if self._scrolls:
            # What a ticker does every frame.
            self.display_manager.set_scrolling_state(True, 2)
        return next(self._results, True)


@pytest.fixture
def controller(test_display_controller, monkeypatch):
    c = test_display_controller
    clock = _Clock()
    monkeypatch.setattr("src.display_controller.time", clock.module())
    c._refresh_config_cache({"display": {"hardware": {"brightness": 90}}})
    c.current_brightness = 90
    c.is_display_active = True
    c._check_wifi_status_message = MagicMock(return_value=None)
    c._cleanup_expired_wifi_status = MagicMock()
    c.cache_manager.get = MagicMock(return_value=None)   # no on-demand request
    c.plugin_manager.plugin_executor.execute_display.side_effect = (
        lambda target, plugin_id, force_clear=False, display_mode=None, **kw:
        target.display(force_clear=force_clear))

    events = []
    dm = c.display_manager
    dm.end_scroll_for_static_screen = MagicMock(
        side_effect=lambda: events.append("end_scroll"))
    dm.set_scrolling_state = MagicMock(
        side_effect=lambda state, *a, **k: events.append(("scrolling", state)))
    dm.frame_timing.note_op = MagicMock(
        side_effect=lambda kind, nbytes=0: events.append(("note", kind)))
    dm.frame_timing.drop_op = MagicMock(
        side_effect=lambda kind: events.append(("drop", kind)))
    c.events = events
    return c


def _rotation(c, screens, durations):
    c.plugin_modes.clear()
    c.mode_to_plugin_id.clear()
    c.plugin_display_modes.clear()
    for screen in screens:
        screen.display_manager = c.display_manager
        c.plugin_modes[screen.plugin_id] = screen
        c.mode_to_plugin_id[screen.plugin_id] = screen.plugin_id
        c.plugin_display_modes[screen.plugin_id] = [screen.plugin_id]
    c.available_modes = [screen.plugin_id for screen in screens]
    c.current_mode_index = 0
    c.current_display_mode = c.available_modes[0]
    c.config.setdefault("display", {})["display_durations"] = durations


def _turn(events, plugin_id):
    """The events from plugin_id's first display() to the next screen's."""
    first = events.index(("display", plugin_id))
    end = next((i for i in range(first + 1, len(events))
                if events[i][0] == "display" and events[i][1] != plugin_id),
               len(events))
    return first, events[first:end]


class TestRunLoop:
    def test_a_static_screen_ends_the_scroll_around_its_first_dispatch(self, controller):
        c = controller
        ticker = _Screen("ticker", c.events, needs_high_fps=True, scrolls=True)
        clock = _Screen("clock", c.events, needs_high_fps=False, stop_after=3)
        _rotation(c, [ticker, clock], {"ticker": 1, "clock": 30})

        c.run()

        events = c.events
        first, turn = _turn(events, "clock")
        # Before the first dispatch: the panel is readied, then the frame tagged.
        assert events[first - 2:first] == ["end_scroll", ("note", "handover")]
        # After it, before the 1 Hz loop's next frame: the tag dropped if no
        # frame took it, and the scroll ended.
        second = turn.index(("display", "clock"), 1)
        assert turn[1:second] == [("drop", "handover"), ("scrolling", False)]

    def test_a_scroller_keeps_its_state(self, controller):
        c = controller
        ticker = _Screen("ticker", c.events, needs_high_fps=True, scrolls=True,
                         stop_after=50)
        _rotation(c, [ticker], {"ticker": 60})

        c.run()

        assert "end_scroll" not in c.events
        assert ("scrolling", False) not in c.events
        # The first frame is still tagged: a slow scroller-to-scroller
        # handover is a handover gap, not a freeze.
        first = c.events.index(("display", "ticker"))
        assert c.events[first - 1] == ("note", "handover")

    def test_screens_that_declare_nothing_are_told_apart_by_enable_scrolling(
            self, controller):
        # Most plugins declare no needs_high_fps (the odds, stocks and news
        # tickers, the scoreboards): enable_scrolling decides. It now also
        # decides whether the scroll is ended before the first dispatch, so a
        # ticker read as static would lose its pacing every turn.
        c = controller
        ticker = _Screen("ticker", c.events, needs_high_fps=None, scrolls=True)
        ticker.enable_scrolling = True
        board = _Screen("board", c.events, needs_high_fps=None, stop_after=1)
        board.enable_scrolling = False
        _rotation(c, [ticker, board], {"ticker": 1, "board": 30})

        c.run()

        events = c.events
        first, turn = _turn(events, "board")
        assert "end_scroll" not in events[:first - 2]
        assert ("scrolling", False) not in events[:first - 2]
        assert events[first - 2:first] == ["end_scroll", ("note", "handover")]
        assert turn[1:3] == [("drop", "handover"), ("scrolling", False)]

    def test_an_old_static_image_is_still_a_high_fps_screen(self, controller):
        # static-image versions from before needs_high_fps declare nothing and
        # are forced to the high-FPS loop for their GIFs: not a static screen.
        c = controller
        image = _Screen("static-image", c.events, needs_high_fps=None, stop_after=5)
        _rotation(c, [image], {"static-image": 60})

        c.run()

        assert ("display", "static-image") in c.events
        assert "end_scroll" not in c.events
        assert ("scrolling", False) not in c.events

    def test_a_static_screen_whose_display_raises_still_ends_the_scroll(self, controller):
        class Raising(_Screen):
            def display(self, force_clear=False):
                self._events.append(("display", self.plugin_id))
                raise RuntimeError("plugin bug")

        c = controller
        ticker = _Screen("ticker", c.events, needs_high_fps=True, scrolls=True)
        broken = Raising("broken", c.events, needs_high_fps=False)
        clock = _Screen("clock", c.events, needs_high_fps=False, stop_after=1)
        _rotation(c, [ticker, broken, clock], {"ticker": 1, "broken": 30, "clock": 30})

        c.run()

        first, turn = _turn(c.events, "broken")
        assert c.events[first - 2:first] == ["end_scroll", ("note", "handover")]
        assert turn[1:3] == [("drop", "handover"), ("scrolling", False)]

    def test_a_raise_inside_the_executor_is_a_failure_and_still_ends_the_scroll(
            self, controller):
        # The real executor, with raise_errors: a display() that raises comes
        # back as a PluginError, which the breaker records as a failure (not
        # a success). The handover is finished after that record, and only
        # touches the display manager.
        import threading
        from src.plugin_system.plugin_executor import PluginExecutor

        class Raising(_Screen):
            def display(self, force_clear=False):
                self._events.append(("display", self.plugin_id))
                raise RuntimeError("plugin bug")

        c = controller
        pm = c.plugin_manager
        pm.plugin_executor = PluginExecutor(default_timeout=5.0)
        locks = {}
        pm.get_plugin_lock = lambda pid: locks.setdefault(pid, threading.Lock())
        tracker = MagicMock()
        tracker.should_skip_plugin.return_value = False
        tracker.record_failure.side_effect = (
            lambda pid, exc=None: c.events.append(("failure", pid, str(exc))))
        tracker.record_success.side_effect = (
            lambda pid: c.events.append(("success", pid)))
        pm.health_tracker = tracker
        ticker = _Screen("ticker", c.events, needs_high_fps=True, scrolls=True)
        broken = Raising("broken", c.events, needs_high_fps=False)
        clock = _Screen("clock", c.events, needs_high_fps=False, stop_after=1)
        _rotation(c, [ticker, broken, clock], {"ticker": 1, "broken": 30, "clock": 30})

        c.run()

        first, turn = _turn(c.events, "broken")
        assert c.events[first - 2:first] == ["end_scroll", ("note", "handover")]
        assert turn[1:4] == [("failure", "broken", "plugin bug"),
                             ("drop", "handover"), ("scrolling", False)]
        assert ("success", "broken") not in c.events
        assert tracker.record_failure.call_count == 1

    def test_every_turn_starts_with_a_handover_even_of_the_same_mode(self, controller):
        # A one-mode rotation (or a mode kept on by live priority) comes back
        # to itself, and that turn's first frame is a first display() too:
        # a scroller rebuilding its content there is a handover gap, not a
        # freeze. See "Handover gaps" in docs/SCROLL_PERFORMANCE.md.
        c = controller
        ticker = _Screen("ticker", c.events, needs_high_fps=True, scrolls=True,
                         stop_after=200)
        _rotation(c, [ticker], {"ticker": 1})

        c.run()

        notes = [i for i, event in enumerate(c.events) if event == ("note", "handover")]
        assert len(notes) == 2
        assert all(c.events[i + 1] == ("display", "ticker") for i in notes)

    def test_a_static_screen_with_nothing_to_show_still_ends_the_scroll(self, controller):
        c = controller
        ticker = _Screen("ticker", c.events, needs_high_fps=True, scrolls=True)
        empty = _Screen("empty", c.events, needs_high_fps=False, results=[False])
        clock = _Screen("clock", c.events, needs_high_fps=False, stop_after=1)
        _rotation(c, [ticker, empty, clock], {"ticker": 1, "empty": 30, "clock": 30})

        c.run()

        first, turn = _turn(c.events, "empty")
        assert c.events[first - 2:first] == ["end_scroll", ("note", "handover")]
        assert turn[1:3] == [("drop", "handover"), ("scrolling", False)]

    def test_a_busy_plugin_is_not_tagged(self, controller):
        # update() holds the plugin's lock, so its first dispatch is skipped
        # and presents nothing: no tag to leave lying around.
        c = controller
        clock = _Screen("clock", c.events, needs_high_fps=False)
        _rotation(c, [clock], {"clock": 3})
        busy = MagicMock()
        busy.acquire.return_value = False
        c.plugin_manager.get_plugin_lock.return_value = busy
        c._sleep_with_plugin_updates = MagicMock(side_effect=KeyboardInterrupt)
        stop = MagicMock(side_effect=[None, None, None, KeyboardInterrupt])
        c._tick_plugin_updates = stop

        c.run()

        assert ("note", "handover") not in c.events
        assert c.events[:3] == ["end_scroll", ("drop", "handover"), ("scrolling", False)]

    def test_a_plugin_whose_fps_flag_raises_does_not_stop_the_display(self, controller):
        class Broken(_Screen):
            @property
            def needs_high_fps(self):
                raise RuntimeError("plugin bug")

            @needs_high_fps.setter
            def needs_high_fps(self, value):
                pass

        c = controller
        broken = Broken("broken", c.events, needs_high_fps=None, results=[False])
        clock = _Screen("clock", c.events, needs_high_fps=False, stop_after=1)
        _rotation(c, [broken, clock], {"broken": 30, "clock": 30})

        c.run()

        assert ("display", "broken") in c.events
        assert ("display", "clock") in c.events   # the loop went on
        # Undecidable, so the broken screen's turn left the scroll state alone.
        _, turn = _turn(c.events, "broken")
        assert ("scrolling", False) not in turn


def _core_screen_controller(dm):
    """Just enough of a DisplayController to draw its own screens on ``dm``."""
    from src.display_controller import DisplayController
    c = MagicMock()
    c.display_manager = dm
    c.on_demand_active = False
    c._end_scroll_before_core_screen = types.MethodType(
        DisplayController._end_scroll_before_core_screen, c)
    return c


def _record_scrolling(dm, monkeypatch):
    """The ``scrolling`` flag each presented frame is timed with."""
    flags = []
    real = dm.frame_timing.record

    def record(*args, **kwargs):
        flags.append(args[3])
        return real(*args, **kwargs)
    monkeypatch.setattr(dm.frame_timing, "record", record)
    return flags


class TestTheControllersOwnScreens:
    """The schedule-off blank and the WiFi notice are drawn by the controller,
    not dispatched to a plugin, so they end the scroll themselves."""

    def test_the_schedule_off_blank_shows_none_of_the_ticker(self, dm, monkeypatch):
        from src.display_controller import DisplayController
        dm._scan_lag_bands = [(24, 48, 1)]
        dm.set_scrolling_state(True, 1)
        _push(dm, (255, 0, 0))               # the ticker's last frame
        flags = _record_scrolling(dm, monkeypatch)
        c = _core_screen_controller(dm)
        DisplayController._blank_while_scheduled_off(c, 60.0)
        assert dm._presented[-1].getpixel((10, 30)) == (0, 0, 0)
        assert flags == [False]              # a static frame, not a freeze
        assert not dm.is_currently_scrolling()
        c._sleep_with_plugin_updates.assert_called_once_with(60)

    def test_the_wifi_notice_shows_none_of_the_ticker(self, dm, monkeypatch):
        from src.display_arbiter import WifiNotice
        from src.display_controller import DisplayController
        dm._scan_lag_bands = [(24, 48, 1)]
        dm.set_scrolling_state(True, 1)
        _push(dm, (255, 0, 0))
        flags = _record_scrolling(dm, monkeypatch)
        c = _core_screen_controller(dm)
        c._display_wifi_status_message.side_effect = lambda _s: _push(dm, (0, 0, 255)) and True
        notice = WifiNotice(message="x", expires_at=1e12)
        assert DisplayController._show_wifi_notice(c, notice, 0.5) is True
        assert dm._presented[-1].getpixel((10, 30)) == (0, 0, 255)
        assert flags == [False]

    def test_no_notice_leaves_the_scroll_alone(self, dm):
        """With no notice the Arbiter does not pick the WiFi screen, so
        nothing ends the scroll."""
        from src.display_arbiter import Arbiter, ArbiterState, Source
        from src.display_controller import DisplayController
        dm.set_scrolling_state(True, 2)
        c = _core_screen_controller(dm)
        c.is_display_active = True
        c.on_demand_schedule_override = False
        c.sync_manager.is_follower_active.return_value = False
        c._read_wifi_notice = types.MethodType(DisplayController._read_wifi_notice, c)
        c._check_wifi_status_message.return_value = None
        inputs = DisplayController._arbiter_inputs(c)
        assert inputs.wifi_notice is None
        assert Arbiter.decide(ArbiterState(), inputs, 0.0).source is Source.LEGACY
        assert dm.is_currently_scrolling()
        assert dm._frame_hold == 2
