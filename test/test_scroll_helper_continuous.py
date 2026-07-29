"""
Tests for ScrollHelper's continuous-strip primitives.

append_content extends the strip to the right without disturbing motion, and
drop_scrolled_prefix reclaims what has already gone past. Together they let a
caller keep one endless strip instead of swapping a new one in, which is what
shows as a flash and a hard cut to already-full-screen content.
"""

import numpy as np
import pytest
from PIL import Image

from src.common.scroll_helper import ScrollHelper
from src.vegas_mode.geometry import column_has_ink

W, H = 128, 32


def helper():
    return ScrollHelper(W, H)


def block(width, colour=(255, 255, 255), height=H):
    return Image.new('RGB', (width, height), colour)


class TestAppendContent:
    def test_first_append_builds_the_strip(self):
        sh = helper()
        assert sh.append_content([block(100)], item_gap=0)
        assert sh.cached_image is not None
        assert sh.total_scroll_width == sh.cached_image.width

    def test_strip_grows_by_content_plus_gaps(self):
        sh = helper()
        sh.create_scrolling_image([block(100)], item_gap=0, element_gap=0, lead_gap=0)
        assert sh.cached_image.width == 100

        sh.append_content([block(50)], item_gap=10, element_gap=0)
        # one leading gap of 10 then the 50px block
        assert sh.cached_image.width == 160
        assert sh.total_scroll_width == 160

    def test_scroll_position_is_preserved(self):
        sh = helper()
        sh.create_scrolling_image([block(400)], item_gap=0, element_gap=0, lead_gap=0)
        sh.scroll_position = 137.0
        sh.total_distance_scrolled = 137.0

        sh.append_content([block(200)], item_gap=16)
        assert sh.scroll_position == 137.0
        assert sh.total_distance_scrolled == 137.0

    def test_appending_defers_completion(self):
        sh = helper()
        sh.create_scrolling_image([block(200)], item_gap=0, element_gap=0, lead_gap=0)
        sh.scroll_complete = True

        sh.append_content([block(200)], item_gap=0)
        assert not sh.scroll_complete
        assert sh.total_distance_scrolled < sh.total_scroll_width

    def test_existing_pixels_are_untouched(self):
        sh = helper()
        original = block(80, (10, 200, 10))
        sh.create_scrolling_image([original], item_gap=0, element_gap=0, lead_gap=0)
        before = sh.cached_image.crop((0, 0, 80, H)).tobytes()

        sh.append_content([block(40, (200, 10, 10))], item_gap=8)
        assert sh.cached_image.crop((0, 0, 80, H)).tobytes() == before

    def test_appended_content_sits_after_the_gap(self):
        sh = helper()
        sh.create_scrolling_image([block(50)], item_gap=0, element_gap=0, lead_gap=0)
        sh.append_content([block(30)], item_gap=12)

        ink = column_has_ink(sh.cached_image)
        assert ink[:50].all()
        assert not ink[50:62].any()      # the 12px gap
        assert ink[62:92].all()

    def test_array_and_image_stay_consistent(self):
        # get_visible_portion slices cached_array but bounds-checks against
        # cached_image.width, so a mismatch corrupts frames.
        sh = helper()
        sh.create_scrolling_image([block(200)], item_gap=0, element_gap=0, lead_gap=0)
        sh.append_content([block(100)], item_gap=8)
        assert sh.cached_array.shape[1] == sh.cached_image.width
        assert sh.cached_array.shape[0] == sh.cached_image.height

    def test_visible_portion_still_renders_after_append(self):
        sh = helper()
        sh.create_scrolling_image([block(300)], item_gap=0, element_gap=0, lead_gap=0)
        sh.append_content([block(300)], item_gap=8)
        sh.scroll_position = 250.0
        frame = sh.get_visible_portion()
        assert frame is not None and frame.size == (W, H)

    def test_empty_append_is_a_no_op(self):
        sh = helper()
        sh.create_scrolling_image([block(100)], item_gap=0, element_gap=0, lead_gap=0)
        assert sh.append_content([]) is False
        assert sh.cached_image.width == 100

    def test_repeated_appends_accumulate(self):
        sh = helper()
        sh.append_content([block(100)], item_gap=0)
        for _ in range(5):
            sh.append_content([block(100)], item_gap=0)
        assert sh.cached_image.width == 600


class TestDropScrolledPrefix:
    def test_removes_consumed_columns(self):
        sh = helper()
        sh.create_scrolling_image([block(1000)], item_gap=0, element_gap=0, lead_gap=0)
        sh.scroll_position = 500.0
        sh.total_distance_scrolled = 500.0

        removed = sh.drop_scrolled_prefix(keep_before=0)
        assert removed == 500
        assert sh.cached_image.width == 500
        assert sh.scroll_position == 0.0

    def test_keeps_the_requested_margin(self):
        sh = helper()
        sh.create_scrolling_image([block(1000)], item_gap=0, element_gap=0, lead_gap=0)
        sh.scroll_position = 500.0
        sh.drop_scrolled_prefix(keep_before=100)
        assert sh.scroll_position == 100.0
        assert sh.cached_image.width == 600

    def test_completion_difference_is_preserved(self):
        # total_distance_scrolled and total_scroll_width must shift together, or
        # trimming would spuriously complete or un-complete the cycle.
        sh = helper()
        sh.create_scrolling_image([block(1000)], item_gap=0, element_gap=0, lead_gap=0)
        sh.scroll_position = 600.0
        sh.total_distance_scrolled = 600.0
        before = sh.total_scroll_width - sh.total_distance_scrolled

        sh.drop_scrolled_prefix(keep_before=0)
        assert sh.total_scroll_width - sh.total_distance_scrolled == before

    def test_never_trims_below_the_viewport(self):
        sh = helper()
        sh.create_scrolling_image([block(200)], item_gap=0, element_gap=0, lead_gap=0)
        sh.scroll_position = 190.0
        sh.drop_scrolled_prefix(keep_before=0)
        assert sh.cached_image.width >= W

    def test_no_op_before_anything_has_scrolled(self):
        sh = helper()
        sh.create_scrolling_image([block(500)], item_gap=0, element_gap=0, lead_gap=0)
        assert sh.drop_scrolled_prefix(keep_before=0) == 0
        assert sh.cached_image.width == 500

    def test_no_op_with_no_strip(self):
        assert helper().drop_scrolled_prefix() == 0

    def test_visible_frame_is_unchanged_by_trimming(self):
        # The whole point: trimming is invisible. Same pixels on screen before
        # and after. Position chosen so the viewport is well clear of the end,
        # i.e. not wrapping.
        sh = helper()
        items = [block(200, (255, 0, 0)), block(200, (0, 255, 0)),
                 block(200, (0, 0, 255))]
        sh.create_scrolling_image(items, item_gap=20, element_gap=0, lead_gap=0)
        sh.scroll_position = 300.0
        before = sh.get_visible_portion().tobytes()

        assert sh.drop_scrolled_prefix(keep_before=0) > 0, "trim should have run"
        after = sh.get_visible_portion().tobytes()
        assert after == before

    def test_refuses_to_trim_while_the_viewport_wraps(self):
        # Wrapping reads the head of the strip into the right of the frame, so
        # trimming the head there would visibly change the picture.
        sh = helper()
        sh.create_scrolling_image([block(200)], item_gap=0, element_gap=0, lead_gap=0)
        sh.scroll_position = 150.0          # 150 + 128 > 200, so wrapping
        before = sh.get_visible_portion().tobytes()
        assert sh.drop_scrolled_prefix(keep_before=0) == 0
        assert sh.get_visible_portion().tobytes() == before

    def test_array_and_image_stay_consistent_after_trim(self):
        sh = helper()
        sh.create_scrolling_image([block(900)], item_gap=0, element_gap=0, lead_gap=0)
        sh.scroll_position = 400.0
        sh.drop_scrolled_prefix(keep_before=0)
        assert sh.cached_array.shape[1] == sh.cached_image.width


class TestRemainingUnscrolled:
    def test_counts_content_right_of_the_viewport(self):
        sh = helper()
        sh.create_scrolling_image([block(500)], item_gap=0, element_gap=0, lead_gap=0)
        assert sh.remaining_unscrolled() == 500 - W

    def test_shrinks_as_the_strip_scrolls(self):
        sh = helper()
        sh.create_scrolling_image([block(500)], item_gap=0, element_gap=0, lead_gap=0)
        sh.scroll_position = 200.0
        assert sh.remaining_unscrolled() == 500 - 200 - W

    def test_never_negative(self):
        sh = helper()
        sh.create_scrolling_image([block(200)], item_gap=0, element_gap=0, lead_gap=0)
        sh.scroll_position = 500.0
        assert sh.remaining_unscrolled() == 0

    def test_zero_with_no_strip(self):
        assert helper().remaining_unscrolled() == 0

    def test_grows_when_content_is_appended(self):
        sh = helper()
        sh.create_scrolling_image([block(600)], item_gap=0, element_gap=0, lead_gap=0)
        sh.scroll_position = 100.0
        before = sh.remaining_unscrolled()
        assert before > 0, "fixture should leave content ahead of the viewport"
        sh.append_content([block(400)], item_gap=0)
        assert sh.remaining_unscrolled() == before + 400


class TestContinuousScrollingEndToEnd:
    def test_strip_can_be_extended_indefinitely_at_bounded_size(self):
        """The invariant that makes this viable: extend + trim keeps the strip
        bounded while motion never stops."""
        sh = helper()
        sh.create_scrolling_image([block(600)], item_gap=0, element_gap=0, lead_gap=0)

        widths = []
        for _ in range(20):
            sh.scroll_position += 200
            sh.total_distance_scrolled += 200
            if sh.remaining_unscrolled() < 2 * W:
                sh.append_content([block(600)], item_gap=16)
            sh.drop_scrolled_prefix(keep_before=W)
            widths.append(sh.cached_image.width)
            # A frame must always be renderable.
            assert sh.get_visible_portion() is not None

        assert max(widths) < 3000, f"strip grew unbounded: max {max(widths)}"
        assert not sh.scroll_complete, "continuous strip should never complete"
