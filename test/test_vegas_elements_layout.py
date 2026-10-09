"""Where live elements land in the Vegas strip, and that the records stay true.

The pipeline records each live element's place (ElementRecord) as it builds
the strip, in absolute columns that a trim never moves: the strip column is
``abs_x - _strip_origin``. Everything a live update will do depends on these
being exact, so the check here is the strongest one available -- after any
sequence of compose, extend and trim, the strip's pixels at every record are
the element's own pixels.
"""
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.vegas_mode import elements  # noqa: E402
from src.vegas_mode.config import VegasModeConfig  # noqa: E402
from src.vegas_mode.elements import ElementMeta  # noqa: E402
from src.vegas_mode.render_pipeline import RenderPipeline  # noqa: E402

W, H = 128, 32


def _live(pid, key, width, seed):
    """A tagged, pinned element as the adapter hands it over."""
    rng = np.random.default_rng(seed)
    pixels = rng.integers(20, 255, (H, width, 3), dtype=np.uint8)
    image = Image.frombytes("RGB", (width, H), pixels.tobytes())
    pinned, array = elements.pin_element(image, 8)
    return elements.tag(pinned, ElementMeta(
        pid, key, 1, elements.pixel_digest(array), 0.0, 0.0))


def _plain(width, colour=(200, 200, 200)):
    image = Image.new("RGB", (width, H), (0, 0, 0))
    ImageDraw.Draw(image).rectangle([0, 0, width - 1, H - 1], outline=colour)
    return image


class _Stream:
    def __init__(self, groups):
        self.groups = groups
        self.plugin_manager = type("PM", (), {"plugins": {}})()
        self.plugin_adapter = None
        self.i = 0

    def get_grouped_content_for_composition(self):
        return self.groups[0]

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


def _pipeline(groups, **cfg):
    cfg.setdefault("continuous_scroll", True)
    return RenderPipeline(VegasModeConfig(**cfg), _DM(), _Stream(groups))


def _assert_records_match(p, images_by_key):
    strip = p.scroll_helper.cached_array
    for record in p.live_records():
        x = record.abs_x - p._strip_origin
        expected = np.asarray(images_by_key[record.key])
        assert expected.shape[1] == record.width
        lo = max(0, x)
        got = strip[:, lo:x + record.width]
        assert np.array_equal(got, expected[:, lo - x:]), record.key


def _group_images(groups):
    found = {}
    for group in groups:
        for _pid, images in group:
            for image in images:
                meta = elements.meta_of(image)
                if meta:
                    found[meta.key] = image
    return found


def test_a_compose_records_every_live_element_exactly():
    groups = [[("a", [_live("a", "a1", 40, 1), _plain(20), _live("a", "a2", 50, 2)]),
               ("b", [_plain(60)]),
               ("c", [_live("c", "c1", 70, 3)])]]
    p = _pipeline(groups, lead_in_width=10)
    assert p.compose_scroll_content()
    keys = [r.key for r in p.live_records()]
    assert keys == ["a1", "a2", "c1"]
    assert all(r.width == 16 + {"a1": 40, "a2": 50, "c1": 70}[r.key]
               for r in p.live_records())
    _assert_records_match(p, _group_images(groups))


def test_extensions_and_trims_keep_every_record_true():
    # A strip long enough that the viewport never runs off its end (trimming
    # is refused while it wraps), advanced by less than each extension adds.
    groups = [[("a", [_live("a", "a1", 40, 1), _plain(500)])]]
    for n in range(12):
        groups.append([(f"p{n}", [_live(f"p{n}", f"k{n}", 30 + n, 10 + n),
                                  _plain(25)]),
                       (f"q{n}", [_plain(45)])])
    p = _pipeline(groups, lead_in_width=0)
    assert p.compose_scroll_content()
    images = _group_images(groups)
    cuts = 0
    for _ in range(11):
        width_before = p.scroll_helper.cached_array.shape[1]
        p.scroll_helper.scroll_position += 150
        assert p.scroll_helper.scroll_position + W <= width_before
        position = p.scroll_helper.scroll_position
        assert p.extend_scroll_content()
        cut = int(position - p.scroll_helper.scroll_position)
        assert 0 <= cut <= width_before
        cuts += cut
        assert p._strip_origin == cuts
        _assert_records_match(p, images)
    # Records wholly trimmed away are forgotten, and only those.
    for record in p.live_records():
        assert record.abs_x + record.width > p._strip_origin
    assert set(p._record_by_seq) == {r.seq for r in p.live_records()}
    assert cuts > 0


def test_the_first_extension_of_an_empty_strip_starts_at_zero():
    groups = [[], [("a", [_live("a", "a1", 40, 1)]), ("b", [_live("b", "b1", 20, 2)])]]
    p = _pipeline(groups)
    assert p.extend_scroll_content()
    first = p.live_records()[0]
    assert first.abs_x == 0 and p._strip_origin == 0
    _assert_records_match(p, _group_images(groups))


def test_plain_content_is_not_recorded():
    p = _pipeline([[("a", [_plain(80)])], [("b", [_plain(90)])]])
    assert p.compose_scroll_content()
    assert p.extend_scroll_content()
    assert p.live_records() == ()


def test_a_new_strip_starts_a_new_generation_with_no_records():
    groups = [[("a", [_live("a", "a1", 40, 1)])]]
    p = _pipeline(groups)
    assert p.compose_scroll_content()
    gen = p._strip_gen
    assert p.live_records()
    assert p.compose_scroll_content()
    assert p._strip_gen == gen + 1
    assert [r.key for r in p.live_records()] == ["a1"]
    p.reset()
    assert p._strip_gen == gen + 2
    assert p.live_records() == () and p._strip_origin == 0


def test_record_numbers_are_never_reused():
    groups = [[("a", [_live("a", "a1", 40, 1)])]]
    p = _pipeline(groups)
    seen = set()
    for _ in range(3):
        assert p.compose_scroll_content()
        for record in p.live_records():
            assert record.seq not in seen
            seen.add(record.seq)


def test_static_markers_still_land_where_they_did():
    # The marker arithmetic now shares _block_starts with the records.
    class Stream(_Stream):
        def is_static_plugin(self, pid):
            return pid == "pause"

    groups = [[("a", [_plain(100)])],
              [("b", [_plain(60)]), ("pause", []), ("c", [_plain(70)])]]
    p = RenderPipeline(VegasModeConfig(continuous_scroll=True, lead_in_width=0,
                                       separator_width=32),
                       _DM(), Stream(groups))
    assert p.compose_scroll_content()
    strip_end = p.scroll_helper.total_scroll_width
    assert p.extend_scroll_content()
    # b starts after one separator and ends 60 later; the pause follows b.
    assert p._static_markers == ((strip_end + 32 + 60, "pause"),)
