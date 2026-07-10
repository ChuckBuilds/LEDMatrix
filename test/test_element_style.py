"""Tests for src/element_style.py — universal per-element style resolution.

The load-bearing behavior is the user-override check: saved configs ALWAYS
contain the schema defaults (merge_with_defaults runs at save time and again
before plugin instantiation), so "key present" must never be read as "user
set it". Only "present and different from the schema default" counts.
"""

import json
import os
import sys

import pytest
from PIL import ImageFont

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.element_style import (  # noqa: E402
    ElementStyle,
    ElementStyleResolver,
    FONT_ALIASES,
    defaults_from_schema_file,
    extract_schema_defaults,
    load_font,
    resolve_font_name,
)

PRESS_START = "PressStart2P-Regular.ttf"
FOUR_BY_SIX = "4x6-font.ttf"
FIVE_BY_SEVEN_BDF = "5x7.bdf"


# ---------------------------------------------------------------------------
# load_font
# ---------------------------------------------------------------------------

class TestLoadFont:
    def test_ttf(self):
        font = load_font(PRESS_START, 8)
        assert isinstance(font, ImageFont.FreeTypeFont)
        assert font.size == 8

    def test_bdf_at_native_size(self):
        """FreeType loads BDF strikes directly at their native size."""
        font = load_font(FIVE_BY_SEVEN_BDF, 7)
        assert isinstance(font, ImageFont.FreeTypeFont)

    def test_bdf_at_wrong_size_falls_back(self):
        """BDF fonts are fixed-size; a non-native size falls back to the
        fallback font at the requested size rather than raising."""
        font = load_font(FIVE_BY_SEVEN_BDF, 14)
        assert isinstance(font, ImageFont.FreeTypeFont)
        assert font.size == 14  # fallback font honored the requested size

    def test_alias_resolves(self):
        assert resolve_font_name("press_start") == PRESS_START
        font = load_font("press_start", 16)
        assert isinstance(font, ImageFont.FreeTypeFont)
        assert font.size == 16

    def test_filename_passes_through_alias(self):
        assert resolve_font_name(FOUR_BY_SIX) == FOUR_BY_SIX

    def test_missing_file_falls_back(self):
        font = load_font("no-such-font.ttf", 10)
        assert isinstance(font, ImageFont.FreeTypeFont)
        assert font.size == 10

    @pytest.mark.parametrize("garbage", ["", None, "../../etc/passwd", "x" * 300])
    def test_garbage_never_raises(self, garbage):
        font = load_font(garbage, 8)
        assert font is not None

    def test_everything_missing_uses_pil_default(self):
        font = load_font("nope.ttf", 8, fonts_dir="/nonexistent",
                         fallback_font="also-nope.ttf")
        assert font is not None  # ImageFont.load_default()

    def test_aliases_cover_the_baseball_set(self):
        """The centralized map must be a superset of the per-plugin copies
        it replaces (baseball game_renderer.py + sports.py)."""
        assert FONT_ALIASES["press_start"] == PRESS_START
        assert FONT_ALIASES["four_by_six"] == FOUR_BY_SIX
        assert FONT_ALIASES["five_by_seven"] == FIVE_BY_SEVEN_BDF


# ---------------------------------------------------------------------------
# user_forced provenance
# ---------------------------------------------------------------------------

SCHEMA_DEFAULTS = {
    "customization": {
        "score_text": {"font": PRESS_START, "font_size": 10},
    }
}


def _style(config, defaults=SCHEMA_DEFAULTS):
    return ElementStyleResolver(config, defaults).style(
        "score_text", classic_font=PRESS_START, classic_size=10)


class TestUserForced:
    def test_absent_is_not_forced(self):
        style = _style({})
        assert not style.user_forced
        assert style.font_name == PRESS_START
        assert style.font_size == 10

    def test_schema_default_present_is_not_forced(self):
        """THE bug this module exists to fix: the save flow writes schema
        defaults into every saved config, so their presence means nothing."""
        config = {"customization": {"score_text": {
            "font": PRESS_START, "font_size": 10}}}
        style = _style(config)
        assert not style.user_forced

    def test_different_font_is_forced(self):
        config = {"customization": {"score_text": {
            "font": FOUR_BY_SIX, "font_size": 10}}}
        style = _style(config)
        assert style.user_forced_font
        assert not style.user_forced_size
        assert style.user_forced

    def test_different_size_is_forced(self):
        config = {"customization": {"score_text": {
            "font": PRESS_START, "font_size": 14}}}
        style = _style(config)
        assert style.user_forced_size
        assert not style.user_forced_font
        assert style.font_size == 14

    def test_string_size_equal_to_default_is_not_forced(self):
        config = {"customization": {"score_text": {"font_size": "10"}}}
        assert not _style(config).user_forced

    def test_without_schema_defaults_compares_against_classic(self):
        """Degraded mode (old cores, tests): classic_* is the reference."""
        config = {"customization": {"score_text": {
            "font": PRESS_START, "font_size": 10}}}
        style = _style(config, defaults={})
        assert not style.user_forced
        forced = _style({"customization": {"score_text": {"font_size": 12}}},
                        defaults={})
        assert forced.user_forced

    def test_schema_default_differing_from_classic_wins_as_reference(self):
        """When the schema declares a different default than the classic_*
        args, the schema is the reference — a config equal to the schema
        default is untouched."""
        defaults = {"customization": {"score_text": {
            "font": FOUR_BY_SIX, "font_size": 6}}}
        config = {"customization": {"score_text": {
            "font": FOUR_BY_SIX, "font_size": 6}}}
        style = ElementStyleResolver(config, defaults).style(
            "score_text", classic_font=PRESS_START, classic_size=10)
        assert not style.user_forced

    def test_unknown_element_uses_classic(self):
        style = ElementStyleResolver({}, SCHEMA_DEFAULTS).style(
            "no_such_element", classic_font=FOUR_BY_SIX, classic_size=6)
        assert not style.user_forced
        assert style.font_name == FOUR_BY_SIX
        assert style.font_size == 6

    def test_malformed_customization_is_tolerated(self):
        for bad in [{"customization": "oops"},
                    {"customization": {"score_text": "oops"}},
                    {"customization": {"score_text": {"font_size": "huge"}}},
                    None]:
            style = _style(bad)
            assert not style.user_forced
            assert style.font_size == 10


# ---------------------------------------------------------------------------
# color
# ---------------------------------------------------------------------------

class TestColor:
    def test_absent_returns_classic_color(self):
        style = ElementStyleResolver({}).style(
            "score_text", classic_font=PRESS_START, classic_size=10,
            classic_color=(255, 215, 0))
        assert style.color == (255, 215, 0)

    def test_absent_with_no_classic_is_none(self):
        assert _style({}).color is None

    def test_list_becomes_tuple(self):
        config = {"customization": {"score_text": {"text_color": [0, 128, 255]}}}
        assert _style(config).color == (0, 128, 255)

    def test_values_clamped(self):
        config = {"customization": {"score_text": {"text_color": [300, -5, 128]}}}
        assert _style(config).color == (255, 0, 128)

    @pytest.mark.parametrize("bad", [[1, 2], [1, 2, 3, 4], "red",
                                     ["a", "b", "c"], 255, None])
    def test_malformed_falls_back_to_classic(self, bad):
        config = {"customization": {"score_text": {"text_color": bad}}}
        style = ElementStyleResolver(config).style(
            "score_text", classic_font=PRESS_START, classic_size=10,
            classic_color=(1, 2, 3))
        assert style.color == (1, 2, 3)


# ---------------------------------------------------------------------------
# offsets
# ---------------------------------------------------------------------------

class TestOffsets:
    def test_unset_is_zero(self):
        assert ElementStyleResolver({}).offset("score") == (0, 0)

    def test_layout_section(self):
        """The deployed sports convention: customization.layout.<element>."""
        config = {"customization": {"layout": {"score": {
            "x_offset": 3, "y_offset": -2}}}}
        assert ElementStyleResolver(config).offset("score") == (3, -2)

    def test_element_section_fallback(self):
        config = {"customization": {"score": {"x_offset": 5}}}
        assert ElementStyleResolver(config).offset("score") == (5, 0)

    def test_layout_section_wins_over_element_section(self):
        config = {"customization": {
            "layout": {"score": {"x_offset": 1}},
            "score": {"x_offset": 9, "y_offset": 9},
        }}
        resolver = ElementStyleResolver(config)
        assert resolver.offset_value("score", "x_offset") == 1
        # y_offset absent from layout section -> element section supplies it
        assert resolver.offset_value("score", "y_offset") == 9

    @pytest.mark.parametrize("raw,expected", [
        (2, 2), (2.7, 2), ("3", 3), ("2.0", 2), ("-4", -4),
        (None, 0), ("junk", 0), ([], 0), (True, 0),
    ])
    def test_coercion_matches_sports_helper(self, raw, expected):
        """Same tolerance as the sports.py/_get_layout_offset copies this
        replaces: int/float/numeric-string pass, anything else -> default."""
        config = {"customization": {"layout": {"e": {"x_offset": raw}}}}
        assert ElementStyleResolver(config).offset_value("e", "x_offset") == expected

    def test_custom_axis_names(self):
        """Football's records use away_x_offset/home_x_offset."""
        config = {"customization": {"layout": {"records": {
            "away_x_offset": 4, "home_x_offset": -4}}}}
        resolver = ElementStyleResolver(config)
        assert resolver.offset_value("records", "away_x_offset") == 4
        assert resolver.offset_value("records", "home_x_offset") == -4

    def test_style_carries_offset(self):
        config = {"customization": {"layout": {"score_text": {
            "x_offset": 2, "y_offset": 1}}}}
        assert _style(config).offset == (2, 1)


# ---------------------------------------------------------------------------
# caching
# ---------------------------------------------------------------------------

class TestCaching:
    def test_same_call_is_cached(self):
        resolver = ElementStyleResolver({}, SCHEMA_DEFAULTS)
        a = resolver.style("score_text", classic_font=PRESS_START, classic_size=10)
        b = resolver.style("score_text", classic_font=PRESS_START, classic_size=10)
        assert a is b

    def test_clear_cache(self):
        resolver = ElementStyleResolver({}, SCHEMA_DEFAULTS)
        a = resolver.style("score_text", classic_font=PRESS_START, classic_size=10)
        resolver.clear_cache()
        b = resolver.style("score_text", classic_font=PRESS_START, classic_size=10)
        assert a is not b
        # PIL fonts compare by identity; compare the value fields
        assert (a.font_name, a.font_size, a.color, a.offset, a.user_forced) == \
               (b.font_name, b.font_size, b.color, b.offset, b.user_forced)


# ---------------------------------------------------------------------------
# schema default extraction
# ---------------------------------------------------------------------------

class TestSchemaDefaults:
    def test_matches_schema_manager_extraction(self):
        """The pure helper must agree with SchemaManager.extract_defaults_from_schema
        on a real plugin-style schema — it exists so plugins get the same
        answer in harness contexts where the schema manager is absent."""
        from src.plugin_system.schema_manager import SchemaManager
        schema = {
            "type": "object",
            "properties": {
                "enabled": {"type": "boolean", "default": True},
                "customization": {
                    "type": "object",
                    "properties": {
                        "score_text": {
                            "type": "object",
                            "properties": {
                                "font": {"type": "string", "default": PRESS_START},
                                "font_size": {"type": "integer", "default": 10},
                                "y_percent": {"type": "number"},
                            },
                        },
                        "layout": {
                            "type": "object",
                            "properties": {
                                "score": {
                                    "type": "object",
                                    "properties": {
                                        "x_offset": {"type": "integer", "default": 0},
                                    },
                                },
                            },
                        },
                    },
                },
                "opaque_with_default": {"type": "object", "default": {},
                                        "properties": {"x": {"default": 1}}},
            },
        }
        pure = extract_schema_defaults(schema)
        managed = SchemaManager().extract_defaults_from_schema(schema)
        assert pure == managed
        assert pure["customization"]["score_text"]["font"] == PRESS_START
        # object-level default short-circuits recursion (both must agree)
        assert pure["opaque_with_default"] == {}

    def test_defaults_from_schema_file(self, tmp_path):
        schema_path = tmp_path / "config_schema.json"
        schema_path.write_text(json.dumps({
            "type": "object",
            "properties": {
                "customization": {
                    "type": "object",
                    "properties": {
                        "title_text": {
                            "type": "object",
                            "properties": {
                                "font": {"type": "string", "default": PRESS_START},
                            },
                        },
                    },
                },
            },
        }))
        defaults = defaults_from_schema_file(str(schema_path))
        assert defaults["customization"]["title_text"]["font"] == PRESS_START

    def test_defaults_from_missing_or_bad_file(self, tmp_path):
        assert defaults_from_schema_file("/nonexistent/schema.json") == {}
        bad = tmp_path / "bad.json"
        bad.write_text("{not json")
        assert defaults_from_schema_file(str(bad)) == {}


# ---------------------------------------------------------------------------
# BasePlugin integration
# ---------------------------------------------------------------------------

class _StubSchemaManager:
    """Schema manager double exposing the two methods the resolver path uses."""

    def __init__(self, schema):
        self._schema = schema

    def load_schema(self, plugin_id, use_cache=True):
        return self._schema

    def extract_defaults_from_schema(self, schema, prefix=""):
        # Mirror the real nested-dict extraction for this simple shape
        def walk(props):
            out = {}
            for key, spec in props.get("properties", {}).items():
                if "default" in spec:
                    out[key] = spec["default"]
                elif spec.get("type") == "object" and "properties" in spec:
                    nested = walk(spec)
                    if nested:
                        out[key] = nested
            return out
        return walk(schema)


def _make_plugin(config, schema=None):
    from src.plugin_system.base_plugin import BasePlugin
    from src.plugin_system.testing.mocks import (
        MockCacheManager, MockPluginManager)
    from src.plugin_system.testing.visual_display_manager import (
        VisualTestDisplayManager)

    class _Plugin(BasePlugin):
        def update(self):
            return True

        def display(self, force_clear=False):
            return None

    plugin_manager = MockPluginManager()
    if schema is not None:
        plugin_manager.schema_manager = _StubSchemaManager(schema)
    return _Plugin("test-plugin", config,
                   VisualTestDisplayManager(64, 32),
                   MockCacheManager(), plugin_manager)


TEST_SCHEMA = {
    "type": "object",
    "properties": {
        "customization": {
            "type": "object",
            "properties": {
                "score_text": {
                    "type": "object",
                    "properties": {
                        "font": {"type": "string", "default": PRESS_START},
                        "font_size": {"type": "integer", "default": 10},
                    },
                },
            },
        },
    },
}


class TestBasePluginIntegration:
    def test_element_style_with_schema_defaults(self):
        """The full path: saved config carries schema defaults, plugin's
        element_style still reports not-forced."""
        config = {"enabled": True, "customization": {"score_text": {
            "font": PRESS_START, "font_size": 10}}}
        plugin = _make_plugin(config, schema=TEST_SCHEMA)
        style = plugin.element_style("score_text", classic_font=PRESS_START,
                                     classic_size=10)
        assert not style.user_forced

    def test_element_style_detects_real_override(self):
        config = {"enabled": True, "customization": {"score_text": {
            "font": PRESS_START, "font_size": 14}}}
        plugin = _make_plugin(config, schema=TEST_SCHEMA)
        style = plugin.element_style("score_text", classic_font=PRESS_START,
                                     classic_size=10)
        assert style.user_forced_size
        assert style.font_size == 14

    def test_works_without_schema_manager(self):
        """MockPluginManager has no schema_manager attribute by default —
        the resolver degrades to classic-default comparison, no crash."""
        config = {"enabled": True, "customization": {"score_text": {
            "font_size": 12}}}
        plugin = _make_plugin(config, schema=None)
        style = plugin.element_style("score_text", classic_font=PRESS_START,
                                     classic_size=10)
        assert style.user_forced_size  # 12 != classic 10

    def test_resolver_is_cached_and_invalidated_on_config_change(self):
        plugin = _make_plugin({"enabled": True}, schema=TEST_SCHEMA)
        first = plugin.style_resolver
        assert plugin.style_resolver is first
        plugin.on_config_change({"enabled": True, "customization": {
            "score_text": {"font_size": 14}}})
        second = plugin.style_resolver
        assert second is not first
        style = plugin.element_style("score_text", classic_font=PRESS_START,
                                     classic_size=10)
        assert style.user_forced_size


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
