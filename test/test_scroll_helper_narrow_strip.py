"""
ScrollHelper frames for a strip narrower than the panel, and other wraps.

A frame that runs past the end of the strip continues from its head: column
j of the frame is strip column (position + j) modulo the strip's width. The
wrap path sliced the strip's tail and then "the rest of the frame" from its
head, which assumed the head was at least that wide. For a strip narrower
than the panel it raised ValueError at every position, so a narrow strip
(Vegas composes one when its content is narrower than the chain, with its
lead-in of 0) logged a traceback every frame instead of drawing.
"""

import numpy as np
import pytest
from PIL import Image

from src.common.scroll_helper import ScrollHelper

W, H = 128, 32


def _strip(width, height=H):
    """A strip whose every column is distinct: R and B are the column number."""
    columns = np.arange(width)
    pixels = np.zeros((height, width, 3), dtype=np.uint8)
    pixels[:, :, 0] = columns % 256
    pixels[:, :, 1] = 255 - (columns % 256)
    pixels[:, :, 2] = columns // 256
    return Image.fromarray(pixels, 'RGB')


def _helper(strip_width, sub_pixel=False):
    sh = ScrollHelper(W, H)
    sh.set_scrolling_image(_strip(strip_width))
    sh.sub_pixel_scrolling = sub_pixel
    return sh


def _frame(sh, position):
    sh.scroll_position = position
    frame = sh.get_visible_portion()
    assert frame is not None and frame.size == (W, H) and frame.mode == 'RGB'
    return np.asarray(frame)


def _wrapped(sh, start):
    """What the panel should show from ``start``: the strip, wrapping."""
    return sh.cached_array[:, np.arange(start, start + W) % sh.cached_array.shape[1]]


class TestNarrowStrip:
    @pytest.mark.parametrize('strip_width', [1, 40, 50, W - 1])
    @pytest.mark.parametrize('position', [0, 10, 39])
    def test_frame_repeats_the_strip_across_the_panel(self, strip_width, position):
        sh = _helper(strip_width)
        position %= strip_width
        assert np.array_equal(_frame(sh, position), _wrapped(sh, position))

    def test_a_composed_strip_without_lead_in(self):
        # How Vegas builds its strip: lead_gap=0 (vegas_scroll.lead_in_width).
        sh = ScrollHelper(W, H)
        sh.create_scrolling_image([_strip(40)], item_gap=0, element_gap=0, lead_gap=0)
        assert sh.total_scroll_width == 40
        for position in range(40):
            assert np.array_equal(_frame(sh, position), _wrapped(sh, position))

    def test_a_whole_pass_scrolls_without_raising(self):
        sh = _helper(50)
        sh.set_pixels_per_frame(3)
        for _ in range(60):
            sh.update_scroll_position()
            assert sh.get_visible_portion().size == (W, H)

    @pytest.mark.parametrize('position', [0.5, 10.25, 49.5])
    def test_sub_pixel_blend_of_a_narrow_strip(self, position):
        sh = _helper(50, sub_pixel=True)
        frame = _frame(sh, position)
        start = int(position)
        near, far = _wrapped(sh, start), _wrapped(sh, start + 1)
        lo, hi = np.minimum(near, far), np.maximum(near, far)
        assert (frame >= lo).all() and (frame <= hi).all()


class TestWrapOfAWideStrip:
    """Unchanged: the tail, then the head."""

    @pytest.mark.parametrize('position', [200 - W + 1, 150, 199])
    def test_tail_then_head(self, position):
        sh = _helper(200)
        frame = _frame(sh, position)
        tail = 200 - position
        assert np.array_equal(frame[:, :tail], sh.cached_array[:, position:])
        assert np.array_equal(frame[:, tail:], sh.cached_array[:, :W - tail])

    def test_at_the_end_shows_the_head(self):
        sh = _helper(200)
        assert np.array_equal(_frame(sh, 200), sh.cached_array[:, :W])

    def test_sub_pixel_at_the_last_column(self):
        sh = _helper(200, sub_pixel=True)
        assert _frame(sh, 199.5).shape == (H, W, 3)

    def test_a_position_before_the_start_wraps_too(self):
        # Slicing [-10:118] of the array was an empty slice: frombytes raised.
        sh = _helper(200)
        assert np.array_equal(_frame(sh, -10), _wrapped(sh, -10))


def test_a_zero_width_strip_is_a_black_frame():
    sh = ScrollHelper(W, H)
    sh.set_scrolling_image(Image.new('RGB', (0, H)))
    assert not _frame(sh, 0).any()
