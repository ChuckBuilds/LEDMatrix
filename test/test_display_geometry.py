"""The web preview must be the size DisplayManager actually renders at.

Before src/display_geometry.py, the preview endpoints computed
``cols * chain_length`` by ``rows * parallel`` themselves, ignored
``display.double_sided`` (so a double-sided panel previewed two screens side
by side), and the ``chain_length`` fallback was 2 in DisplayManager but 1 in
the Starlark magnify default and the sync handshake.
"""

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from flask import Flask

from src.display_geometry import (
    DEFAULT_CHAIN_LENGTH, DEFAULT_COLS, DEFAULT_PARALLEL, DEFAULT_ROWS,
    logical_size, physical_size,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


def _config(hardware=None, double_sided=None):
    display = {'hardware': hardware or {}}
    if double_sided is not None:
        display['double_sided'] = double_sided
    return {'display': display}


def test_defaults_match_the_config_template():
    template = json.loads(
        (REPO_ROOT / 'config' / 'config.template.json').read_text(encoding='utf-8'))
    hw = template['display']['hardware']
    assert (DEFAULT_ROWS, DEFAULT_COLS, DEFAULT_CHAIN_LENGTH, DEFAULT_PARALLEL) == (
        hw['rows'], hw['cols'], hw['chain_length'], hw['parallel'])


def test_missing_hardware_uses_the_template_defaults():
    assert physical_size({}) == (128, 32)
    assert logical_size({}) == (128, 32)


def test_parallel_multiplies_height():
    cfg = _config({'rows': 32, 'cols': 64, 'chain_length': 2, 'parallel': 2})
    assert physical_size(cfg) == (128, 64)


def test_double_sided_horizontal_previews_one_screen():
    cfg = _config({'rows': 32, 'cols': 64, 'chain_length': 4, 'parallel': 1},
                  {'enabled': True, 'copies': 2, 'axis': 'horizontal'})
    assert physical_size(cfg) == (256, 32)
    assert logical_size(cfg) == (128, 32)


def test_double_sided_vertical_splits_parallel():
    cfg = _config({'rows': 32, 'cols': 64, 'chain_length': 2, 'parallel': 2},
                  {'enabled': True, 'copies': 2, 'axis': 'vertical'})
    assert logical_size(cfg) == (128, 32)


def test_double_sided_that_does_not_divide_falls_back_to_physical():
    cfg = _config({'rows': 32, 'cols': 64, 'chain_length': 2, 'parallel': 1},
                  {'enabled': True, 'copies': 3, 'axis': 'horizontal'})
    assert logical_size(cfg) == (128, 32)


def test_disabled_double_sided_is_ignored():
    cfg = _config({'chain_length': 4}, {'enabled': False, 'copies': 2})
    assert logical_size(cfg) == (256, 32)


@pytest.fixture
def display_client(monkeypatch):
    from web_interface.blueprints.api_v3 import api_v3
    config_manager = MagicMock()
    monkeypatch.setattr(api_v3, 'config_manager', config_manager, raising=False)
    app = Flask(__name__)
    app.config['TESTING'] = True
    app.register_blueprint(api_v3, url_prefix='/api/v3')
    with app.test_client() as client:
        yield client, config_manager


def test_display_current_reports_the_logical_size(display_client):
    client, config_manager = display_client
    config_manager.load_config.return_value = _config(
        {'rows': 32, 'cols': 64, 'chain_length': 4, 'parallel': 2},
        {'enabled': True, 'copies': 2, 'axis': 'horizontal'})

    data = client.get('/api/v3/display/current').get_json()['data']

    assert (data['width'], data['height']) == (128, 64)


def test_display_current_defaults_chain_length_like_display_manager(display_client):
    client, config_manager = display_client
    config_manager.load_config.return_value = _config({'rows': 32, 'cols': 64})

    data = client.get('/api/v3/display/current').get_json()['data']

    assert (data['width'], data['height']) == (64 * DEFAULT_CHAIN_LENGTH, 32)


def test_preview_callers_do_not_rederive_the_size():
    """Every preview/size caller goes through display_geometry, so none of
    them can drift back to a private chain_length default."""
    for rel in ('web_interface/app.py',
                'web_interface/blueprints/api_v3/display.py',
                'web_interface/blueprints/api_v3/__init__.py',
                'src/common/sync_manager.py',
                'src/display_manager.py'):
        source = (REPO_ROOT / rel).read_text(encoding='utf-8')
        assert "get('chain_length', 1)" not in source, rel
        assert 'get("chain_length", 1)' not in source, rel
        assert 'cols * chain_length' not in source, rel
