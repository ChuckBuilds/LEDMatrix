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


class TestGetMasksSecrets:
    """GET /plugins/config returned the section with config_secrets.json
    merged in, secrets and all: the masking #276 added was lost when the
    route was rewritten. The settings page and GET /config/secrets mask."""

    def test_secrets_come_back_blank(self, env):
        data = env.client.get(f"/api/v3/plugins/config?plugin_id={PLUGIN_ID}").get_json()["data"]
        assert data["api_key"] == ""
        assert data["accounts"] == [{"name": "a", "token": ""}, {"name": "b", "token": ""}]
        assert data["city"] == "Paris"

    def test_posting_the_response_back_keeps_every_secret(self, env):
        data = env.client.get(f"/api/v3/plugins/config?plugin_id={PLUGIN_ID}").get_json()["data"]
        resp = env.post_json(data)
        assert resp.status_code == 200, resp.get_json()
        assert env.secrets()[PLUGIN_ID] == STORED_SECRETS[PLUGIN_ID]
        assert "TOPSECRET" not in json.dumps(env.main())

    def test_the_settings_form_posting_masked_fields_keeps_every_secret(self, env):
        # The page renders secrets blank (pages_v3 masks the same way)
        resp = env.post_form({
            "api_key": "", "city": "Lyon", "mqtt.host": "broker", "mqtt.port": "1883",
            "mqtt.password": "", "__rendered_section": ["api_key", "city", "mqtt"]})
        assert resp.status_code == 200, resp.get_json()
        assert env.secrets()[PLUGIN_ID] == STORED_SECRETS[PLUGIN_ID]
        assert env.main()[PLUGIN_ID]["city"] == "Lyon"

    def test_a_plugin_without_a_schema_has_credential_named_fields_blanked(self, env, tmp_path):
        (tmp_path / "plugins" / "bare").mkdir()
        env.store({"enabled": True, "station": "KAUS"}, plugin_id="bare")
        secrets = env.secrets()
        secrets["bare"] = {"api_token": "BARE-TOKEN"}
        (tmp_path / "config_secrets.json").write_text(json.dumps(secrets))
        data = env.client.get("/api/v3/plugins/config?plugin_id=bare").get_json()["data"]
        assert data["api_token"] == ""
        assert data["station"] == "KAUS"

    @pytest.mark.parametrize("section", ["web_auth", "github", "display"])
    def test_a_core_section_is_refused(self, env, tmp_path, section):
        secrets = env.secrets()
        secrets["web_auth"] = {"cookie_secret": "COOKIE-KEY", "password_hash": "HASH"}
        secrets["github"] = {"api_token": "ghp_TOKEN"}
        (tmp_path / "config_secrets.json").write_text(json.dumps(secrets))
        env.store({"hardware": {"rows": 32}}, plugin_id="display")
        resp = env.client.get(f"/api/v3/plugins/config?plugin_id={section}")
        assert resp.status_code == 400
        body = resp.get_data(as_text=True)
        assert "COOKIE-KEY" not in body and "ghp_TOKEN" not in body


ROWS_SCHEMA = {
    "type": "object",
    "properties": {
        "enabled": {"type": "boolean", "default": True},
        "cities": {
            "type": "array",
            "x-widget": "array-table",
            "default": [],
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "timezone": {"type": "string"},
                    "lat": {"type": "number"},
                    "show": {"type": "boolean", "default": True},
                },
                "required": ["name", "lat"],
            },
        },
    },
}


class TestArrayRowCellsFollowTheItemSchema:
    """A table row posts its cells as ``cities.0.timezone``. The schema
    lookup stopped at the array, so each cell was parsed blind: a blank
    optional text cell became null and a text cell holding digits became a
    number, and either failed validation -- every save of the page, for as
    long as the row existed (geochron's city without a timezone, a countdown
    named "2027")."""

    ROW = {"cities.0.name": "Tokyo", "cities.0.timezone": "Asia/Tokyo",
           "cities.0.lat": "35.68", "cities.0.show": "true",
           "__rendered_section": ["cities"]}

    @pytest.fixture(autouse=True)
    def _rows(self, env):
        env.use_schema(ROWS_SCHEMA)
        env.store({"enabled": True, "cities": [
            {"name": "Tokyo", "timezone": "Asia/Tokyo", "lat": 35.68, "show": True}]})

    def test_a_blank_optional_text_cell_saves(self, env):
        resp = env.post_form({**self.ROW, "cities.0.timezone": ""})
        assert resp.status_code == 200, resp.get_json()
        assert env.main()[PLUGIN_ID]["cities"][0]["timezone"] == ""

    def test_a_text_cell_of_digits_stays_text(self, env):
        resp = env.post_form({**self.ROW, "cities.0.name": "2027"})
        assert resp.status_code == 200, resp.get_json()
        assert env.main()[PLUGIN_ID]["cities"][0]["name"] == "2027"

    def test_number_and_boolean_cells_still_convert(self, env):
        resp = env.post_form({**self.ROW, "cities.0.show": "false"})
        assert resp.status_code == 200, resp.get_json()
        assert env.main()[PLUGIN_ID]["cities"] == [
            {"name": "Tokyo", "timezone": "Asia/Tokyo", "lat": 35.68, "show": False}]


class TestMaskedSecretCellsInARow:
    """The same lookup: a row's secret cell, rendered blank, came back as
    null and failed validation, so a plugin with secrets in a list could not
    be saved from its settings page at all."""

    def test_the_stored_tokens_survive_a_save_of_the_form(self, env):
        resp = env.post_form({
            "city": "Lyon", "accounts.0.name": "a", "accounts.0.token": "",
            "accounts.1.name": "b", "accounts.1.token": "",
            "__rendered_section": ["city", "accounts"]})
        assert resp.status_code == 200, resp.get_json()
        assert env.secrets()[PLUGIN_ID] == STORED_SECRETS[PLUGIN_ID]
        assert env.main()[PLUGIN_ID]["accounts"] == [{"name": "a"}, {"name": "b"}]
