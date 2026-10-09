"""POST /display/on-demand/start and /stop: control socket first, mailbox fallback.

The routes hand the request to the display over the control socket
(src/ipc) and get an acknowledgement. When the socket could not carry the
request -- no socket (a stopped display, or one older than the socket), a
refused or timed-out connect, a display too old to know the command, a bug
in the client -- they write the file mailbox exactly as they did before the
socket existed. When the display had the request and failed it (a full
queue, bad arguments, no answer in time) the route says so and writes
nothing. These tests pin each path, that at most one of them is used, that
the response says which, and that the request id is the same either way (the
display deduplicates on it).

The socket client is patched at the route's module attribute; the last class
runs a real server on a temp socket (Linux/macOS only).
"""

import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from test._api_v3_test_helpers import api_v3_client, api_v3_module  # noqa: F401,E402

from src.ipc import client as control_client  # noqa: E402
from src.ipc import contract as c  # noqa: E402

START_URL = "/api/v3/display/on-demand/start"
STOP_URL = "/api/v3/display/on-demand/stop"
MAILBOX = "display_on_demand_request"
CLIENT = "web_interface.blueprints.api_v3.display.control_client"


@pytest.fixture
def service(api_v3_module):
    """A running display service; records systemctl calls and mailbox writes."""
    api_v3_module.api_v3.plugin_catalog = None
    api_v3_module.api_v3.config_manager = None
    state = {"active": True}
    calls = []

    def status():
        return {"active": state["active"]}

    def systemctl(args):
        calls.append(("systemctl", args[-2]))
        if args[-2:] == ["start", "ledmatrix.service"]:
            state["active"] = True
        return {"returncode": 0, "stdout": "", "stderr": ""}

    cache = api_v3_module.api_v3.cache_manager
    cache.set.side_effect = lambda key, value, *a, **kw: calls.append(("cache", key))
    with patch("web_interface.blueprints.api_v3._get_display_service_status",
               side_effect=status), \
         patch("web_interface.blueprints.api_v3.display._get_display_service_status",
               side_effect=status), \
         patch("web_interface.blueprints.api_v3._run_systemctl_command",
               side_effect=systemctl), \
         patch("web_interface.blueprints.api_v3.display._stop_display_service"):
        yield {"state": state, "cache": cache, "calls": calls}


def _mailbox_writes(cache):
    return [call.args[1] for call in cache.set.call_args_list
            if call.args and call.args[0] == MAILBOX]


def _ack(request_id, *a, **kw):
    return {"accepted": True, "request_id": request_id, "queued": 1}


class TestSocketPath:
    def test_start_goes_over_the_socket_and_skips_the_mailbox(self, api_v3_client, service):
        with patch(f"{CLIENT}.on_demand_start", side_effect=_ack) as start:
            resp = api_v3_client.post(START_URL, json={
                "plugin_id": "weather", "mode": "weather_current",
                "duration": 60, "pinned": True})
        assert resp.status_code == 200, resp.get_json()
        data = resp.get_json()["data"]
        assert data["transport"] == "socket"
        assert "socket_error" not in data
        assert _mailbox_writes(service["cache"]) == []
        start.assert_called_once_with(data["request_id"], "weather", "weather_current", 60, True)

    def test_a_callers_request_id_is_passed_through(self, api_v3_client, service):
        with patch(f"{CLIENT}.on_demand_start", side_effect=_ack) as start:
            data = api_v3_client.post(START_URL, json={
                "plugin_id": "weather", "request_id": "ha-123"}).get_json()["data"]
        assert data["request_id"] == "ha-123"
        assert start.call_args.args[0] == "ha-123"

    def test_stop_goes_over_the_socket(self, api_v3_client, service):
        with patch(f"{CLIENT}.on_demand_stop", side_effect=_ack) as stop:
            data = api_v3_client.post(STOP_URL, json={}).get_json()["data"]
        assert data["transport"] == "socket"
        stop.assert_called_once_with(data["request_id"])
        assert _mailbox_writes(service["cache"]) == []


class TestMailboxFallback:
    @pytest.mark.parametrize("reason", [
        "no_socket", "refused", "timeout", "closed", "bad_response", "invalid_request",
        "busy", "forbidden", "unknown_command", "unsupported_version", "disabled",
        "unsupported",
    ])
    def test_a_request_the_socket_never_carried_writes_the_mailbox(
            self, api_v3_client, service, reason):
        # sent=False: the display never had it (no socket, a refused or
        # timed-out connect, turned away at the door).
        with patch(f"{CLIENT}.on_demand_start",
                   side_effect=control_client.ControlError(reason, "x", sent=False)):
            resp = api_v3_client.post(START_URL, json={
                "plugin_id": "weather", "mode": "weather_current",
                "duration": 60, "pinned": True})
        assert resp.status_code == 200
        data = resp.get_json()["data"]
        assert data["transport"] == "mailbox"
        assert data["socket_error"] == reason
        [write] = _mailbox_writes(service["cache"])
        assert write["request_id"] == data["request_id"]
        assert write["action"] == "start"
        assert (write["plugin_id"], write["mode"], write["duration"], write["pinned"]) == \
            ("weather", "weather_current", 60, True)

    def test_a_client_bug_still_falls_back(self, api_v3_client, service):
        with patch(f"{CLIENT}.on_demand_start", side_effect=RuntimeError("boom")):
            resp = api_v3_client.post(START_URL, json={"plugin_id": "weather"})
        assert resp.status_code == 200
        assert resp.get_json()["data"]["socket_error"] == "internal"
        assert len(_mailbox_writes(service["cache"])) == 1

    def test_an_unknown_reason_is_reported_as_other(self, api_v3_client, service):
        # Only known codes are echoed back; anything else stays server-side.
        with patch(f"{CLIENT}.on_demand_start",
                   side_effect=control_client.ControlError("/run/secret/path", "x")):
            data = api_v3_client.post(START_URL, json={"plugin_id": "weather"}).get_json()["data"]
        assert data["transport"] == "mailbox"
        assert data["socket_error"] == "other"
        assert len(_mailbox_writes(service["cache"])) == 1

    def test_every_display_error_code_is_reportable(self):
        from web_interface.blueprints.api_v3 import display
        codes = {v for k, v in vars(c.ErrorCode).items() if not k.startswith("_")}
        assert codes <= set(display._REPORTABLE_SOCKET_REASONS)

    def test_stop_falls_back(self, api_v3_client, service):
        with patch(f"{CLIENT}.on_demand_stop",
                   side_effect=control_client.ControlError("timeout")):
            data = api_v3_client.post(STOP_URL, json={}).get_json()["data"]
        assert data["transport"] == "mailbox"
        [write] = _mailbox_writes(service["cache"])
        assert write == {"request_id": data["request_id"], "action": "stop",
                         "timestamp": write["timestamp"]}

    def test_a_stopped_display_gets_the_mailbox_before_it_is_started(
            self, api_v3_client, service):
        service["state"]["active"] = False
        with patch(f"{CLIENT}.on_demand_start",
                   side_effect=control_client.ControlError("no_socket")):
            resp = api_v3_client.post(START_URL, json={"plugin_id": "weather"})
        assert resp.status_code == 200
        assert service["calls"] == [("cache", MAILBOX), ("systemctl", "start")]

    def test_the_socket_is_off_in_the_test_suite(self, api_v3_client, service):
        # conftest's _hermetic_control_socket: a suite run on a device must
        # not drive the live display.
        assert os.environ[c.SOCKET_PATH_ENV] == "off"
        data = api_v3_client.post(START_URL, json={"plugin_id": "weather"}).get_json()["data"]
        assert data["transport"] == "mailbox"
        assert data["socket_error"] in ("disabled", "unsupported")   # Linux, Windows


class TestTheDisplayHadIt:
    """Once the display has the request, its answer stands: no mailbox copy.

    A busy queue, a refusal or silence after the request was sent mean the
    display may have applied it, or would refuse the mailbox copy too, so
    the route reports the failure instead of posting it a second time.
    """

    @pytest.mark.parametrize("reason,status", [
        ("busy", 503), ("internal", 503), ("timeout", 503), ("closed", 503),
        ("bad_response", 503), ("invalid_args", 400),
    ])
    def test_start_is_answered_with_the_failure(self, api_v3_client, service, reason, status):
        with patch(f"{CLIENT}.on_demand_start",
                   side_effect=control_client.ControlError(reason, "x", sent=True)):
            resp = api_v3_client.post(START_URL, json={"plugin_id": "weather"})
        assert resp.status_code == status
        body = resp.get_json()
        assert body["status"] == "error"
        assert body["data"]["transport"] == "socket"
        assert body["data"]["socket_error"] == reason
        assert _mailbox_writes(service["cache"]) == []
        assert not [call for call in service["calls"] if call[0] == "systemctl"]

    def test_stop_is_answered_with_the_failure(self, api_v3_client, service):
        with patch(f"{CLIENT}.on_demand_stop",
                   side_effect=control_client.ControlError("busy", "x", sent=True)):
            resp = api_v3_client.post(STOP_URL, json={})
        assert resp.status_code == 503
        assert _mailbox_writes(service["cache"]) == []

    def test_a_stop_with_stop_service_still_stops_the_service(self, api_v3_client, service):
        with patch(f"{CLIENT}.on_demand_stop",
                   side_effect=control_client.ControlError("timeout", "x", sent=True)), \
             patch("web_interface.blueprints.api_v3.display._stop_display_service",
                   return_value={"active": False}) as stop:
            resp = api_v3_client.post(STOP_URL, json={"stop_service": True})
        assert resp.status_code == 200
        data = resp.get_json()["data"]
        assert data["transport"] == "socket" and data["socket_error"] == "timeout"
        stop.assert_called_once()
        assert _mailbox_writes(service["cache"]) == []

    @pytest.mark.parametrize("reason", ["unknown_command", "unsupported_version"])
    def test_an_older_display_that_does_not_speak_it_gets_the_mailbox(
            self, api_v3_client, service, reason):
        # The upgrade case: new web interface, display still on an old build.
        with patch(f"{CLIENT}.on_demand_start",
                   side_effect=control_client.ControlError(reason, "x", sent=True)):
            data = api_v3_client.post(START_URL, json={"plugin_id": "weather"}).get_json()["data"]
        assert data["transport"] == "mailbox" and data["socket_error"] == reason
        assert len(_mailbox_writes(service["cache"])) == 1


@pytest.mark.skipif(not c.socket_supported(), reason="AF_UNIX sockets are Linux/macOS only")
class TestRealSocket:
    @pytest.fixture
    def live(self, monkeypatch):
        import shutil
        import tempfile
        from src.ipc.server import ControlServer
        d = tempfile.mkdtemp(prefix="lmipc-")
        path = os.path.join(d, "control.sock")
        server = ControlServer(path, status_provider=dict)
        assert server.start()
        monkeypatch.setenv(c.SOCKET_PATH_ENV, path)
        yield server
        server.close()
        shutil.rmtree(d, ignore_errors=True)

    def test_start_is_acked_and_queued(self, api_v3_client, service, live):
        data = api_v3_client.post(START_URL, json={
            "plugin_id": "weather", "duration": "30"}).get_json()["data"]
        assert data["transport"] == "socket"
        [cmd] = live.drain()
        payload = cmd.as_on_demand_request()
        assert payload["request_id"] == data["request_id"]
        assert payload["plugin_id"] == "weather" and payload["duration"] == 30.0
        assert _mailbox_writes(service["cache"]) == []

    def test_stop_is_acked_and_queued(self, api_v3_client, service, live):
        data = api_v3_client.post(STOP_URL, json={}).get_json()["data"]
        assert data["transport"] == "socket"
        assert [x.request_id for x in live.drain()] == [data["request_id"]]

    def test_a_display_that_went_away_falls_back(self, api_v3_client, service, live):
        live.close()
        data = api_v3_client.post(START_URL, json={"plugin_id": "weather"}).get_json()["data"]
        assert data["transport"] == "mailbox" and data["socket_error"] == "no_socket"
        assert len(_mailbox_writes(service["cache"])) == 1

    def test_a_full_queue_is_reported_not_mailed(self, api_v3_client, service, monkeypatch):
        import shutil
        import tempfile
        from src.ipc.server import ControlServer
        d = tempfile.mkdtemp(prefix="lmipc-")
        path = os.path.join(d, "control.sock")
        server = ControlServer(path, status_provider=dict, queue_size=1)
        assert server.start()
        monkeypatch.setenv(c.SOCKET_PATH_ENV, path)
        try:
            first = api_v3_client.post(START_URL, json={"plugin_id": "weather"})
            assert first.get_json()["data"]["transport"] == "socket"
            second = api_v3_client.post(START_URL, json={"plugin_id": "weather"})
            assert second.status_code == 503
            assert second.get_json()["data"]["socket_error"] == "busy"
            assert _mailbox_writes(service["cache"]) == []
        finally:
            server.close()
            shutil.rmtree(d, ignore_errors=True)
