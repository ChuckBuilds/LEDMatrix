"""plugin_system.auto_discover / auto_load_enabled / development_mode.

The General tab offered three toggles for these, with help tips promising
"plugins installed but dormant" and "verbose logging". Nothing in src/,
web_interface/ or scripts/ reads them: every enabled plugin is discovered and
loaded regardless. The toggles are gone; the keys stay tolerated in stored
configs, and saving the General tab must not rewrite them.
"""
import copy
import json
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from test._api_v3_test_helpers import api_v3_client, api_v3_module  # noqa: F401,E402
from test.test_web_settings_ui import REALISTIC_CONFIG, client  # noqa: F401,E402

LEGACY_FLAGS = ('auto_discover', 'auto_load_enabled', 'development_mode')


def test_the_general_tab_no_longer_offers_the_toggles(client):
    body = client.get('/v3/partials/general').get_data(as_text=True)
    for flag in LEGACY_FLAGS:
        assert f'name="{flag}"' not in body, flag
        assert f'setting-general-{flag}' not in body, flag
    # The setting that does work stays.
    assert 'name="plugins_directory"' in body


@pytest.fixture
def saved(api_v3_module, monkeypatch):
    captured = {}
    stored = copy.deepcopy(REALISTIC_CONFIG)
    stored['plugin_system'].update(
        auto_discover=True, auto_load_enabled=True, development_mode=True)
    api_v3_module.api_v3.config_manager.load_config.return_value = stored

    def fake_save(_manager, config, **_kwargs):
        captured['config'] = config
        return True, ''

    monkeypatch.setattr(api_v3_module, '_save_config_atomic', fake_save)
    return captured


def test_saving_the_general_form_leaves_stored_flags_alone(api_v3_client, saved):
    # What the General form posts now: no checkbox fields for the flags.
    resp = api_v3_client.post('/api/v3/config/main', data={
        'timezone': 'America/Chicago', 'city': 'Dallas', 'state': 'Texas',
        'country': 'US', 'plugins_directory': 'plugin-repos',
    })
    assert resp.status_code == 200, resp.get_json()
    plugin_system = saved['config']['plugin_system']
    # Missing used to mean "unchecked" and saved all three as false.
    assert all(plugin_system[flag] is True for flag in LEGACY_FLAGS), plugin_system
    assert plugin_system['plugins_directory'] == 'plugin-repos'


def test_an_api_client_can_still_store_a_flag(api_v3_client, saved):
    resp = api_v3_client.post('/api/v3/config/main', data=json.dumps({
        'timezone': 'America/Chicago', 'development_mode': False,
    }), content_type='application/json')
    assert resp.status_code == 200, resp.get_json()
    plugin_system = saved['config']['plugin_system']
    assert plugin_system['development_mode'] is False
    assert plugin_system['auto_discover'] is True
