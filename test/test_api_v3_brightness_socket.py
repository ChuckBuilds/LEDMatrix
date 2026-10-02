"""POST /api/v3/config/main with a brightness: on the panel at once.

The saved brightness used to reach the panel when the display's config
watcher next noticed config.json (it polls every 2 s) and the render thread
then applied it (every 0.25 s). After a successful save the route now also
sends ``brightness.set`` over the control socket (src/ipc), which the
display applies on its render thread at once. When the socket cannot carry
it, nothing changes: the watcher applies the saved value as before. The
response says which (``brightness_transport``), and why
(``brightness_socket_error``).
"""

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from test._api_v3_test_helpers import api_v3_client, api_v3_module  # noqa: F401,E402

from src.ipc import client as control_client  # noqa: E402

SET = "web_interface.blueprints.api_v3.control_client.brightness_set"


@pytest.fixture
def saved(api_v3_module, monkeypatch):
    captured = {'ok': True}
    api_v3_module.api_v3.config_manager.load_config.return_value = {}

    def fake_save(_manager, config, **_kwargs):
        captured['config'] = config
        return (True, '') if captured['ok'] else (False, 'disk full')

    monkeypatch.setattr(api_v3_module, '_save_config_atomic', fake_save)
    return captured


def _post(client, body):
    return client.post('/api/v3/config/main', data=json.dumps(body),
                       content_type='application/json')


@pytest.mark.parametrize('value', [55, '55'], ids=['number', 'form-string'])
def test_a_saved_brightness_goes_over_the_socket(api_v3_client, saved, value):
    with patch(SET, return_value={'brightness': 55, 'panel_brightness': 55,
                                  'dimmed': False, 'display_active': True}) as send:
        resp = _post(api_v3_client, {'brightness': value})
    assert resp.status_code == 200, resp.get_json()
    send.assert_called_once_with(55)
    body = resp.get_json()
    assert body['brightness_transport'] == 'socket'
    assert 'brightness_socket_error' not in body
    # Saved as well: the socket's value is transient on the display.
    assert saved['config']['display']['hardware']['brightness'] == 55


@pytest.mark.parametrize('reason', ['no_socket', 'unknown_command', 'pending', 'failed',
                                    'timeout', 'busy'])
def test_without_the_socket_the_config_watcher_applies_it(api_v3_client, saved, reason):
    with patch(SET, side_effect=control_client.ControlError(reason, 'x')):
        resp = _post(api_v3_client, {'brightness': 40})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body['brightness_transport'] == 'config'
    assert body['brightness_socket_error'] == reason
    assert saved['config']['display']['hardware']['brightness'] == 40


def test_a_client_bug_never_fails_the_save(api_v3_client, saved):
    with patch(SET, side_effect=RuntimeError('bug')):
        resp = _post(api_v3_client, {'brightness': 40})
    assert resp.status_code == 200
    assert resp.get_json()['brightness_socket_error'] == 'internal'


def test_the_test_suite_has_no_socket(api_v3_client, saved):
    """LEDMATRIX_CONTROL_SOCKET=off (conftest): the real client says so."""
    body = _post(api_v3_client, {'brightness': 40}).get_json()
    assert body['brightness_transport'] == 'config'
    assert body['brightness_socket_error'] in ('disabled', 'unsupported')


def test_a_save_without_a_brightness_sends_nothing(api_v3_client, saved):
    with patch(SET) as send:
        resp = _post(api_v3_client, {'rows': 32})
    assert resp.status_code == 200
    send.assert_not_called()
    assert 'brightness_transport' not in resp.get_json()


@pytest.mark.parametrize('body', [{'brightness': 0}, {'brightness': 101}, {'brightness': 'x'}])
def test_a_refused_brightness_is_not_sent(api_v3_client, saved, body):
    with patch(SET) as send:
        resp = _post(api_v3_client, body)
    assert resp.status_code == 400
    send.assert_not_called()


def test_a_failed_save_is_not_sent(api_v3_client, saved):
    saved['ok'] = False
    with patch(SET) as send:
        resp = _post(api_v3_client, {'brightness': 40})
    assert resp.status_code == 500
    send.assert_not_called()
