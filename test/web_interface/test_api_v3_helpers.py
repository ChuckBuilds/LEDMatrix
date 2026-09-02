"""
Unit tests for the module-level helper functions in
web_interface/blueprints/api_v3.py.

These helpers back the plugin config save endpoint (the largest function in
the repo) and the store's update-available detection, but were previously
exercised only indirectly through full Flask route tests. Testing them
directly pins behavior that the routes rely on — including a few
characterized quirks marked below.
"""

import sys
from pathlib import Path
from typing import Any, ClassVar, Dict

import pytest

project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

from web_interface.blueprints.api_v3 import (  # noqa: E402
    _is_plugin_update_available,
    _coerce_to_bool,
    deep_merge,
    _parse_form_value,
    _get_schema_property,
    _set_nested_value,
)


class TestIsPluginUpdateAvailable:
    def test_equal_versions_no_update(self):
        assert _is_plugin_update_available("1.2.0", "1.2.0") is False

    def test_newer_registry_version_needs_update(self):
        assert _is_plugin_update_available("1.2.0", "1.3.0") is True

    def test_installed_ahead_of_registry_no_update(self):
        # A locally modified plugin ahead of the registry must not be
        # flagged — this is the whole point of semantic comparison here.
        assert _is_plugin_update_available("2.0.0", "1.9.0") is False

    def test_empty_versions_no_update(self):
        assert _is_plugin_update_available("", "1.0.0") is False
        assert _is_plugin_update_available("1.0.0", "") is False
        assert _is_plugin_update_available("", "") is False

    def test_v_prefix_parses_as_equal(self):
        # packaging.version treats "v1.2.0" == "1.2.0" (PEP 440 tolerates the
        # prefix), so no update is flagged. Contrast with store_manager's
        # string-equality check — see test_version_comparison_consistency.py.
        assert _is_plugin_update_available("v1.2.0", "1.2.0") is False

    def test_two_part_version_parses_as_equal(self):
        assert _is_plugin_update_available("1.2", "1.2.0") is False

    def test_unparseable_version_surfaces_mismatch(self):
        # Direction unknowable → surface the difference rather than hide a
        # potential update.
        assert _is_plugin_update_available("abc.def", "1.0.0") is True

    def test_prerelease_below_release(self):
        assert _is_plugin_update_available("1.2.0-rc1", "1.2.0") is True


class TestCoerceToBool:
    @pytest.mark.parametrize("value", ["true", "TRUE", "on", "1", "yes", "YES"])
    def test_truthy_strings(self, value):
        assert _coerce_to_bool(value) is True

    @pytest.mark.parametrize("value", ["false", "off", "0", "no", "", "banana"])
    def test_falsey_strings(self, value):
        assert _coerce_to_bool(value) is False

    def test_none_is_false(self):
        assert _coerce_to_bool(None) is False

    def test_bools_pass_through(self):
        assert _coerce_to_bool(True) is True
        assert _coerce_to_bool(False) is False

    def test_int_only_one_is_true(self):
        # Characterized quirk: ints coerce via `value == 1`, so 2 (truthy in
        # Python) is False here.
        assert _coerce_to_bool(1) is True
        assert _coerce_to_bool(2) is False
        assert _coerce_to_bool(0) is False

    def test_other_types_false(self):
        assert _coerce_to_bool([1]) is False
        assert _coerce_to_bool({"a": 1}) is False


class TestDeepMerge:
    def test_nested_dicts_merge_recursively(self):
        base = {"a": {"x": 1, "y": 2}, "b": 1}
        update = {"a": {"y": 3, "z": 4}}
        assert deep_merge(base, update) == {"a": {"x": 1, "y": 3, "z": 4}, "b": 1}

    def test_scalar_over_dict_replaces(self):
        assert deep_merge({"a": {"x": 1}}, {"a": 5}) == {"a": 5}

    def test_dict_over_scalar_replaces(self):
        assert deep_merge({"a": 5}, {"a": {"x": 1}}) == {"a": {"x": 1}}

    def test_lists_replaced_wholesale(self):
        assert deep_merge({"a": [1, 2]}, {"a": [3]}) == {"a": [3]}

    def test_top_level_not_mutated_but_shallow_copy(self):
        # Characterized: result = base.copy() protects base's top level, but
        # nested dicts NOT touched by the update are shared by reference.
        base = {"a": {"x": 1}, "keep": {"y": 2}}
        result = deep_merge(base, {"a": {"x": 9}})
        assert base == {"a": {"x": 1}, "keep": {"y": 2}}  # base unchanged
        assert result["keep"] is base["keep"]  # untouched subtree is shared


class TestParseFormValue:
    def test_boolean_strings(self):
        assert _parse_form_value("true") is True
        assert _parse_form_value("False") is False

    def test_null_like_strings(self):
        assert _parse_form_value("null") is None
        assert _parse_form_value("none") is None
        assert _parse_form_value("") is None

    def test_none_passthrough(self):
        assert _parse_form_value(None) is None

    def test_numbers(self):
        assert _parse_form_value("42") == 42
        assert isinstance(_parse_form_value("42"), int)
        assert _parse_form_value("3.5") == 3.5
        assert isinstance(_parse_form_value("3.5"), float)

    def test_json_array_parsed_before_numbers(self):
        # RGB arrays like "[255, 0, 0]" must come back as lists.
        assert _parse_form_value("[255, 0, 0]") == [255, 0, 0]

    def test_json_object(self):
        assert _parse_form_value('{"a": 1}') == {"a": 1}

    def test_malformed_json_falls_back_to_string(self):
        assert _parse_form_value("[not json") == "[not json"

    def test_plain_string_returned_unstripped(self):
        # The original value (not the stripped copy) is returned.
        assert _parse_form_value("  hello  ") == "  hello  "

    def test_non_string_passthrough(self):
        assert _parse_form_value(7) == 7
        assert _parse_form_value([1, 2]) == [1, 2]


class TestGetSchemaProperty:
    SCHEMA: ClassVar[Dict[str, Any]] = {
        "properties": {
            "brightness": {"type": "integer"},
            "customization": {
                "type": "object",
                "properties": {
                    "time_text": {
                        "type": "object",
                        "properties": {"font": {"type": "string"}},
                    },
                },
            },
            "fifa.world": {"type": "object",
                           "properties": {"enabled": {"type": "boolean"}}},
        }
    }

    def test_top_level_lookup(self):
        assert _get_schema_property(self.SCHEMA, "brightness") == {"type": "integer"}

    def test_nested_dot_path(self):
        prop = _get_schema_property(self.SCHEMA, "customization.time_text.font")
        assert prop == {"type": "string"}

    def test_dotted_schema_key_matched_longest_first(self):
        # League keys like "fifa.world" contain a literal dot and must match
        # as a single key, not be split into nested fifa -> world lookups.
        prop = _get_schema_property(self.SCHEMA, "fifa.world.enabled")
        assert prop == {"type": "boolean"}

    def test_missing_path_returns_none(self):
        assert _get_schema_property(self.SCHEMA, "nope.nope") is None

    def test_no_properties_returns_none(self):
        assert _get_schema_property({}, "a") is None
        assert _get_schema_property(None, "a") is None


class TestSetNestedValue:
    def test_sets_top_level(self):
        config = {}
        _set_nested_value(config, "brightness", 80)
        assert config == {"brightness": 80}

    def test_creates_intermediate_dicts(self):
        config = {}
        _set_nested_value(config, "customization.time_text.font", "5x7")
        assert config == {"customization": {"time_text": {"font": "5x7"}}}

    def test_merges_into_existing_nested_dict(self):
        config = {"customization": {"color": "red"}}
        _set_nested_value(config, "customization.font", "5x7")
        assert config == {"customization": {"color": "red", "font": "5x7"}}

    def test_scalar_intermediate_replaced_with_dict(self):
        # Characterized: a non-dict intermediate is silently replaced.
        config = {"customization": "oops"}
        _set_nested_value(config, "customization.font", "5x7")
        assert config == {"customization": {"font": "5x7"}}

    def test_existing_dotted_key_preserved(self):
        # An existing literal "fifa.world" key must be updated in place, not
        # exploded into nested {"fifa": {"world": ...}}.
        config = {"fifa.world": {"enabled": False}}
        _set_nested_value(config, "fifa.world.enabled", True)
        assert config == {"fifa.world": {"enabled": True}}

    def test_none_does_not_overwrite_existing(self):
        config = {"a": 1}
        _set_nested_value(config, "a", None)
        assert config == {"a": 1}

    def test_none_sets_missing_key(self):
        config = {}
        _set_nested_value(config, "a", None)
        assert config == {"a": None}
