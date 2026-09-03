"""Tests for the shared scroll configuration resolver."""

import logging
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.common.scroll_config import (  # noqa: E402
    DEFAULT_PIXELS_PER_SECOND,
    MAX_PIXELS_PER_SECOND,
    MIN_PIXELS_PER_SECOND,
    ScrollSettings,
    configure,
    refresh_hz_from_config,
    resolve,
)


class FakeHelper:
    """Records what configure() applied."""

    def __init__(self, with_optional=True):
        self.speed = None
        self.frame_based = None
        self.target_fps = None
        if not with_optional:
            del FakeHelper.set_frame_based_scrolling
            del FakeHelper.set_target_fps

    def set_scroll_speed(self, speed):
        self.speed = speed

    def set_frame_based_scrolling(self, enabled):
        self.frame_based = enabled

    def set_target_fps(self, fps):
        self.target_fps = fps


class MinimalHelper:
    """An older helper exposing only set_scroll_speed."""

    def __init__(self):
        self.speed = None

    def set_scroll_speed(self, speed):
        self.speed = speed


class TestPrecedence:
    def test_display_options_pair_wins(self):
        s = resolve({"display_options": {"scroll_speed": 1.0, "scroll_delay": 0.01}})
        assert s.pixels_per_second == 100.0
        assert s.source == "display_options.scroll_speed/delay"

    def test_display_block_used_when_options_absent(self):
        s = resolve({"display": {"scroll_speed": 2.0, "scroll_delay": 0.01}})
        assert s.pixels_per_second == 200.0
        assert s.source == "display.scroll_speed/delay"

    def test_root_pair_used_when_both_blocks_absent(self):
        s = resolve({"scroll_speed": 1.0, "scroll_delay": 0.02})
        assert s.pixels_per_second == 50.0

    def test_pixels_per_second_used_when_no_pair_given(self):
        s = resolve({"display_options": {"scroll_pixels_per_second": 120.0}})
        assert s.pixels_per_second == 120.0
        assert s.source == "display_options.scroll_pixels_per_second"

    def test_global_display_is_the_last_resort_before_default(self):
        s = resolve({}, {"display": {"scroll_speed": 1.0, "scroll_delay": 0.005}})
        assert s.pixels_per_second == 200.0

    def test_default_when_nothing_configured(self):
        s = resolve({}, {})
        assert s.pixels_per_second == DEFAULT_PIXELS_PER_SECOND
        assert s.source == "default"


class TestDeprecatedKeyCannotOverrideExplicitPair:
    """Regression for ChuckBuilds/ledmatrix-plugins#408.

    odds-ticker ranked scroll_pixels_per_second above the documented
    scroll_speed/scroll_delay pair. Because that key carries a schema default,
    the documented settings became unreachable for every user and the ticker
    silently ran at the default speed. The pair must win.
    """

    def test_pair_beats_pixels_per_second_in_the_same_block(self):
        s = resolve(
            {
                "display_options": {
                    "scroll_speed": 1.0,
                    "scroll_delay": 0.01,
                    "scroll_pixels_per_second": 50.0,  # schema default
                }
            }
        )
        assert s.pixels_per_second == 100.0
        assert "scroll_speed/delay" in s.source

    def test_pair_beats_pixels_per_second_in_an_outer_block(self):
        s = resolve(
            {
                "display_options": {"scroll_speed": 1.0, "scroll_delay": 0.01},
                "scroll_pixels_per_second": 50.0,
            }
        )
        assert s.pixels_per_second == 100.0


class TestMalformedValues:
    @pytest.mark.parametrize("bad", [None, "fast", "", {}, [], float("nan")])
    def test_unusable_speed_falls_through(self, bad):
        s = resolve({"display_options": {"scroll_speed": bad, "scroll_delay": 0.01}})
        assert s.pixels_per_second == DEFAULT_PIXELS_PER_SECOND

    @pytest.mark.parametrize("bad", [0, -5, 0.0])
    def test_non_positive_values_fall_through(self, bad):
        s = resolve({"display_options": {"scroll_pixels_per_second": bad}})
        assert s.pixels_per_second == DEFAULT_PIXELS_PER_SECOND

    def test_booleans_are_not_treated_as_numbers(self):
        s = resolve({"display_options": {"scroll_pixels_per_second": True}})
        assert s.pixels_per_second == DEFAULT_PIXELS_PER_SECOND

    def test_zero_delay_does_not_divide_by_zero(self):
        s = resolve({"display_options": {"scroll_speed": 1.0, "scroll_delay": 0}})
        assert s.pixels_per_second == DEFAULT_PIXELS_PER_SECOND

    def test_non_dict_blocks_are_ignored(self):
        s = resolve({"display_options": "nonsense", "display": 5})
        assert s.pixels_per_second == DEFAULT_PIXELS_PER_SECOND


class TestClamping:
    def test_absurdly_fast_is_clamped(self):
        s = resolve({"display_options": {"scroll_pixels_per_second": 100000.0}})
        assert s.pixels_per_second == MAX_PIXELS_PER_SECOND
        assert s.warning and "clamped" in s.warning

    def test_absurdly_slow_is_clamped(self):
        s = resolve({"display_options": {"scroll_pixels_per_second": 0.01}})
        assert s.pixels_per_second == MIN_PIXELS_PER_SECOND


class TestWholePixelWarning:
    """The display rule: whole pixels per refresh, or it judders."""

    def test_no_warning_when_speed_divides_evenly(self):
        s = resolve({"display_options": {"scroll_pixels_per_second": 100.0}}, refresh_hz=100)
        assert s.warning is None
        assert s.pixels_per_frame == pytest.approx(1.0)

    def test_no_warning_at_an_integer_multiple(self):
        s = resolve({"display_options": {"scroll_pixels_per_second": 200.0}}, refresh_hz=100)
        assert s.warning is None

    def test_warns_on_a_half_pixel_per_frame(self):
        s = resolve({"display_options": {"scroll_pixels_per_second": 50.0}}, refresh_hz=100)
        assert s.warning is not None
        assert "judder" in s.warning
        assert "100 px/s" in s.warning

    def test_warns_on_a_fractional_multiple(self):
        s = resolve({"display_options": {"scroll_pixels_per_second": 130.0}}, refresh_hz=100)
        assert s.warning is not None

    def test_respects_a_non_default_refresh_rate(self):
        s = resolve({"display_options": {"scroll_pixels_per_second": 150.0}}, refresh_hz=150)
        assert s.warning is None
        assert s.pixels_per_frame == pytest.approx(1.0)


class TestConfigure:
    def test_applies_time_based_mode_and_speed(self):
        helper = FakeHelper()
        s = configure(helper, {"display_options": {"scroll_speed": 1.0, "scroll_delay": 0.01}})
        assert helper.speed == 100.0
        assert helper.frame_based is False, "must not use the wall-clock step gate"
        assert helper.target_fps == 100.0
        assert s.pixels_per_second == 100.0

    def test_works_against_a_helper_without_optional_methods(self):
        helper = MinimalHelper()
        configure(helper, {"display_options": {"scroll_pixels_per_second": 100.0}})
        assert helper.speed == 100.0

    def test_logs_the_warning_when_speed_will_judder(self, caplog):
        helper = FakeHelper()
        with caplog.at_level(logging.WARNING):
            configure(helper, {"display_options": {"scroll_pixels_per_second": 50.0}})
        assert any("judder" in r.getMessage() for r in caplog.records)

    def test_returns_settings_describing_the_source(self):
        s = configure(FakeHelper(), {"display_options": {"scroll_speed": 2.0, "scroll_delay": 0.01}})
        assert isinstance(s, ScrollSettings)
        assert "200.0 px/s" in s.describe()


class TestRefreshFromConfig:
    def test_reads_the_hardware_limit(self):
        assert refresh_hz_from_config(
            {"display": {"hardware": {"limit_refresh_rate_hz": 150}}}
        ) == 150.0

    @pytest.mark.parametrize("cfg", [None, {}, {"display": {}}, {"display": {"hardware": {}}},
                                     "nonsense", {"display": {"hardware": {"limit_refresh_rate_hz": None}}}])
    def test_falls_back_to_the_default(self, cfg):
        assert refresh_hz_from_config(cfg) == 100.0
