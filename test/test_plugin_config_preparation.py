"""One preparation for a plugin's config, wherever the config comes from.

A plugin's stored section becomes the config it runs with through
schema_manager.prepare_plugin_config: legacy booleans (#588) read as
``{"enabled": ...}`` objects, then schema and core defaults filled in. Loading
did that; hot reload handed plugins the raw section instead (a legacy
``dynamic_duration: true`` came back as a boolean, turning dynamic duration
off), and the dev tools each built configs their own way:

- dev_server read only top-level defaults and let a schema ``enabled: false``
  override its forced ``enabled: True``;
- check_plugin/render_plugin (build_full_config) merged overrides with
  dict.update, so ``{"nhl": {"enabled": true}}`` dropped every other nhl
  default;
- the harness and the device disagreed on object and array defaults.

The web saves and GET are covered in
test/web_interface/test_plugin_config_json_saves.py.
"""

import importlib.util
import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.plugin_system.plugin_manager import PluginManager
from src.plugin_system.schema_manager import (
    CORE_PLUGIN_PROPERTIES, SchemaManager, extract_schema_defaults,
)
from src.plugin_system.testing import loading

REPO = Path(__file__).resolve().parent.parent

SCHEMA = {
    "type": "object",
    "properties": {
        "enabled": {"type": "boolean", "default": False},
        "global": {
            "type": "object",
            "properties": {
                "dynamic_duration": {
                    "type": "object",
                    "properties": {
                        "enabled": {"type": "boolean", "default": True},
                        "max_duration_seconds": {"type": "integer", "default": 300},
                    },
                },
            },
        },
        "nhl": {
            "type": "object",
            "properties": {
                "enabled": {"type": "boolean", "default": False},
                "favorite_teams": {"type": "array", "default": ["TB"]},
                "show_records": {"type": "boolean", "default": True},
            },
        },
        "colors": {
            "type": "object",
            "default": {"text": [255, 255, 255]},
            "properties": {"text": {"type": "array", "default": [1, 2, 3]}},
        },
        "feeds": {"type": "array", "items": {"type": "string"}},
    },
}


@pytest.fixture
def plugin_dir(tmp_path):
    pdir = tmp_path / "plugins" / "demo"
    pdir.mkdir(parents=True)
    (pdir / "config_schema.json").write_text(json.dumps(SCHEMA))
    (pdir / "manifest.json").write_text(json.dumps({"id": "demo"}))
    return pdir


@pytest.fixture
def plugin_manager(plugin_dir, tmp_path):
    manager = PluginManager.__new__(PluginManager)  # skip the heavy constructor
    manager.logger = MagicMock()
    manager.schema_manager = SchemaManager(plugins_dir=plugin_dir.parent, project_root=tmp_path)
    return manager


class TestPluginManagerPreparation:
    def test_legacy_boolean_and_defaults(self, plugin_manager):
        prepared = plugin_manager.prepare_plugin_config(
            "demo", {"enabled": True, "global": {"dynamic_duration": True}})
        assert prepared["global"]["dynamic_duration"] == {
            "enabled": True, "max_duration_seconds": 300}
        assert prepared["nhl"]["favorite_teams"] == ["TB"]
        assert prepared["display_duration"] == 15

    def test_does_not_mutate_the_raw_section(self, plugin_manager):
        raw = {"global": {"dynamic_duration": True}}
        plugin_manager.prepare_plugin_config("demo", raw)
        assert raw == {"global": {"dynamic_duration": True}}

    def test_never_raises(self, plugin_manager):
        plugin_manager.schema_manager = MagicMock()
        plugin_manager.schema_manager.load_schema.return_value = SCHEMA
        plugin_manager.schema_manager.prepare_plugin_config.side_effect = RuntimeError("boom")
        prepared = plugin_manager.prepare_plugin_config("demo", {"global": {"dynamic_duration": False}})
        assert prepared["global"]["dynamic_duration"] == {"enabled": False}


class TestHotReload:
    def test_on_config_change_gets_the_prepared_config(self, test_display_controller, plugin_manager):
        controller = test_display_controller
        plugin = MagicMock()
        plugin.modes = ["demo"]
        pm = controller.plugin_manager
        pm.discover_plugins.return_value = ["demo"]
        pm.load_plugin.return_value = True
        pm.plugin_manifests = {}
        pm.get_plugin.side_effect = lambda pid: plugin if pid == "demo" else None
        pm.prepare_plugin_config.side_effect = plugin_manager.prepare_plugin_config
        controller.config_service.get_config = lambda: {"demo": {"enabled": True}}
        controller._reconcile_enabled_plugins()

        callback = controller._plugin_config_callbacks["demo"]
        callback({"enabled": True}, {"enabled": True, "global": {"dynamic_duration": True}})

        new_config = plugin.on_config_change.call_args[0][0]
        assert new_config["global"]["dynamic_duration"] == {
            "enabled": True, "max_duration_seconds": 300}
        assert new_config["nhl"]["show_records"] is True

    def test_raw_section_still_delivered_without_a_preparer(self, test_display_controller):
        controller = test_display_controller
        plugin = MagicMock()
        plugin.modes = ["demo"]
        pm = controller.plugin_manager
        pm.discover_plugins.return_value = ["demo"]
        pm.load_plugin.return_value = True
        pm.plugin_manifests = {}
        pm.get_plugin.side_effect = lambda pid: plugin if pid == "demo" else None
        pm.prepare_plugin_config.return_value = None
        controller.config_service.get_config = lambda: {"demo": {"enabled": True}}
        controller._reconcile_enabled_plugins()

        raw = {"enabled": False}
        controller._plugin_config_callbacks["demo"]({}, raw)
        plugin.on_config_change.assert_called_once_with(raw)


class TestDevToolsMatchTheDevice:
    def test_harness_defaults_are_the_device_defaults(self, plugin_dir):
        assert loading.load_config_defaults(plugin_dir) == extract_schema_defaults(SCHEMA)
        defaults = loading.load_config_defaults(plugin_dir)
        # An object's own default wins, as on a device; arrays start empty
        assert defaults["colors"] == {"text": [255, 255, 255]}
        assert defaults["feeds"] == []

    def test_nested_override_keeps_sibling_defaults(self, plugin_dir):
        config = loading.build_full_config(plugin_dir, cli_config={"nhl": {"enabled": True}})
        assert config["nhl"] == {"enabled": True, "favorite_teams": ["TB"], "show_records": True}

    def test_spec_and_cli_overrides_both_deep_merge(self, plugin_dir):
        config = loading.build_full_config(
            plugin_dir, spec={"config": {"nhl": {"show_records": False}}},
            cli_config={"nhl": {"enabled": True}})
        assert config["nhl"] == {"enabled": True, "favorite_teams": ["TB"], "show_records": False}

    def test_build_config_matches_the_device_load(self, plugin_dir, plugin_manager):
        overrides = {"enabled": True, "global": {"dynamic_duration": False}}
        assert loading.build_config(plugin_dir, overrides) == \
            plugin_manager.prepare_plugin_config("demo", overrides)

    def test_core_defaults_are_present(self, plugin_dir):
        config = loading.build_full_config(plugin_dir)
        assert config["enabled"] is True
        assert config["display_duration"] == CORE_PLUGIN_PROPERTIES["display_duration"]["default"]
        assert config["live_priority"] is False

    def test_render_plugin_matrix_config(self, plugin_dir, monkeypatch):
        from src.plugin_system.testing import harness
        seen = {}

        def fake_render_size(plugin_id, manifest, pdir, config, *args, **kwargs):
            seen["config"] = config
            return []

        monkeypatch.setattr(harness, "_render_size", fake_render_size)
        harness.render_plugin_matrix("demo", plugin_dir, config={"nhl": {"enabled": True}},
                                     sizes=[(64, 32)], run_update=False)
        assert seen["config"]["enabled"] is True, "a schema enabled:false must not win"
        assert seen["config"]["nhl"]["favorite_teams"] == ["TB"]

    def test_dev_server_render_config(self, plugin_dir, monkeypatch):
        spec = importlib.util.spec_from_file_location("dev_server_under_test",
                                                      REPO / "scripts" / "dev_server.py")
        dev_server = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(dev_server)
        monkeypatch.setattr(dev_server, "find_plugin_dir", lambda pid: plugin_dir)
        monkeypatch.setattr(dev_server, "_trusted_plugin_dir", lambda d: plugin_dir)

        _, _, config, _, _ = dev_server._parse_render_request(
            {"plugin_id": "demo", "config": {"nhl": {"enabled": True}}})
        assert config["enabled"] is True, "a schema enabled:false must not win"
        assert config["nhl"] == {"enabled": True, "favorite_teams": ["TB"], "show_records": True}
        assert config["global"]["dynamic_duration"]["max_duration_seconds"] == 300
        assert dev_server.load_config_defaults(plugin_dir) == extract_schema_defaults(SCHEMA)
