"""ScrollHelper builds the strip's PIL image only when something reads it.

Vegas extends and trims one long strip on the render thread. Each of those used
to rebuild ``cached_image`` from ``cached_array`` in full -- 1.7-3.8ms apiece on
a Pi 4 for a Vegas-sized strip, twice per extension -- though nothing on the
frame path reads the image's pixels. These tests pin that the frame path never
builds it, that a read still gets the right pixels, and that the two threads
which do read it (a multi-display sync push, the render thread) cannot leave a
stale image behind.
"""
import sys
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.common import scroll_helper as scroll_helper_module  # noqa: E402
from src.common.scroll_helper import ScrollHelper  # noqa: E402

W, H = 64, 16


def _block(width, seed):
    # frombytes, not fromarray: the no_fromarray fixture refuses the latter
    # everywhere, and a test block is not the strip.
    rng = np.random.default_rng(seed)
    pixels = rng.integers(0, 255, (H, width, 3), dtype=np.uint8)
    return Image.frombytes("RGB", (width, H), pixels.tobytes())


def _helper(width=400):
    helper = ScrollHelper(W, H)
    helper.set_scrolling_image(_block(width, 0))
    return helper


@pytest.fixture
def no_fromarray(monkeypatch):
    """Fail if the helper builds a PIL image from its array."""
    def refuse(*_a, **_k):
        raise AssertionError("the strip's PIL image was built")
    monkeypatch.setattr(scroll_helper_module.Image, "fromarray", refuse)


def test_append_and_trim_do_not_build_the_image(no_fromarray):
    helper = _helper()
    helper.scroll_position = 300.0
    assert helper.append_content([_block(200, 1)], item_gap=8)
    assert helper.drop_scrolled_prefix(keep_before=W) > 0
    assert helper.__dict__["_cached_image"] is None


def test_the_frame_path_never_builds_the_image(no_fromarray):
    helper = _helper()
    helper.set_pixels_per_frame(2)
    for i in range(400):
        if helper.remaining_unscrolled() <= 2 * W:
            helper.append_content([_block(150, i)], item_gap=8)
            helper.drop_scrolled_prefix(keep_before=W)
        helper.update_scroll_position()
        frame = helper.get_visible_portion()
        assert frame is not None and frame.size == (W, H)
        helper.get_scroll_info()
    assert not helper.is_scroll_complete()


def test_reading_the_image_gives_the_strip_as_it_is():
    helper = _helper()
    helper.scroll_position = 250.0
    helper.append_content([_block(120, 7)], item_gap=8)
    helper.drop_scrolled_prefix(keep_before=W)
    image = helper.cached_image
    assert image.size == (helper.cached_array.shape[1], H)
    assert np.array_equal(np.asarray(image), helper.cached_array)
    assert helper.total_scroll_width == image.width


def test_the_built_image_is_kept_until_the_strip_changes():
    helper = _helper()
    helper.append_content([_block(50, 1)], item_gap=0)
    first = helper.cached_image
    assert helper.cached_image is first
    helper.append_content([_block(50, 2)], item_gap=0)
    assert helper.cached_image is not first
    assert helper.cached_image.width == first.width + 50


def test_an_assigned_image_is_kept_exactly():
    # Plugins and the multi-display follower assign cached_image themselves.
    helper = _helper()
    helper.append_content([_block(50, 1)], item_gap=0)
    mine = _block(99, 3)
    helper.cached_image = mine
    assert helper.cached_image is mine
    helper.cached_image = None
    assert helper.cached_image is None


def test_frames_are_the_same_as_with_an_eager_image():
    lazy = _helper()
    eager = _helper()
    blocks = [_block(90, 10 + i) for i in range(6)]
    for i, block in enumerate(blocks):
        for helper in (lazy, eager):
            helper.scroll_position = 60.0 * (i + 1)
            helper.append_content([block], item_gap=8)
            helper.drop_scrolled_prefix(keep_before=W)
        eager.cached_image = Image.fromarray(eager.cached_array)   # the old way
        for x in range(0, lazy.cached_array.shape[1] - W, 7):
            lazy.scroll_position = eager.scroll_position = float(x)
            assert lazy.get_visible_portion().tobytes() == \
                eager.get_visible_portion().tobytes()


def test_a_read_racing_an_extension_does_not_keep_a_stale_image(monkeypatch):
    # The sync push reads the image on its own thread. If the render thread
    # extends the strip while that read is building the image, the read gets
    # the strip as it was, and the next read must not be handed it again.
    helper = _helper()
    helper.append_content([_block(40, 1)], item_gap=0)
    real = Image.fromarray

    def build_while_the_strip_changes(array, *a, **k):
        monkeypatch.setattr(scroll_helper_module.Image, "fromarray", real)
        helper.append_content([_block(40, 2)], item_gap=0)   # "render thread"
        return real(array, *a, **k)

    monkeypatch.setattr(scroll_helper_module.Image, "fromarray",
                        build_while_the_strip_changes)
    before_width = helper.cached_array.shape[1]
    raced = helper.cached_image
    assert raced.width == before_width
    fresh = helper.cached_image
    assert fresh.width == before_width + 40
    assert np.array_equal(np.asarray(fresh), helper.cached_array)


def test_clearing_the_cache_forgets_a_deferred_image():
    helper = _helper()
    helper.append_content([_block(40, 1)], item_gap=0)
    helper.clear_cache()
    assert helper.cached_image is None
    assert not helper.has_strip()
    assert helper.get_visible_portion() is None
    assert helper.remaining_unscrolled() == 0


def test_a_helper_with_no_strip_has_nothing_to_scroll():
    helper = ScrollHelper(W, H)
    assert not helper.has_strip()
    helper.update_scroll_position()
    assert helper.scroll_position == 0.0
    assert helper.get_scroll_info()["cached_image_size"] is None


def test_dropping_a_plugins_scroll_cache_does_not_build_it_first(no_fromarray):
    # Vegas clears a plugin's own scroll cache on the render thread whenever
    # the plugin updates; that must not build a deferred image to discard it.
    from types import SimpleNamespace

    from src.vegas_mode.plugin_adapter import PluginAdapter

    helper = _helper()
    helper.append_content([_block(40, 1)], item_gap=0)
    dm = SimpleNamespace(width=W, height=H, image=Image.new("RGB", (W, H)))
    adapter = PluginAdapter(dm)
    assert adapter.invalidate_plugin_scroll_cache(
        SimpleNamespace(scroll_helper=helper), "p")
    assert helper.cached_array is None and not helper.has_strip()


def test_vegas_extends_without_building_the_image(no_fromarray):
    from src.vegas_mode.config import VegasModeConfig
    from src.vegas_mode.render_pipeline import RenderPipeline

    groups = [[("a", [_block(300, 1)])]] + [[(f"p{i}", [_block(300, i + 2)])]
                                           for i in range(6)]

    class Stream:
        plugin_manager = type("PM", (), {"plugins": {}})()
        plugin_adapter = None
        i = 0

        def get_grouped_content_for_composition(self):
            return groups[0]

        def take_next_group(self, count=None, offscreen_only=False):
            self.i += 1
            return groups[self.i] if self.i < len(groups) else []

    class DM:
        width, height = W, H

        def __init__(self):
            self.image = Image.new("RGB", (W, H))

        def set_scrolling_state(self, *a):
            pass

        def update_display(self):
            pass

    p = RenderPipeline(VegasModeConfig(lead_in_width=0, continuous_scroll=True),
                       DM(), Stream())
    # compose pastes into a new image and converts that to the array once;
    # it never needs fromarray either.
    assert p.compose_scroll_content()
    for _ in range(5):
        p.scroll_helper.scroll_position += 250
        assert p.extend_scroll_content()
        assert p.render_frame()
    assert p.scroll_helper.__dict__["_cached_image"] is None
