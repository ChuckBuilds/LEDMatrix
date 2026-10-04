"""Four web answers that disagreed with the rig they describe (found on ledpi).

1. POST /config/schedule refused the schedule GET returns on a fresh install
   (config.template.json: per-day, every day off, schedule disabled) with
   "At least one day must be enabled", as did /config/dim-schedule. A
   disabled schedule needs no enabled day.
2. A brightness-only POST /config/main answered ``restart_required: true``,
   though the display applies brightness live (brightness.set over the
   socket, and the config watcher). The flag now says whether anything
   changed that the running display does not pick up by itself.
3. /health stayed "healthy" with the display service stopped: only the
   sub-checks changed. Service inactive, no socket and no live heartbeat
   is now ``display_loop: stopped`` and "degraded".
4. /display/current-status kept answering ``is_display_active: true`` from
   the cache for up to 120 s after the display stopped. With no socket and
   no live heartbeat it is now unknown.
"""

import copy
import json
import os
import sys
import time
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from test._api_v3_test_helpers import api_v3_client, api_v3_module  # noqa: F401,E402

from src import display_watchdog  # noqa: E402
from src.ipc import client as control_client  # noqa: E402
from web_interface import display_state  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
TEMPLATE = json.loads((REPO / 'config' / 'config.template.json').read_text(encoding='utf-8'))


@pytest.fixture
def store(api_v3_module, monkeypatch):
    state = {'config': {}, 'saves': 0}
    api_v3_module.api_v3.config_manager.load_config.side_effect = \
        lambda *a, **k: copy.deepcopy(state['config'])

    def fake_save(_manager, config, **_kwargs):
        state['config'] = copy.deepcopy(config)
        state['saves'] += 1
        return True, ''

    monkeypatch.setattr(api_v3_module, '_save_config_atomic', fake_save)
    return state


# --- 1. schedules ---------------------------------------------------------------

SCHEDULE_ROUTES = [('/api/v3/config/schedule', 'schedule'),
                   ('/api/v3/config/dim-schedule', 'dim_schedule')]


@pytest.mark.parametrize('route,section', SCHEDULE_ROUTES)
def test_the_templates_disabled_per_day_schedule_saves_back(api_v3_client, store,
                                                            route, section):
    stored = copy.deepcopy(TEMPLATE[section])
    stored['mode'] = 'per-day'
    assert stored['enabled'] is False
    assert not any(day['enabled'] for day in stored['days'].values())
    store['config'] = {section: copy.deepcopy(stored)}

    read = api_v3_client.get(route).get_json()['data']
    resp = api_v3_client.post(route, json=read)

    assert resp.status_code == 200, resp.get_json()
    saved = store['config'][section]
    assert saved['enabled'] is False and saved['mode'] == 'per-day'
    # The disabled days keep their times: switching one on finds them.
    assert saved['days'] == stored['days']


@pytest.mark.parametrize('route,section', SCHEDULE_ROUTES)
def test_an_enabled_per_day_schedule_still_needs_a_day(api_v3_client, store, route, section):
    body = copy.deepcopy(TEMPLATE[section])
    body.update(enabled=True, mode='per-day')
    resp = api_v3_client.post(route, json=body)
    assert resp.status_code == 400
    assert 'At least one day must be enabled' in resp.get_json()['message']
    assert store['saves'] == 0


@pytest.mark.parametrize('route', [r for r, _ in SCHEDULE_ROUTES])
def test_the_pickers_form_post_with_every_day_off_saves(api_v3_client, store, route):
    """What schedule-picker.js posts: flat hidden inputs, booleans as strings,
    times for every day."""
    body = {'enabled': 'false', 'mode': 'per_day', 'start_time': '07:00', 'end_time': '23:00'}
    for day in ('monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday'):
        body.update({f'{day}_enabled': 'false', f'{day}_start': '06:30', f'{day}_end': '22:15'})
    resp = api_v3_client.post(route, json=body)
    assert resp.status_code == 200, resp.get_json()


def test_an_invalid_time_on_a_disabled_day_is_dropped_not_refused(api_v3_client, store):
    body = {'enabled': False, 'mode': 'per-day',
            'days': {'monday': {'enabled': False, 'start_time': 'soon', 'end_time': '22:00'}}}
    resp = api_v3_client.post('/api/v3/config/schedule', json=body)
    assert resp.status_code == 200, resp.get_json()
    assert store['config']['schedule']['days']['monday'] == {'enabled': False,
                                                             'end_time': '22:00'}


# --- 2. restart_required on /config/main ------------------------------------------

STORED_MAIN = {
    'timezone': 'America/Chicago',
    'display': {
        'hardware': {'rows': 32, 'cols': 64, 'chain_length': 2, 'brightness': 90,
                     'disable_hardware_pulsing': False, 'inverse_colors': False,
                     'show_refresh_rate': False},
        'runtime': {'gpio_slowdown': 4},
        'display_durations': {'clock': 15},
        'use_short_date_format': False,
    },
}


@pytest.fixture
def main_store(store):
    store['config'] = copy.deepcopy(STORED_MAIN)
    return store


def _save_main(client, body):
    with patch('web_interface.blueprints.api_v3.control_client.brightness_set',
               side_effect=control_client.ControlError('no_socket', 'x')):
        resp = client.post('/api/v3/config/main', data=json.dumps(body),
                           content_type='application/json')
    assert resp.status_code == 200, resp.get_json()
    return resp.get_json()


def test_a_brightness_only_save_needs_no_restart(api_v3_client, main_store):
    body = _save_main(api_v3_client, {'brightness': 40})
    assert main_store['config']['display']['hardware']['brightness'] == 40
    assert body['restart_required'] is False


def test_a_brightness_save_on_a_config_without_a_display_section(api_v3_client, store):
    """The route creates display.hardware and display.runtime on the way;
    empty sections are not a change."""
    store['config'] = {}
    assert _save_main(api_v3_client, {'brightness': 40})['restart_required'] is False


def test_the_display_form_with_only_brightness_changed_needs_no_restart(api_v3_client,
                                                                       main_store):
    hw = STORED_MAIN['display']['hardware']
    body = {'__form_section': 'display', 'rows': 32, 'cols': 64, 'chain_length': 2,
            'brightness': 55, 'gpio_slowdown': 4}
    body.update({k: 'on' for k in ('disable_hardware_pulsing', 'inverse_colors',
                                   'show_refresh_rate') if hw[k]})
    assert _save_main(api_v3_client, body)['restart_required'] is False


def test_a_mode_duration_needs_no_restart(api_v3_client, main_store):
    body = _save_main(api_v3_client, {'duration__clock': 40})
    assert main_store['config']['display']['display_durations']['clock'] == 40
    assert body['restart_required'] is False


@pytest.mark.parametrize('change', [{'rows': 64}, {'brightness': 40, 'chain_length': 3},
                                    {'gpio_slowdown': 2}, {'timezone': 'UTC'}])
def test_a_setting_the_display_reads_at_startup_still_needs_one(api_v3_client, main_store,
                                                                change):
    assert _save_main(api_v3_client, change)['restart_required'] is True


def test_restart_needed_compares_leaves():
    from web_interface.blueprints.api_v3.config import restart_needed
    before = {'display': {'hardware': {'brightness': 90, 'rows': 32}}}
    assert not restart_needed(before, copy.deepcopy(before))
    assert not restart_needed(before, {'display': {'hardware': {'brightness': 10, 'rows': 32},
                                                   'runtime': {}}})
    assert restart_needed(before, {'display': {'hardware': {'brightness': 90}}})  # removed
    assert not restart_needed({}, {'clock': {'enabled': True}}, live_paths=[('clock',)])
    assert restart_needed({}, {'clockwork': {'enabled': True}}, live_paths=[('clock',)])


# --- 3 and 4. a stopped display -----------------------------------------------------

@pytest.fixture
def no_display(monkeypatch, tmp_path):
    """A Pi whose display service has stopped: the socket is expected here
    but does not answer, and systemd took the heartbeat's directory away."""
    monkeypatch.setattr(display_state, 'socket_supported', lambda: True)
    monkeypatch.setattr(display_state, 'client_socket_paths', lambda: [str(tmp_path / 'gone')])
    monkeypatch.setattr(display_state, 'read_state', lambda: None)
    path = tmp_path / 'display-heartbeat.json'
    monkeypatch.setattr(display_watchdog, 'HEARTBEAT_PATH', str(path))

    def beat(age, pid=None):
        path.write_text(json.dumps({'pid': os.getpid() if pid is None else pid,
                                    'mono': time.monotonic() - age,
                                    'wall': time.time() - age}))
    return beat


@pytest.fixture
def service(monkeypatch):
    status = {'active': False, 'returncode': 3, 'stdout': 'inactive', 'stderr': ''}
    monkeypatch.setattr('web_interface.blueprints.api_v3.misc._get_display_service_status',
                        lambda: dict(status))
    return status


@pytest.fixture
def fresh_preview(tmp_path, monkeypatch):
    """The preview frame the display left behind, under 60 s old: on its own
    it kept the hardware check "connected"."""
    from web_interface import display_preview
    snapshot = tmp_path / 'preview.png'
    snapshot.write_bytes(b'png')
    monkeypatch.setattr(display_preview, 'SNAPSHOT_PATH', str(snapshot))


def _health(client):
    resp = client.get('/api/v3/health')
    assert resp.status_code == 200, resp.get_json()
    return resp.get_json()['data']


class TestHealth:
    def test_a_stopped_display_service_is_degraded(self, api_v3_client, no_display, service,
                                                   fresh_preview):
        data = _health(api_v3_client)
        assert data['services']['display_service']['status'] == 'inactive'
        assert data['checks']['display_loop']['status'] == 'stopped'
        assert data['status'] == 'degraded'

    def test_a_service_still_starting_is_not(self, api_v3_client, no_display, service,
                                             fresh_preview):
        """Active, before its socket and first heartbeat: not stopped."""
        service.update(active=True, stdout='active', returncode=0)
        data = _health(api_v3_client)
        assert data['checks']['display_loop']['status'] == 'not_reported'
        assert data['status'] == 'healthy'

    def test_a_display_run_by_hand_is_not_stopped(self, api_v3_client, no_display, service,
                                                  fresh_preview):
        """The service is off but a display process beats (sudo python3 run.py)."""
        no_display(age=2)
        data = _health(api_v3_client)
        assert data['checks']['display_loop']['status'] == 'running'
        assert data['status'] == 'healthy'

    @pytest.mark.parametrize('platform', ['no_unix_sockets', 'socket_off'])
    def test_without_a_socket_to_expect_nothing_changes(self, api_v3_client, no_display,
                                                        service, fresh_preview, monkeypatch,
                                                        platform):
        """Windows and the dev server (no systemd unit), or the socket
        deliberately off: no heartbeat is no signal, as before."""
        if platform == 'no_unix_sockets':
            monkeypatch.setattr(display_state, 'socket_supported', lambda: False)
        else:
            monkeypatch.setattr(display_state, 'client_socket_paths', lambda: [])
        service.update(returncode=-1, stdout='', stderr='systemctl not found')
        data = _health(api_v3_client)
        assert data['checks']['display_loop']['status'] == 'not_reported'
        assert data['status'] == 'healthy'

    def test_the_status_only_answer_says_degraded(self, api_v3_client, no_display, service,
                                                  fresh_preview, monkeypatch):
        monkeypatch.setattr('web_interface.blueprints.api_v3.misc.request_is_authenticated',
                            lambda: False)
        resp = api_v3_client.get('/api/v3/health')
        assert resp.get_json()['data'] == {'status': 'degraded'}


class TestCurrentStatus:
    CACHED = {'mode': 'clock', 'plugin_id': 'clock', 'is_display_active': True,
              'on_demand_active': False, 'last_updated': None}

    @pytest.fixture
    def cached(self, api_v3_module):
        entry = dict(self.CACHED, last_updated=time.time() - 30)
        cache = api_v3_module.api_v3.cache_manager
        cache.get.side_effect = lambda key, *a, **kw: (
            dict(entry) if key == 'display_current_state' else None)
        return entry

    def _status(self, client):
        resp = client.get('/api/v3/display/current-status')
        assert resp.status_code == 200
        return resp.get_json()['data']

    def test_a_stopped_display_is_not_reported_active(self, api_v3_client, no_display, cached):
        data = self._status(api_v3_client)
        assert not data.get('is_display_active')
        assert data['mode'] is None and data['last_updated'] is None
        assert data['source'] == 'cache'

    def test_a_stale_heartbeat_is_not_active_either(self, api_v3_client, no_display, cached):
        no_display(age=display_watchdog.HEARTBEAT_STALE_SECONDS + 5)
        assert self._status(api_v3_client)['mode'] is None

    @pytest.mark.skipif(os.name != 'posix', reason='process_exists answers only on POSIX')
    def test_a_heartbeat_from_a_dead_process_is_not_active(self, api_v3_client, no_display,
                                                           cached):
        no_display(age=1, pid=2 ** 22 + 12345)
        assert self._status(api_v3_client)['mode'] is None

    def test_a_live_heartbeat_without_a_socket_reads_the_cache(self, api_v3_client,
                                                               no_display, cached):
        """An older display with no socket, still running."""
        no_display(age=2)
        data = self._status(api_v3_client)
        assert data['mode'] == 'clock' and data['is_display_active'] is True

    @pytest.mark.parametrize('platform', ['no_unix_sockets', 'socket_off'])
    def test_without_a_socket_to_expect_the_cache_answers(self, api_v3_client, no_display,
                                                          cached, monkeypatch, platform):
        if platform == 'no_unix_sockets':
            monkeypatch.setattr(display_state, 'socket_supported', lambda: False)
        else:
            monkeypatch.setattr(display_state, 'client_socket_paths', lambda: [])
        data = self._status(api_v3_client)
        assert data['mode'] == 'clock' and data['is_display_active'] is True
