"""
Tests for SchemaManager.merge_with_defaults — the merge every plugin config
passes through at load time (schema defaults + user config, with None
replacement). A regression here silently changes every plugin's effective
config, so the exact branch behavior is pinned, including the
characterized type-mismatch cases.
"""

import pytest

from src.plugin_system.schema_manager import SchemaManager


@pytest.fixture
def sm(tmp_path):
    return SchemaManager(plugins_dir=str(tmp_path))


class TestBasicMerge:
    def test_missing_keys_filled_from_defaults(self, sm):
        merged = sm.merge_with_defaults(
            {"city": "Austin"}, {"city": "NYC", "units": "metric"})
        assert merged == {"city": "Austin", "units": "metric"}

    def test_present_keys_preserved(self, sm):
        merged = sm.merge_with_defaults({"enabled": False}, {"enabled": True})
        assert merged["enabled"] is False

    def test_nested_three_level_merge(self, sm):
        config = {"a": {"b": {"c": 1}}}
        defaults = {"a": {"b": {"c": 0, "d": 2}, "e": 3}}
        merged = sm.merge_with_defaults(config, defaults)
        assert merged == {"a": {"b": {"c": 1, "d": 2}, "e": 3}}

    def test_inputs_not_mutated(self, sm):
        config = {"a": {"b": 1}}
        defaults = {"a": {"b": 0, "c": 2}, "d": 3}
        sm.merge_with_defaults(config, defaults)
        assert config == {"a": {"b": 1}}
        assert defaults == {"a": {"b": 0, "c": 2}, "d": 3}

    def test_merged_values_are_copies_not_aliases(self, sm):
        config = {"teams": ["DAL"]}
        merged = sm.merge_with_defaults(config, {"teams": []})
        merged["teams"].append("HOU")
        assert config["teams"] == ["DAL"]  # user's list untouched


class TestNoneReplacement:
    def test_none_replaced_by_default(self, sm):
        merged = sm.merge_with_defaults({"units": None}, {"units": "metric"})
        assert merged["units"] == "metric"

    def test_falsey_non_none_values_kept(self, sm):
        merged = sm.merge_with_defaults(
            {"enabled": False, "count": 0, "label": ""},
            {"enabled": True, "count": 5, "label": "x"},
        )
        assert merged == {"enabled": False, "count": 0, "label": ""}

    def test_nested_none_replaced(self, sm):
        merged = sm.merge_with_defaults(
            {"style": {"color": None}}, {"style": {"color": "red"}})
        assert merged["style"]["color"] == "red"

    def test_none_with_no_default_stays_none(self, sm):
        merged = sm.merge_with_defaults({"extra": None}, {})
        assert merged["extra"] is None

    def test_none_replaced_by_dict_default_is_a_copy(self, sm):
        defaults = {"style": {"color": "red"}}
        merged = sm.merge_with_defaults({"style": None}, defaults)
        assert merged["style"] == {"color": "red"}
        merged["style"]["color"] = "blue"
        assert defaults["style"]["color"] == "red"


class TestTypeMismatches:
    def test_user_scalar_over_dict_default_wins(self, sm):
        # Characterized: a scalar user value replaces a dict default outright.
        merged = sm.merge_with_defaults(
            {"style": "compact"}, {"style": {"color": "red"}})
        assert merged["style"] == "compact"

    def test_user_dict_over_scalar_default_wins(self, sm):
        merged = sm.merge_with_defaults(
            {"style": {"color": "red"}}, {"style": "compact"})
        assert merged["style"] == {"color": "red"}

    def test_arrays_replaced_wholesale_not_merged(self, sm):
        # Pinned contract: arrays never element-merge — the user's array is
        # the whole answer, even when shorter than the default.
        merged = sm.merge_with_defaults(
            {"teams": ["DAL"]}, {"teams": ["NYG", "PHI", "WAS"]})
        assert merged["teams"] == ["DAL"]

    def test_empty_user_array_beats_default(self, sm):
        merged = sm.merge_with_defaults({"teams": []}, {"teams": ["NYG"]})
        assert merged["teams"] == []

    def test_extra_user_keys_survive(self, sm):
        # Keys with no schema default pass through untouched.
        merged = sm.merge_with_defaults({"custom_flag": 7}, {"known": 1})
        assert merged == {"known": 1, "custom_flag": 7}
