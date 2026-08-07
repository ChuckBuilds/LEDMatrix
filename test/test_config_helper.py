"""
Tests for src/common/config_helper.py — pins the ConfigHelper contract.

Covers: load/save round trips (missing/malformed files return {} rather
than raising, non-ASCII preserved via ensure_ascii=False, top-level JSON
lists returned as-is), dot-notation get/set including the silent-failure
contract when an intermediate key holds a non-dict, merge_configs deep
semantics with NO aliasing of the base config (the fixed bug — the old
shallow copy let mutations of the merged result leak into base's nested
dicts), simplified schema validation including the caught-TypeError path
when a schema 'type' is given as a string, plugin config key conventions
('{plugin_id}_config', enabled defaults True), and required-key checks
where a key present with value None counts as present.
"""

import json

import pytest

from src.common.config_helper import ConfigHelper


@pytest.fixture
def helper():
    return ConfigHelper()


class TestLoadConfig:
    def test_missing_file_returns_empty_dict(self, helper, tmp_path):
        assert helper.load_config(tmp_path / "nope.json") == {}

    def test_malformed_json_returns_empty_dict(self, helper, tmp_path):
        path = tmp_path / "bad.json"
        path.write_text("{ this is not json", encoding="utf-8")
        assert helper.load_config(path) == {}

    def test_top_level_list_returned_as_is(self, helper, tmp_path):
        # load_config does not enforce a dict shape: a JSON list comes
        # straight back. Pinned as a characterization of current behavior.
        path = tmp_path / "list.json"
        path.write_text("[1, 2, 3]", encoding="utf-8")
        assert helper.load_config(path) == [1, 2, 3]


class TestSaveConfig:
    def test_round_trip(self, helper, tmp_path):
        path = tmp_path / "config.json"
        config = {'display': {'hardware': {'rows': 32}}, 'timezone': 'UTC'}
        assert helper.save_config(config, path) is True
        assert helper.load_config(path) == config

    def test_creates_parent_directories(self, helper, tmp_path):
        path = tmp_path / "deep" / "nested" / "config.json"
        assert helper.save_config({'a': 1}, path) is True
        assert path.exists()
        assert helper.load_config(path) == {'a': 1}

    def test_non_ascii_survives_round_trip(self, helper, tmp_path):
        path = tmp_path / "config.json"
        config = {'city': 'Zürich', 'note': 'météo ☀'}
        assert helper.save_config(config, path) is True
        assert helper.load_config(path) == config
        # ensure_ascii=False: characters are written raw, not \u-escaped
        assert 'Zürich' in path.read_text(encoding='utf-8')

    def test_directory_path_returns_false_not_raise(self, helper, tmp_path):
        assert helper.save_config({'a': 1}, tmp_path) is False


class TestGetConfigValue:
    def test_dot_notation_hit(self, helper):
        config = {'display': {'hardware': {'rows': 32}}}
        assert helper.get_config_value(config, 'display.hardware.rows') == 32

    def test_missing_returns_default(self, helper):
        sentinel = object()
        assert helper.get_config_value({}, 'display.rows', default=sentinel) is sentinel

    def test_intermediate_non_dict_returns_default(self, helper):
        config = {'display': 'not-a-dict'}
        assert helper.get_config_value(config, 'display.hardware.rows', default=64) == 64

    def test_required_missing_raises_keyerror(self, helper):
        with pytest.raises(KeyError):
            helper.get_config_value({}, 'display.rows', required=True)


class TestSetConfigValue:
    def test_sets_top_level(self, helper):
        config = {}
        helper.set_config_value(config, 'timezone', 'UTC')
        assert config == {'timezone': 'UTC'}

    def test_auto_creates_intermediates(self, helper):
        config = {}
        helper.set_config_value(config, 'display.hardware.rows', 32)
        assert config == {'display': {'hardware': {'rows': 32}}}

    def test_silent_failure_on_non_dict_intermediate(self, helper):
        # 'a' exists but holds an int; the assignment attempt raises
        # TypeError internally, which set_config_value swallows and logs.
        # The config is left unchanged — pinned silent-failure contract.
        config = {'a': 5}
        helper.set_config_value(config, 'a.b', 1)
        assert config == {'a': 5}


class TestMergeConfigs:
    def test_nested_dicts_merge_recursively(self, helper):
        base = {'display': {'rows': 32, 'cols': 64}, 'timezone': 'UTC'}
        override = {'display': {'cols': 128, 'brightness': 90}}
        merged = helper.merge_configs(base, override)
        assert merged == {
            'display': {'rows': 32, 'cols': 128, 'brightness': 90},
            'timezone': 'UTC',
        }

    def test_scalar_override_wins_over_dict(self, helper):
        merged = helper.merge_configs({'display': {'rows': 32}}, {'display': 7})
        assert merged['display'] == 7

    def test_dict_override_wins_over_scalar(self, helper):
        merged = helper.merge_configs({'display': 7}, {'display': {'rows': 32}})
        assert merged['display'] == {'rows': 32}

    def test_no_aliasing_of_base(self, helper):
        # Post-fix: merge deep-copies base, so mutating the result never
        # leaks back into the caller's base config.
        base = {'display': {'x': 1}}
        merged = helper.merge_configs(base, {})
        assert merged['display'] is not base['display']
        merged['display']['x'] = 99
        assert base['display']['x'] == 1

    def test_inputs_unchanged(self, helper):
        base = {'a': {'b': 1}}
        override = {'a': {'c': 2}}
        helper.merge_configs(base, override)
        assert base == {'a': {'b': 1}}
        assert override == {'a': {'c': 2}}

    def test_no_aliasing_of_override_values(self, helper):
        # The non-recursive branch must deep-copy the override value too:
        # mutating a merged-in list or dict must not reach back into
        # override_config.
        override = {'teams': ['A', 'B'], 'nested': {'x': [1]}}
        merged = helper.merge_configs({}, override)
        merged['teams'].append('C')
        merged['nested']['x'].append(2)
        assert override == {'teams': ['A', 'B'], 'nested': {'x': [1]}}


class TestValidateConfig:
    def test_no_schema_dict_is_valid(self, helper):
        assert helper.validate_config({'a': 1}) is True

    def test_no_schema_list_is_invalid(self, helper):
        assert helper.validate_config([1, 2]) is False

    def test_required_key_missing_is_invalid(self, helper):
        schema = {'rows': {'required': True, 'type': int}}
        assert helper.validate_config({}, schema) is False

    def test_optional_key_missing_is_valid(self, helper):
        schema = {'rows': {'required': False, 'type': int}}
        assert helper.validate_config({}, schema) is True

    def test_wrong_type_is_invalid(self, helper):
        schema = {'rows': {'type': int}}
        assert helper.validate_config({'rows': 'thirty-two'}, schema) is False
        assert helper.validate_config({'rows': 32}, schema) is True

    def test_allowed_values_violation_is_invalid(self, helper):
        schema = {'mode': {'allowed_values': ['clock', 'weather']}}
        assert helper.validate_config({'mode': 'stocks'}, schema) is False
        assert helper.validate_config({'mode': 'clock'}, schema) is True

    def test_string_type_in_schema_is_invalid_via_typeerror(self, helper):
        # 'type' given as the STRING "int" makes isinstance() raise
        # TypeError; validate_config catches it and returns False rather
        # than raising. Pinned characterization.
        schema = {'rows': {'type': 'int'}}
        assert helper.validate_config({'rows': 32}, schema) is False


class TestPluginConfigHelpers:
    def test_get_plugin_config_uses_suffixed_key(self, helper):
        plugin_cfg = {'enabled': True, 'display_duration': 30}
        assert helper.get_plugin_config({'clock_config': plugin_cfg}, 'clock') == plugin_cfg

    def test_get_plugin_config_bare_id_key_not_found(self, helper):
        # Only '{plugin_id}_config' is consulted — a bare 'clock' section
        # is invisible to this helper. Pinned key contract.
        assert helper.get_plugin_config({'clock': {'enabled': True}}, 'clock') == {}

    def test_create_default_config_wraps_in_suffixed_key(self, helper):
        defaults = {'enabled': True}
        assert helper.create_default_config('clock', defaults) == {'clock_config': defaults}

    def test_is_plugin_enabled_defaults_true_for_unknown(self, helper):
        assert helper.is_plugin_enabled({}, 'clock') is True

    def test_is_plugin_enabled_false_when_disabled(self, helper):
        config = {'clock_config': {'enabled': False}}
        assert helper.is_plugin_enabled(config, 'clock') is False

    def test_is_plugin_enabled_ignores_bare_id_key(self, helper):
        # Disabled under the wrong key -> still reported enabled (default).
        config = {'clock': {'enabled': False}}
        assert helper.is_plugin_enabled(config, 'clock') is True


class TestSportsAndDisplayHelpers:
    def test_get_display_config(self, helper):
        display = {'hardware': {'rows': 32}}
        assert helper.get_display_config({'display': display}) == display
        assert helper.get_display_config({}) == {}

    def test_get_sports_config_uses_scoreboard_suffix(self, helper):
        sport_cfg = {'favorite_teams': ['TB']}
        config = {'football_scoreboard': sport_cfg}
        assert helper.get_sports_config(config, 'football') == sport_cfg
        assert helper.get_sports_config(config, 'hockey') == {}

    def test_get_favorite_teams(self, helper):
        config = {'football_scoreboard': {'favorite_teams': ['TB', 'DAL']}}
        assert helper.get_favorite_teams(config, 'football') == ['TB', 'DAL']
        assert helper.get_favorite_teams({}, 'football') == []

    def test_get_display_modes(self, helper):
        modes = {'live': True, 'recent': False}
        config = {'football_scoreboard': {'display_modes': modes}}
        assert helper.get_display_modes(config, 'football') == modes
        assert helper.get_display_modes({}, 'football') == {}


class TestValidateRequiredKeys:
    def test_returns_missing_subset(self, helper):
        config = {'a': 1, 'c': {'d': 2}}
        missing = helper.validate_required_keys(config, ['a', 'b', 'c.d', 'c.e'])
        assert missing == ['b', 'c.e']

    def test_dot_notation_present(self, helper):
        config = {'display': {'hardware': {'rows': 32}}}
        assert helper.validate_required_keys(config, ['display.hardware.rows']) == []

    def test_empty_requirements(self, helper):
        assert helper.validate_required_keys({'a': 1}, []) == []

    def test_present_with_none_counts_as_present(self, helper):
        # _has_key checks key membership, not truthiness — a key set to
        # None is NOT reported missing. Pinned semantics.
        assert helper.validate_required_keys({'a': None}, ['a']) == []
