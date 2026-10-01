"""The offline live-element checks (src/plugin_system/testing/vegas.py).

Run against the stub fixture plugin, which honours the contract, and against
small broken plugins, each breaking one clause of it.
"""
import sys
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.plugin_system.base_plugin import BasePlugin  # noqa: E402
from src.plugin_system.testing.harness import _instantiate  # noqa: E402
from src.plugin_system.testing.loading import build_full_config, load_harness_spec, load_manifest  # noqa: E402
from src.plugin_system.testing.vegas import (  # noqa: E402
    check_vegas_elements, implements_vegas_elements, render_vegas_elements,
    render_vegas_strip, render_vegas_timeline,
)
from src.plugin_system.testing.visual_display_manager import VisualTestDisplayManager  # noqa: E402
from src.plugin_system.vegas_elements import VegasElement  # noqa: E402

STUB = Path(__file__).resolve().parent / "fixtures" / "plugins" / "vegas-live-stub"
W, H = 192, 48


def _stub(**config):
    dm = VisualTestDisplayManager(width=W, height=H)
    full = {**build_full_config(STUB, load_harness_spec(STUB), {}), **config}
    plugin = _instantiate("vegas-live-stub", load_manifest(STUB), STUB, full, {}, dm)
    return plugin, dm


class _Broken(BasePlugin):
    """A plugin whose get_vegas_elements returns whatever it is given."""

    def __init__(self, dm, result, redraw=None):
        self.plugin_id = "broken"
        self.config = {}
        self.display_manager = dm
        self.plugin_manager = None
        self._result = result
        self._redraw = redraw

    def update(self):
        pass

    def display(self, force_clear=False):
        pass

    def get_vegas_elements(self):
        return self._result() if callable(self._result) else self._result

    def redraw_vegas_element(self, key, width, height, at):
        return self._redraw(width, height) if self._redraw else None


def _img(w, h=H):
    return Image.new("RGB", (w, h), (255, 0, 0))


def test_the_stub_passes_every_check():
    plugin, dm = _stub()
    report = check_vegas_elements(plugin, dm)
    assert report.implemented and report.ok, report.errors
    assert report.elements == 8 and report.live == 7
    assert not report.warnings, report.warnings


def test_the_stub_renders_at_the_width_it_is_given():
    plugin, dm = _stub()
    elements = render_vegas_elements(plugin, dm, width=96)
    by_key = {e.key: e for e in elements}
    assert by_key["map"].image.size == (96, H)
    assert by_key["card:0"].image.width == 24
    assert plugin.get_vegas_render_width() == W   # restored afterwards


def test_the_stubs_cards_change_on_update_but_keep_their_width():
    plugin, dm = _stub()
    before = {e.key: e for e in render_vegas_elements(plugin, dm)}
    plugin.update()
    after = {e.key: e for e in render_vegas_elements(plugin, dm)}
    for key in ("card:0", "card:3"):
        assert after[key].version != before[key].version
        assert after[key].image.size == before[key].image.size
        assert after[key].image.tobytes() != before[key].image.tobytes()


def test_a_plugin_without_the_hook_is_not_checked():
    class Plain(BasePlugin):
        def update(self):
            pass

        def display(self, force_clear=False):
            pass

    plugin = Plain.__new__(Plain)
    dm = VisualTestDisplayManager(width=W, height=H)
    report = check_vegas_elements(plugin, dm)
    assert not implements_vegas_elements(plugin)
    assert not report.implemented and report.ok


def _errors(result, redraw=None):
    dm = VisualTestDisplayManager(width=W, height=H)
    return check_vegas_elements(_Broken(dm, result, redraw), dm)


def test_each_broken_clause_is_an_error():
    assert "expected a list" in _errors("x").errors[0]
    assert "not a VegasElement" in _errors([object()]).errors[0]
    assert "twice" in _errors([VegasElement("k", _img(10)),
                               VegasElement("k", _img(10))]).errors[0]
    assert "tall" in _errors([VegasElement("k", _img(10, H + 1))]).errors[0]
    assert "no key" in _errors([VegasElement("", _img(10))]).errors[0]


def test_a_width_that_changes_with_nothing_new_is_an_error():
    widths = iter([10, 12, 10, 10])
    report = _errors(lambda: [VegasElement("k", _img(next(widths)))])
    assert any("changed width" in e for e in report.errors)


def test_a_redraw_of_the_wrong_size_is_an_error():
    report = _errors([VegasElement("m", _img(40), refresh_hz=2)],
                     redraw=lambda w, h: _img(w + 1, h))
    assert any("asked for 40x48" in e for e in report.errors)


def test_animation_without_a_redraw_is_a_warning():
    dm = VisualTestDisplayManager(width=W, height=H)

    class NoRedraw(_Broken):
        redraw_vegas_element = BasePlugin.redraw_vegas_element

    report = check_vegas_elements(
        NoRedraw(dm, [VegasElement("m", _img(40), refresh_hz=2)]), dm)
    assert report.ok
    assert any("not implemented" in w for w in report.warnings)


def test_none_means_legacy_content_and_is_only_a_warning():
    report = _errors(None)
    assert report.ok and "get_vegas_content" in report.warnings[0]


def test_a_refresh_rate_that_is_not_a_number_is_an_error_not_a_crash():
    report = _errors([VegasElement("m", _img(40), refresh_hz="fast")])
    assert any("not a number" in e for e in report.errors)
    # None is what a plugin passing the dataclass default through gets.
    assert _errors([VegasElement("m", _img(40), refresh_hz=None)]).ok


def test_an_empty_image_is_an_error():
    assert any("empty" in e for e in _errors([VegasElement("k", _img(0))]).errors)


def test_a_second_call_that_breaks_the_contract_is_an_error_not_a_crash():
    answers = iter([[VegasElement("k", _img(10))], "x", "x", "x"])
    report = _errors(lambda: next(answers))
    assert any("second call" in e for e in report.errors)
    answers = iter([[VegasElement("k", _img(10))], [VegasElement("k", "not an image")],
                    [], []])
    report = _errors(lambda: next(answers))
    assert any("disappeared" in e for e in report.errors)


def test_check_plugin_reports_a_failing_element_check_and_carries_on(monkeypatch):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
    import check_plugin

    def boom(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(check_plugin, "check_plugin_vegas_elements", boom)
    results = check_plugin.check_one(
        "vegas-live-stub", [str(STUB.parent)], [(W, H)], {}, {}, False, None,
        False, None, None)
    vegas = [r for r in results if r.mode == "vegas elements"]
    assert len(vegas) == 1 and "boom" in vegas[0].error


def test_a_timeline_moves_what_animates_and_nothing_else():
    import numpy as np
    plugin, dm = _stub(dot_speed=200)
    _block, layout = render_vegas_strip(plugin, "vegas-live-stub", dm)
    image, rows = render_vegas_timeline(plugin, "vegas-live-stub", dm, steps=3,
                                        step_seconds=0.5)
    assert rows == 3 and image.height == 3 * H + 2
    pixels = np.asarray(image)
    first, last = pixels[:H], pixels[2 * (H + 1):]
    columns = {key: (x, width) for x, key, width in layout}
    x, width = columns["map"]
    assert (first[:, x:x + width] != last[:, x:x + width]).any()
    x, width = columns["card:0"]
    assert (first[:, x:x + width] == last[:, x:x + width]).all()


def test_a_timeline_with_updates_redraws_the_cards_in_place():
    import numpy as np
    plugin, dm = _stub(map_hz=0)
    _block, layout = render_vegas_strip(plugin, "vegas-live-stub", dm)
    image, rows = render_vegas_timeline(plugin, "vegas-live-stub", dm, steps=2,
                                        run_update=True)
    pixels = np.asarray(image)
    x, width = {key: (x, width) for x, key, width in layout}["card:0"]
    assert (pixels[:H, x:x + width] != pixels[H + 1:, x:x + width]).any()
