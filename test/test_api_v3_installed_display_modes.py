"""GET /api/v3/plugins/installed carries each plugin's ``display_modes``.

The on-demand modal (plugins_manager.js) fills its Display Mode list from
``plugin.display_modes``, but the route never included the field, so every
plugin offered one option -- its own id -- under "This plugin exposes a
single display mode". The display turns that id into the plugin's first
mode, so a multi-mode plugin could only be started, and pinned, on that one.

The modes come from the plugin catalog (the manifests the web process
discovered), the same source /display/modes and on-demand/start use.
"""

from unittest.mock import MagicMock

import pytest

from test._api_v3_test_helpers import (  # noqa: F401 - fixtures
    api_v3_client, api_v3_module,
)


@pytest.fixture
def installed(api_v3_module, api_v3_client, tmp_path):
    def _get(declared_modes):
        api = api_v3_module.api_v3
        # The listing's own metadata says nothing about modes: what the
        # route reports must come from the catalog.
        info = {'id': 'football-scoreboard', 'name': 'Football', 'version': '1.0.0'}
        api.plugin_catalog.plugins_dir = str(tmp_path)  # no manifest on disk
        api.plugin_catalog.get_all_plugin_info = MagicMock(return_value=[info])
        api.plugin_catalog.get_plugin_display_modes = MagicMock(return_value=declared_modes)
        api.plugin_store_manager.get_registry_info = MagicMock(return_value=None)
        api.config_manager.load_config = MagicMock(return_value={})
        response = api_v3_client.get('/api/v3/plugins/installed')
        assert response.status_code == 200
        plugins = [p for p in response.get_json()['data']['plugins']
                   if p['id'] == 'football-scoreboard']
        assert len(plugins) == 1
        api.plugin_catalog.get_plugin_display_modes.assert_any_call('football-scoreboard')
        return plugins[0]
    return _get


def test_every_declared_mode_is_listed_in_order(installed):
    modes = ['nfl_live', 'nfl_recent', 'nfl_upcoming']
    assert installed(modes)['display_modes'] == modes


def test_a_single_mode_plugin_lists_its_one_mode(installed):
    assert installed(['clock-simple'])['display_modes'] == ['clock-simple']


def test_no_declared_modes_is_an_empty_list(installed):
    # The modal falls back to the plugin id for an empty list.
    assert installed([])['display_modes'] == []


def test_a_hand_edited_manifest_cannot_put_non_strings_in_the_list(installed):
    assert installed(['nfl_live', 7, None, {'x': 1}])['display_modes'] == ['nfl_live']
