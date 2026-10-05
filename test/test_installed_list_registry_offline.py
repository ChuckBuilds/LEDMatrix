"""GET /api/v3/plugins/installed never waits on the network for registry data.

The route used `get_registry_info`, which goes through `fetch_registry`: on a
cold (or expired) cache that downloads plugins.json from GitHub with a 10s
timeout and three attempts -- and, with no registry to fall back on, every
plugin's lookup repeated the whole cycle. The first plugin-list load after a
restart waited on GitHub, and offline it waited out every timeout.

Now the route reads the registry copy already in memory, however old, and a
missing or expired copy only starts a background refresh. These tests block
the network at the socket layer (DNS lookups hang, then fail) and assert the
request returns quickly without a single network attempt on the request path.
"""

import functools
import socket
import threading
import time
from unittest.mock import MagicMock

import pytest
import requests

from test._api_v3_test_helpers import (  # noqa: F401 - fixtures
    api_v3_client, api_v3_module,
)
from src.plugin_system.store_manager import PluginStoreManager

# How long a blocked lookup hangs before failing: long enough that a single
# one on the request path blows the response budget below.
HANG_SECONDS = 1.5
FAST_SECONDS = 1.0

REGISTRY = {'plugins': [
    {'id': 'weather', 'name': 'Weather', 'verified': True, 'latest_version': '1.2.0'},
]}


@pytest.fixture
def blocked_network(monkeypatch):
    """Every DNS lookup hangs, then fails; records the thread it came from."""
    attempts = []

    def hang_then_fail(host, *args, **kwargs):
        attempts.append((host, threading.current_thread().name))
        time.sleep(HANG_SECONDS)
        raise socket.gaierror(-3, 'Temporary failure in name resolution (blocked by test)')

    monkeypatch.setattr(socket, 'getaddrinfo', hang_then_fail)
    return attempts


@pytest.fixture
def store(tmp_path, monkeypatch):
    store = PluginStoreManager(plugins_dir=str(tmp_path / 'plugins'))
    # One attempt, no pause between attempts, so a background refresh against
    # the blocked network ends within the test (teardown joins it).
    monkeypatch.setattr(store, '_http_get_with_retries', functools.partial(
        PluginStoreManager._http_get_with_retries, store, max_retries=1))
    yield store
    thread = getattr(store, '_registry_refresh_thread', None)
    if thread is not None:
        thread.join(timeout=30)


@pytest.fixture
def get_installed(api_v3_module, api_v3_client, store, tmp_path):
    api = api_v3_module.api_v3
    api.plugin_store_manager = store
    api.plugin_catalog.plugins_dir = str(tmp_path / 'plugins')
    api.plugin_catalog.get_all_plugin_info = MagicMock(return_value=[
        {'id': 'weather', 'name': 'Weather', 'version': '1.0.0'},
        {'id': 'clock', 'name': 'Clock', 'version': '2.0.0'},
    ])
    api.plugin_catalog.get_plugin_display_modes = MagicMock(return_value=[])
    api.config_manager.load_config = MagicMock(return_value={})

    def _get():
        start = time.perf_counter()
        response = api_v3_client.get('/api/v3/plugins/installed')
        elapsed = time.perf_counter() - start
        assert response.status_code == 200
        plugins = {p['id']: p for p in response.get_json()['data']['plugins']}
        return plugins, elapsed
    return _get


def _request_path_attempts(attempts):
    return [a for a in attempts if a[1] != 'registry-refresh']


def test_a_cold_cache_offline_returns_fast_without_registry_info(get_installed, blocked_network):
    plugins, elapsed = get_installed()

    assert _request_path_attempts(blocked_network) == []
    assert elapsed < FAST_SECONDS, f"installed list took {elapsed:.2f}s with the network blocked"
    weather = plugins['weather']
    assert weather['latest_version'] == ''
    assert weather['update_available'] is False
    assert weather['verified'] is False


def test_a_stale_cache_is_used_as_is_without_a_fetch(get_installed, store, blocked_network):
    store.registry_cache = REGISTRY
    store.registry_cache_time = time.time() - store.registry_cache_timeout - 3600

    plugins, elapsed = get_installed()

    assert _request_path_attempts(blocked_network) == []
    assert elapsed < FAST_SECONDS
    weather = plugins['weather']
    assert weather['latest_version'] == '1.2.0'
    assert weather['update_available'] is True
    assert weather['verified'] is True
    assert plugins['clock']['latest_version'] == ''


def test_a_fresh_cache_starts_no_refresh(get_installed, store, blocked_network):
    store.registry_cache = REGISTRY
    store.registry_cache_time = time.time()

    plugins, _ = get_installed()

    assert blocked_network == []
    assert store._registry_refresh_thread is None
    assert plugins['weather']['update_available'] is True


def test_a_cold_cache_is_filled_in_the_background_for_the_next_load(get_installed, store, monkeypatch):
    response = MagicMock()
    response.json.return_value = REGISTRY
    fetched_on = []

    def fake_get(url, **kwargs):
        fetched_on.append(threading.current_thread().name)
        return response

    monkeypatch.setattr(store, '_http_get_with_retries', fake_get)

    first, _ = get_installed()
    assert first['weather']['update_available'] is False
    store._registry_refresh_thread.join(timeout=10)

    second, _ = get_installed()
    assert second['weather']['latest_version'] == '1.2.0'
    assert second['weather']['update_available'] is True
    # One background fetch for the whole listing, none on the request path.
    assert fetched_on == ['registry-refresh']


def test_an_offline_background_refresh_backs_off(store, monkeypatch):
    def offline(url, **kwargs):
        raise requests.ConnectionError('blocked by test')

    monkeypatch.setattr(store, '_http_get_with_retries', offline)

    assert store.refresh_registry_in_background() is True
    store._registry_refresh_thread.join(timeout=10)
    assert store.registry_cache is None
    # Offline: the next page load does not start another attempt straight away.
    assert store.refresh_registry_in_background() is False
    store._registry_refresh_retry_after = 0.0
    assert store.refresh_registry_in_background() is True


def test_only_one_background_refresh_runs_at_a_time(store, monkeypatch):
    release = threading.Event()

    def slow(url, **kwargs):
        release.wait(10)
        raise requests.ConnectionError('blocked by test')

    monkeypatch.setattr(store, '_http_get_with_retries', slow)
    try:
        assert store.refresh_registry_in_background() is True
        assert store.refresh_registry_in_background() is False
    finally:
        release.set()


def test_get_registry_info_still_fetches_for_the_store(store, monkeypatch):
    """The store, install and update paths keep fetching a cold registry."""
    response = MagicMock()
    response.json.return_value = REGISTRY
    monkeypatch.setattr(store, '_http_get_with_retries', MagicMock(return_value=response))

    assert store.get_registry_info('weather')['latest_version'] == '1.2.0'
    store._http_get_with_retries.assert_called_once()
