"""The adapter's side of live redraws: render_live_elements() and its memo.

The worker asks the adapter to redraw a plugin's elements whenever the plugin's
data moves on. What comes back is compared with what the strip shows, so the
adapter may skip converting an element it has seen before -- but only when it
is provably the same picture, and a bad element must cost only itself.
"""
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.plugin_system.vegas_elements import VegasElement  # noqa: E402
from src.vegas_mode.config import VegasModeConfig  # noqa: E402
from src.vegas_mode.elements import LiveEpochs  # noqa: E402
from src.vegas_mode.plugin_adapter import PluginAdapter  # noqa: E402

from test.test_vegas_elements_api import _DM, _Plugin, H  # noqa: E402


def _card(width=40, colour=(255, 0, 0)):
    image = Image.new("RGB", (width, H))
    ImageDraw.Draw(image).rectangle([0, 0, width - 1, H - 1], outline=colour)
    return image


def _adapter():
    lock = threading.Lock()
    pm = SimpleNamespace(get_plugin_lock=lambda pid: lock)
    adapter = PluginAdapter(_DM(), VegasModeConfig(), plugin_manager=pm)
    adapter.live_elements_enabled = True
    adapter.live_epochs = LiveEpochs()
    return adapter


def _render(adapter, plugin):
    epoch, rendered = adapter.render_live_elements(plugin, "p", lock_timeout=1.0)
    return rendered


def test_the_same_image_at_the_same_version_is_not_converted_again():
    card = _card()
    plugin = _Plugin(lambda: [VegasElement("c", card, version=1)])
    adapter = _adapter()
    first = _render(adapter, plugin)["c"]
    again = _render(adapter, plugin)["c"]
    assert again.pixels is first.pixels


def test_a_new_picture_at_the_same_version_is_converted():
    # A config change (new colours) redraws the card without moving its data
    # version; the strip must still get the new pixels.
    colours = iter([(255, 0, 0), (0, 255, 0)])
    plugin = _Plugin(lambda: [VegasElement("c", _card(colour=next(colours)), version=1)])
    adapter = _adapter()
    first = _render(adapter, plugin)["c"]
    again = _render(adapter, plugin)["c"]
    assert again.digest != first.digest


def test_the_same_image_at_a_new_version_is_converted():
    card = _card()
    versions = iter([1, 2])
    plugin = _Plugin(lambda: [VegasElement("c", card, version=next(versions))])
    adapter = _adapter()
    first = _render(adapter, plugin)["c"]
    again = _render(adapter, plugin)["c"]
    assert again.pixels is not first.pixels


def test_invalidating_every_plugin_forgets_what_was_converted():
    card = _card()
    plugin = _Plugin(lambda: [VegasElement("c", card, version=1)])
    adapter = _adapter()
    first = _render(adapter, plugin)["c"]
    adapter.invalidate_cache()
    assert _render(adapter, plugin)["c"].pixels is not first.pixels


def test_an_element_that_cannot_be_converted_costs_only_itself(monkeypatch):
    plugin = _Plugin(lambda: [VegasElement("bad", _card()), VegasElement("good", _card())])
    adapter = _adapter()
    real = adapter._element_image

    def element_image(element):
        if element.key == "bad":
            raise OSError("closed file")
        return real(element)

    monkeypatch.setattr(adapter, "_element_image", element_image)
    assert set(_render(adapter, plugin)) == {"good"}
    placed = adapter.get_content(plugin, "p", offscreen_only=True)
    from src.vegas_mode.elements import meta_of
    assert [meta_of(img).key for img in placed] == ["good"]


@pytest.mark.parametrize("size", [(0, H), (40, 0)])
def test_an_empty_image_is_dropped(size):
    plugin = _Plugin(lambda: [VegasElement("empty", Image.new("RGB", size)),
                              VegasElement("good", _card())])
    adapter = _adapter()
    assert set(_render(adapter, plugin)) == {"good"}


def test_a_bad_vegas_width_pct_is_reported_once(caplog):
    plugin = _Plugin(lambda: [VegasElement("c", _card())], config={"vegas_width_pct": "wide"})
    adapter = _adapter()
    with caplog.at_level("WARNING"):
        for _ in range(5):
            adapter.resolve_render_width(plugin, "p")
    assert sum("vegas_width_pct" in r.getMessage() for r in caplog.records) == 1
    plugin.config["vegas_width_pct"] = 500
    with caplog.at_level("WARNING"):
        adapter.resolve_render_width(plugin, "p")
    assert sum("out of range" in r.getMessage() for r in caplog.records) == 1
