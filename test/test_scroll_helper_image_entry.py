"""ScrollHelper: what a new scrolling image does on the way in.

- set_scrolling_image cached a non-RGB image as-is, and every frame is cut
  with Image.frombytes('RGB', ...): an RGBA strip came out as garbage and an
  L strip raised.
- Only reset_scroll() reset last_update_time. In time-based mode the first
  update after a new image (e.g. the plugin coming back on screen) advanced
  the position by the whole idle gap, skipping the start of the content.
"""

import time

import pytest
from PIL import Image

from src.common.scroll_helper import ScrollHelper

W, H = 16, 8


@pytest.fixture
def helper():
    return ScrollHelper(display_width=W, display_height=H)


class TestNonRgbImages:
    def test_rgba_is_composited_onto_black(self, helper):
        img = Image.new('RGBA', (40, H), (0, 0, 0, 0))
        for x in range(10):
            for y in range(H):
                img.putpixel((x, y), (255, 0, 0, 255))
        # Hidden colour under full transparency must not leak through.
        img.putpixel((20, 0), (0, 255, 0, 0))
        helper.set_scrolling_image(img)
        assert helper.cached_image.mode == 'RGB'
        frame = helper.get_visible_portion()
        assert frame.size == (W, H)
        assert frame.getpixel((0, 0)) == (255, 0, 0)
        assert frame.getpixel((12, 0)) == (0, 0, 0)
        helper.scroll_position = 20.0
        assert helper.get_visible_portion().getpixel((0, 0)) == (0, 0, 0)

    def test_greyscale_does_not_raise(self, helper):
        helper.set_scrolling_image(Image.new('L', (40, H), 200))
        frame = helper.get_visible_portion()
        assert frame.getpixel((0, 0)) == (200, 200, 200)

    def test_palette_with_transparency(self, helper):
        img = Image.new('P', (40, H), 1)
        img.putpalette([0, 0, 0, 0, 0, 255] + [0] * 762)
        img.info['transparency'] = 1
        helper.set_scrolling_image(img)
        assert helper.get_visible_portion().getpixel((0, 0)) == (0, 0, 0)

    def test_an_rgb_image_is_kept_as_the_same_object(self, helper):
        img = Image.new('RGB', (40, H), (1, 2, 3))
        helper.set_scrolling_image(img)
        assert helper.cached_image is img


class TestNoJumpAfterIdle:
    def _idle_then_new_image(self, helper, install):
        helper.scroll_speed = 100.0  # px/s, time-based
        install(helper)
        helper.update_scroll_position()
        # Off screen for 10 seconds, then a fresh image.
        helper.last_update_time = time.time() - 10.0
        install(helper)
        helper.update_scroll_position()
        return helper.scroll_position

    def test_set_scrolling_image(self, helper):
        pos = self._idle_then_new_image(
            helper, lambda h: h.set_scrolling_image(Image.new('RGB', (5000, H))))
        assert pos < 50, f"jumped {pos}px on the first update of a new image"

    def test_create_scrolling_image(self, helper):
        pos = self._idle_then_new_image(
            helper, lambda h: h.create_scrolling_image([Image.new('RGB', (5000, H))]))
        assert pos < 50, f"jumped {pos}px on the first update of a new image"

    def test_fixed_step_mode_still_steps_exactly(self, helper):
        helper.set_pixels_per_frame(2)
        helper.set_scrolling_image(Image.new('RGB', (5000, H)))
        helper.update_scroll_position()
        helper.update_scroll_position()
        assert helper.scroll_position == 4.0
