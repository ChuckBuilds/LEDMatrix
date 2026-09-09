"""Smooth motion is a per-frame property, not a frame-rate one.

Frame-time statistics on this project have twice looked perfect while a
marquee still visibly stuttered. They measure the wrong thing. What the eye
judges is whether the strip advances the SAME number of whole pixels on every
presented frame, and that is what these tests measure.

The old stepping derived position from ``scroll_speed * delta_time`` and then
truncated it to a pixel, so any jitter in delta_time landed either side of an
integer boundary. Against the frame times measured on hardware -- a rock-steady
100.0 fps whose individual frames still ranged 5.6ms to 15.2ms -- about 5.8% of
frames advanced 0 or 2 pixels instead of 1. Roughly six hitches a second.
"""
import random
from collections import Counter
from unittest.mock import patch

import numpy as np
import pytest
from PIL import Image

from src.common import scroll_config
from src.common.scroll_helper import ScrollHelper

#: Wide enough that no test consumes the strip. A scroll that completes freezes
#: at the end, and counting those frozen frames as "did not advance" is how the
#: first version of this measurement talked itself into a wrong answer.
STRIP_WIDTH = 60000


class _Panel:
    """A display manager that only has to answer the refresh question."""

    refresh_hz = 100.0

    def set_frame_hold(self, refreshes):
        self.hold = refreshes


def _helper():
    helper = ScrollHelper(128, 64)
    helper.cached_image = Image.new("RGB", (STRIP_WIDTH, 64))
    helper.cached_array = np.zeros((64, STRIP_WIDTH, 3), dtype=np.uint8)
    helper.total_scroll_width = STRIP_WIDTH
    return helper


def _measured_frame_times(count, seed=7):
    """Frame gaps shaped like the ones logged on hardware.

    100.0 fps overall and a 10.00ms median, with the short and long tails that
    a real vsync-paced loop actually produces.
    """
    rng = random.Random(seed)
    out = []
    for _ in range(count):
        roll = rng.random()
        if roll < 0.04:
            out.append(rng.uniform(5.6, 7.5))
        elif roll < 0.08:
            out.append(rng.uniform(13.0, 15.2))
        else:
            out.append(rng.gauss(10.0, 0.35))
    return out


def _advances(helper, frame_times):
    """Histogram of whole-pixel movement per presented frame."""
    counts, last, now = Counter(), int(helper.scroll_position), 1000.0
    with patch("src.common.scroll_helper.time.time") as clock:
        for gap_ms in frame_times:
            now += gap_ms / 1000.0
            clock.return_value = now
            helper.update_scroll_position()
            position = int(helper.scroll_position)
            counts[position - last] += 1
            last = position
    assert not helper.is_scroll_complete(), (
        "the strip ran out mid-measurement; a finished scroll freezes and its "
        "frozen frames read as zero-advance")
    return counts


class TestWholePixelStepping:
    def test_a_crisp_speed_moves_the_same_pixels_every_frame(self):
        helper = _helper()
        scroll_config.configure(helper, plugin_config=None,
                                default_pixels_per_second=100.0,
                                display_manager=_Panel(), refresh_hz=100.0)
        counts = _advances(helper, _measured_frame_times(3000))
        assert set(counts) == {1}, (
            "frames advanced %s px; at a crisp speed every frame must move the "
            "same whole number, or the motion beats against the frame rate"
            % sorted(counts))

    def test_the_wall_clock_is_not_consulted_at_all(self):
        """Jitter must not reach the position, however violent."""
        helper = _helper()
        scroll_config.configure(helper, plugin_config=None,
                                default_pixels_per_second=100.0,
                                display_manager=_Panel(), refresh_hz=100.0)
        savage = [1.0, 40.0, 2.0, 25.0, 0.5, 60.0] * 200
        assert set(_advances(helper, savage)) == {1}

    def test_two_pixels_per_frame_is_also_uniform(self):
        helper = _helper()
        scroll_config.configure(helper, plugin_config=None,
                                default_pixels_per_second=200.0,
                                display_manager=_Panel(), refresh_hz=100.0)
        assert helper.fixed_pixels_per_frame == 2
        assert set(_advances(helper, _measured_frame_times(2000))) == {2}

    def test_time_based_stepping_is_what_it_replaces(self):
        """Pin the defect, so a revert cannot pass this file quietly."""
        helper = _helper()
        helper.set_scroll_speed(100.0)          # the old path: px/s off the clock
        assert helper.fixed_pixels_per_frame is None
        counts = _advances(helper, _measured_frame_times(3000))
        uneven = sum(n for px, n in counts.items() if px != 1)
        assert uneven > 0, (
            "time-based stepping no longer produces uneven motion against real "
            "frame times; if that is genuinely fixed elsewhere, this test and "
            "the fixed-step mode both deserve re-examining")


class TestItStaysOptIn:
    def test_a_speed_that_is_not_crisp_keeps_pacing_off_time(self):
        helper = _helper()
        scroll_config.configure(helper, plugin_config=None,
                                default_pixels_per_second=100.0,
                                display_manager=_Panel(), refresh_hz=100.0,
                                snap_to_crisp=False)
        assert helper.fixed_pixels_per_frame is None

    def test_setting_a_speed_directly_drops_the_fixed_step(self):
        """Otherwise the new speed is silently ignored."""
        helper = _helper()
        scroll_config.configure(helper, plugin_config=None,
                                default_pixels_per_second=100.0,
                                display_manager=_Panel(), refresh_hz=100.0)
        assert helper.fixed_pixels_per_frame == 1
        helper.set_scroll_speed(37.0)
        assert helper.fixed_pixels_per_frame is None
        assert helper.scroll_speed == 37.0

    @pytest.mark.parametrize("value", [None, 0])
    def test_clearing_the_step_restores_time_based_motion(self, value):
        helper = _helper()
        helper.set_pixels_per_frame(3)
        helper.set_pixels_per_frame(value)
        assert helper.fixed_pixels_per_frame is None

    def test_an_older_core_without_the_setter_still_configures(self):
        """Plugins ship independently of the core they run against."""
        class Old:
            scroll_speed = None
            def set_scroll_speed(self, v): self.scroll_speed = v
            def set_frame_based_scrolling(self, v): pass
            def set_target_fps(self, v): pass

        old = Old()
        scroll_config.configure(old, plugin_config=None,
                                default_pixels_per_second=100.0,
                                display_manager=_Panel(), refresh_hz=100.0)
        assert old.scroll_speed == 100.0
