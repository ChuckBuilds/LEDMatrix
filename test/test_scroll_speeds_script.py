"""scripts/scroll_speeds.py must drive the panel the way the service does.

--measure and --demo open the matrix themselves. They used to build the
options from a private copy of DisplayManager's builder that had drifted: it
read gpio_slowdown from display.hardware (the service reads display.runtime),
and skipped rp1_rio, panel_type, orientation and more. On a panel that needs a
high slowdown that measured -- or garbled -- a panel the service never drives.
"""
import importlib.util
import os
from pathlib import Path
from unittest.mock import patch

import pytest

os.environ.setdefault("EMULATOR", "true")

import src.display_manager as display_manager  # noqa: E402
from src.display_manager import DisplayManager  # noqa: E402

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "scroll_speeds.py"


@pytest.fixture(scope="module")
def script():
    spec = importlib.util.spec_from_file_location("scroll_speeds_script", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Options:
    """Records what is set; declares rp1_rio like a Pi 5-capable binding."""

    rp1_rio = None


CONFIG = {
    "display": {
        "hardware": {
            "rows": 64, "cols": 64, "chain_length": 2, "parallel": 1,
            "hardware_mapping": "adafruit-hat-pwm",
            "brightness": 70, "pwm_bits": 9, "pwm_lsb_nanoseconds": 130,
            "row_address_type": 5, "panel_type": "FM6126A",
            "limit_refresh_rate_hz": 120, "orientation": "180",
            "pixel_mapper_config": "U-mapper",
            "gpio_slowdown": 2,  # a stale key in the wrong block
        },
        "runtime": {"gpio_slowdown": 7, "rp1_rio": 1},
    }
}


def _script_options(script, config, **kwargs):
    with patch.object(display_manager, "RGBMatrixOptions", _Options):
        return script.build_options(config, **kwargs)


def test_runtime_settings_are_used(script):
    options = _script_options(script, CONFIG)
    assert options.gpio_slowdown == 7
    assert options.rp1_rio == 1
    assert options.panel_type == "FM6126A"
    assert options.row_address_type == 5
    assert options.pixel_mapper_config == "U-mapper;Rotate:180"
    assert options.limit_refresh_rate_hz == 120


def test_the_options_match_the_display_service(script):
    """Same config in, same options out, attribute for attribute."""
    service = DisplayManager.apply_matrix_options(_Options(), CONFIG)
    assert vars(_script_options(script, CONFIG)) == vars(service)


def test_measure_runs_uncapped(script):
    assert _script_options(script, CONFIG, refresh_override=0).limit_refresh_rate_hz == 0


def test_an_empty_config_gets_the_service_defaults(script):
    options = _script_options(script, {})
    assert vars(options) == vars(DisplayManager.apply_matrix_options(_Options(), {}))
    assert options.gpio_slowdown == 3


def test_the_service_uses_the_shared_builder(test_config):
    """DisplayManager._setup_matrix fills its options through the same call."""
    with patch.object(display_manager, "RGBMatrix"), \
            patch.object(display_manager, "RGBMatrixOptions", _Options), \
            patch.object(display_manager, "freetype"), \
            patch.object(DisplayManager, "apply_matrix_options",
                         wraps=DisplayManager.apply_matrix_options) as shared, \
            patch.dict(os.environ, {"EMULATOR": "false"}):
        DisplayManager._instance = None
        try:
            DisplayManager(test_config)
        finally:
            DisplayManager._instance = None
    shared.assert_called_once()
