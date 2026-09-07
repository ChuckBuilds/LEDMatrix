"""
End-to-end secret round-trips through the three api_v3 endpoints that
separate secrets from regular config (main-config save, plugin-config save,
plugin-config reset) — now backed by the canonical
src/web_interface/secret_helpers implementations.

Unlike test_web_api.py (which mocks the config manager), these tests run a
REAL ConfigManager and a REAL SchemaManager over tmp_path files, so they
prove the whole chain: endpoint separation -> config_secrets.json write ->
atomic config.json save (strip) -> load_config (merge back), including the
array-item secret shape (accounts[].token) the inline copies never
supported.
"""

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from flask import Flask

project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

from src.config_manager import ConfigManager  # noqa: E402
from src.plugin_system.schema_manager import SchemaManager  # noqa: E402
from web_interface.blueprints.api_v3 import api_v3  # noqa: E402


PLUGIN_ID = "testplugin"

SCHEMA = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "type": "object",
    "properties": {
        "enabled": {"type": "boolean", "default": True},
        "display_duration": {"type": "number", "default": 15},
        "api_key": {"type": "string", "x-secret": True, "default": ""},
        "city": {"type": "string", "default": "Austin"},
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


@pytest.fixture
def env(tmp_path):
    """Real ConfigManager + SchemaManager over tmp_path, wired onto the
    api_v3 blueprint with the remaining managers mocked."""
    config_file = tmp_path / "config.json"
    config_file.write_text("{}")

    plugins_dir = tmp_path / "plugins"
    plugin_dir = plugins_dir / PLUGIN_ID
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "config_schema.json").write_text(json.dumps(SCHEMA))
    (plugin_dir / "manifest.json").write_text(json.dumps({
        "id": PLUGIN_ID, "name": "Test Plugin", "version": "1.0.0",
    }))

    config_manager = ConfigManager(
        config_path=str(config_file),
        secrets_path=str(tmp_path / "config_secrets.json"))
    config_manager.template_path = str(tmp_path / "no-template.json")

    schema_manager = SchemaManager(plugins_dir=plugins_dir,
                                   project_root=tmp_path)

    plugin_manager = MagicMock()
    plugin_manager.plugin_manifests = {PLUGIN_ID: {"id": PLUGIN_ID}}
    plugin_manager.plugins_dir = plugins_dir
    plugin_manager.get_plugin.return_value = None

    api_v3.config_manager = config_manager
    api_v3.schema_manager = schema_manager
    api_v3.plugin_manager = plugin_manager
    api_v3.plugin_store_manager = MagicMock()
    api_v3.saved_repositories_manager = MagicMock()
    api_v3.operation_queue = MagicMock()
    api_v3.plugin_state_manager = MagicMock()
    api_v3.operation_history = MagicMock()
    api_v3.cache_manager = MagicMock()

    app = Flask(__name__)
    app.config["TESTING"] = True
    app.register_blueprint(api_v3, url_prefix="/api/v3")

    class Env:
        pass

    e = Env()
    e.client = app.test_client()
    e.config_manager = config_manager
    e.config_file = config_file
    e.secrets_file = tmp_path / "config_secrets.json"
    e.tmp_path = tmp_path

    def fresh_load():
        """Load via a NEW ConfigManager, as the next request/process would.

        The endpoint's manager serves its post-save in-memory config via the
        mtime fast path, and that copy predates the secrets it just
        separated out — a pre-existing quirk that applies to scalar secrets
        too. On-disk truth is what these tests care about.
        """
        fresh = ConfigManager(config_path=str(config_file),
                              secrets_path=str(e.secrets_file))
        fresh.template_path = str(tmp_path / "no-template.json")
        return fresh.load_config()

    e.fresh_load = fresh_load
    return e


def _on_disk(path):
    return json.loads(path.read_text())


class TestSaveMainConfig:
    """Site A: POST /config/main with a plugin-id key."""

    def test_array_and_scalar_secrets_routed_to_secrets_file(self, env):
        resp = env.client.post("/api/v3/config/main", json={
            PLUGIN_ID: {
                "city": "Dallas",
                "api_key": "s3cret-key",
                "accounts": [
                    {"name": "a", "token": "s3cret-a"},
                    {"name": "b"},
                ],
            },
        })
        assert resp.status_code == 200, resp.get_json()

        on_disk = _on_disk(env.config_file)
        assert on_disk[PLUGIN_ID]["city"] == "Dallas"
        assert "api_key" not in on_disk[PLUGIN_ID]
        assert on_disk[PLUGIN_ID]["accounts"] == [{"name": "a"}, {"name": "b"}]
        assert "s3cret" not in env.config_file.read_text()

        secrets = _on_disk(env.secrets_file)
        assert secrets[PLUGIN_ID]["api_key"] == "s3cret-key"
        assert secrets[PLUGIN_ID]["accounts"] == [{"token": "s3cret-a"}, {}]

    def test_load_config_merges_secrets_back(self, env):
        env.client.post("/api/v3/config/main", json={
            PLUGIN_ID: {"accounts": [{"name": "a", "token": "s3cret-a"}]},
        })
        merged = env.fresh_load()
        assert merged[PLUGIN_ID]["accounts"] == [
            {"name": "a", "token": "s3cret-a"}]


class TestSavePluginConfig:
    """Site B: POST /plugins/config (JSON body)."""

    def _save(self, env, config):
        return env.client.post("/api/v3/plugins/config", json={
            "plugin_id": PLUGIN_ID, "config": config,
        })

    def test_round_trip_with_array_secrets(self, env):
        resp = self._save(env, {
            "enabled": True,
            "city": "Houston",
            "api_key": "s3cret-key",
            "accounts": [
                {"name": "a", "token": "s3cret-a"},
                {"name": "b", "token": "s3cret-b"},
            ],
        })
        assert resp.status_code == 200, resp.get_json()

        assert "s3cret" not in env.config_file.read_text()
        on_disk = _on_disk(env.config_file)
        assert on_disk[PLUGIN_ID]["accounts"] == [{"name": "a"}, {"name": "b"}]

        secrets = _on_disk(env.secrets_file)
        assert secrets[PLUGIN_ID]["accounts"] == [
            {"token": "s3cret-a"}, {"token": "s3cret-b"}]

        merged = env.fresh_load()
        assert merged[PLUGIN_ID]["accounts"][1]["token"] == "s3cret-b"

    def test_secret_count_message_counts_top_level_keys(self, env):
        # Pinned: the "(N secret field(s))" message counts TOP-LEVEL keys of
        # the separated secrets dict. Here that is 1: the posted accounts
        # array, whose item tokens all count as ONE key.
        #
        # It was 2 before blank secrets were dropped, the second being the
        # schema's api_key default (""), which merge_with_defaults adds to
        # every save. Counting it was the visible edge of a real bug: that
        # injected blank was merged over the stored api_key, so saving any
        # unrelated field destroyed the credential. See
        # test_an_unrelated_edit_does_not_erase_a_stored_secret.
        resp = self._save(env, {
            "accounts": [{"name": "a", "token": "t"}],
        })
        message = resp.get_json()["message"]
        assert "(1 secret field(s) saved to config_secrets.json)" in message

    def test_an_unrelated_edit_does_not_erase_a_stored_secret(self, env):
        """Editing one field must not wipe the plugin's API key.

        The config form renders secrets masked, so the browser posts them
        back blank; merge_with_defaults injects a blank api_key even when
        the client omits it entirely. Either way a "" reached the secrets
        file and deep_merge wrote it over the stored credential.
        """
        assert self._save(env, {"api_key": "REAL-KEY-0123456789",
                                "city": "Austin"}).status_code == 200
        assert _on_disk(env.secrets_file)[PLUGIN_ID]["api_key"] == \
            "REAL-KEY-0123456789"

        # the user changes the city; the masked api_key rides along blank
        assert self._save(env, {"api_key": "", "city": "Dallas"}).status_code == 200

        assert _on_disk(env.secrets_file)[PLUGIN_ID]["api_key"] == \
            "REAL-KEY-0123456789", "an unrelated edit destroyed the API key"
        assert env.fresh_load()[PLUGIN_ID]["city"] == "Dallas"

    def test_an_unrelated_edit_does_not_erase_array_item_secrets(self, env):
        """The scalar api_key case above, but for a list of credentials.

        remove_empty_secrets recursed into dicts only, so a list went into
        deep_merge untouched -- and lists merge by *replacement*. Saving any
        unrelated field posted [{"token": ""}, ...] straight over the stored
        array and destroyed every token in it at once.
        """
        assert self._save(env, {"accounts": [
            {"name": "a", "token": "REAL-A"},
            {"name": "b", "token": "REAL-B"},
        ], "city": "Austin"}).status_code == 200

        # the user changes the city; both masked tokens ride along blank
        assert self._save(env, {"accounts": [
            {"name": "a", "token": ""},
            {"name": "b", "token": ""},
        ], "city": "Dallas"}).status_code == 200

        merged = env.fresh_load()[PLUGIN_ID]
        assert [a.get("token") for a in merged["accounts"]] == \
            ["REAL-A", "REAL-B"], "an unrelated edit destroyed the array secrets"
        assert [a["name"] for a in merged["accounts"]] == ["a", "b"]
        assert merged["city"] == "Dallas"

    def test_one_array_secret_can_be_changed_without_losing_the_rest(self, env):
        assert self._save(env, {"accounts": [
            {"name": "a", "token": "REAL-A"},
            {"name": "b", "token": "REAL-B"},
        ]}).status_code == 200
        assert self._save(env, {"accounts": [
            {"name": "a", "token": ""},
            {"name": "b", "token": "NEW-B"},
        ]}).status_code == 200

        merged = env.fresh_load()[PLUGIN_ID]
        assert [a.get("token") for a in merged["accounts"]] == ["REAL-A", "NEW-B"]

    def test_a_secret_can_still_be_changed(self, env):
        """Dropping blanks must not stop a real new value from being saved."""
        self._save(env, {"api_key": "first-key"})
        self._save(env, {"api_key": "second-key"})
        assert _on_disk(env.secrets_file)[PLUGIN_ID]["api_key"] == "second-key"

    def test_resave_replaces_stored_secrets_list_wholesale(self, env):
        # Characterized: api_v3's deep_merge intentionally replaces lists,
        # so a re-save's parallel secrets list is authoritative.
        self._save(env, {"accounts": [
            {"name": "a", "token": "old-a"},
            {"name": "b", "token": "old-b"},
        ]})
        self._save(env, {"accounts": [{"name": "only", "token": "new-only"}]})

        secrets = _on_disk(env.secrets_file)
        assert secrets[PLUGIN_ID]["accounts"] == [{"token": "new-only"}]
        merged = env.fresh_load()
        assert merged[PLUGIN_ID]["accounts"] == [
            {"name": "only", "token": "new-only"}]


class TestResetPluginConfig:
    """Site C: POST /plugins/config/reset."""

    def _seed(self, env):
        env.client.post("/api/v3/plugins/config", json={
            "plugin_id": PLUGIN_ID,
            "config": {"city": "Houston", "api_key": "s3cret-key",
                       "accounts": [{"name": "a", "token": "s3cret-a"}]},
        })

    def test_reset_preserving_secrets(self, env):
        self._seed(env)
        resp = env.client.post("/api/v3/plugins/config/reset", json={
            "plugin_id": PLUGIN_ID, "preserve_secrets": True,
        })
        assert resp.status_code == 200, resp.get_json()

        on_disk = _on_disk(env.config_file)
        assert on_disk[PLUGIN_ID]["city"] == "Austin"  # schema default
        assert on_disk[PLUGIN_ID]["accounts"] == []    # schema default

        # Existing secrets survive (top-level-only preserve merge, pinned).
        secrets = _on_disk(env.secrets_file)
        assert secrets[PLUGIN_ID]["api_key"] == "s3cret-key"
        assert secrets[PLUGIN_ID]["accounts"] == [{"token": "s3cret-a"}]

    def test_reset_without_preserving_secrets(self, env):
        self._seed(env)
        resp = env.client.post("/api/v3/plugins/config/reset", json={
            "plugin_id": PLUGIN_ID, "preserve_secrets": False,
        })
        assert resp.status_code == 200, resp.get_json()

        secrets = _on_disk(env.secrets_file)
        # Replaced with schema-default secrets — the schema declares no
        # secret defaults, so the plugin's secrets are emptied.
        assert secrets[PLUGIN_ID] in ({}, {"api_key": ""})
