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
        # A bare MagicMock is truthy, which would send every connect down
        # the setup-AP background path.
        instance._is_ap_mode_active.return_value = False
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


class TestConnectThroughSetupAp:
    """With the setup AP up, the phone making the request is connected through
    the very network the connect tears down. A synchronous answer can never
    arrive, so the route answers 202 first and connects in the background."""

    URL = "/api/v3/wifi/connect"

    @pytest.fixture(autouse=True)
    def ap_active(self, api_v3_module, wifi_manager, monkeypatch):
        from web_interface.blueprints.api_v3 import wifi as wifi_routes
        wifi_manager._is_ap_mode_active.return_value = True
        monkeypatch.setattr(wifi_routes, "_AP_HANDOFF_DELAY_SECONDS", 0)
        monkeypatch.setattr(wifi_routes, "_last_connect_attempt", None)
        self.spawned = []
        monkeypatch.setattr(wifi_routes, "_spawn", self.spawned.append)
        self.routes = wifi_routes

    def _run_spawned(self):
        assert len(self.spawned) == 1
        self.spawned.pop()()

    def test_answers_202_before_connecting(self, api_v3_client, wifi_manager):
        response = api_v3_client.post(self.URL, json={"ssid": "HomeNet", "password": "hunter22"})
        assert response.status_code == 202
        assert response.get_json()["status"] == "pending"
        wifi_manager.connect_to_network.assert_not_called()
        assert self.routes._last_connect_snapshot()["state"] == "pending"

    def test_background_result_is_reported_by_status(self, api_v3_client, wifi_manager):
        wifi_manager.connect_to_network.return_value = (True, "Connected to HomeNet")
        api_v3_client.post(self.URL, json={"ssid": "HomeNet", "password": "hunter22"})
        self._run_spawned()
        wifi_manager.connect_to_network.assert_called_once_with("HomeNet", "hunter22")

        wifi_manager.config = {}
        wifi_manager.get_wifi_status.return_value = MagicMock(
            connected=True, ssid="HomeNet", ip_address="10.0.0.5", signal=70,
            ap_mode_active=False)
        attempt = api_v3_client.get("/api/v3/wifi/status").get_json()["data"]["last_connect_attempt"]
        assert attempt["ssid"] == "HomeNet"
        assert attempt["state"] == "success"
        assert "hunter22" not in str(attempt)

    def test_wrong_password_is_flagged(self, api_v3_client, wifi_manager):
        wifi_manager.connect_to_network.return_value = (
            False, "wrong_password: Secrets were required, but not provided")
        api_v3_client.post(self.URL, json={"ssid": "HomeNet", "password": "hunter22"})
        self._run_spawned()
        attempt = self.routes._last_connect_snapshot()
        assert attempt["state"] == "failed"
        assert attempt["error_type"] == "wrong_password"
        assert attempt["message"] == "Incorrect password for HomeNet"

    def test_manager_exception_is_recorded_as_a_failure(self, api_v3_client, wifi_manager):
        wifi_manager.connect_to_network.side_effect = RuntimeError("nmcli vanished")
        api_v3_client.post(self.URL, json={"ssid": "HomeNet"})
        self._run_spawned()
        attempt = self.routes._last_connect_snapshot()
        assert attempt["state"] == "failed"
        assert "RuntimeError" in attempt["message"]

    def test_a_second_connect_while_one_is_pending_is_refused(self, api_v3_client, wifi_manager):
        api_v3_client.post(self.URL, json={"ssid": "HomeNet"})
        response = api_v3_client.post(self.URL, json={"ssid": "OtherNet"})
        assert response.status_code == 409
        assert len(self.spawned) == 1


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
        (False, False), ("false", False), ("no", False), (0, False),
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

    def test_unrecognized_enabled_value_is_rejected_not_treated_as_false(
            self, api_v3_client, wifi_manager):
        # An invalid `enabled` used to silently fall back to False, which
        # can disconnect Wi-Fi (or, with force=true, drop the caller's own
        # connection to this interface) even though nothing asked for that.
        response = api_v3_client.post(self.URL, json={"enabled": "typo"})
        assert response.status_code == 400
        wifi_manager.set_wifi_radio.assert_not_called()

    def test_unrecognized_force_value_is_rejected_not_treated_as_false(
            self, api_v3_client, wifi_manager):
        response = api_v3_client.post(
            self.URL, json={"enabled": False, "force": "typo"})
        assert response.status_code == 400
        wifi_manager.set_wifi_radio.assert_not_called()


class TestAutoEnableApMode:
    URL = "/api/v3/wifi/ap/auto-enable"

    def test_requires_the_field(self, api_v3_client, wifi_manager):
        response = api_v3_client.post(self.URL, json={})
        assert response.status_code == 400

    @pytest.mark.parametrize("raw,expected", [
        (True, True), (False, False),
        ("true", True), ("True", True), ("1", True), ("yes", True),
        ("false", False), ("False", False), ("0", False), ("no", False),
        (1, True), (0, False),
    ])
    def test_value_is_coerced_not_just_truthy(
            self, api_v3_client, wifi_manager, raw, expected):
        # Regression: bool(data['auto_enable_ap_mode']) meant a caller who
        # sent the JSON string "false" got it stored as True — bool("false")
        # is True, since any non-empty string is truthy.
        wifi_manager.config = {}
        response = api_v3_client.post(self.URL, json={"auto_enable_ap_mode": raw})
        assert response.status_code == 200, response.get_json()
        assert response.get_json()["data"]["auto_enable_ap_mode"] is expected
        assert wifi_manager.config["auto_enable_ap_mode"] is expected

    def test_a_string_false_does_not_enable_it(self, api_v3_client, wifi_manager):
        # The exact shape of the bug.
        wifi_manager.config = {}
        api_v3_client.post(self.URL, json={"auto_enable_ap_mode": "false"})
        assert wifi_manager.config["auto_enable_ap_mode"] is False

    def test_unrecognized_value_is_rejected_not_treated_as_false(
            self, api_v3_client, wifi_manager):
        wifi_manager.config = {}
        response = api_v3_client.post(self.URL, json={"auto_enable_ap_mode": "typo"})
        assert response.status_code == 400
        assert "auto_enable_ap_mode" not in wifi_manager.config


class TestRadioEnabledAndForceAcceptIntegers:
    """`{"enabled": 1}` / `{"enabled": 0}` used to be mishandled: the old
    coercion was `raw is True or (isinstance(raw, str) and ...)`, and
    `1 is True` is False in Python -- an int is never the `True` singleton
    even though it equals it -- so a plain integer fell through to False
    regardless of its value.
    """
    URL = "/api/v3/wifi/radio"

    @pytest.mark.parametrize("raw,expected", [(1, True), (0, False)])
    def test_enabled_as_an_integer(self, api_v3_client, wifi_manager, raw, expected):
        wifi_manager.set_wifi_radio.return_value = (True, "ok", None)
        wifi_manager.get_wifi_radio_state.return_value = {}
        api_v3_client.post(self.URL, json={"enabled": raw})
        wifi_manager.set_wifi_radio.assert_called_once_with(expected, force=False)

    @pytest.mark.parametrize("raw,expected", [(1, True), (0, False)])
    def test_force_as_an_integer(self, api_v3_client, wifi_manager, raw, expected):
        wifi_manager.set_wifi_radio.return_value = (True, "ok", None)
        wifi_manager.get_wifi_radio_state.return_value = {}
        api_v3_client.post(self.URL, json={"enabled": True, "force": raw})
        wifi_manager.set_wifi_radio.assert_called_once_with(True, force=expected)


class TestNoRealNetworking:
    def test_wifi_manager_is_never_constructed_for_real(self, api_v3_client):
        # Guard against a future refactor moving the import to module level,
        # where the fixture's patch of the definition site would stop
        # applying and the tests would start driving real networking.
        with patch("src.wifi_manager.WiFiManager") as cls:
            cls.return_value.disconnect_from_network.return_value = (True, "ok")
            api_v3_client.post("/api/v3/wifi/disconnect")
            assert cls.called
