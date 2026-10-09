"""No API response carries an exception's message (CodeQL py/stack-trace-exposure).

One representative route per file that had open alerts. Each forces a failure
whose message holds a marker and asserts the marker is nowhere in the body:
the message goes to the log, the client gets a fixed message plus a reason
code (describe_exception: the type, and the errno for an OSError).
"""

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from test._api_v3_test_helpers import api_v3_client, api_v3_module  # noqa: F401,E402

LEAK = "LEAKED-/home/pi/secret token=abc123"
API = "web_interface.blueprints.api_v3"


def _assert_no_leak(response):
    body = response.get_data(as_text=True)
    assert "LEAKED" not in body, body
    assert "abc123" not in body, body
    return json.loads(body)


def test_display_service_status_drops_systemctl_output(api_v3_module, api_v3_client,
                                                      monkeypatch):
    """display.py: the on-demand routes return the service status verbatim."""
    api_v3_module.api_v3.cache_manager.get.return_value = None
    monkeypatch.setattr(f"{API}.display.display_state.read_state", lambda: None)
    with patch(f"{API}.subprocess.run", side_effect=OSError(13, LEAK)):
        body = _assert_no_leak(api_v3_client.get("/api/v3/display/on-demand/status"))
    assert body["data"]["service"] == {"active": False, "returncode": -1}


@pytest.mark.parametrize("helper", ["_ensure_display_service_running",
                                    "_stop_display_service"])
def test_service_results_keep_returncode_but_not_output(api_v3_module, helper):
    """display.py start/stop: returncode/active/started stay, stdout/stderr go."""
    failed = MagicMock(returncode=1, stdout=LEAK, stderr=LEAK)
    with patch(f"{API}.subprocess.run", return_value=failed):
        result = getattr(api_v3_module, helper)()
    assert "LEAKED" not in json.dumps(result)
    assert result["returncode"] == 1 and result["active"] is False
    assert "stdout" not in result and "stderr" not in result


def test_wifi_connect_failure(api_v3_client):
    """wifi.py: a raising connect, and the attempt /wifi/status reports after."""
    with patch("src.wifi_manager.WiFiManager") as cls:
        cls.return_value._is_ap_mode_active.return_value = False
        cls.return_value.connect_to_network.side_effect = RuntimeError(LEAK)
        body = _assert_no_leak(api_v3_client.post(
            "/api/v3/wifi/connect", json={"ssid": "HomeNet", "password": "pw"}))
        assert body["details"] == "RuntimeError"
        cls.return_value.get_wifi_status.return_value = MagicMock(
            connected=False, ssid=None, ip_address=None, signal=0, ap_mode_active=False)
        cls.return_value.config = {}
        status = _assert_no_leak(api_v3_client.get("/api/v3/wifi/status"))
    assert status["data"]["last_connect_attempt"]["message"] == (
        "Failed to connect to network (RuntimeError)")


def test_wifi_manager_messages_carry_no_exception_text():
    """src/wifi_manager.py: its (success, message) is what the wifi routes return."""
    from src.wifi_manager import WiFiManager
    manager = WiFiManager.__new__(WiFiManager)  # no __init__: no host access
    manager.get_wifi_status = MagicMock(side_effect=OSError(5, LEAK))
    success, message = manager.disconnect_from_network()
    assert success is False
    assert "LEAKED" not in message and "OSError" in message


def test_system_action_exception(api_v3_client):
    """system.py: execute_system_action's catch-all."""
    with patch("subprocess.run", side_effect=OSError(5, LEAK)):
        body = _assert_no_leak(api_v3_client.post(
            "/api/v3/system/action", json={"action": "stop_display"}))
    assert body["details"] == "OSError:EIO"


def test_calendar_registration_failure(api_v3_client, tmp_path, monkeypatch):
    """plugin_calendar.py: the auth script could not be run."""
    plugin_dir = tmp_path / "calendar"
    plugin_dir.mkdir()
    (plugin_dir / "credentials.json").write_text("{}", encoding="utf-8")
    (plugin_dir / "calendar_registration.py").write_text("", encoding="utf-8")
    monkeypatch.setattr(f"{API}._calendar_plugin_dir", lambda: plugin_dir)
    with patch(f"{API}.subprocess.run", side_effect=OSError(13, LEAK)):
        body = _assert_no_leak(api_v3_client.post(
            "/api/v3/plugins/calendar/authenticate", json={"code": "x"}))
    assert "EACCES" in body["message"]


def test_health_failure(api_v3_client, monkeypatch):
    """misc.py: get_health's catch-all."""
    def boom():
        raise RuntimeError(LEAK)
    monkeypatch.setattr(f"{API}.misc._get_display_service_status", boom)
    body = _assert_no_leak(api_v3_client.get("/api/v3/health"))
    assert body["details"] == "RuntimeError"


def test_config_route_failure(api_v3_module, api_v3_client):
    """error_handler.py: create_error_response, as config.py's routes use it."""
    api_v3_module.api_v3.config_manager.load_config.side_effect = RuntimeError(LEAK)
    body = _assert_no_leak(api_v3_client.get("/api/v3/config/schedule"))
    assert body["details"] == "RuntimeError"


def test_plugin_route_failure(api_v3_module, api_v3_client):
    """plugins.py: an unhandled error in a plugin route."""
    api_v3_module.api_v3.plugin_catalog.get_all_plugin_info.side_effect = RuntimeError(LEAK)
    body = _assert_no_leak(api_v3_client.get("/api/v3/plugins/installed"))
    assert body["details"] == "RuntimeError"


def test_starlark_route_failure(api_v3_client):
    """starlark.py: one of its catch-alls."""
    with patch(f"{API}._get_starlark_plugin", side_effect=RuntimeError(LEAK)):
        body = _assert_no_leak(api_v3_client.get("/api/v3/starlark/status"))
    assert body["details"] == "RuntimeError"


def test_unit_refresh_failure(monkeypatch):
    """system.py git_pull: perform_core_update appends unit_refresh's message."""
    from web_interface import unit_refresh

    def boom(*_a, **_k):
        raise RuntimeError(LEAK)
    monkeypatch.setattr(unit_refresh, "stale_units", boom)
    result = unit_refresh.refresh_after_update()
    assert result["status"] == unit_refresh.FAILED
    assert "LEAKED" not in result["message"]


def test_install_base_requirements_failure(api_v3_client):
    """system.py: a pip install that could not start, in the action's output."""
    with patch(f"{API}.system._pip_install_requirements", side_effect=OSError(5, LEAK)):
        body = _assert_no_leak(api_v3_client.post(
            "/api/v3/system/action", json={"action": "install_base_requirements"}))
    assert "Failed: OSError:EIO" in body["output"]
