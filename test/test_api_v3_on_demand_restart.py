"""POST /display/on-demand/start and /stop must not restart a running display.

The start route used to treat ``start_service`` (default True, and what both
the web UI and the MQTT bridge send) as "restart": with the service running it
ran ``systemctl stop``, slept 1.5s and started it again. Every on-demand or
"Preview on display" click therefore cold-restarted the display process --
every plugin reloaded, the panel blank for seconds -- to deliver a request the
running process polls for every ON_DEMAND_POLL_INTERVAL anyway (see
test_on_demand_mailbox.py and test_display_pending_changes.py for the display
side: the mailbox is read mid-dwell, mid-screen and mid-Vegas-iteration).

The restart did not buy anything either: a freshly started display restores
only the on-demand session it saved itself (``display_on_demand_config``), so
the new request reached it through the same mailbox, one cold start later.

This file previously pinned that restart path (it guarded a broken
``import _pkg.time`` inside it). The path is gone; these tests pin its
replacement: a running service is left alone, a stopped one is started (only
when start_service is set), and the request lands in the mailbox either way.

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

START_URL = "/api/v3/display/on-demand/start"
STOP_URL = "/api/v3/display/on-demand/stop"
MAILBOX = "display_on_demand_request"
DISPLAY = "web_interface.blueprints.api_v3.display"


@pytest.fixture
def service(api_v3_module):
    """A display service whose state the test sets; records systemctl calls.

    plugin_manager and config_manager are None so the route skips plugin
    resolution (not what is under test here). The cache is the blueprint's
    MagicMock cache_manager, so mailbox writes are visible as set() calls.
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

    with patch("web_interface.blueprints.api_v3._get_display_service_status",
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

    def test_the_request_is_posted_for_the_running_display(self, api_v3_client, service):
        response = api_v3_client.post(
            START_URL, json={"plugin_id": "weather", "mode": "weather_current",
                             "duration": 60, "pinned": True})
        data = response.get_json()["data"]
        writes = _mailbox_writes(service["cache"])
        assert len(writes) == 1
        assert writes[0]["action"] == "start"
        assert writes[0]["request_id"] == data["request_id"]
        assert writes[0]["plugin_id"] == "weather"
        assert writes[0]["mode"] == "weather_current"
        assert writes[0]["duration"] == 60
        assert writes[0]["pinned"] is True

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
        # Written before the start, so the new process finds it on its first poll.
        assert len(_mailbox_writes(service["cache"])) == 1

    def test_without_start_service_it_is_left_stopped(self, api_v3_client, service):
        service["state"]["active"] = False
        response = api_v3_client.post(
            START_URL, json={"plugin_id": "weather", "start_service": "false"})
        assert response.status_code == 400
        assert _systemctl_verbs(service["systemctl"]) == []

    def test_a_start_that_fails_is_reported(self, api_v3_client, service):
        service["state"]["active"] = False
        service["systemctl"].side_effect = lambda args: {
            "returncode": 1, "stdout": "", "stderr": "denied"}
        response = api_v3_client.post(START_URL, json={"plugin_id": "weather"})
        assert response.status_code == 500
        assert response.get_json()["status"] == "error"


class _Mailbox:
    """The CacheManager calls the routes make, over a dict."""

    def __init__(self):
        self.entries = {}

    def set(self, key, value, ttl=None):
        self.entries[key] = value

    def get(self, key, max_age=300, memory_ttl=None):
        return self.entries.get(key)

    def delete(self, key):
        self.entries.pop(key, None)


class TestARefusedStartLeavesNoRequestBehind:
    """A start the route answers with an error must not run later.

    The request was posted (to the mailbox, with the display stopped) before
    the route refused it, and the display reads the mailbox for an hour
    without looking at a request's age. So "Display service is not running"
    (start_service off) or "Failed to start display service" left the
    request waiting, and the next time the display started -- minutes later,
    by hand -- it ran that plugin, pinned if the request said so.

    A socket acknowledgement is the other side of it: the display answered,
    so it is running and has the request, whatever systemd says (a display
    run by hand or in the emulator has no active unit). That is a success,
    not "not running", and no unit is started beside it.
    """

    @pytest.fixture
    def mailbox(self, api_v3_module, service):
        box = _Mailbox()
        api_v3_module.api_v3.cache_manager = box
        service["state"]["active"] = False
        return box

    @pytest.mark.parametrize("body", [
        {"plugin_id": "weather", "start_service": False},
        {"plugin_id": "weather"},                           # start_service defaults on
    ])
    def test_a_socket_ack_is_a_success_whatever_systemd_says(
            self, api_v3_client, service, mailbox, body):
        with patch(f"{DISPLAY}.control_client.on_demand_start",
                   side_effect=lambda request_id, *a: {"accepted": True}):
            response = api_v3_client.post(START_URL, json=body)
        assert response.status_code == 200, response.get_json()
        assert response.get_json()["data"]["transport"] == "socket"
        assert MAILBOX not in mailbox.entries
        assert _systemctl_verbs(service["systemctl"]) == [], (
            "a unit was started beside a display that answered the socket")

    def test_without_start_service_the_request_is_taken_back(
            self, api_v3_client, service, mailbox):
        response = api_v3_client.post(START_URL, json={
            "plugin_id": "weather", "pinned": True, "start_service": False})
        assert response.status_code == 400
        assert response.get_json()["status"] == "error"
        assert MAILBOX not in mailbox.entries

    def test_a_start_that_fails_takes_its_request_back(self, api_v3_client, service, mailbox):
        service["systemctl"].side_effect = lambda args: {
            "returncode": 1, "stdout": "", "stderr": "denied"}
        response = api_v3_client.post(START_URL, json={"plugin_id": "weather"})
        assert response.status_code == 500
        assert MAILBOX not in mailbox.entries

    def test_a_newer_request_is_left_alone_on_the_400(self, api_v3_client, service, mailbox):
        newer = {"request_id": "someone-else", "action": "start", "plugin_id": "clock"}

        def stopped_and_another_post_lands(*args):
            mailbox.entries[MAILBOX] = newer
            return {"active": False}

        with patch(f"{DISPLAY}._get_display_service_status",
                   side_effect=stopped_and_another_post_lands):
            response = api_v3_client.post(START_URL, json={
                "plugin_id": "weather", "start_service": False})
        assert response.status_code == 400
        assert mailbox.entries[MAILBOX] is newer

    def test_a_newer_request_is_left_alone_on_the_500(self, api_v3_client, service, mailbox):
        newer = {"request_id": "someone-else", "action": "start", "plugin_id": "clock"}

        def start_fails_after_another_post(args):
            mailbox.entries[MAILBOX] = newer
            return {"returncode": 1, "stdout": "", "stderr": "denied"}

        service["systemctl"].side_effect = start_fails_after_another_post
        response = api_v3_client.post(START_URL, json={"plugin_id": "weather"})
        assert response.status_code == 500
        assert mailbox.entries[MAILBOX] is newer


class TestStop:
    def test_stop_posts_a_stop_request_and_leaves_the_service_running(
            self, api_v3_client, service):
        response = api_v3_client.post(STOP_URL, json={})
        assert response.status_code == 200, response.get_json()
        writes = _mailbox_writes(service["cache"])
        assert [w["action"] for w in writes] == ["stop"]
        service["stop_service"].assert_not_called()
        assert _systemctl_verbs(service["systemctl"]) == []

    def test_a_string_false_stop_service_does_not_stop_it(self, api_v3_client, service):
        # bool("false") is True: the flag was read raw and stopped the service.
        api_v3_client.post(STOP_URL, json={"stop_service": "false"})
        service["stop_service"].assert_not_called()

    def test_stop_service_true_still_stops_it(self, api_v3_client, service):
        api_v3_client.post(STOP_URL, json={"stop_service": True})
        service["stop_service"].assert_called_once()
