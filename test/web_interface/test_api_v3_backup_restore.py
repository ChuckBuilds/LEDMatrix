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
    'config_manager', 'plugin_manager', 'plugin_store_manager',
    'plugin_state_manager', 'saved_repositories_manager', 'schema_manager',
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

    def to_dict(self):
        return {
            "success": self.success,
            "restored": self.restored,
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
        with patch("web_interface.blueprints.api_v3.plugin_store_manager", None):
            body = post(client).get_json()
        assert body["data"]["plugins_failed"][0]["error"] == "Store manager unavailable"


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
