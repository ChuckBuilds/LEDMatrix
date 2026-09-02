"""
Endpoint tests for the /wifi/* routes in api_v3.

These routes drive the host's actual networking — connecting, dropping a
connection, switching the radio off — and had no endpoint-level tests at
all. WiFiManager is mocked throughout; nothing here may touch real
networking.

Each handler does `from src.wifi_manager import WiFiManager` inside the
function body, so the patch target is the class at its definition site.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from test._api_v3_test_helpers import api_v3_client, api_v3_module  # noqa: F401,E402


@pytest.fixture
def wifi_manager():
    """Patch WiFiManager where it is defined; yield the instance mock."""
    with patch("src.wifi_manager.WiFiManager") as cls:
        instance = MagicMock()
        cls.return_value = instance
        yield instance


class TestConnect:
    URL = "/api/v3/wifi/connect"

    def test_success(self, api_v3_client, wifi_manager):
        wifi_manager.connect_to_network.return_value = (True, "Connected to HomeNet")
        response = api_v3_client.post(self.URL, json={"ssid": "HomeNet", "password": "pw"})
        assert response.status_code == 200
        assert response.get_json()["message"] == "Connected to HomeNet"
        wifi_manager.connect_to_network.assert_called_once_with("HomeNet", "pw")

    def test_missing_body_rejected(self, api_v3_client, wifi_manager):
        response = api_v3_client.post(self.URL, json={})
        assert response.status_code == 400
        wifi_manager.connect_to_network.assert_not_called()

    def test_missing_ssid_rejected(self, api_v3_client, wifi_manager):
        response = api_v3_client.post(self.URL, json={"password": "pw"})
        assert response.status_code == 400
        assert "SSID is required" in response.get_json()["message"]
        wifi_manager.connect_to_network.assert_not_called()

    @pytest.mark.parametrize("ssid", ["", "   ", "\t"])
    def test_blank_ssid_rejected(self, api_v3_client, wifi_manager, ssid):
        response = api_v3_client.post(self.URL, json={"ssid": ssid})
        assert response.status_code == 400
        wifi_manager.connect_to_network.assert_not_called()

    def test_ssid_is_trimmed(self, api_v3_client, wifi_manager):
        wifi_manager.connect_to_network.return_value = (True, "ok")
        api_v3_client.post(self.URL, json={"ssid": "  HomeNet  "})
        wifi_manager.connect_to_network.assert_called_once_with("HomeNet", "")

    def test_missing_password_becomes_empty_string(self, api_v3_client, wifi_manager):
        wifi_manager.connect_to_network.return_value = (True, "ok")
        api_v3_client.post(self.URL, json={"ssid": "OpenNet"})
        wifi_manager.connect_to_network.assert_called_once_with("OpenNet", "")

    def test_null_password_becomes_empty_string(self, api_v3_client, wifi_manager):
        wifi_manager.connect_to_network.return_value = (True, "ok")
        api_v3_client.post(self.URL, json={"ssid": "OpenNet", "password": None})
        wifi_manager.connect_to_network.assert_called_once_with("OpenNet", "")

    def test_failure_reports_the_managers_reason(self, api_v3_client, wifi_manager):
        wifi_manager.connect_to_network.return_value = (False, "Bad password")
        response = api_v3_client.post(self.URL, json={"ssid": "HomeNet"})
        assert response.status_code == 400
        assert response.get_json()["message"] == "Bad password"

    def test_failure_without_reason_uses_fallback_text(self, api_v3_client, wifi_manager):
        wifi_manager.connect_to_network.return_value = (False, None)
        response = api_v3_client.post(self.URL, json={"ssid": "HomeNet"})
        assert response.status_code == 400
        assert response.get_json()["message"] == "Failed to connect to network"

    def test_manager_exception_is_a_500_without_leaking_internals(
            self, api_v3_client, wifi_manager):
        wifi_manager.connect_to_network.side_effect = RuntimeError(
            "/usr/lib/secret/path blew up")
        response = api_v3_client.post(self.URL, json={"ssid": "HomeNet"})
        assert response.status_code == 500
        body = response.get_json()
        assert body["message"] == "An error occurred; see logs for details"
        # `details` comes from describe_exception, which is deliberately
        # safe to return (redacted, capped) — it names the type.
        assert "RuntimeError" in body["details"]


class TestDisconnect:
    URL = "/api/v3/wifi/disconnect"

    def test_success(self, api_v3_client, wifi_manager):
        wifi_manager.disconnect_from_network.return_value = (True, "Disconnected")
        response = api_v3_client.post(self.URL)
        assert response.status_code == 200
        assert response.get_json()["message"] == "Disconnected"

    def test_failure(self, api_v3_client, wifi_manager):
        wifi_manager.disconnect_from_network.return_value = (False, "Not connected")
        response = api_v3_client.post(self.URL)
        assert response.status_code == 400
        assert response.get_json()["message"] == "Not connected"

    def test_failure_without_reason_uses_fallback(self, api_v3_client, wifi_manager):
        wifi_manager.disconnect_from_network.return_value = (False, "")
        response = api_v3_client.post(self.URL)
        assert response.get_json()["message"] == "Failed to disconnect from network"

    def test_exception_is_a_500(self, api_v3_client, wifi_manager):
        wifi_manager.disconnect_from_network.side_effect = OSError("nmcli missing")
        assert api_v3_client.post(self.URL).status_code == 500


class TestApMode:
    ENABLE = "/api/v3/wifi/ap/enable"
    DISABLE = "/api/v3/wifi/ap/disable"

    def test_enable_success(self, api_v3_client, wifi_manager):
        wifi_manager.enable_ap_mode.return_value = (True, "AP enabled")
        response = api_v3_client.post(self.ENABLE, json={})
        assert response.status_code == 200
        wifi_manager.enable_ap_mode.assert_called_once_with(force=False)

    @pytest.mark.parametrize("raw,expected", [
        (True, True), (False, False),
        ("true", True), ("TRUE", True), ("1", True),
        ("false", False), ("no", False), ("yes", False),
        (1, False),  # only real True or the listed strings count
    ])
    def test_force_coercion(self, api_v3_client, wifi_manager, raw, expected):
        wifi_manager.enable_ap_mode.return_value = (True, "ok")
        api_v3_client.post(self.ENABLE, json={"force": raw})
        wifi_manager.enable_ap_mode.assert_called_once_with(force=expected)

    def test_enable_without_body(self, api_v3_client, wifi_manager):
        wifi_manager.enable_ap_mode.return_value = (True, "ok")
        assert api_v3_client.post(self.ENABLE).status_code == 200

    def test_enable_failure(self, api_v3_client, wifi_manager):
        wifi_manager.enable_ap_mode.return_value = (False, "hostapd missing")
        response = api_v3_client.post(self.ENABLE, json={})
        assert response.status_code == 400
        assert response.get_json()["message"] == "hostapd missing"

    def test_disable_success(self, api_v3_client, wifi_manager):
        wifi_manager.disable_ap_mode.return_value = (True, "AP disabled")
        assert api_v3_client.post(self.DISABLE).status_code == 200

    def test_disable_failure(self, api_v3_client, wifi_manager):
        wifi_manager.disable_ap_mode.return_value = (False, "not running")
        assert api_v3_client.post(self.DISABLE).status_code == 400

    def test_enable_exception_is_a_500(self, api_v3_client, wifi_manager):
        wifi_manager.enable_ap_mode.side_effect = RuntimeError("boom")
        assert api_v3_client.post(self.ENABLE, json={}).status_code == 500


class TestRadio:
    URL = "/api/v3/wifi/radio"

    def test_get_state(self, api_v3_client, wifi_manager):
        wifi_manager.get_wifi_radio_state.return_value = {
            "enabled": True, "ethernet_connected": False}
        response = api_v3_client.get(self.URL)
        assert response.status_code == 200
        assert response.get_json()["data"]["enabled"] is True

    def test_get_state_exception_is_a_500(self, api_v3_client, wifi_manager):
        wifi_manager.get_wifi_radio_state.side_effect = OSError("rfkill missing")
        assert api_v3_client.get(self.URL).status_code == 500

    def test_enabled_is_required(self, api_v3_client, wifi_manager):
        response = api_v3_client.post(self.URL, json={})
        assert response.status_code == 400
        assert "enabled is required" in response.get_json()["message"]
        wifi_manager.set_wifi_radio.assert_not_called()

    def test_enable_success(self, api_v3_client, wifi_manager):
        wifi_manager.set_wifi_radio.return_value = (True, "Radio on", None)
        wifi_manager.get_wifi_radio_state.return_value = {"enabled": True}
        response = api_v3_client.post(self.URL, json={"enabled": True})
        assert response.status_code == 200
        wifi_manager.set_wifi_radio.assert_called_once_with(True, force=False)

    @pytest.mark.parametrize("raw,expected", [
        (True, True), ("true", True), ("1", True), ("yes", True),
        (False, False), ("false", False), ("off", False), (0, False),
    ])
    def test_enabled_coercion_is_string_aware(
            self, api_v3_client, wifi_manager, raw, expected):
        # bool("false") is True, so the endpoint parses strings explicitly
        # rather than trusting truthiness — it is a public contract, not
        # only the shipped UI which always sends real JSON booleans.
        wifi_manager.set_wifi_radio.return_value = (True, "ok", None)
        wifi_manager.get_wifi_radio_state.return_value = {}
        api_v3_client.post(self.URL, json={"enabled": raw})
        wifi_manager.set_wifi_radio.assert_called_once_with(expected, force=False)

    def test_force_passed_through(self, api_v3_client, wifi_manager):
        wifi_manager.set_wifi_radio.return_value = (True, "ok", None)
        wifi_manager.get_wifi_radio_state.return_value = {}
        api_v3_client.post(self.URL, json={"enabled": False, "force": "true"})
        wifi_manager.set_wifi_radio.assert_called_once_with(False, force=True)

    def test_refusal_reports_reason(self, api_v3_client, wifi_manager):
        # Disabling the radio without Ethernet would lock the user out of
        # this very interface, so the manager can refuse with a reason.
        wifi_manager.set_wifi_radio.return_value = (
            False, "Refusing: no wired fallback", "no_ethernet")
        response = api_v3_client.post(self.URL, json={"enabled": False})
        assert response.status_code == 400
        body = response.get_json()
        assert body["reason"] == "no_ethernet"
        assert "Refusing" in body["message"]

    def test_exception_is_a_500(self, api_v3_client, wifi_manager):
        wifi_manager.set_wifi_radio.side_effect = RuntimeError("boom")
        assert api_v3_client.post(self.URL, json={"enabled": True}).status_code == 500


class TestNoRealNetworking:
    def test_wifi_manager_is_never_constructed_for_real(self, api_v3_client):
        # Guard against a future refactor moving the import to module level,
        # where the fixture's patch of the definition site would stop
        # applying and the tests would start driving real networking.
        with patch("src.wifi_manager.WiFiManager") as cls:
            cls.return_value.disconnect_from_network.return_value = (True, "ok")
            api_v3_client.post("/api/v3/wifi/disconnect")
            assert cls.called
