"""Visibility, alignment and scale reach the draw.

The resolver has understood these three since the framework landed, but nothing
on the scoreboard path consumed them: an element could be marked hidden in the
web UI and still render. This covers the readers, the mixin accessors the draw
paths use, and the one shared logo-sizing seam.

The rule throughout is that an untouched config takes exactly the path it took
before -- so each test that asserts an effect has a sibling asserting the
default is inert.
"""

import logging
import sys
from pathlib import Path

import pytest
from PIL import Image

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.common.logo_helper import LogoHelper  # noqa: E402
from src.common.sports_shared import SportsCoreSharedMixin  # noqa: E402
from src.element_style import (  # noqa: E402
    element_align, element_scale, element_visible)

HIDDEN = {"customization": {"records": {"visible": False}}}
ALIGNED = {"customization": {"score_text": {"align": "right"}}}
SCALED = {"customization": {"layout": {"home_logo": {"scale": 0.5}}}}


class TestTheReaders:
    def test_an_element_is_visible_unless_it_says_otherwise(self):
        assert element_visible({}, "records") is True
        assert element_visible(HIDDEN, "records") is False

    def test_a_mode_can_hide_what_the_base_shows(self):
        cfg = {"customization": {"records": {"visible": True},
                                 "modes": {"recent": {"records": {"visible": False}}}}}
        assert element_visible(cfg, "records") is True
        assert element_visible(cfg, "records", mode="recent") is False

    def test_a_mode_that_says_nothing_inherits(self):
        """None is the inherit sentinel and must not read as False."""
        cfg = {"customization": {"records": {"visible": False},
                                 "modes": {"live": {"records": {"visible": None}}}}}
        assert element_visible(cfg, "records", mode="live") is False

    def test_alignment_is_unset_by_default(self):
        assert element_align({}, "score_text") is None
        assert element_align(ALIGNED, "score_text") == "right"

    def test_scale_reads_the_layout_block(self):
        assert element_scale({}, "home_logo") == 1.0
        assert element_scale(SCALED, "home_logo") == 0.5

    def test_scale_takes_a_per_mode_override(self):
        cfg = {"customization": {"layout": {"home_logo": {"scale": 0.5}},
                                 "modes": {"live": {"layout": {"home_logo": {"scale": 2.0}}}}}}
        assert element_scale(cfg, "home_logo", mode="live") == 2.0
        assert element_scale(cfg, "home_logo") == 0.5

    @pytest.mark.parametrize("cfg", [
        None, {}, "nonsense", {"customization": "nonsense"},
        {"customization": {"records": "nonsense"}},
        {"customization": {"records": {"visible": "maybe"}}},
    ])
    def test_a_hostile_config_never_raises(self, cfg):
        assert element_visible(cfg, "records") is True
        assert element_align(cfg, "records") is None
        assert element_scale(cfg, "records") == 1.0


class _Draw:
    """Records what would have been drawn."""

    def __init__(self):
        self.calls = []
        self.fontmode = ""

    def text(self, position, text, font=None, fill=None):
        self.calls.append({"position": position, "text": text, "fill": fill})


class _Host(SportsCoreSharedMixin):
    def __init__(self, config, mode=None):
        self.config = config
        self.fonts = {}
        self.logger = logging.getLogger("test")
        self.SKIN_MODE = mode


class TestTheDrawPath:
    def test_a_hidden_element_is_not_drawn_at_all(self):
        draw = _Draw()
        _Host(HIDDEN)._draw_text_with_outline(
            draw, "9-1", (0, 0), None, element="records")
        assert draw.calls == [], (
            "a hidden element must produce no draw, outline included")

    def test_a_visible_element_still_draws(self):
        draw = _Draw()
        _Host({})._draw_text_with_outline(
            draw, "9-1", (0, 0), None, element="records")
        assert draw.calls, "the default must be inert"

    def test_naming_the_element_resolves_its_colour(self):
        draw = _Draw()
        cfg = {"customization": {"score_text": {"text_color": [1, 2, 3]}}}
        _Host(cfg)._draw_text_with_outline(
            draw, "21", (0, 0), None, element="score_text")
        # Last call is the text itself; the earlier eight are the outline.
        assert draw.calls[-1]["fill"] == (1, 2, 3)

    def test_an_explicit_fill_still_wins(self):
        """Odds colours and the favourite-result tint mean something the
        palette does not."""
        draw = _Draw()
        cfg = {"customization": {"score_text": {"text_color": [1, 2, 3]}}}
        _Host(cfg)._draw_text_with_outline(
            draw, "21", (0, 0), None, fill=(9, 9, 9), element="score_text")
        assert draw.calls[-1]["fill"] == (9, 9, 9)

    def test_a_mode_hides_only_that_mode(self):
        cfg = {"customization": {"modes": {"recent": {"records": {"visible": False}}}}}
        recent, live = _Draw(), _Draw()
        _Host(cfg, "recent")._draw_text_with_outline(
            recent, "9-1", (0, 0), None, element="records")
        _Host(cfg, "live")._draw_text_with_outline(
            live, "9-1", (0, 0), None, element="records")
        assert recent.calls == []
        assert live.calls


class TestAlignment:
    def test_unset_leaves_the_caller_where_it_was(self):
        """These draws carry per-sport nudges; a centre recomputed here would
        not be the same pixel."""
        assert _Host({})._aligned_x("score_text", 20, 64, 22) == 22

    def test_right_pushes_to_the_edge(self):
        assert _Host(ALIGNED)._aligned_x("score_text", 20, 64, 22) == 44

    def test_left_goes_to_zero(self):
        cfg = {"customization": {"score_text": {"align": "left"}}}
        assert _Host(cfg)._aligned_x("score_text", 20, 64, 22) == 0

    def test_text_wider_than_the_panel_is_not_pushed_off(self):
        assert _Host(ALIGNED)._aligned_x("score_text", 100, 64, 0) == 0


class TestLogoScale:
    @pytest.fixture
    def logo(self, tmp_path):
        path = tmp_path / "team.png"
        Image.new("RGBA", (40, 40), (255, 0, 0, 255)).save(path)
        return path

    def test_an_unscaled_load_is_unchanged(self, logo):
        helper = LogoHelper(display_width=64, display_height=32)
        plain = helper.load_logo("AAA", logo, 32, 32)
        assert plain.size == (32, 32)

    def test_a_half_scale_logo_is_half_the_box(self, logo):
        helper = LogoHelper(display_width=64, display_height=32)
        small = helper.load_logo("AAA", logo, 32, 32, scale=0.5)
        assert small.size == (16, 16)

    def test_scaling_up_grows_the_logo(self, logo):
        """The fit rule never grows an image, so this only happens when the
        user asks for it."""
        helper = LogoHelper(display_width=64, display_height=32)
        big = helper.load_logo("AAA", logo, 64, 64, scale=2.0)
        assert big.size[0] > 40

    def test_two_scales_do_not_share_a_cache_entry(self, logo):
        helper = LogoHelper(display_width=64, display_height=32)
        full = helper.load_logo("AAA", logo, 32, 32)
        half = helper.load_logo("AAA", logo, 32, 32, scale=0.5)
        assert full.size == (32, 32) and half.size == (16, 16)

    @pytest.mark.parametrize("bad", [0, -1, None, "big", float("inf"),
                                     float("nan")])
    def test_an_unusable_scale_is_ignored(self, logo, bad):
        helper = LogoHelper(display_width=64, display_height=32)
        assert helper.load_logo("AAA", logo, 32, 32, scale=bad).size == (32, 32)

    def test_a_scale_the_schema_allows_is_applied(self, logo):
        """The Scale field's maximum is honoured, not reset to 1.0."""
        from src.element_style import MAX_ELEMENT_SCALE
        helper = LogoHelper(display_width=64, display_height=32)
        big = helper.load_logo("AAA", logo, 4, 4, scale=MAX_ELEMENT_SCALE)
        assert big.size == (40, 40)

    def test_a_scale_beyond_the_range_is_clamped(self, logo):
        from src.element_style import MAX_ELEMENT_SCALE, MIN_ELEMENT_SCALE
        helper = LogoHelper(display_width=64, display_height=32)
        assert helper.load_logo("AAA", logo, 4, 4, scale=MAX_ELEMENT_SCALE * 3).size == (40, 40)
        assert helper.load_logo("AAA", logo, 40, 40, scale=MIN_ELEMENT_SCALE / 2).size == (4, 4)


class TestScaleCoercion:
    """One range for the schema, element_scale and LogoHelper."""

    def test_schema_bounds_are_the_clamp_bounds(self):
        from src.element_style import (MAX_ELEMENT_SCALE, MIN_ELEMENT_SCALE,
                                       _offset_block_from_spec)
        prop = _offset_block_from_spec("home_logo", {"scale": True})["properties"]["scale"]
        assert (prop["minimum"], prop["maximum"]) == (MIN_ELEMENT_SCALE, MAX_ELEMENT_SCALE)

    @pytest.mark.parametrize("raw,expected", [
        (0.5, 0.5), (25, 10.0), (0.01, 0.1),
        (0, 1.0), (-2, 1.0), ("x", 1.0), (True, 1.0),
        (float("nan"), 1.0), (float("inf"), 1.0),
    ])
    def test_element_scale_clamps_and_rejects(self, raw, expected):
        cfg = {"customization": {"layout": {"home_logo": {"scale": raw}}}}
        assert element_scale(cfg, "home_logo") == expected
