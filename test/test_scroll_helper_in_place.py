"""ScrollHelper extends and trims a strip in place (src/common/scroll_helper.py).

Every extension of the Vegas strip used to rebuild it whole (np.concatenate)
and every trim copied what was left: 3.5-4.5 ms on the render thread at 512x64
on a Pi 4, so the frame after each extension was late. The strip now lives in
a buffer with spare room: an append writes only the new columns, a trim only
moves the view's start, and a full copy happens only when the buffer is
reallocated. What a frame shows must not change at all.
"""
import random
import sys
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.common.scroll_helper import ScrollHelper  # noqa: E402

W, H = 64, 16


def _items(rng, n):
    out = []
    for _ in range(n):
        width = rng.randint(5, 60)
        out.append(Image.frombytes("RGB", (width, H),
                                   bytes(rng.randrange(256) for _ in range(width * H * 3))))
    return out


def _helper():
    helper = ScrollHelper(W, H)
    helper.create_scrolling_image(_items(random.Random(1), 4), item_gap=3, lead_gap=0)
    return helper


def _reference_append(strip, items, gap):
    """What append_content used to do."""
    width = sum(i.width for i in items) + gap * len(items)
    addition = Image.new("RGB", (width, H))
    x = 0
    for item in items:
        x += gap
        addition.paste(item, (x, 0))
        x += item.width
    return np.concatenate((strip, np.array(addition)), axis=1)


def test_an_append_writes_into_the_buffer_and_copies_only_the_new_columns():
    helper = _helper()
    helper.append_content(_items(random.Random(2), 2), item_gap=3)    # allocates
    buffer = helper._strip_buffer
    before = helper.cached_array.shape[1]
    items = _items(random.Random(3), 2)
    helper.append_content(items, item_gap=3)
    assert helper._strip_buffer is buffer
    assert np.shares_memory(helper.cached_array, buffer)
    added = helper.cached_array.shape[1] - before
    assert helper.last_copy_bytes == added * H * 3


def test_a_trim_copies_nothing():
    helper = _helper()
    helper.append_content(_items(random.Random(2), 3), item_gap=3)
    helper.scroll_position = 120.0
    cut = helper.drop_scrolled_prefix()
    assert cut == 120 and helper.last_copy_bytes == 0
    assert np.shares_memory(helper.cached_array, helper._strip_buffer)


def test_the_buffer_is_reallocated_when_the_room_runs_out():
    helper = _helper()
    helper.append_content(_items(random.Random(2), 1), item_gap=3)
    first = helper._strip_buffer
    rng = random.Random(4)
    while helper._strip_buffer is first:
        helper.append_content(_items(rng, 3), item_gap=3)
    assert helper.last_copy_bytes == helper.cached_array.nbytes
    assert helper._strip_start == 0


def test_a_strip_set_from_outside_is_never_written_through():
    # The multi-display follower adopts a read-only array straight from an image.
    helper = _helper()
    helper.append_content(_items(random.Random(2), 1), item_gap=3)
    adopted = np.asarray(Image.new("RGB", (300, H), (9, 9, 9)))
    helper.cached_array = adopted
    helper.total_scroll_width = 300
    helper.append_content(_items(random.Random(3), 1), item_gap=3)
    assert not np.shares_memory(helper.cached_array, adopted)
    assert (adopted == 9).all()
    helper.cached_array = adopted
    helper.scroll_position = 100.0
    helper.drop_scrolled_prefix()
    assert not np.shares_memory(helper.cached_array, adopted)


def test_a_new_strip_lets_the_old_buffer_go():
    helper = _helper()
    helper.append_content(_items(random.Random(2), 1), item_gap=3)
    helper.create_scrolling_image(_items(random.Random(5), 2), item_gap=3, lead_gap=0)
    assert helper._strip_buffer is None
    helper.append_content(_items(random.Random(2), 1), item_gap=3)
    helper.clear_cache()
    assert helper._strip_buffer is None and helper._strip_view is None


@pytest.mark.parametrize("seed", range(12))
def test_every_frame_matches_the_old_copying_strip(seed):
    """Random appends, trims, scrolling and patches, against a strip kept the old way."""
    rng = random.Random(seed)
    helper = _helper()
    reference = helper.cached_array.copy()
    for _ in range(60):
        op = rng.random()
        if op < 0.35:
            items = _items(rng, rng.randint(1, 3))
            gap = rng.randint(0, 6)
            helper.append_content(items, item_gap=gap)
            reference = _reference_append(reference, items, gap)
        elif op < 0.55:
            keep = rng.randint(0, W)
            before = helper.scroll_position
            cut = helper.drop_scrolled_prefix(keep_before=keep)
            reference = reference[:, cut:].copy()
            assert helper.scroll_position == before - cut
        elif op < 0.7 and helper.cached_array.shape[1] > 8:
            x = rng.randrange(helper.cached_array.shape[1] - 4)
            pixels = np.full((H, 4, 3), rng.randrange(256), dtype=np.uint8)
            helper.patch_columns(x, pixels)
            reference[:, x:x + 4] = pixels
        else:
            limit = max(0, helper.cached_array.shape[1] - W - 1)
            helper.scroll_position = float(rng.randint(0, limit)) if limit else 0.0
        assert helper.cached_array.shape == reference.shape
        assert (helper.cached_array == reference).all()
        assert helper.total_scroll_width == reference.shape[1]
        frame = np.asarray(helper.get_visible_portion())
        x = int(helper.scroll_position)
        if x + W <= reference.shape[1]:
            assert (frame == reference[:, x:x + W]).all()
    # The lazily built image is the strip as it stands.
    assert (np.asarray(helper.cached_image) == reference).all()
