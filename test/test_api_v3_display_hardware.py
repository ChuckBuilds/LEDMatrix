"""Display hardware settings accept what the rgbmatrix library accepts.

Held to the ranges in the pinned library (RGBMatrix::Options::Validate in
lib/options-initialize.cc, the gpio_slowdown check in lib/led-matrix.cc), with
one deliberate exception: rows has no upper bound here, although the library
currently rejects more than 64 per panel. Two ways this used to go wrong:

- The Display form capped cols at 128, chain_length at 24 and
  pwm_lsb_nanoseconds at 500, and its submit handler (fixInvalidNumberInputs)
  rewrites anything past an input's min/max to that bound -- so a wide panel or
  a long chain silently saved as the wrong size.
- The API checked none of these, so a value the library rejects (odd rows,
  parallel 4, pwm_dither_bits 3) saved, and the matrix then refused to start.

Row address type 5 is the SM5368 / B707 row shift register the Waveshare 96x48
V2 needs (Waveshare's own "96X48_1_24_SM5368" panel type in their library fork
just sets rows/cols, row_address_type=5 and BGR); the API used to stop at 4.
"""
import copy
import json
import re
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from flask import Flask

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from test._api_v3_test_helpers import api_v3_client, api_v3_module  # noqa: F401,E402
from test.test_web_settings_ui import REALISTIC_CONFIG  # noqa: E402

#: What a Waveshare RGB-Matrix-P2.5-96x48 V2 (back silkscreen 24S-A1) needed on
#: a Pi 4 with an Adafruit Triple LED Matrix Bonnet, checked on the panel.
WAVESHARE_96X48_V2 = {
    'rows': 48, 'cols': 96, 'chain_length': 1, 'parallel': 1,
    'hardware_mapping': 'regular', 'panel_type': '', 'row_address_type': 5,
    'led_rgb_sequence': 'BGR', 'gpio_slowdown': 8,
}

#: Fields stored under display.runtime; the rest go under display.hardware.
RUNTIME_FIELDS = {'gpio_slowdown'}


def _stored(config, field):
    section = 'runtime' if field in RUNTIME_FIELDS else 'hardware'
    return config['display'][section][field]


def _post(client, body):
    return client.post('/api/v3/config/main', data=json.dumps(body),
                       content_type='application/json')


@pytest.fixture
def saved(api_v3_module, monkeypatch):
    """Capture what save_main_config would write.

    Asserting on the stored value, not just the status code, is what shows the
    value passed validation and landed where DisplayManager reads it.
    """
    captured = {}
    api_v3_module.api_v3.config_manager.load_config.return_value = {}

    def fake_save(_manager, config, **_kwargs):
        captured['config'] = config
        return True, ''

    monkeypatch.setattr(api_v3_module, '_save_config_atomic', fake_save)
    return captured


@pytest.mark.parametrize('as_strings', [False, True], ids=['json-numbers', 'form-strings'])
def test_waveshare_96x48_v2_settings_all_save(api_v3_client, saved, as_strings):
    """The Display form posts every value as a string (json-enc); API clients send numbers."""
    body = {k: str(v) if as_strings else v for k, v in WAVESHARE_96X48_V2.items()}
    response = _post(api_v3_client, body)
    assert response.status_code == 200, response.get_data(as_text=True)[:200]
    for field, value in WAVESHARE_96X48_V2.items():
        assert _stored(saved['config'], field) == value, field


@pytest.mark.parametrize('field,value', [
    ('rows', 8), ('rows', 64), ('rows', 96), ('rows', 128),
    ('cols', 16), ('cols', 192), ('cols', 512),
    ('chain_length', 1), ('chain_length', 32),
    ('parallel', 3),
    ('row_address_type', 0), ('row_address_type', 5),
    ('gpio_slowdown', 0), ('gpio_slowdown', 10),
    ('pwm_bits', 1), ('pwm_bits', 11),
    ('pwm_dither_bits', 0), ('pwm_dither_bits', 2),
    ('pwm_lsb_nanoseconds', 50), ('pwm_lsb_nanoseconds', 3000),
    ('scan_mode', 1),
    ('brightness', 1), ('brightness', 100),
    ('limit_refresh_rate_hz', 0), ('limit_refresh_rate_hz', 1000),
])
def test_values_in_range_are_saved(api_v3_client, saved, field, value):
    response = _post(api_v3_client, {field: value})
    assert response.status_code == 200, response.get_data(as_text=True)[:200]
    assert _stored(saved['config'], field) == value


@pytest.mark.parametrize('field,value', [
    ('rows', 6), ('rows', 47), ('rows', 97), ('rows', '48.5'),
    ('cols', 15), ('cols', 96.5), ('cols', True), ('cols', 'wide'),
    ('chain_length', 0),
    ('parallel', 0), ('parallel', 4),
    ('row_address_type', -1), ('row_address_type', 6),
    ('gpio_slowdown', -1), ('gpio_slowdown', 11),
    ('pwm_bits', 0), ('pwm_bits', 12),
    ('pwm_dither_bits', 3),
    ('pwm_lsb_nanoseconds', 49), ('pwm_lsb_nanoseconds', 3001),
    ('scan_mode', 2),
    ('brightness', 0), ('brightness', 101),
    ('limit_refresh_rate_hz', -1),
])
def test_values_out_of_range_are_refused(api_v3_client, saved, field, value):
    """Refused with a message naming the field, and nothing written."""
    response = _post(api_v3_client, {field: value})
    assert response.status_code == 400
    assert field in response.get_json()['message']
    assert 'config' not in saved


@pytest.fixture
def display_page(monkeypatch):
    """Render the Display settings partial for a given config."""
    from web_interface.blueprints import pages_v3 as pv

    def render(config):
        base = PROJECT_ROOT / 'web_interface'
        app = Flask(__name__, template_folder=str(base / 'templates'),
                    static_folder=str(base / 'static'))
        app.config['TESTING'] = True
        config_manager = MagicMock()
        config_manager.load_config.return_value = config
        config_manager.get_raw_file_content.return_value = config
        config_manager.get_config_path.return_value = 'config/config.json'
        config_manager.get_secrets_path.return_value = 'config/config_secrets.json'
        monkeypatch.setattr(pv.pages_v3, 'config_manager', config_manager, raising=False)
        monkeypatch.setattr(pv.pages_v3, 'plugin_manager', MagicMock(plugins={}), raising=False)
        app.register_blueprint(pv.pages_v3, url_prefix='/v3')
        response = app.test_client().get('/v3/partials/display')
        assert response.status_code == 200
        return response.get_data(as_text=True)

    return render


def _config_with(hardware=None, runtime=None):
    config = copy.deepcopy(REALISTIC_CONFIG)
    config['display']['hardware'].update(hardware or {})
    config['display']['runtime'].update(runtime or {})
    return config


def _input_tag(body, input_id):
    match = re.search(r'<input[^>]*\bid="%s"[^>]*>' % re.escape(input_id), body)
    assert match, f'no <input id="{input_id}">'
    return match.group(0)


def _attr(tag, name):
    match = re.search(r'\s%s="([^"]*)"' % name, tag)
    return match.group(1) if match else None


def _selected_option(body, select_id):
    select = re.search(r'<select id="%s".*?</select>' % select_id, body, re.S)
    assert select, f'no <select id="{select_id}">'
    return re.findall(r'<option value="([^"]*)"\s+selected\s*>', select.group(0))


@pytest.mark.parametrize('input_id,expected', [
    ('rows', {'min': '8', 'max': None, 'step': '2'}),
    ('cols', {'min': '16', 'max': None}),
    ('chain_length', {'min': '1', 'max': None}),
    ('parallel', {'min': '1', 'max': '3'}),
    ('gpio_slowdown', {'min': '0', 'max': '10'}),
    ('pwm_bits', {'min': '1', 'max': '11'}),
    ('pwm_dither_bits', {'min': '0', 'max': '2'}),
    ('pwm_lsb_nanoseconds', {'min': '50', 'max': '3000'}),
    ('limit_refresh_rate_hz', {'min': '0', 'max': '1000'}),
])
def test_form_limits_match_the_library(display_page, input_id, expected):
    """fixInvalidNumberInputs rewrites a value past min/max on submit, so these
    attributes are the real limits: a max below the library's clamps panels
    that would work, and one above it saves a value the matrix rejects."""
    tag = _input_tag(display_page(_config_with()), input_id)
    for name, value in expected.items():
        assert _attr(tag, name) == value, f'{input_id} {name}'


def test_waveshare_96x48_v2_config_renders_back_unchanged(display_page):
    """Saving the Display tab posts what it rendered, so each value must render as stored.

    Before row address type 5 was in the dropdown no option was selected, the
    browser posted the first one (0), and one save scrambled the panel again.
    """
    hardware = {k: v for k, v in WAVESHARE_96X48_V2.items() if k not in RUNTIME_FIELDS}
    body = display_page(_config_with(hardware=hardware, runtime={'gpio_slowdown': 8}))

    assert _selected_option(body, 'row_address_type') == ['5']
    assert _selected_option(body, 'led_rgb_sequence') == ['BGR']
    assert _selected_option(body, 'hardware_mapping') == ['regular']
    for input_id in ('rows', 'cols', 'chain_length', 'parallel', 'gpio_slowdown'):
        assert _attr(_input_tag(body, input_id), 'value') == str(WAVESHARE_96X48_V2[input_id]), input_id


@pytest.mark.parametrize('field,section', [
    ('gpio_slowdown', 'runtime'), ('pwm_dither_bits', 'hardware'),
    ('limit_refresh_rate_hz', 'hardware'),
])
def test_a_stored_zero_renders_as_zero(display_page, field, section):
    """`value or default` showed a stored 0 as the default, and the next save wrote it back."""
    body = display_page(_config_with(**{section: {field: 0}}))
    assert _attr(_input_tag(body, field), 'value') == '0'
