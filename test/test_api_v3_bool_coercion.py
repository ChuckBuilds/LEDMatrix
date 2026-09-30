"""Boolean request fields are coerced, not used raw.

``bool("false")`` is True. /plugins/toggle stored a JSON ``"enabled": "false"``
as-is in config.json (a truthy string the display then treats as enabled) and
passed it to the Starlark toggle the same way, and /display/on-demand/start
pinned the mode and restarted the service for ``"pinned": "false"`` /
``"start_service": "false"``.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from test._api_v3_test_helpers import api_v3_client, api_v3_module  # noqa: F401,E402


class TestPluginToggle:
    @pytest.fixture
    def saved(self, api_v3_module, monkeypatch):
        monkeypatch.setattr(api_v3_module, '_discovered_plugin_manifests',
                            lambda *a, **k: {'clock': {}})
        api_v3_module.api_v3.config_manager.load_config = MagicMock(return_value={})
        captured = {}

        def save(config_manager, config, create_backup=True):
            captured.update(config)
            return True, None
        monkeypatch.setattr(api_v3_module, '_save_config_atomic', save)
        return captured

    @pytest.mark.parametrize("raw,expected", [
        ("false", False), ("true", True), (False, False), (True, True), (0, False),
    ])
    def test_enabled_is_stored_as_a_real_bool(self, api_v3_client, saved, raw, expected):
        response = api_v3_client.post('/api/v3/plugins/toggle',
                                      json={'plugin_id': 'clock', 'enabled': raw})
        assert response.status_code == 200, response.get_json()
        assert saved['clock']['enabled'] is expected

    def test_a_starlark_app_gets_the_coerced_value(self, api_v3_client, api_v3_module):
        toggle = MagicMock(return_value=({'status': 'success'}, 200))
        with patch('web_interface.blueprints.api_v3.plugins._toggle_starlark_app', toggle):
            api_v3_client.post('/api/v3/plugins/toggle',
                               json={'plugin_id': 'starlark:clock', 'enabled': 'false'})
        toggle.assert_called_once_with('clock', False)


class TestOnDemandStart:
    @pytest.fixture
    def service(self, api_v3_module):
        api_v3_module.api_v3.plugin_catalog = None
        api_v3_module.api_v3.config_manager = None
        with patch("web_interface.blueprints.api_v3.display._get_display_service_status",
                   return_value={"active": True}), \
             patch("web_interface.blueprints.api_v3.display._stop_display_service") as stop, \
             patch("web_interface.blueprints.api_v3.display._ensure_display_service_running",
                   return_value={"active": True}) as ensure:
            yield stop, ensure

    def test_string_false_neither_pins_nor_restarts(self, api_v3_client, service):
        stop, ensure = service
        response = api_v3_client.post('/api/v3/display/on-demand/start', json={
            'plugin_id': 'weather', 'pinned': 'false', 'start_service': 'false'})
        assert response.status_code == 200, response.get_json()
        assert response.get_json()['data']['pinned'] is False
        stop.assert_not_called()
        ensure.assert_not_called()

    def test_string_true_pins(self, api_v3_client, service):
        response = api_v3_client.post('/api/v3/display/on-demand/start', json={
            'plugin_id': 'weather', 'pinned': 'true', 'start_service': False})
        assert response.get_json()['data']['pinned'] is True
