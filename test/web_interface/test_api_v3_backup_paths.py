"""
Path-containment tests for the backup file routes:
GET /backup/download/<filename>, DELETE /backup/<filename>, and the
listing/validation routes alongside them.

Both filename routes take user input straight from the URL and turn it
into a filesystem path, one to read and one to unlink. `_safe_backup_path`
is what stops that from reaching outside the export directory, and it had
no tests.

This is verification of existing containment, not a fix: no bypass was
found. The tests exist so that a later "just let dots through" change has
to argue with something.
"""

import io
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from flask import Flask

project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

from web_interface.blueprints import api_v3 as api_v3_module  # noqa: E402
from web_interface.blueprints.api_v3 import api_v3  # noqa: E402

_MANAGER_ATTRS = (
    'config_manager', 'plugin_manager', 'plugin_store_manager',
    'plugin_state_manager', 'saved_repositories_manager', 'schema_manager',
    'operation_queue', 'operation_history', 'cache_manager',
)
_SENTINEL = object()

# Anything that tries to name a file outside the export directory, or that
# is not a plain <name>.zip.
TRAVERSAL_ATTEMPTS = [
    "../../etc/passwd",
    "../config.json",
    "..%2f..%2fetc%2fpasswd",
    "....//....//etc/passwd",
    "/etc/passwd",
    "..\\..\\config.json",
    "backup.zip/../../../etc/passwd",
    ".hidden.zip",
    "backup.txt",
    "backup.zip.exe",
    "",
    ".",
    "..",
]


@pytest.fixture
def env(tmp_path, monkeypatch):
    export_dir = tmp_path / "backups"
    export_dir.mkdir()
    monkeypatch.setattr(api_v3_module, "_BACKUP_EXPORT_DIR", export_dir)

    # A file outside the export dir that a traversal would be reaching for.
    secret = tmp_path / "config.json"
    secret.write_text(json.dumps({"secret": "do not touch"}))

    originals = {name: getattr(api_v3, name, _SENTINEL) for name in _MANAGER_ATTRS}
    for name in _MANAGER_ATTRS:
        setattr(api_v3, name, MagicMock())

    app = Flask(__name__)
    app.config["TESTING"] = True
    app.register_blueprint(api_v3, url_prefix="/api/v3")

    class Env:
        pass

    e = Env()
    e.client = app.test_client()
    e.export_dir = export_dir
    e.secret = secret
    yield e

    for name, original in originals.items():
        if original is _SENTINEL:
            if hasattr(api_v3, name):
                delattr(api_v3, name)
        else:
            setattr(api_v3, name, original)


def make_backup(export_dir, name="backup-2026-01-01.zip"):
    path = export_dir / name
    path.write_bytes(b"PK\x03\x04fake zip")
    return path


class TestSafeBackupPath:
    """The containment helper itself."""

    @pytest.mark.parametrize("filename", TRAVERSAL_ATTEMPTS)
    def test_rejects_unsafe_names(self, env, filename):
        assert api_v3_module._safe_backup_path(filename) is None

    def test_rejects_none(self, env):
        assert api_v3_module._safe_backup_path(None) is None

    @pytest.mark.parametrize("filename", [
        "backup.zip",
        "backup-2026-01-01.zip",
        "backup_2026.01.01-v2.zip",
        "a.zip",
    ])
    def test_accepts_plain_zip_names(self, env, filename):
        resolved = api_v3_module._safe_backup_path(filename)
        assert resolved is not None
        assert resolved.parent == env.export_dir.resolve()

    def test_result_is_always_inside_the_export_dir(self, env):
        resolved = api_v3_module._safe_backup_path("backup.zip")
        resolved.relative_to(env.export_dir.resolve())  # raises if outside

    def test_overlong_name_rejected(self, env):
        assert api_v3_module._safe_backup_path("a" * 250 + ".zip") is None


class TestDownload:
    def test_downloads_an_existing_backup(self, env):
        make_backup(env.export_dir)
        response = env.client.get("/api/v3/backup/download/backup-2026-01-01.zip")
        assert response.status_code == 200
        assert response.data == b"PK\x03\x04fake zip"

    def test_missing_file_is_a_404(self, env):
        response = env.client.get("/api/v3/backup/download/never-made.zip")
        assert response.status_code == 404

    @pytest.mark.parametrize("filename", TRAVERSAL_ATTEMPTS)
    def test_traversal_attempts_are_refused(self, env, filename):
        response = env.client.get(f"/api/v3/backup/download/{filename}")
        # However the request is turned away — 404 from the containment
        # check, or 308/405 from routing never matching at all — what
        # matters is that no file outside the export directory is served.
        assert response.status_code != 200
        assert b"do not touch" not in response.data


class TestDelete:
    def test_deletes_an_existing_backup(self, env):
        path = make_backup(env.export_dir)
        response = env.client.delete("/api/v3/backup/backup-2026-01-01.zip")
        assert response.status_code == 200
        assert not path.exists()

    def test_missing_file_is_a_404(self, env):
        response = env.client.delete("/api/v3/backup/never-made.zip")
        assert response.status_code == 404

    @pytest.mark.parametrize("filename", TRAVERSAL_ATTEMPTS)
    def test_traversal_attempts_delete_nothing(self, env, filename):
        response = env.client.delete(f"/api/v3/backup/{filename}")
        assert response.status_code != 200
        assert env.secret.exists()  # the file a traversal was aiming at

    def test_only_the_named_backup_is_removed(self, env):
        keep = make_backup(env.export_dir, "keep.zip")
        drop = make_backup(env.export_dir, "drop.zip")
        env.client.delete("/api/v3/backup/drop.zip")
        assert keep.exists()
        assert not drop.exists()

    def test_directory_with_a_matching_name_is_not_removed(self, env):
        # The delete loop matches by name but requires a regular file.
        (env.export_dir / "sneaky.zip").mkdir()
        response = env.client.delete("/api/v3/backup/sneaky.zip")
        assert response.status_code == 404
        assert (env.export_dir / "sneaky.zip").is_dir()


class TestList:
    def test_lists_only_zip_files(self, env):
        make_backup(env.export_dir, "one.zip")
        (env.export_dir / "notes.txt").write_text("ignore me")
        response = env.client.get("/api/v3/backup/list")
        assert response.status_code == 200
        names = [entry["filename"] for entry in response.get_json()["data"]]
        assert names == ["one.zip"]

    def test_empty_directory_lists_nothing(self, env):
        response = env.client.get("/api/v3/backup/list")
        assert response.get_json()["data"] == []

    def test_entries_carry_size_and_timestamp(self, env):
        make_backup(env.export_dir, "one.zip")
        entry = env.client.get("/api/v3/backup/list").get_json()["data"][0]
        assert entry["size"] == len(b"PK\x03\x04fake zip")
        assert entry["created_at"]


class TestValidate:
    def test_missing_file_is_a_400(self, env):
        response = env.client.post("/api/v3/backup/validate", data={},
                                   content_type="multipart/form-data")
        assert response.status_code == 400
        assert "No backup_file" in response.get_json()["message"]

    def test_invalid_archive_is_a_400(self, env):
        response = env.client.post(
            "/api/v3/backup/validate",
            data={"backup_file": (io.BytesIO(b"not a zip"), "bad.zip")},
            content_type="multipart/form-data")
        assert response.status_code == 400
        assert "Invalid or corrupted" in response.get_json()["message"]

    def test_validation_does_not_leave_temp_files_in_the_export_dir(self, env):
        env.client.post(
            "/api/v3/backup/validate",
            data={"backup_file": (io.BytesIO(b"not a zip"), "bad.zip")},
            content_type="multipart/form-data")
        assert list(env.export_dir.iterdir()) == []
