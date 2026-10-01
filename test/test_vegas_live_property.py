"""Live patches change live elements' columns and nothing else, whatever happens.

Two pipelines are built from the same content and put through the same random
sequence of scrolling, extending and trimming. One of them also has random
patches applied. After every step, the only columns allowed to differ between
the two are those of live elements, and each of those must show exactly the
last patch applied to it (or its original pixels).
"""
import collections
import random
import sys
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.vegas_mode import elements  # noqa: E402
from src.vegas_mode.config import VegasModeConfig  # noqa: E402
from src.vegas_mode.elements import ElementMeta, LivePatch  # noqa: E402
from src.vegas_mode.render_pipeline import RenderPipeline  # noqa: E402

W, H = 96, 24


def _live(key, width, seed):
    rng = np.random.default_rng(seed)
    pixels = rng.integers(20, 255, (H, width, 3), dtype=np.uint8)
    image = Image.frombytes("RGB", (width, H), pixels.tobytes())
    pinned, array = elements.pin_element(image, 8)
    return elements.tag(pinned, ElementMeta("p", key, 0, elements.pixel_digest(array), 0.0, 0.0))


def _plain(width, seed):
    image = Image.new("RGB", (width, H))
    ImageDraw.Draw(image).rectangle([0, 0, width - 1, H - 1],
                                    outline=(seed * 37 % 255, 90, 200))
    return image


def _groups(rng, n):
    groups = []
    for g in range(n):
        group = []
        for p in range(rng.randint(1, 3)):
            rows = []
            for r in range(rng.randint(1, 3)):
                key = f"g{g}p{p}r{r}"
                if rng.random() < 0.6:
                    rows.append(_live(key, rng.randint(10, 50), rng.randint(0, 10**6)))
                else:
                    rows.append(_plain(rng.randint(10, 50), rng.randint(0, 255)))
            group.append((f"pl{g}{p}", rows))
        groups.append(group)
    return groups


class _Stream:
    def __init__(self, groups):
        self.groups = groups
        self.plugin_manager = type("PM", (), {"plugins": {}})()
        self.plugin_adapter = None
        self.i = 0

    def get_grouped_content_for_composition(self):
        return self.groups[0]

    def get_active_plugin_ids(self):
        return []

    def take_next_group(self, count=None, offscreen_only=False):
        self.i += 1
        return self.groups[self.i] if self.i < len(self.groups) else []


class _DM:
    width, height = W, H

    def __init__(self):
        self.image = Image.new("RGB", (W, H))

    def set_scrolling_state(self, *a):
        pass

    def update_display(self):
        pass


def _pipeline(groups):
    p = RenderPipeline(VegasModeConfig(continuous_scroll=True, lead_in_width=0,
                                       separator_width=12), _DM(), _Stream(groups))
    assert p.compose_scroll_content()
    return p


@pytest.mark.parametrize("seed", range(12))
def test_patches_touch_only_live_columns(seed):
    rng = random.Random(seed)
    groups = _groups(rng, 25)
    patched, reference = _pipeline(groups), _pipeline(groups)
    expected = {}          # seq -> pixels the patched strip must show
    counter = collections.Counter()

    for _step in range(60):
        action = rng.random()
        if action < 0.35:
            advance = rng.randint(5, 60)
            for p in (patched, reference):
                p.scroll_helper.scroll_position += advance
        elif action < 0.6:
            for p in (patched, reference):
                if p.scroll_helper.remaining_unscrolled() < 4 * W:
                    p.extend_scroll_content()
            counter["extend"] += 1
        else:
            records = patched.live_records()
            if not records:
                continue
            record = rng.choice(records)
            pixels = np.full((H, record.width, 3), rng.randint(0, 255), dtype=np.uint8)
            pixels.setflags(write=False)
            patched._live_slots[record.seq] = LivePatch(
                seq=record.seq, strip_gen=patched._strip_gen, epoch=0, pixels=pixels,
                digest=elements.pixel_digest(pixels), made_at=0.0)
            patched._live_ready.append(record.seq)
            if patched.apply_live_patches():
                expected[record.seq] = pixels
                counter["patch"] += 1

        # The twins agree on everything but live columns.
        a = patched.scroll_helper.cached_array
        b = reference.scroll_helper.cached_array
        assert a.shape == b.shape
        assert patched._strip_origin == reference._strip_origin
        live = np.zeros(a.shape[1], dtype=bool)
        for record in patched.live_records():
            x = record.abs_x - patched._strip_origin
            lo, hi = max(0, x), max(0, x + record.width)
            live[lo:hi] = True
            if record.seq in expected:
                assert np.array_equal(a[:, lo:hi], expected[record.seq][:, lo - x:hi - x])
        assert np.array_equal(a[:, ~live], b[:, ~live])

    assert counter["patch"] > 0
