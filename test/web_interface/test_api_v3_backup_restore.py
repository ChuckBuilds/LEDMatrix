"""
Endpoint tests for POST /backup/restore.

Restore is the most destructive operation the web interface exposes: it
overwrites config, secrets, WiFi settings and fonts, and reinstalls
plugins. It had no tests.

restore_backup itself is mocked — this file is about what the route does
with the request and with the result, not about ZIP handling, which
belongs to backup_manager's own tests.

Regression coverage for one fixed bug: a malformed `options` field fell
back to {}, and since every RestoreOptions flag defaults to True, that
turned a mis-serialized narrow restore into a full one — secrets
included — with no indication anything had been ignored.
"""

import io
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from flask import Flask

project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

from web_interface.blueprints.api_v3 import api_v3  # noqa: E402

URL = "/api/v3/backup/restore"

_MANAGER_ATTRS = (
    'config_manager', 'plugin_catalog', 'plugin_store_manager',
    'saved_repositories_manager', 'schema_manager',
    'operation_queue', 'operation_history', 'cache_manager',
)
_SENTINEL = object()


class FakeResult:
    """Stand-in for backup_manager.RestoreResult."""

    def __init__(self, success=True, restored=None, errors=None,
                 plugins_to_install=None):
        self.success = success
        self.restored = restored if restored is not None else ["config"]
        self.errors = errors or []
        self.plugins_to_install = plugins_to_install or []
        self.plugins_installed = []
        self.plugins_failed = []
        self.skipped = []

    def to_dict(self):
        return {
            "success": self.success,
            "restored": self.restored,
            "skipped": self.skipped,
            "errors": self.errors,
            "plugins_installed": self.plugins_installed,
            "plugins_failed": self.plugins_failed,
        }


@pytest.fixture
def client():
    originals = {name: getattr(api_v3, name, _SENTINEL) for name in _MANAGER_ATTRS}
    for name in _MANAGER_ATTRS:
        setattr(api_v3, name, MagicMock())

    app = Flask(__name__)
    app.config["TESTING"] = True
    app.register_blueprint(api_v3, url_prefix="/api/v3")
    yield app.test_client()

    for name, original in originals.items():
        if original is _SENTINEL:
            if hasattr(api_v3, name):
                delattr(api_v3, name)
        else:
            setattr(api_v3, name, original)


@pytest.fixture
def restore():
    """Patch backup_manager.restore_backup (imported inside the handler)."""
    with patch("src.backup_manager.restore_backup") as mock:
        mock.return_value = FakeResult()
        yield mock


def post(client, options=None, filename="backup.zip", content=b"PK\x03\x04fake"):
    data = {"backup_file": (io.BytesIO(content), filename)}
    if options is not None:
        data["options"] = options
    return client.post(URL, data=data, content_type="multipart/form-data")


class TestRequestValidation:
    def test_missing_file_is_a_400(self, client, restore):
        response = client.post(URL, data={}, content_type="multipart/form-data")
        assert response.status_code == 400
        assert "No backup_file" in response.get_json()["message"]
        restore.assert_not_called()

    def test_absent_options_defaults_to_a_full_restore(self, client, restore):
        # Documented default, not the bug: omitting options entirely means
        # "restore everything".
        post(client)
        options = restore.call_args[0][2]
        assert options.restore_config is True
        assert options.restore_secrets is True
        assert options.reinstall_plugins is True

    def test_partial_options_are_honoured(self, client, restore):
        post(client, options=json.dumps({
            "restore_secrets": False, "reinstall_plugins": False}))
        options = restore.call_args[0][2]
        assert options.restore_secrets is False
        assert options.reinstall_plugins is False
        assert options.restore_config is True  # unspecified stays default

    @pytest.mark.parametrize("raw", ["{not json", "", "{'single': 'quotes'}"])
    def test_malformed_options_are_refused(self, client, restore, raw):
        # Regression: this fell back to {}, and every flag defaults to
        # True, so a caller asking for a narrow restore and mis-serializing
        # it got a full one — secrets overwritten — and no warning.
        response = post(client, options=raw)
        assert response.status_code == 400
        assert "Invalid options" in response.get_json()["message"]
        restore.assert_not_called()

    @pytest.mark.parametrize("raw", ["[1,2,3]", '"a string"', "42", "true", "null"])
    def test_options_that_are_not_an_object_are_refused(self, client, restore, raw):
        response = post(client, options=raw)
        assert response.status_code == 400
        restore.assert_not_called()

    def test_empty_object_is_accepted_as_all_defaults(self, client, restore):
        assert post(client, options="{}").status_code == 200
        assert restore.call_args[0][2].restore_config is True

    def test_unknown_option_key_is_refused(self, client, restore):
        # Regression: opts_dict.get('restore_secrets', True) silently
        # ignores a typo'd/renamed key like "restoreSecrets" and keeps the
        # True default, restoring secrets a caller's request clearly meant
        # to exclude -- with no indication anything was wrong.
        response = post(client, options=json.dumps({"restoreSecrets": False}))
        assert response.status_code == 400
        assert "Unknown restore option" in response.get_json()["message"]
        assert "restoreSecrets" in response.get_json()["message"]
        restore.assert_not_called()

    def test_known_and_unknown_keys_together_are_refused(self, client, restore):
        response = post(client, options=json.dumps({
            "restore_secrets": False, "restore_everything": True}))
        assert response.status_code == 400
        restore.assert_not_called()


class TestOptionsAreBooleanAware:
    """Regression: bool("false") is True in Python.

    Every restore flag used bare bool() coercion, so a caller that sends its
    options as JSON strings rather than real booleans -- a form field, a
    hand-built request -- had `{"restore_secrets": "false"}` restore secrets
    anyway, the opposite of what was asked. Fixed with the same
    string-aware `_coerce_to_bool` already used for checkbox-style config
    fields elsewhere in this package (config.py, plugins.py).
    """

    @pytest.mark.parametrize("raw,expected", [
        ("false", False), ("False", False), ("FALSE", False),
        ("0", False),
        ("true", True), ("True", True), ("1", True),
    ])
    def test_string_valued_flags_are_parsed_not_just_truthy(
            self, client, restore, raw, expected):
        post(client, options=json.dumps({"restore_secrets": raw}))
        assert restore.call_args[0][2].restore_secrets is expected

    def test_a_string_false_does_not_restore_secrets(self, client, restore):
        # The exact shape of the bug: a truthy non-empty string coerced by
        # bare bool() to True regardless of its contents.
        post(client, options=json.dumps({"restore_secrets": "false"}))
        assert restore.call_args[0][2].restore_secrets is False

    def test_real_json_booleans_still_work(self, client, restore):
        post(client, options=json.dumps({"restore_secrets": False,
                                          "restore_config": True}))
        options = restore.call_args[0][2]
        assert options.restore_secrets is False
        assert options.restore_config is True


class TestSuccess:
    def test_success_returns_the_result(self, client, restore):
        restore.return_value = FakeResult(success=True, restored=["config", "secrets"])
        response = post(client)
        assert response.status_code == 200
        body = response.get_json()
        assert body["status"] == "success"
        assert body["data"]["restored"] == ["config", "secrets"]

    def test_temp_file_is_cleaned_up(self, client, restore):
        seen = {}

        def capture(path, project_root, options):
            seen["path"] = Path(path)
            assert seen["path"].exists()  # present while restoring
            return FakeResult()

        restore.side_effect = capture
        post(client)
        assert not seen["path"].exists()

    def test_temp_file_cleaned_up_even_when_restore_raises(self, client, restore):
        seen = {}

        def blow_up(path, project_root, options):
            seen["path"] = Path(path)
            raise RuntimeError("corrupt archive")

        restore.side_effect = blow_up
        response = post(client)
        assert response.status_code == 500
        assert not seen["path"].exists()


class TestPluginReinstall:
    def test_plugins_are_reinstalled_when_requested(self, client, restore):
        restore.return_value = FakeResult(
            plugins_to_install=[{"plugin_id": "clock"}, {"plugin_id": "weather"}])
        api_v3.plugin_store_manager.install_plugin.return_value = True
        response = post(client)
        assert response.status_code == 200
        assert response.get_json()["data"]["plugins_installed"] == ["clock", "weather"]

    def test_reinstall_skipped_when_not_requested(self, client, restore):
        restore.return_value = FakeResult(plugins_to_install=[{"plugin_id": "clock"}])
        post(client, options=json.dumps({"reinstall_plugins": False}))
        api_v3.plugin_store_manager.install_plugin.assert_not_called()

    def test_entries_without_a_plugin_id_are_skipped(self, client, restore):
        restore.return_value = FakeResult(plugins_to_install=[{}, {"plugin_id": "clock"}])
        api_v3.plugin_store_manager.install_plugin.return_value = True
        post(client)
        assert api_v3.plugin_store_manager.install_plugin.call_count == 1

    def test_failed_reinstall_turns_the_whole_restore_into_an_error(
            self, client, restore):
        # Pinned as intentional: file restoration succeeded and does not
        # touch result.errors, but a user whose plugins did not come back
        # should not be told the restore was a success.
        restore.return_value = FakeResult(
            success=True, plugins_to_install=[{"plugin_id": "clock"}])
        api_v3.plugin_store_manager.install_plugin.return_value = False
        response = post(client)
        assert response.status_code == 500
        body = response.get_json()
        assert body["status"] == "error"
        assert "clock" in body["message"]

    def test_message_names_what_landed_and_what_did_not(self, client, restore):
        restore.return_value = FakeResult(
            success=True, restored=["config", "fonts"],
            plugins_to_install=[{"plugin_id": "clock"}])
        api_v3.plugin_store_manager.install_plugin.return_value = False
        message = post(client).get_json()["message"]
        assert "restored: config, fonts" in message
        assert "plugins not reinstalled: clock" in message

    def test_install_exception_is_recorded_without_leaking_details(
            self, client, restore):
        restore.return_value = FakeResult(plugins_to_install=[{"plugin_id": "clock"}])
        api_v3.plugin_store_manager.install_plugin.side_effect = RuntimeError(
            "/srv/internal/path exploded")
        body = post(client).get_json()
        failures = body["data"]["plugins_failed"]
        assert failures[0]["plugin_id"] == "clock"
        assert "/srv/internal/path" not in json.dumps(body)

    def test_missing_store_manager_is_reported_per_plugin(self, client, restore):
        restore.return_value = FakeResult(plugins_to_install=[{"plugin_id": "clock"}])
        api_v3.plugin_store_manager = None
        body = post(client).get_json()
        assert body["data"]["plugins_failed"][0]["error"] == "Store manager unavailable"


class TestInstalledPluginsAreNotReinstalled:
    """"Reinstall missing plugins" installs only what is missing.

    Every plugin the backup listed went to install_plugin, which replaces an
    installed copy with a fresh download: restoring onto the same device
    re-downloaded all of them inside the request. One installed from its own
    URL is not in the registry, so its "reinstall" returned False and the
    whole restore answered 500 "Restore failed" with the plugin still there.
    """

    @staticmethod
    def _installed(tmp_path, *names):
        found = {}
        for name in names:
            (tmp_path / name).mkdir()
            found[name] = tmp_path / name
        return lambda plugin_id: found.get(plugin_id)

    def test_an_installed_plugin_is_skipped_and_a_missing_one_installed(
            self, client, restore, tmp_path):
        restore.return_value = FakeResult(
            plugins_to_install=[{"plugin_id": "clock"}, {"plugin_id": "weather"}])
        store = api_v3.plugin_store_manager
        store._existing_install.side_effect = self._installed(tmp_path, "clock")
        store.install_plugin.return_value = True
        response = post(client)
        assert response.status_code == 200
        store.install_plugin.assert_called_once_with("weather")
        data = response.get_json()["data"]
        assert data["plugins_installed"] == ["weather"]
        assert data["plugins_failed"] == []
        assert "plugin:clock (installed)" in data["skipped"]

    def test_an_installed_plugin_the_store_cannot_install_is_not_a_failure(
            self, client, restore, tmp_path):
        restore.return_value = FakeResult(plugins_to_install=[{"plugin_id": "my-3p"}])
        store = api_v3.plugin_store_manager
        store._existing_install.side_effect = self._installed(tmp_path, "my-3p")
        store.install_plugin.return_value = False
        response = post(client)
        assert response.status_code == 200
        assert response.get_json()["data"]["plugins_failed"] == []
        store.install_plugin.assert_not_called()

    @pytest.fixture
    def real_store(self, tmp_path):
        from src.plugin_system.store_manager import PluginStoreManager
        plugins_dir = tmp_path / "plugin-repos"
        for folder, manifest_id in (("ledmatrix-weather", "ledmatrix-weather"),
                                    ("my-3p", "my-3p")):
            (plugins_dir / folder).mkdir(parents=True)
            (plugins_dir / folder / "manifest.json").write_text(
                json.dumps({"id": manifest_id, "version": "1.0.0"}))
        store = PluginStoreManager(plugins_dir=str(plugins_dir),
                                   uninstalled_registry_path=str(tmp_path / "uninstalled.json"))
        # The official weather plugin's registry id differs from the id it
        # installs under; my-3p was installed from its own URL.
        registry = {"plugins": [{
            "id": "weather", "repo": "https://github.com/ChuckBuilds/ledmatrix-plugins",
            "plugin_path": "plugins/ledmatrix-weather"}]}
        store.registry_cache = registry
        store.fetch_registry = lambda *a, **k: registry
        store.install_plugin = MagicMock(return_value=True)
        api_v3.plugin_store_manager = store
        return store

    def test_with_the_real_store_aliases_and_third_party_installs_count(
            self, client, restore, real_store):
        restore.return_value = FakeResult(plugins_to_install=[
            {"plugin_id": "weather"}, {"plugin_id": "my-3p"}, {"plugin_id": "clock"}])
        response = post(client)
        assert response.status_code == 200
        real_store.install_plugin.assert_called_once_with("clock")
        skipped = response.get_json()["data"]["skipped"]
        assert "plugin:weather (installed)" in skipped
        assert "plugin:my-3p (installed)" in skipped


class TestFontsCatalogCache:
    """The Fonts tab's catalog is cached for 5 minutes (fonts.py).

    Upload and delete clear it; a restore did not, so restored fonts were
    missing from the Fonts tab and every font picker until it expired.
    """

    @pytest.fixture
    def cached_catalog(self):
        from web_interface.cache import delete_cached, get_cached, set_cached
        set_cached('fonts_catalog', {'fonts': ['5x7.bdf']}, ttl_seconds=300)
        yield lambda: get_cached('fonts_catalog', ttl_seconds=300)
        delete_cached('fonts_catalog')

    def test_a_restore_that_restored_fonts_clears_it(self, client, restore, cached_catalog):
        restore.return_value = FakeResult(restored=["config", "fonts (2)"])
        assert post(client).status_code == 200
        assert cached_catalog() is None

    def test_a_restore_without_fonts_keeps_it(self, client, restore, cached_catalog):
        restore.return_value = FakeResult(restored=["config"])
        assert post(client).status_code == 200
        assert cached_catalog() == {'fonts': ['5x7.bdf']}


class TestFailureReporting:
    def test_restore_errors_produce_a_500(self, client, restore):
        restore.return_value = FakeResult(
            success=False, restored=[], errors=["config: permission denied"])
        response = post(client)
        assert response.status_code == 500
        assert "permission denied" in response.get_json()["message"]

    def test_partial_restore_names_both_sides(self, client, restore):
        restore.return_value = FakeResult(
            success=False, restored=["config"], errors=["secrets: unwritable"])
        message = post(client).get_json()["message"]
        assert "restored: config" in message
        assert "failed: secrets: unwritable" in message

    def test_failure_without_detail_still_says_something(self, client, restore):
        restore.return_value = FakeResult(success=False, restored=[], errors=[])
        message = post(client).get_json()["message"]
        assert "Restore incomplete" in message

    def test_unexpected_exception_is_a_500(self, client, restore):
        restore.side_effect = RuntimeError("boom")
        response = post(client)
        assert response.status_code == 500
        assert response.get_json()["status"] == "error"
