"""GET /api/v3/config/refresh-rate: the cap, the measured rate, a cap to hold."""
import json
from unittest.mock import MagicMock

import pytest
from flask import Flask

from web_interface.blueprints.api_v3 import api_v3


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


def _get(client):
    body = client.get("/api/v3/config/refresh-rate").get_json()
    assert body["status"] == "success"
    return body["data"]


def test_nothing_measured_yet(client):
    data = _get(client)
    assert data == {"planned_hz": 120.0, "measured_hz": None, "shortfall": None}


def test_a_panel_short_of_its_cap_gets_a_cap_it_can_hold(client):
    client.stats_path.write_text(json.dumps(
        {"measured_refresh_hz": 110.4, "planned_refresh_hz": 120.0}))
    data = _get(client)
    assert data["measured_hz"] == 110.4
    assert data["shortfall"]["suggested_cap_hz"] == 100
    assert data["shortfall"]["slow_percent"] == 8


def test_a_panel_at_its_cap_has_no_shortfall(client):
    client.stats_path.write_text(json.dumps(
        {"measured_refresh_hz": 121.3, "planned_refresh_hz": 120.0}))
    assert _get(client)["shortfall"] is None


def test_a_file_written_under_another_cap_is_stale(client):
    # The cap was changed to 120 but the display still runs under 100 Hz.
    client.stats_path.write_text(json.dumps(
        {"measured_refresh_hz": 99.9, "planned_refresh_hz": 100.0}))
    data = _get(client)
    assert data["measured_hz"] is None
    assert data["shortfall"] is None


def test_a_file_from_a_display_too_old_to_record_its_cap_still_counts(client):
    client.stats_path.write_text(json.dumps({"measured_refresh_hz": 110.4}))
    assert _get(client)["shortfall"]["suggested_cap_hz"] == 100


def test_a_measurement_recorded_without_a_planned_rate_is_not_a_panel(client):
    # The emulator and the fallback canvas write the key as null: their frames
    # are not paced by a panel, so a rate under the cap is no shortfall.
    client.stats_path.write_text(json.dumps(
        {"measured_refresh_hz": 60.0, "planned_refresh_hz": None}))
    data = _get(client)
    assert data["measured_hz"] is None
    assert data["shortfall"] is None
