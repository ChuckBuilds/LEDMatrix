"""The setup AP must not outlive the process that started it.

Seen on a Pi 4: it lost its wired link overnight, the daemon raised the setup
AP, and the user power-cycled it. NetworkManager had saved
``LEDMatrix-Setup-AP`` with autoconnect on (the default), so it brought the AP
back at boot ahead of the saved home WiFi, which was in range at 100%. The
fresh daemon had not started that AP, so ``_ap_enabled_at`` was None and the
15-minute idle timeout never applied; while the AP held the radio the home
WiFi could not connect either. The device stayed offline for an hour until a
phone joined the AP and reconnected it by hand.

Two fixes, two tests each:
  * the profile is created with ``connection.autoconnect no``;
  * an AP the daemon finds already up is adopted: its idle clock starts and
    autoconnect is cleared on the profile (unless the AP was force-enabled).
"""

import os
import sys
import time
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.wifi_manager import AP_PROFILE_NAME, WiFiManager, WiFiStatus  # noqa: E402


def _ok(stdout=""):
    m = MagicMock()
    m.returncode = 0
    m.stdout = stdout
    m.stderr = ""
    return m


@pytest.fixture
def wm():
    manager = WiFiManager.__new__(WiFiManager)
    manager.config = {"auto_enable_ap_mode": True, "ap_idle_timeout_minutes": 15}
    manager._disconnected_checks = 0
    manager._disconnected_checks_required = 3
    manager._ap_enabled_at = None
    manager.has_nmcli = True
    manager._wifi_interface = "wlan0"
    manager._FORCE_AP_FLAG_PATH = MagicMock()
    manager._FORCE_AP_FLAG_PATH.exists.return_value = False
    manager._connect_in_progress = MagicMock(return_value=False)
    manager.disable_ap_mode = MagicMock(return_value=(True, "ok"))
    manager.enable_ap_mode = MagicMock(return_value=(True, "ok"))
    manager._has_ap_clients = MagicMock(return_value=False)
    return manager


def _orphan_ap(wm):
    """Nothing associated, AP up -- the state right after the reboot."""
    wm._get_wifi_status_with_retry = MagicMock(return_value=WiFiStatus(connected=False))
    wm._is_ethernet_connected = MagicMock(return_value=False)
    wm._is_ap_mode_active = MagicMock(return_value=True)


def _value_after(cmd, key):
    return cmd[cmd.index(key) + 1]


@pytest.mark.unit
def test_ap_profile_is_created_without_autoconnect(wm):
    captured = []

    def _run(cmd, **kw):
        captured.append(list(cmd))
        return _ok()

    with patch("src.wifi_manager.subprocess.run", side_effect=_run), \
         patch("src.wifi_manager.time.sleep"), \
         patch.object(wm, "disconnect_from_network", return_value=(True, "ok"), create=True), \
         patch.object(wm, "_validate_ap_config", return_value=("LEDMatrix-Setup", 7), create=True), \
         patch.object(wm, "_write_nm_dnsmasq_captive_conf", create=True), \
         patch.object(wm, "_setup_iptables_redirect", return_value=True, create=True), \
         patch.object(wm, "_get_ap_status_nmcli", return_value={"active": True, "ip": "192.168.4.1"}, create=True), \
         patch.object(wm, "_show_led_message", create=True):
        ok, _ = wm._enable_ap_mode_nmcli_hotspot()

    assert ok
    add = next(c for c in captured if c[:3] == ["nmcli", "connection", "add"])
    assert _value_after(add, "con-name") == AP_PROFILE_NAME
    assert _value_after(add, "connection.autoconnect") == "no"


@pytest.mark.unit
def test_an_ap_found_running_is_adopted_and_times_out(wm):
    _orphan_ap(wm)
    with patch("src.wifi_manager.subprocess.run", return_value=_ok()) as run:
        changed, *_ = wm.check_and_manage_ap_mode_with_state()

    assert changed is False, "adopting must not tear the AP down on the spot"
    assert wm._ap_enabled_at is not None, "idle clock must start when the AP is adopted"
    modified = [c.args[0] for c in run.call_args_list
                if c.args[0][:3] == ["nmcli", "connection", "modify"]]
    assert any(AP_PROFILE_NAME in c and _value_after(c, "connection.autoconnect") == "no"
               for c in modified), "autoconnect must be cleared on the saved AP profile"
    wm.disable_ap_mode.assert_not_called()

    # 16 minutes on, still no clients: the idle timeout now takes it down.
    wm._ap_enabled_at = time.time() - 16 * 60
    with patch("src.wifi_manager.subprocess.run", return_value=_ok()):
        changed, *_ = wm.check_and_manage_ap_mode_with_state()
    assert changed is True
    wm.disable_ap_mode.assert_called_once()


@pytest.mark.unit
def test_adoption_happens_once(wm):
    _orphan_ap(wm)
    with patch("src.wifi_manager.subprocess.run", return_value=_ok()) as run:
        wm.check_and_manage_ap_mode_with_state()
        started = wm._ap_enabled_at
        n = run.call_count
        wm.check_and_manage_ap_mode_with_state()
    assert wm._ap_enabled_at == started, "a later check must not restart the idle clock"
    assert run.call_count == n, "no more nmcli modify calls once adopted"


@pytest.mark.unit
def test_a_failsafe_ap_replacing_an_adopted_one_is_not_timed_out(wm):
    """Adopt an AP, let its clock expire with a client on it, then have the web
    process's failed connect replace it with a force-enabled failsafe AP --
    inside a connect window this daemon skips, so it never saw the AP drop.
    The stale clock must not take the recovery AP down."""
    _orphan_ap(wm)
    with patch("src.wifi_manager.subprocess.run", return_value=_ok()):
        wm.check_and_manage_ap_mode_with_state()         # adopted
        wm._ap_enabled_at = time.time() - 60 * 60        # well past the timeout
        wm._has_ap_clients.return_value = True           # a phone keeps it up
        wm.check_and_manage_ap_mode_with_state()
        wm.disable_ap_mode.assert_not_called()

        wm._connect_in_progress.return_value = True      # connect -> fails -> failsafe AP
        wm.check_and_manage_ap_mode_with_state()
        wm._connect_in_progress.return_value = False
        wm._FORCE_AP_FLAG_PATH.exists.return_value = True
        wm._has_ap_clients.return_value = False          # client not back yet
        changed, *_ = wm.check_and_manage_ap_mode_with_state()

    assert changed is False
    wm.disable_ap_mode.assert_not_called()


@pytest.mark.unit
def test_the_idle_clock_is_dropped_when_the_ap_goes_away(wm):
    _orphan_ap(wm)
    with patch("src.wifi_manager.subprocess.run", return_value=_ok()):
        wm.check_and_manage_ap_mode_with_state()
        assert wm._ap_enabled_at is not None
        wm._is_ap_mode_active.return_value = False       # taken down elsewhere
        wm.check_and_manage_ap_mode_with_state()
    assert wm._ap_enabled_at is None


@pytest.mark.unit
def test_a_force_enabled_ap_is_not_adopted(wm):
    _orphan_ap(wm)
    wm._FORCE_AP_FLAG_PATH.exists.return_value = True
    with patch("src.wifi_manager.subprocess.run", return_value=_ok()) as run:
        wm.check_and_manage_ap_mode_with_state()
    assert wm._ap_enabled_at is None
    assert not any(c.args[0][:3] == ["nmcli", "connection", "modify"]
                   for c in run.call_args_list)
