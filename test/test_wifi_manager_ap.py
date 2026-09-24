"""
Unit tests for WiFi AP mode — src.wifi_manager.

Each test exercises logic that can be verified through the subprocess calls the
manager emits, without requiring root access, hardware, or a running Pi.

Scenarios covered:
1. nmcli AP profile is created with no security parameters (open/passwordless).
2. iptables PREROUTING and INPUT rules are added when the nmcli AP starts.
3. iptables rules and ip_forward are reverted when the AP is torn down.
4. LED matrix message includes the SSID, 'No password', and the setup URL.
5. Known AP profile names are deleted before the new profile is created.
6. Wi-Fi passwords are not written to wifi_config.json, and old ones are scrubbed.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.wifi_manager import WiFiManager


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ok(stdout: str = "", stderr: str = "") -> MagicMock:
    r = MagicMock()
    r.returncode = 0
    r.stdout = stdout
    r.stderr = stderr
    return r


def _fail(stdout: str = "", stderr: str = "error") -> MagicMock:
    r = MagicMock()
    r.returncode = 1
    r.stdout = stdout
    r.stderr = stderr
    return r


def _find_path_side_effect(name: str) -> str:
    """Deterministic fake for _find_command_path."""
    return {"iptables": "/usr/sbin/iptables", "sysctl": "/usr/sbin/sysctl"}.get(
        name, f"/usr/bin/{name}"
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def wifi_config(tmp_path: Path) -> Path:
    """Minimal wifi_config.json in a temporary directory."""
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir()
    cfg = {
        "ap_ssid": "LEDMatrix-Setup",
        "ap_channel": 7,
        "auto_enable_ap_mode": True,
    }
    p = cfg_dir / "wifi_config.json"
    p.write_text(json.dumps(cfg))
    return p


@pytest.fixture()
def manager(wifi_config: Path, tmp_path: Path) -> WiFiManager:
    """
    WiFiManager with all system calls stubbed out during construction and the
    ip_forward save file redirected to a per-test temporary path.
    """
    with patch("src.wifi_manager.subprocess.run", return_value=_ok(stdout="wlan0\n")):
        mgr = WiFiManager(config_path=wifi_config)

    # Force clean, deterministic state regardless of what __init__ inferred
    mgr._wifi_interface = "wlan0"
    mgr.has_nmcli = True
    mgr.has_hostapd = False
    mgr.has_dnsmasq = False
    mgr.has_iwlist = False
    # Redirect the ip_forward save file to tmp so tests never share state
    mgr._IP_FORWARD_SAVE_PATH = tmp_path / "ip_fwd_saved"
    return mgr


# ---------------------------------------------------------------------------
# 1. AP profile is open (no password)
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_nmcli_ap_profile_has_no_security_params(manager: WiFiManager) -> None:
    """
    The 'nmcli connection add' command must not include key-mgmt, psk, or any
    WPA-related parameter.  On Bookworm/Trixie, NM creates a WPA2-protected
    hotspot even when those values are set to 'none'/empty via a later
    'connection modify', so the profile must be created without a security
    section from the start.
    """
    captured: list[list[str]] = []

    def _run(cmd, **kw):
        captured.append(list(cmd))
        return _ok()

    with patch("src.wifi_manager.subprocess.run", side_effect=_run), \
         patch.object(manager, "disconnect_from_network", return_value=(True, "ok")), \
         patch.object(manager, "_setup_iptables_redirect", return_value=True), \
         patch.object(manager, "_get_ap_status_nmcli",
                      return_value={"active": True, "ip": "192.168.4.1"}), \
         patch.object(manager, "_show_led_message"):

        success, _ = manager._enable_ap_mode_nmcli_hotspot()

    assert success, "AP enable should report success"

    add_calls = [c for c in captured if "nmcli" in c and "connection" in c and "add" in c]
    assert add_calls, "Expected at least one 'nmcli connection add' invocation"

    add_str = " ".join(add_calls[0])
    assert "key-mgmt" not in add_str, "AP profile must not set key-mgmt"
    assert "psk" not in add_str, "AP profile must not include a PSK/password"
    assert "wpa" not in add_str.lower(), "AP profile must not reference WPA"
    assert "802-11-wireless.mode" in add_str, "AP profile must declare wireless mode"
    # Verify the value for 802-11-wireless.mode is exactly "ap" — check the element
    # that immediately follows the key in the command list, not a loose substring match.
    cmd = add_calls[0]
    try:
        mode_idx = cmd.index("802-11-wireless.mode")
        assert cmd[mode_idx + 1] == "ap", \
            f"802-11-wireless.mode value must be exactly 'ap', got {cmd[mode_idx + 1]!r}"
    except ValueError:
        pytest.fail("802-11-wireless.mode not found as a list element in nmcli command")


# ---------------------------------------------------------------------------
# 2. iptables NAT rules are added when the AP starts
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_iptables_nat_rules_added_on_ap_start(manager: WiFiManager) -> None:
    """
    _setup_iptables_redirect must add:
    - a PREROUTING REDIRECT rule that maps incoming TCP port 80 to port 5000, and
    - an INPUT ACCEPT rule for port 5000 (the post-redirect destination port,
      NOT port 80 which never hits the INPUT chain after PREROUTING rewrites it).
    """
    captured: list[list[str]] = []

    def _run(cmd, **kw):
        captured.append(list(cmd))
        # iptables -C (check) → rc=1 so the -A (add) branch executes
        if "iptables" in " ".join(str(x) for x in cmd) and "-C" in cmd:
            return _fail()
        return _ok()

    # Patch Path.read_text so /proc/sys/net/ipv4/ip_forward is readable on any OS
    with patch("src.wifi_manager.subprocess.run", side_effect=_run), \
         patch.object(manager, "_find_command_path", side_effect=_find_path_side_effect), \
         patch("pathlib.Path.read_text", return_value="0\n"):

        result = manager._setup_iptables_redirect()

    assert result, "_setup_iptables_redirect must return True on success"

    prerouting_adds = [c for c in captured if "iptables" in " ".join(c) and "-A" in c and "PREROUTING" in c]
    assert prerouting_adds, "Expected 'iptables -A PREROUTING' invocation"
    pr_str = " ".join(prerouting_adds[0])
    assert "--dport" in pr_str and "80" in pr_str, "PREROUTING rule must match dport 80"
    assert "5000" in pr_str, "PREROUTING rule must redirect to port 5000"
    assert "REDIRECT" in pr_str, "PREROUTING rule must use REDIRECT target"

    input_adds = [c for c in captured if "iptables" in " ".join(c) and "-A" in c and "INPUT" in c]
    assert input_adds, "Expected 'iptables -A INPUT' invocation"
    in_str = " ".join(input_adds[0])
    assert "5000" in in_str, "INPUT rule must accept port 5000 (post-PREROUTING destination)"
    assert "ACCEPT" in in_str, "INPUT rule must use ACCEPT target"

    # Port 80 must NOT be used in the INPUT rule (it is already redirected by PREROUTING)
    input_80 = [c for c in captured if "iptables" in " ".join(c) and "INPUT" in c and "--dport" in c and "80" in c]
    assert not input_80, "INPUT rule must target port 5000, not port 80"


# ---------------------------------------------------------------------------
# 3a. iptables rules and ip_forward reverted on teardown
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_iptables_rules_and_ip_forward_reverted_on_teardown(manager: WiFiManager) -> None:
    """
    _teardown_iptables_redirect must:
    - remove the PREROUTING and INPUT iptables rules, and
    - restore ip_forward to the exact value recorded in the save file.
    """
    original_fwd = "0"
    manager._IP_FORWARD_SAVE_PATH.write_text(original_fwd)
    # Teardown dispatches on the backend recorded during setup
    manager._redirect_backend = "iptables"

    captured: list[list[str]] = []

    with patch("src.wifi_manager.subprocess.run",
               side_effect=lambda cmd, **kw: (captured.append(list(cmd)) or _ok())), \
         patch.object(manager, "_find_command_path", side_effect=_find_path_side_effect):

        manager._teardown_iptables_redirect()

    assert [c for c in captured if "iptables" in " ".join(c) and "-D" in c and "PREROUTING" in c], \
        "Expected 'iptables -D PREROUTING' invocation"

    assert [c for c in captured if "iptables" in " ".join(c) and "-D" in c and "INPUT" in c], \
        "Expected 'iptables -D INPUT' invocation"

    restore_calls = [
        c for c in captured
        if "sysctl" in " ".join(c) and f"ip_forward={original_fwd}" in " ".join(c)
    ]
    assert restore_calls, f"Expected sysctl to restore ip_forward to {original_fwd!r}"

    assert not manager._IP_FORWARD_SAVE_PATH.exists(), \
        "Save file must be removed after successful teardown"


# ---------------------------------------------------------------------------
# 3b. ip_forward untouched when no save file exists
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_ip_forward_not_restored_when_save_file_absent(manager: WiFiManager) -> None:
    """
    When the save file is missing (setup never wrote it, e.g. because /proc was
    unreadable or the write failed), teardown must NOT call sysctl so it does not
    accidentally clobber ip_forward state owned by a VPN or NetworkManager.
    """
    assert not manager._IP_FORWARD_SAVE_PATH.exists()

    captured: list[list[str]] = []

    with patch("src.wifi_manager.subprocess.run",
               side_effect=lambda cmd, **kw: (captured.append(list(cmd)) or _ok())), \
         patch.object(manager, "_find_command_path", side_effect=_find_path_side_effect):

        manager._teardown_iptables_redirect()

    sysctl_calls = [
        c for c in captured
        if "sysctl" in " ".join(c) and "ip_forward" in " ".join(c)
    ]
    assert not sysctl_calls, \
        "sysctl must not be called when no ip_forward save file exists"


# ---------------------------------------------------------------------------
# 4. LED message content
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_led_message_shows_ssid_no_password_and_url(manager: WiFiManager) -> None:
    """
    When the nmcli AP activates, the LED message must include:
    - the AP SSID ('LEDMatrix-Setup')
    - the string 'No password'
    - the AP IP address (192.168.4.1) and Flask port (5000)
    """
    led_messages: list[str] = []

    with patch("src.wifi_manager.subprocess.run", return_value=_ok()), \
         patch.object(manager, "disconnect_from_network", return_value=(True, "ok")), \
         patch.object(manager, "_setup_iptables_redirect", return_value=True), \
         patch.object(manager, "_get_ap_status_nmcli",
                      return_value={"active": True, "ip": "192.168.4.1"}), \
         patch.object(manager, "_show_led_message",
                      side_effect=lambda msg, **kw: led_messages.append(msg)):

        success, _ = manager._enable_ap_mode_nmcli_hotspot()

    assert success, "AP enable should report success"
    assert led_messages, "Expected at least one _show_led_message call"

    combined = "\n".join(led_messages)
    assert "No password" in combined, "LED message must say 'No password'"
    assert "LEDMatrix-Setup" in combined, "LED message must include the AP SSID"
    assert "192.168.4.1" in combined, "LED message must include the AP IP address"
    assert "5000" in combined, "LED message must include the Flask port"


# ---------------------------------------------------------------------------
# 5. Stale AP profiles deleted before the new one is created
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_existing_ap_profiles_deleted_before_new_profile_created(manager: WiFiManager) -> None:
    """
    Before 'nmcli connection add', the manager must issue
    'nmcli connection down/delete' for every known AP profile name so stale
    profiles (from a previous crash or partial setup) cannot block the new one.
    """
    captured: list[list[str]] = []

    def _run(cmd, **kw):
        captured.append(list(cmd))
        return _ok()

    with patch("src.wifi_manager.subprocess.run", side_effect=_run), \
         patch.object(manager, "disconnect_from_network", return_value=(True, "ok")), \
         patch.object(manager, "_setup_iptables_redirect", return_value=True), \
         patch.object(manager, "_get_ap_status_nmcli",
                      return_value={"active": True, "ip": "192.168.4.1"}), \
         patch.object(manager, "_show_led_message"):

        success, _ = manager._enable_ap_mode_nmcli_hotspot()

    assert success

    cmd_strs = [" ".join(c) for c in captured]

    for profile in ("LEDMatrix-Setup-AP", "Hotspot", "TickerSetup-AP"):
        assert any("connection delete" in s and profile in s for s in cmd_strs), \
            f"Expected 'nmcli connection delete {profile}' before creating the new profile"

    add_indices = [i for i, s in enumerate(cmd_strs) if "connection add" in s]
    del_indices = [i for i, s in enumerate(cmd_strs) if "connection delete" in s]

    assert add_indices, "Expected 'nmcli connection add' call"
    assert del_indices, "Expected 'nmcli connection delete' calls"
    assert max(del_indices) < min(add_indices), \
        "All connection deletions must complete before the new profile is created"


# ---------------------------------------------------------------------------
# 6. Wi-Fi passwords are not kept in wifi_config.json
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_loading_scrubs_plaintext_saved_networks(wifi_config: Path) -> None:
    """Older versions wrote every joined network's password to the config in
    plaintext and never read it back. Loading must remove it from disk."""
    cfg = json.loads(wifi_config.read_text())
    cfg["saved_networks"] = [
        {"ssid": "HomeNet", "password": "hunter22", "saved_at": 0},
    ]
    wifi_config.write_text(json.dumps(cfg))

    with patch("src.wifi_manager.subprocess.run", return_value=_ok(stdout="wlan0\n")):
        mgr = WiFiManager(config_path=wifi_config)

    assert "saved_networks" not in mgr.config
    assert "hunter22" not in wifi_config.read_text()
    on_disk = json.loads(wifi_config.read_text())
    assert "saved_networks" not in on_disk
    # Everything else survives the scrub.
    assert on_disk["ap_ssid"] == "LEDMatrix-Setup"
    assert on_disk["auto_enable_ap_mode"] is True


@pytest.mark.unit
def test_default_config_has_no_saved_networks(tmp_path: Path) -> None:
    config_path = tmp_path / "config" / "wifi_config.json"
    config_path.parent.mkdir()

    with patch("src.wifi_manager.subprocess.run", return_value=_ok(stdout="wlan0\n")):
        WiFiManager(config_path=config_path)

    assert "saved_networks" not in json.loads(config_path.read_text())


@pytest.mark.unit
def test_connecting_does_not_store_the_password(manager: WiFiManager) -> None:
    commands = []

    def fake_run(cmd, *args, **kwargs):
        commands.append(cmd)
        # No existing profile for the SSID, so a new connection is created.
        if cmd[:3] == ["nmcli", "connection", "show"] and "HomeNet" in cmd:
            return _fail()
        return _ok(stdout="")

    with patch("src.wifi_manager.subprocess.run", side_effect=fake_run), \
         patch("src.wifi_manager.time.sleep"), \
         patch.object(manager, "_show_led_message"):
        manager._connect_nmcli("HomeNet", "hunter22")

    assert ["nmcli", "device", "wifi", "connect", "HomeNet", "password", "hunter22"] in commands, \
        "the new-connection path was not reached"
    assert "hunter22" not in json.dumps(manager.config)
    assert "hunter22" not in manager.config_path.read_text()


# ---------------------------------------------------------------------------
# 7. Disconnect takes the saved profile down
# ---------------------------------------------------------------------------

def _profile_nmcli(profiles: dict):
    """A fake subprocess.run for `nmcli connection show` over ``profiles``
    (profile name -> SSID), recording every command it is given."""
    commands = []

    def fake_run(cmd, *args, **kwargs):
        commands.append(cmd)
        if cmd == ["nmcli", "-t", "-f", "NAME,TYPE", "connection", "show"]:
            lines = [name.replace(":", "\\:") + ":802-11-wireless" for name in profiles]
            lines.append("Wired connection 1:802-3-ethernet")
            return _ok(stdout="\n".join(lines) + "\n")
        if cmd[:4] == ["nmcli", "-g", "802-11-wireless.ssid", "connection"]:
            return _ok(stdout=profiles.get(cmd[-1], "") + "\n")
        if cmd[:3] == ["nmcli", "connection", "show"]:
            return _ok() if cmd[3] in profiles else _fail()
        # nmcli rejects 802-11-wireless.ssid as a `connection show -f` column.
        if "802-11-wireless.ssid" in cmd:
            return _fail(stderr="Error: invalid field '802-11-wireless.ssid'")
        return _ok()

    return fake_run, commands


@pytest.mark.unit
def test_find_profile_for_ssid_matches_by_ssid_not_name(manager: WiFiManager) -> None:
    fake_run, _ = _profile_nmcli({"home: upstairs": "HomeNet", "Office": "OfficeNet"})
    with patch("src.wifi_manager.subprocess.run", side_effect=fake_run):
        assert manager._find_profile_for_ssid("HomeNet") == "home: upstairs"
        assert manager._find_profile_for_ssid("OfficeNet") == "Office"
        assert manager._find_profile_for_ssid("Elsewhere") is None


@pytest.mark.unit
def test_disconnect_takes_the_profile_down(manager: WiFiManager) -> None:
    from src.wifi_manager import WiFiStatus

    fake_run, commands = _profile_nmcli({"Home profile": "HomeNet"})
    with patch("src.wifi_manager.subprocess.run", side_effect=fake_run), \
         patch("src.wifi_manager.time.sleep"), \
         patch.object(manager, "get_wifi_status",
                      return_value=WiFiStatus(connected=True, ssid="HomeNet")):
        ok, _ = manager.disconnect_from_network(skip_ap_check=True)

    assert ok
    assert ["nmcli", "connection", "down", "Home profile"] in commands
    assert ["nmcli", "device", "disconnect", "wlan0"] in commands
