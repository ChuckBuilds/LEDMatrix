"""
Getting Started checklist: what the server decides, and what it must not.

The timezone step used to tick server-side when the saved timezone differed
from the shipped default, OR-ed with the saved city. That made the step
unsatisfiable for anyone genuinely in the default zone (the card nagged
forever), and let a saved city tick it off while the timezone was still wrong.
The step is now verified in the browser against its own zone, so the server's
only job is to hand over the configured value and stay out of the decision.

These tests pin that contract: the panel-size step still reflects config, the
timezone step never pre-ticks, it carries the configured zone, and the city
has no influence on it.
"""

import copy
import re
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from flask import Flask

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

BASE_CONFIG = {
    "timezone": "America/New_York",
    "location": {"city": "Tampa", "state": "Florida", "country": "US"},
    "display": {
        "hardware": {"rows": 32, "cols": 64, "chain_length": 2, "parallel": 1},
        "runtime": {},
        "double_sided": {"enabled": False},
        "vegas_scroll": {"plugin_order": [], "excluded_plugins": []},
        "plugin_rotation_order": [],
    },
    "plugin_system": {},
    "schedule": {},
    "dim_schedule": {},
    "sync": {},
}


def render(config):
    """Render the overview partial against one config, as app.py would."""
    base = PROJECT_ROOT / "web_interface"
    app = Flask(
        __name__,
        template_folder=str(base / "templates"),
        static_folder=str(base / "static"),
    )
    app.config["TESTING"] = True

    from web_interface.blueprints import pages_v3 as pv

    # pages_v3 is a module-level singleton shared across the test process;
    # restore whatever the previous test left on it.
    original_cm = getattr(pv.pages_v3, "config_manager", None)
    original_pm = getattr(pv.pages_v3, "plugin_manager", None)

    mock_cm = MagicMock()
    mock_cm.load_config.return_value = config
    mock_cm.get_raw_file_content.return_value = config
    pv.pages_v3.config_manager = mock_cm

    mock_pm = MagicMock()
    mock_pm.plugins = {}
    mock_pm.get_all_plugin_info.return_value = []
    mock_pm.get_plugin_display_modes.side_effect = lambda pid: []
    pv.pages_v3.plugin_manager = mock_pm

    app.register_blueprint(pv.pages_v3, url_prefix="")
    try:
        resp = app.test_client().get("/partials/overview")
        assert resp.status_code == 200, resp.status_code
        return resp.get_data(as_text=True)
    finally:
        pv.pages_v3.config_manager = original_cm
        pv.pages_v3.plugin_manager = original_pm


def timezone_step(body):
    """The checklist <button> for the timezone step."""
    match = re.search(r"<button[^>]*data-check=\"timezone\"[^>]*>", body)
    assert match, "timezone step not found in the rendered checklist"
    return match.group(0)


def config_with(**overrides):
    config = copy.deepcopy(BASE_CONFIG)
    for key, value in overrides.items():
        config[key] = value
    return config


@pytest.mark.parametrize(
    "timezone",
    ["America/New_York", "America/Los_Angeles", "Europe/Madrid", "Asia/Kolkata"],
)
def test_timezone_step_never_pre_ticks_server_side(timezone):
    """The browser owns this decision; the server must not pre-empt it.

    The default zone is in the list deliberately: that is the case the old
    default-comparison could never tick.
    """
    step = timezone_step(render(config_with(timezone=timezone)))
    assert 'data-done="0"' in step, step


@pytest.mark.parametrize(
    "timezone",
    ["America/New_York", "Europe/Madrid", "Pacific/Auckland"],
)
def test_timezone_step_carries_the_configured_zone(timezone):
    """JS compares data-tz against the browser, so it has to be the real value."""
    assert f'data-tz="{timezone}"' in timezone_step(render(config_with(timezone=timezone)))


def test_city_does_not_influence_the_timezone_step():
    """The coupling this change removes: city said nothing about the timezone,
    and OR-ing it let a saved city tick the step off with the zone still wrong."""
    tampa = timezone_step(render(config_with(
        location={"city": "Tampa", "state": "Florida", "country": "US"})))
    seattle = timezone_step(render(config_with(
        location={"city": "Seattle", "state": "Washington", "country": "US"})))
    assert tampa == seattle


def test_missing_timezone_leaves_the_step_open():
    """Nothing saved means nothing to verify: the step stays unticked and the
    JS bails on the empty value rather than comparing against ''."""
    step = timezone_step(render(config_with(timezone="")))
    assert 'data-tz=""' in step
    assert 'data-done="0"' in step


@pytest.mark.parametrize(
    "hardware,expected",
    [
        ({"rows": 32, "cols": 64, "chain_length": 2, "parallel": 1}, "1"),
        ({"rows": 0, "cols": 0, "chain_length": 0, "parallel": 1}, "0"),
    ],
)
def test_panel_size_step_still_reflects_config(hardware, expected):
    """Regression guard: the hardware step is still decided server-side."""
    config = config_with()
    config["display"]["hardware"] = hardware
    body = render(config)
    match = re.search(r"<button[^>]*data-tab=\"display\"[^>]*>", body)
    assert match, "panel-size step not found"
    assert f'data-done="{expected}"' in match.group(0), match.group(0)
