"""The library-refusal rules in src/matrix_support.py mirror the pinned library.

rpi-rgb-led-matrix-master at 1ee4f76: RGBMatrix::Options::Validate in
lib/options-initialize.cc, the runtime checks in RGBMatrix::CreateFromOptions
(lib/led-matrix.cc), the mapping table in lib/hardware-mapping.c with the
abort()s in lib/framebuffer.cc, and the setter types in
bindings/python/rgbmatrix/core.pyx. When the submodule is bumped and those
change, these are the cases to revisit.
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src import matrix_support as ms  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]


def _messages(hardware, runtime=None, pi5=False):
    return [r.message for r in ms.library_refusals(hardware, runtime, pi5=pi5)]


def test_the_config_template_starts():
    template = json.loads((REPO_ROOT / 'config' / 'config.template.json').read_text(encoding='utf-8'))
    display = template['display']
    assert ms.library_refusals(display['hardware'], display['runtime']) == []
    assert ms.library_refusals(display['hardware'], display['runtime'], pi5=True) == []


def test_display_manager_defaults_start():
    assert ms.library_refusals({}) == []


@pytest.mark.parametrize('hardware', [
    {'rows': 8}, {'rows': 64}, {'cols': 16}, {'cols': 1024},
    {'chain_length': 1}, {'chain_length': 255},
    {'hardware_mapping': 'regular', 'parallel': 3},
    {'hardware_mapping': 'classic', 'parallel': 3},
    {'hardware_mapping': 'REGULAR', 'parallel': 2},
    {'hardware_mapping': '', 'parallel': 3},  # empty reads as "regular"
    {'hardware_mapping': 'classic-pi1'},
    {'hardware_mapping': 'regular-pi1', 'parallel': 1},
    {'pwm_bits': 11}, {'pwm_dither_bits': 2}, {'pwm_lsb_nanoseconds': 3000},
    {'row_address_type': 5}, {'multiplexing': 22}, {'scan_mode': 1},
    {'brightness': 1}, {'limit_refresh_rate_hz': 0},
    {'led_rgb_sequence': 'bgr'}, {'led_rgb_sequence': 'GBR'},
    # Not a number: left to the binding, which raises a TypeError.
    {'rows': 'thirty-two'},
])
def test_what_the_library_starts_with(hardware):
    assert ms.library_refusals(hardware) == []


@pytest.mark.parametrize('hardware,runtime,named', [
    ({'rows': 66}, None, 'rows 66'),
    ({'rows': 128}, None, 'rows 128'),
    ({'rows': 31}, None, 'rows 31'),
    ({'cols': 8}, None, 'cols 8'),
    ({'chain_length': 0}, None, 'chain_length 0'),
    ({'chain_length': 256}, None, 'chain_length 256'),  # uint8_t setter
    ({'hardware_mapping': 'regular', 'parallel': 4}, None, 'parallel 4'),
    ({'hardware_mapping': 'adafruit-hat-pwm', 'parallel': 2}, None, 'which has 1 output'),
    ({'hardware_mapping': 'adafruit-hat', 'parallel': 3}, None, 'which has 1 output'),
    ({'hardware_mapping': 'regular-pi1', 'parallel': 2}, None, 'which has 1 output'),
    ({'hardware_mapping': 'classic-pi1', 'parallel': 2}, None, 'which has 1 output'),
    ({'parallel': 2}, None, 'adafruit-hat-pwm'),  # DisplayManager's default mapping
    ({'hardware_mapping': 'adafruit-hat-pwn'}, None, '"adafruit-hat-pwn"'),
    ({'hardware_mapping': 'compute-module'}, None, '"compute-module"'),
    ({'hardware_mapping': None}, None, 'hardware mapping None'),
    ({'brightness': 0}, None, 'brightness 0'),
    ({'pwm_bits': 12}, None, 'pwm_bits 12'),
    ({'pwm_dither_bits': 3}, None, 'pwm_dither_bits 3'),
    ({'pwm_lsb_nanoseconds': 49}, None, 'pwm_lsb_nanoseconds 49'),
    ({'row_address_type': 6}, None, 'row_address_type 6'),
    ({'multiplexing': 23}, None, 'multiplexing 23'),
    ({'scan_mode': 2}, None, 'scan_mode 2'),
    ({'led_rgb_sequence': 'RGBW'}, None, 'LED RGB sequence'),
    ({'led_rgb_sequence': 'RRB'}, None, 'LED RGB sequence'),
    ({}, {'gpio_slowdown': 11}, 'gpio_slowdown 11'),
    ({}, {'gpio_slowdown': -1}, 'gpio_slowdown -1'),
    ({}, {'rp1_rio': 2}, 'rp1_rio 2'),
])
def test_what_it_refuses(hardware, runtime, named):
    messages = _messages(hardware, runtime)
    assert any(named in m for m in messages), messages


def test_pi5_limits_apply_only_on_a_pi5():
    assert ms.library_refusals({'row_address_type': 5}) == []
    refusals = ms.library_refusals({'row_address_type': 5}, pi5=True)
    assert len(refusals) == 1 and refusals[0].pi5
    assert set(refusals[0].fields) == {'row_address_type', 'parallel', 'hardware_mapping'}


def test_refusal_names_the_fields_involved():
    (refusal,) = ms.library_refusals({'hardware_mapping': 'adafruit-hat', 'parallel': 2})
    assert set(refusal.fields) == {'parallel', 'hardware_mapping'}
    (refusal,) = ms.library_refusals({'rows': 128})
    assert refusal.fields == ('rows',)


def test_message_names_every_problem():
    message = ms.refusal_message(ms.library_refusals(
        {'rows': 128, 'row_address_type': 5}, {'gpio_slowdown': 11}, pi5=True))
    assert message.startswith("The installed rgbmatrix library can't start")
    assert 'rows 128' in message and 'gpio_slowdown 11' in message
    assert 'Raspberry Pi 5' in message and 'row address type 5' in message
    assert ms.refusal_message([]) is None


def test_non_mapping_sections_are_treated_as_empty():
    assert ms.library_refusals('oops', ['a']) == []


def test_display_manager_defaults_match_display_manager():
    """The guard fills missing keys the way DisplayManager._setup_matrix does."""
    source = (REPO_ROOT / 'src' / 'display_manager.py').read_text(encoding='utf-8')
    for field, value in ms.DISPLAY_MANAGER_DEFAULTS.items():
        if field in ('rows', 'cols', 'chain_length', 'parallel'):
            continue  # read through display_geometry's DEFAULT_* constants
        assert f"get('{field}', {value!r})" in source, field
