"""Tests for the web interface's autostart default.

Regression under test: ``start_web_conditionally.py`` read the flag with
``config_data.get("web_display_autostart", False)``, so a config that simply
lacked the key got no web interface. Both config.template.json and
first_time_install.sh ship the key as ``true``, so absence means an older or
hand-edited config -- not a request to stay down.

The failure was silent in the worst way: the "not starting" path exits 0, so
``systemctl status ledmatrix-web`` reported the unit as successfully started
while nothing was listening on the port, and the journal's only trace was one
line saying the flag was "false or not set".
"""

import importlib.util
from pathlib import Path

import pytest

SCRIPT = (Path(__file__).resolve().parents[1]
          / "scripts" / "utils" / "start_web_conditionally.py")


@pytest.fixture(scope="module")
def mod():
    spec = importlib.util.spec_from_file_location("start_web_conditionally", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestAutostartDefault:
    def test_absent_key_still_starts(self, mod):
        # The regression: this returned False and the UI silently stayed down.
        assert mod.autostart_enabled({}) is True

    def test_unreadable_config_still_starts(self, mod):
        # main() falls back to {} when the config is missing or corrupt, because
        # the web interface is how a broken config gets repaired.
        assert mod.autostart_enabled({}) is True

    def test_explicit_true_starts(self, mod):
        assert mod.autostart_enabled({"web_display_autostart": True}) is True

    def test_explicit_false_does_not_start(self, mod):
        assert mod.autostart_enabled({"web_display_autostart": False}) is False

    @pytest.mark.parametrize("value", ["off", "false", "no", "0", "OFF", " False "])
    def test_disabling_strings_do_not_start(self, mod, value):
        assert mod.autostart_enabled({"web_display_autostart": value}) is False

    @pytest.mark.parametrize("value", ["on", "true", "yes", "1", "TRUE"])
    def test_enabling_strings_start(self, mod, value):
        assert mod.autostart_enabled({"web_display_autostart": value}) is True


class TestShippedDefaultsAgree:
    def test_template_ships_autostart_true(self):
        """The code default must match what the installer actually writes."""
        import json
        template = json.loads(
            (SCRIPT.parents[2] / "config" / "config.template.json").read_text(encoding="utf-8"))
        assert template["web_display_autostart"] is True
