"""A group's blocks are laid out by the thread that fetched it, not at the extension.

Joining a plugin's rows (measuring the separation between them) and turning
the block into pixels cost the frame after every extension ~35 ms on a Pi 4,
against ~3.75 ms of slack. The prefetch thread and the live-element worker now
do it as each member arrives (RenderPipeline.prepare_group_member), and the
extension only writes those pixels into the strip (ScrollHelper.append_content
takes arrays). The strip a viewer sees must be exactly what it was.
"""
import random
import sys
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.common.scroll_helper import ScrollHelper  # noqa: E402
from src.vegas_mode import elements  # noqa: E402
from src.vegas_mode import render_pipeline  # noqa: E402
from src.vegas_mode.config import VegasModeConfig  # noqa: E402
from src.vegas_mode.elements import ElementMeta  # noqa: E402
from src.vegas_mode.render_pipeline import RenderPipeline  # noqa: E402

W, H = 96, 24


def _row(width, seed, live_key=None):
    rng = np.random.default_rng(seed)
    pixels = np.zeros((H, width, 3), dtype=np.uint8)
    # Margins of different sizes, so the measured separation matters.
    left, right = seed % 5, width - 1 - (seed % 7)
    pixels[2:H - 2, left:right] = rng.integers(20, 255, (H - 4, right - left, 3), dtype=np.uint8)
    image = Image.frombytes("RGB", (width, H), pixels.tobytes())
    if live_key is None:
        return image
    pinned, array = elements.pin_element(image, 8)
    return elements.tag(pinned, ElementMeta("p", live_key, 0, elements.pixel_digest(array), 0.0, 0.0))


def _groups(seed, n=8):
    rng = random.Random(seed)
    groups = []
    for g in range(n):
        group = []
        for p in range(rng.randint(1, 3)):
            rows = [_row(rng.randint(12, 60), rng.randint(0, 10**6),
                         f"g{g}p{p}r{r}" if rng.random() < 0.5 else None)
                    for r in range(rng.randint(1, 4))]
            group.append((f"pl{g}{p}", rows))
        groups.append(group)
    return groups


class _Stream:
    def __init__(self, groups, statics=()):
        self.groups = groups
        self.statics = set(statics)
        self.plugin_manager = type("PM", (), {"plugins": {}})()
        self.plugin_adapter = None
        self.i = 0

    def get_grouped_content_for_composition(self):
        return self.groups[0]

    def take_next_group(self, count=None, offscreen_only=False):
        self.i += 1
        return self.groups[self.i] if self.i < len(self.groups) else []

    def is_static_plugin(self, plugin_id):
        return plugin_id in self.statics


class _DM:
    width, height = W, H

    def __init__(self):
        self.image = Image.new("RGB", (W, H))

    def set_scrolling_state(self, *a):
        pass

    def update_display(self):
        pass


def _pipeline(groups, prepared, statics=()):
    p = RenderPipeline(VegasModeConfig(continuous_scroll=True, lead_in_width=0,
                                       separator_width=12), _DM(), _Stream(groups, statics))
    if prepared:
        # The prefetch thread, run to completion before each extension.
        def prefetch_now():
            p._prepared_group = p.stream_manager.take_next_group(offscreen_only=True)
            for member in p._prepared_group:
                p.prepare_group_member(member)
        p.start_prefetch = prefetch_now
    else:
        p.start_prefetch = lambda: None     # every extension fetches inline
    assert p.compose_scroll_content()
    return p


def _run(p, steps=6):
    for _ in range(steps):
        p.scroll_helper.scroll_position = max(
            p.scroll_helper.scroll_position, p.scroll_helper.total_scroll_width - 2 * W)
        if not p.extend_scroll_content():
            break
    return p


@pytest.mark.parametrize("seed", range(6))
def test_the_strip_is_the_same_prepared_ahead_or_joined_at_the_extension(seed):
    inline = _run(_pipeline(_groups(seed), prepared=False))
    ahead = _run(_pipeline(_groups(seed), prepared=True))
    assert np.array_equal(ahead.scroll_helper.cached_array, inline.scroll_helper.cached_array)
    assert ahead._strip_origin == inline._strip_origin
    assert [(r.key, r.abs_x, r.width) for r in ahead.live_records()] == \
        [(r.key, r.abs_x, r.width) for r in inline.live_records()]
    assert ahead.live_records()


def test_a_prepared_extension_joins_nothing_on_the_render_thread(monkeypatch):
    p = _pipeline(_groups(1), prepared=True)
    p.start_prefetch()                       # the group, prepared ahead
    p.start_prefetch = lambda: None          # and not the one after it

    def joined_here(*_a, **_k):
        raise AssertionError("the extension laid a block out itself")

    monkeypatch.setattr(render_pipeline, "join_plugin_rows", joined_here)
    monkeypatch.setattr(render_pipeline, "separation_gap", joined_here)
    monkeypatch.setattr(Image.Image, "paste", joined_here)
    assert p.extend_scroll_content()
    assert not p._prepared_blocks            # each block used once, then let go


def test_a_static_plugins_block_is_let_go_unused():
    groups = _groups(2)
    static = groups[1][0][0]
    p = _pipeline(groups, prepared=True, statics=[static])
    p.start_prefetch()
    p.start_prefetch = lambda: None
    assert p.extend_scroll_content()
    assert not p._prepared_blocks
    assert any(pid == static for _x, pid in p._static_markers)


def test_blocks_prepared_for_other_images_are_not_used():
    groups = _groups(3)
    p = _pipeline(groups, prepared=False)
    images = groups[1][0][1]
    p.prepare_group_member(("x", list(images)))          # equal, not the same list
    assert p._take_prepared_block(images) is None
    # Filed under these images' id() -- as when a list dies and its id is
    # reused -- but made from others.
    p._prepared_blocks[id(images)] = render_pipeline.PreparedBlock(
        [_row(20, 99)], p.config)
    assert p._take_prepared_block(images) is None


def test_a_reset_drops_prepared_blocks():
    p = _pipeline(_groups(4), prepared=True)
    p.start_prefetch()
    assert p._prepared_blocks
    p.reset()
    assert not p._prepared_blocks


def test_preparing_never_raises(caplog):
    p = _pipeline(_groups(5), prepared=False)
    p.prepare_group_member(("broken", [object()]))
    p.prepare_group_member(("empty", []))
    p.prepare_group_member(("deferred", None))
    assert not p._prepared_blocks


def test_the_waiting_blocks_are_bounded():
    p = _pipeline(_groups(6), prepared=False)
    for n in range(RenderPipeline.PREPARED_BLOCKS_MAX * 2):
        p.prepare_group_member((f"p{n}", [_row(20, n)]))
    assert len(p._prepared_blocks) <= RenderPipeline.PREPARED_BLOCKS_MAX


# -- ScrollHelper.append_content with arrays ----------------------------------------


def _strip():
    helper = ScrollHelper(W, H)
    helper.create_scrolling_image([_row(40, k) for k in range(4)], item_gap=3, lead_gap=0)
    return helper


@pytest.mark.parametrize("in_place", [False, True])
def test_appending_pixels_draws_exactly_what_appending_images_does(in_place):
    items = [_row(30, 7),
             _row(25, 8).convert("RGBA"),                       # converted, as paste does
             Image.new("RGB", (20, H - 6), (200, 40, 40)),      # short: black beneath
             Image.new("RGB", (15, H + 6), (40, 200, 40))]      # tall: cut at the strip
    as_images, as_pixels = _strip(), _strip()
    if in_place:
        for helper in (as_images, as_pixels):
            helper.append_content([_row(10, 1)], item_gap=2)    # a buffer with room
    as_images.append_content(items, item_gap=5, element_gap=2)
    as_pixels.append_content([np.asarray(i.convert("RGB")) for i in items],
                             item_gap=5, element_gap=2)
    assert np.array_equal(as_images.cached_array, as_pixels.cached_array)
    assert as_images.total_scroll_width == as_pixels.total_scroll_width


def test_reference_paste_layout_is_unchanged():
    """Gaps black, short items padded below, tall ones cut: what paste did."""
    items = [Image.new("RGB", (20, H - 6), (200, 40, 40)),
             Image.new("RGBA", (15, H + 6), (40, 200, 40, 9))]
    helper = _strip()
    before = helper.cached_array.copy()
    helper.append_content(items, item_gap=5, element_gap=2)
    addition = Image.new("RGB", (5 + 20 + 2 + 5 + 15 + 2, H))
    addition.paste(items[0], (5, 0))
    addition.paste(items[1], (5 + 20 + 2 + 5, 0))
    expected = np.concatenate((before, np.asarray(addition)), axis=1)
    assert np.array_equal(helper.cached_array, expected)


def test_a_first_build_from_pixels_matches_one_from_images():
    items = [_row(30, 3), _row(40, 4)]
    a, b = ScrollHelper(W, H), ScrollHelper(W, H)
    a.append_content(items, item_gap=4)
    b.append_content([np.asarray(i) for i in items], item_gap=4)
    assert np.array_equal(a.cached_array, b.cached_array)


def test_gaps_and_short_rows_are_black_whatever_the_spare_room_held():
    """Only uncovered columns are blanked, so they must all be."""
    items = [Image.new("RGB", (20, H - 6), (200, 40, 40)), _row(30, 5)]
    clean, dirty = _strip(), _strip()
    for helper in (clean, dirty):
        helper.append_content([_row(10, 1)], item_gap=2)        # a buffer with room
    end = dirty._strip_start + dirty.cached_array.shape[1]
    dirty._strip_buffer[:, end:] = 255                          # what an old strip left
    for helper in (clean, dirty):
        helper.append_content([np.asarray(i) for i in items], item_gap=5, element_gap=3)
    assert np.array_equal(dirty.cached_array, clean.cached_array)
    assert not dirty.cached_array[:, -3:].any()                 # the trailing element gap
