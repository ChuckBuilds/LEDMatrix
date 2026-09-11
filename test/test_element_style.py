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

import copy
import json
import os

import pytest
from PIL import ImageFont

from src.element_style import (
    alias_keys,
    ElementStyleResolver,
    _load_font_sized,
    native_bdf_size,
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

    def test_a_schema_with_nothing_to_expand_is_returned_as_is(self):
        """No needless deep copy when there is nothing to do."""
        empty = {"properties": {}}
        assert expand_style_elements(empty) is empty
        no_style = {"properties": {"customization": {"type": "object",
            "properties": {"favorite_result_colors": {"type": "object",
                "properties": {"win_color": {"type": "array"}}}}}}}
        assert expand_style_elements(no_style) is no_style

    def test_a_hand_written_block_is_adopted(self):
        """Nineteen plugins spell their style elements out longhand rather
        than declaring them, and predate this system entirely. They pick up
        the editor and the font picker on a core update rather than on a
        plugin release."""
        expanded = expand_style_elements(MANUAL_SCHEMA)
        assert expanded is not MANUAL_SCHEMA
        customization = expanded["properties"]["customization"]
        assert customization["x-widget"] == "style-editor"
        assert "x-style-elements" not in MANUAL_SCHEMA["properties"]["customization"]

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

    @pytest.mark.parametrize("hostile", [
        "../../config/config.json",
        "../secrets.txt",
        "sub/dir/font.ttf",
        "..",
    ])
    def test_relative_font_name_with_path_components_is_rejected(self, hostile):
        # font_name comes from plugin config (web-UI writable); a relative name
        # carrying path separators would escape assets/fonts/ after os.path.join
        # and let a config probe arbitrary paths. Only bare filenames resolve.
        assert resolve_font_path(hostile) is None

    def test_bare_filename_still_resolves(self):
        # The guard must not reject legitimate bare names.
        assert resolve_font_path("PressStart2P-Regular.ttf") is not None

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
class TestBdfSizing:
    """A BDF is a fixed-size bitmap strike, not a scalable outline.

    FreeType accepts only the exact pixel size baked into the file and raises
    for anything else, and 32 of the 35 shipped fonts are BDF -- so a size
    picked in the web UI usually is not a valid strike. This used to fall
    through to the generic font-load except and return *PressStart2P*, so
    asking for 5x7.bdf at size 10 silently rendered a different typeface.
    It now falls back to the file's own size instead, matching what
    SportsCore._load_custom_font_from_element_config already did.
    """

    def test_a_valid_strike_loads_at_that_size(self):
        import freetype
        font, realised = _load_font_sized("5x7.bdf", 7)
        assert isinstance(font, freetype.Face)
        assert realised == 7

    @pytest.mark.parametrize("requested", [4, 10, 16, 32])
    def test_a_wrong_size_keeps_the_font_and_snaps_the_size(self, requested):
        """The regression: the face must still be 5x7, not a substitute."""
        import freetype
        font, realised = _load_font_sized("5x7.bdf", requested)
        assert isinstance(font, freetype.Face), (
            "a BDF asked for a bad size used to come back as a PIL "
            "PressStart2P face -- a different font entirely")
        assert realised == 7

    def test_the_reported_size_is_what_was_realised(self, style_schema_path):
        """ElementStyle.font_size drives caller layout, so it must not report
        a size nothing was drawn at."""
        config = {"customization": {"title_text": {"font": "5x7.bdf",
                                                   "font_size": 20}}}
        style = ElementStyleResolver(
            config, defaults_from_schema_file(style_schema_path)
        ).style("title_text", classic_font="PressStart2P-Regular.ttf",
                classic_size=8)
        assert style.font_name == "5x7.bdf"
        assert style.font_size == 7

    def test_native_bdf_size_reads_the_file(self):
        assert native_bdf_size("5x7.bdf") == 7

    @pytest.mark.parametrize("name", ["PressStart2P-Regular.ttf",
                                      "no-such-font.bdf", "", None])
    def test_native_bdf_size_is_none_when_size_is_a_free_choice(self, name):
        """None is the web UI's signal that the size field stays editable."""
        assert native_bdf_size(name) is None

    def test_a_scalable_font_realises_the_requested_size(self):
        for size in (6, 8, 13):
            font, realised = _load_font_sized("PressStart2P-Regular.ttf", size)
            assert realised == size
            assert not isinstance(font, type(None))


class TestFontCacheIsBounded:
    """The display process runs for weeks and every config save can add a new
    (font, size) pair; this cache was unbounded, against the house style of
    every other hot cache in the codebase."""

    def test_the_cache_evicts_past_its_bound(self):
        import src.element_style as es
        es._font_cache.clear()
        try:
            for size in range(1, es._FONT_CACHE_MAX + 40):
                load_font("PressStart2P-Regular.ttf", size)
            assert len(es._font_cache) <= es._FONT_CACHE_MAX
        finally:
            es._font_cache.clear()

    def test_a_repeated_load_is_the_same_object(self):
        import src.element_style as es
        es._font_cache.clear()
        try:
            first = load_font("PressStart2P-Regular.ttf", 8)
            assert load_font("PressStart2P-Regular.ttf", 8) is first
        finally:
            es._font_cache.clear()

    def test_eviction_is_least_recently_used(self):
        import src.element_style as es
        es._font_cache.clear()
        try:
            oldest = load_font("PressStart2P-Regular.ttf", 1)
            for size in range(2, es._FONT_CACHE_MAX + 1):
                load_font("PressStart2P-Regular.ttf", size)
            # Touch the oldest so it is no longer the eviction candidate,
            # then overflow by one.
            assert load_font("PressStart2P-Regular.ttf", 1) is oldest
            load_font("PressStart2P-Regular.ttf", es._FONT_CACHE_MAX + 1)
            assert load_font("PressStart2P-Regular.ttf", 1) is oldest
        finally:
            es._font_cache.clear()

class TestModes:
    """Per-mode overrides: one element styled differently per situation.

    The motivating case is a scoreboard, where the score wants a bigger font
    on a live card than on an upcoming one. Live/Upcoming/Recent are already
    separate instances with distinct SKIN_MODE values, so the mode is bound
    to the resolver rather than threaded through every call site.

    A mode layer is pure override: its fields default to None, meaning
    inherit. That is why None and 0 must stay distinct -- a mode y_offset of
    0 means "sit at the base position", not "no preference".
    """

    def _resolver(self, config, mode=None, schema=None):
        defaults = defaults_from_schema_file(schema) if schema else {}
        return ElementStyleResolver(config, defaults, mode=mode)

    def test_no_mode_block_resolves_exactly_as_before(self, style_schema_path):
        config = {"customization": {"title_text": {"font_size": 12}}}
        plain = self._resolver(config, schema=style_schema_path)
        moded = self._resolver(config, mode="live", schema=style_schema_path)
        a = plain.style("title_text", classic_size=8)
        b = moded.style("title_text", classic_size=8)
        assert (a.font_name, a.font_size, a.color) == (b.font_name, b.font_size,
                                                       b.color)

    def test_a_mode_overrides_the_base_size(self, style_schema_path):
        config = {"customization": {
            "title_text": {"font_size": 12},
            "modes": {"live": {"title_text": {"font_size": 16}}}}}
        r = self._resolver(config, mode="live", schema=style_schema_path)
        assert r.style("title_text", classic_size=8).font_size == 16
        assert r.style("title_text", classic_size=8).user_forced is True

    def test_a_different_mode_is_unaffected(self, style_schema_path):
        config = {"customization": {
            "title_text": {"font_size": 12},
            "modes": {"live": {"title_text": {"font_size": 16}}}}}
        r = self._resolver(config, mode="upcoming", schema=style_schema_path)
        assert r.style("title_text", classic_size=8).font_size == 12

    def test_two_resolvers_share_a_config_and_differ_by_mode(
            self, style_schema_path):
        """The SportsUpcoming / SportsRecent case: same config dict, two
        instances, two answers."""
        config = {"customization": {
            "title_text": {"font_size": 12},
            "modes": {"live": {"title_text": {"font_size": 16}},
                      "recent": {"title_text": {"font_size": 6}}}}}
        live = self._resolver(config, mode="live", schema=style_schema_path)
        recent = self._resolver(config, mode="recent", schema=style_schema_path)
        assert live.style("title_text", classic_size=8).font_size == 16
        assert recent.style("title_text", classic_size=8).font_size == 6

    def test_a_mode_overrides_font_and_colour(self, style_schema_path):
        config = {"customization": {
            "modes": {"live": {"title_text": {"font": "4x6-font.ttf",
                                              "text_color": [1, 2, 3]}}}}}
        st = self._resolver(config, mode="live",
                            schema=style_schema_path).style(
            "title_text", classic_font="PressStart2P-Regular.ttf",
            classic_color=(255, 255, 255))
        assert st.font_name == "4x6-font.ttf"
        assert st.color == (1, 2, 3)
        assert st.user_forced and st.user_forced_color

    def test_an_unset_mode_field_inherits_rather_than_resetting(
            self, style_schema_path):
        """Only font_size is overridden; the colour must survive."""
        config = {"customization": {
            "title_text": {"text_color": [9, 9, 9]},
            "modes": {"live": {"title_text": {"font_size": 16}}}}}
        st = self._resolver(config, mode="live",
                            schema=style_schema_path).style(
            "title_text", classic_color=(255, 255, 255))
        assert st.font_size == 16
        assert st.color == (9, 9, 9)

    def test_a_null_mode_field_means_inherit(self, style_schema_path):
        config = {"customization": {
            "title_text": {"font_size": 12},
            "modes": {"live": {"title_text": {"font_size": None}}}}}
        st = self._resolver(config, mode="live",
                            schema=style_schema_path).style("title_text")
        assert st.font_size == 12

    def test_a_per_call_mode_overrides_the_bound_one(self, style_schema_path):
        config = {"customization": {"modes": {
            "live": {"title_text": {"font_size": 16}},
            "recent": {"title_text": {"font_size": 6}}}}}
        r = self._resolver(config, mode="live", schema=style_schema_path)
        assert r.style("title_text").font_size == 16
        assert r.style("title_text", mode="recent").font_size == 6

    def test_the_memo_does_not_leak_between_modes(self, style_schema_path):
        config = {"customization": {"modes": {
            "live": {"title_text": {"font_size": 16}},
            "recent": {"title_text": {"font_size": 6}}}}}
        r = self._resolver(config, mode="live", schema=style_schema_path)
        assert r.style("title_text").font_size == 16
        assert r.style("title_text", mode="recent").font_size == 6
        assert r.style("title_text").font_size == 16

    def test_mode_is_exposed(self):
        assert ElementStyleResolver({}, {}, mode="live").mode == "live"
        assert ElementStyleResolver({}, {}).mode is None


class TestModeOffsets:
    def _r(self, config, mode=None):
        return ElementStyleResolver(config, {}, mode=mode)

    def test_a_mode_offset_wins(self):
        config = {"customization": {
            "layout": {"score": {"y_offset": -2}},
            "modes": {"live": {"layout": {"score": {"y_offset": 5}}}}}}
        assert self._r(config, "live").offset_value("score", "y_offset") == 5

    def test_an_absent_mode_offset_inherits_the_base(self):
        config = {"customization": {
            "layout": {"score": {"y_offset": -2}},
            "modes": {"live": {"layout": {"score": {"x_offset": 1}}}}}}
        r = self._r(config, "live")
        assert r.offset_value("score", "y_offset") == -2
        assert r.offset_value("score", "x_offset") == 1

    def test_an_explicit_zero_is_an_override_not_an_absence(self):
        """The reason None is the inherit sentinel: 0 has to mean something."""
        config = {"customization": {
            "layout": {"score": {"y_offset": -2}},
            "modes": {"live": {"layout": {"score": {"y_offset": 0}}}}}}
        assert self._r(config, "live").offset_value("score", "y_offset") == 0

    def test_a_null_mode_offset_inherits(self):
        config = {"customization": {
            "layout": {"score": {"y_offset": -2}},
            "modes": {"live": {"layout": {"score": {"y_offset": None}}}}}}
        assert self._r(config, "live").offset_value("score", "y_offset") == -2

    def test_offset_pair_is_mode_aware(self):
        config = {"customization": {
            "layout": {"score": {"x_offset": 1, "y_offset": 2}},
            "modes": {"live": {"layout": {"score": {"y_offset": 9}}}}}}
        assert self._r(config, "live").offset("score") == (1, 9)

    def test_an_arbitrary_axis_is_mode_aware(self):
        """The scoreboards use away_x_offset / home_x_offset."""
        config = {"customization": {
            "layout": {"records": {"away_x_offset": 3}},
            "modes": {"recent": {"layout": {"records": {"away_x_offset": 7}}}}}}
        assert self._r(config, "recent").offset_value(
            "records", "away_x_offset") == 7

    @pytest.mark.parametrize("modes", [
        None, "nonsense", {"live": "nonsense"}, {"live": {"layout": 5}},
        {"live": {"layout": {"score": {"y_offset": "bad"}}}},
    ])
    def test_garbage_modes_degrade_to_the_base(self, modes):
        config = {"customization": {"layout": {"score": {"y_offset": -2}},
                                    "modes": modes}}
        assert self._r(config, "live").offset_value("score", "y_offset") == -2

    @pytest.mark.parametrize("modes", [
        None, "nonsense", {"live": "nonsense"},
        {"live": {"title_text": "nonsense"}},
        {"live": {"title_text": {"font_size": "huge", "text_color": "red"}}},
    ])
    def test_garbage_mode_styles_degrade(self, modes, style_schema_path):
        config = {"customization": {"title_text": {"font_size": 12},
                                    "modes": modes}}
        st = ElementStyleResolver(
            config, defaults_from_schema_file(style_schema_path),
            mode="live").style("title_text", classic_size=8)
        assert st.font_size == 12

MODE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "enabled": {"type": "boolean", "default": False},
        "customization": {
            "type": "object",
            "x-style-modes": ["live", "recent"],
            "x-style-elements": {
                "score_text": {
                    "title": "Score",
                    "font": {"default": "PressStart2P-Regular.ttf"},
                    "size": {"default": 10, "min": 4, "max": 16},
                    "color": {"default": [255, 255, 255]},
                    "offsets": True,
                },
            },
        },
    },
}


class TestModeSchemaEmission:
    def _expanded(self):
        return expand_style_elements(MODE_SCHEMA)["properties"]["customization"]

    def test_a_modes_block_is_emitted_per_declared_mode(self):
        assert sorted(self._expanded()["properties"]["modes"]["properties"]) == [
            "live", "recent"]

    def test_mode_fields_are_nullable_and_default_to_null(self):
        """Null is the inherit sentinel. A concrete default here would turn
        every mode into a copy of the base the moment the user pressed Save,
        because the save flow writes schema defaults into config wholesale."""
        live = self._expanded()["properties"]["modes"]["properties"]["live"]
        block = live["properties"]["score_text"]["properties"]
        for name, prop in block.items():
            assert prop["default"] is None, name
            assert "null" in prop["type"], name

    def test_mode_offsets_are_nullable_too(self):
        live = self._expanded()["properties"]["modes"]["properties"]["live"]
        axes = live["properties"]["layout"]["properties"]["score_text"]["properties"]
        assert set(axes) == {"x_offset", "y_offset"}
        for prop in axes.values():
            assert prop["default"] is None
            assert "null" in prop["type"]

    def test_the_base_block_keeps_its_real_defaults(self):
        block = self._expanded()["properties"]["score_text"]["properties"]
        assert block["font_size"]["default"] == 10
        assert block["font"]["default"] == "PressStart2P-Regular.ttf"

    def test_the_font_field_asks_for_the_font_selector_widget(self):
        """The widget already existed and was already allowlisted by the
        config form; without the hint the field was a bare text box the user
        had to type a filename into."""
        block = self._expanded()["properties"]["score_text"]["properties"]
        assert block["font"]["x-widget"] == "font-selector"

    def test_no_declared_modes_emits_no_modes_block(self):
        schema = copy.deepcopy(MODE_SCHEMA)
        del schema["properties"]["customization"]["x-style-modes"]
        expanded = expand_style_elements(schema)["properties"]["customization"]
        assert "modes" not in expanded["properties"]

    @pytest.mark.parametrize("modes", ["nonsense", [], [None], [""], 5, {}])
    def test_garbage_mode_declarations_do_not_break_expansion(self, modes):
        schema = copy.deepcopy(MODE_SCHEMA)
        schema["properties"]["customization"]["x-style-modes"] = modes
        expanded = expand_style_elements(schema)["properties"]["customization"]
        assert "score_text" in expanded["properties"]

    def test_the_input_schema_is_not_mutated(self):
        expand_style_elements(MODE_SCHEMA)
        assert "properties" not in MODE_SCHEMA["properties"]["customization"]


class TestModeSaveRoundTrip:
    """The path a real save takes: schema -> defaults -> merge -> validate ->
    resolve. The risk being covered is that the save flow writes schema
    defaults into config.json wholesale, so a mode block has to survive that
    still meaning "inherit"."""

    @pytest.fixture
    def plugin(self, tmp_path):
        from src.plugin_system.schema_manager import SchemaManager
        pdir = tmp_path / "plugins" / "demo"
        pdir.mkdir(parents=True)
        (pdir / "config_schema.json").write_text(json.dumps(MODE_SCHEMA),
                                                 encoding="utf-8")
        sm = SchemaManager(plugins_dir=tmp_path / "plugins",
                           project_root=tmp_path)
        schema = sm.load_schema("demo", use_cache=False)
        return sm, schema, sm.extract_defaults_from_schema(schema), pdir

    def test_a_save_leaves_mode_blocks_meaning_inherit(self, plugin):
        sm, schema, defaults, pdir = plugin
        merged = sm.merge_with_defaults(
            {"enabled": True, "customization": {"score_text": {"font_size": 14}}},
            defaults)
        live = merged["customization"]["modes"]["live"]["score_text"]
        assert live == {"font": None, "font_size": None, "text_color": None}

        ok, errors = sm.validate_config_against_schema(merged, schema)
        assert ok, errors

        style = ElementStyleResolver(
            merged, defaults_from_schema_file(str(pdir / "config_schema.json")),
            mode="live").style("score_text",
                               classic_font="PressStart2P-Regular.ttf",
                               classic_size=10)
        assert style.font_size == 14, "the base must still reach the mode"

    def test_a_real_mode_override_validates_and_wins(self, plugin):
        sm, schema, defaults, pdir = plugin
        merged = sm.merge_with_defaults({"customization": {
            "score_text": {"font_size": 14},
            "modes": {"live": {"score_text": {"font_size": 16},
                               "layout": {"score_text": {"y_offset": -3}}}},
        }}, defaults)
        ok, errors = sm.validate_config_against_schema(merged, schema)
        assert ok, errors

        schema_defaults = defaults_from_schema_file(
            str(pdir / "config_schema.json"))
        live = ElementStyleResolver(merged, schema_defaults, mode="live")
        recent = ElementStyleResolver(merged, schema_defaults, mode="recent")
        assert live.style("score_text", classic_size=10).font_size == 16
        assert live.offset("score_text") == (0, -3)
        assert recent.style("score_text", classic_size=10).font_size == 14
        assert recent.offset("score_text") == (0, 0)

    def test_a_mode_size_outside_the_declared_range_is_rejected(self, plugin):
        """min/max from the declaration must carry into the mode blocks."""
        sm, schema, defaults, _ = plugin
        merged = sm.merge_with_defaults(
            {"customization": {"modes": {"live": {"score_text": {
                "font_size": 99}}}}}, defaults)
        ok, _errors = sm.validate_config_against_schema(merged, schema)
        assert not ok

EXTRA_SCHEMA = {
    "type": "object",
    "properties": {
        "customization": {
            "type": "object",
            "x-style-modes": ["live"],
            "x-style-elements": {
                "score_text": {
                    "title": "Score",
                    "font": {"default": "PressStart2P-Regular.ttf"},
                    "size": {"default": 10},
                    "color": {"default": [255, 255, 255]},
                    "visible": True,
                    "align": {"default": "center"},
                    "offsets": True,
                },
                "home_logo": {
                    "title": "Home Logo",
                    "offsets": True,
                    "visible": True,
                    "scale": {"default": 1.0, "min": 0.25, "max": 4},
                },
            },
        },
    },
}


@pytest.fixture
def extra_schema_path(tmp_path):
    path = tmp_path / "extra_schema.json"
    path.write_text(json.dumps(EXTRA_SCHEMA), encoding="utf-8")
    return str(path)


class TestVisibleAlignScale:
    """visible / align / scale all resolve to "change nothing" until asked.

    That is the same invariant the font fields keep: a caller that honours
    these must still render an untouched config exactly as it did before
    they existed, so the neutral values are True / None / 1.0 rather than
    whatever the schema happens to declare.
    """

    def _style(self, config, mode=None, schema=None):
        defaults = defaults_from_schema_file(schema) if schema else {}
        return ElementStyleResolver(config, defaults, mode=mode).style(
            "score_text", classic_font="PressStart2P-Regular.ttf",
            classic_size=10, classic_color=(255, 255, 255))

    def test_an_untouched_config_is_neutral(self, extra_schema_path):
        st = self._style({}, schema=extra_schema_path)
        assert (st.visible, st.align, st.scale) == (True, None, 1.0)

    def test_a_schema_default_is_not_a_choice(self, extra_schema_path):
        """align defaults to 'center' in the schema, and the save flow writes
        that into config -- which must not read as the user asking for it."""
        config = {"customization": {"score_text": {"align": "center",
                                                   "visible": True}}}
        st = self._style(config, schema=extra_schema_path)
        assert st.align is None, "the plugin keeps its own alignment"
        assert st.visible is True

    def test_hiding_an_element(self, extra_schema_path):
        config = {"customization": {"score_text": {"visible": False}}}
        assert self._style(config, schema=extra_schema_path).visible is False

    def test_a_real_alignment_choice_comes_through(self, extra_schema_path):
        config = {"customization": {"score_text": {"align": "right"}}}
        assert self._style(config, schema=extra_schema_path).align == "right"

    @pytest.mark.parametrize("written,expected", [
        ("left", "left"), ("RIGHT", "right"), ("  center ", "center"),
        ("centre", "center"), ("middle", "center"),
        ("sideways", None), ("", None), (5, None), (None, None),
    ])
    def test_alignment_coercion(self, written, expected, extra_schema_path):
        config = {"customization": {"score_text": {"align": written}}}
        assert self._style(config, schema=extra_schema_path).align == expected

    @pytest.mark.parametrize("written,expected", [
        (2, 2.0), (0.5, 0.5), ("1.5", 1.5),
        (0, 1.0), (-3, 1.0),          # nonsense degrades, never inverts
        (999, 10.0),                  # clamped: the panel is 32px tall
        ("huge", 1.0), (None, 1.0), (True, 1.0),
    ])
    def test_scale_coercion(self, written, expected, extra_schema_path):
        config = {"customization": {"layout": {"score_text": {"scale": written}}}}
        assert self._style(config, schema=extra_schema_path).scale == expected

    def test_scale_reads_from_the_layout_block(self, extra_schema_path):
        """scale is geometry, so it sits with the offsets -- a logo has a
        scale and no font."""
        config = {"customization": {"layout": {"score_text": {"scale": 2}}}}
        assert self._style(config, schema=extra_schema_path).scale == 2.0

    @pytest.mark.parametrize("field,value,attr,expected", [
        ("visible", False, "visible", False),
        ("align", "right", "align", "right"),
    ])
    def test_a_mode_overrides_them(self, field, value, attr, expected,
                                   extra_schema_path):
        config = {"customization": {"modes": {
            "live": {"score_text": {field: value}}}}}
        st = self._style(config, mode="live", schema=extra_schema_path)
        assert getattr(st, attr) == expected

    def test_a_mode_overrides_scale(self, extra_schema_path):
        config = {"customization": {
            "layout": {"score_text": {"scale": 2}},
            "modes": {"live": {"layout": {"score_text": {"scale": 3}}}}}}
        assert self._style(config, mode="live",
                           schema=extra_schema_path).scale == 3.0

    def test_an_unset_mode_field_inherits_the_base(self, extra_schema_path):
        config = {"customization": {
            "score_text": {"visible": False},
            "modes": {"live": {"score_text": {"align": "right"}}}}}
        st = self._style(config, mode="live", schema=extra_schema_path)
        assert st.visible is False and st.align == "right"


class TestExtraFieldSchema:
    def _props(self):
        return expand_style_elements(
            EXTRA_SCHEMA)["properties"]["customization"]["properties"]

    def test_only_declared_subfields_are_emitted(self):
        """home_logo declares no font, so it gets no font control."""
        assert list(self._props()["home_logo"]["properties"]) == ["visible"]

    def test_a_text_element_gets_the_text_fields(self):
        assert list(self._props()["score_text"]["properties"]) == [
            "font", "font_size", "text_color", "visible", "align"]

    def test_scale_is_emitted_with_the_offsets(self):
        layout = self._props()["layout"]["properties"]
        assert list(layout["home_logo"]["properties"]) == [
            "x_offset", "y_offset", "scale"]
        assert list(layout["score_text"]["properties"]) == [
            "x_offset", "y_offset"], "scale is opt-in"

    def test_declared_scale_bounds_are_kept(self):
        scale = self._props()["layout"]["properties"]["home_logo"]["properties"]["scale"]
        assert (scale["minimum"], scale["maximum"]) == (0.25, 4)

    def test_align_offers_only_valid_choices(self):
        align = self._props()["score_text"]["properties"]["align"]
        assert align["enum"] == ["left", "center", "right"]

    def test_the_mode_copies_are_nullable(self):
        live = self._props()["modes"]["properties"]["live"]["properties"]
        assert live["score_text"]["properties"]["visible"]["default"] is None
        assert "null" in live["score_text"]["properties"]["visible"]["type"]
        scale = live["layout"]["properties"]["home_logo"]["properties"]["scale"]
        assert scale["default"] is None and "null" in scale["type"]

class TestAliasKeys:
    """Two naming conventions collided as the scoreboards grew.

    Counted across the published schemas: the style block names elements
    with a _text suffix (score_text, status_text), while the layout block
    mostly uses the bare noun (score, date, odds) -- except status_text,
    which kept the suffix in seven plugins and lost it in two. records vs
    record splits seven to two the same way.

    Renaming config keys to fix that would orphan whatever offsets users had
    already dialled in, so lookups try the alternatives instead.
    """

    @pytest.mark.parametrize("key,expected", [
        ("score_text", ("score_text", "score")),
        ("status", ("status", "status_text")),
        ("status_text", ("status_text", "status")),
        ("records", ("records", "record")),
        ("record", ("record", "records")),
        ("rank_text", ("rank_text", "ranking", "rank")),
        ("team_name", ("team_name", "team")),
    ])
    def test_the_spellings_tried(self, key, expected):
        assert alias_keys(key) == expected

    def test_the_exact_name_is_always_first(self):
        for key in ("score_text", "status", "records", "home_logo"):
            assert alias_keys(key)[0] == key

    def test_an_explicit_entry_replaces_the_suffix_rule(self):
        """'records' must not also generate the meaningless 'records_text'."""
        assert "records_text" not in alias_keys("records")

    @pytest.mark.parametrize("key", ["", None, 5, [], {}])
    def test_nonsense_keys_yield_nothing(self, key):
        assert alias_keys(key) == ()


class TestAliasedLookup:
    def _r(self, config, mode=None):
        return ElementStyleResolver(config, {}, mode=mode)

    def test_a_compact_plugin_finds_offsets_saved_under_the_bare_noun(self):
        """The migration case: a scoreboard moving to the compact form asks
        for score_text offsets, and its users wrote layout.score."""
        config = {"customization": {"layout": {"score": {"y_offset": -3}}}}
        assert self._r(config).offset("score_text") == (0, -3)

    def test_status_and_status_text_find_each_other(self):
        written_long = {"customization": {"layout": {"status_text": {"x_offset": 2}}}}
        written_short = {"customization": {"layout": {"status": {"x_offset": 4}}}}
        assert self._r(written_long).offset("status") == (2, 0)
        assert self._r(written_short).offset("status_text") == (4, 0)

    def test_record_and_records_find_each_other(self):
        config = {"customization": {"layout": {"records": {"away_x_offset": 5}}}}
        assert self._r(config).offset_value("record", "away_x_offset") == 5

    def test_an_exact_match_beats_an_alias(self):
        """Nothing changes for a config that already uses the right name."""
        config = {"customization": {"layout": {
            "score": {"y_offset": 1}, "score_text": {"y_offset": 9}}}}
        assert self._r(config).offset("score_text") == (0, 9)
        assert self._r(config).offset("score") == (0, 1)

    def test_style_blocks_alias_too(self, style_schema_path):
        config = {"customization": {"title": {"font_size": 13}}}
        st = ElementStyleResolver(
            config, defaults_from_schema_file(style_schema_path)
        ).style("title_text", classic_size=8)
        assert st.font_size == 13

    def test_a_mode_block_aliases_too(self):
        config = {"customization": {
            "layout": {"score": {"y_offset": -3}},
            "modes": {"live": {"layout": {"score": {"y_offset": 7}}}}}}
        assert self._r(config, "live").offset("score_text") == (0, 7)

    def test_an_unrelated_element_is_unaffected(self):
        config = {"customization": {"layout": {"home_logo": {"x_offset": 3}}}}
        r = self._r(config)
        assert r.offset("home_logo") == (3, 0)
        assert r.offset("away_logo") == (0, 0)

    def test_scale_is_found_through_an_alias(self, extra_schema_path):
        config = {"customization": {"layout": {"score": {"scale": 2}}}}
        st = ElementStyleResolver(
            config, defaults_from_schema_file(extra_schema_path)
        ).style("score_text")
        assert st.scale == 2.0

HANDWRITTEN = {
    "type": "object",
    "properties": {
        "customization": {
            "type": "object",
            "title": "Display Customization",
            "properties": {
                "score_text": {
                    "type": "object",
                    "title": "Game Score",
                    "properties": {
                        "font": {
                            "type": "string",
                            "enum": ["PressStart2P-Regular.ttf", "4x6-font.ttf",
                                     "5by7.regular.ttf", "5x7.bdf", "4x6.bdf"],
                            "default": "PressStart2P-Regular.ttf",
                        },
                        "font_size": {"type": "integer", "minimum": 4,
                                      "maximum": 16, "default": 10},
                        "text_color": {"type": "array", "minItems": 3,
                                       "maxItems": 3, "default": [255, 255, 255]},
                    },
                },
                "favorite_result_colors": {
                    "type": "object",
                    "properties": {
                        "enabled": {"type": "boolean", "default": False},
                        "win_color": {"type": "array", "default": [0, 255, 0]},
                    },
                },
                # baseball's 'count' shape: one field this system knows,
                # next to geometry that means nothing to it.
                "count": {
                    "type": "object",
                    "properties": {
                        "text_color": {"type": "array", "default": [0, 255, 0]},
                        "y_offset": {"type": "integer", "default": 2},
                    },
                },
                "layout": {
                    "type": "object",
                    "properties": {
                        "score": {"type": "object", "properties": {
                            "x_offset": {"type": "integer", "default": 0},
                            "y_offset": {"type": "integer", "default": 0}}},
                        "home_logo": {"type": "object", "properties": {
                            "x_offset": {"type": "integer", "default": 0}}},
                    },
                },
            },
        },
    },
}


class TestHandWrittenAdoption:
    """Nineteen plugins spell their style elements out longhand -- football's
    block is 701 lines for seven elements -- and predate this system. Core
    recognises that shape so they pick up the editor and the real font picker
    on a core update rather than on a plugin release.
    """

    def _customization(self, schema=None):
        return expand_style_elements(
            schema or HANDWRITTEN)["properties"]["customization"]

    def test_the_block_gets_the_composite_editor(self):
        assert self._customization()["x-widget"] == "style-editor"

    def test_the_declared_order_is_stated_explicitly(self):
        """Flask's JSON provider sorts keys, so without this the elements
        reach the browser alphabetised."""
        order = self._customization()["x-propertyOrder"]
        assert order[0] == "score_text"

    def test_a_block_that_is_not_styling_is_left_alone(self):
        """favorite_result_colors, baseball's bases/outs/player_card and the
        stocks blocks all carry fields this system knows nothing about."""
        block = self._customization()["properties"]["favorite_result_colors"]
        assert "x-style-managed" not in block
        assert block["properties"]["win_color"]["default"] == [0, 255, 0]

    def test_a_block_that_merely_shares_a_field_is_left_alone(self):
        """The reason detection requires *every* field to be one this system
        understands. baseball's 'count' carries a text_color beside a
        y_offset that means nothing here.

        Asserted on the element list rather than on the block itself: an
        over-eager rule leaves a fontless block looking untouched, and only
        shows up as an extra row in the editor and an extra per-mode
        override group.
        """
        schema = copy.deepcopy(HANDWRITTEN)
        schema["properties"]["customization"]["x-style-modes"] = ["live"]
        live = expand_style_elements(schema)["properties"]["customization"][
            "properties"]["modes"]["properties"]["live"]["properties"]
        assert "score_text" in live
        assert "count" not in live, (
            "'count' is not a style element -- it shares one field and "
            "carries geometry this system does not understand")
        assert "favorite_result_colors" not in live

    def test_the_hardcoded_font_list_is_replaced_by_the_picker(self):
        """The reason an uploaded font could never appear in one of these."""
        font = self._customization()["properties"]["score_text"]["properties"]["font"]
        assert "enum" not in font
        assert font["x-widget"] == "font-selector"

    def test_the_picker_inherits_the_declared_size_ceiling(self):
        """A bitmap font ignores font_size and renders at its own baked-in
        size, so a field capped at 16 must not offer a 27px face."""
        font = self._customization()["properties"]["score_text"]["properties"]["font"]
        assert font["x-options"]["maxFixedSize"] == 16

    def test_the_users_existing_font_choice_stays_valid(self):
        font = self._customization()["properties"]["score_text"]["properties"]["font"]
        assert font["default"] == "PressStart2P-Regular.ttf"
        assert font["type"] == "string"

    def test_the_input_schema_is_not_mutated(self):
        expand_style_elements(HANDWRITTEN)
        original = HANDWRITTEN["properties"]["customization"]
        assert "x-widget" not in original
        assert "enum" in original["properties"]["score_text"]["properties"]["font"]

    def test_no_style_blocks_means_no_expansion(self):
        schema = {"properties": {"customization": {"type": "object",
            "properties": {"favorite_result_colors": {"type": "object",
                "properties": {"win_color": {"type": "array"}}}}}}}
        assert expand_style_elements(schema) is schema


class TestAdoptedModes:
    """Per-mode overrides stay opt-in: core cannot invent a plugin's list of
    display modes. Declaring x-style-modes is the one line that unlocks them
    for a hand-written block."""

    def _live(self):
        schema = copy.deepcopy(HANDWRITTEN)
        schema["properties"]["customization"]["x-style-modes"] = ["live", "recent"]
        return expand_style_elements(schema)["properties"]["customization"][
            "properties"]["modes"]["properties"]["live"]["properties"]

    def test_modes_are_not_invented(self):
        assert "modes" not in self._customization_props()

    def _customization_props(self):
        return expand_style_elements(HANDWRITTEN)["properties"]["customization"]["properties"]

    def test_declaring_modes_generates_them(self):
        assert "score_text" in self._live()

    def test_mode_fields_are_nullable(self):
        size = self._live()["score_text"]["properties"]["font_size"]
        assert size["default"] is None and "null" in size["type"]

    def test_every_positionable_element_gets_per_mode_offsets(self):
        """The two namespaces do not line up in a hand-written schema: this
        one styles 'score_text' but positions 'score', and positions a logo
        that has no style block at all. Keying the layout off the style
        elements would have left both without a per-mode offset."""
        layout = self._live()["layout"]["properties"]
        assert set(layout) == {"score", "home_logo"}

    def test_the_mode_font_picker_keeps_the_ceiling(self):
        font = self._live()["score_text"]["properties"]["font"]
        assert font["x-options"]["maxFixedSize"] == 16
        assert "null" in font["type"]
