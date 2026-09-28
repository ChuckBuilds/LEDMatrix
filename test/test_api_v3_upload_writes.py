"""Plugin asset uploads and the calendar credentials upload write safely.

* An upload of several files checked and saved them one at a time, so a bad
  third file answered 400 after the first two were already on disk and in
  .metadata.json -- the user was told it failed and the images appeared anyway.
* .metadata.json and credentials.json were written in place (open 'w' /
  FileStorage.save), so a failure mid-write left a truncated file.
* The asset delete route called get_json() without silent=True.
* The credentials route returned the server's absolute path; nothing reads it.
"""

import io
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from test._api_v3_test_helpers import api_v3_client, api_v3_module  # noqa: F401,E402

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 20


@pytest.fixture
def project(tmp_path, api_v3_module, monkeypatch):
    import web_interface.blueprints.api_v3.plugin_assets as plugins_module
    monkeypatch.setattr(plugins_module, "PROJECT_ROOT", tmp_path)
    return tmp_path / "assets" / "plugins" / "static-image" / "uploads"


def _upload(client, files):
    return client.post(
        "/api/v3/plugins/assets/upload",
        data={"plugin_id": "static-image",
              "files": [(io.BytesIO(body), name) for name, body in files]},
        content_type="multipart/form-data",
    )


class TestAssetUpload:
    def test_a_bad_file_saves_none_of_the_batch(self, api_v3_client, project):
        response = _upload(api_v3_client, [
            ("a.png", PNG), ("b.png", PNG), ("c.png", b"not an image at all"),
        ])
        assert response.status_code == 400
        saved = [p.name for p in project.iterdir()] if project.exists() else []
        assert saved == [], "files before the bad one were saved anyway"

    def test_a_batch_over_the_total_limit_saves_none(self, api_v3_client, project, monkeypatch):
        # The total-size check also runs before anything is written.
        project.mkdir(parents=True)
        (project / ".metadata.json").write_text(json.dumps(
            {"old": {"id": "old", "size": 50 * 1024 * 1024 - 30}}), encoding="utf-8")
        response = _upload(api_v3_client, [("a.png", PNG), ("b.png", PNG)])
        assert response.status_code == 400
        assert sorted(p.name for p in project.iterdir()) == [".metadata.json"]

    def test_a_good_batch_is_all_saved_and_recorded(self, api_v3_client, project):
        response = _upload(api_v3_client, [("a.png", PNG), ("b.png", PNG)])
        assert response.status_code == 200, response.get_json()
        body = response.get_json()
        assert len(body["uploaded_files"]) == 2
        metadata = json.loads((project / ".metadata.json").read_text(encoding="utf-8"))
        assert sorted(metadata) == sorted(f["id"] for f in body["uploaded_files"])
        for entry in body["uploaded_files"]:
            assert (project / entry["filename"]).exists()

    def test_metadata_is_replaced_by_rename(self, api_v3_client, project, monkeypatch):
        import src.config_manager_atomic as atomic
        replaced = []
        real = atomic.os.replace
        monkeypatch.setattr(atomic.os, "replace",
                            lambda s, d: (replaced.append(Path(d).name), real(s, d)))
        _upload(api_v3_client, [("a.png", PNG)])
        assert ".metadata.json" in replaced


class TestAssetDelete:
    @pytest.mark.parametrize("kwargs", [
        {},                                             # no body at all
        {"data": "plugin_id=x", "content_type": "application/x-www-form-urlencoded"},
        {"json": ["plugin_id", "image_id"]},            # JSON, not an object
    ])
    def test_a_missing_or_non_object_body_is_a_400(self, api_v3_client, project, kwargs):
        response = api_v3_client.post("/api/v3/plugins/assets/delete", **kwargs)
        assert response.status_code == 400
        assert response.get_json()["status"] == "error"

    def test_metadata_is_replaced_by_rename(self, api_v3_client, project, monkeypatch):
        uploaded = _upload(api_v3_client, [("a.png", PNG)]).get_json()["uploaded_files"][0]
        import src.config_manager_atomic as atomic
        replaced = []
        real = atomic.os.replace
        monkeypatch.setattr(atomic.os, "replace",
                            lambda s, d: (replaced.append(Path(d).name), real(s, d)))
        response = api_v3_client.post("/api/v3/plugins/assets/delete",
                                      json={"plugin_id": "static-image",
                                            "image_id": uploaded["id"]})
        assert response.status_code == 200
        assert ".metadata.json" in replaced
        assert json.loads((project / ".metadata.json").read_text(encoding="utf-8")) == {}


class TestCalendarCredentials:
    CREDS = {"installed": {"client_id": "x", "client_secret": "y"}}

    @pytest.fixture
    def plugin_dir(self, tmp_path, api_v3_module):
        directory = tmp_path / "plugins" / "calendar"
        directory.mkdir(parents=True)
        api_v3_module.api_v3.plugin_manager.get_plugin_directory.return_value = str(directory)
        return directory

    def _post(self, client):
        return client.post(
            "/api/v3/plugins/calendar/upload-credentials",
            data={"file": (io.BytesIO(json.dumps(self.CREDS).encode()), "credentials.json")},
            content_type="multipart/form-data",
        )

    def test_the_absolute_server_path_is_not_returned(self, api_v3_client, plugin_dir):
        body = self._post(api_v3_client).get_json()
        assert body["status"] == "success"
        assert body["path"] == "credentials.json"
        assert str(plugin_dir) not in json.dumps(body)

    def test_the_file_is_replaced_by_rename(self, api_v3_client, plugin_dir, monkeypatch):
        (plugin_dir / "credentials.json").write_text(json.dumps({"installed": {"old": 1}}))
        import src.config_manager_atomic as atomic
        replaced = []
        real = atomic.os.replace
        monkeypatch.setattr(atomic.os, "replace",
                            lambda s, d: (replaced.append(Path(d).name), real(s, d)))
        assert self._post(api_v3_client).status_code == 200
        assert replaced == ["credentials.json"]
        assert json.loads((plugin_dir / "credentials.json").read_text()) == self.CREDS
