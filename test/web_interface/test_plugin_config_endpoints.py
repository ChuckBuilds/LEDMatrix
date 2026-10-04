"""GET and POST /plugins/config against a real ConfigManager and SchemaManager.

Each class is one bug, reproduced through the endpoint the settings form and
API clients use, with assertions on config.json and config_secrets.json.
"""

import json
from unittest.mock import MagicMock

import pytest
from flask import Flask

from src.config_manager import ConfigManager
from src.plugin_system.schema_manager import SchemaManager
from web_interface.blueprints.api_v3 import api_v3

PLUGIN_ID = "demo"
OTHER_ID = "other"

SCHEMA = {
    "type": "object",
    "properties": {
        "enabled": {"type": "boolean", "default": True},
        "api_key": {"type": "string", "x-secret": True, "default": ""},
        "city": {"type": "string", "default": "Austin"},
        "mqtt": {
            "type": "object",
            "properties": {
                "host": {"type": "string", "default": ""},
                "port": {"type": "integer", "default": 1883,
                         "minimum": 1, "maximum": 65535},
                "password": {"type": "string", "x-secret": True, "default": ""},
            },
        },
        "accounts": {
            "type": "array",
            "default": [],
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "token": {"type": "string", "x-secret": True},
                },
            },
        },
    },
}

OTHER_SCHEMA = {
    "type": "object",
    "properties": {
        "enabled": {"type": "boolean", "default": True},
        "label": {"type": "string", "default": "x"},
    },
}

STORED = {
    PLUGIN_ID: {"enabled": True, "city": "Paris",
                "mqtt": {"host": "broker", "port": 1883},
                "accounts": [{"name": "a"}, {"name": "b"}]},
    OTHER_ID: {"enabled": True, "label": "hello"},
}

STORED_SECRETS = {
    PLUGIN_ID: {"api_key": "TOPSECRET",
                "accounts": [{"token": "TOK-A"}, {"token": "TOK-B"}]},
}

_ATTRS = ('config_manager', 'plugin_catalog', 'plugin_store_manager',
          'saved_repositories_manager', 'schema_manager',
          'operation_queue', 'operation_history', 'cache_manager')


@pytest.fixture
def env(tmp_path):
    config_file = tmp_path / "config.json"
    secrets_file = tmp_path / "config_secrets.json"
    plugins_dir = tmp_path / "plugins"
    for plugin_id, schema in ((PLUGIN_ID, SCHEMA), (OTHER_ID, OTHER_SCHEMA)):
        plugin_dir = plugins_dir / plugin_id
        plugin_dir.mkdir(parents=True)
        (plugin_dir / "config_schema.json").write_text(json.dumps(schema))
        (plugin_dir / "manifest.json").write_text(json.dumps({"id": plugin_id}))
    config_file.write_text(json.dumps(STORED))
    secrets_file.write_text(json.dumps(STORED_SECRETS))

    sentinel = object()
    originals = {name: getattr(api_v3, name, sentinel) for name in _ATTRS}

    config_manager = ConfigManager(config_path=str(config_file),
                                   secrets_path=str(secrets_file))
    config_manager.template_path = str(tmp_path / "no-template.json")
    plugin_manager = MagicMock()
    plugin_manager.plugin_manifests = {PLUGIN_ID: {"id": PLUGIN_ID},
                                       OTHER_ID: {"id": OTHER_ID}}
    plugin_manager.plugins_dir = plugins_dir

    for name in _ATTRS:
        setattr(api_v3, name, MagicMock())
    api_v3.config_manager = config_manager
    api_v3.schema_manager = SchemaManager(plugins_dir=plugins_dir, project_root=tmp_path)
    api_v3.plugin_catalog = plugin_manager
    api_v3.operation_queue = None

    app = Flask(__name__)
    app.config["TESTING"] = True
    app.register_blueprint(api_v3, url_prefix="/api/v3")

    class Env:
        client = app.test_client()

        @staticmethod
        def use_schema(schema, plugin_id=PLUGIN_ID):
            (plugins_dir / plugin_id / "config_schema.json").write_text(json.dumps(schema))

        @staticmethod
        def store(section, plugin_id=PLUGIN_ID):
            main = json.loads(config_file.read_text())
            main[plugin_id] = section
            config_file.write_text(json.dumps(main))

        @staticmethod
        def main():
            return json.loads(config_file.read_text())

        @staticmethod
        def secrets():
            return json.loads(secrets_file.read_text())

        @staticmethod
        def post_form(data, plugin_id=PLUGIN_ID):
            return Env.client.post(f"/api/v3/plugins/config?plugin_id={plugin_id}",
                                   data=data)

        @staticmethod
        def post_json(config, plugin_id=PLUGIN_ID):
            return Env.client.post("/api/v3/plugins/config",
                                   json={"plugin_id": plugin_id, "config": config})

    yield Env

    for name, original in originals.items():
        if original is sentinel:
            if hasattr(api_v3, name):
                delattr(api_v3, name)
        else:
            setattr(api_v3, name, original)


class TestARejectedSaveLeavesNothingBehind:
    """The form save edited the cached config load_config hands out, then
    failed validation. The cache kept the edit, and the next save of any
    other setting wrote it to config.json -- the rejected value, and a
    nested secret typed into the same form in plain text."""

    REJECTED = {"mqtt.host": "broker", "mqtt.port": "99999",
                "mqtt.password": "hunter2", "__rendered_section": ["mqtt"]}

    def test_the_rejected_values_never_reach_config_json(self, env):
        assert env.post_form(self.REJECTED).status_code == 400

        resp = env.post_json({"label": "bye"}, plugin_id=OTHER_ID)
        assert resp.status_code == 200, resp.get_json()

        main = env.main()
        assert main[OTHER_ID]["label"] == "bye"
        assert main[PLUGIN_ID]["mqtt"] == {"host": "broker", "port": 1883}
        assert "hunter2" not in json.dumps(main)

    def test_the_form_reloads_with_the_stored_values(self, env):
        assert env.post_form(self.REJECTED).status_code == 400
        assert api_v3.config_manager.load_config()[PLUGIN_ID]["mqtt"]["port"] == 1883
