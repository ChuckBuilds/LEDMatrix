"""POST /display/on-demand/start and /stop must not restart a running display.

The start route used to treat ``start_service`` (default True, and what both
the web UI and the MQTT bridge send) as "restart": with the service running it
ran ``systemctl stop``, slept 1.5s and started it again. Every on-demand or
"Preview on display" click therefore cold-restarted the display process --
every plugin reloaded, the panel blank for seconds -- to deliver a request the
running process takes within a frame over its control socket anyway (see
test_display_pending_changes.py for the display side: commands land
mid-dwell, mid-screen and mid-Vegas-iteration).

The restart did not buy anything either: a freshly started display restores
only the on-demand session it saved itself (``display_on_demand_config``).

This file previously pinned that restart path (it guarded a broken
``import _pkg.time`` inside it). The path is gone; these tests pin its
replacement: a running service is left alone, a stopped one is started (only
when start_service is set), and the request goes over the control socket
either way -- to a stopped display once it has started and its socket is up.
Nothing is ever written to the cache: the file mailbox is gone (stage 5).

The service helpers are patched where they run. display.py binds
_get_display_service_status by value, while _ensure_display_service_running
(in the package __init__) looks it up in its own module, so both are patched;
_run_systemctl_command is the one place a systemctl command is issued.
"""

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from test._api_v3_test_helpers import api_v3_client, api_v3_module  # noqa: F401,E402

from src.ipc import client as control_client  # noqa: E402

START_URL = "/api/v3/display/on-demand/start"
STOP_URL = "/api/v3/display/on-demand/stop"
MAILBOX = "display_on_demand_request"
DISPLAY = "web_interface.blueprints.api_v3.display"


@pytest.fixture
def service(api_v3_module):
    """A display service whose state the test sets; records systemctl calls.

    plugin_manager and config_manager are None so the route skips plugin
    resolution (not what is under test here). The cache is the blueprint's
    MagicMock cache_manager, so a write would be visible as a set() call.

    The control socket answers while the service is active and is missing
    (``no_socket``) while it is not; ``sent`` records what it carried.
    """
    api_v3_module.api_v3.plugin_catalog = None
    api_v3_module.api_v3.config_manager = None
    state = {"active": True}

    def status():
        return {"active": state["active"]}

    def systemctl(args):
        if args[-2:] == ["start", "ledmatrix.service"]:
            state["active"] = True
        elif args[-2:] == ["stop", "ledmatrix.service"]:
            state["active"] = False
        return {"returncode": 0, "stdout": "", "stderr": ""}

    sent = []

    def socket(action):
        def call(request_id, *args, **kwargs):
            if not state["active"]:
                raise control_client.ControlError("no_socket", "x", sent=False)
            sent.append((action, request_id) + args)
            return {"accepted": True, "request_id": request_id}
        return call

    class Clock:
        now = 1000.0

        def monotonic(self):
            return self.now

        def time(self):
            return self.now

        def sleep(self, seconds):
            self.now += seconds

    with patch("web_interface.blueprints.api_v3.time", Clock()), \
         patch(f"{DISPLAY}.control_client.on_demand_start", side_effect=socket("start")), \
         patch(f"{DISPLAY}.control_client.on_demand_stop", side_effect=socket("stop")), \
         patch("web_interface.blueprints.api_v3._get_display_service_status",
               side_effect=status), \
         patch("web_interface.blueprints.api_v3.display._get_display_service_status",
               side_effect=status), \
         patch("web_interface.blueprints.api_v3._run_systemctl_command",
               side_effect=systemctl) as run_systemctl, \
         patch("web_interface.blueprints.api_v3.display._stop_display_service") as stop_service:
        yield {
            "state": state,
            "systemctl": run_systemctl,
            "stop_service": stop_service,
            "cache": api_v3_module.api_v3.cache_manager,
            "sent": sent,
        }


def _mailbox_writes(cache):
    return [c.args[1] for c in cache.set.call_args_list if c.args and c.args[0] == MAILBOX]


def _systemctl_verbs(run_systemctl):
    return [c.args[0][-2] for c in run_systemctl.call_args_list]


class TestStartWhileTheServiceIsRunning:
    @pytest.mark.parametrize("body", [
        {"plugin_id": "weather"},                           # "Preview on display", MQTT
        {"plugin_id": "weather", "start_service": True},    # on-demand modal, box ticked
        {"plugin_id": "weather", "start_service": "true"},
    ])
    def test_the_service_is_not_stopped_or_restarted(self, api_v3_client, service, body):
        response = api_v3_client.post(START_URL, json=body)
        assert response.status_code == 200, response.get_json()
        assert response.get_json()["status"] == "success"
        service["stop_service"].assert_not_called()
        assert _systemctl_verbs(service["systemctl"]) == [], (
            "a running display service was sent a systemctl command")

    def test_the_request_is_sent_to_the_running_display(self, api_v3_client, service):
        response = api_v3_client.post(
            START_URL, json={"plugin_id": "weather", "mode": "weather_current",
                             "duration": 60, "pinned": True})
        data = response.get_json()["data"]
        assert service["sent"] == [("start", data["request_id"], "weather",
                                    "weather_current", 60, True)]
        assert data["transport"] == "socket"
        assert _mailbox_writes(service["cache"]) == []

    def test_the_response_reports_the_service_was_not_started(self, api_v3_client, service):
        data = api_v3_client.post(START_URL, json={"plugin_id": "weather"}).get_json()["data"]
        assert data["service"]["active"] is True
        assert data["service"]["started"] is False

    def test_it_answers_without_the_old_restart_pause(self, api_v3_client, service):
        # The restart slept 1.5s; nothing here should sleep at all.
        with patch("time.sleep") as sleep:
            api_v3_client.post(START_URL, json={"plugin_id": "weather"})
        sleep.assert_not_called()


class TestStartWhileTheServiceIsStopped:
    def test_start_service_starts_it_once_and_never_stops_it(self, api_v3_client, service):
        service["state"]["active"] = False
        response = api_v3_client.post(START_URL, json={"plugin_id": "weather"})
        assert response.status_code == 200, response.get_json()
        assert _systemctl_verbs(service["systemctl"]) == ["start"]
        service["stop_service"].assert_not_called()
        # Sent once the started display's socket answered.
        assert [s[0] for s in service["sent"]] == ["start"]
        assert _mailbox_writes(service["cache"]) == []

    def test_without_start_service_it_is_left_stopped(self, api_v3_client, service):
        service["state"]["active"] = False
        response = api_v3_client.post(
            START_URL, json={"plugin_id": "weather", "start_service": "false"})
        assert response.status_code == 400
        assert _systemctl_verbs(service["systemctl"]) == []
        assert service["sent"] == []

    def test_a_start_that_fails_is_reported(self, api_v3_client, service):
        service["state"]["active"] = False
        service["systemctl"].side_effect = lambda args: {
            "returncode": 1, "stdout": "", "stderr": "denied"}
        response = api_v3_client.post(START_URL, json={"plugin_id": "weather"})
        assert response.status_code == 500
        assert response.get_json()["status"] == "error"


class TestARefusedStartLeavesNoRequestBehind:
    """A start the route answers with an error must not run later.

    With the mailbox, the request was posted before the route refused it, and
    a display started later ran it. Now nothing is written anywhere: the
    request only ever goes over the socket, to a display that answers.

    A socket acknowledgement is the other side of it: the display answered,
    so it is running and has the request, whatever systemd says (a display
    run by hand or in the emulator has no active unit). That is a success,
    not "not running", and no unit is started beside it.
    """

    @pytest.fixture
    def stopped(self, service):
        service["state"]["active"] = False
        return service

    @pytest.mark.parametrize("body", [
        {"plugin_id": "weather", "start_service": False},
        {"plugin_id": "weather"},                           # start_service defaults on
    ])
    def test_a_socket_ack_is_a_success_whatever_systemd_says(
            self, api_v3_client, stopped, body):
        with patch(f"{DISPLAY}.control_client.on_demand_start",
                   side_effect=lambda request_id, *a: {"accepted": True}):
            response = api_v3_client.post(START_URL, json=body)
        assert response.status_code == 200, response.get_json()
        assert response.get_json()["data"]["transport"] == "socket"
        assert _systemctl_verbs(stopped["systemctl"]) == [], (
            "a unit was started beside a display that answered the socket")

    def test_without_start_service_nothing_is_left_behind(self, api_v3_client, stopped):
        response = api_v3_client.post(START_URL, json={
            "plugin_id": "weather", "pinned": True, "start_service": False})
        assert response.status_code == 400
        assert response.get_json()["status"] == "error"
        assert stopped["cache"].set.call_count == 0
        assert stopped["sent"] == []

    def test_a_start_that_fails_leaves_nothing_behind(self, api_v3_client, stopped):
        stopped["systemctl"].side_effect = lambda args: {
            "returncode": 1, "stdout": "", "stderr": "denied"}
        response = api_v3_client.post(START_URL, json={"plugin_id": "weather"})
        assert response.status_code == 500
        assert stopped["cache"].set.call_count == 0
        assert stopped["sent"] == []


class TestStop:
    def test_stop_sends_a_stop_request_and_leaves_the_service_running(
            self, api_v3_client, service):
        response = api_v3_client.post(STOP_URL, json={})
        assert response.status_code == 200, response.get_json()
        assert [s[0] for s in service["sent"]] == ["stop"]
        assert _mailbox_writes(service["cache"]) == []
        service["stop_service"].assert_not_called()
        assert _systemctl_verbs(service["systemctl"]) == []

    def test_a_string_false_stop_service_does_not_stop_it(self, api_v3_client, service):
        # bool("false") is True: the flag was read raw and stopped the service.
        api_v3_client.post(STOP_URL, json={"stop_service": "false"})
        service["stop_service"].assert_not_called()

    def test_stop_service_true_still_stops_it(self, api_v3_client, service):
        api_v3_client.post(STOP_URL, json={"stop_service": True})
        service["stop_service"].assert_called_once()
