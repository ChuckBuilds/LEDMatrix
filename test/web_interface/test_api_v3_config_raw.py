"""
Endpoint tests for POST /config/raw/main and POST /config/raw/secrets.

These write whatever JSON they are given straight to config.json and
config_secrets.json, bypassing the secret-separation path that
/config/main and the plugin-config endpoints go through. Given how much
care the rest of the config surface takes to keep secrets out of
config.json, an untested pair of endpoints that writes it verbatim is
worth pinning precisely.

Like test_api_v3_secret_roundtrip.py, these run a REAL ConfigManager over
tmp_path so the assertions are against files on disk rather than mock
calls.
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
from src.exceptions import ConfigError  # noqa: E402
from web_interface.blueprints.api_v3 import api_v3  # noqa: E402

MAIN = "/api/v3/config/raw/main"
SECRETS = "/api/v3/config/raw/secrets"


@pytest.fixture
def env(tmp_path):
    config_file = tmp_path / "config.json"
    config_file.write_text(json.dumps({"timezone": "UTC"}))
    secrets_file = tmp_path / "config_secrets.json"

    config_manager = ConfigManager(
        config_path=str(config_file), secrets_path=str(secrets_file))
    config_manager.template_path = str(tmp_path / "no-template.json")

    _SENTINEL = object()
    attrs = ('config_manager', 'plugin_manager', 'plugin_store_manager',
             'plugin_state_manager', 'saved_repositories_manager',
             'schema_manager', 'operation_queue', 'operation_history',
             'cache_manager')
    originals = {name: getattr(api_v3, name, _SENTINEL) for name in attrs}

    for name in attrs:
        setattr(api_v3, name, MagicMock())
    api_v3.config_manager = config_manager

    app = Flask(__name__)
    app.config["TESTING"] = True
    app.register_blueprint(api_v3, url_prefix="/api/v3")

    class Env:
        pass

    e = Env()
    e.client = app.test_client()
    e.config_manager = config_manager
    e.config_file = config_file
    e.secrets_file = secrets_file
    yield e

    for name, original in originals.items():
        if original is _SENTINEL:
            if hasattr(api_v3, name):
                delattr(api_v3, name)
        else:
            setattr(api_v3, name, original)


class TestSaveRawMain:
    def test_writes_the_body_to_config_json(self, env):
        response = env.client.post(MAIN, json={"timezone": "America/Chicago"})
        assert response.status_code == 200
        assert json.loads(env.config_file.read_text()) == {"timezone": "America/Chicago"}

    def test_replaces_rather_than_merges(self, env):
        env.client.post(MAIN, json={"only": "this"})
        assert json.loads(env.config_file.read_text()) == {"only": "this"}

    def test_does_not_touch_the_secrets_file(self, env):
        env.secrets_file.write_text(json.dumps({"weather": {"api_key": "k"}}))
        env.client.post(MAIN, json={"timezone": "UTC"})
        assert json.loads(env.secrets_file.read_text()) == {"weather": {"api_key": "k"}}

    def test_uninitialized_manager_is_a_500(self, env):
        api_v3.config_manager = None
        response = env.client.post(MAIN, json={"timezone": "UTC"})
        assert response.status_code == 500
        assert "not initialized" in response.get_json()["message"]

    def test_empty_object_is_a_400(self, env):
        response = env.client.post(MAIN, json={})
        assert response.status_code == 400
        assert "No data provided" in response.get_json()["message"]

    def test_bodyless_post_is_a_400(self, env):
        response = env.client.post(MAIN)
        assert response.status_code == 400
        assert "No data provided" in response.get_json()["message"]

    def test_malformed_json_is_a_400_in_the_app_shape(self, env):
        response = env.client.post(MAIN, data="{not json",
                                   content_type="application/json")
        assert response.status_code == 400
        body = response.get_json()
        assert body["status"] == "error"
        # A body that was sent but does not parse is a distinct mistake
        # from sending none, and says so. Previously the handler's own
        # json.JSONDecodeError arm was unreachable — Werkzeug raised
        # first — so this collapsed into "No data provided".
        assert "Invalid JSON in request body" in body["message"]

    def test_config_error_is_a_500_with_context(self, env, monkeypatch):
        def refuse(kind, data):
            raise ConfigError("cannot write", config_path="/etc/x.json")
        monkeypatch.setattr(env.config_manager, "save_raw_file_content", refuse)
        response = env.client.post(MAIN, json={"timezone": "UTC"})
        assert response.status_code == 500
        assert "/etc/x.json" in json.dumps(response.get_json())

    def test_unexpected_error_is_a_500(self, env, monkeypatch):
        def boom(kind, data):
            raise RuntimeError("disk on fire")
        monkeypatch.setattr(env.config_manager, "save_raw_file_content", boom)
        response = env.client.post(MAIN, json={"timezone": "UTC"})
        assert response.status_code == 500
        assert response.get_json()["status"] == "error"


class TestSaveRawSecrets:
    def test_writes_only_to_the_secrets_file(self, env):
        response = env.client.post(SECRETS, json={"weather": {"api_key": "s3cret"}})
        assert response.status_code == 200
        assert json.loads(env.secrets_file.read_text()) == {"weather": {"api_key": "s3cret"}}

    def test_secret_values_never_reach_config_json(self, env):
        env.client.post(SECRETS, json={"weather": {"api_key": "s3cret"}})
        assert "s3cret" not in env.config_file.read_text()

    def test_existing_main_config_is_untouched(self, env):
        before = env.config_file.read_text()
        env.client.post(SECRETS, json={"weather": {"api_key": "k"}})
        assert env.config_file.read_text() == before

    def test_github_token_is_reloaded_for_the_store_manager(self, env):
        store = MagicMock()
        store._load_github_token.return_value = "ghp_new"
        api_v3.plugin_store_manager = store
        env.client.post(SECRETS, json={"github": {"token": "ghp_new"}})
        store._load_github_token.assert_called_once()
        assert store.github_token == "ghp_new"

    def test_absent_store_manager_is_fine(self, env):
        api_v3.plugin_store_manager = None
        assert env.client.post(SECRETS, json={"a": 1}).status_code == 200

    def test_uninitialized_manager_is_a_500(self, env):
        api_v3.config_manager = None
        assert env.client.post(SECRETS, json={"a": 1}).status_code == 500

    def test_empty_object_is_a_400(self, env):
        assert env.client.post(SECRETS, json={}).status_code == 400

    def test_bodyless_post_is_a_400(self, env):
        assert env.client.post(SECRETS).status_code == 400

    def test_error_is_a_500(self, env, monkeypatch):
        def boom(kind, data):
            raise RuntimeError("nope")
        monkeypatch.setattr(env.config_manager, "save_raw_file_content", boom)
        assert env.client.post(SECRETS, json={"a": 1}).status_code == 500


class TestRawEndpointsBypassSecretSeparation:
    """Pinned behaviour, deliberately not "fixed".

    These endpoints are the escape hatch for editing the config files
    directly from the web UI's raw JSON editor. They write what they are
    given, so a secret typed into the main-config editor lands in
    config.json in plain text — unlike /config/main and the plugin-config
    endpoints, which route x-secret fields into config_secrets.json.

    That is the point of a raw editor, but it is a sharp edge worth
    stating out loud: anyone adding a "convenience" that posts plugin
    config through this endpoint would silently lose secret separation.
    """

    def test_secret_shaped_keys_are_written_verbatim_to_main(self, env):
        env.client.post(MAIN, json={"weather": {"api_key": "PLAINTEXT-KEY"}})
        on_disk = json.loads(env.config_file.read_text())
        assert on_disk["weather"]["api_key"] == "PLAINTEXT-KEY"

    def test_no_separation_happens_on_the_raw_path(self, env):
        env.client.post(MAIN, json={"weather": {"api_key": "PLAINTEXT-KEY"}})
        # Nothing was moved aside into the secrets file.
        assert not env.secrets_file.exists() or "PLAINTEXT-KEY" not in env.secrets_file.read_text()
