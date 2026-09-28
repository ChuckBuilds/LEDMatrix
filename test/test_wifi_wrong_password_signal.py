"""A rejected passphrase must reach the web UI as "wrong_password:".

_connect_nmcli prefixes the message with "wrong_password:" and the api_v3
wifi route turns that into ``error_type == 'wrong_password'``, which the
setup pages key their "incorrect password" prompt off. _connect_validated
used to replace the message on every failure branch (restore the old
network, or bring the setup AP up), so the prefix never survived.
"""

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.wifi_manager import WiFiManager  # noqa: E402


def _manager(connected_ssid=None):
    manager = WiFiManager.__new__(WiFiManager)  # no __init__: no real host access
    manager.has_nmcli = True
    manager._wifi_interface = "wlan0"
    manager.get_wifi_status = MagicMock(return_value=SimpleNamespace(
        connected=bool(connected_ssid), ssid=connected_ssid, ip_address=None))
    manager._is_ap_mode_active = MagicMock(return_value=False)
    manager._ensure_wifi_radio_enabled = MagicMock(return_value=True)
    manager._show_led_message = MagicMock()
    manager.disconnect_from_network = MagicMock(return_value=(True, "ok"))
    manager._wait_for_device_idle = MagicMock(return_value=True)
    manager.enable_ap_mode = MagicMock(return_value=(True, "AP up"))
    return manager


def _run(manager, nmcli_result):
    manager._connect_nmcli = MagicMock(return_value=nmcli_result)
    nmcli_out = SimpleNamespace(returncode=0, stdout="GENERAL.CONNECTION:OldNet\n")
    with patch("src.wifi_manager.subprocess.run", return_value=nmcli_out), \
            patch("src.wifi_manager.time.sleep"):
        return manager._connect_validated("HomeNet", "hunter22")


WRONG = (False, "wrong_password: Secrets were required, but not provided")


class TestWrongPasswordSurvivesRecovery:
    def test_when_the_setup_ap_comes_back_up(self):
        ok, message = _run(_manager(), WRONG)
        assert ok is False
        assert message.startswith("wrong_password:")

    def test_when_the_setup_ap_fails_too(self):
        manager = _manager()
        manager.enable_ap_mode.return_value = (False, "hostapd missing")
        ok, message = _run(manager, WRONG)
        assert ok is False
        assert message.startswith("wrong_password:")
        assert "hostapd missing" in message

    @pytest.mark.parametrize("restored", [True, False])
    def test_when_the_previous_network_is_restored_or_not(self, restored):
        manager = _manager(connected_ssid="OldNet")
        manager._restore_original_connection = MagicMock(return_value=restored)
        ok, message = _run(manager, WRONG)
        assert ok is False
        assert message.startswith("wrong_password:")


class TestOtherFailuresAreUnchanged:
    def test_a_generic_failure_gets_no_prefix(self):
        ok, message = _run(_manager(), (False, "Connection timed out"))
        assert ok is False
        assert message == "Connection failed. AP mode enabled."

    def test_success_passes_through(self):
        assert _run(_manager(), (True, "Connected to HomeNet")) == (True, "Connected to HomeNet")
