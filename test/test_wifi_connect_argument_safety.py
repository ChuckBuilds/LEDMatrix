"""The SSID and password reaching nmcli's argv come from an HTTP request body.

POST /api/v3/wifi/connect takes both verbatim and WiFiManager.connect_to_network
hands them to::

    subprocess.run(["nmcli", "device", "wifi", "connect", ssid, "password", password])

There is no shell in that, so no metacharacter can start a second command --
CodeQL's py/command-line-injection alert overstates it on that point. What is
real is argument injection: nmcli reads a leading "-" as an option, so an SSID
of "--ask" or "-t" asks nmcli to *run differently* rather than to join a
network. Neither value was checked for shape at all before reaching argv.

These tests pin the validation without touching real networking: the
validators are classmethods, so nothing here constructs a WiFiManager.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.wifi_manager import WiFiManager  # noqa: E402
from test._api_v3_test_helpers import api_v3_client, api_v3_module  # noqa: F401,E402


class TestSsidValidation:
    @pytest.mark.parametrize("ssid", [
        "HomeNet", "my wifi 5G", "Cafe-Guest", "café", "x" * 32, "-not-leading".lstrip("-"),
    ])
    def test_ordinary_ssids_pass_through(self, ssid):
        value, error = WiFiManager._validate_ssid(ssid)
        assert error is None
        assert value == ssid

    def test_surrounding_whitespace_is_trimmed_not_rejected(self):
        value, error = WiFiManager._validate_ssid("  HomeNet  ")
        assert error is None
        assert value == "HomeNet"

    @pytest.mark.parametrize("ssid", ["--ask", "-t", "-"])
    def test_an_ssid_nmcli_would_read_as_an_option_is_refused(self, ssid):
        value, error = WiFiManager._validate_ssid(ssid)
        assert error is not None
        assert value == ""

    def test_an_ssid_over_32_octets_is_refused(self):
        # 802.11 caps the SSID element at 32 octets, so a longer one could
        # never name a real network.
        _, error = WiFiManager._validate_ssid("x" * 33)
        assert error is not None
        # Multi-byte characters count as octets, not characters.
        _, error = WiFiManager._validate_ssid("é" * 17)
        assert error is not None

    @pytest.mark.parametrize("ssid", ["a\nb", "a\rb", "a\x00b", "a\x7fb", "a\tb"])
    def test_control_characters_are_refused(self, ssid):
        _, error = WiFiManager._validate_ssid(ssid)
        assert error is not None

    @pytest.mark.parametrize("ssid", ["", "   ", None, 42, ["HomeNet"]])
    def test_empty_and_non_text_values_are_refused(self, ssid):
        _, error = WiFiManager._validate_ssid(ssid)
        assert error is not None


class TestPasswordValidation:
    def test_an_empty_password_means_an_open_network(self):
        value, error = WiFiManager._validate_wifi_password("")
        assert error is None
        assert value == ""
        value, error = WiFiManager._validate_wifi_password(None)
        assert error is None
        assert value == ""

    @pytest.mark.parametrize("password", ["hunter22", "a" * 63, "0" * 64, "AbCdEf0123" * 6 + "abcd"])
    def test_valid_psk_lengths_pass_through(self, password):
        value, error = WiFiManager._validate_wifi_password(password)
        assert error is None, f"{password!r} rejected: {error}"
        assert value == password

    @pytest.mark.parametrize("password", ["short", "z" * 64, "a" * 200])
    def test_lengths_that_could_never_authenticate_are_refused(self, password):
        # 8-63 chars for a passphrase, or exactly 64 hex chars for a raw key.
        # "z" * 64 is the right length for a key but is not hex, so it is
        # neither -- note "a" * 64 *is* valid hex and must stay accepted.
        _, error = WiFiManager._validate_wifi_password(password)
        assert error is not None

    @pytest.mark.parametrize("password", ["-password", "--ask"])
    def test_a_password_nmcli_would_read_as_an_option_is_refused(self, password):
        _, error = WiFiManager._validate_wifi_password(password)
        assert error is not None

    def test_control_characters_are_refused(self):
        _, error = WiFiManager._validate_wifi_password("pass\nword")
        assert error is not None


class TestConnectRefusesBeforeRunningNmcli:
    """connect_to_network must not reach subprocess with a rejected value."""

    @pytest.mark.parametrize("ssid,password", [
        ("--ask", "hunter22"),
        ("x" * 40, "hunter22"),
        ("Home\nNet", "hunter22"),
        ("HomeNet", "-secret1"),
        ("HomeNet", "short"),
    ])
    def test_no_subprocess_runs_for_a_rejected_request(self, ssid, password):
        manager = WiFiManager.__new__(WiFiManager)  # no __init__: no real host access
        with patch("src.wifi_manager.subprocess.run") as run:
            ok, message = manager.connect_to_network(ssid, password)
        assert ok is False
        assert message
        run.assert_not_called()


class TestConnectEndpointSurfacesTheRefusal:
    URL = "/api/v3/wifi/connect"

    @pytest.fixture
    def wifi_manager(self):
        with patch("src.wifi_manager.WiFiManager") as cls:
            instance = MagicMock()
            cls.return_value = instance
            yield instance

    def test_a_rejected_ssid_comes_back_as_a_client_error(
        self, api_v3_client, wifi_manager
    ):
        # The route delegates the shape check to the manager, so mirror what
        # the real one now returns rather than asserting on a mock's default.
        wifi_manager.connect_to_network.return_value = (False, "SSID cannot start with '-'")
        response = api_v3_client.post(self.URL, json={"ssid": "--ask", "password": "hunter22"})
        assert response.status_code == 400
        assert "-" in response.get_json()["message"]

    def test_an_ordinary_request_is_unaffected(self, api_v3_client, wifi_manager):
        wifi_manager.connect_to_network.return_value = (True, "Connected to HomeNet")
        response = api_v3_client.post(self.URL, json={"ssid": "HomeNet", "password": "hunter22"})
        assert response.status_code == 200
        wifi_manager.connect_to_network.assert_called_once_with("HomeNet", "hunter22")
