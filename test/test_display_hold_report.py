"""The report of a scrolling screen held by its plugin's update().

While a plugin's update() runs it holds the plugin's lock, and its screen's
frames are skipped -- on a scroller, a frozen strip -- with nothing logged.
_note_display_hold times each such run and reports one of
DISPLAY_HOLD_REPORT_SECONDS or more.
"""

import threading
import types
from unittest.mock import MagicMock

import pytest


class _Clock:
    """display_controller's clock: moves only when run() sleeps or a test says."""

    def __init__(self, start=10_000.0):
        self.t = start

    def now(self):
        return self.t

    def sleep(self, seconds):
        self.t += max(seconds, 0.0005)

    def module(self):
        return types.SimpleNamespace(time=self.now, monotonic=self.now,
                                     perf_counter=self.now, sleep=self.sleep)


@pytest.fixture
def clock(monkeypatch):
    c = _Clock()
    monkeypatch.setattr("src.display_controller.time", c.module())
    return c


class _Locks:
    """get_plugin_lock for one plugin, whose lock the test can hold."""

    def __init__(self):
        self.lock = threading.Lock()

    def __call__(self, plugin_id):
        return self.lock


@pytest.fixture
def held(test_display_controller):
    c = test_display_controller
    locks = _Locks()
    c.plugin_manager.get_plugin_lock = locks
    c.plugin_manager._warn_rate_limited = MagicMock()
    c.plugin_manager.health_tracker = MagicMock()
    c._display_hold = None
    return c, locks.lock


def _plugin(plugin_id):
    p = MagicMock()
    p.plugin_id = plugin_id
    p.display.return_value = True
    return p


class TestTheDisplayHoldReport:
    def test_a_long_hold_is_reported_when_it_ends(self, held, clock):
        c, lock = held
        ticker = _plugin("ticker")
        lock.acquire()                         # update() running
        assert c._display_once(ticker, "ticker", False, report_hold=True) is True
        clock.t += 0.2
        assert c._display_once(ticker, "ticker", False, report_hold=True) is True
        assert ticker.display.call_count == 0
        c.plugin_manager._warn_rate_limited.assert_not_called()
        clock.t += 0.2
        lock.release()                         # update() done
        c._display_once(ticker, "ticker", False, report_hold=True)
        assert ticker.display.call_count == 1
        key, message, plugin_id, ms = c.plugin_manager._warn_rate_limited.call_args[0]
        assert key == "display-hold:ticker" and plugin_id == "ticker"
        assert "held" in message and ms == pytest.approx(400.0)
        c.plugin_manager.health_tracker.record_busy_skip.assert_called_once_with(
            "ticker", "display hold", pytest.approx(0.4))

    def test_a_short_hold_is_not(self, held, clock):
        c, lock = held
        ticker = _plugin("ticker")
        lock.acquire()
        c._display_once(ticker, "ticker", False, report_hold=True)
        clock.t += 0.1
        lock.release()
        c._display_once(ticker, "ticker", False, report_hold=True)
        c.plugin_manager._warn_rate_limited.assert_not_called()
        c.plugin_manager.health_tracker.record_busy_skip.assert_not_called()

    def test_a_hold_that_ends_on_another_plugins_screen_is_not_blamed_on_it(
            self, held, clock):
        c, lock = held
        lock.acquire()
        c._display_once(_plugin("ticker"), "ticker", False, report_hold=True)
        clock.t += 1.0
        lock.release()
        c._display_once(_plugin("clock"), "clock", False, report_hold=True)
        c.plugin_manager._warn_rate_limited.assert_not_called()
        assert c._display_hold is None

    def test_frames_that_draw_report_nothing(self, held, clock):
        c, _lock = held
        ticker = _plugin("ticker")
        for _ in range(5):
            c._display_once(ticker, "ticker", False, report_hold=True)
            clock.t += 0.5
        c.plugin_manager._warn_rate_limited.assert_not_called()

    def test_the_1hz_loop_reports_no_holds(self, held, clock):
        # A static screen's frames are a second apart: one skipped frame is
        # not a measured hold, and nothing on the panel froze. (On ledpi the
        # first version reported every such skip as "held 1000 ms".)
        c, lock = held
        board = _plugin("board")
        lock.acquire()
        c._display_once(board, "board", False)
        clock.t += 1.0
        lock.release()
        c._display_once(board, "board", False)
        c.plugin_manager._warn_rate_limited.assert_not_called()
        c.plugin_manager.health_tracker.record_busy_skip.assert_not_called()

    def test_a_run_left_open_is_dropped_by_a_1hz_frame(self, held, clock):
        c, lock = held
        ticker = _plugin("ticker")
        lock.acquire()
        c._display_once(ticker, "ticker", False, report_hold=True)
        lock.release()
        c._display_once(ticker, "ticker", False)        # the 1 Hz loop draws
        assert c._display_hold is None
        clock.t += 5.0
        c._display_once(ticker, "ticker", False, report_hold=True)
        c.plugin_manager._warn_rate_limited.assert_not_called()
