"""GET /api/v3/plugins/<plugin_id>/static/<path> serves binary files too.

The route opened every file as UTF-8 text, so an image -- what the API
reference says it is for, plugin previews and icons -- failed to decode and
answered 500 "UnicodeDecodeError". Files are now sent as bytes. The text
types the route always set are unchanged, and the path checks are pinned in
test_path_traversal_guards.py::TestServePluginStatic.
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from test._api_v3_test_helpers import api_v3_client, api_v3_module  # noqa: F401,E402

PNG = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
       b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89")


@pytest.fixture
def plugin_dir(tmp_path, api_v3_module):
    d = tmp_path / "demo"
    (d / "web_ui").mkdir(parents=True)
    (d / "manifest.json").write_text(json.dumps({"id": "demo"}), encoding="utf-8")
    api_v3_module.api_v3.plugin_catalog.get_plugin_directory.side_effect = (
        lambda pid: str(d) if pid == "demo" else None)
    return d


def _get(client, path):
    return client.get(f"/api/v3/plugins/demo/static/{path}")


def test_an_image_is_served_as_its_bytes(api_v3_client, plugin_dir):
    (plugin_dir / "web_ui" / "icon.png").write_bytes(PNG)
    response = _get(api_v3_client, "web_ui/icon.png")
    assert response.status_code == 200, response.get_json(silent=True)
    assert response.mimetype == "image/png"
    assert response.data == PNG


def test_an_unknown_binary_file_is_served_too(api_v3_client, plugin_dir):
    blob = bytes(range(256))
    (plugin_dir / "data.bin").write_bytes(blob)
    response = _get(api_v3_client, "data.bin")
    assert response.status_code == 200, response.get_json(silent=True)
    assert response.data == blob


@pytest.mark.parametrize("name,mimetype", [
    ("page.html", "text/html"),
    ("app.js", "application/javascript"),
    ("style.css", "text/css"),
    ("data.json", "application/json"),
    ("notes.txt", "text/plain"),
    ("README.md", "text/plain"),
    ("helper.py", "text/plain"),
])
def test_text_files_keep_their_types(api_v3_client, plugin_dir, name, mimetype):
    content = "caf\u00e9 \u2713 <p>hi</p>\n"
    (plugin_dir / name).write_bytes(content.encode("utf-8"))
    response = _get(api_v3_client, name)
    assert response.status_code == 200
    assert response.mimetype == mimetype
    assert response.data == content.encode("utf-8")


def test_a_missing_file_is_still_a_404(api_v3_client, plugin_dir):
    assert _get(api_v3_client, "nope.png").status_code == 404
