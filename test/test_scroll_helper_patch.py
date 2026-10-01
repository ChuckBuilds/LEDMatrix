"""ScrollHelper.patch_columns: changing a strip's pixels in place, between frames.

The primitive a live Vegas element update is built on. It writes exactly the
columns it is given and nothing else, never moves the scroll or resizes the
strip, and refuses an array it may not write (the multi-display follower's).
"""
import sys
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.common.scroll_helper import ScrollHelper  # noqa: E402

W, H = 64, 16


def _helper(width=300):
    rng = np.random.default_rng(1)
    pixels = rng.integers(0, 255, (H, width, 3), dtype=np.uint8)
    helper = ScrollHelper(W, H)
    helper.set_scrolling_image(Image.frombytes("RGB", (width, H), pixels.tobytes()))
    helper.scroll_position = 42.0
    return helper


def _patch(width, value=7):
    return np.full((H, width, 3), value, dtype=np.uint8)


def test_writes_exactly_its_columns():
    helper = _helper()
    before = helper.cached_array.copy()
    assert helper.patch_columns(100, _patch(30)) == H * 30 * 3
    after = helper.cached_array
    assert (after[:, 100:130] == 7).all()
    assert np.array_equal(after[:, :100], before[:, :100])
    assert np.array_equal(after[:, 130:], before[:, 130:])


def test_moves_nothing_else():
    helper = _helper()
    width, position, distance = (helper.total_scroll_width, helper.scroll_position,
                                 helper.total_distance_scrolled)
    helper.patch_columns(10, _patch(20))
    assert (helper.total_scroll_width, helper.scroll_position,
            helper.total_distance_scrolled) == (width, position, distance)


def test_clips_at_both_ends():
    helper = _helper(width=300)
    assert helper.patch_columns(-10, _patch(30, 1)) == H * 20 * 3
    assert (helper.cached_array[:, :20] == 1).all()
    assert helper.patch_columns(290, _patch(30, 2)) == H * 10 * 3
    assert (helper.cached_array[:, 290:] == 2).all()
    assert helper.patch_columns(400, _patch(10)) == 0
    assert helper.patch_columns(-50, _patch(10)) == 0


def test_the_patch_lands_in_the_next_frame():
    helper = _helper()
    helper.scroll_position = 100.0
    helper.patch_columns(110, _patch(10, 200))
    frame = np.asarray(helper.get_visible_portion())
    assert (frame[:, 10:20] == 200).all()


def test_refuses_a_read_only_strip():
    # The follower adopts np.asarray(image), which is read-only.
    helper = _helper()
    helper.cached_array = np.asarray(helper.cached_image)
    assert not helper.cached_array.flags.writeable
    assert helper.patch_columns(10, _patch(10)) == 0


def test_refuses_a_patch_of_the_wrong_height_and_no_strip():
    helper = _helper()
    assert helper.patch_columns(10, np.zeros((H + 1, 5, 3), dtype=np.uint8)) == 0
    assert ScrollHelper(W, H).patch_columns(0, _patch(5)) == 0


def test_a_deferred_image_is_built_from_the_patched_strip():
    helper = _helper()
    helper.append_content([Image.new("RGB", (40, H))], item_gap=0)
    helper.patch_columns(5, _patch(5, 99))
    assert (np.asarray(helper.cached_image)[:, 5:10] == 99).all()
