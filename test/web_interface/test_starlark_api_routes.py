"""Regression tests for the Starlark API routes used by the v3 plugin UI."""

import pytest
from flask import Flask

from web_interface.blueprints import api_v3 as mod


@pytest.fixture
def client(monkeypatch, tmp_path):
    apps_dir = tmp_path / "starlark-apps"
    monkeypatch.setattr(mod, "_STARLARK_APPS_DIR", apps_dir)
    monkeypatch.setattr(mod, "_STARLARK_MANIFEST_FILE", apps_dir / "manifest.json")
    monkeypatch.setattr(mod.api_v3, "plugin_manager", None, raising=False)

    app = Flask(__name__)
    app.config["TESTING"] = True
    app.register_blueprint(mod.api_v3, url_prefix="/api/v3")
    with app.test_client() as test_client:
        yield test_client


def test_all_documented_starlark_routes_are_registered():
    app = Flask(__name__)
    app.register_blueprint(mod.api_v3, url_prefix="/api/v3")
    rules = {(rule.rule, tuple(sorted(rule.methods - {"HEAD", "OPTIONS"}))) for rule in app.url_map.iter_rules()}

    expected = {
        ("/api/v3/starlark/status", ("GET",)),
        ("/api/v3/starlark/install-pixlet", ("POST",)),
        ("/api/v3/starlark/apps", ("GET",)),
        ("/api/v3/starlark/apps/<app_id>", ("GET",)),
        ("/api/v3/starlark/apps/<app_id>", ("DELETE",)),
        ("/api/v3/starlark/apps/<app_id>/config", ("GET",)),
        ("/api/v3/starlark/apps/<app_id>/config", ("PUT",)),
        ("/api/v3/starlark/apps/<app_id>/render", ("POST",)),
        ("/api/v3/starlark/apps/<app_id>/toggle", ("POST",)),
        ("/api/v3/starlark/repository/categories", ("GET",)),
        ("/api/v3/starlark/repository/browse", ("GET",)),
        ("/api/v3/starlark/repository/install", ("POST",)),
        ("/api/v3/starlark/upload", ("POST",)),
    }
    assert expected <= rules


def test_status_works_when_display_plugin_is_not_loaded(client):
    response = client.get("/api/v3/starlark/status")

    assert response.status_code == 200
    body = response.get_json()
    assert body["status"] == "success"
    assert body["installed_apps"] == 0
    assert body["enabled_apps"] == 0
    assert body["plugin_enabled"] is True
    assert "pixlet_available" in body


def test_apps_falls_back_to_the_standalone_manifest(client):
    assert mod._write_starlark_manifest({
        "apps": {"clock": {"name": "Clock", "enabled": True}}
    })

    response = client.get("/api/v3/starlark/apps")

    assert response.status_code == 200
    apps = response.get_json()["apps"]
    assert len(apps) == 1
    assert apps[0]["id"] == "clock"
    assert apps[0]["name"] == "Clock"
    assert apps[0]["enabled"] is True


def test_app_path_traversal_is_rejected(client):
    response = client.get("/api/v3/starlark/apps/..%5Csecret")

    assert response.status_code == 400
    assert "invalid app_id" in response.get_json()["message"].lower()
