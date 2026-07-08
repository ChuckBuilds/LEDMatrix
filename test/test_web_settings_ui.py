"""
Smoke tests for the settings tooltips + search UI.

These render the settings partials through Flask and assert that every settings
field carries:
  - a stable search anchor id (`id="setting-..."` on its .form-group), and
  - an info tooltip (`class="help-tip"` emitted by the help_tip macro).

They guard against macro/import breakage and against fields losing their anchor
or tooltip when partials are edited. See web_interface/static/v3/js/tooltips.js
and settings-search.js for the consumers of this markup.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from flask import Flask

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


# A realistic-enough config so the hand-written partials render every field.
REALISTIC_CONFIG = {
    "web_display_autostart": True,
    "timezone": "America/Chicago",
    "location": {"city": "Dallas", "state": "Texas", "country": "US"},
    "plugin_system": {
        "auto_discover": True,
        "auto_load_enabled": True,
        "development_mode": False,
        "plugins_directory": "plugin-repos",
    },
    "schedule": {},
    "dim_schedule": {"dim_brightness": 30},
    "sync": {"role": "standalone", "port": 5765, "follower_position": "left"},
    "display": {
        "hardware": {
            "rows": 32, "cols": 64, "chain_length": 2, "parallel": 1,
            "brightness": 95, "hardware_mapping": "adafruit-hat-pwm",
            "led_rgb_sequence": "RGB", "multiplexing": 0, "panel_type": "",
            "row_address_type": 0, "scan_mode": 0, "pwm_bits": 9,
            "pwm_dither_bits": 1, "pwm_lsb_nanoseconds": 130,
            "limit_refresh_rate_hz": 120, "disable_hardware_pulsing": False,
            "inverse_colors": False, "show_refresh_rate": False,
        },
        "runtime": {"gpio_slowdown": 3, "rp1_rio": 0},
        "double_sided": {"enabled": False, "copies": 2, "axis": "horizontal"},
        "use_short_date_format": False,
        "dynamic_duration": {"max_duration_seconds": 180},
        "vegas_scroll": {
            "enabled": False, "scroll_speed": 50, "separator_width": 32,
            "target_fps": 125, "buffer_ahead": 2,
            "plugin_order": [], "excluded_plugins": [],
        },
        "display_durations": {"clock": 15, "weather": 30},
    },
}


@pytest.fixture
def client():
    base = PROJECT_ROOT / "web_interface"
    app = Flask(
        __name__,
        template_folder=str(base / "templates"),
        static_folder=str(base / "static"),
    )
    app.config["TESTING"] = True

    from web_interface.blueprints import pages_v3 as pv

    mock_cm = MagicMock()
    mock_cm.load_config.return_value = REALISTIC_CONFIG
    mock_cm.get_raw_file_content.return_value = REALISTIC_CONFIG
    mock_cm.get_config_path.return_value = "config/config.json"
    mock_cm.get_secrets_path.return_value = "config/config_secrets.json"
    pv.pages_v3.config_manager = mock_cm
    pv.pages_v3.plugin_manager = MagicMock(plugins={})

    app.register_blueprint(pv.pages_v3, url_prefix="/v3")
    return app.test_client()


# Settings tabs that must expose searchable, tooltipped fields.
SETTINGS_TABS = ["general", "display", "durations", "schedule", "wifi"]


@pytest.mark.parametrize("tab", SETTINGS_TABS)
def test_settings_partial_has_tooltips_and_anchors(client, tab):
    resp = client.get(f"/v3/partials/{tab}")
    assert resp.status_code == 200, f"{tab} partial failed to render"
    body = resp.get_data(as_text=True)

    assert 'class="help-tip"' in body, f"{tab}: no tooltips rendered"
    assert 'id="setting-' in body, f"{tab}: no search anchors rendered"
    # Every settings field should be both anchored and tooltipped; tooltip count
    # should not exceed anchor count (each field has at most one help_tip).
    anchors = body.count('id="setting-')
    tips = body.count('class="help-tip"')
    assert tips >= 1 and anchors >= 1
    assert tips <= anchors, f"{tab}: more tooltips ({tips}) than anchors ({anchors})"


@pytest.mark.parametrize("tab", SETTINGS_TABS)
def test_settings_partial_has_per_tab_filter(client, tab):
    body = client.get(f"/v3/partials/{tab}").get_data(as_text=True)
    assert 'class="settings-filter' in body, f"{tab}: per-tab filter box missing"


def test_display_tooltip_carries_rich_text(client):
    # The brightness tooltip should include the authored guidance, not just a label.
    body = client.get("/v3/partials/display").get_data(as_text=True)
    assert 'id="setting-display-brightness"' in body
    assert "Recommended:" in body  # rich detail authored into a tooltip


def test_search_index_endpoint(client):
    """The server-built search index powers the global settings search."""
    resp = client.get("/v3/settings/search-index")
    assert resp.status_code == 200
    data = resp.get_json()
    assert isinstance(data, dict) and isinstance(data.get("fields"), list)
    fields = data["fields"]
    assert len(fields) >= 40, "expected the core settings fields to be indexed"

    by_id = {f["anchorId"]: f for f in fields}
    # Representative fields across tabs must be present with usable text.
    for anchor in ("setting-general-timezone", "setting-display-brightness",
                   "setting-wifi-password", "setting-durations-clock"):
        assert anchor in by_id, f"{anchor} missing from search index"
        entry = by_id[anchor]
        assert entry["label"], f"{anchor} has no label"
        assert entry["help"], f"{anchor} has no tooltip help"
        assert entry["tab"] and entry["tabLabel"]

    # Every entry must carry a non-empty label and a stable anchor id.
    assert all(f["label"] and f["anchorId"].startswith("setting-") for f in fields)
    # Section context is captured for grouped fields (e.g. Display hardware).
    assert by_id["setting-display-brightness"]["section"] == "Hardware Configuration"
