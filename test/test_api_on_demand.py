import pytest
from flask import Flask

from web_interface.blueprints import api_v3


class FakeCacheManager:
    def __init__(self):
        self.store = {}

    def get(self, key, max_age=None):
        return self.store.get(key)

    def set(self, key, value):
        self.store[key] = value


class FakePluginManager:
    def __init__(self):
        self.plugin_manifests = {
            'test-plugin': {
                'id': 'test-plugin',
                'display_modes': ['test-mode', 'secondary-mode']
            }
        }

    def get_plugin_display_modes(self, plugin_id):
        manifest = self.plugin_manifests.get(plugin_id, {})
        modes = manifest.get('display_modes')
        if isinstance(modes, list) and modes:
            return modes
        return [plugin_id]

    def find_plugin_for_mode(self, mode):
        for plugin_id, manifest in self.plugin_manifests.items():
            modes = manifest.get('display_modes', [])
            if isinstance(modes, list) and mode in modes:
                return plugin_id
        return None


class FakeConfigManager:
    def __init__(self, initial_config=None):
        self._config = initial_config or {}

    def load_config(self):
        return {key: value.copy() for key, value in self._config.items()}

    def set_config(self, new_config):
        self._config = new_config


@pytest.fixture
def api_client(monkeypatch):
    fake_cache = FakeCacheManager()
    fake_plugin_manager = FakePluginManager()
    fake_config = FakeConfigManager({'test-plugin': {'enabled': True}})

    blueprint = api_v3.api_v3

    api_v3.cache_manager = fake_cache
    api_v3.plugin_manager = fake_plugin_manager
    api_v3.config_manager = fake_config
    api_v3.plugin_store_manager = None
    api_v3.saved_repositories_manager = None

    blueprint.cache_manager = fake_cache
    blueprint.plugin_manager = fake_plugin_manager
    blueprint.config_manager = fake_config
    blueprint.plugin_store_manager = None
    blueprint.saved_repositories_manager = None

    monkeypatch.setattr(api_v3, '_ensure_display_service_running', lambda: {'active': True, 'started': False, 'status': {}})
    monkeypatch.setattr(api_v3, '_stop_display_service', lambda: {'active': False, 'started': False, 'status': {}})

    app = Flask(__name__)
    app.config['TESTING'] = True
    app.register_blueprint(api_v3.api_v3, url_prefix='/api/v3')

    with app.test_client() as client:
        yield client, fake_cache, fake_plugin_manager, fake_config


def test_get_on_demand_status_default(api_client):
    client, cache_manager, *_ = api_client

    response = client.get('/api/v3/display/on-demand/status')
    assert response.status_code == 200
    payload = response.get_json()

    assert payload['status'] == 'success'
    state = payload['data']['state']
    service = payload['data']['service']

    assert state['active'] is False
    assert state['status'] in ('idle', None)
    assert 'active' in service


def test_start_on_demand_with_plugin_id(api_client):
    client, cache_manager, *_ = api_client

    response = client.post('/api/v3/display/on-demand/start', json={
        'plugin_id': 'test-plugin',
        'duration': 45,
        'pinned': True
    })

    assert response.status_code == 200
    payload = response.get_json()
    assert payload['status'] == 'success'
    data = payload['data']

    assert data['plugin_id'] == 'test-plugin'
    assert data['mode'] == 'test-mode'
    assert data['duration'] == 45
    assert data['pinned'] is True

    request_payload = cache_manager.store.get('display_on_demand_request')
    assert request_payload is not None
    assert request_payload['action'] == 'start'
    assert request_payload['plugin_id'] == 'test-plugin'
    assert request_payload['mode'] == 'test-mode'
    assert request_payload['duration'] == 45
    assert request_payload['pinned'] is True
    assert 'request_id' in request_payload
    assert 'timestamp' in request_payload


def test_start_on_demand_with_mode_only(api_client):
    client, cache_manager, _, config_manager = api_client
    config_manager.set_config({'test-plugin': {'enabled': True}})

    response = client.post('/api/v3/display/on-demand/start', json={
        'mode': 'secondary-mode'
    })

    assert response.status_code == 200
    payload = response.get_json()
    assert payload['status'] == 'success'

    request_payload = cache_manager.store.get('display_on_demand_request')
    assert request_payload['plugin_id'] == 'test-plugin'
    assert request_payload['mode'] == 'secondary-mode'


def test_start_on_demand_rejects_disabled_plugin(api_client):
    client, cache_manager, _, config_manager = api_client
    config_manager.set_config({'test-plugin': {'enabled': False}})

    response = client.post('/api/v3/display/on-demand/start', json={
        'plugin_id': 'test-plugin'
    })

    assert response.status_code == 400
    payload = response.get_json()
    assert payload['status'] == 'error'
    assert 'disabled' in payload['message']
    assert 'display_on_demand_request' not in cache_manager.store


def test_stop_on_demand_request(api_client):
    client, cache_manager, *_ = api_client

    response = client.post('/api/v3/display/on-demand/stop', json={})
    assert response.status_code == 200

    payload = response.get_json()
    assert payload['status'] == 'success'

    request_payload = cache_manager.store.get('display_on_demand_request')
    assert request_payload is not None
    assert request_payload['action'] == 'stop'
    assert 'request_id' in request_payload
    assert 'timestamp' in request_payload

