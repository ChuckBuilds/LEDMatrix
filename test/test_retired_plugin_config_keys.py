"""``skin`` and ``skin_options`` outlive the skin system in stored configs.

The skin system was removed, but a config.json written while it existed can
carry ``skin`` / ``skin_options`` in any plugin section. They used to be core
plugin properties; now they are RETIRED_PLUGIN_KEYS, dropped where a section
is prepared or validated. Most plugin schemas set
``"additionalProperties": false``, so without that a device upgraded with such
a config would flag the plugin degraded on every start.

The web save paths are covered in
test/web_interface/test_plugin_config_json_saves.py (TestRetiredSkinKeys).
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from src.config_manager import ConfigManager
from src.plugin_system.plugin_manager import PluginManager
from src.plugin_system.schema_manager import (
    CORE_PLUGIN_PROPERTIES,
    RETIRED_PLUGIN_KEYS,
    SchemaManager,
    drop_retired_plugin_keys,
)

PLUGIN_ID = "scoreboard-shaped"

STRICT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "enabled": {"type": "boolean", "default": True},
        "favorite_teams": {"type": "array", "items": {"type": "string"},
                           "default": []},
    },
}

OLD_SECTION = {
    "enabled": True,
    "favorite_teams": ["TB"],
    "skin": {"live": "neon"},
    "skin_options": {"accent": "#ff0000"},
}


def _copy(value):
    return json.loads(json.dumps(value))


class TestDropRetiredPluginKeys:
    def test_they_are_no_longer_core_properties(self):
        assert RETIRED_PLUGIN_KEYS == {"skin", "skin_options"}
        assert RETIRED_PLUGIN_KEYS.isdisjoint(CORE_PLUGIN_PROPERTIES)

    def test_drops_both(self):
        assert drop_retired_plugin_keys(OLD_SECTION, STRICT_SCHEMA) == {
            "enabled": True, "favorite_teams": ["TB"]}

    def test_does_not_mutate_the_section(self):
        section = _copy(OLD_SECTION)
        drop_retired_plugin_keys(section, STRICT_SCHEMA)
        assert section == OLD_SECTION

    def test_returns_the_same_object_when_there_is_nothing_to_drop(self):
        section = {"enabled": True}
        assert drop_retired_plugin_keys(section, STRICT_SCHEMA) is section

    def test_a_plugin_that_declares_skin_keeps_it(self):
        schema = _copy(STRICT_SCHEMA)
        schema["properties"]["skin"] = {"type": "string"}
        assert drop_retired_plugin_keys(OLD_SECTION, schema) == {
            "enabled": True, "favorite_teams": ["TB"], "skin": {"live": "neon"}}

    def test_without_a_schema_nothing_is_dropped(self):
        assert drop_retired_plugin_keys(OLD_SECTION, None) is OLD_SECTION

    def test_tolerates_a_non_dict(self):
        assert drop_retired_plugin_keys(None, STRICT_SCHEMA) is None


@pytest.fixture
def schema_manager(tmp_path):
    plugin_dir = tmp_path / "plugins" / PLUGIN_ID
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "config_schema.json").write_text(json.dumps(STRICT_SCHEMA),
                                                   encoding="utf-8")
    (plugin_dir / "manifest.json").write_text(json.dumps({"id": PLUGIN_ID}),
                                              encoding="utf-8")
    return SchemaManager(plugins_dir=tmp_path / "plugins", project_root=tmp_path)


class TestValidation:
    def test_the_prepared_section_has_neither_and_validates(self, schema_manager):
        prepared = schema_manager.prepare_plugin_config(PLUGIN_ID, _copy(OLD_SECTION))
        assert "skin" not in prepared and "skin_options" not in prepared
        assert prepared["favorite_teams"] == ["TB"]
        assert schema_manager.validate_config_against_schema(
            prepared, STRICT_SCHEMA, PLUGIN_ID) == (True, [])

    def test_the_raw_section_validates_too(self, schema_manager):
        assert schema_manager.validate_config_against_schema(
            _copy(OLD_SECTION), STRICT_SCHEMA, PLUGIN_ID) == (True, [])

    def test_validate_all_plugin_configs_passes(self, schema_manager, tmp_path):
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({"timezone": "UTC", PLUGIN_ID: OLD_SECTION}),
                               encoding="utf-8")
        config_manager = ConfigManager(config_path=str(config_file),
                                       secrets_path=str(tmp_path / "secrets.json"))
        results = config_manager.validate_all_plugin_configs(schema_manager)
        assert results[PLUGIN_ID] == {"valid": True, "errors": []}

    def test_a_real_violation_is_still_reported(self, schema_manager):
        section = dict(OLD_SECTION, not_declared=1)
        valid, errors = schema_manager.validate_config_against_schema(
            section, STRICT_SCHEMA, PLUGIN_ID)
        assert valid is False and len(errors) == 1
        assert "not_declared" in errors[0]


class TestLoadPath:
    """The real PluginManager.load_plugin, with the plugin class stubbed."""

    def test_an_old_section_loads_without_warning_or_degraded(self, tmp_path):
        plugins_dir = tmp_path / "plugins"
        plugin_dir = plugins_dir / PLUGIN_ID
        plugin_dir.mkdir(parents=True)
        manifest = {"id": PLUGIN_ID, "name": "Scoreboard-shaped", "version": "1.0.0",
                    "entry_point": "manager.py", "class_name": "Plugin"}
        (plugin_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        (plugin_dir / "config_schema.json").write_text(json.dumps(STRICT_SCHEMA),
                                                       encoding="utf-8")

        config_manager = MagicMock()
        config_manager.load_config.return_value = {PLUGIN_ID: _copy(OLD_SECTION)}
        with patch('src.common.permission_utils.ensure_directory_permissions'):
            manager = PluginManager(plugins_dir=str(plugins_dir),
                                    config_manager=config_manager,
                                    display_manager=MagicMock(),
                                    cache_manager=MagicMock())
        manager.logger = MagicMock()
        manager.health_tracker = MagicMock()
        manager.plugin_manifests[PLUGIN_ID] = manifest
        manager.plugin_loader.find_plugin_directory = MagicMock(return_value=plugin_dir)

        received = {}

        def fake_load_plugin(**kwargs):
            received.update(kwargs["config"])
            instance = MagicMock()
            instance.validate_config.return_value = True
            return instance, MagicMock()

        manager.plugin_loader.load_plugin = MagicMock(side_effect=fake_load_plugin)

        assert manager.load_plugin(PLUGIN_ID) is True
        assert "skin" not in received and "skin_options" not in received
        assert received["favorite_teams"] == ["TB"]
        warnings = [c for c in manager.logger.warning.call_args_list
                    if "does not match its schema" in str(c.args[0])]
        assert warnings == []
        degraded = [c.args for c in manager.health_tracker.set_degraded.call_args_list
                    if c.args[0] == PLUGIN_ID]
        assert degraded, "schema validation never ran"
        assert degraded[-1][1] is None
