"""/api/v3/errors/* report the display service's errors, not the web's.

The error aggregator is a per-process singleton, and only the display service
runs plugins, so only its aggregator ever records anything. The routes used to
read the web process's own aggregator and so always answered "no errors".

Here the two services are two ErrorAggregator instances and two CacheManagers
over one temporary directory -- the same arrangement as the real services,
which share /var/cache/ledmatrix. The display side publishes through
ErrorSnapshotPublisher.tick(); the web side is the real blueprint.
"""
import json
import os
import stat
import sys
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.cache_manager import CacheManager  # noqa: E402
from src import error_aggregator as errors  # noqa: E402
from src.error_aggregator import (  # noqa: E402
    ERROR_SNAPSHOT_KEY, ErrorAggregator,
    ErrorSnapshotPublisher,
)
from test._api_v3_test_helpers import api_v3_client, api_v3_module  # noqa: F401,E402


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def _fail(aggregator, plugin_id="p1", message="boom", exc=ValueError, **kwargs):
    try:
        raise exc(message)
    except Exception as e:  # noqa: BLE001 - recording is the point
        return aggregator.record_error(e, plugin_id=plugin_id, operation="update", **kwargs)


@pytest.fixture
def shared_cache(tmp_path, monkeypatch):
    """Two cache managers over one directory: the display's and the web's."""
    monkeypatch.setattr(CacheManager, "_get_writable_cache_dir", lambda self: str(tmp_path))
    display_cache, web_cache = CacheManager(), CacheManager()
    yield display_cache, web_cache, tmp_path
    display_cache.stop_cleanup_thread()
    web_cache.stop_cleanup_thread()


@pytest.fixture
def display(shared_cache):
    display_cache, _, _ = shared_cache
    aggregator = ErrorAggregator()
    clock = FakeClock()
    publisher = ErrorSnapshotPublisher(display_cache, aggregator=aggregator, clock=clock)
    return aggregator, publisher, clock


@pytest.fixture
def web(api_v3_module, api_v3_client, shared_cache):  # noqa: F811
    _, web_cache, _ = shared_cache
    api_v3_module.api_v3.cache_manager = web_cache
    return api_v3_client


def _summary(client):
    response = client.get("/api/v3/errors/summary")
    assert response.status_code == 200, response.get_data(as_text=True)
    return response.get_json()["data"]


# --- Display side -----------------------------------------------------------

class TestPublishing:
    def test_errors_reach_the_shared_cache(self, display, shared_cache):
        aggregator, publisher, _ = display
        _, web_cache, _ = shared_cache
        _fail(aggregator)
        assert publisher.tick() is True
        snapshot = web_cache.get(ERROR_SNAPSHOT_KEY, max_age=None, memory_ttl=0)
        assert snapshot["total_errors"] == 1
        assert snapshot["plugin_error_counts"] == {"p1": {"ValueError": 1}}
        assert datetime.fromisoformat(snapshot["generated_at"])

    def test_first_tick_publishes_even_with_no_errors(self, display, shared_cache):
        # Replaces whatever a previous run of the display service left behind.
        _, web_cache, _ = shared_cache
        web_cache.set(ERROR_SNAPSHOT_KEY, {"total_errors": 99})
        _, publisher, _ = display
        assert publisher.tick() is True
        assert web_cache.get(ERROR_SNAPSHOT_KEY, max_age=None, memory_ttl=0)["total_errors"] == 0

    def test_nothing_is_written_when_nothing_changed(self, display, shared_cache):
        aggregator, publisher, clock = display
        publisher.tick()
        publisher.cache_manager = MagicMock(wraps=publisher.cache_manager)
        clock.now += 3600
        assert publisher.tick() is False
        publisher.cache_manager.set.assert_not_called()

    def test_a_tight_failure_loop_is_throttled(self, display):
        aggregator, publisher, clock = display
        _fail(aggregator)
        assert publisher.tick() is True
        publisher.cache_manager = MagicMock(wraps=publisher.cache_manager)
        for _ in range(50):
            _fail(aggregator)
            clock.now += 0.1
            publisher.tick()
        publisher.cache_manager.set.assert_not_called()
        # Once the interval has passed, the backlog is published without a
        # new error having to arrive.
        clock.now += errors.SNAPSHOT_MIN_INTERVAL
        assert publisher.tick() is True
        assert publisher.cache_manager.set.call_count == 1
        assert publisher.cache_manager.set.call_args[0][1]["total_errors"] == 51

    def test_a_failing_cache_never_raises(self, display):
        aggregator, publisher, clock = display
        broken = MagicMock()
        broken.get.side_effect = OSError("read-only file system")
        broken.set.side_effect = OSError("read-only file system")
        publisher.cache_manager = broken
        _fail(aggregator)
        assert publisher.tick() is False
        broken.get.side_effect = None
        broken.get.return_value = None
        assert publisher.tick() is False  # set still fails
        # ...and retries at the throttled rate, not every tick.
        calls = broken.set.call_count
        clock.now += 1
        publisher.tick()
        assert broken.set.call_count == calls

    def test_an_unserialisable_context_does_not_break_the_snapshot(self, display, shared_cache):
        aggregator, publisher, _ = display
        _fail(aggregator, context={"obj": object(), "n": 3})
        assert publisher.tick() is True
        _, web_cache, _ = shared_cache
        snap = web_cache.get(ERROR_SNAPSHOT_KEY, max_age=None, memory_ttl=0)
        assert snap["recent_errors"][0]["context"]["n"] == 3

    def test_snapshot_stays_small(self, display):
        aggregator, _, _ = display
        for i in range(300):
            _fail(aggregator, plugin_id=f"plugin-{i % 5}", message="x" * 20000,
                  context={f"k{j}": "v" * 5000 for j in range(50)})
        snapshot = aggregator.build_snapshot()
        assert len(snapshot["recent_errors"]) == 20
        assert all(len(r["message"]) <= 300 for r in snapshot["recent_errors"])
        assert len(json.dumps(snapshot)) < 200_000

    def test_start_publishes_from_a_background_thread(self, shared_cache):
        display_cache, web_cache, _ = shared_cache
        aggregator = ErrorAggregator()
        _fail(aggregator)
        publisher = ErrorSnapshotPublisher(display_cache, aggregator=aggregator)
        try:
            publisher.start(interval=0.05)
            deadline = datetime.now() + timedelta(seconds=5)
            snapshot = None
            while snapshot is None and datetime.now() < deadline:
                snapshot = web_cache.get(ERROR_SNAPSHOT_KEY, max_age=None, memory_ttl=0)
            assert snapshot and snapshot["total_errors"] == 1
        finally:
            publisher.stop()

    def test_start_helper_never_raises(self, monkeypatch):
        monkeypatch.setattr(errors, "_snapshot_publisher", None)

        def explode(*a, **k):
            raise RuntimeError("no threads for you")

        monkeypatch.setattr(errors.ErrorSnapshotPublisher, "start", explode)
        assert errors.start_error_snapshot_publisher(MagicMock()) is None

    def test_display_controller_starts_the_publisher(self):
        # The display service is the only process that runs plugins, so it is
        # the one that must publish; the web process must not.
        # Read, not imported: display_controller needs rgbmatrix.
        root = Path(__file__).parent.parent
        controller = (root / "src" / "display_controller.py").read_text(encoding="utf-8")
        init = controller.split("    def __init__(self):", 1)[1].split("\n    def ", 1)[0]
        assert "start_error_snapshot_publisher(self.cache_manager)" in init
        web_dir = root / "web_interface"
        for source in web_dir.rglob("*.py"):
            assert "start_error_snapshot_publisher" not in source.read_text(encoding="utf-8"), source


class TestClearBefore:
    def test_keeps_later_errors_and_rebuilds_counts(self):
        aggregator = ErrorAggregator(pattern_threshold=2)
        for _ in range(3):
            _fail(aggregator, plugin_id="old")
        for record in aggregator._records:
            record.timestamp -= timedelta(hours=2)
        for pattern in aggregator._patterns.values():
            pattern.first_seen -= timedelta(hours=2)
        _fail(aggregator, plugin_id="new", exc=KeyError)
        cleared = aggregator.clear_before(datetime.now() - timedelta(hours=1))
        assert cleared == 3
        summary = aggregator.get_error_summary()
        assert summary["total_errors"] == 1
        assert summary["error_counts_by_type"] == {"KeyError": 1}
        assert summary["plugin_error_counts"] == {"new": {"KeyError": 1}}
        assert summary["active_patterns"] == {}

    def test_a_pattern_made_only_of_later_errors_survives(self):
        # A clear applied a few seconds late must not drop a pattern that
        # formed entirely after the cutoff.
        aggregator = ErrorAggregator(pattern_threshold=2)
        cutoff = datetime.now() - timedelta(seconds=1)
        for _ in range(3):
            _fail(aggregator)
        aggregator.clear_before(cutoff)
        assert list(aggregator.get_error_summary()["active_patterns"]) == ["ValueError"]

    def test_changes_the_version(self):
        aggregator = ErrorAggregator()
        v = aggregator.version
        aggregator.clear_before(datetime.now())
        assert aggregator.version != v


# --- Web side ---------------------------------------------------------------

class TestRoutes:
    def test_no_snapshot_yet(self, web):
        data = _summary(web)
        assert data["snapshot_available"] is False
        assert data["generated_at"] is None
        assert data["total_errors"] == 0
        assert data["recent_errors"] == [] and data["active_patterns"] == {}
        response = web.get("/api/v3/errors/summary")
        assert "not reported" in response.get_json()["message"]

    def test_summary_is_the_display_services(self, web, display):
        aggregator, publisher, _ = display
        for _ in range(5):
            _fail(aggregator, plugin_id="weather", message="HTTP 500")
        publisher.tick()
        data = _summary(web)
        assert data["snapshot_available"] is True
        assert data["clear_pending"] is False
        assert data["total_errors"] == 5
        assert data["plugin_error_counts"] == {"weather": {"ValueError": 5}}
        pattern = data["active_patterns"]["ValueError"]
        assert pattern["affected_plugins"] == ["weather"]
        assert pattern["sample_messages"] == ["HTTP 500"]
        # The documented shape, plus only the documented additions.
        assert set(data) == set(ErrorAggregator().get_error_summary()) | {
            "generated_at", "snapshot_available", "clear_pending"}

    def test_web_process_aggregator_is_not_what_is_reported(self, web, display, monkeypatch):
        # The original bug: the route read this process's own aggregator.
        local = ErrorAggregator()
        _fail(local, plugin_id="only-in-web")
        monkeypatch.setattr(errors, "_error_aggregator", local)
        aggregator, publisher, _ = display
        _fail(aggregator, plugin_id="from-display")
        publisher.tick()
        assert list(_summary(web)["plugin_error_counts"]) == ["from-display"]

    def test_plugin_slice(self, web, display):
        aggregator, publisher, _ = display
        for _ in range(6):
            _fail(aggregator, plugin_id="weather")
        _fail(aggregator, plugin_id="clock", exc=KeyError)
        publisher.tick()
        data = web.get("/api/v3/errors/plugin/weather").get_json()["data"]
        assert data["plugin_id"] == "weather"
        assert data["status"] == "unhealthy"
        assert data["total_errors"] == 6
        assert data["error_types"] == {"ValueError": 6}
        assert data["last_error"]["plugin_id"] == "weather"
        assert data["snapshot_available"] is True
        healthy = web.get("/api/v3/errors/plugin/never-failed").get_json()["data"]
        assert healthy["status"] == "healthy" and healthy["total_errors"] == 0
        assert healthy["last_error"] is None

    def test_credentials_in_exception_text_are_redacted(self, web, display):
        aggregator, publisher, _ = display
        for _ in range(5):
            _fail(aggregator, message="GET https://api.example.com/?api_key=SEKRIT123 failed")
        publisher.tick()
        body = web.get("/api/v3/errors/summary").get_data(as_text=True)
        body += web.get("/api/v3/errors/plugin/p1").get_data(as_text=True)
        assert "SEKRIT123" not in body
        assert "api_key=<redacted>" in body

    def test_snapshot_is_redacted_before_it_is_clipped(self, display):
        """The display redacts what it publishes, before clipping: keeping
        only a traceback's tail could cut ``api_key=`` off and leave the key
        itself, which the web side's redaction would then not recognise."""
        aggregator, _, _ = display
        record = _fail(aggregator, message="GET /?token=MSGSECRET failed")
        tail = errors._SNAPSHOT_TRACE_CHARS - 3
        filler = "x" * (tail - len("TRACESECRET "))
        record.stack_trace = "requests failed: api_key=TRACESECRET " + filler
        assert record.stack_trace[-tail:].startswith("TRACESECRET")  # marker falls outside
        record.context = {"url": "https://h/?password=CTXSECRET"}
        for _ in range(5):
            _fail(aggregator, message="GET /?token=SAMPLESECRET failed")
        published = json.dumps(aggregator.build_snapshot())
        for secret in ("MSGSECRET", "TRACESECRET", "CTXSECRET", "SAMPLESECRET"):
            assert secret not in published, secret


CLIENT = "web_interface.blueprints.api_v3.control_client"
RETIRED_CLEAR_KEY = "plugin_error_clear_request"


def _mailbox_file(shared_cache):
    _, _, directory = shared_cache
    return directory / f"{RETIRED_CLEAR_KEY}.json"


@pytest.fixture
def socket_up(display, monkeypatch):
    """The control socket, as the display serves it: errors_clear runs the
    display's own handler against its publisher."""
    from src.ipc import client as control_client
    from src.ipc.contract import ErrorsClearArgs
    _, publisher, _ = display
    monkeypatch.setattr(errors, "_snapshot_publisher", publisher)
    calls = []

    def errors_clear(request_id, cutoff, **kw):
        calls.append((request_id, cutoff))
        return errors.apply_error_clear(request_id, ErrorsClearArgs(cutoff=cutoff))

    monkeypatch.setattr(f"{CLIENT}.errors_clear", errors_clear)
    assert control_client.errors_clear is errors_clear
    return calls


class TestClear:
    """``errors.clear``: the display applies the clear before it answers."""

    def test_socket_clear_is_applied_before_the_answer(self, web, display, socket_up,
                                                       shared_cache):
        aggregator, publisher, _ = display
        for _ in range(3):
            _fail(aggregator)
        publisher.tick()
        response = web.post("/api/v3/errors/clear", json={"all": True})
        assert response.status_code == 200
        body = response.get_json()
        data = body["data"]
        assert data["transport"] == "socket" and data["applied"] is True
        assert data["clear_requested"] is True
        assert data["cleared_count"] == 3
        assert body["message"] == "Cleared all errors"
        [(request_id, _)] = socket_up
        assert data["request_id"] == request_id
        # Applied already: no display tick needed, nothing pending.
        assert aggregator.get_error_summary()["total_errors"] == 0
        summary = _summary(web)
        assert summary["total_errors"] == 0 and summary["clear_pending"] is False
        assert not _mailbox_file(shared_cache).exists()
        # Nothing left for a tick to do.
        assert publisher.tick() is False

    def test_errors_after_the_clear_are_kept(self, web, display, socket_up):
        aggregator, publisher, _ = display
        _fail(aggregator, plugin_id="before")
        for record in aggregator._records:
            record.timestamp -= timedelta(seconds=5)
        publisher.tick()
        web.post("/api/v3/errors/clear", json={"all": True})
        _fail(aggregator, plugin_id="after")
        publisher.min_interval = 0
        publisher.tick()
        data = _summary(web)
        assert data["plugin_error_counts"] == {"after": {"ValueError": 1}}
        assert data["clear_pending"] is False

    def test_age_based_clear(self, web, display, socket_up):
        aggregator, publisher, _ = display
        _fail(aggregator, plugin_id="old")
        _fail(aggregator, plugin_id="old")
        for record in aggregator._records:
            record.timestamp -= timedelta(hours=3)
        _fail(aggregator, plugin_id="new")
        publisher.tick()
        body = web.post("/api/v3/errors/clear", json={"max_age_hours": 1}).get_json()["data"]
        assert body["cleared_count"] == 2
        data = _summary(web)
        assert data["plugin_error_counts"] == {"new": {"ValueError": 1}}
        assert data["total_errors"] == 1

    def test_default_body_still_means_older_than_24_hours(self, web, display, socket_up):
        aggregator, publisher, _ = display
        _fail(aggregator)
        publisher.tick()
        response = web.post("/api/v3/errors/clear")
        assert response.status_code == 200
        assert response.get_json()["data"]["cleared_count"] == 0
        assert _summary(web)["total_errors"] == 1

    def test_validation_is_unchanged_but_all_skips_it(self, web, socket_up):
        assert web.post("/api/v3/errors/clear", json={"max_age_hours": 0}).status_code == 400
        assert web.post("/api/v3/errors/clear", json={"max_age_hours": 9000}).status_code == 400
        assert web.post("/api/v3/errors/clear",
                        json={"all": True, "max_age_hours": "junk"}).status_code == 200


class TestClearWithoutTheSocket:
    """Stage 5: no mailbox to fall back to. The route says why it failed and
    writes nothing; the errors stay as the display last reported them."""

    def _post(self, web, display, monkeypatch, error):
        monkeypatch.setattr(f"{CLIENT}.errors_clear", MagicMock(side_effect=error))
        aggregator, publisher, _ = display
        _fail(aggregator)
        publisher.tick()
        return web.post("/api/v3/errors/clear", json={"all": True})

    @pytest.mark.parametrize("reason", ["no_socket", "refused"])
    def test_a_stopped_display_is_an_error(self, web, display, shared_cache, monkeypatch,
                                           reason):
        from src.ipc import client as control_client
        response = self._post(web, display, monkeypatch,
                              control_client.ControlError(reason, sent=False))
        assert response.status_code == 503
        body = response.get_json()
        assert body["context"]["socket_error"] == reason
        assert "not running" in body["message"]
        assert not _mailbox_file(shared_cache).exists()
        # Nothing hides them: they are still the display's last report.
        summary = _summary(web)
        assert summary["total_errors"] == 1 and summary["clear_pending"] is False

    @pytest.mark.parametrize("reason", ["disabled", "unsupported"])
    def test_no_socket_here_is_an_error(self, web, display, shared_cache, monkeypatch, reason):
        from src.ipc import client as control_client
        response = self._post(web, display, monkeypatch,
                              control_client.ControlError(reason, sent=False))
        assert response.status_code == 503
        assert "not available" in response.get_json()["message"]
        assert not _mailbox_file(shared_cache).exists()

    def test_an_older_display_is_told_to_restart(self, web, display, shared_cache,
                                                 monkeypatch):
        from src.ipc import client as control_client
        response = self._post(web, display, monkeypatch,
                              control_client.ControlError("unknown_command", sent=True))
        assert response.status_code == 503
        assert "restart" in response.get_json()["message"]
        assert not _mailbox_file(shared_cache).exists()

    @pytest.mark.parametrize("reason", ["internal", "timeout", "busy", "invalid_args"])
    def test_a_display_that_had_it_and_failed_is_an_error(self, web, display, shared_cache,
                                                         monkeypatch, reason):
        from src.ipc import client as control_client
        response = self._post(web, display, monkeypatch,
                              control_client.ControlError(reason, sent=True))
        assert response.status_code == 503
        body = response.get_json()
        assert body["context"]["socket_error"] == reason
        assert body["message"] == "The display service did not apply the clear"
        assert not _mailbox_file(shared_cache).exists()

    def test_the_default_test_setup_has_no_socket(self, web, display, shared_cache):
        # conftest turns the socket off: the real client answers "disabled".
        aggregator, publisher, _ = display
        _fail(aggregator)
        publisher.tick()
        response = web.post("/api/v3/errors/clear", json={"all": True})
        assert response.status_code == 503
        assert response.get_json()["context"]["socket_error"] in ("disabled", "unsupported")


class TestPublisher:
    def test_a_tick_reads_no_clear_request(self, display, shared_cache):
        _, publisher, clock = display
        _, web_cache, directory = shared_cache
        # An old web interface's leftover request file is ignored.
        (directory / f"{RETIRED_CLEAR_KEY}.json").write_text(
            '{"timestamp": 1, "data": {"request_id": "old", "cutoff": 9e9}}')
        publisher.cache_manager = MagicMock(wraps=publisher.cache_manager)
        for _ in range(5):
            clock.now += 60
            publisher.tick()
        publisher.cache_manager.get.assert_not_called()
        snapshot = web_cache.get(ERROR_SNAPSHOT_KEY, max_age=None, memory_ttl=0)
        assert snapshot["applied_clear_id"] is None

    def test_clear_now_publishes_what_it_applied(self, display, shared_cache):
        aggregator, publisher, _ = display
        _, web_cache, _ = shared_cache
        _fail(aggregator)
        assert publisher.clear_now("sock-1", datetime.now().timestamp() + 1) == 1
        snapshot = web_cache.get(ERROR_SNAPSHOT_KEY, max_age=None, memory_ttl=0)
        assert snapshot["applied_clear_id"] == "sock-1"
        assert snapshot["total_errors"] == 0

    def test_the_handler_needs_a_running_publisher(self, monkeypatch):
        from src.ipc.contract import ErrorsClearArgs
        monkeypatch.setattr(errors, "_snapshot_publisher", None)
        with pytest.raises(RuntimeError):
            errors.apply_error_clear("x", ErrorsClearArgs(cutoff=1.0))


@pytest.mark.skipif(not hasattr(os, "fchmod") or os.name == "nt",
                    reason="POSIX file modes")
def test_the_snapshot_is_group_readable(web, display, shared_cache):
    aggregator, publisher, _ = display
    _, _, directory = shared_cache
    _fail(aggregator)
    publisher.tick()
    mode = stat.S_IMODE(os.stat(directory / f"{ERROR_SNAPSHOT_KEY}.json").st_mode)
    assert mode == 0o660, oct(mode)
