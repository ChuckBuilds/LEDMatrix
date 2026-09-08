"""GET /api/v3/display/modes -- the list of modes an on-demand request can name.

Anything driving the display from outside the web UI needs two things that no
existing endpoint gave it: the set of display modes, and which plugin owns each
one. /plugins/installed carries neither, so callers read every plugin's
manifest.json off disk and reimplemented PluginManager's own fallbacks.
"""

import json
from unittest.mock import MagicMock

import pytest

from test._api_v3_test_helpers import (  # noqa: F401 - fixtures
    api_v3_client, api_v3_module,
)


MANIFESTS = {
    'clock-simple': {'name': 'Simple Clock', 'display_modes': ['clock-simple']},
    'football-scoreboard': {
        'name': 'Football Scoreboard',
        'display_modes': ['nfl_live', 'nfl_recent', 'nfl_upcoming'],
    },
    'ledmatrix-weather': {'name': 'Weather', 'display_modes': ['weather']},
}

CONFIG = {
    'clock-simple': {'enabled': True},
    'football-scoreboard': {'enabled': True},
    'ledmatrix-weather': {'enabled': False},
}


@pytest.fixture
def client(api_v3_module, api_v3_client):
    pm = api_v3_module.api_v3.plugin_manager
    pm.plugin_manifests = MANIFESTS
    pm.discover_plugins = MagicMock(return_value=list(MANIFESTS))
    pm.get_plugin_display_modes = MagicMock(
        side_effect=lambda pid: MANIFESTS[pid]['display_modes'])
    api_v3_module.api_v3.config_manager.load_config = MagicMock(return_value=CONFIG)
    return api_v3_client


def _modes(response):
    return {m['mode']: m for m in response.get_json()['data']['modes']}


class TestTheModeListing:
    def test_enabled_plugins_contribute_every_mode(self, client):
        modes = _modes(client.get('/api/v3/display/modes'))
        assert set(modes) == {'clock-simple', 'nfl_live', 'nfl_recent', 'nfl_upcoming'}

    def test_each_mode_names_its_plugin(self, client):
        """on-demand/start's find_plugin_for_mode fallback cannot see generated
        modes, so the caller has to send plugin_id -- it must come from here."""
        modes = _modes(client.get('/api/v3/display/modes'))
        assert modes['nfl_live']['plugin_id'] == 'football-scoreboard'

    def test_disabled_plugins_are_left_out_by_default(self, client):
        modes = _modes(client.get('/api/v3/display/modes'))
        assert 'weather' not in modes

    def test_disabled_plugins_can_be_asked_for(self, client):
        """They are still valid on-demand targets: the controller enables them
        for the duration of the request."""
        modes = _modes(client.get('/api/v3/display/modes?include_disabled=1'))
        assert modes['weather']['enabled'] is False

    def test_a_single_mode_plugin_is_labelled_with_its_own_name(self, client):
        modes = _modes(client.get('/api/v3/display/modes'))
        assert modes['clock-simple']['name'] == 'Simple Clock'

    def test_a_multi_mode_plugin_labels_each_mode_distinctly(self, client):
        """There is no per-mode name anywhere, and three modes all called
        "Football Scoreboard" are not a usable dropdown."""
        modes = _modes(client.get('/api/v3/display/modes'))
        assert modes['nfl_live']['name'] == 'nfl_live'
        assert modes['nfl_live']['plugin_name'] == 'Football Scoreboard'


class TestItWorksForACallerThatNeverOpensTheDashboard:
    def test_discovery_is_triggered(self, client, api_v3_module):
        """Discovery is lazy and normally runs because a person loaded the
        dashboard; a bridge or script would otherwise get an empty list."""
        client.get('/api/v3/display/modes')
        api_v3_module.api_v3.plugin_manager.discover_plugins.assert_called_once()

    def test_no_plugin_manager_is_a_clean_error(self, api_v3_module, api_v3_client):
        api_v3_module.api_v3.plugin_manager = None
        response = api_v3_client.get('/api/v3/display/modes')
        assert response.status_code == 500
        assert response.get_json()['status'] == 'error'

    def test_a_plugin_with_no_declared_modes_still_appears(self, client, api_v3_module):
        """Its mode is its own id -- the same fallback the controller uses."""
        pm = api_v3_module.api_v3.plugin_manager
        pm.plugin_manifests = {'starlark-apps': {'name': 'Starlark Apps', 'display_modes': []}}
        pm.get_plugin_display_modes = MagicMock(return_value=[])
        api_v3_module.api_v3.config_manager.load_config = MagicMock(
            return_value={'starlark-apps': {'enabled': True}})

        modes = _modes(client.get('/api/v3/display/modes'))
        assert modes['starlark-apps']['plugin_id'] == 'starlark-apps'


class TestOneBadConfigSectionDoesNotBlankTheList:
    """config.json can hold a non-dict under a plugin id.

    DisplayController guards the same shape, so it happens in practice. Here it
    used to raise AttributeError mid-loop and answer 500 with no modes at all --
    and the MQTT bridge builds every one of its entities from this list, so one
    hand-edited section would empty the Home Assistant dropdown.
    """

    @pytest.fixture
    def client_with_bad_section(self, api_v3_module, api_v3_client):
        pm = api_v3_module.api_v3.plugin_manager
        pm.plugin_manifests = MANIFESTS
        pm.discover_plugins = MagicMock(return_value=list(MANIFESTS))
        pm.get_plugin_display_modes = MagicMock(
            side_effect=lambda pid: MANIFESTS[pid]['display_modes'])
        api_v3_module.api_v3.config_manager.load_config = MagicMock(return_value={
            'clock-simple': {'enabled': True},
            'football-scoreboard': "true",          # a string, not an object
            'ledmatrix-weather': {'enabled': True},
        })
        return api_v3_client

    def test_the_endpoint_still_answers(self, client_with_bad_section):
        assert client_with_bad_section.get('/api/v3/display/modes').status_code == 200

    def test_the_healthy_plugins_are_still_listed(self, client_with_bad_section):
        modes = _modes(client_with_bad_section.get('/api/v3/display/modes'))
        assert 'clock-simple' in modes and 'weather' in modes

    def test_the_bad_section_is_treated_as_disabled(self, client_with_bad_section):
        modes = _modes(client_with_bad_section.get('/api/v3/display/modes'))
        assert 'nfl_live' not in modes

    def test_a_failure_is_reported_the_way_every_other_handler_reports_one(
            self, api_v3_module, api_v3_client):
        """describe_exception, per test_web_error_detail's contract -- an
        opaque "see logs for details" is what that test exists to prevent."""
        api_v3_module.api_v3.plugin_manager.discover_plugins = MagicMock(
            side_effect=RuntimeError("disk is gone"))
        resp = api_v3_client.get('/api/v3/display/modes')
        assert resp.status_code == 500
        assert 'disk is gone' in resp.get_json()['details']

    def test_credentials_in_the_exception_are_redacted(self, api_v3_module, api_v3_client):
        """describe_exception is what makes returning detail safe."""
        api_v3_module.api_v3.plugin_manager.discover_plugins = MagicMock(
            side_effect=RuntimeError("GET https://x/y?api_key=SEC123 failed"))
        body = api_v3_client.get('/api/v3/display/modes').get_json()
        assert 'SEC123' not in json.dumps(body)
