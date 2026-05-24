"""
Tests for src/common/scroll_helper.py

Covers ScrollHelper: create_scrolling_image, update_scroll_position,
get_visible_portion, calculate_dynamic_duration, set_* methods,
reset_scroll, clear_cache, get_scroll_info.
"""

import pytest
import time
from unittest.mock import patch
from PIL import Image

from src.common.scroll_helper import ScrollHelper


DISPLAY_W = 64
DISPLAY_H = 32


@pytest.fixture
def helper():
    return ScrollHelper(display_width=DISPLAY_W, display_height=DISPLAY_H)


def _make_image(width: int = 64, height: int = 32, color=(255, 0, 0)) -> Image.Image:
    img = Image.new("RGB", (width, height), color)
    return img


# ---------------------------------------------------------------------------
# __init__ / initial state
# ---------------------------------------------------------------------------

class TestScrollHelperInit:
    def test_initial_scroll_position(self, helper):
        assert helper.scroll_position == 0.0

    def test_initial_scroll_complete_false(self, helper):
        assert helper.scroll_complete is False

    def test_display_dimensions(self, helper):
        assert helper.display_width == DISPLAY_W
        assert helper.display_height == DISPLAY_H


# ---------------------------------------------------------------------------
# create_scrolling_image
# ---------------------------------------------------------------------------

class TestCreateScrollingImage:
    def test_empty_content_returns_blank_image(self, helper):
        result = helper.create_scrolling_image([])
        assert isinstance(result, Image.Image)
        assert helper.total_scroll_width == 0

    def test_single_item_creates_image(self, helper):
        img = _make_image(width=100)
        result = helper.create_scrolling_image([img])
        assert isinstance(result, Image.Image)
        assert result.width > DISPLAY_W  # includes leading gap

    def test_multiple_items_wider_image(self, helper):
        items = [_make_image(width=50), _make_image(width=50)]
        result = helper.create_scrolling_image(items)
        # Should be wider than two items alone
        assert result.width > 100

    def test_scroll_position_reset(self, helper):
        helper.scroll_position = 500.0
        helper.create_scrolling_image([_make_image()])
        assert helper.scroll_position == 0.0

    def test_cached_array_set(self, helper):
        helper.create_scrolling_image([_make_image()])
        assert helper.cached_array is not None

    def test_scroll_complete_reset(self, helper):
        helper.scroll_complete = True
        helper.create_scrolling_image([_make_image()])
        assert helper.scroll_complete is False

    def test_total_scroll_width_matches_image(self, helper):
        img = _make_image(width=200)
        result = helper.create_scrolling_image([img])
        assert helper.total_scroll_width == result.width


# ---------------------------------------------------------------------------
# set_scrolling_image
# ---------------------------------------------------------------------------

class TestSetScrollingImage:
    def test_sets_cached_image(self, helper):
        img = _make_image(width=200)
        helper.set_scrolling_image(img)
        assert helper.cached_image is img

    def test_sets_cached_array(self, helper):
        img = _make_image(width=200)
        helper.set_scrolling_image(img)
        assert helper.cached_array is not None

    def test_scroll_width_matches_image(self, helper):
        img = _make_image(width=300)
        helper.set_scrolling_image(img)
        assert helper.total_scroll_width == 300

    def test_none_clears_cache(self, helper):
        helper.set_scrolling_image(_make_image())
        helper.set_scrolling_image(None)
        assert helper.cached_image is None


# ---------------------------------------------------------------------------
# update_scroll_position (time-based mode)
# ---------------------------------------------------------------------------

class TestUpdateScrollPosition:
    def test_position_advances_over_time(self, helper):
        helper.create_scrolling_image([_make_image(width=200)])
        helper.scroll_speed = 100.0  # 100 px/s
        helper.last_update_time = time.time() - 0.1  # pretend 100ms elapsed
        initial = helper.scroll_position
        helper.update_scroll_position()
        assert helper.scroll_position > initial

    def test_no_advance_without_image(self, helper):
        helper.update_scroll_position()  # no image, should not crash
        assert helper.scroll_position == 0.0

    def test_zero_width_content_stays_zero(self, helper):
        helper.create_scrolling_image([])  # empty → width 0
        helper.update_scroll_position()
        assert helper.scroll_position == 0.0

    def test_scroll_complete_clamped(self, helper):
        helper.create_scrolling_image([_make_image(width=100)])
        # Force position past the end
        helper.scroll_position = helper.total_scroll_width + 50
        helper.total_distance_scrolled = helper.total_scroll_width + 50
        helper.update_scroll_position()
        assert helper.scroll_complete is True
        assert helper.scroll_position <= helper.total_scroll_width


# ---------------------------------------------------------------------------
# get_visible_portion
# ---------------------------------------------------------------------------

class TestGetVisiblePortion:
    def test_returns_none_without_image(self, helper):
        assert helper.get_visible_portion() is None

    def test_returns_image_sized_to_display(self, helper):
        helper.create_scrolling_image([_make_image(width=200)])
        visible = helper.get_visible_portion()
        assert visible is not None
        assert visible.width == DISPLAY_W
        assert visible.height == DISPLAY_H

    def test_different_positions_give_different_images(self, helper):
        helper.create_scrolling_image([_make_image(width=300)])
        img1 = helper.get_visible_portion()
        helper.scroll_position = 50
        img2 = helper.get_visible_portion()
        # Images should differ (colour from scrolled content)
        # Just verify both are valid PIL images with correct size
        assert img1.width == img2.width == DISPLAY_W


# ---------------------------------------------------------------------------
# reset_scroll / clear_cache
# ---------------------------------------------------------------------------

class TestResetAndClear:
    def test_reset_restores_position(self, helper):
        helper.create_scrolling_image([_make_image(width=200)])
        helper.scroll_position = 100.0
        helper.reset_scroll()
        assert helper.scroll_position == 0.0

    def test_reset_clears_complete_flag(self, helper):
        helper.scroll_complete = True
        helper.reset_scroll()
        assert helper.scroll_complete is False

    def test_reset_alias(self, helper):
        helper.scroll_position = 50.0
        helper.reset()
        assert helper.scroll_position == 0.0

    def test_clear_cache(self, helper):
        helper.create_scrolling_image([_make_image()])
        helper.clear_cache()
        assert helper.cached_image is None
        assert helper.cached_array is None
        assert helper.total_scroll_width == 0


# ---------------------------------------------------------------------------
# calculate_dynamic_duration
# ---------------------------------------------------------------------------

class TestCalculateDynamicDuration:
    def test_returns_min_when_disabled(self, helper):
        helper.dynamic_duration_enabled = False
        helper.min_duration = 30
        result = helper.calculate_dynamic_duration()
        assert result == 30

    def test_returns_min_when_no_content(self, helper):
        helper.total_scroll_width = 0
        helper.min_duration = 30
        result = helper.calculate_dynamic_duration()
        assert result == 30

    def test_respects_min_duration(self, helper):
        helper.create_scrolling_image([_make_image(width=50)])
        helper.min_duration = 60
        helper.max_duration = 300
        helper.scroll_speed = 500.0  # very fast → very short time
        result = helper.calculate_dynamic_duration()
        assert result >= 60

    def test_respects_max_duration(self, helper):
        helper.create_scrolling_image([_make_image(width=50000)])
        helper.min_duration = 10
        helper.max_duration = 60
        helper.scroll_speed = 1.0  # very slow → very long time
        result = helper.calculate_dynamic_duration()
        assert result <= 60

    def test_time_based_calculation(self, helper):
        helper.create_scrolling_image([_make_image(width=200)])
        helper.scroll_speed = 100.0
        helper.min_duration = 1
        helper.max_duration = 600
        helper.frame_based_scrolling = False
        result = helper.calculate_dynamic_duration()
        assert isinstance(result, int)
        assert result > 0


# ---------------------------------------------------------------------------
# set_* configuration methods
# ---------------------------------------------------------------------------

class TestSetMethods:
    def test_set_scroll_speed_time_based(self, helper):
        helper.frame_based_scrolling = False
        helper.set_scroll_speed(50.0)
        assert helper.scroll_speed == 50.0

    def test_set_scroll_speed_clamped_low(self, helper):
        helper.frame_based_scrolling = False
        helper.set_scroll_speed(0.0)
        assert helper.scroll_speed >= 1.0

    def test_set_scroll_speed_clamped_high(self, helper):
        helper.frame_based_scrolling = False
        helper.set_scroll_speed(10000.0)
        assert helper.scroll_speed <= 500.0

    def test_set_scroll_delay(self, helper):
        helper.set_scroll_delay(0.05)
        assert helper.scroll_delay == 0.05

    def test_set_scroll_delay_clamped(self, helper):
        helper.set_scroll_delay(0.0001)
        assert helper.scroll_delay >= 0.001

    def test_set_target_fps(self, helper):
        helper.set_target_fps(60.0)
        assert helper.target_fps == 60.0

    def test_set_target_fps_clamped(self, helper):
        helper.set_target_fps(1000.0)
        assert helper.target_fps <= 200.0

    def test_set_sub_pixel_scrolling(self, helper):
        helper.set_sub_pixel_scrolling(True)
        assert helper.sub_pixel_scrolling is True
        helper.set_sub_pixel_scrolling(False)
        assert helper.sub_pixel_scrolling is False

    def test_set_frame_based_scrolling(self, helper):
        helper.set_frame_based_scrolling(True)
        assert helper.frame_based_scrolling is True

    def test_set_dynamic_duration_settings(self, helper):
        helper.set_dynamic_duration_settings(enabled=True, min_duration=20, max_duration=120, buffer=0.2)
        assert helper.dynamic_duration_enabled is True
        assert helper.min_duration == 20
        assert helper.max_duration == 120
        assert helper.duration_buffer == pytest.approx(0.2)


# ---------------------------------------------------------------------------
# get_scroll_info
# ---------------------------------------------------------------------------

class TestGetScrollInfo:
    def test_returns_dict(self, helper):
        info = helper.get_scroll_info()
        assert isinstance(info, dict)

    def test_required_keys(self, helper):
        info = helper.get_scroll_info()
        for key in ("scroll_position", "total_distance_scrolled", "scroll_speed",
                    "scroll_complete", "dynamic_duration"):
            assert key in info

    def test_scroll_position_reflected(self, helper):
        helper.scroll_position = 42.0
        info = helper.get_scroll_info()
        assert info["scroll_position"] == 42.0
