"""The render thread's half of a live update: RenderPipeline.apply_live_patches.

Between two frames the render thread copies prepared pixels into the strip.
It must do nothing else there -- no drawing, no locks, a bounded number of
bytes -- and must refuse a patch that no longer fits: a strip rebuilt since,
an element trimmed away or already behind the screen, a patch older than what
the strip already shows.
"""
import sys
import threading
import time
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.vegas_mode import elements  # noqa: E402
from src.vegas_mode.config import VegasModeConfig  # noqa: E402
from src.vegas_mode.elements import ElementMeta, LivePatch  # noqa: E402
from src.vegas_mode.render_pipeline import RenderPipeline  # noqa: E402

W, H = 128, 32


def _live(key, width, seed):
    rng = np.random.default_rng(seed)
    pixels = rng.integers(20, 255, (H, width, 3), dtype=np.uint8)
    image = Image.frombytes("RGB", (width, H), pixels.tobytes())
    pinned, array = elements.pin_element(image, 8)
    return elements.tag(pinned, ElementMeta("p", key, 1, elements.pixel_digest(array), 0.0, 0.0))


class _Stream:
    def __init__(self, groups):
        self.groups = groups
        self.plugin_manager = type("PM", (), {"plugins": {}})()
        self.plugin_adapter = None
        self.i = 0

    def get_grouped_content_for_composition(self):
        return self.groups[0]

    def get_active_plugin_ids(self):
        return ["p"]

    def take_next_group(self, count=None, offscreen_only=False):
        self.i += 1
        return self.groups[self.i] if self.i < len(self.groups) else []


class _Timing:
    def __init__(self):
        self.notes = []

    def note_op(self, kind, nbytes=0):
        self.notes.append((kind, nbytes))


class _DM:
    width, height = W, H

    def __init__(self):
        self.image = Image.new("RGB", (W, H))
        self.frame_timing = _Timing()

    def set_scrolling_state(self, *a):
        pass

    def update_display(self):
        pass


def _pipeline(n=6):
    groups = [[("p", [_live(f"k{i}", 40, i) for i in range(n)])]]
    p = RenderPipeline(VegasModeConfig(continuous_scroll=True, lead_in_width=0),
                       _DM(), _Stream(groups))
    assert p.compose_scroll_content()
    p.display_manager.frame_timing.notes.clear()    # the compose's own note
    return p


def _patch_for(p, record, value, epoch=None, gen=None):
    pixels = np.full((H, record.width, 3), value, dtype=np.uint8)
    pixels.setflags(write=False)
    return LivePatch(seq=record.seq, strip_gen=p._strip_gen if gen is None else gen,
                     epoch=record.epoch if epoch is None else epoch, pixels=pixels,
                     digest=elements.pixel_digest(pixels), made_at=0.0)


def _offer(p, patch):
    p._live_slots[patch.seq] = patch
    p._live_ready.append(patch.seq)


def _columns(p, record):
    x = record.abs_x - p._strip_origin
    return p.scroll_helper.cached_array[:, x:x + record.width]


def test_a_patch_is_copied_into_its_columns_and_noted():
    p = _pipeline()
    record = p.live_records()[2]
    _offer(p, _patch_for(p, record, 123))
    assert p.apply_live_patches() == 1
    assert (_columns(p, record) == 123).all()
    assert p._applied[record.seq][1] == elements.pixel_digest(_columns(p, record))
    assert p.display_manager.frame_timing.notes == [("patch", record.width * H * 3)]


def test_nothing_to_apply_costs_nothing():
    p = _pipeline()
    assert p.apply_live_patches() == 0
    assert p.display_manager.frame_timing.notes == []


def test_at_most_four_patches_a_frame_the_rest_next_frame():
    p = _pipeline()
    for record in p.live_records():
        _offer(p, _patch_for(p, record, 50))
    assert p.apply_live_patches() == 4
    assert p.apply_live_patches() == 2


def test_the_byte_budget_still_applies_one():
    p = _pipeline(n=1)
    record = p.live_records()[0]
    big = LivePatch(seq=record.seq, strip_gen=p._strip_gen, epoch=1,
                    pixels=np.full((H, record.width, 3), 9, dtype=np.uint8),
                    digest=((H, record.width, 3), 1), made_at=0.0)
    p.LIVE_PATCH_BUDGET_SCREENS = 0
    _offer(p, big)
    assert p.apply_live_patches() == 1


@pytest.mark.parametrize("why", ["old strip", "unknown element", "older data",
                                 "behind the screen"])
def test_a_patch_that_no_longer_fits_is_dropped(why):
    p = _pipeline()
    record = p.live_records()[0]
    before = _columns(p, record).copy()
    patch = _patch_for(p, record, 77)
    if why == "old strip":
        patch = _patch_for(p, record, 77, gen=p._strip_gen - 1)
    elif why == "unknown element":
        patch = patch._replace(seq=999)
    elif why == "older data":
        p._applied[record.seq] = (5, p._applied[record.seq][1])
        patch = _patch_for(p, record, 77, epoch=4)
    else:
        p.scroll_helper.scroll_position = record.abs_x + record.width + 1
    _offer(p, patch)
    assert p.apply_live_patches() == 0
    assert np.array_equal(_columns(p, record), before)


def test_the_latest_patch_wins_and_its_duplicate_entry_is_harmless():
    p = _pipeline()
    record = p.live_records()[1]
    _offer(p, _patch_for(p, record, 10))
    _offer(p, _patch_for(p, record, 20))       # replaces the slot, queues seq again
    assert p.apply_live_patches() == 1
    assert (_columns(p, record) == 20).all()
    assert not p._live_ready


def test_under_sync_nothing_is_applied_and_the_queue_is_emptied():
    p = _pipeline()
    record = p.live_records()[0]
    before = _columns(p, record).copy()
    p.sync_manager = object()
    _offer(p, _patch_for(p, record, 5))
    assert p.apply_live_patches() == 0
    assert not p._live_ready and not p._live_slots
    assert np.array_equal(_columns(p, record), before)


def test_the_render_thread_takes_no_lock_and_draws_nothing(monkeypatch):
    # Another thread holds every lock a live update could involve; the render
    # thread's apply must not care, and must not draw or rebuild the strip.
    p = _pipeline()
    locks = [p._prefetch_lock, threading.Lock()]
    for lock in locks:
        lock.acquire()
    for name in ("new", "fromarray"):
        monkeypatch.setattr(Image, name, lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("drew on the render thread")))
    monkeypatch.setattr(np, "concatenate", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("rebuilt the strip")))
    try:
        for record in p.live_records():
            _offer(p, _patch_for(p, record, 3))
        started = time.perf_counter()
        applied = p.apply_live_patches() + p.apply_live_patches()
        assert time.perf_counter() - started < 0.05
        assert applied == 6
    finally:
        for lock in locks:
            lock.release()


def test_the_view_is_published_each_frame():
    p = _pipeline()
    p.scroll_helper.set_pixels_per_frame(1)
    assert p.render_frame()
    view = p._view
    assert view.abs_right - view.abs_left == W
    assert view.abs_left == p._strip_origin + int(p.scroll_helper.scroll_position)
    assert view.abs_end == p._strip_origin + p.scroll_helper.total_scroll_width
