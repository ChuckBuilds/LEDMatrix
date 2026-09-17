"""Tests that api_v3 routes find installed plugins before anything else has.

The web process discovers plugins lazily (web_interface/app.py): nothing scans
the plugins directory at startup, and plugin_manifests stays empty until some
endpoint calls discover_plugins(). Routes that consulted plugin_manifests
without discovering therefore misbehaved for as long as nothing else had run.
Measured on a rig after restarting ledmatrix-web:

    POST /api/v3/display/on-demand/start {"plugin_id": "ledmatrix-stocks"}
    -> 404 "Plugin ledmatrix-stocks not found", for over three minutes,
       until GET /api/v3/plugins/installed happened to discover plugins.

The browser UI loads the plugin list first, so people rarely saw it; API-only
callers (the Home Assistant MQTT bridge, scripts) saw it after every restart.
/plugins/toggle answered the same 404, and /config/main was worse: an
undiscovered plugin section skipped secret separation and wrote its API key
into config.json in plain text.

A real PluginManager over a temporary plugins directory, so "empty until
discovered" is the real behaviour rather than a mock's.
"""

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.config_manager import ConfigManager  # noqa: E402
from src.plugin_system.plugin_manager import PluginManager  # noqa: E402
from src.plugin_system.schema_manager import SchemaManager  # noqa: E402
from test._api_v3_test_helpers import api_v3_client, api_v3_module  # noqa: F401,E402

PLUGIN_ID = 'ledmatrix-stocks'
MODE = 'stocks'


def _install(plugins_dir, plugin_id, modes, schema=None):
    plugin_dir = plugins_dir / plugin_id
    plugin_dir.mkdir(parents=True)
    (plugin_dir / 'manifest.json').write_text(json.dumps({
        'id': plugin_id, 'name': plugin_id, 'version': '1.0.0',
        'entry_point': 'manager.py', 'class_name': 'Plugin',
        'display_modes': modes,
    }))
    (plugin_dir / 'manager.py').write_text('')
    if schema is not None:
        (plugin_dir / 'config_schema.json').write_text(json.dumps(schema))


@pytest.fixture
def plugins_dir(tmp_path):
    path = tmp_path / 'plugin-repos'
    path.mkdir()
    _install(path, PLUGIN_ID, [MODE], schema={
        'type': 'object',
        'properties': {
            'enabled': {'type': 'boolean'},
            'symbols': {'type': 'string'},
            'api_key': {'type': 'string', 'x-secret': True},
        },
    })
    return path


@pytest.fixture
def fresh_web_process(api_v3_module, plugins_dir):
    """The web process right after a restart: nothing discovered yet."""
    manager = PluginManager(plugins_dir=str(plugins_dir))
    assert not manager.plugin_manifests
    api_v3_module.api_v3.plugin_manager = manager
    return manager


@pytest.fixture
def display_service():
    """Keep on-demand start away from systemctl and the real cache."""
    with patch('web_interface.blueprints.api_v3.display._ensure_cache_manager') as cache, \
         patch('web_interface.blueprints.api_v3.display._get_display_service_status') as status:
        cache.return_value = MagicMock()
        status.return_value = {'active': True}
        yield cache.return_value


def _start(client, **body):
    body.setdefault('start_service', False)
    return client.post('/api/v3/display/on-demand/start', json=body)


class TestOnDemandStart:
    def test_by_plugin_id(self, api_v3_client, fresh_web_process, display_service):
        response = _start(api_v3_client, plugin_id=PLUGIN_ID)

        assert response.status_code == 200, response.get_json()
        request = display_service.set.call_args.args[1]
        assert (request['plugin_id'], request['mode']) == (PLUGIN_ID, MODE)

    def test_by_mode_alone(self, api_v3_client, fresh_web_process, display_service):
        response = _start(api_v3_client, mode=MODE)

        assert response.status_code == 200, response.get_json()
        assert display_service.set.call_args.args[1]['plugin_id'] == PLUGIN_ID

    def test_a_plugin_installed_since_the_last_scan(
            self, api_v3_client, fresh_web_process, plugins_dir, display_service):
        fresh_web_process.discover_plugins()
        _install(plugins_dir, 'ledmatrix-weather', ['weather'])

        assert _start(api_v3_client, plugin_id='ledmatrix-weather').status_code == 200
        assert _start(api_v3_client, mode=MODE).status_code == 200

    def test_a_mode_installed_since_the_last_scan(
            self, api_v3_client, fresh_web_process, plugins_dir, display_service):
        fresh_web_process.discover_plugins()
        _install(plugins_dir, 'ledmatrix-weather', ['weather'])

        response = _start(api_v3_client, mode='weather')

        assert response.status_code == 200, response.get_json()

    def test_an_unknown_plugin_is_still_not_found(
            self, api_v3_client, fresh_web_process, display_service):
        assert _start(api_v3_client, plugin_id='no-such-plugin').status_code == 404
        assert _start(api_v3_client, mode='no-such-mode').status_code == 404
        display_service.set.assert_not_called()


def test_toggle_finds_the_plugin(api_v3_client, api_v3_module, fresh_web_process):
    config_manager = api_v3_module.api_v3.config_manager
    config_manager.load_config.return_value = {PLUGIN_ID: {'enabled': False}}
    config_manager.save_config_atomic.return_value = MagicMock(
        status=MagicMock(value='success'))

    response = api_v3_client.post('/api/v3/plugins/toggle',
                                  json={'plugin_id': PLUGIN_ID, 'enabled': True})

    assert response.status_code == 200, response.get_json()
    saved = config_manager.save_config_atomic.call_args.args[0]
    assert saved[PLUGIN_ID]['enabled'] is True


def test_main_config_save_keeps_a_plugin_secret_out_of_config_json(
        api_v3_client, api_v3_module, fresh_web_process, plugins_dir, tmp_path):
    # /config/main validates a plugin section like POST /plugins/config does,
    # so it needs a real schema manager rather than the helper's mock.
    api_v3_module.api_v3.schema_manager = SchemaManager(plugins_dir=plugins_dir)
    config_file = tmp_path / 'config.json'
    config_file.write_text('{}')
    secrets_file = tmp_path / 'config_secrets.json'
    config_manager = ConfigManager(config_path=str(config_file),
                                   secrets_path=str(secrets_file))
    config_manager.template_path = str(tmp_path / 'no-template.json')
    api_v3_module.api_v3.config_manager = config_manager

    response = api_v3_client.post('/api/v3/config/main', json={
        PLUGIN_ID: {'symbols': 'AAPL', 'api_key': 's3cret-key'},
    })

    assert response.status_code == 200, response.get_json()
    assert 's3cret' not in config_file.read_text()
    assert json.loads(secrets_file.read_text())[PLUGIN_ID]['api_key'] == 's3cret-key'
    assert json.loads(config_file.read_text())[PLUGIN_ID]['symbols'] == 'AAPL'
