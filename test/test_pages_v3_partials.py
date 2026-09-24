"""/partials/<name> dispatch and the plugin web_ui page's directory lookup."""

import logging
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from flask import Flask

sys.path.insert(0, str(Path(__file__).parent.parent))

from web_interface.blueprints import pages_v3 as module  # noqa: E402


@pytest.fixture
def client(tmp_path, monkeypatch):
    plugin_manager = MagicMock()
    plugin_manager.plugins_dir = tmp_path
    monkeypatch.setattr(module.pages_v3, "plugin_manager", plugin_manager, raising=False)
    monkeypatch.setattr(module.pages_v3, "config_manager",
                        MagicMock(load_config=lambda: {}), raising=False)
    app = Flask(__name__, template_folder=str(
        Path(module.__file__).resolve().parents[1] / "templates"))
    app.register_blueprint(module.pages_v3)
    return app.test_client()


def test_every_tab_the_old_if_chain_served_is_still_served():
    assert set(module._PARTIAL_LOADERS) == {
        "overview", "general", "display", "durations", "schedule", "plugins",
        "fonts", "logs", "raw-json", "backup-restore", "wifi", "cache",
        "operation-history", "tools",
    }


def test_an_unknown_partial_is_a_404(client):
    response = client.get("/partials/nope")
    assert response.status_code == 404


def test_a_failing_loader_is_one_500_that_names_the_partial(client, monkeypatch, caplog):
    def boom():
        raise RuntimeError("template exploded")
    monkeypatch.setitem(module._PARTIAL_LOADERS, "logs", boom)

    with caplog.at_level(logging.ERROR):
        response = client.get("/partials/logs")

    assert response.status_code == 500
    assert response.get_data(as_text=True) == "Error loading partial"
    errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert len(errors) == 1
    assert errors[0].getMessage() == "Error loading partial logs"


def test_web_ui_page_uses_the_ledmatrix_prefix_fallback(client, tmp_path):
    web_ui = tmp_path / "ledmatrix-radar" / "web_ui"
    web_ui.mkdir(parents=True)
    (web_ui / "panel.html").write_text("<p>radar panel</p>", encoding="utf-8")

    response = client.get("/plugin-ui/radar/web-ui/panel.html")

    assert response.status_code == 200
    assert "radar panel" in response.get_data(as_text=True)
