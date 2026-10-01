"""GET /api/v3/config/scroll-speed-advice: what the panel does with a speed."""
import json
from unittest.mock import MagicMock

import pytest
from flask import Flask

from web_interface.blueprints.api_v3 import api_v3
from web_interface.blueprints.api_v3 import config as config_routes


@pytest.fixture
def client(monkeypatch, tmp_path):
    stats = tmp_path / "stats.json"
    monkeypatch.setattr("src.common.frame_timing.default_stats_path", lambda: str(stats))
    manager = MagicMock()
    manager.load_config.return_value = {
        "display": {"hardware": {"limit_refresh_rate_hz": 120}}}
    monkeypatch.setattr(api_v3, "config_manager", manager, raising=False)
    app = Flask(__name__)
    app.register_blueprint(api_v3, url_prefix="/api/v3")
    c = app.test_client()
    c.stats_path = stats
    return c


def test_uses_the_configured_cap_without_a_measurement(client):
    body = client.get("/api/v3/config/scroll-speed-advice?speed=50").get_json()
    data = body["data"]
    assert data["refresh_source"] == "configured"
    assert data["refresh_hz"] == 120.0
    assert [a["pixels_per_second"] for a in data["alternatives"]] == [40.0, 60.0]


def test_prefers_the_measured_refresh(client):
    client.stats_path.write_text(json.dumps({"measured_refresh_hz": 125.74}))
    data = client.get("/api/v3/config/scroll-speed-advice?speed=50").get_json()["data"]
    assert data["refresh_source"] == "measured"
    assert data["refresh_hz"] == 125.7


def test_ignores_an_implausible_stale_measurement(client):
    client.stats_path.write_text(json.dumps({"measured_refresh_hz": 40.0}))
    data = client.get("/api/v3/config/scroll-speed-advice?speed=50").get_json()["data"]
    assert data["refresh_source"] == "configured"


def test_rejects_a_non_numeric_speed(client):
    assert client.get("/api/v3/config/scroll-speed-advice?speed=fast").status_code == 400
