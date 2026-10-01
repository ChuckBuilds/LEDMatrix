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

    def test_it_drops_the_hold_and_keeps_the_scroll_state(self, dm):
        dm.set_scrolling_state(True, 5)      # e.g. 2px every 5 refreshes
        dm.end_scroll_for_static_screen()
        assert dm._frame_hold == 1
        # Still scrolling until the controller says otherwise, so the gap to
        # the first static frame is timed and watched.
        assert dm.is_currently_scrolling()

    def test_a_thread_drawing_off_screen_cannot_end_the_live_scroll(self, dm):
        dm.set_scrolling_state(True, 3)
        with dm.offscreen():
            dm.end_scroll_for_static_screen()
        assert dm._frame_hold == 3


# -- what the frame-timing soak records -------------------------------------


class _FakeTime:
    """display_manager's clock, moved by hand."""

    def __init__(self, t):
        self.t = t

    def time(self):
        return self.t

    perf_counter = monotonic = time


def _handover(dm, monkeypatch, tmp_path, controller_calls, first_frame_after=0.040):
    """A scroll, then a static screen, presented through update_display.

    The static screen's frames land 40 ms, 1.065 s and 2.09 s after the
    scroller's last frame: a handover, then the 1 Hz loop. With
    ``controller_calls`` the display manager is driven the way
    DisplayController.run() drives it at that handover.
    """
    clock = _FakeTime(1000.0)
    monkeypatch.setattr("src.display_manager.time", clock)
    recorder = FrameTimingRecorder(path=str(tmp_path / "stats.json"),
                                   flush_interval=1e9, refresh_hz=100.0)
    monkeypatch.setattr(dm, "frame_timing", recorder)

    for i in range(200):
        dm.set_scrolling_state(True, 1)
        dm.draw.rectangle([0, 0, 4, 4], fill=(i % 256, 0, 0))
        dm.update_display()
        clock.t += PERIOD
    last_scroll_frame = clock.t - PERIOD

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
