"""
Web-UI smoke tests: every page, partial, and critical static asset must render.

These boot the pages blueprint with the same dual registration app.py uses
(un-prefixed primary + /v3 legacy alias) and assert each surface returns 200
with its load-bearing markers present. They exist to catch, in CI, the class
of regression that only shows up when a real request renders a real template:
a broken partial, a missing tab wiring, a renamed element id that JS depends
on, or a static asset that stopped being served.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from flask import Flask

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


SMOKE_CONFIG = {
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
    "clock": {"enabled": True},
    "ledmatrix-weather": {"enabled": True},
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
        "display_durations": {"stale_saved_mode": 45},
        "plugin_rotation_order": ["ledmatrix-weather", "clock"],
    },
}

PLUGIN_MODES = {
    "clock": ["clock"],
    "ledmatrix-weather": ["weather_current", "weather_daily"],
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

    # pages_v3 is a module-level Blueprint singleton shared by the whole test
    # process (test_web_settings_ui.py mutates the same attributes) - save
    # the originals and restore them on teardown so this fixture can't leak
    # its mocks into tests that run afterward.
    original_config_manager = getattr(pv.pages_v3, "config_manager", None)
    original_plugin_manager = getattr(pv.pages_v3, "plugin_manager", None)

    mock_cm = MagicMock()
    mock_cm.load_config.return_value = SMOKE_CONFIG
    mock_cm.get_raw_file_content.return_value = SMOKE_CONFIG
    mock_cm.get_config_path.return_value = "config/config.json"
    mock_cm.get_secrets_path.return_value = "config/config_secrets.json"
    pv.pages_v3.config_manager = mock_cm

    mock_pm = MagicMock()
    mock_pm.plugins = {}
    mock_pm.get_all_plugin_info.return_value = [
        {"id": "clock", "name": "Clock"},
        {"id": "ledmatrix-weather", "name": "Weather"},
    ]
    mock_pm.get_plugin_display_modes.side_effect = (
        lambda pid: PLUGIN_MODES.get(pid, [])
    )
    pv.pages_v3.plugin_manager = mock_pm

    # Same dual registration as web_interface/app.py: un-prefixed primary,
    # /v3 kept as a working legacy alias.
    app.register_blueprint(pv.pages_v3, url_prefix="")
    app.register_blueprint(pv.pages_v3, url_prefix="/v3", name="pages_v3_legacy")
    try:
        yield app.test_client()
    finally:
        pv.pages_v3.config_manager = original_config_manager
        pv.pages_v3.plugin_manager = original_plugin_manager


# (path, [markers that must appear in the body])
PAGES = [
    ("/", ["site-nav", "mobileNavOpen", 'rel="manifest"',
           "restart-pending-banner", "activeTab = 'durations'"]),
    ("/partials/overview", ["getting-started-card", "displayImage"]),
    ("/partials/general", ["timezone"]),
    ("/partials/display", ["display-section-advanced-hardware",
                           "display-resolution-value", "vegas_scroll_label"]),
    ("/partials/durations", ["rotation_plugin_order", "duration__clock",
                             "duration__weather_current",
                             "duration__stale_saved_mode"]),
    ("/partials/schedule", ["schedule"]),
]


@pytest.mark.parametrize("path,markers", PAGES, ids=[p for p, _ in PAGES])
def test_page_renders_with_markers(client, path, markers):
    resp = client.get(path)
    assert resp.status_code == 200, f"{path} -> {resp.status_code}"
    body = resp.get_data(as_text=True)
    for marker in markers:
        assert marker in body, f"{path}: missing marker {marker!r}"


@pytest.mark.parametrize("path", [p for p, _ in PAGES if p != "/"])
def test_legacy_v3_alias_serves_the_same_partials(client, path):
    assert client.get("/v3" + path).status_code == 200


STATIC_ASSETS = [
    "/static/v3/app.css",
    "/static/v3/app.js",
    "/static/v3/manifest.json",
    "/static/v3/icons/icon-192.png",
    "/static/v3/js/app-shell.js",
    "/static/v3/js/app-early.js",
    "/static/v3/js/htmx-config.js",
    "/static/v3/js/widgets/plugin-order-list.js",
    "/static/v3/js/widgets/notification.js",
    "/static/v3/vendor/fontawesome/css/all.min.css",
    "/static/v3/vendor/codemirror/codemirror.min.js",
]


@pytest.mark.parametrize("asset", STATIC_ASSETS)
def test_static_asset_served(client, asset):
    resp = client.get(asset)
    assert resp.status_code == 200, f"{asset} -> {resp.status_code}"
    assert len(resp.data) > 0


def test_durations_page_groups_by_plugin(client):
    """One duration input per display mode of each enabled plugin, plus the
    leftover group for saved keys no enabled plugin owns."""
    body = client.get("/partials/durations").get_data(as_text=True)
    assert body.count("duration__") >= 2 * len(
        [m for modes in PLUGIN_MODES.values() for m in modes]
    )  # each mode: id= and name=
    assert "Other saved entries" in body


def test_display_advanced_section_contains_tuning_fields(client):
    body = client.get("/partials/display").get_data(as_text=True)
    adv = body.find('id="display-section-advanced-hardware"')
    adv_close = body.find("/#display-section-advanced-hardware")
    assert 0 < adv < adv_close
    for field in ["multiplexing", "pwm_bits", "inverse_colors"]:
        pos = body.find(f'name="{field}"')
        assert adv < pos < adv_close, f"{field} not inside the advanced section"
