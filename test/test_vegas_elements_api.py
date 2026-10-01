"""Live Vegas elements: the plugin API and how the adapter fetches them.

A plugin opts in by implementing get_vegas_elements(); the adapter then asks
for elements instead of pictures, but only where that is safe (the background
fetch, under the plugin's lock, with live elements switched on) and falls back
to get_vegas_content() everywhere else. What makes an element live rides in
its image's ``info`` so it survives the adapter's cache and the pipeline's
join unchanged. These tests pin all of that.
"""
import dataclasses
import sys
import threading
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import numpy as np
import pytest
from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.plugin_system.base_plugin import BasePlugin  # noqa: E402
from src.plugin_system.vegas_elements import VegasElement  # noqa: E402
from src.vegas_mode import elements  # noqa: E402
from src.vegas_mode.config import VegasModeConfig  # noqa: E402
from src.vegas_mode.elements import ElementMeta, LiveEpochs  # noqa: E402
from src.vegas_mode.plugin_adapter import PluginAdapter  # noqa: E402

W, H = 128, 32


def _card(width=40, colour=(255, 0, 0), height=H):
    image = Image.new("RGB", (width, height), (0, 0, 0))
    ImageDraw.Draw(image).rectangle([0, 0, width - 1, height - 1], outline=colour)
    return image


class _DM:
    """A display manager with the per-thread canvas Vegas renders on."""

    def __init__(self):
        self.width, self.height = W, H
        self.image = Image.new("RGB", (W, H))
        self.offscreen_widths = []

    @contextmanager
    def offscreen(self, width=None, height=None):
        self.offscreen_widths.append(width)
        yield SimpleNamespace(image=Image.new("RGB", (width or W, H)))


class _Plugin(BasePlugin):
    """A BasePlugin with both the legacy and the element hooks."""

    def __init__(self, elements_result=None, config=None):
        self.plugin_id = "p"
        self.config = config or {}
        self.elements_result = elements_result
        self.element_calls = 0
        self.content_calls = 0
        self.render_widths = []
        self.plugin_manager = None

    def update(self):
        pass

    def display(self, force_clear=False):
        pass

    def get_vegas_content(self):
        self.content_calls += 1
        return [_card(40, (0, 0, 255))]

    def get_vegas_elements(self):
        self.element_calls += 1
        self.render_widths.append(self.get_vegas_render_width())
        result = self.elements_result
        if isinstance(result, Exception):
            raise result
        return result() if callable(result) else result


class _Legacy(_Plugin):
    get_vegas_elements = BasePlugin.get_vegas_elements


def _adapter(**cfg):
    lock = threading.Lock()
    pm = SimpleNamespace(get_plugin_lock=lambda pid: lock)
    adapter = PluginAdapter(_DM(), VegasModeConfig(**cfg), plugin_manager=pm)
    adapter.live_elements_enabled = True
    adapter.live_epochs = LiveEpochs()
    return adapter


def _elements():
    return [VegasElement("card:a", _card(40, (255, 0, 0)), version=1),
            VegasElement("sep", _card(10, (90, 90, 90)), live=False),
            VegasElement("card:b", _card(40, (0, 255, 0)), version=2)]


# -- the type and the tag -----------------------------------------------------


def test_element_defaults():
    element = VegasElement("k", _card())
    assert element.live and element.refresh_hz == 0 and element.version is None
    with pytest.raises(dataclasses.FrozenInstanceError):
        element.key = "other"


def test_a_tag_survives_what_the_plumbing_does_to_an_image():
    meta = ElementMeta("p", "k", 3, ((H, 10, 3), 1), 0.0, 0.0)
    image = elements.tag(_card(), meta)
    for derived in (image.copy(), image.crop((0, 0, 10, H)), image.convert("RGB"),
                    image.resize((20, H))):
        assert elements.meta_of(derived) == meta
    assert elements.meta_of(elements.untag(image.copy())) is None
    assert elements.meta_of(_card()) is None
    assert elements.meta_of(object()) is None


def test_pinning_pads_with_black_and_freezes_the_pixels():
    image, array = elements.pin_element(_card(20), 8)
    assert image.size == (36, H)
    assert array.shape == (H, 36, 3)
    assert not array.flags.writeable
    assert not array[:, :8].any() and not array[:, -8:].any()
    assert np.array_equal(array[:, 8:28], np.asarray(_card(20)))


def test_the_digest_sees_a_one_pixel_change():
    _, a = elements.pin_element(_card(20), 0)
    b = np.array(a)
    b[5, 5, 0] ^= 1
    assert elements.pixel_digest(a) != elements.pixel_digest(b)
    assert elements.pixel_digest(a) == elements.pixel_digest(np.array(a))


def test_epochs_move_on_per_plugin_and_never_repeat():
    epochs = LiveEpochs()
    assert epochs.get("a") == 0
    first = epochs.bump("a")
    second = epochs.bump("b")
    assert second > first and epochs.get("a") == first
    assert epochs.bump("a") > second


# -- BasePlugin ---------------------------------------------------------------


def test_the_base_hooks_do_nothing():
    plugin = _Legacy()
    assert plugin.get_vegas_elements() is None
    assert plugin.redraw_vegas_element("k", 10, H, 0.0) is None


def test_notify_vegas_data_changed_reaches_the_plugin_manager():
    plugin = _Legacy()
    plugin.plugin_manager = MagicMock()
    plugin.notify_vegas_data_changed()
    plugin.plugin_manager.notify_data_changed.assert_called_once_with("p")
    plugin.plugin_manager = None
    plugin.notify_vegas_data_changed()          # no manager: nothing to do


# -- the adapter's keyed path --------------------------------------------------


def test_the_background_fetch_asks_for_elements():
    adapter = _adapter()
    adapter.live_epochs.bump("p")
    plugin = _Plugin(_elements)
    images = adapter.get_content(plugin, "p", offscreen_only=True)
    assert plugin.element_calls == 1 and plugin.content_calls == 0
    metas = [elements.meta_of(img) for img in images]
    assert [m.key if m else None for m in metas] == ["card:a", None, "card:b"]
    assert metas[0].epoch == adapter.live_epochs.get("p")
    assert metas[0].version == 1


def test_live_elements_are_pinned_not_trimmed():
    adapter = _adapter(content_padding=8)
    plugin = _Plugin(_elements)
    images = adapter.get_content(plugin, "p", offscreen_only=True)
    live = [img for img in images if elements.meta_of(img)]
    assert all(img.width == 40 + 16 for img in live)
    # The plain separator is trimmed as always: drawn to its edges, it keeps
    # its width (trimming never widens an image).
    separator = images[1]
    assert elements.meta_of(separator) is None
    assert separator.width == 10


def test_an_elements_digest_matches_its_pixels():
    adapter = _adapter()
    images = adapter.get_content(_Plugin(_elements), "p", offscreen_only=True)
    meta = elements.meta_of(images[0])
    assert meta.digest == elements.pixel_digest(np.asarray(images[0]))


def test_elements_render_on_a_canvas_of_their_own_at_the_render_width():
    adapter = _adapter(render_width_pct=50)
    plugin = _Plugin(_elements)
    adapter.get_content(plugin, "p", offscreen_only=True)
    assert plugin.render_widths == [W // 2]
    assert adapter.display_manager.offscreen_widths == [W // 2]
    assert plugin.get_vegas_render_width() == W   # restored afterwards


@pytest.mark.parametrize("why", ["disabled", "render thread", "restricted",
                                 "plugin opted out", "legacy plugin"])
def test_everywhere_else_the_legacy_content_is_used(why):
    cfg = {"offscreen_prefetch": False} if why == "restricted" else {}
    adapter = _adapter(**cfg)
    plugin_cls = _Legacy if why == "legacy plugin" else _Plugin
    plugin = plugin_cls(_elements, config={"vegas_live": False}
                        if why == "plugin opted out" else None)
    if why == "disabled":
        adapter.live_elements_enabled = False
    images = adapter.get_content(plugin, "p", offscreen_only=(why != "render thread"))
    assert plugin.element_calls == 0
    if why != "restricted":
        assert plugin.content_calls == 1
        assert all(elements.meta_of(img) is None for img in images)


def test_a_mock_plugin_is_never_asked_for_elements():
    adapter = _adapter()
    plugin = MagicMock()
    plugin.config = {}
    plugin.get_vegas_content.return_value = [_card()]
    adapter.get_content(plugin, "p", offscreen_only=True)
    plugin.get_vegas_elements.assert_not_called()


@pytest.mark.parametrize("result", [None, RuntimeError("boom"), "nonsense",
                                    [], [object()]])
def test_a_broken_or_empty_answer_falls_back_to_legacy_content(result):
    adapter = _adapter()
    plugin = _Plugin(result)
    images = adapter.get_content(plugin, "p", offscreen_only=True)
    assert plugin.content_calls == 1
    assert images and all(elements.meta_of(img) is None for img in images)


def test_duplicate_keys_keep_the_first():
    adapter = _adapter()
    plugin = _Plugin(lambda: [VegasElement("k", _card(40)),
                              VegasElement("k", _card(30))])
    images = adapter.get_content(plugin, "p", offscreen_only=True)
    assert len(images) == 1 and images[0].width == 40 + 16


def test_elements_are_brought_to_the_display_height_and_rgb():
    adapter = _adapter()
    plugin = _Plugin(lambda: [VegasElement("k", _card(40, height=H * 2).convert("RGBA"))])
    image = adapter.get_content(plugin, "p", offscreen_only=True)[0]
    assert image.mode == "RGB" and image.height == H
    assert elements.meta_of(image) is not None


def test_a_keyed_fetch_ignores_legacy_content_in_the_cache():
    # The first compose runs on the render thread and caches legacy content;
    # the first background fetch after it must still ask for elements.
    adapter = _adapter()
    plugin = _Plugin(_elements)
    adapter.get_content(plugin, "p", offscreen_only=False)
    assert plugin.content_calls == 1
    images = adapter.get_content(plugin, "p", offscreen_only=True)
    assert plugin.element_calls == 1
    assert elements.meta_of(images[0]) is not None


def test_keyed_content_is_cached_with_its_tags():
    adapter = _adapter()
    plugin = _Plugin(_elements)
    adapter.get_content(plugin, "p", offscreen_only=True)
    again = adapter.get_content(plugin, "p", offscreen_only=True)
    assert plugin.element_calls == 1
    assert elements.meta_of(again[0]).key == "card:a"


def test_an_element_cropped_to_a_width_budget_is_no_longer_live():
    adapter = _adapter(max_plugin_width_ratio=0.5)
    wide = Image.new("RGB", (400, H), (255, 255, 255))
    plugin = _Plugin(lambda: [VegasElement("map", wide)])
    image = adapter.get_content(plugin, "p", offscreen_only=True)[0]
    budget = adapter._width_budget(plugin, "p")
    assert budget // 2 <= image.width < 400
    # A real window of the element, not a cut in the middle of its padding.
    assert np.asarray(image).any()
    assert elements.meta_of(image) is None


def test_an_element_whose_drawing_fits_the_budget_stays_live():
    adapter = _adapter(max_plugin_width_ratio=0.5)
    budget = adapter._width_budget(None, "p")
    # Over the budget only by the black padding pinned on either side.
    plugin = _Plugin(lambda: [VegasElement("card", _card(budget - 4))])
    image = adapter.get_content(plugin, "p", offscreen_only=True)[0]
    assert image.width > budget
    assert elements.meta_of(image).key == "card"


def test_a_padded_solid_image_over_the_budget_is_cropped_not_slivered():
    # Legacy content has the same margins after trimming, and the same cut
    # used to land mid-margin.
    adapter = _adapter(max_plugin_width_ratio=0.5)
    budget = adapter._width_budget(None, "p")
    solid = Image.new("RGB", (budget * 3, H), (255, 255, 255))
    cropped = adapter._apply_width_budget(
        [_padded(solid, adapter.config.content_padding)], "p", None)
    assert budget // 2 <= cropped[0].width
    assert np.asarray(cropped[0]).any()


def _padded(image, pad):
    out = Image.new("RGB", (image.width + 2 * pad, image.height))
    out.paste(image, (pad, 0))
    return out


def test_rows_under_a_width_budget_keep_their_tags():
    adapter = _adapter(max_plugin_width_ratio=1.0)
    plugin = _Plugin(lambda: [VegasElement(f"c{i}", _card(40)) for i in range(6)])
    images = adapter.get_content(plugin, "p", offscreen_only=True)
    assert 0 < len(images) < 6
    assert all(elements.meta_of(img) is not None for img in images)
