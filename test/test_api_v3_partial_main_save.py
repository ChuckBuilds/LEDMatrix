"""POST /config/main: a partial JSON body changes only what it sends.

The settings forms post every field, and a browser leaves an unchecked box out,
so for a form a missing checkbox means False. JSON API clients send only what
they change. Treating their missing keys as unchecked meant:

- the MQTT bridge's Home Assistant brightness slider (``{"brightness": N}``)
  turned off disable_hardware_pulsing, inverse_colors, show_refresh_rate and
  use_short_date_format on every change;
- the documented timezone/location update turned off web_display_autostart and
  weekly automatic updates.

The v3 forms post JSON as well (htmx json-enc), so they mark themselves with a
hidden ``__form_section`` input; these tests pin both halves of that contract.

Also here: the Vegas cycle-time fields no longer land in display_durations
(and a blank one no longer 400s the Display save), the Raw JSON editor starts
auto-update setup like the General form does, and both schedule POSTs accept
the per-day shape their GETs return.
"""

import copy
import json
import re
from pathlib import Path

import pytest

from test._api_v3_test_helpers import api_v3_client, api_v3_module  # noqa: F401

REPO = Path(__file__).resolve().parent.parent
PARTIALS = REPO / 'web_interface' / 'templates' / 'v3' / 'partials'

STORED = {
    'web_display_autostart': True,
    'auto_update': {'enabled': True},
    'timezone': 'America/Chicago',
    'plugin_system': {'auto_discover': True, 'auto_load_enabled': True,
                      'development_mode': True},
    'display': {
        'hardware': {'rows': 32, 'cols': 64, 'chain_length': 2, 'brightness': 90,
                     'disable_hardware_pulsing': True, 'inverse_colors': True,
                     'show_refresh_rate': True},
        'runtime': {'gpio_slowdown': 4},
        'use_short_date_format': True,
        'double_sided': {'enabled': True, 'copies': 2, 'axis': 'horizontal'},
        'vegas_scroll': {'enabled': True, 'auto_trim': True,
                         'dynamic_duration_enabled': True,
                         'continuous_scroll': True, 'smooth_scroll': True},
    },
}


@pytest.fixture
def saved(api_v3_module, monkeypatch):
    captured = {}
    api_v3_module.api_v3.config_manager.load_config.side_effect = \
        lambda *a, **k: copy.deepcopy(STORED)

    def fake_save(_manager, config, **_kwargs):
        captured['config'] = config
        return True, ''

    monkeypatch.setattr(api_v3_module, '_save_config_atomic', fake_save)
    from web_interface import auto_update
    monkeypatch.setattr(auto_update, 'start_setup_if_needed', lambda *a, **k: None)
    return captured


def _post_json(client, body):
    return client.post('/api/v3/config/main', data=json.dumps(body),
                       content_type='application/json')


class TestJsonPartialSaves:
    def test_mqtt_bridge_brightness_changes_only_brightness(self, api_v3_client, saved):
        # integrations/mqtt_bridge/ledmatrix_mqtt_bridge.py set_brightness
        resp = _post_json(api_v3_client, {'brightness': 40})
        assert resp.status_code == 200, resp.get_json()
        display = saved['config']['display']
        assert display['hardware']['brightness'] == 40
        assert display['hardware']['disable_hardware_pulsing'] is True
        assert display['hardware']['inverse_colors'] is True
        assert display['hardware']['show_refresh_rate'] is True
        assert display['use_short_date_format'] is True

    def test_timezone_only_keeps_autostart_and_auto_update(self, api_v3_client, saved):
        resp = _post_json(api_v3_client, {'timezone': 'UTC'})
        assert resp.status_code == 200, resp.get_json()
        config = saved['config']
        assert config['timezone'] == 'UTC'
        assert config['web_display_autostart'] is True
        assert config['auto_update'] == {'enabled': True}

    def test_location_only_keeps_autostart_and_auto_update(self, api_v3_client, saved):
        resp = _post_json(api_v3_client, {'city': 'Paris', 'country': 'FR'})
        assert resp.status_code == 200, resp.get_json()
        config = saved['config']
        assert config['location'] == {'city': 'Paris', 'country': 'FR'}
        assert config['web_display_autostart'] is True
        assert config['auto_update'] == {'enabled': True}

    @pytest.mark.parametrize('key', ['auto_discover', 'auto_load_enabled', 'development_mode'])
    def test_a_legacy_plugin_system_toggle_is_not_a_general_save(self, api_v3_client, saved, key):
        # These left the General form; a client still sending one must not
        # have the general-settings checkboxes treated as unchecked.
        resp = api_v3_client.post('/api/v3/config/main', data={key: 'on'},
                                  content_type='application/x-www-form-urlencoded')
        assert resp.status_code == 200, resp.get_json()
        assert saved['config']['web_display_autostart'] is True
        assert saved['config']['auto_update'] == {'enabled': True}

    def test_vegas_speed_only_keeps_vegas_toggles(self, api_v3_client, saved):
        resp = _post_json(api_v3_client, {'vegas_scroll_speed': 80})
        assert resp.status_code == 200, resp.get_json()
        vegas = saved['config']['display']['vegas_scroll']
        assert vegas['scroll_speed'] == 80
        for key in ('enabled', 'auto_trim', 'dynamic_duration_enabled',
                    'continuous_scroll', 'smooth_scroll'):
            assert vegas[key] is True, key

    def test_double_sided_axis_only_keeps_enabled(self, api_v3_client, saved):
        resp = _post_json(api_v3_client, {'double_sided_axis': 'horizontal'})
        assert resp.status_code == 200, resp.get_json()
        assert saved['config']['display']['double_sided']['enabled'] is True

    def test_json_can_still_turn_a_checkbox_off(self, api_v3_client, saved):
        resp = _post_json(api_v3_client, {'inverse_colors': False, 'auto_update_enabled': False})
        assert resp.status_code == 200, resp.get_json()
        assert saved['config']['display']['hardware']['inverse_colors'] is False
        assert saved['config']['display']['hardware']['disable_hardware_pulsing'] is True
        assert saved['config']['auto_update'] == {'enabled': False}

    def test_json_with_charset_is_still_json(self, api_v3_client, saved):
        resp = api_v3_client.post('/api/v3/config/main', data=json.dumps({'brightness': 41}),
                                  content_type='application/json; charset=utf-8')
        assert resp.status_code == 200, resp.get_json()
        assert saved['config']['display']['hardware']['brightness'] == 41
        assert saved['config']['display']['hardware']['inverse_colors'] is True

    def test_a_non_object_body_is_refused(self, api_v3_client, saved):
        assert _post_json(api_v3_client, [1, 2]).status_code == 400


class TestFormSavesStillUncheck:
    """Unchecking a box in the UI must still save false."""

    def test_marked_json_form_post_unchecks_missing_display_boxes(self, api_v3_client, saved):
        resp = _post_json(api_v3_client, {'__form_section': 'display', 'brightness': '40',
                                          'vegas_scroll_speed': '50'})
        assert resp.status_code == 200, resp.get_json()
        display = saved['config']['display']
        for key in ('disable_hardware_pulsing', 'inverse_colors', 'show_refresh_rate'):
            assert display['hardware'][key] is False, key
        assert display['use_short_date_format'] is False
        assert display['vegas_scroll']['enabled'] is False
        assert '__form_section' not in saved['config']

    def test_marked_json_form_post_keeps_checked_boxes(self, api_v3_client, saved):
        resp = _post_json(api_v3_client, {'__form_section': 'display', 'brightness': '40',
                                          'inverse_colors': 'on'})
        assert resp.status_code == 200, resp.get_json()
        assert saved['config']['display']['hardware']['inverse_colors'] is True
        assert saved['config']['display']['hardware']['show_refresh_rate'] is False

    def test_marked_general_form_unchecks_autostart_and_auto_update(self, api_v3_client, saved):
        resp = _post_json(api_v3_client, {'__form_section': 'general', 'timezone': 'UTC'})
        assert resp.status_code == 200, resp.get_json()
        assert saved['config']['web_display_autostart'] is False
        assert saved['config']['auto_update'] == {'enabled': False}

    def test_form_encoded_post_unchecks_missing_boxes(self, api_v3_client, saved):
        resp = api_v3_client.post('/api/v3/config/main', data={'brightness': '40'},
                                  content_type='application/x-www-form-urlencoded')
        assert resp.status_code == 200, resp.get_json()
        assert saved['config']['display']['hardware']['inverse_colors'] is False

    @pytest.mark.parametrize('partial', ['general.html', 'display.html', 'durations.html'])
    def test_every_config_main_form_carries_the_marker(self, partial):
        html = (PARTIALS / partial).read_text(encoding='utf-8')
        form = re.search(r'<form hx-post="/api/v3/config/main".*?</form>', html, re.S)
        assert form, f'{partial} no longer posts to /config/main'
        assert re.search(r'<input type="hidden" name="__form_section" value="\w+">',
                         form.group(0)), f'{partial} form lost its __form_section marker'


class TestVegasCycleDurations:
    def test_cycle_durations_are_not_display_durations(self, api_v3_client, saved):
        resp = api_v3_client.post('/api/v3/config/main', data={
            'vegas_scroll_enabled': 'on', 'vegas_min_cycle_duration': '90',
            'vegas_max_cycle_duration': '300'})
        assert resp.status_code == 200, resp.get_json()
        display = saved['config']['display']
        assert display['vegas_scroll']['min_cycle_duration'] == 90
        assert display['vegas_scroll']['max_cycle_duration'] == 300
        durations = display.get('display_durations', {})
        assert 'vegas_min_cycle_duration' not in durations
        assert 'vegas_max_cycle_duration' not in durations
        assert 'vegas_min_cycle_duration' not in saved['config']

    def test_blank_cycle_duration_does_not_reject_the_save(self, api_v3_client, saved):
        resp = api_v3_client.post('/api/v3/config/main', data={
            'vegas_scroll_enabled': 'on', 'vegas_scroll_speed': '60',
            'vegas_min_cycle_duration': ''})
        assert resp.status_code == 200, resp.get_json()
        assert saved['config']['display']['vegas_scroll']['scroll_speed'] == 60

    def test_real_display_durations_still_save(self, api_v3_client, saved):
        resp = _post_json(api_v3_client, {'clock_duration': '45'})
        assert resp.status_code == 200, resp.get_json()
        assert saved['config']['display']['display_durations']['clock_duration'] == 45

    def test_per_mode_duration_saves_and_blank_clears_it(self, api_v3_client, saved, api_v3_module):
        # The Rotation page leaves a mode blank to mean "the plugin's own
        # duration"; a saved value overrides the plugin, so blank must remove
        # it rather than 400 or pin a number.
        stored = copy.deepcopy(STORED)
        stored['display']['display_durations'] = {'weather_current': 40, 'clock': 20}
        api_v3_module.api_v3.config_manager.load_config.side_effect =             lambda *a, **k: copy.deepcopy(stored)
        resp = _post_json(api_v3_client, {'__form_section': 'durations',
                                          'duration__clock': '45',
                                          'duration__weather_current': ''})
        assert resp.status_code == 200, resp.get_json()
        assert saved['config']['display']['display_durations'] == {'clock': 45}


class TestRawSaveStartsAutoUpdateSetup:
    @pytest.fixture
    def raw_env(self, api_v3_module, monkeypatch):
        cm = api_v3_module.api_v3.config_manager
        cm.get_raw_file_content.return_value = {'timezone': 'UTC', 'auto_update': {'enabled': False}}
        calls = []
        from web_interface import auto_update
        monkeypatch.setattr(auto_update, 'start_setup_if_needed',
                            lambda was, config: calls.append((was, config)) or 'Setup note.')
        return cm, calls

    def test_enabling_from_raw_json_calls_setup(self, api_v3_client, raw_env):
        cm, calls = raw_env
        body = {'timezone': 'UTC', 'auto_update': {'enabled': True}}
        resp = api_v3_client.post('/api/v3/config/raw/main', json=body)
        assert resp.status_code == 200, resp.get_json()
        cm.save_raw_file_content.assert_called_once_with('main', body)
        assert calls == [(False, body)]
        assert 'Setup note.' in resp.get_json()['message']

    def test_previously_enabled_is_passed_through(self, api_v3_client, raw_env):
        cm, calls = raw_env
        cm.get_raw_file_content.return_value = {'auto_update': {'enabled': True}}
        body = {'auto_update': {'enabled': True}}
        assert api_v3_client.post('/api/v3/config/raw/main', json=body).status_code == 200
        assert calls == [(True, body)]


class TestSchedulesAcceptTheirGetShape:
    PER_DAY = {
        'enabled': True, 'mode': 'per-day', 'dim_brightness': 20,
        'days': {
            'monday': {'enabled': True, 'start_time': '21:15', 'end_time': '06:45'},
            'tuesday': {'enabled': False},
            'wednesday': {'enabled': True, 'start_time': '22:00', 'end_time': '05:30'},
            'thursday': {'enabled': True, 'start_time': '20:00', 'end_time': '07:00'},
            'friday': {'enabled': True, 'start_time': '23:00', 'end_time': '08:00'},
            'saturday': {'enabled': True, 'start_time': '23:30', 'end_time': '09:00'},
            'sunday': {'enabled': True, 'start_time': '21:00', 'end_time': '07:00'},
        },
    }

    @pytest.fixture
    def store(self, api_v3_module, monkeypatch):
        state = {'config': {}}
        api_v3_module.api_v3.config_manager.load_config.side_effect = \
            lambda *a, **k: copy.deepcopy(state['config'])

        def fake_save(_manager, config, **_kwargs):
            state['config'] = copy.deepcopy(config)
            return True, ''

        monkeypatch.setattr(api_v3_module, '_save_config_atomic', fake_save)
        return state

    @pytest.mark.parametrize('route,section,extra', [
        ('/api/v3/config/dim-schedule', 'dim_schedule', {'dim_brightness': 20}),
        ('/api/v3/config/schedule', 'schedule', {}),
    ])
    def test_get_output_posts_back_unchanged(self, api_v3_client, store, route, section, extra):
        body = {k: v for k, v in self.PER_DAY.items() if k != 'dim_brightness'}
        body.update(extra)
        store['config'] = {section: copy.deepcopy(body)}
        read = api_v3_client.get(route).get_json()['data']
        resp = api_v3_client.post(route, json=read)
        assert resp.status_code == 200, resp.get_json()
        assert store['config'][section]['days'] == body['days']

    def test_flat_form_keys_still_work(self, api_v3_client, store):
        body = {'enabled': True, 'mode': 'per-day', 'dim_brightness': 25,
                'monday_enabled': 'on', 'monday_start': '19:00', 'monday_end': '06:00'}
        resp = api_v3_client.post('/api/v3/config/dim-schedule', json=body)
        assert resp.status_code == 200, resp.get_json()
        assert store['config']['dim_schedule']['days']['monday'] == {
            'enabled': True, 'start_time': '19:00', 'end_time': '06:00'}
