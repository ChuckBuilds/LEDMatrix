"""The stdlib gzip fallback must compress the UI's text assets.

flask-compress is optional (app.py tolerates its absence). Without a fallback a
Pi missing it ships ~1.2 MB of uncompressed JavaScript to a phone over WiFi on
every first load and every update. These pin the fallback's contract: text
assets compress and round-trip byte-for-byte, clients that don't ask for gzip
get the original bytes, and small or non-text responses are left alone.
"""

import gzip
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parents[2] / "web_interface" / "static"


@pytest.fixture
def client(monkeypatch):
    import web_interface.app as web_app
    # Exercise the fallback even on machines that do have flask-compress.
    monkeypatch.setattr(web_app, "_HAVE_FLASK_COMPRESS", False)
    web_app.app.config["TESTING"] = True
    with web_app.app.test_client() as c:
        yield c


@pytest.mark.parametrize("url,path", [
    ("/static/v3/plugins_manager.js", "v3/plugins_manager.js"),
    ("/static/v3/app.css", "v3/app.css"),
])
def test_static_text_asset_is_gzipped_and_round_trips(client, url, path):
    resp = client.get(url, headers={"Accept-Encoding": "gzip, deflate"})
    assert resp.status_code == 200
    assert resp.headers.get("Content-Encoding") == "gzip"
    assert "Accept-Encoding" in resp.headers.get("Vary", "")
    original = (STATIC / path).read_bytes()
    assert gzip.decompress(resp.data) == original
    assert len(resp.data) < len(original) / 2
    assert int(resp.headers["Content-Length"]) == len(resp.data)


def test_repeat_request_serves_identical_compressed_bytes(client):
    headers = {"Accept-Encoding": "gzip"}
    first = client.get("/static/v3/js/app-shell.js", headers=headers).data
    second = client.get("/static/v3/js/app-shell.js", headers=headers).data
    assert first == second


def test_client_without_gzip_gets_the_original_bytes(client):
    resp = client.get("/static/v3/app.css", headers={"Accept-Encoding": "identity"})
    assert resp.status_code == 200
    assert "Content-Encoding" not in resp.headers
    assert resp.data == (STATIC / "v3/app.css").read_bytes()


def test_binary_assets_are_not_recompressed(client):
    resp = client.get("/static/v3/icons/icon-192.png", headers={"Accept-Encoding": "gzip"})
    assert resp.status_code == 200
    assert "Content-Encoding" not in resp.headers


def test_widget_bundle_is_gzipped_and_immutable(client):
    resp = client.get("/assets/widgets.js", headers={"Accept-Encoding": "gzip"})
    assert resp.status_code == 200
    assert resp.headers.get("Content-Encoding") == "gzip"
    assert "immutable" in resp.headers.get("Cache-Control", "")
    assert b"/* registry.js */" in gzip.decompress(resp.data)
