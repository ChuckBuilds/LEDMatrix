"""Tests for grading a run of presented frames against the panel refresh.

The arithmetic here decides whether a rig ships, so it is pinned down without
hardware: every case below is a list of frame intervals with a known verdict.
"""

import json
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.common.frame_pacing import (  # noqa: E402
    DEFAULT_MAX_MISSED_PERCENT,
    analyze,
    measure_refresh_hz,
    refresh_from_intervals,
)

HZ = 100.0
PERIOD = 1.0 / HZ


class TestAPerfectlyPacedRun:
    def test_reports_the_panel_rate(self):
        report = analyze([PERIOD] * 1000, HZ, 1)
        assert report.presented_fps == pytest.approx(100.0)
        assert report.expected_fps == pytest.approx(100.0)
        assert report.missed == 0
        assert report.locked
        assert report.passed()

    def test_counts_intervals_not_frames(self):
        # Ten pushes give nine intervals: the first push has no predecessor.
        assert analyze([PERIOD] * 9, HZ, 1).frames == 9

    def test_a_held_frame_is_paced_at_the_fraction(self):
        report = analyze([2 * PERIOD] * 500, HZ, 2)
        assert report.expected_fps == pytest.approx(50.0)
        assert report.presented_fps == pytest.approx(50.0)
        assert report.missed == 0
        assert report.early == 0
        assert report.locked


class TestWhatCountsAsMissed:
    def test_a_frame_that_slips_a_whole_refresh(self):
        report = analyze([PERIOD] * 99 + [2 * PERIOD], HZ, 1)
        assert report.missed == 1
        assert report.histogram == {1: 99, 2: 1}

    def test_running_a_little_long_is_not_a_miss(self):
        # 11ms on a 10ms refresh still presented on the refresh it was meant
        # to. Counting it would make every run fail for no visible reason.
        assert analyze([0.011] * 100, HZ, 1).missed == 0

    def test_past_the_halfway_point_is_a_miss(self):
        assert analyze([0.0151] * 100, HZ, 1).missed == 100

    def test_two_refreshes_late_still_counts_once(self):
        # The metric is "frames that slipped", not "refreshes lost".
        report = analyze([PERIOD] * 99 + [3 * PERIOD], HZ, 1)
        assert report.missed == 1
        assert report.histogram[3] == 1

    def test_the_hold_moves_the_target(self):
        # 20ms frames are perfect at hold 2 and a miss at hold 1. Grading a run
        # against the wrong hold is the easiest way to report a false pass.
        assert analyze([2 * PERIOD] * 100, HZ, 2).missed == 0
        assert analyze([2 * PERIOD] * 100, HZ, 1).missed == 100


class TestTheGate:
    def test_one_in_a_thousand_sits_exactly_on_it(self):
        report = analyze([PERIOD] * 999 + [2 * PERIOD], HZ, 1)
        assert report.missed_percent == pytest.approx(0.1)
        assert report.passed(DEFAULT_MAX_MISSED_PERCENT)

    def test_two_in_a_thousand_does_not(self):
        report = analyze([PERIOD] * 998 + [2 * PERIOD] * 2, HZ, 1)
        assert not report.passed(DEFAULT_MAX_MISSED_PERCENT)

    def test_a_looser_gate_can_be_asked_for(self):
        report = analyze([PERIOD] * 990 + [2 * PERIOD] * 10, HZ, 1)
        assert not report.passed(0.1)
        assert report.passed(1.0)


class TestAnUnlockedRunNeverPasses:
    def test_a_loop_faster_than_the_panel_is_not_locked(self):
        # 8ms frames on a 100Hz panel: every one lands in the 1-refresh bucket,
        # so the miss count is zero, but 125fps is not something a panel at
        # 100Hz can present. The swap did not block.
        report = analyze([0.008] * 1000, HZ, 1)
        assert report.missed == 0
        assert not report.locked
        assert not report.passed()

    def test_a_whole_refresh_early_is_counted(self):
        report = analyze([PERIOD] * 100, HZ, 2)
        assert report.early == 100
        assert not report.locked

    def test_a_loop_stuck_at_half_rate_is_not_locked(self):
        # Every frame lands on a whole number of refreshes and the timing is
        # perfectly even -- but it is not the hold that was asked for.
        report = analyze([2 * PERIOD] * 1000, HZ, 1)
        assert not report.locked
        assert not report.passed()

    def test_the_verdict_says_so(self):
        text = analyze([0.008] * 100, HZ, 1).describe()
        assert "NOT LOCKED" in text
        assert text.rstrip().endswith("FAIL")


class TestDegenerateInput:
    def test_no_intervals(self):
        report = analyze([], HZ, 1)
        assert report.frames == 0
        assert report.missed_percent == 0.0
        assert not report.locked
        assert not report.passed()
        assert report.describe() == "no frames measured"

    def test_a_refresh_rate_of_zero(self):
        report = analyze([PERIOD] * 10, 0.0, 1)
        assert report.frames == 0
        assert report.expected_period == 0.0
        assert not report.passed()

    def test_unusable_samples_are_dropped(self):
        # A clock that went backwards, or a caller that padded the list.
        report = analyze([PERIOD, 0.0, -1.0, None, PERIOD], HZ, 1)
        assert report.frames == 2

    def test_a_hold_below_one_is_treated_as_one(self):
        assert analyze([PERIOD] * 10, HZ, 0).frame_hold == 1


class TestTheNumbersReported:
    def test_percentiles_are_nearest_rank(self):
        samples = [0.001 * n for n in range(1, 101)]
        report = analyze(samples, HZ, 1)
        assert report.p95 == pytest.approx(0.095)
        assert report.p99 == pytest.approx(0.099)
        assert report.maximum == pytest.approx(0.100)
        assert report.minimum == pytest.approx(0.001)

    def test_seconds_defaults_to_the_span_of_the_run(self):
        assert analyze([PERIOD] * 100, HZ, 1).seconds == pytest.approx(1.0)

    def test_seconds_can_be_given_for_a_run_with_gaps(self):
        assert analyze([PERIOD] * 100, HZ, 1, seconds=12.5).seconds == 12.5

    def test_the_report_survives_json(self):
        payload = analyze([PERIOD] * 99 + [2 * PERIOD], HZ, 1).as_dict()
        restored = json.loads(json.dumps(payload))
        assert restored["missed"] == 1
        assert restored["locked"] is True
        assert restored["histogram"] == {"1": 99, "2": 1}


class FakePanel:
    """A matrix whose swaps block for a fixed period, like real vsync."""

    def __init__(self, period, fail=False):
        self.period = period
        self.fail = fail
        self.swaps = 0

    def CreateFrameCanvas(self):
        if self.fail:
            raise RuntimeError("no hardware here")
        return object()

    def SwapOnVSync(self, canvas, framerate_fraction=1):
        self.swaps += 1
        time.sleep(self.period)
        return canvas


class TestMeasuringTheRefreshRate:
    def test_times_the_swaps_and_not_the_loop(self):
        # The upper bound is the half that carries the meaning: a loop that
        # spun without waiting for each swap would report far more than the
        # 200Hz a 5ms swap allows. The lower bound is loose on purpose --
        # sleep() under a loaded test runner overshoots, and a slow answer
        # here is the runner, not a bug.
        measured = measure_refresh_hz(FakePanel(0.005), seconds=0.2)
        assert 0 < measured <= 210.0

    def test_the_first_swap_is_discarded(self):
        panel = FakePanel(0.005)
        measure_refresh_hz(panel, seconds=0.05)
        assert panel.swaps >= 2

    def test_a_matrix_without_hardware_reports_nothing(self):
        assert measure_refresh_hz(FakePanel(0.0, fail=True), seconds=0.1) == 0.0

    def test_an_object_that_is_not_a_matrix_reports_nothing(self):
        assert measure_refresh_hz(object(), seconds=0.1) == 0.0


class TestReadingTheRefreshBackFromTheFrames:
    """The panel is slower while the Pi is pushing frames into it.

    Measured on a Pi 4 with a 512x64 chain: 100.4Hz idle, 96.3Hz mid-scroll.
    Grading against the idle number is what these tests exist to prevent.
    """

    def test_a_locked_run_reports_its_own_rate(self):
        # Intervals clustered just above a 10.38ms period, as a locked loop on
        # a panel holding 96.3Hz actually looks.
        samples = [0.01038 + 0.00002 * (n % 20) for n in range(500)]
        assert refresh_from_intervals(samples, 1) == pytest.approx(96.3, abs=0.5)

    def test_the_hold_is_divided_out(self):
        assert refresh_from_intervals([2 * PERIOD] * 500, 2) == pytest.approx(HZ)

    def test_one_short_sample_does_not_set_the_period(self):
        # A single 5ms outlier among 10ms frames would, if the minimum were
        # used, claim a 200Hz panel and make every real frame a miss.
        samples = [0.005] + [PERIOD] * 499
        assert refresh_from_intervals(samples, 1) == pytest.approx(HZ, abs=1.0)

    def test_too_few_samples_to_say(self):
        assert refresh_from_intervals([PERIOD] * 5, 1) == 0.0
        assert refresh_from_intervals([], 1) == 0.0

    def test_the_idle_rate_makes_a_locked_run_look_slow(self):
        samples = [0.01038] * 1000
        idle = analyze(samples, 100.4, 1)
        assert idle.presented_fps == pytest.approx(96.3, abs=0.1)
        assert idle.expected_fps == pytest.approx(100.4)

        loaded = analyze(samples, refresh_from_intervals(samples, 1), 1)
        assert loaded.presented_fps == pytest.approx(loaded.expected_fps)
        assert loaded.missed == 0
        assert loaded.locked

    def test_a_big_enough_drop_becomes_a_miss_on_every_frame(self):
        # Half the idle rate: each frame spans two idle refreshes, so grading
        # against idle calls all of them late. Against the rate the panel held,
        # none of them are.
        samples = [2 * PERIOD] * 1000
        assert analyze(samples, HZ, 1).missed == 1000
        assert analyze(samples, refresh_from_intervals(samples, 1), 1).missed == 0
