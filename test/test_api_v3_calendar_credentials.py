"""
Endpoint tests for POST /plugins/calendar/upload-credentials.

The endpoint takes an uploaded Google OAuth credentials file, writes it
into the calendar plugin's directory as credentials.json at mode 0600, and
copies any previous file aside first. It had no tests.

Regression coverage for two fixed bugs:
- The OAuth-shape check sat inside `except Exception: pass`, so a valid
  JSON document that is not an object — a bare `42`, a list, a string —
  raised TypeError on the membership test, was swallowed, and got saved
  as credentials.json anyway.
- Each overwrite created a timestamped backup and nothing ever removed
  them, so every re-upload left another complete copy of the user's OAuth
  client credentials in the plugin directory, indefinitely.
"""

import io
import json
import os
import stat
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from test._api_v3_test_helpers import api_v3_client, api_v3_module  # noqa: F401,E402

URL = "/api/v3/plugins/calendar/upload-credentials"

VALID_CREDENTIALS = {
    "installed": {
        "client_id": "abc.apps.googleusercontent.com",
        "client_secret": "shh",
        "redirect_uris": ["http://localhost"],
    }
}


@pytest.fixture
def plugin_dir(tmp_path, api_v3_module):
    directory = tmp_path / "plugins" / "calendar"
    directory.mkdir(parents=True)
    api_v3_module.api_v3.plugin_manager.get_plugin_directory.return_value = str(directory)
    return directory


def upload(client, content, filename="credentials.json"):
    # bytes are sent verbatim (to exercise malformed input); anything else
    # is serialized, so None becomes the JSON literal null rather than an
    # empty body.
    payload = content if isinstance(content, bytes) else json.dumps(content).encode()
    return client.post(
        URL,
        data={"file": (io.BytesIO(payload), filename)},
        content_type="multipart/form-data",
    )


def backups(plugin_dir):
    return sorted(plugin_dir.glob("credentials.json.backup.*"))


class TestRequestValidation:
    def test_no_file_part_is_a_400(self, api_v3_client, plugin_dir):
        response = api_v3_client.post(URL, data={}, content_type="multipart/form-data")
        assert response.status_code == 400
        assert "No file provided" in response.get_json()["message"]

    def test_empty_filename_is_a_400(self, api_v3_client, plugin_dir):
        response = upload(api_v3_client, VALID_CREDENTIALS, filename="")
        assert response.status_code == 400

    @pytest.mark.parametrize("filename", ["creds.txt", "creds.pem", "creds"])
    def test_non_json_extension_is_a_400(self, api_v3_client, plugin_dir, filename):
        response = upload(api_v3_client, VALID_CREDENTIALS, filename=filename)
        assert response.status_code == 400
        assert "JSON file" in response.get_json()["message"]

    def test_uppercase_json_extension_accepted(self, api_v3_client, plugin_dir):
        assert upload(api_v3_client, VALID_CREDENTIALS,
                      filename="CREDENTIALS.JSON").status_code == 200

    def test_oversized_file_is_a_400(self, api_v3_client, plugin_dir):
        response = upload(api_v3_client, b"x" * (1024 * 1024 + 1))
        assert response.status_code == 400
        assert "1MB" in response.get_json()["message"]
        assert not (plugin_dir / "credentials.json").exists()

    def test_invalid_json_is_a_400(self, api_v3_client, plugin_dir):
        response = upload(api_v3_client, b"{not json")
        assert response.status_code == 400
        assert "not valid JSON" in response.get_json()["message"]
        assert not (plugin_dir / "credentials.json").exists()

    def test_missing_plugin_directory_is_a_404(self, api_v3_client, api_v3_module, tmp_path):
        api_v3_module.api_v3.plugin_manager.get_plugin_directory.return_value = str(
            tmp_path / "not-installed")
        assert upload(api_v3_client, VALID_CREDENTIALS).status_code == 404


class TestOAuthShapeValidation:
    def test_installed_key_accepted(self, api_v3_client, plugin_dir):
        assert upload(api_v3_client, VALID_CREDENTIALS).status_code == 200

    def test_web_key_accepted(self, api_v3_client, plugin_dir):
        assert upload(api_v3_client, {"web": {"client_id": "x"}}).status_code == 200

    def test_object_without_oauth_keys_is_a_400(self, api_v3_client, plugin_dir):
        response = upload(api_v3_client, {"something": "else"})
        assert response.status_code == 400
        assert "valid Google OAuth" in response.get_json()["message"]
        assert not (plugin_dir / "credentials.json").exists()

    @pytest.mark.parametrize("content", [42, "a string", [1, 2, 3], True, None])
    def test_valid_json_that_is_not_an_object_is_rejected(
            self, api_v3_client, plugin_dir, content):
        # Regression: `'installed' not in 42` raises TypeError, which the
        # bare `except Exception: pass` swallowed — the file was then saved
        # as credentials.json despite being unusable as credentials.
        response = upload(api_v3_client, content)
        assert response.status_code == 400
        assert "valid Google OAuth" in response.get_json()["message"]
        assert not (plugin_dir / "credentials.json").exists()


class TestSaving:
    def test_file_written_with_contents_intact(self, api_v3_client, plugin_dir):
        response = upload(api_v3_client, VALID_CREDENTIALS)
        assert response.status_code == 200
        saved = json.loads((plugin_dir / "credentials.json").read_text())
        assert saved == VALID_CREDENTIALS

    def test_response_reports_the_path(self, api_v3_client, plugin_dir):
        body = upload(api_v3_client, VALID_CREDENTIALS).get_json()
        assert body["path"].endswith("credentials.json")

    def test_permissions_are_owner_only(self, api_v3_client, plugin_dir):
        upload(api_v3_client, VALID_CREDENTIALS)
        mode = stat.S_IMODE((plugin_dir / "credentials.json").stat().st_mode)
        assert mode == 0o600

    def test_first_upload_creates_no_backup(self, api_v3_client, plugin_dir):
        upload(api_v3_client, VALID_CREDENTIALS)
        assert backups(plugin_dir) == []

    def test_overwrite_backs_up_the_previous_file(self, api_v3_client, plugin_dir):
        (plugin_dir / "credentials.json").write_text(json.dumps({"installed": {"old": 1}}))
        upload(api_v3_client, VALID_CREDENTIALS)
        assert len(backups(plugin_dir)) == 1
        assert json.loads(backups(plugin_dir)[0].read_text()) == {"installed": {"old": 1}}
        assert json.loads((plugin_dir / "credentials.json").read_text()) == VALID_CREDENTIALS


class TestBackupPruning:
    def _seed(self, plugin_dir, count):
        """Create `count` backups with distinct, increasing mtimes."""
        now = int(time.time())
        for i in range(count):
            path = plugin_dir / f"credentials.json.backup.{now - (count - i) * 10}"
            path.write_text(json.dumps({"installed": {"gen": i}}))
            os.utime(path, (now - (count - i) * 10, now - (count - i) * 10))

    def test_old_backups_are_pruned(self, api_v3_client, plugin_dir):
        # Regression: nothing ever removed these, so a plugin directory
        # accumulated one full copy of the user's OAuth credentials per
        # re-upload, forever.
        (plugin_dir / "credentials.json").write_text(json.dumps({"installed": {"cur": 1}}))
        self._seed(plugin_dir, 7)
        assert len(backups(plugin_dir)) == 7

        upload(api_v3_client, VALID_CREDENTIALS)
        assert len(backups(plugin_dir)) == 5

    def test_the_newest_backups_are_the_ones_kept(self, api_v3_client, plugin_dir):
        (plugin_dir / "credentials.json").write_text(json.dumps({"installed": {"cur": 1}}))
        self._seed(plugin_dir, 7)

        upload(api_v3_client, VALID_CREDENTIALS)
        remaining = backups(plugin_dir)
        # The just-created backup (of "cur") plus the four newest seeds.
        contents = [json.loads(p.read_text()) for p in remaining]
        assert {"installed": {"cur": 1}} in contents
        assert {"installed": {"gen": 0}} not in contents  # oldest seed gone

    def test_under_the_limit_nothing_is_removed(self, api_v3_client, plugin_dir):
        (plugin_dir / "credentials.json").write_text(json.dumps({"installed": {"cur": 1}}))
        self._seed(plugin_dir, 2)
        upload(api_v3_client, VALID_CREDENTIALS)
        assert len(backups(plugin_dir)) == 3  # 2 seeded + 1 new

    def test_repeated_uploads_stay_bounded(self, api_v3_client, plugin_dir):
        for i in range(10):
            upload(api_v3_client, {"installed": {"round": i}})
            # Distinct mtimes so ordering is well-defined between rounds.
            for path in backups(plugin_dir):
                os.utime(path, (path.stat().st_mtime, path.stat().st_mtime))
            time.sleep(0.01)
        assert len(backups(plugin_dir)) <= 5

    def test_unremovable_backup_does_not_fail_the_upload(
            self, api_v3_client, plugin_dir, monkeypatch):
        (plugin_dir / "credentials.json").write_text(json.dumps({"installed": {"cur": 1}}))
        self._seed(plugin_dir, 7)

        def refuse(self):
            raise OSError("read-only filesystem")
        monkeypatch.setattr(Path, "unlink", refuse)

        # Pruning is housekeeping; failing it must not lose the upload.
        assert upload(api_v3_client, VALID_CREDENTIALS).status_code == 200
