"""
Tests for src.element_style — the shared per-element style resolver behind
the x-style-elements system.

The contract under test (defined by the plugin consumers: of-the-day,
ledmatrix-music, football-scoreboard):

- defaults_from_schema_file parses BOTH declaration forms — the compact
  x-style-elements map and hand-written customization blocks.
- expand_style_elements turns an x-style-elements declaration into the full
  per-element blocks (plus layout offsets) the web-UI form renders.
- A config value counts as user-forced only when it genuinely differs from
  the schema default; untouched (or schema-default-populated) configs
  resolve to EXACTLY the classic font/size/color, keeping rendering
  byte-identical.
- style() never raises; malformed input degrades to the classic style.
"""

import json
import os

import pytest
from PIL import ImageFont

from src.element_style import (
    ElementStyleResolver,
    defaults_from_schema,
    defaults_from_schema_file,
    expand_style_elements,
    load_font,
    resolve_font_path,
)

# ---------------------------------------------------------------------------
# Schema fixtures
# ---------------------------------------------------------------------------

# Compact declaration form (of-the-day's shape).
STYLE_ELEMENTS_SCHEMA = {
    "type": "object",
    "properties": {
        "enabled": {"type": "boolean", "default": False},
        "customization": {
            "type": "object",
            "x-style-elements": {
                "title_text": {
                    "title": "Title",
                    "font": {"default": "PressStart2P-Regular.ttf"},
                    "size": {"default": 8, "min": 4, "max": 16},
                    "color": {"default": [255, 255, 255]},
                    "offsets": True,
                },
                "body_text": {
                    "title": "Body Text",
                    "font": {"default": "4x6-font.ttf"},
                    "size": {"default": 6, "min": 4, "max": 12},
                    "color": {"default": [200, 200, 200]},
                    "offsets": True,
                },
            },
        },
    },
}

# Manual declaration form (the scoreboards' / music's shape).
MANUAL_SCHEMA = {
    "type": "object",
    "properties": {
        "customization": {
            "type": "object",
            "properties": {
                "status_text": {
                    "type": "object",
                    "properties": {
                        "font": {"type": "string",
                                 "default": "4x6-font.ttf"},
                        "font_size": {"type": "integer", "default": 6},
                    },
                },
                "score_text": {
                    "type": "object",
                    "properties": {
                        "font": {"type": "string",
                                 "default": "PressStart2P-Regular.ttf"},
                        "font_size": {"type": "integer", "default": 10},
                        "text_color": {"type": "array",
                                       "default": [255, 255, 0]},
                    },
                },
                "layout": {"type": "object", "properties": {}},
            },
        },
    },
}


@pytest.fixture
def style_schema_path(tmp_path):
    path = tmp_path / "config_schema.json"
    path.write_text(json.dumps(STYLE_ELEMENTS_SCHEMA))
    return str(path)


@pytest.fixture
def manual_schema_path(tmp_path):
    path = tmp_path / "config_schema.json"
    path.write_text(json.dumps(MANUAL_SCHEMA))
    return str(path)


def _resolver(config, schema_path):
    return ElementStyleResolver(config, defaults_from_schema_file(schema_path))


# ---------------------------------------------------------------------------
# Schema parsing
# ---------------------------------------------------------------------------

class TestDefaultsFromSchema:
    def test_x_style_elements_defaults(self, style_schema_path):
        defaults = defaults_from_schema_file(style_schema_path)
        cust = defaults["customization"]
        assert cust["title_text"] == {"font": "PressStart2P-Regular.ttf",
                                      "font_size": 8,
                                      "text_color": [255, 255, 255]}
        assert cust["body_text"]["font_size"] == 6
        assert cust["body_text"]["text_color"] == [200, 200, 200]

    def test_manual_block_defaults(self, manual_schema_path):
        defaults = defaults_from_schema_file(manual_schema_path)
        cust = defaults["customization"]
        assert cust["status_text"] == {"font": "4x6-font.ttf", "font_size": 6}
        assert cust["score_text"]["text_color"] == [255, 255, 0]
        assert "layout" not in cust

    def test_missing_file_degrades_to_empty(self, tmp_path):
        defaults = defaults_from_schema_file(str(tmp_path / "nope.json"))
        assert defaults == {"customization": {}}

    def test_malformed_file_degrades_to_empty(self, tmp_path):
        path = tmp_path / "bad.json"
        path.write_text("{not json")
        assert defaults_from_schema_file(str(path)) == {"customization": {}}

    def test_schema_without_customization(self):
        assert defaults_from_schema({"properties": {}}) == {"customization": {}}


class TestExpandStyleElements:
    def test_expansion_generates_blocks(self):
        expanded = expand_style_elements(STYLE_ELEMENTS_SCHEMA)
        cust = expanded["properties"]["customization"]["properties"]
        title = cust["title_text"]
        assert title["x-style-managed"] is True
        assert title["properties"]["font"]["default"] == \
            "PressStart2P-Regular.ttf"
        assert title["properties"]["font_size"]["default"] == 8
        assert title["properties"]["font_size"]["minimum"] == 4
        assert title["properties"]["font_size"]["maximum"] == 16
        assert cust["body_text"]["properties"]["text_color"]["default"] == \
            [200, 200, 200]

    def test_expansion_generates_layout_offsets(self):
        expanded = expand_style_elements(STYLE_ELEMENTS_SCHEMA)
        layout = expanded["properties"]["customization"]["properties"]["layout"]
        assert "title_text" in layout["properties"]
        offsets = layout["properties"]["body_text"]["properties"]
        assert offsets["x_offset"]["default"] == 0
        assert offsets["y_offset"]["default"] == 0

    def test_input_schema_not_mutated(self):
        before = json.dumps(STYLE_ELEMENTS_SCHEMA, sort_keys=True)
        expand_style_elements(STYLE_ELEMENTS_SCHEMA)
        assert json.dumps(STYLE_ELEMENTS_SCHEMA, sort_keys=True) == before

    def test_no_declaration_returns_same_object(self):
        assert expand_style_elements(MANUAL_SCHEMA) is MANUAL_SCHEMA
        empty = {"properties": {}}
        assert expand_style_elements(empty) is empty

    def test_garbage_input_never_raises(self):
        bad = {"properties": {"customization": {"x-style-elements": "nope"}}}
        assert expand_style_elements(bad) is bad


# ---------------------------------------------------------------------------
# Classic identity: untouched configs resolve to the classic style
# ---------------------------------------------------------------------------

class TestClassicIdentity:
    def test_bare_config_resolves_classic(self, style_schema_path):
        r = _resolver({}, style_schema_path)
        style = r.style("title_text", classic_font="PressStart2P-Regular.ttf",
                        classic_size=8, classic_color=(255, 255, 255))
        assert style.font_name == "PressStart2P-Regular.ttf"
        assert style.font_size == 8
        assert style.color == (255, 255, 255)
        assert style.offset == (0, 0)
        assert not style.user_forced
        assert not style.user_forced_color
        assert isinstance(style.font, ImageFont.FreeTypeFont)
        assert style.font.size == 8

    def test_schema_populated_config_is_not_an_override(self, style_schema_path):
        # The web UI's save flow writes the full schema defaults into config
        # on every save — that must not count as a user override.
        config = {"customization": {
            "title_text": {"font": "PressStart2P-Regular.ttf", "font_size": 8,
                           "text_color": [255, 255, 255]},
            "layout": {"title_text": {"x_offset": 0, "y_offset": 0}},
        }}
        style = _resolver(config, style_schema_path).style(
            "title_text", classic_font="PressStart2P-Regular.ttf",
            classic_size=8, classic_color=(255, 255, 255))
        assert not style.user_forced
        assert not style.user_forced_color
        assert style.font_size == 8
        assert style.color == (255, 255, 255)
        assert style.offset == (0, 0)

    def test_schema_default_falls_back_to_classic_not_schema_font(
            self, manual_schema_path):
        # Classic values and schema defaults can legitimately differ
        # (football's status_text: schema says 4x6, classic loader used
        # PressStart). A schema-default config value must yield the CLASSIC
        # font, byte-identical to the old loader.
        config = {"customization": {"status_text": {"font": "4x6-font.ttf",
                                                    "font_size": 6}}}
        style = _resolver(config, manual_schema_path).style(
            "status_text", classic_font="PressStart2P-Regular.ttf",
            classic_size=6)
        assert not style.user_forced
        assert style.font_name == "PressStart2P-Regular.ttf"
        assert style.font_size == 6

    def test_same_font_object_from_cache(self, style_schema_path):
        r = _resolver({}, style_schema_path)
        s1 = r.style("title_text", classic_font="PressStart2P-Regular.ttf",
                     classic_size=8)
        s2 = ElementStyleResolver({}, {}).style(
            "title_text", classic_font="PressStart2P-Regular.ttf",
            classic_size=8)
        assert s1.font is s2.font


# ---------------------------------------------------------------------------
# User overrides engage
# ---------------------------------------------------------------------------

class TestUserOverrides:
    def test_font_override(self, style_schema_path):
        config = {"customization": {"title_text": {"font": "4x6-font.ttf"}}}
        style = _resolver(config, style_schema_path).style(
            "title_text", classic_font="PressStart2P-Regular.ttf",
            classic_size=8)
        assert style.user_forced
        assert style.font_name == "4x6-font.ttf"
        assert style.font_size == 8  # size untouched -> classic

    def test_size_override(self, style_schema_path):
        config = {"customization": {"title_text": {
            "font": "PressStart2P-Regular.ttf", "font_size": 16}}}
        style = _resolver(config, style_schema_path).style(
            "title_text", classic_font="PressStart2P-Regular.ttf",
            classic_size=8)
        assert style.user_forced
        assert style.font_name == "PressStart2P-Regular.ttf"
        assert style.font_size == 16
        assert style.font.size == 16

    def test_size_override_detected_vs_schema_default(self, manual_schema_path):
        # font_size 8 differs from the schema default 6 -> forced.
        config = {"customization": {"status_text": {"font": "4x6-font.ttf",
                                                    "font_size": 8}}}
        style = _resolver(config, manual_schema_path).style(
            "status_text", classic_font="PressStart2P-Regular.ttf",
            classic_size=6)
        assert style.user_forced
        assert style.font_size == 8

    def test_color_override(self, style_schema_path):
        config = {"customization": {"title_text": {"text_color": [255, 0, 0]}}}
        style = _resolver(config, style_schema_path).style(
            "title_text", classic_font="PressStart2P-Regular.ttf",
            classic_size=8, classic_color=(255, 255, 255))
        assert style.user_forced_color
        assert not style.user_forced
        assert style.color == (255, 0, 0)

    def test_offsets(self, style_schema_path):
        config = {"customization": {"layout": {
            "title_text": {"x_offset": 4, "y_offset": -2}}}}
        r = _resolver(config, style_schema_path)
        assert r.offset("title_text") == (4, -2)
        assert r.offset("body_text") == (0, 0)
        style = r.style("title_text", classic_font="PressStart2P-Regular.ttf",
                        classic_size=8)
        assert style.offset == (4, -2)

    def test_offset_value_arbitrary_axis_and_strings(self, style_schema_path):
        # The scoreboards read non-standard axes (away_x_offset) and configs
        # can carry numeric strings/floats.
        config = {"customization": {"layout": {"records": {
            "away_x_offset": "3", "home_x_offset": 2.7}}}}
        r = _resolver(config, style_schema_path)
        assert r.offset_value("records", "away_x_offset", 0) == 3
        assert r.offset_value("records", "home_x_offset", 0) == 2
        assert r.offset_value("records", "missing_axis", 5) == 5


# ---------------------------------------------------------------------------
# Defensive degradation
# ---------------------------------------------------------------------------

class TestDegradation:
    @pytest.mark.parametrize("config", [
        None,
        {"customization": "not a dict"},
        {"customization": {"title_text": "not a dict"}},
        {"customization": {"title_text": {"font": 42, "font_size": "huge",
                                          "text_color": "red"}}},
        {"customization": {"layout": {"title_text": {"x_offset": "junk"}}}},
    ])
    def test_bad_config_degrades_to_classic(self, config, style_schema_path):
        style = _resolver(config, style_schema_path).style(
            "title_text", classic_font="PressStart2P-Regular.ttf",
            classic_size=8, classic_color=(10, 20, 30))
        assert not style.user_forced
        assert not style.user_forced_color
        assert style.font_name == "PressStart2P-Regular.ttf"
        assert style.font_size == 8
        assert style.color == (10, 20, 30)
        assert style.offset == (0, 0)

    def test_unknown_font_falls_back(self, style_schema_path):
        config = {"customization": {"title_text": {"font": "no-such.ttf"}}}
        style = _resolver(config, style_schema_path).style(
            "title_text", classic_font="PressStart2P-Regular.ttf",
            classic_size=8)
        # The override IS honored as forced, but the face degrades safely.
        assert style.user_forced
        assert style.font is not None

    def test_empty_defaults_treats_config_as_reference_to_classic(self):
        # No schema defaults at all: a config value equal to the classic
        # value is not forced; a different one is.
        r = ElementStyleResolver(
            {"customization": {"e": {"font": "4x6-font.ttf"}}}, {})
        assert not r.style("e", classic_font="4x6-font.ttf",
                           classic_size=6).user_forced
        assert r.style("e", classic_font="PressStart2P-Regular.ttf",
                       classic_size=6).user_forced


# ---------------------------------------------------------------------------
# Resolver plumbing the consumers rely on
# ---------------------------------------------------------------------------

class TestResolverPlumbing:
    def test_config_identity_exposed(self, style_schema_path):
        # Consumers rebuild the resolver when the config dict is swapped:
        # `resolver._config is not self.config`.
        config = {"customization": {}}
        r = _resolver(config, style_schema_path)
        assert r._config is config

    def test_font_path_resolution_is_cwd_independent(self, tmp_path,
                                                     monkeypatch):
        monkeypatch.chdir(tmp_path)  # no assets/fonts under cwd
        path = resolve_font_path("PressStart2P-Regular.ttf")
        assert path is not None and os.path.isfile(path)
        font = load_font("PressStart2P-Regular.ttf", 8)
        assert isinstance(font, ImageFont.FreeTypeFont)

    def test_bdf_font_loads_as_freetype_face(self):
        import freetype
        font = load_font("5x7.bdf", 7)
        assert isinstance(font, freetype.Face)

    def test_schema_manager_expands_on_load(self, tmp_path):
        # The web-UI form path: SchemaManager.load_schema serves the
        # expanded schema so the style blocks actually appear in the UI.
        from src.plugin_system.schema_manager import SchemaManager
        plugin_dir = tmp_path / "plugins" / "styled"
        plugin_dir.mkdir(parents=True)
        (plugin_dir / "config_schema.json").write_text(
            json.dumps(STYLE_ELEMENTS_SCHEMA))
        (plugin_dir / "manifest.json").write_text(json.dumps({
            "id": "styled", "config_schema": "config_schema.json"}))
        manager = SchemaManager(plugins_dir=tmp_path / "plugins",
                                project_root=tmp_path)
        schema = manager.load_schema("styled")
        assert schema is not None
        cust = schema["properties"]["customization"]["properties"]
        assert cust["title_text"]["x-style-managed"] is True
        assert "title_text" in cust["layout"]["properties"]
