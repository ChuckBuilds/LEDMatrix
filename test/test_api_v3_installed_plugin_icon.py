"""GET /api/v3/plugins/installed carries the manifest's `icon`.

The tab nav sets ``iconEl.className = plugin.icon || 'fas fa-puzzle-piece'``
(app-shell.js, app-early.js), but the route never included `icon`, so every
plugin tab showed the default puzzle piece.
"""

from unittest.mock import MagicMock

import pytest

from test._api_v3_test_helpers import (  # noqa: F401 - fixtures
    api_v3_client, api_v3_module,
)


@pytest.fixture
def installed(api_v3_module, api_v3_client, tmp_path):
    def _get(manifest_extra):
        api = api_v3_module.api_v3
        info = {'id': 'demo', 'name': 'Demo', 'version': '1.0.0', 'loaded': False}
        info.update(manifest_extra)
        api.plugin_catalog.plugins_dir = str(tmp_path)  # no manifest on disk
        api.plugin_catalog.get_all_plugin_info = MagicMock(return_value=[info])
        api.plugin_store_manager.get_cached_registry_info = MagicMock(return_value=None)
        api.config_manager.load_config = MagicMock(return_value={})
        response = api_v3_client.get('/api/v3/plugins/installed')
        assert response.status_code == 200
        plugins = [p for p in response.get_json()['data']['plugins'] if p['id'] == 'demo']
        assert len(plugins) == 1
        return plugins[0]
    return _get


def test_the_manifest_icon_is_passed_through(installed):
    assert installed({'icon': 'fas fa-cloud-sun'})['icon'] == 'fas fa-cloud-sun'


def test_no_icon_comes_back_as_null(installed):
    # The JS falls back to the puzzle piece on a falsy value.
    assert installed({})['icon'] is None


def test_a_non_string_icon_is_not_passed_through(installed):
    assert installed({'icon': {'class': 'fas fa-star'}})['icon'] is None
