"""
Tests for src/web_interface/secret_helpers.py — the canonical secret
identification / separation / masking helpers.

This module is the extracted single source of truth for x-secret handling,
but until now had zero test coverage (only ``mask_secret_fields`` is even
imported by production code, from pages_v3). api_v3.py still carries three
inline re-implementations of ``find_secret_fields``/``separate_secrets`` —
see test_secret_separation_parity.py — so pinning the canonical behavior
here is a precondition for ever migrating those copies.
"""

import copy

from src.web_interface.secret_helpers import (
    find_secret_fields,
    separate_secrets,
    mask_secret_fields,
    mask_all_secret_values,
    remove_empty_secrets,
)


SCHEMA_PROPS = {
    "api_key": {"type": "string", "x-secret": True},
    "city": {"type": "string"},
    "auth": {
        "type": "object",
        "properties": {
            "token": {"type": "string", "x-secret": True},
            "username": {"type": "string"},
        },
    },
    "accounts": {
        "type": "array",
        "items": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "token": {"type": "string", "x-secret": True},
            },
        },
    },
    "recovery_codes": {
        "type": "array",
        "items": {"type": "string", "x-secret": True},
    },
}


class TestFindSecretFields:
    def test_top_level_secret(self):
        assert "api_key" in find_secret_fields(SCHEMA_PROPS)

    def test_non_secret_not_included(self):
        assert "city" not in find_secret_fields(SCHEMA_PROPS)

    def test_nested_object_secret_uses_dot_path(self):
        assert "auth.token" in find_secret_fields(SCHEMA_PROPS)
        assert "auth.username" not in find_secret_fields(SCHEMA_PROPS)

    def test_array_item_object_secret_uses_bracket_path(self):
        assert "accounts[].token" in find_secret_fields(SCHEMA_PROPS)

    def test_array_of_secrets_uses_bracket_path(self):
        assert "recovery_codes[]" in find_secret_fields(SCHEMA_PROPS)

    def test_full_set(self):
        assert find_secret_fields(SCHEMA_PROPS) == {
            "api_key", "auth.token", "accounts[].token", "recovery_codes[]",
        }

    def test_non_dict_properties_tolerated(self):
        assert find_secret_fields({"weird": "not-a-dict"}) == set()

    def test_non_dict_input_returns_empty(self):
        assert find_secret_fields(None) == set()
        assert find_secret_fields([]) == set()


class TestSeparateSecrets:
    def test_flat_partition(self):
        regular, secrets = separate_secrets(
            {"api_key": "s3cret", "city": "Austin"}, {"api_key"})
        assert regular == {"city": "Austin"}
        assert secrets == {"api_key": "s3cret"}

    def test_nested_partition(self):
        config = {"auth": {"token": "t0k", "username": "chuck"}}
        regular, secrets = separate_secrets(config, {"auth.token"})
        assert regular == {"auth": {"username": "chuck"}}
        assert secrets == {"auth": {"token": "t0k"}}

    def test_empty_nested_dicts_pruned_from_regular(self):
        # A dict that is all secrets leaves nothing behind on the regular
        # side — the key must be dropped, not kept as {}.
        config = {"auth": {"token": "t0k"}}
        regular, secrets = separate_secrets(config, {"auth.token"})
        assert regular == {}
        assert secrets == {"auth": {"token": "t0k"}}

    def test_whole_array_secret(self):
        config = {"recovery_codes": ["a", "b"], "city": "Austin"}
        regular, secrets = separate_secrets(config, {"recovery_codes[]"})
        assert regular == {"city": "Austin"}
        assert secrets == {"recovery_codes": ["a", "b"]}

    def test_array_item_secrets_produce_parallel_lists(self):
        # Per-item secrets keep the arrays index-aligned so they can be
        # recombined: regular gets the stripped items, secrets a parallel
        # list of the extracted values.
        config = {"accounts": [
            {"name": "a", "token": "ta"},
            {"name": "b", "token": "tb"},
        ]}
        regular, secrets = separate_secrets(config, {"accounts[].token"})
        assert regular == {"accounts": [{"name": "a"}, {"name": "b"}]}
        assert secrets == {"accounts": [{"token": "ta"}, {"token": "tb"}]}

    def test_array_item_non_dict_items_get_placeholder(self):
        config = {"accounts": [{"name": "a", "token": "ta"}, "oddball"]}
        regular, secrets = separate_secrets(config, {"accounts[].token"})
        assert regular == {"accounts": [{"name": "a"}, "oddball"]}
        assert secrets == {"accounts": [{"token": "ta"}, {}]}

    def test_array_without_secret_paths_stays_regular(self):
        config = {"teams": ["DAL", "HOU"]}
        regular, secrets = separate_secrets(config, {"api_key"})
        assert regular == {"teams": ["DAL", "HOU"]}
        assert secrets == {}

    def test_round_trip_loses_nothing(self):
        # separate + naive recombine must reconstruct the original config.
        config = {
            "api_key": "k",
            "city": "Austin",
            "auth": {"token": "t", "username": "chuck"},
            "recovery_codes": ["a", "b"],
        }
        paths = find_secret_fields(SCHEMA_PROPS)
        regular, secrets = separate_secrets(copy.deepcopy(config), paths)

        def recombine(reg, sec):
            out = copy.deepcopy(reg)
            for k, v in sec.items():
                if isinstance(v, dict) and isinstance(out.get(k), dict):
                    out[k] = recombine(out[k], v)
                else:
                    out[k] = v
            return out

        assert recombine(regular, secrets) == config


class TestMaskSecretFields:
    def test_masks_present_secret_to_empty_string(self):
        result = mask_secret_fields({"api_key": "s3cret"}, SCHEMA_PROPS)
        assert result["api_key"] == ""

    def test_leaves_non_secret_untouched(self):
        result = mask_secret_fields({"city": "Austin"}, SCHEMA_PROPS)
        assert result["city"] == "Austin"

    def test_none_and_empty_left_alone(self):
        result = mask_secret_fields({"api_key": None}, SCHEMA_PROPS)
        assert result["api_key"] is None
        result = mask_secret_fields({"api_key": ""}, SCHEMA_PROPS)
        assert result["api_key"] == ""

    def test_falsey_but_set_values_are_masked(self):
        # 0 and False are real values; the check is `is not None and != ''`.
        # Note False == '' is False in Python, so False IS masked; 0 == '' is
        # also False, so 0 is masked too.
        result = mask_secret_fields({"api_key": 0}, SCHEMA_PROPS)
        assert result["api_key"] == ""
        result = mask_secret_fields({"api_key": False}, SCHEMA_PROPS)
        assert result["api_key"] == ""

    def test_nested_object_masked_without_mutating_input(self):
        config = {"auth": {"token": "t0k", "username": "chuck"}}
        original = copy.deepcopy(config)
        result = mask_secret_fields(config, SCHEMA_PROPS)
        assert result["auth"]["token"] == ""
        assert result["auth"]["username"] == "chuck"
        assert config == original  # input not mutated

    def test_array_of_secrets_masked_elementwise(self):
        result = mask_secret_fields(
            {"recovery_codes": ["a", "b"]}, SCHEMA_PROPS)
        assert result["recovery_codes"] == ["", ""]

    def test_array_of_objects_masked_per_item(self):
        config = {"accounts": [{"name": "a", "token": "ta"}, "oddball"]}
        result = mask_secret_fields(config, SCHEMA_PROPS)
        assert result["accounts"][0] == {"name": "a", "token": ""}
        assert result["accounts"][1] == "oddball"

    def test_non_dict_schema_property_tolerated(self):
        assert mask_secret_fields({"x": 1}, {"x": "bogus"}) == {"x": 1}


class TestMaskAllSecretValues:
    def test_real_values_replaced_with_bullets(self):
        assert mask_all_secret_values({"key": "abc"}) == {"key": "••••••••"}

    def test_placeholders_preserved(self):
        # YOUR_* placeholders must survive so the UI can show "not set".
        result = mask_all_secret_values({"key": "YOUR_API_KEY_HERE"})
        assert result == {"key": "YOUR_API_KEY_HERE"}

    def test_empty_and_none_preserved(self):
        assert mask_all_secret_values({"a": "", "b": None}) == {"a": "", "b": None}

    def test_recurses_into_nested_dicts(self):
        result = mask_all_secret_values({"plugin": {"token": "t", "empty": ""}})
        assert result == {"plugin": {"token": "••••••••", "empty": ""}}

    def test_non_string_real_values_masked(self):
        assert mask_all_secret_values({"port": 8080}) == {"port": "••••••••"}


class TestRemoveEmptySecrets:
    def test_strips_empty_string(self):
        assert remove_empty_secrets({"a": "", "b": "real"}) == {"b": "real"}

    def test_strips_whitespace_only(self):
        assert remove_empty_secrets({"a": "   "}) == {}

    def test_strips_none(self):
        assert remove_empty_secrets({"a": None}) == {}

    def test_prunes_empty_nested_dicts(self):
        assert remove_empty_secrets({"plugin": {"token": ""}}) == {}

    def test_keeps_nested_real_values(self):
        result = remove_empty_secrets({"plugin": {"token": "t", "empty": ""}})
        assert result == {"plugin": {"token": "t"}}

    def test_keeps_falsey_non_string_values(self):
        # 0 and False are neither None nor blank strings — they are kept.
        assert remove_empty_secrets({"a": 0, "b": False}) == {"a": 0, "b": False}
