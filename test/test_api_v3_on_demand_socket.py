"""POST /display/on-demand/start and /stop: the control socket is the only way.

The routes hand the request to the display over the control socket
(src/ipc) and get an acknowledgement. The file mailbox they used to fall
back to (``display_on_demand_request``) is gone (stage 5), so nothing is
ever written to the cache. When no display is listening (a stopped one, or
one still starting) the start route starts the service if asked to and
sends the request again once the socket is up; every other failure is
answered as what it is.

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
DISPLAY = "web_interface.blueprints.api_v3.display"


@pytest.fixture
def service(api_v3_module):
    """A running display service; records systemctl calls and cache writes."""
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


class FakeTime:
    """Stands in for the route's ``time`` module: sleep() moves the clock."""

    def __init__(self):
        self.now = 1000.0
        self.sleeps = 0

    def monotonic(self):
        return self.now

    def time(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps += 1
        self.now += seconds


@pytest.fixture
def clock():
    fake = FakeTime()
    with patch("web_interface.blueprints.api_v3.time", fake):
        yield fake


def _attempts(*outcomes, calls=None, clock=None):
    """A fake on_demand_start/stop that answers ``outcomes`` in turn (an
    exception is raised, anything else acks); the last one repeats."""
    outcomes = list(outcomes)
    seen = calls if calls is not None else []

    def attempt(request_id, *a, **kw):
        seen.append(clock.now if clock is not None else None)
        outcome = outcomes.pop(0) if len(outcomes) > 1 else outcomes[0]
        if isinstance(outcome, BaseException):
            raise outcome
        return _ack(request_id)
    return attempt


def _no_socket():
    return control_client.ControlError("no_socket", "x", sent=False)


class TestNoDisplayListening:
    """No socket to talk to: the display is stopped, or still starting."""

    def test_a_display_still_starting_gets_the_request_once_it_listens(
            self, api_v3_client, service, clock):
        calls = []
        with patch(f"{CLIENT}.on_demand_start",
                   side_effect=_attempts(_no_socket(), _no_socket(), "ack",
                                         calls=calls, clock=clock)):
            resp = api_v3_client.post(START_URL, json={"plugin_id": "weather"})
        assert resp.status_code == 200, resp.get_json()
        data = resp.get_json()["data"]
        assert data["transport"] == "socket" and "socket_error" not in data
        assert data["service"]["started"] is False
        assert len(calls) == 3
        assert not [c for c in service["calls"] if c[0] == "systemctl"]
        assert _mailbox_writes(service["cache"]) == []

    def test_a_stopped_display_is_started_and_then_sent_the_request(
            self, api_v3_client, service, clock):
        service["state"]["active"] = False
        calls = []
        with patch(f"{CLIENT}.on_demand_start",
                   side_effect=_attempts(*[_no_socket()] * 6, "ack",
                                         calls=calls, clock=clock)) as start:
            resp = api_v3_client.post(START_URL, json={"plugin_id": "weather",
                                                       "duration": 30})
        assert resp.status_code == 200, resp.get_json()
        data = resp.get_json()["data"]
        assert data["transport"] == "socket"
        assert data["service"]["started"] is True
        assert service["calls"] == [("systemctl", "start")]
        assert len(calls) == 7
        # The same request, the same id, every time.
        assert {call.args[0] for call in start.call_args_list} == {data["request_id"]}
        assert _mailbox_writes(service["cache"]) == []

    def test_a_stopped_display_without_start_service_is_an_error(
            self, api_v3_client, service, clock):
        service["state"]["active"] = False
        with patch(f"{CLIENT}.on_demand_start", side_effect=_no_socket()) as start:
            resp = api_v3_client.post(START_URL, json={"plugin_id": "weather",
                                                       "start_service": False})
        assert resp.status_code == 400
        body = resp.get_json()
        assert "not running" in body["message"]
        assert body["data"]["socket_error"] == "no_socket"
        assert start.call_count == 1 and clock.sleeps == 0
        assert service["calls"] == []

    def test_a_display_that_never_comes_up_is_given_up_on(self, api_v3_client, service, clock):
        from web_interface.blueprints.api_v3 import display
        service["state"]["active"] = False
        calls = []
        with patch(f"{CLIENT}.on_demand_start",
                   side_effect=_attempts(_no_socket(), calls=calls, clock=clock)):
            resp = api_v3_client.post(START_URL, json={"plugin_id": "weather"})
        assert resp.status_code == 503
        body = resp.get_json()
        assert body["data"]["socket_error"] == "no_socket"
        assert body["data"]["service"]["started"] is True
        assert "did not answer within 45 seconds" in body["message"]
        waited = calls[-1] - calls[1]
        assert display.ON_DEMAND_SOCKET_WAIT_SECONDS - 1 <= waited \
            <= display.ON_DEMAND_SOCKET_WAIT_SECONDS
        assert _mailbox_writes(service["cache"]) == []

    def test_a_running_service_without_a_socket_is_waited_for_less(
            self, api_v3_client, service, clock):
        from web_interface.blueprints.api_v3 import display
        calls = []
        with patch(f"{CLIENT}.on_demand_start",
                   side_effect=_attempts(_no_socket(), calls=calls, clock=clock)):
            resp = api_v3_client.post(START_URL, json={"plugin_id": "weather"})
        assert resp.status_code == 503
        assert f"within {int(display.ON_DEMAND_SOCKET_WAIT_RUNNING_SECONDS)} seconds" \
            in resp.get_json()["message"]
        assert calls[-1] - calls[0] <= display.ON_DEMAND_SOCKET_WAIT_RUNNING_SECONDS
        assert not [c for c in service["calls"] if c[0] == "systemctl"]

    def test_a_service_that_will_not_start_is_an_error(self, api_v3_client, service, clock):
        service["state"]["active"] = False
        with patch("web_interface.blueprints.api_v3._run_systemctl_command",
                   return_value={"returncode": 1, "stdout": "", "stderr": "nope"}), \
             patch(f"{CLIENT}.on_demand_start", side_effect=_no_socket()) as start:
            resp = api_v3_client.post(START_URL, json={"plugin_id": "weather"})
        assert resp.status_code == 500
        assert "Failed to start display service" in resp.get_json()["message"]
        assert start.call_count == 1

    def test_a_different_failure_while_waiting_ends_the_wait(self, api_v3_client, service,
                                                             clock):
        calls = []
        busy = control_client.ControlError("busy", "x", sent=True)
        with patch(f"{CLIENT}.on_demand_start",
                   side_effect=_attempts(_no_socket(), busy, calls=calls, clock=clock)):
            resp = api_v3_client.post(START_URL, json={"plugin_id": "weather"})
        assert resp.status_code == 503
        assert resp.get_json()["data"]["socket_error"] == "busy"
        assert len(calls) == 2

    def test_stop_with_no_display_running_is_an_error(self, api_v3_client, service, clock):
        service["state"]["active"] = False
        with patch(f"{CLIENT}.on_demand_stop", side_effect=_no_socket()) as stop:
            resp = api_v3_client.post(STOP_URL, json={})
        assert resp.status_code == 503
        body = resp.get_json()
        assert "not running" in body["message"]
        assert body["data"]["socket_error"] == "no_socket"
        assert stop.call_count == 1 and clock.sleeps == 0
        assert service["calls"] == []
        assert _mailbox_writes(service["cache"]) == []

    def test_stop_with_a_running_service_but_no_socket_is_an_error(
            self, api_v3_client, service, clock):
        with patch(f"{CLIENT}.on_demand_stop",
                   side_effect=control_client.ControlError("refused", "x")):
            resp = api_v3_client.post(STOP_URL, json={})
        assert resp.status_code == 503
        assert "still be starting" in resp.get_json()["message"]

    def test_stop_with_stop_service_stops_it_anyway(self, api_v3_client, service, clock):
        with patch(f"{CLIENT}.on_demand_stop", side_effect=_no_socket()), \
             patch(f"{DISPLAY}._stop_display_service",
                   return_value={"active": False}) as stop:
            resp = api_v3_client.post(STOP_URL, json={"stop_service": True})
        assert resp.status_code == 200
        assert resp.get_json()["data"]["socket_error"] == "no_socket"
        stop.assert_called_once()
        assert _mailbox_writes(service["cache"]) == []

    def test_the_socket_is_off_in_the_test_suite(self, api_v3_client, service):
        # conftest's _hermetic_control_socket: a suite run on a device must
        # not drive the live display. Nothing is retried or started for it.
        assert os.environ[c.SOCKET_PATH_ENV] == "off"
        service["state"]["active"] = False
        resp = api_v3_client.post(START_URL, json={"plugin_id": "weather"})
        assert resp.status_code == 503
        assert resp.get_json()["data"]["socket_error"] in ("disabled", "unsupported")
        assert service["calls"] == []


class TestNothingElseIsRetried:
    @pytest.mark.parametrize("reason,sent,status", [
        ("timeout", False, 503), ("busy", False, 503), ("forbidden", False, 503),
        ("invalid_request", False, 503), ("disabled", False, 503),
        ("unsupported", False, 503), ("unknown_command", True, 503),
        ("unsupported_version", True, 503),
    ])
    def test_answered_at_once_and_nothing_written(self, api_v3_client, service, clock,
                                                  reason, sent, status):
        service["state"]["active"] = False
        with patch(f"{CLIENT}.on_demand_start",
                   side_effect=control_client.ControlError(reason, "x", sent=sent)) as start:
            resp = api_v3_client.post(START_URL, json={"plugin_id": "weather"})
        assert resp.status_code == status
        body = resp.get_json()
        assert body["status"] == "error"
        assert body["data"]["socket_error"] == reason
        assert start.call_count == 1 and clock.sleeps == 0
        assert service["calls"] == []

    def test_a_client_bug_is_an_error(self, api_v3_client, service):
        with patch(f"{CLIENT}.on_demand_start", side_effect=RuntimeError("boom")):
            resp = api_v3_client.post(START_URL, json={"plugin_id": "weather"})
        assert resp.status_code == 503
        assert resp.get_json()["data"]["socket_error"] == "internal"
        assert _mailbox_writes(service["cache"]) == []

    def test_an_unknown_reason_is_reported_as_other(self, api_v3_client, service):
        # Only known codes are echoed back; anything else stays server-side.
        with patch(f"{CLIENT}.on_demand_start",
                   side_effect=control_client.ControlError("/run/secret/path", "x")):
            resp = api_v3_client.post(START_URL, json={"plugin_id": "weather"})
        assert resp.status_code == 503
        assert resp.get_json()["data"]["socket_error"] == "other"
        assert "/run/secret" not in resp.get_data(as_text=True)

    def test_every_display_error_code_is_reportable(self):
        from web_interface.blueprints.api_v3 import display
        codes = {v for k, v in vars(c.ErrorCode).items() if not k.startswith("_")}
        assert codes <= set(display._REPORTABLE_SOCKET_REASONS)


class TestTheDisplayHadIt:
    """Once the display has the request, its answer stands.

    A busy queue, a refusal or silence after the request was sent mean the
    display may have applied it, so the route reports the failure and does
    not send it again.
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

    def test_a_display_that_went_away_is_an_error(self, api_v3_client, service, live,
                                                  monkeypatch):
        monkeypatch.setattr(f"{DISPLAY}.ON_DEMAND_SOCKET_WAIT_RUNNING_SECONDS", 0.3)
        live.close()
        resp = api_v3_client.post(START_URL, json={"plugin_id": "weather"})
        assert resp.status_code == 503
        assert resp.get_json()["data"]["socket_error"] == "no_socket"
        assert _mailbox_writes(service["cache"]) == []

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
