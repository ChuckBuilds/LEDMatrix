"""Tests for x-style-elements schema expansion.

A plugin declares styleable elements once, compactly; expansion generates
the full customization property blocks at schema-load time. The invariants
that matter:

- idempotent (expand(expand(s)) == expand(s)) and the input is never mutated
- both load paths (cached GET, uncached save) see the identical shape
- generated defaults flow into generate_default_config, and saving twice is
  round-trip stable (merge_with_defaults produces no churn)
- hand-written property blocks for the same element always win
- defaults_from_schema_file (what plugins use to build resolvers from their
  RAW schema file) agrees exactly with the schema manager's expanded view
"""

import copy
import json
import os
import sys

import jsonschema
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.element_style import (  # noqa: E402
    defaults_from_schema_file,
    expand_style_elements,
    extract_schema_defaults,
    get_style_elements,
)
from src.plugin_system.schema_manager import SchemaManager  # noqa: E402

PRESS_START = "PressStart2P-Regular.ttf"


def _declared_schema():
    return {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "type": "object",
        "properties": {
            "enabled": {"type": "boolean", "default": True},
            "customization": {
                "type": "object",
                "title": "Display Customization",
                "x-style-elements": {
                    "score_text": {
                        "title": "Game Score",
                        "font": {"default": PRESS_START},
                        "size": {"default": 10, "min": 4, "max": 16},
                        "color": True,
                        "offsets": True,
                    },
                    "detail_text": {
                        "font": {"default": "4x6-font.ttf"},
                        "size": {"default": 6},
                    },
                },
                "properties": {},
                "additionalProperties": False,
            },
        },
    }


class TestExpansionShape:
    def test_generates_element_blocks(self):
        expanded = expand_style_elements(_declared_schema())
        cust = expanded["properties"]["customization"]["properties"]
        score = cust["score_text"]
        assert score["x-style-managed"] is True
        assert score["title"] == "Game Score"
        assert score["properties"]["font"]["default"] == PRESS_START
        assert score["properties"]["font"]["x-widget"] == "font-selector"
        assert score["properties"]["font_size"]["default"] == 10
        assert score["properties"]["font_size"]["minimum"] == 4
        assert score["properties"]["font_size"]["maximum"] == 16
        assert score["properties"]["text_color"]["x-widget"] == "color-picker"
        assert score["properties"]["text_color"]["default"] == [255, 255, 255]
        assert score["additionalProperties"] is False

    def test_color_and_offsets_are_optional(self):
        expanded = expand_style_elements(_declared_schema())
        cust = expanded["properties"]["customization"]["properties"]
        detail = cust["detail_text"]
        assert "text_color" not in detail["properties"]
        assert detail["title"] == "Detail Text"  # prettified from the key
        layout = cust["layout"]["properties"]
        assert "score_text" in layout
        assert "detail_text" not in layout

    def test_offsets_block_shape(self):
        expanded = expand_style_elements(_declared_schema())
        layout = expanded["properties"]["customization"]["properties"]["layout"]
        assert layout["x-style-managed"] is True
        entry = layout["properties"]["score_text"]
        assert entry["properties"]["x_offset"]["default"] == 0
        assert entry["properties"]["y_offset"]["default"] == 0

    def test_declared_color_default(self):
        schema = _declared_schema()
        decl = schema["properties"]["customization"]["x-style-elements"]
        decl["score_text"]["color"] = {"default": [255, 215, 0]}
        expanded = expand_style_elements(schema)
        color = (expanded["properties"]["customization"]["properties"]
                 ["score_text"]["properties"]["text_color"])
        assert color["default"] == [255, 215, 0]

    def test_declaration_survives_expansion(self):
        """The declaration is the element registry for tooling — it must
        remain readable from the expanded schema."""
        expanded = expand_style_elements(_declared_schema())
        assert set(get_style_elements(expanded)) == {"score_text", "detail_text"}
        assert set(SchemaManager.get_style_elements(expanded)) == {
            "score_text", "detail_text"}

    def test_no_declaration_returns_same_object(self):
        schema = {"type": "object", "properties": {"enabled": {"default": True}}}
        assert expand_style_elements(schema) is schema

    def test_valid_draft7(self):
        jsonschema.Draft7Validator.check_schema(
            expand_style_elements(_declared_schema()))

    def test_property_order_updated_when_present(self):
        schema = _declared_schema()
        schema["properties"]["customization"]["x-propertyOrder"] = []
        expanded = expand_style_elements(schema)
        order = expanded["properties"]["customization"]["x-propertyOrder"]
        # generated elements before layout (the template only renders keys
        # in x-propertyOrder when one exists)
        assert set(order) == {"score_text", "detail_text", "layout"}
        assert order.index("score_text") < order.index("layout")

    def test_malformed_declaration_is_harmless(self):
        schema = _declared_schema()
        schema["properties"]["customization"]["x-style-elements"] = {
            "bad": "not a dict", "score_text": {"size": {"default": 10}}}
        expanded = expand_style_elements(schema)
        cust = expanded["properties"]["customization"]["properties"]
        assert "bad" not in cust
        assert "score_text" in cust


class TestExpansionInvariants:
    def test_idempotent(self):
        once = expand_style_elements(_declared_schema())
        twice = expand_style_elements(once)
        assert once == twice

    def test_input_never_mutated(self):
        schema = _declared_schema()
        snapshot = copy.deepcopy(schema)
        expand_style_elements(schema)
        assert schema == snapshot

    def test_hand_written_block_wins(self):
        schema = _declared_schema()
        hand_written = {
            "type": "object",
            "properties": {"font": {"type": "string", "default": "custom.ttf"}},
        }
        schema["properties"]["customization"]["properties"]["score_text"] = \
            copy.deepcopy(hand_written)
        expanded = expand_style_elements(schema)
        assert (expanded["properties"]["customization"]["properties"]["score_text"]
                == hand_written)

    def test_hand_written_layout_entry_wins(self):
        schema = _declared_schema()
        schema["properties"]["customization"]["properties"]["layout"] = {
            "type": "object",
            "properties": {"score_text": {"type": "object", "properties": {
                "x_offset": {"type": "integer", "default": 5}}}},
        }
        expanded = expand_style_elements(schema)
        layout = expanded["properties"]["customization"]["properties"]["layout"]
        assert layout["properties"]["score_text"]["properties"]["x_offset"]["default"] == 5


class TestSchemaManagerIntegration:
    def _manager_with_schema(self, tmp_path, schema):
        plugin_dir = tmp_path / "test-plugin"
        plugin_dir.mkdir()
        (plugin_dir / "config_schema.json").write_text(json.dumps(schema))
        return SchemaManager(plugins_dir=tmp_path)

    def test_load_schema_expands(self, tmp_path):
        mgr = self._manager_with_schema(tmp_path, _declared_schema())
        loaded = mgr.load_schema("test-plugin")
        assert "score_text" in loaded["properties"]["customization"]["properties"]

    def test_cached_and_uncached_loads_agree(self, tmp_path):
        """The save path uses use_cache=False while the form GET uses the
        cache — they must see the identical expanded shape."""
        mgr = self._manager_with_schema(tmp_path, _declared_schema())
        cached = mgr.load_schema("test-plugin", use_cache=True)
        again = mgr.load_schema("test-plugin", use_cache=True)
        uncached = mgr.load_schema("test-plugin", use_cache=False)
        assert cached == uncached == again

    def test_disk_file_untouched(self, tmp_path):
        schema = _declared_schema()
        mgr = self._manager_with_schema(tmp_path, schema)
        mgr.load_schema("test-plugin")
        on_disk = json.loads(
            (tmp_path / "test-plugin" / "config_schema.json").read_text())
        assert on_disk == schema
        assert "score_text" not in on_disk["properties"]["customization"]["properties"]

    def test_defaults_include_generated_elements(self, tmp_path):
        mgr = self._manager_with_schema(tmp_path, _declared_schema())
        defaults = mgr.generate_default_config("test-plugin")
        assert defaults["customization"]["score_text"]["font"] == PRESS_START
        assert defaults["customization"]["score_text"]["font_size"] == 10
        assert defaults["customization"]["score_text"]["text_color"] == [255, 255, 255]
        assert defaults["customization"]["layout"]["score_text"]["x_offset"] == 0

    def test_save_twice_is_round_trip_stable(self, tmp_path):
        """merge_with_defaults(merged, defaults) must be a fixed point —
        saving a config twice can't keep growing/altering it."""
        mgr = self._manager_with_schema(tmp_path, _declared_schema())
        defaults = mgr.generate_default_config("test-plugin")
        user_config = {"enabled": True, "customization": {
            "score_text": {"font_size": 14}}}
        merged_once = mgr.merge_with_defaults(user_config, defaults)
        merged_twice = mgr.merge_with_defaults(merged_once, defaults)
        assert merged_once == merged_twice
        assert merged_once["customization"]["score_text"]["font_size"] == 14


class TestResolverParity:
    def test_defaults_from_schema_file_matches_manager_view(self, tmp_path):
        """Plugins build resolvers from their RAW schema file; the web UI
        merges defaults from the EXPANDED schema. Both must produce the
        same defaults or override detection diverges between contexts."""
        schema = _declared_schema()
        plugin_dir = tmp_path / "test-plugin"
        plugin_dir.mkdir()
        schema_path = plugin_dir / "config_schema.json"
        schema_path.write_text(json.dumps(schema))

        mgr = SchemaManager(plugins_dir=tmp_path)
        manager_defaults = mgr.extract_defaults_from_schema(
            mgr.load_schema("test-plugin"))
        raw_file_defaults = defaults_from_schema_file(str(schema_path))
        assert raw_file_defaults == manager_defaults

    def test_resolver_treats_generated_defaults_as_untouched(self, tmp_path):
        """End to end: a config saved through the web UI (all generated
        defaults baked in) must not read as a user override, and the
        schema-default color must not clobber a classic color."""
        from src.element_style import ElementStyleResolver
        schema_path = tmp_path / "config_schema.json"
        schema_path.write_text(json.dumps(_declared_schema()))
        defaults = defaults_from_schema_file(str(schema_path))

        saved_config = {"enabled": True, "customization": {
            "score_text": {"font": PRESS_START, "font_size": 10,
                           "text_color": [255, 255, 255]},
            "layout": {"score_text": {"x_offset": 0, "y_offset": 0}},
        }}
        resolver = ElementStyleResolver(saved_config, defaults)
        style = resolver.style("score_text", classic_font=PRESS_START,
                               classic_size=10, classic_color=(255, 215, 0))
        assert not style.user_forced
        assert not style.user_forced_color
        assert style.color == (255, 215, 0)  # semantic classic color survives
        assert style.offset == (0, 0)


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
