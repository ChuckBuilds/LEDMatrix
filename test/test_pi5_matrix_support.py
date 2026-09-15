"""The Pi 5 rule in src/pi5_matrix_support.py mirrors the pinned library.

Rp1PioPlatformDetected() and Rp1PioConfigSupported() in
rpi-rgb-led-matrix-master/lib/rp1/rp1_pio_backend.cc (commit 1ee4f76). When the
submodule is bumped and those change, these are the cases to revisit.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src import pi5_matrix_support as pi5  # noqa: E402


@pytest.fixture
def model_file(tmp_path, monkeypatch):
    def write(model):
        path = tmp_path / 'model'
        # The device tree string is NUL-terminated.
        path.write_bytes(model.encode() + b'\x00')
        monkeypatch.setattr(pi5, 'MODEL_PATH', str(path))
    return write


@pytest.mark.parametrize('model', [
    'Raspberry Pi 5 Model B Rev 1.0',
    'Raspberry Pi 500 Rev 1.0',
    'Raspberry Pi Compute Module 5 Rev 1.0',
])
def test_pi5_family_is_detected(model_file, model):
    model_file(model)
    assert pi5.is_raspberry_pi_5()


@pytest.mark.parametrize('model', [
    'Raspberry Pi 4 Model B Rev 1.5',
    'Raspberry Pi Zero 2 W Rev 1.0',
    'Raspberry Pi Compute Module 4 Rev 1.0',
])
def test_other_boards_are_not(model_file, model):
    model_file(model)
    assert not pi5.is_raspberry_pi_5()


def test_no_device_tree_is_not_a_pi5(tmp_path, monkeypatch):
    monkeypatch.setattr(pi5, 'MODEL_PATH', str(tmp_path / 'missing'))
    assert not pi5.is_raspberry_pi_5()


@pytest.mark.parametrize('hardware', [
    {},
    {'row_address_type': 0}, {'row_address_type': 2}, {'row_address_type': '2'},
    {'parallel': 1}, {'parallel': 3},
    {'hardware_mapping': 'regular'}, {'hardware_mapping': 'regular-pi1'},
    {'hardware_mapping': 'classic'}, {'hardware_mapping': 'adafruit-hat'},
    {'hardware_mapping': 'adafruit-hat-pwm'}, {'hardware_mapping': ''},
])
def test_what_the_pi5_path_supports(hardware):
    assert pi5.pi5_unsupported_settings(hardware) is None


@pytest.mark.parametrize('hardware,named', [
    ({'row_address_type': 1}, 'row address type 1'),
    ({'row_address_type': 3}, 'row address type 3'),
    ({'row_address_type': 4}, 'row address type 4'),
    ({'row_address_type': '5'}, 'row address type 5'),
    ({'parallel': 4}, 'parallel 4'),
    ({'hardware_mapping': 'compute-module'}, 'hardware mapping "compute-module"'),
])
def test_what_it_does_not(hardware, named):
    message = pi5.pi5_unsupported_settings(hardware)
    assert message is not None and named in message


def test_every_problem_is_named():
    message = pi5.pi5_unsupported_settings({'row_address_type': 5, 'parallel': 4})
    assert 'row address type 5' in message and 'parallel 4' in message
