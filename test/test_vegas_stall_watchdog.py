"""Tests the watchdog that catches a stalled render loop in the act.

Frame-time statistics can say a stall happened but not what caused it, and by
the time the numbers reach the log the stack is long gone. On a live rig the
Vegas loop showed a 3.2s freeze roughly twice an hour with every other frame
under 25ms -- invisible in the mean, and unattributable from the log alone.
This watchdog samples every thread's stack while the loop is still wedged,
which is how that freeze was traced to a plugin generating a 17,000px scroll
image, logo PNG decode and all, on the render thread.
"""

import threading
import time

import pytest

from src.vegas_mode.coordinator import _StallWatchdog


@pytest.fixture
def watchdog():
    made = []

    def build(threshold):
        w = _StallWatchdog(threshold)
        made.append(w)
        return w

    yield build
    for w in made:
        w.stop()
    for w in made:
        w._thread.join(timeout=2.0)
        assert not w._thread.is_alive(), "watchdog thread outlived its owner"


def _dumps(caplog):
    return [r for r in caplog.records if 'render loop stalled' in r.getMessage()]


class TestItFiresOnlyWhenStalled:
    def test_a_beating_loop_is_never_reported(self, watchdog, caplog):
        w = watchdog(0.2)
        deadline = time.time() + 0.9
        while time.time() < deadline:
            w.beat()
            time.sleep(0.02)
        assert not _dumps(caplog)

    def test_a_stalled_loop_is_reported(self, watchdog, caplog):
        w = watchdog(0.2)
        w.beat()
        time.sleep(0.9)
        assert _dumps(caplog), "no stall dump for a loop that stopped beating"

    def test_one_dump_per_stall_not_per_poll(self, watchdog, caplog):
        # The watchdog polls at threshold/4, so a stall lasting many poll
        # intervals must not flood the log with a dump each time.
        w = watchdog(0.2)
        w.beat()
        time.sleep(1.2)
        assert len(_dumps(caplog)) == 1, (
            "%d dumps for one stall" % len(_dumps(caplog)))

    def test_a_later_stall_is_reported_again(self, watchdog, caplog):
        w = watchdog(0.2)
        w.beat()
        time.sleep(0.6)
        first = len(_dumps(caplog))
        w.beat()                      # recovered
        time.sleep(0.6)               # then stalled again
        assert len(_dumps(caplog)) == first + 1


class TestWhatItReports:
    def test_the_dump_names_threads_and_shows_frames(self, watchdog, caplog):
        started = threading.Event()
        release = threading.Event()

        def parked():
            started.set()
            release.wait(3.0)

        t = threading.Thread(target=parked, name="CulpritThread", daemon=True)
        t.start()
        started.wait(2.0)
        try:
            w = watchdog(0.2)
            w.beat()
            time.sleep(0.7)
            dumps = _dumps(caplog)
            assert dumps
            text = dumps[0].getMessage()
            assert "CulpritThread" in text, text
            assert " in " in text, "no frames in the dump"
            assert ".py:" in text, "no file:line in the dump"
        finally:
            release.set()
            t.join(timeout=2.0)

    def test_it_reports_how_long_the_stall_ran(self, watchdog, caplog):
        w = watchdog(0.2)
        w.beat()
        time.sleep(0.8)
        text = _dumps(caplog)[0].getMessage()
        assert "stalled" in text
        # Long enough to have tripped, and not an absurd value.
        stalled = float(text.split("stalled")[1].split("s")[0])
        assert 0.2 <= stalled <= 3.0, stalled


class TestItIsCheapWhenIdle:
    def test_stop_is_prompt(self, caplog):
        w = _StallWatchdog(4.0)          # long threshold, long poll interval
        t0 = time.time()
        w.stop()
        w._thread.join(timeout=3.0)
        assert not w._thread.is_alive(), "stop() did not end the thread"
        assert time.time() - t0 < 2.0, "stop() waited out the poll interval"
