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

import html
import json
import re
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
    attrs = ('config_manager', 'plugin_catalog', 'plugin_store_manager',
             'saved_repositories_manager',
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

    @pytest.mark.parametrize("error,code", [
        (ConfigError("cannot write", config_path="/etc/s.json"), "CONFIG_SAVE_FAILED"),
        (RuntimeError("nope"), "UNKNOWN_ERROR"),
    ])
    def test_errors_answer_in_the_main_routes_shape(self, env, monkeypatch, error, code):
        # Both raw routes build their 500 with one helper now; this one used
        # to hand-roll a body without error_code or context. raw_json.html
        # reads only `message`, which both shapes carry.
        def boom(kind, data):
            raise error
        monkeypatch.setattr(env.config_manager, "save_raw_file_content", boom)
        secrets = env.client.post(SECRETS, json={"a": 1})
        main = env.client.post(MAIN, json={"a": 1})
        assert secrets.status_code == main.status_code == 500
        body = secrets.get_json()
        assert body["error_code"] == code
        assert body["message"] == main.get_json()["message"]
        assert set(body) == set(main.get_json())


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


class TestConfigEditorRoundTrip:
    """The Config Editor tab (/partials/raw-json) and the save it posts to.

    The secrets editor is shown masked, like GET /config/secrets: the page is
    served to anyone who can reach the port while the optional web login is
    off. Its save strips the masks and merges onto the stored file, so a
    masked editor saved back as it is changes nothing.
    """

    STORED = {
        "github": {"api_token": "ghp_REAL_TOKEN_1234"},
        "ledmatrix-weather": {"api_key": "WEATHER_KEY_abcdef", "units_id": 42},
        "calendar": {"accounts": [{"name": "home", "token": "CAL_TOKEN_9"}]},
        "youtube": {"api_key": "YOUR_YOUTUBE_API_KEY", "channel_secret": ""},
    }
    REAL_VALUES = ("ghp_REAL_TOKEN_1234", "WEATHER_KEY_abcdef", "CAL_TOKEN_9")

    @pytest.fixture
    def editor(self, env, monkeypatch):
        from web_interface.blueprints import pages_v3 as pages_module
        env.secrets_file.write_text(json.dumps(self.STORED))
        monkeypatch.setattr(pages_module.pages_v3, "config_manager",
                            env.config_manager, raising=False)
        app = Flask(__name__, template_folder=str(project_root / "web_interface" / "templates"))
        app.config["TESTING"] = True
        app.register_blueprint(pages_module.pages_v3)
        app.register_blueprint(api_v3, url_prefix="/api/v3")
        return app.test_client()

    @staticmethod
    def _secrets_textarea(client):
        page = client.get("/partials/raw-json")
        assert page.status_code == 200
        match = re.search(r'<textarea id="secrets-config-editor"[^>]*>(.*?)</textarea>',
                          page.get_data(as_text=True), re.S)
        assert match, "the secrets editor is missing from the partial"
        return html.unescape(match.group(1))

    def test_the_editor_shows_no_secret_value(self, editor):
        text = self._secrets_textarea(editor)
        for value in self.REAL_VALUES:
            assert value not in text
        shown = json.loads(text)
        assert shown["github"]["api_token"] == "\u2022" * 8
        # Same shape as the file, and "not set" still reads as not set.
        assert shown["calendar"]["accounts"][0]["name"] == "\u2022" * 8
        assert shown["youtube"] == {"api_key": "YOUR_YOUTUBE_API_KEY", "channel_secret": ""}

    def test_saving_it_back_unchanged_keeps_every_secret(self, editor, env):
        shown = json.loads(self._secrets_textarea(editor))
        response = editor.post(SECRETS, json=shown)
        assert response.status_code == 200
        assert json.loads(env.secrets_file.read_text()) == self.STORED

    def test_editing_one_secret_changes_only_that_one(self, editor, env):
        shown = json.loads(self._secrets_textarea(editor))
        shown["ledmatrix-weather"]["api_key"] = "NEW_WEATHER_KEY"
        assert editor.post(SECRETS, json=shown).status_code == 200
        expected = json.loads(json.dumps(self.STORED))
        expected["ledmatrix-weather"]["api_key"] = "NEW_WEATHER_KEY"
        assert json.loads(env.secrets_file.read_text()) == expected
