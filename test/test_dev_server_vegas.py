"""scripts/dev_server.py's Vegas strip view, on the stub fixture plugin."""
import importlib.util
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
STUB = REPO / "test" / "fixtures" / "plugins" / "vegas-live-stub"


@pytest.fixture
def client(monkeypatch):
    spec = importlib.util.spec_from_file_location("dev_server_vegas_under_test",
                                                  REPO / "scripts" / "dev_server.py")
    dev_server = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(dev_server)
    monkeypatch.setattr(dev_server, "find_plugin_dir", lambda pid: STUB)
    monkeypatch.setattr(dev_server, "_trusted_plugin_dir", lambda d: STUB)
    return dev_server.app.test_client()


def _render(client, **extra):
    return client.post("/api/render", json={"plugin_id": "vegas-live-stub", "width": 192,
                                            "height": 48, "config": {"enabled": True},
                                            **extra})


def test_the_live_strip_lists_its_elements(client):
    data = _render(client, vegas="live").get_json()
    assert data["image"].startswith("data:image/png;base64,")
    assert data["height"] == 48 and data["width"] > 192
    keys = [e["key"] for e in data["live_elements"]]
    assert keys[:2] == ["card:0", "card:1"] and "map" in keys
    assert not data["errors"]


def test_the_plain_strip_has_no_live_elements(client):
    data = _render(client, vegas="plain").get_json()
    assert data["live_elements"] == [] and not data["errors"]


def test_the_display_view_is_unchanged(client):
    data = _render(client).get_json()
    assert (data["width"], data["height"]) == (192, 48)
    assert "live_elements" not in data


def test_an_unknown_view_is_refused(client):
    assert _render(client, vegas="sideways").status_code == 400
