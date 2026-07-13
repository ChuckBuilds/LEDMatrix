"""Tests for check_and_manage_ap_mode_with_state (src/wifi_manager.py).

The wifi monitor daemon used to fetch WiFi status before AND after each
check on top of the check's own internal fetch — every fetch is several
nmcli subprocess forks, every 30s, forever. The new API returns the state
the check observed, so the daemon runs exactly one fetch battery per tick.
"""

import os
import sys
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.wifi_manager import WiFiManager, WiFiStatus  # noqa: E402


@pytest.fixture
def wm(tmp_path):
    with patch.object(WiFiManager, "_load_config", return_value={}, create=True):
        manager = WiFiManager.__new__(WiFiManager)
        # minimal attribute setup without running the real __init__
        manager.config = {"auto_enable_ap_mode": True}
        manager._disconnected_checks = 0
        manager._disconnected_checks_required = 3
        manager._ap_enabled_at = None
        return manager


def _wire(wm, connected, ethernet, ap_active):
    wm._get_wifi_status_with_retry = MagicMock(
        return_value=WiFiStatus(connected=connected, ssid="net" if connected else None))
    wm._is_ethernet_connected = MagicMock(return_value=ethernet)
    wm._is_ap_mode_active = MagicMock(return_value=ap_active)
    wm.enable_ap_mode = MagicMock(return_value=(True, "ok"))
    wm.disable_ap_mode = MagicMock(return_value=(True, "ok"))
    wm.scan_networks = MagicMock(return_value=([], False))
    wm._save_cached_scan = MagicMock()
    wm._FORCE_AP_FLAG_PATH = MagicMock()
    wm._FORCE_AP_FLAG_PATH.exists.return_value = False


class TestWithState:
    def test_single_fetch_per_call(self, wm):
        _wire(wm, connected=True, ethernet=False, ap_active=False)
        wm.check_and_manage_ap_mode_with_state()
        assert wm._get_wifi_status_with_retry.call_count == 1
        assert wm._is_ethernet_connected.call_count == 1
        assert wm._is_ap_mode_active.call_count == 1

    def test_returns_observed_state(self, wm):
        _wire(wm, connected=True, ethernet=True, ap_active=False)
        changed, status, ethernet, ap_after = wm.check_and_manage_ap_mode_with_state()
        assert changed is False
        assert status.connected is True
        assert ethernet is True
        assert ap_after is False

    def test_ap_after_inverts_on_disable(self, wm):
        """WiFi reconnects while AP is up -> auto-disable -> ap_after False."""
        _wire(wm, connected=True, ethernet=False, ap_active=True)
        changed, status, ethernet, ap_after = wm.check_and_manage_ap_mode_with_state()
        assert changed is True
        assert ap_after is False
        wm.disable_ap_mode.assert_called_once()

    def test_ap_after_inverts_on_enable(self, wm):
        """Grace period exhausted with nothing connected -> enable -> True."""
        _wire(wm, connected=False, ethernet=False, ap_active=False)
        wm._disconnected_checks = 2  # this call is the 3rd
        changed, status, ethernet, ap_after = wm.check_and_manage_ap_mode_with_state()
        assert changed is True
        assert ap_after is True
        wm.enable_ap_mode.assert_called_once()

    def test_bool_wrapper_is_back_compatible(self, wm):
        _wire(wm, connected=True, ethernet=False, ap_active=False)
        assert wm.check_and_manage_ap_mode() is False
        _wire(wm, connected=True, ethernet=False, ap_active=True)
        assert wm.check_and_manage_ap_mode() is True

    def test_exception_path_never_raises(self, wm):
        wm._get_wifi_status_with_retry = MagicMock(side_effect=RuntimeError("nmcli gone"))
        changed, status, ethernet, ap_after = wm.check_and_manage_ap_mode_with_state()
        assert changed is False
        assert status.connected is False


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
