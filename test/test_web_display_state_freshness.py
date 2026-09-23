"""Tests that the web UI reports the display's state as it is now.

The display service writes display_on_demand_state and display_current_state;
the web interface is a different process and can only see them on disk. Its
reads went through the cache's memory tier, which keeps the first copy read
for the full max_age (120s). Measured on a rig once the web interface could
read these files at all: /display/on-demand/status said "active" for over 100
seconds while the file on disk had said "idle" the whole time.
"""

import json

import pytest
from flask import Flask

import web_interface.blueprints.api_v3 as api_pkg
from src.cache_manager import CacheManager


@pytest.fixture
def two_processes(tmp_path, monkeypatch):
    """A display-side and a web-side cache over one directory, as on a rig."""
    monkeypatch.setattr(CacheManager, '_get_writable_cache_dir', lambda self: str(tmp_path))
    monkeypatch.setattr(CacheManager, 'start_cleanup_thread', lambda self: None)
    display, web = CacheManager(), CacheManager()
    monkeypatch.setattr(api_pkg.api_v3, 'cache_manager', web, raising=False)
    monkeypatch.setattr(api_pkg, '_get_display_service_status', lambda: {'active': True})
    return display


@pytest.fixture
def client():
    app = Flask(__name__)
    app.config['TESTING'] = True
    if 'api_v3' not in app.blueprints:
        app.register_blueprint(api_pkg.api_v3, url_prefix='/api/v3')
    with app.test_client() as c:
        yield c


def _get(client, url):
    response = client.get(url)
    assert response.status_code == 200
    return json.loads(response.data)['data']


def test_on_demand_status_sees_the_stop_immediately(client, two_processes):
    two_processes.set('display_on_demand_state', {'active': True, 'status': 'active'})
    assert _get(client, '/api/v3/display/on-demand/status')['state']['status'] == 'active'

    two_processes.set('display_on_demand_state', {'active': False, 'status': 'idle'})

    assert _get(client, '/api/v3/display/on-demand/status')['state']['status'] == 'idle'


def test_current_status_sees_the_mode_change_immediately(client, two_processes):
    two_processes.set('display_current_state', {'mode': 'odds_ticker'})
    assert _get(client, '/api/v3/display/current-status')['mode'] == 'odds_ticker'

    two_processes.set('display_current_state', {'mode': 'stocks'})

    assert _get(client, '/api/v3/display/current-status')['mode'] == 'stocks'
