"""The web process's on-demand dispatcher (web_interface/on_demand_dispatch.py).

A start that finds no display listening -- the service was just started, or
is still loading its plugins -- is answered at once (202), and the
dispatcher's one worker thread sends it again until the display
acknowledges it or the wait runs out. These tests drive the worker with a
fake ``send`` and short waits:

* acknowledged: delivered once, and reported as such;
* nothing listening for the whole wait: ``start-timeout``;
* any other failure: reported at once, not retried;
* a newer start supersedes the pending one; a stop cancels it;
* the outcome is reported for a while, then forgotten.
"""

import threading
import time

import pytest

from src.ipc import client as control_client
from web_interface import on_demand_dispatch
from web_interface.on_demand_dispatch import OnDemandDispatcher


def _not_listening():
    return control_client.ControlError("no_socket", "x", sent=False)


def _payload(rid, plugin_id="weather"):
    return {"request_id": rid, "action": "start", "plugin_id": plugin_id,
            "mode": plugin_id, "duration": 30, "pinned": False}


class FakeSend:
    """Answers with ``outcomes`` in turn (an exception is raised, anything
    else acks); the last one repeats. Records the request ids it was sent."""

    def __init__(self, *outcomes):
        self.outcomes = list(outcomes) or ["ack"]
        self.sent = []
        self.lock = threading.Lock()

    def __call__(self, payload):
        with self.lock:
            self.sent.append(payload["request_id"])
            outcome = self.outcomes.pop(0) if len(self.outcomes) > 1 else self.outcomes[0]
        if isinstance(outcome, BaseException):
            raise outcome
        if callable(outcome):
            return outcome(payload)
        return {"accepted": True}


def _until(predicate, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(0.005)
    return False


def _settled(d):
    return _until(lambda: not d.pending() and d._thread is None)


@pytest.fixture
def make():
    made = []

    def build(send, wait_seconds=2.0, retry_interval=0.01):
        d = OnDemandDispatcher(send, wait_seconds=wait_seconds, retry_interval=retry_interval)
        made.append(d)
        return d
    yield build
    for d in made:
        d.cancel("teardown")


class TestDelivery:
    def test_an_acknowledged_start_is_delivered_once(self, make):
        send = FakeSend("ack")
        d = make(send)
        d.submit(_payload("r1"))
        assert _settled(d)
        assert send.sent == ["r1"]
        status = d.status()
        assert status["status"] == "delivered" and status["request_id"] == "r1"
        assert status["source"] == "web"

    def test_it_is_sent_again_until_the_display_listens(self, make):
        send = FakeSend(_not_listening(), _not_listening(), "ack")
        d = make(send)
        d.submit(_payload("r1"))
        assert _settled(d)
        assert send.sent == ["r1", "r1", "r1"]
        assert d.status()["status"] == "delivered"

    def test_while_it_waits_it_reports_starting(self, make):
        gate = threading.Event()

        def blocked(payload):
            gate.wait(5)
            raise _not_listening()

        send = FakeSend(blocked, "ack")
        d = make(send)
        d.submit(_payload("r1"))
        status = d.status()
        assert status["status"] == "starting" and status["active"] is False
        assert (status["plugin_id"], status["mode"], status["duration"]) == ("weather", "weather", 30)
        gate.set()
        assert _settled(d)
        assert d.status()["status"] == "delivered"


class TestGivingUp:
    def test_nothing_listening_for_the_whole_wait_is_a_start_timeout(self, make):
        send = FakeSend(_not_listening())
        d = make(send, wait_seconds=0.2)
        started = time.monotonic()
        d.submit(_payload("r1"))
        assert _settled(d)
        waited = time.monotonic() - started
        status = d.status()
        assert status["status"] == "error" and status["error"] == "start-timeout"
        assert 0.15 <= waited < 2.0
        assert len(send.sent) > 3   # it kept trying in between

    def test_a_start_can_carry_its_own_wait(self, make):
        # The route passes a shorter wait for a service that was already
        # running; the dispatcher's default must not override it.
        d = make(FakeSend(_not_listening()), wait_seconds=30.0)
        d.submit(_payload("r1"), wait_seconds=0.1)
        assert _until(lambda: not d.pending(), timeout=3.0), "it waited the default"
        assert d.status()["error"] == "start-timeout"

    @pytest.mark.parametrize("reason,sent", [("busy", True), ("unknown_command", True),
                                             ("timeout", True), ("forbidden", False)])
    def test_any_other_failure_is_reported_at_once(self, make, reason, sent):
        send = FakeSend(control_client.ControlError(reason, "x", sent=sent))
        d = make(send)
        d.submit(_payload("r1"))
        assert _settled(d)
        assert send.sent == ["r1"]
        status = d.status()
        assert status["status"] == "error" and status["error"] == reason

    def test_a_client_bug_is_internal(self, make):
        d = make(FakeSend(RuntimeError("boom")))
        d.submit(_payload("r1"))
        assert _settled(d)
        assert d.status()["error"] == "internal"


class TestOneAtATime:
    def test_a_newer_start_supersedes_the_pending_one(self, make):
        send = FakeSend(_not_listening())
        d = make(send)
        d.submit(_payload("old"))
        assert _until(lambda: "old" in send.sent)
        d.submit(_payload("new", plugin_id="clock"))
        assert d.status()["request_id"] == "new"
        n = len(send.sent)
        send.outcomes = ["ack"]
        assert _settled(d)
        assert send.sent[n:] and set(send.sent[n + 1:]) <= {"new"}
        assert send.sent[-1] == "new"
        status = d.status()
        assert status["status"] == "delivered" and status["plugin_id"] == "clock"

    def test_an_answer_for_a_superseded_start_is_not_reported(self, make):
        # The old start's send is in flight when the new one arrives; its
        # ack must not mark the new one delivered.
        in_flight, release = threading.Event(), threading.Event()

        def slow(payload):
            in_flight.set()
            release.wait(5)
            return {"accepted": True}

        send = FakeSend(slow, _not_listening())
        d = make(send, wait_seconds=0.3)
        d.submit(_payload("old"))
        assert in_flight.wait(5)
        d.submit(_payload("new"))
        release.set()
        assert _settled(d)
        status = d.status()
        assert status["request_id"] == "new"
        assert status["status"] == "error" and status["error"] == "start-timeout"

    def test_a_stop_cancels_the_pending_start(self, make):
        send = FakeSend(_not_listening())
        d = make(send)
        d.submit(_payload("r1"))
        assert _until(lambda: send.sent)
        assert d.cancel("requested-stop") == "r1"
        assert _settled(d)
        n = len(send.sent)
        time.sleep(0.05)
        assert len(send.sent) == n, "it kept sending a cancelled start"
        status = d.status()
        assert status["status"] == "idle" and status["last_event"] == "requested-stop"

    def test_a_cancel_waits_for_a_send_in_flight(self, make):
        # Whatever the caller sends after cancel() must land after the
        # cancelled start, not race it to the display.
        in_flight, release = threading.Event(), threading.Event()

        def slow(payload):
            in_flight.set()
            release.wait(5)
            raise _not_listening()

        send = FakeSend(slow)
        d = make(send)
        d.submit(_payload("old"))
        assert in_flight.wait(5)
        done = threading.Event()
        threading.Thread(target=lambda: (d.cancel("superseded"), done.set()),
                         daemon=True).start()
        assert not done.wait(0.1), "cancel returned while the old send was in flight"
        release.set()
        assert done.wait(5)
        assert _settled(d)
        assert send.sent == ["old"]

    def test_a_cancel_with_nothing_pending_does_nothing(self, make):
        d = make(FakeSend("ack"))
        assert d.cancel() is None
        assert d.status() is None

    def test_a_cancel_after_delivery_leaves_the_outcome(self, make):
        d = make(FakeSend("ack"))
        d.submit(_payload("r1"))
        assert _settled(d)
        assert d.cancel() is None
        assert d.status()["status"] == "delivered"


class TestOutcomeLifetime:
    def test_an_outcome_is_forgotten_after_a_while(self, make, monkeypatch):
        d = make(FakeSend("ack"))
        d.submit(_payload("r1"))
        assert _settled(d)
        assert d.status() is not None
        monkeypatch.setattr(on_demand_dispatch, "OUTCOME_SECONDS", 0.0)
        time.sleep(0.01)
        assert d.status() is None

    def test_a_pending_start_never_expires(self, make, monkeypatch):
        monkeypatch.setattr(on_demand_dispatch, "OUTCOME_SECONDS", 0.0)
        gate = threading.Event()
        d = make(FakeSend(lambda p: gate.wait(5) and {"accepted": True}))
        d.submit(_payload("r1"))
        time.sleep(0.01)
        assert d.status()["status"] == "starting"
        gate.set()
        assert _settled(d)


def test_the_process_has_one_dispatcher():
    on_demand_dispatch.reset_for_tests()
    try:
        assert on_demand_dispatch.current() is None
        first = on_demand_dispatch.get_dispatcher(FakeSend())
        assert on_demand_dispatch.get_dispatcher(FakeSend()) is first
        assert on_demand_dispatch.current() is first
    finally:
        on_demand_dispatch.reset_for_tests()
