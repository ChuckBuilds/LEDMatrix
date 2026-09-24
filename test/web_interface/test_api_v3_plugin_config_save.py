"""POST /plugins/config and POST /plugins/config/reset over a real
ConfigManager and SchemaManager.

The save path reshapes what the browser posts before validating it, and these
tests pin the reshaping that validation depends on: repeats in a uniqueItems
list are dropped, and a list the form posted as numbered fields becomes a list
again.
"""

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.config_manager import ConfigManager  # noqa: E402
from src.plugin_system.schema_manager import SchemaManager  # noqa: E402
from test._api_v3_test_helpers import api_v3_module, build_app  # noqa: E402,F401

PLUGIN_ID = "stocks"

SCHEMA = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "type": "object",
    "properties": {
        "enabled": {"type": "boolean", "default": True},
        "stock_symbols": {
            "type": "array",
            "uniqueItems": True,
            "items": {"type": "string"},
            "default": ["AAPL"],
        },
        "refresh_seconds": {"type": "integer", "default": 60},
        "api_key": {"type": "string", "x-secret": True, "default": ""},
    },
}

# The shape of the news plugin's schema: a list of objects nested one level
# down. A form posts it as feeds.custom_feeds.0.name, feeds.custom_feeds.0.url,
# which lands as a dict keyed "0", "1", ...
NEWS_ID = "news"
NEWS_SCHEMA = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "type": "object",
    "properties": {
        "enabled": {"type": "boolean", "default": True},
        "feeds": {
            "type": "object",
            "properties": {
                "enabled_feeds": {
                    "type": "array", "items": {"type": "string"}, "default": [],
                },
                "custom_feeds": {
                    "type": "array",
                    "default": [],
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "url": {"type": "string"},
                            "enabled": {"type": "boolean", "default": True},
                        },
                    },
                },
            },
        },
    },
}


@pytest.fixture
def env(tmp_path, api_v3_module):
    """Real config and schema managers under tmp_path, on the blueprint."""
    plugins_dir = tmp_path / "plugins"
    for plugin_id, schema in ((PLUGIN_ID, SCHEMA), (NEWS_ID, NEWS_SCHEMA)):
        plugin_dir = plugins_dir / plugin_id
        plugin_dir.mkdir(parents=True)
        (plugin_dir / "config_schema.json").write_text(json.dumps(schema))
        (plugin_dir / "manifest.json").write_text(json.dumps(
            {"id": plugin_id, "name": plugin_id, "version": "1.0.0"}))

    config_file = tmp_path / "config.json"
    config_file.write_text(json.dumps(
        {PLUGIN_ID: {"enabled": True, "stock_symbols": ["AAPL", "FNMA"]}}))
    config_manager = ConfigManager(
        config_path=str(config_file),
        secrets_path=str(tmp_path / "config_secrets.json"))
    config_manager.template_path = str(tmp_path / "no-template.json")

    api = api_v3_module.api_v3
    api.config_manager = config_manager
    api.schema_manager = SchemaManager(plugins_dir=plugins_dir, project_root=tmp_path)
    api.plugin_manager.plugin_manifests = {PLUGIN_ID: {"id": PLUGIN_ID},
                                           NEWS_ID: {"id": NEWS_ID}}
    api.plugin_manager.get_plugin.return_value = None

    class Env:
        client = build_app(api).test_client()
        plugin_manager = api.plugin_manager

        @staticmethod
        def stored(plugin_id=PLUGIN_ID):
            return json.loads(config_file.read_text())[plugin_id]

    Env.config_manager = config_manager
    return Env


class TestUniqueItemsRepeats:
    """A repeat in a uniqueItems list is dropped, not a failed save."""

    def test_form_post_repeating_a_saved_symbol_saves(self, env):
        response = env.client.post(
            f"/api/v3/plugins/config?plugin_id={PLUGIN_ID}",
            data={"stock_symbols": "AAPL, FNMA, FNMA"})

        assert response.status_code == 200, response.get_json()
        assert env.stored()["stock_symbols"] == ["AAPL", "FNMA"]

    def test_json_post_keeps_first_occurrence_order(self, env):
        response = env.client.post("/api/v3/plugins/config", json={
            "plugin_id": PLUGIN_ID,
            "config": {"stock_symbols": ["TSLA", "AAPL", "TSLA", "FNMA"]},
        })

        assert response.status_code == 200, response.get_json()
        assert env.stored()["stock_symbols"] == ["TSLA", "AAPL", "FNMA"]


class TestNumberedFieldsBecomeLists:
    """The news plugin's custom feeds, posted one field per row."""

    FEEDS = [{"name": "Local", "url": "https://example.com/local.xml", "enabled": True},
             {"name": "Tech", "url": "https://example.com/tech.xml", "enabled": False}]

    def test_form_post(self, env):
        response = env.client.post(f"/api/v3/plugins/config?plugin_id={NEWS_ID}", data={
            "feeds.custom_feeds.0.name": "Local",
            "feeds.custom_feeds.0.url": "https://example.com/local.xml",
            "feeds.custom_feeds.0.enabled": "on",
            "feeds.custom_feeds.1.name": "Tech",
            "feeds.custom_feeds.1.url": "https://example.com/tech.xml",
        })

        assert response.status_code == 200, response.get_json()
        assert env.stored(NEWS_ID)["feeds"]["custom_feeds"] == self.FEEDS

    def test_json_post(self, env):
        response = env.client.post("/api/v3/plugins/config", json={
            "plugin_id": NEWS_ID,
            "config": {"feeds": {"custom_feeds": {"1": self.FEEDS[1], "0": self.FEEDS[0]}}},
        })

        assert response.status_code == 200, response.get_json()
        assert env.stored(NEWS_ID)["feeds"]["custom_feeds"] == self.FEEDS


class TestReset:
    """POST /plugins/config/reset saves the way every other plugin save does."""

    def test_reset_saves_atomically_with_a_backup(self, env, monkeypatch):
        cm = env.config_manager
        calls = []
        real_atomic = cm.save_config_atomic

        def spy(config, create_backup=True, **kwargs):
            calls.append(create_backup)
            return real_atomic(config, create_backup=create_backup, **kwargs)

        def no_plain_save(_config):
            raise AssertionError("reset bypassed the atomic save")

        monkeypatch.setattr(cm, "save_config_atomic", spy)
        monkeypatch.setattr(cm, "save_config", no_plain_save)

        response = env.client.post("/api/v3/plugins/config/reset",
                                   json={"plugin_id": PLUGIN_ID})

        assert response.status_code == 200, response.get_json()
        assert calls == [True]
        assert env.stored()["stock_symbols"] == ["AAPL"]

    def test_reset_notifies_the_plugin_with_its_prepared_config(self, env):
        plugin = MagicMock()
        env.plugin_manager.get_plugin.return_value = plugin
        env.plugin_manager.prepare_plugin_config.side_effect = (
            lambda _pid, raw: {**raw, "prepared": True})

        response = env.client.post("/api/v3/plugins/config/reset",
                                   json={"plugin_id": PLUGIN_ID})

        assert response.status_code == 200, response.get_json()
        handed_over = plugin.on_config_change.call_args.args[0]
        assert handed_over["prepared"] is True
        assert handed_over["stock_symbols"] == ["AAPL"]

    def test_a_failed_save_is_reported(self, env, monkeypatch):
        failed = MagicMock(message="disk full")
        failed.status.value = "failed"
        monkeypatch.setattr(env.config_manager, "save_config_atomic",
                            MagicMock(return_value=failed))

        response = env.client.post("/api/v3/plugins/config/reset",
                                   json={"plugin_id": PLUGIN_ID})

        assert response.status_code == 500
        assert "disk full" in response.get_json()["message"]
