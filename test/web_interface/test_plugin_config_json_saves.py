"""JSON plugin-config saves store what the device would load, and only what was sent.

Runs a real ConfigManager and SchemaManager over tmp_path, like
test_api_v3_secret_roundtrip.py, so assertions are on config.json itself.

- POST /plugins/config with a partial ``config`` reset every unsent setting to
  its schema default: the JSON path built on defaults, not the stored section.
- Legacy booleans (#588) were normalized only at load, so posting back what
  GET /plugins/config returned failed validation.
- The JSON save's filter kept only enabled/display_duration/live_priority, so
  a submitted skin, skin_options or vegas_* tuning key was silently dropped.
- Plugin sections posted to /config/main skipped all of that and were stored
  verbatim, including values /plugins/config rejects.
"""

import json
from unittest.mock import MagicMock

import pytest
from flask import Flask

from src.config_manager import ConfigManager
from src.plugin_system.schema_manager import SchemaManager
from web_interface.blueprints.api_v3 import api_v3

PLUGIN_ID = "demo"

SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "enabled": {"type": "boolean", "default": True},
        "city": {"type": "string", "default": "Dallas"},
        "update_interval": {"type": "integer", "default": 300, "minimum": 30},
        "api_key": {"type": "string", "x-secret": True, "default": ""},
        "display": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "brightness": {"type": "integer", "default": 50},
                "show_icons": {"type": "boolean", "default": True},
            },
        },
        "global": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "dynamic_duration": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "enabled": {"type": "boolean", "default": True},
                        "min_duration_seconds": {"type": "integer", "default": 30},
                    },
                },
            },
        },
    },
}

STORED = {
    "enabled": True,
    "city": "Paris",
    "update_interval": 600,
    "display": {"brightness": 80, "show_icons": False},
    "global": {"dynamic_duration": {"enabled": False, "min_duration_seconds": 45}},
    "vegas_width_pct": 60,
    "skin": "neon",
}

_ATTRS = ('config_manager', 'plugin_manager', 'plugin_store_manager',
          'plugin_state_manager', 'saved_repositories_manager', 'schema_manager',
          'operation_queue', 'operation_history', 'cache_manager')


@pytest.fixture
def env(tmp_path):
    config_file = tmp_path / "config.json"
    secrets_file = tmp_path / "config_secrets.json"
    plugins_dir = tmp_path / "plugins"
    plugin_dir = plugins_dir / PLUGIN_ID
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "config_schema.json").write_text(json.dumps(SCHEMA))
    (plugin_dir / "manifest.json").write_text(json.dumps({"id": PLUGIN_ID}))

    sentinel = object()
    originals = {name: getattr(api_v3, name, sentinel) for name in _ATTRS}

    config_manager = ConfigManager(config_path=str(config_file),
                                   secrets_path=str(secrets_file))
    config_manager.template_path = str(tmp_path / "no-template.json")
    plugin_manager = MagicMock()
    plugin_manager.plugin_manifests = {PLUGIN_ID: {"id": PLUGIN_ID}}
    plugin_manager.plugins_dir = plugins_dir
    plugin_manager.get_plugin.return_value = None

    for name in _ATTRS:
        setattr(api_v3, name, MagicMock())
    api_v3.config_manager = config_manager
    api_v3.schema_manager = SchemaManager(plugins_dir=plugins_dir, project_root=tmp_path)
    api_v3.plugin_manager = plugin_manager
    api_v3.operation_queue = None

    app = Flask(__name__)
    app.config["TESTING"] = True
    app.register_blueprint(api_v3, url_prefix="/api/v3")

    class Env:
        client = app.test_client()

        @staticmethod
        def store(section):
            config_file.write_text(json.dumps({"timezone": "UTC", PLUGIN_ID: section}))

        @staticmethod
        def main():
            return json.loads(config_file.read_text())

        @staticmethod
        def stored():
            return json.loads(config_file.read_text())[PLUGIN_ID]

        @staticmethod
        def secrets():
            return json.loads(secrets_file.read_text()) if secrets_file.exists() else {}

        @staticmethod
        def save(config):
            return app.test_client().post("/api/v3/plugins/config",
                                          json={"plugin_id": PLUGIN_ID, "config": config})

    Env.store(json.loads(json.dumps(STORED)))
    yield Env

    for name, original in originals.items():
        if original is sentinel:
            if hasattr(api_v3, name):
                delattr(api_v3, name)
        else:
            setattr(api_v3, name, original)


class TestPartialJsonSave:
    def test_unsent_settings_keep_their_values(self, env):
        resp = env.save({"enabled": True})
        assert resp.status_code == 200, resp.get_json()
        stored = env.stored()
        assert stored["city"] == "Paris"
        assert stored["update_interval"] == 600
        assert stored["display"] == {"brightness": 80, "show_icons": False}
        assert stored["global"]["dynamic_duration"] == {"enabled": False, "min_duration_seconds": 45}

    def test_a_nested_partial_keeps_its_siblings(self, env):
        resp = env.save({"display": {"brightness": 20}})
        assert resp.status_code == 200, resp.get_json()
        assert env.stored()["display"] == {"brightness": 20, "show_icons": False}

    def test_a_sent_setting_still_changes(self, env):
        assert env.save({"city": "Lyon", "enabled": False}).status_code == 200
        stored = env.stored()
        assert stored["city"] == "Lyon" and stored["enabled"] is False

    def test_an_invalid_sent_value_is_still_rejected(self, env):
        resp = env.save({"update_interval": 5})
        assert resp.status_code == 400
        assert env.stored()["update_interval"] == 600

    def test_config_must_be_an_object(self, env):
        assert env.save(["city"]).status_code == 400


class TestCoreOwnedKeysSurviveTheFilter:
    def test_submitted_core_keys_are_stored(self, env):
        env.store({"enabled": True, "city": "Paris"})
        resp = env.save({
            "vegas_width_pct": 50, "vegas_overflow": "truncate",
            "vegas_max_width_screens": 2, "skin": "retro",
            "skin_options": {"accent": "#ff0000"}, "live_priority": True,
        })
        assert resp.status_code == 200, resp.get_json()
        stored = env.stored()
        assert stored["vegas_width_pct"] == 50
        assert stored["vegas_overflow"] == "truncate"
        assert stored["vegas_max_width_screens"] == 2
        assert stored["skin"] == "retro"
        assert stored["skin_options"] == {"accent": "#ff0000"}
        assert stored["live_priority"] is True

    def test_stored_core_keys_survive_an_unrelated_save(self, env):
        assert env.save({"city": "Nice"}).status_code == 200
        stored = env.stored()
        assert stored["vegas_width_pct"] == 60 and stored["skin"] == "neon"

    def test_a_non_core_unknown_key_is_still_filtered(self, env):
        assert env.save({"not_in_schema": 1}).status_code == 200
        assert "not_in_schema" not in env.stored()


class TestLegacyBooleans:
    LEGACY = {"enabled": True, "city": "Paris", "global": {"dynamic_duration": True}}

    def test_get_returns_the_object_shape(self, env):
        env.store(dict(self.LEGACY))
        data = env.client.get(f"/api/v3/plugins/config?plugin_id={PLUGIN_ID}").get_json()["data"]
        assert data["global"]["dynamic_duration"] == {"enabled": True, "min_duration_seconds": 30}

    def test_get_output_posts_back(self, env):
        env.store(dict(self.LEGACY))
        data = env.client.get(f"/api/v3/plugins/config?plugin_id={PLUGIN_ID}").get_json()["data"]
        resp = env.save(data)
        assert resp.status_code == 200, resp.get_json()
        assert env.stored()["global"]["dynamic_duration"] == {"enabled": True, "min_duration_seconds": 30}

    def test_an_unrelated_json_save_upgrades_the_stored_boolean(self, env):
        env.store(dict(self.LEGACY))
        resp = env.save({"city": "Lyon"})
        assert resp.status_code == 200, resp.get_json()
        assert env.stored()["global"]["dynamic_duration"] == {"enabled": True, "min_duration_seconds": 30}

    def test_a_posted_legacy_boolean_is_read_as_the_object(self, env):
        resp = env.save({"global": {"dynamic_duration": False}})
        assert resp.status_code == 200, resp.get_json()
        assert env.stored()["global"]["dynamic_duration"]["enabled"] is False


class TestPluginSectionsInConfigMain:
    def _post(self, env, body):
        return env.client.post("/api/v3/config/main", json=body)

    def test_a_value_plugins_config_rejects_is_rejected_here_too(self, env):
        resp = self._post(env, {PLUGIN_ID: {"update_interval": "abc"}})
        assert resp.status_code == 400
        assert env.stored()["update_interval"] == 600
        assert env.save({"update_interval": "abc"}).status_code == 400

    def test_nothing_is_saved_when_a_plugin_section_fails(self, env):
        resp = self._post(env, {"timezone": "Europe/Paris", PLUGIN_ID: {"update_interval": 1}})
        assert resp.status_code == 400
        assert env.main()["timezone"] == "UTC"
        assert env.stored()["update_interval"] == 600

    def test_partial_section_merges_and_keeps_core_keys(self, env):
        resp = self._post(env, {PLUGIN_ID: {"city": "Lyon", "vegas_overflow": "rotate"}})
        assert resp.status_code == 200, resp.get_json()
        stored = env.stored()
        assert stored["city"] == "Lyon"
        assert stored["display"] == {"brightness": 80, "show_icons": False}
        assert stored["vegas_overflow"] == "rotate"
        assert stored["vegas_width_pct"] == 60 and stored["skin"] == "neon"

    def test_secrets_still_go_to_the_secrets_file(self, env):
        resp = self._post(env, {PLUGIN_ID: {"api_key": "s3cret"}})
        assert resp.status_code == 200, resp.get_json()
        assert "api_key" not in env.stored() or env.stored()["api_key"] in ("", None)
        assert env.secrets()[PLUGIN_ID]["api_key"] == "s3cret"

    def test_a_non_object_section_is_rejected(self, env):
        assert self._post(env, {PLUGIN_ID: "on"}).status_code == 400
