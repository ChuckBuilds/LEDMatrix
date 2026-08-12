"""Tests the percentile used by the Vegas frame-time log line.

The FPS line reports p99 next to the worst frame, and the point of having both
is that they say different things: p99 is the bad-but-ordinary frame, worst is
the outlier. The obvious index, int(n * 0.99), is off by one and at exactly
100 samples selects the maximum -- so the two columns would report the same
number precisely when the sample was smallest.
"""

import math

import pytest

from src.vegas_mode.coordinator import _percentile


class TestNearestRank:
    def test_a_hundred_samples_do_not_return_the_maximum(self):
        ordered = [float(i) for i in range(100)]      # 0..99
        assert _percentile(ordered, 0.99) == 98.0
        assert _percentile(ordered, 0.99) != max(ordered)

    def test_it_matches_the_nearest_rank_definition(self):
        for n in (1, 2, 3, 10, 99, 100, 101, 600, 1000):
            ordered = [float(i) for i in range(n)]
            expected = ordered[min(n - 1, max(0, math.ceil(n * 0.99) - 1))]
            assert _percentile(ordered, 0.99) == expected, n

    @pytest.mark.parametrize('fraction,expected', [
        (0.0, 0.0),      # first
        (0.5, 49.0),     # median, nearest-rank
        (1.0, 99.0),     # last
    ])
    def test_other_fractions(self, fraction, expected):
        assert _percentile([float(i) for i in range(100)], fraction) == expected


class TestEdges:
    def test_empty_is_zero_not_an_error(self):
        # The loop calls this before any frame has been timed.
        assert _percentile([], 0.99) == 0.0

    def test_a_single_sample_is_itself(self):
        assert _percentile([4.2], 0.99) == 4.2

    def test_it_never_indexes_past_the_end(self):
        for n in range(1, 50):
            _percentile([float(i) for i in range(n)], 1.0)   # must not raise


class TestItSaysSomethingUsefulAboutFrames:
    def test_one_freeze_does_not_drag_p99_up(self):
        # 599 healthy frames and one 3.2s freeze: p99 should still describe
        # the healthy population, while the worst frame is reported separately.
        frames = [0.0083] * 599 + [3.2]
        p99 = _percentile(sorted(frames), 0.99)
        assert p99 == pytest.approx(0.0083), p99
        assert max(frames) == 3.2

    def test_sustained_slowness_does_move_it(self):
        # Ten percent of frames slow is not an outlier, it is the shape of the
        # distribution, and p99 must reflect that.
        frames = [0.0083] * 540 + [0.05] * 60
        assert _percentile(sorted(frames), 0.99) == pytest.approx(0.05)
