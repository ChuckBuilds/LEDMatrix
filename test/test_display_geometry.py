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


@pytest.mark.parametrize('display', ['oops', ['a'], 1, {'hardware': 'oops'},
                                     {'hardware': ['a']}])
def test_non_mapping_display_config_uses_the_defaults(display):
    assert physical_size({'display': display}) == (128, 32)
    assert logical_size({'display': display}) == (128, 32)


@pytest.mark.parametrize('key', ['rows', 'cols', 'chain_length', 'parallel'])
def test_infinite_hardware_value_raises_value_error(key):
    # json.loads accepts Infinity; int(inf) raises OverflowError, which the
    # Starlark magnify default and the preview stream did not catch (HTTP 500).
    cfg = _config({key: json.loads('Infinity')})
    with pytest.raises(ValueError):
        physical_size(cfg)
    with pytest.raises(ValueError):
        logical_size(cfg)


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


def test_display_current_falls_back_to_the_shared_default(display_client):
    client, config_manager = display_client
    config_manager.load_config.side_effect = ValueError('unreadable')

    data = client.get('/api/v3/display/current').get_json()['data']

    assert (data['width'], data['height']) == logical_size({}) == (128, 32)


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


# --- Pixel mappers ----------------------------------------------------------
# RGBMatrix.width/height are measured after the library's pixel mappers
# (lib/pixel-mapper.cc), so the preview has to apply them too. This used to
# report 128x32 for a Rotate:90 chain the panel drew at 32x128.

@pytest.mark.parametrize('hardware,expected', [
    ({'pixel_mapper_config': 'Rotate:90'}, (32, 128)),
    ({'pixel_mapper_config': 'Rotate:270'}, (32, 128)),
    ({'pixel_mapper_config': 'Rotate:-90'}, (32, 128)),
    ({'pixel_mapper_config': 'Rotate:180'}, (128, 32)),
    ({'orientation': '90'}, (32, 128)),
    ({'orientation': '270'}, (32, 128)),
    ({'orientation': '180'}, (128, 32)),
    # Two quarter turns cancel out.
    ({'pixel_mapper_config': 'Rotate:90', 'orientation': '270'}, (128, 32)),
    # U-mapper folds a chain of 4 into two rows: (256 / 64) * 32 by 2 * 32.
    ({'chain_length': 4, 'pixel_mapper_config': 'U-mapper'}, (128, 64)),
    ({'chain_length': 4, 'pixel_mapper_config': 'u-mapper;Rotate:90'}, (64, 128)),
    # U-mapper needs an even chain of at least 2, or the library skips it.
    ({'chain_length': 3, 'pixel_mapper_config': 'U-mapper'}, (192, 32)),
    ({'cols': 32, 'chain_length': 4, 'pixel_mapper_config': 'V-mapper'}, (32, 128)),
    ({'chain_length': 1, 'parallel': 2, 'pixel_mapper_config': 'StackToRow:Z'}, (128, 32)),
    ({'pixel_mapper_config': 'Remap:64,64|0,0n|0,32n'}, (64, 64)),
    # A Remap panel entirely outside the visible area: the library skips it.
    ({'pixel_mapper_config': 'Remap:64,64|0,0n|0,99n'}, (128, 32)),
    ({'pixel_mapper_config': 'Mirror:H'}, (128, 32)),
    # Unknown or unusable mappers are skipped by the library.
    ({'pixel_mapper_config': 'Bogus;Rotate:45;Rotate:ninety'}, (128, 32)),
])
def test_pixel_mappers_change_the_size_as_the_library_does(hardware, expected):
    hw = {'rows': 32, 'cols': 64, 'chain_length': 2, 'parallel': 1}
    hw.update(hardware)
    assert physical_size(_config(hw)) == expected
    assert logical_size(_config(hw)) == expected


def test_double_sided_splits_the_mapped_size():
    cfg = _config({'rows': 32, 'cols': 64, 'chain_length': 2, 'parallel': 1, 'orientation': '90'},
                  {'enabled': True, 'copies': 2, 'axis': 'vertical'})
    assert logical_size(cfg) == (32, 64)


def test_display_manager_composes_the_mapper_config_it_sizes_from():
    from src.display_geometry import compose_pixel_mapper_config
    assert compose_pixel_mapper_config({}) == ''
    assert compose_pixel_mapper_config({'orientation': '90'}) == 'Rotate:90'
    assert compose_pixel_mapper_config(
        {'pixel_mapper_config': ' U-mapper ', 'orientation': '180'}) == 'U-mapper;Rotate:180'
    assert compose_pixel_mapper_config({'orientation': 'sideways'}) == ''
