"""The core fetch service: merging, host budgets, conditional GET, counters.

No network: every request goes to a fake transport -- a real
``requests.Session`` subclass whose ``get`` answers from a handler -- so the
service sees real ``requests.Response`` objects, real header merging and real
adapters, and nothing leaves the machine. Clocks and sleeps are injected.

What callers already rely on (return values, exceptions, retries) is pinned
by the existing suites, which run unchanged through the service:
test_api_helper.py, test_background_data_service*.py,
test_background_fetch_dedupe.py, test_base_odds_manager.py,
test_odds_request_budget.py, test_espn_dates.py and test_sports_fetch.py.
"""

import importlib.util
import json
import threading
import time

import pytest
import requests
from requests.structures import CaseInsensitiveDict
from urllib3.util.retry import Retry

from src.common import fetch_service as fs
from src.common.fetch_service import (
    FetchService,
    FetchStatsPublisher,
    TokenBucket,
    current_plugin_id,
    plugin_scope,
    read_fetch_stats,
    register_plugin_directory,
    unregister_plugin_directory,
)


# --- fakes -----------------------------------------------------------------------

class FakeClock:
    def __init__(self, start=1000.0):
        self.t = start
        self.sleeps = []

    def now(self):
        return self.t

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.t += seconds

    def advance(self, seconds):
        self.t += seconds


def make_response(status=200, body=b'{"ok": 1}', headers=None, url="https://api.test/x"):
    response = requests.Response()
    response.status_code = status
    response._content = body
    response.headers = CaseInsensitiveDict(headers or {})
    response.url = url
    response.encoding = "utf-8"
    response.reason = "OK" if status < 400 else "Error"
    return response


class FakeSession(requests.Session):
    """A Session whose get() answers from ``handler(url, kwargs)``."""

    def __init__(self, handler=None, gate=None):
        super().__init__()
        self.handler = handler or (lambda url, kwargs: make_response(url=url))
        self.gate = gate
        self.calls = []
        self.started = threading.Event()
        self._calls_lock = threading.Lock()

    def get(self, url, **kwargs):
        with self._calls_lock:
            self.calls.append((url, kwargs))
        self.started.set()
        if self.gate is not None:
            assert self.gate.wait(5), "test gate never opened"
        return self.handler(url, kwargs)


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def service(clock):
    return FetchService({"rate_limits": {}}, clock=clock.now, sleep=clock.sleep)


@pytest.fixture
def global_service(monkeypatch, clock):
    """A fresh process-wide service, for code that calls fetch_get()."""
    svc = FetchService({"rate_limits": {}}, clock=clock.now, sleep=clock.sleep)
    monkeypatch.setattr(fs, "_service", svc)
    return svc


def _counters(svc, plugin=None, host=None):
    snap = svc.snapshot()
    if plugin is not None:
        return snap["plugins"].get(plugin, {})
    if host is not None:
        return snap["hosts"].get(host, {})
    return snap["totals"]


# --- the call itself is unchanged -----------------------------------------------------

class TestPassThrough:

    def test_session_get_sees_exactly_the_callers_arguments(self, service):
        session = FakeSession()
        response = service.get(session, "https://api.test/x", params={"a": 1},
                               headers={"X-Y": "z"}, timeout=7)
        assert session.calls == [("https://api.test/x",
                                  {"params": {"a": 1}, "headers": {"X-Y": "z"}, "timeout": 7})]
        assert response.json() == {"ok": 1}

    def test_no_kwargs_the_caller_did_not_pass(self, service):
        session = FakeSession()
        service.get(session, "https://api.test/x", timeout=5)
        assert session.calls[0][1] == {"timeout": 5}

    def test_the_transport_exception_reaches_the_caller_unchanged(self, service):
        boom = requests.ConnectionError("down")

        def handler(url, kwargs):
            raise boom

        with pytest.raises(requests.ConnectionError) as caught:
            service.get(FakeSession(handler), "https://api.test/x")
        assert caught.value is boom

    def test_an_http_error_response_is_returned_not_raised(self, service):
        session = FakeSession(lambda url, kw: make_response(503, b"busy"))
        response = service.get(session, "https://api.test/x")
        assert response.status_code == 503
        with pytest.raises(requests.HTTPError):
            response.raise_for_status()

    def test_disabled_is_a_plain_session_get(self, clock):
        svc = FetchService({"enabled": False, "rate_limits": {"api.test": {"per_second": 1, "burst": 1}}},
                           clock=clock.now, sleep=clock.sleep)
        session = FakeSession()
        for _ in range(3):
            svc.get(session, "https://api.test/x")
        assert len(session.calls) == 3
        assert clock.sleeps == []
        assert _counters(svc)["requests"] == 0

    def test_a_test_double_session_still_works(self, service):
        from unittest.mock import MagicMock
        session = MagicMock()
        session.get.return_value.json.return_value = {"a": 1}
        assert service.get(session, "https://api.test/x", timeout=3).json() == {"a": 1}
        session.get.assert_called_once_with("https://api.test/x", timeout=3)

    def test_session_none_uses_the_pooled_session_for_the_host(self, service, monkeypatch):
        seen = []
        monkeypatch.setattr(requests.Session, "get",
                            lambda self, url, **kw: seen.append(self) or make_response())
        service.get(None, "https://a.test/1")
        service.get(None, "https://a.test/2")
        service.get(None, "https://b.test/1")
        assert seen[0] is seen[1] is service.session_for("https://a.test/")
        assert seen[2] is not seen[0]


# --- single-flight ------------------------------------------------------------------------

def _wait_for_waiters(svc, count, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with svc._lock:
            flights = list(svc._inflight.values())
        if flights and flights[0].waiters >= count:
            return
        time.sleep(0.005)
    raise AssertionError(f"only {flights[0].waiters if flights else 0} of {count} callers joined")


class TestSingleFlight:

    def test_concurrent_identical_gets_go_out_once(self, service):
        gate = threading.Event()
        session = FakeSession(gate=gate)
        results = []

        def call():
            results.append(service.get(session, "https://api.test/x",
                                       params={"d": "1"}, timeout=5))

        threads = [threading.Thread(target=call) for _ in range(5)]
        threads[0].start()
        assert session.started.wait(5)
        for t in threads[1:]:
            t.start()
        _wait_for_waiters(service, 4)
        gate.set()
        for t in threads:
            t.join(5)

        assert len(session.calls) == 1
        assert len(results) == 5
        assert all(r.json() == {"ok": 1} for r in results)
        # Each caller gets its own Response object to mutate.
        assert len({id(r) for r in results}) == 5
        totals = _counters(service)
        assert totals["requests"] == 1
        assert totals["merged"] == 4

    def test_merged_callers_get_the_leaders_exception(self, service):
        gate = threading.Event()

        def handler(url, kwargs):
            raise requests.Timeout("slow")

        session = FakeSession(handler, gate=gate)
        errors = []

        def call():
            try:
                service.get(session, "https://api.test/x", timeout=5)
            except requests.Timeout as err:
                errors.append(err)

        threads = [threading.Thread(target=call) for _ in range(3)]
        threads[0].start()
        assert session.started.wait(5)
        for t in threads[1:]:
            t.start()
        _wait_for_waiters(service, 2)
        gate.set()
        for t in threads:
            t.join(5)

        assert len(session.calls) == 1
        assert len(errors) == 3
        totals = _counters(service)
        assert totals["errors"] == 1 and totals["merged"] == 2

    @pytest.mark.parametrize("second", [
        {"params": {"d": "2"}},                       # another query
        {"params": {"d": "1"}, "timeout": 9},         # another timeout
        {"params": {"d": "1"}, "headers": {"Accept": "text/plain"}},  # another representation
    ])
    def test_requests_that_could_answer_differently_are_not_merged(self, service, second):
        gate = threading.Event()
        session = FakeSession(gate=gate)
        first = threading.Thread(target=lambda: service.get(
            session, "https://api.test/x", params={"d": "1"}, timeout=5))
        first.start()
        assert session.started.wait(5)
        other = threading.Thread(target=lambda: service.get(
            session, "https://api.test/x", **{"timeout": 5, **second}))
        other.start()
        deadline = time.monotonic() + 5
        while len(session.calls) < 2 and time.monotonic() < deadline:
            time.sleep(0.005)
        gate.set()
        first.join(5)
        other.join(5)
        assert len(session.calls) == 2
        assert _counters(service)["merged"] == 0

    def test_different_retry_policies_are_not_merged(self, service):
        gate = threading.Event()
        retrying = FakeSession(gate=gate)
        retrying.mount("https://", requests.adapters.HTTPAdapter(max_retries=Retry(total=3)))
        plain = FakeSession(gate=gate)
        a = threading.Thread(target=lambda: service.get(retrying, "https://api.test/x"))
        a.start()
        assert retrying.started.wait(5)
        b = threading.Thread(target=lambda: service.get(plain, "https://api.test/x"))
        b.start()
        assert plain.started.wait(5)
        gate.set()
        a.join(5)
        b.join(5)
        assert len(retrying.calls) == len(plain.calls) == 1

    def test_sessions_with_the_same_policy_and_headers_share_a_flight(self, service):
        gate = threading.Event()
        one, two = FakeSession(gate=gate), FakeSession(gate=gate)
        a = threading.Thread(target=lambda: service.get(one, "https://api.test/x", timeout=5))
        a.start()
        assert one.started.wait(5)
        b = threading.Thread(target=lambda: service.get(two, "https://api.test/x", timeout=5))
        b.start()
        _wait_for_waiters(service, 1)
        gate.set()
        a.join(5)
        b.join(5)
        assert len(one.calls) == 1 and two.calls == []

    def test_a_session_with_cookies_only_merges_with_itself(self, service):
        gate = threading.Event()
        cookied, plain = FakeSession(gate=gate), FakeSession(gate=gate)
        cookied.cookies.set("sid", "secret")
        a = threading.Thread(target=lambda: service.get(cookied, "https://api.test/x"))
        a.start()
        assert cookied.started.wait(5)
        b = threading.Thread(target=lambda: service.get(plain, "https://api.test/x"))
        b.start()
        assert plain.started.wait(5)
        gate.set()
        a.join(5)
        b.join(5)
        assert len(cookied.calls) == len(plain.calls) == 1

    def test_sequential_identical_gets_each_go_out(self, service):
        session = FakeSession()
        service.get(session, "https://api.test/x")
        service.get(session, "https://api.test/x")
        assert len(session.calls) == 2

    def test_streamed_requests_are_never_merged(self, service):
        gate = threading.Event()
        session = FakeSession(gate=gate)
        a = threading.Thread(target=lambda: service.get(session, "https://api.test/x", stream=True))
        a.start()
        assert session.started.wait(5)
        b = threading.Thread(target=lambda: service.get(session, "https://api.test/x", stream=True))
        b.start()
        deadline = time.monotonic() + 5
        while len(session.calls) < 2 and time.monotonic() < deadline:
            time.sleep(0.005)
        gate.set()
        a.join(5)
        b.join(5)
        assert len(session.calls) == 2


# --- token buckets ---------------------------------------------------------------------------

class TestTokenBucket:

    def test_burst_then_one_token_per_interval(self, clock):
        bucket = TokenBucket(per_second=2, burst=3, clock=clock.now)
        assert [bucket.reserve(10)[0] for _ in range(3)] == [0.0, 0.0, 0.0]
        assert bucket.reserve(10) == (0.5, False)
        assert bucket.reserve(10) == (1.0, False)

    def test_tokens_refill_with_time_up_to_the_burst(self, clock):
        bucket = TokenBucket(per_second=2, burst=3, clock=clock.now)
        for _ in range(3):
            bucket.reserve(10)
        clock.advance(100)
        assert [bucket.reserve(10)[0] for _ in range(3)] == [0.0, 0.0, 0.0]
        assert bucket.reserve(10)[0] == 0.5

    def test_a_wait_is_capped_at_max_wait(self, clock):
        bucket = TokenBucket(per_second=1, burst=1, clock=clock.now)
        bucket.reserve(0.5)
        assert bucket.reserve(0.5) == (0.5, True)
        # The debt never runs further than max_wait either.
        assert bucket.reserve(0.5) == (0.5, True)
        clock.advance(10)
        assert bucket.reserve(0.5) == (0.0, False)


class TestHostBudgets:

    def test_requests_past_the_budget_wait(self, clock):
        svc = FetchService({"rate_limits": {"api.test": {"per_second": 1, "burst": 2}},
                            "max_wait_seconds": 10}, clock=clock.now, sleep=clock.sleep)
        session = FakeSession()
        for _ in range(4):
            svc.get(session, "https://api.test/x")
        assert clock.sleeps == [1.0, 1.0]
        host = _counters(svc, host="api.test")
        assert host["throttled"] == 2 and host["wait_seconds"] == 2.0
        assert host["requests"] == 4

    def test_other_hosts_are_not_throttled(self, clock):
        svc = FetchService({"rate_limits": {"api.test": {"per_second": 1, "burst": 1}}},
                           clock=clock.now, sleep=clock.sleep)
        session = FakeSession()
        for _ in range(5):
            svc.get(session, "https://elsewhere.test/x")
        assert clock.sleeps == []

    def test_each_host_has_its_own_bucket(self, clock):
        svc = FetchService({"rate_limits": {"*.espn.com": {"per_second": 1, "burst": 1}},
                            "max_wait_seconds": 10}, clock=clock.now, sleep=clock.sleep)
        session = FakeSession()
        svc.get(session, "https://site.api.espn.com/a")
        svc.get(session, "https://sports.core.api.espn.com/a")
        assert clock.sleeps == []
        svc.get(session, "https://site.api.espn.com/a")
        assert clock.sleeps == [1.0]

    def test_wildcard_matches_the_bare_domain_and_subdomains_only(self, clock):
        svc = FetchService({"rate_limits": {"*.espn.com": {"per_second": 5, "burst": 9}}},
                           clock=clock.now, sleep=clock.sleep)
        assert svc._limit_for("espn.com") == (5.0, 9.0)
        assert svc._limit_for("site.api.espn.com") == (5.0, 9.0)
        assert svc._limit_for("notespn.com") is None

    def test_the_default_budget_covers_espn_and_a_cold_season_burst(self, clock):
        svc = FetchService(clock=clock.now, sleep=clock.sleep)
        session = FakeSession()
        for _ in range(200):
            svc.get(session, "https://site.api.espn.com/x")
        assert clock.sleeps == []
        svc.get(session, "https://site.api.espn.com/x")
        assert clock.sleeps == [pytest.approx(0.05)]
        svc.get(session, "https://api.example.org/x")
        assert len(clock.sleeps) == 1

    def test_zero_per_second_removes_a_budget(self, clock):
        svc = FetchService({"rate_limits": {"*.espn.com": {"per_second": 0, "burst": 1}}},
                           clock=clock.now, sleep=clock.sleep)
        session = FakeSession()
        for _ in range(5):
            svc.get(session, "https://site.api.espn.com/x")
        assert clock.sleeps == []

    def test_a_merged_caller_spends_no_token(self, clock):
        svc = FetchService({"rate_limits": {"api.test": {"per_second": 1, "burst": 1}},
                            "max_wait_seconds": 10}, clock=clock.now, sleep=clock.sleep)
        gate = threading.Event()
        session = FakeSession(gate=gate)
        a = threading.Thread(target=lambda: svc.get(session, "https://api.test/x"))
        a.start()
        assert session.started.wait(5)
        b = threading.Thread(target=lambda: svc.get(session, "https://api.test/x"))
        b.start()
        _wait_for_waiters(svc, 1)
        gate.set()
        a.join(5)
        b.join(5)
        assert clock.sleeps == []


# --- conditional GET ----------------------------------------------------------------------------

class Versioned:
    """A server with one resource and an ETag, honouring If-None-Match."""

    def __init__(self, validator="etag"):
        self.version = 1
        self.validator = validator
        self.seen = []

    def body(self):
        return json.dumps({"version": self.version}).encode()

    def tag(self):
        if self.validator == "etag":
            return {"ETag": f'"v{self.version}"'}
        return {"Last-Modified": f"Thu, 01 Oct 2026 00:00:0{self.version} GMT"}

    def __call__(self, url, kwargs):
        headers = CaseInsensitiveDict(kwargs.get("headers") or {})
        self.seen.append(dict(headers))
        current = self.tag()
        if (headers.get("If-None-Match") == current.get("ETag") and "ETag" in current) or \
                (headers.get("If-Modified-Since") == current.get("Last-Modified")
                 and "Last-Modified" in current):
            return make_response(304, b"", headers={**current, "Date": "now"}, url=url)
        return make_response(200, self.body(),
                             headers={**current, "Content-Type": "application/json"}, url=url)


class TestConditionalGet:

    @pytest.mark.parametrize("validator,header", [("etag", "If-None-Match"),
                                                  ("last-modified", "If-Modified-Since")])
    def test_a_304_returns_the_stored_body_as_a_200(self, service, validator, header):
        server = Versioned(validator)
        session = FakeSession(server)
        first = service.get(session, "https://api.test/x", timeout=5)
        second = service.get(session, "https://api.test/x", timeout=5)

        assert header not in server.seen[0]
        assert header in server.seen[1]
        assert second.status_code == 200
        assert second.json() == first.json() == {"version": 1}
        assert second.headers["Content-Type"] == "application/json"
        second.raise_for_status()
        totals = _counters(service)
        assert totals["requests"] == 2
        assert totals["not_modified"] == 1
        assert totals["bytes"] == len(server.body())  # the 304 carried none

    def test_a_changed_resource_is_fetched_and_stored_again(self, service):
        server = Versioned()
        session = FakeSession(server)
        service.get(session, "https://api.test/x")
        server.version = 2
        changed = service.get(session, "https://api.test/x")
        assert changed.json() == {"version": 2}
        again = service.get(session, "https://api.test/x")
        assert again.json() == {"version": 2}
        assert server.seen[2]["If-None-Match"] == '"v2"'

    def test_no_validators_no_conditional_request(self, service):
        session = FakeSession()  # answers 200 with no ETag/Last-Modified
        service.get(session, "https://api.test/x", headers={"A": "1"})
        service.get(session, "https://api.test/x", headers={"A": "1"})
        assert session.calls[1][1] == {"headers": {"A": "1"}}
        assert service.snapshot()["validators"]["entries"] == 0

    def test_a_200_without_validators_drops_the_stored_one(self, service):
        server = Versioned()
        session = FakeSession(server)
        service.get(session, "https://api.test/x")
        session.handler = lambda url, kw: make_response(200, b'{"new": 1}', url=url)
        service.get(session, "https://api.test/x")
        assert service.snapshot()["validators"]["entries"] == 0

    def test_a_callers_own_conditional_request_is_left_alone(self, service):
        server = Versioned()
        session = FakeSession(server)
        service.get(session, "https://api.test/x")
        raw = service.get(session, "https://api.test/x", headers={"If-None-Match": '"v1"'})
        assert raw.status_code == 304

    def test_validators_are_per_representation(self, service):
        server = Versioned()
        session = FakeSession(server)
        service.get(session, "https://api.test/x", params={"d": "1"})
        service.get(session, "https://api.test/x", params={"d": "2"})
        assert "If-None-Match" not in server.seen[1]

    def test_a_body_too_big_for_the_store_is_not_kept(self, clock):
        svc = FetchService({"rate_limits": {}, "validator_store": {"max_entry_bytes": 4}},
                           clock=clock.now, sleep=clock.sleep)
        server = Versioned()
        session = FakeSession(server)
        svc.get(session, "https://api.test/x")
        svc.get(session, "https://api.test/x")
        assert "If-None-Match" not in server.seen[1]

    def test_the_store_evicts_least_recently_used_past_its_budget(self, clock):
        svc = FetchService({"rate_limits": {}, "validator_store": {"max_entries": 2}},
                           clock=clock.now, sleep=clock.sleep)
        session = FakeSession(Versioned())
        for path in ("a", "b", "c"):
            svc.get(session, f"https://api.test/{path}")
        assert svc.snapshot()["validators"]["entries"] == 2

    def test_off_switch(self, clock):
        svc = FetchService({"rate_limits": {}, "conditional_get": False},
                           clock=clock.now, sleep=clock.sleep)
        server = Versioned()
        session = FakeSession(server)
        svc.get(session, "https://api.test/x")
        svc.get(session, "https://api.test/x")
        assert "If-None-Match" not in server.seen[1]


# --- counters and caller identity ----------------------------------------------------------------

class TestCounters:

    def test_per_plugin_and_per_host(self, service):
        session = FakeSession()
        with plugin_scope("weather"):
            service.get(session, "https://api.weather.test/now")
            service.get(session, "https://api.weather.test/later")
        service.get(session, "https://site.api.espn.com/x")
        snap = service.snapshot()
        assert snap["plugins"]["weather"]["requests"] == 2
        assert snap["plugins"]["weather"]["hosts"] == {"api.weather.test": 2}
        assert snap["plugins"]["core"]["requests"] == 1
        assert snap["hosts"]["api.weather.test"]["requests"] == 2
        assert snap["hosts"]["site.api.espn.com"]["requests"] == 1
        assert snap["totals"]["bytes"] == 3 * len(b'{"ok": 1}')

    def test_errors_and_http_errors(self, service):
        def handler(url, kwargs):
            if url.endswith("/down"):
                raise requests.ConnectionError("down")
            return make_response(404, b"nope", url=url)

        session = FakeSession(handler)
        with pytest.raises(requests.ConnectionError):
            service.get(session, "https://api.test/down")
        service.get(session, "https://api.test/missing")
        totals = _counters(service)
        assert totals["requests"] == 2
        assert totals["errors"] == 1
        assert totals["http_errors"] == 1

    def test_every_change_bumps_the_change_count(self, service):
        before = service.change_count
        service.get(FakeSession(), "https://api.test/x")
        assert service.change_count > before

    def test_post_is_counted_and_never_merged(self, service):
        class PostSession(FakeSession):
            def post(self, url, **kwargs):
                self.calls.append((url, kwargs))
                return make_response(201, b"{}", url=url)

        session = PostSession()
        with plugin_scope("poster"):
            response = service.post(session, "https://api.test/x", json={"a": 1})
        assert response.status_code == 201
        assert session.calls == [("https://api.test/x", {"json": {"a": 1}})]
        assert _counters(service, plugin="poster")["requests"] == 1


class TestCallerIdentity:

    def test_scope_wins_and_nests(self):
        assert current_plugin_id() is None
        with plugin_scope("outer"):
            assert current_plugin_id() == "outer"
            with plugin_scope("inner"):
                assert current_plugin_id() == "inner"
            with plugin_scope(None):
                assert current_plugin_id() == "outer"
        assert current_plugin_id() is None

    def test_a_plugins_own_thread_is_named_by_its_source_directory(self, tmp_path, service):
        plugin_dir = tmp_path / "my-plugin"
        plugin_dir.mkdir()
        (plugin_dir / "fetcher.py").write_text(
            "import threading\n"
            "def fetch_in_thread(service, session, url):\n"
            "    t = threading.Thread(target=lambda: service.get(session, url))\n"
            "    t.start()\n"
            "    t.join(5)\n",
            encoding="utf-8")
        spec = importlib.util.spec_from_file_location("_fs_test_fetcher", plugin_dir / "fetcher.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        register_plugin_directory("my-plugin", plugin_dir)
        try:
            module.fetch_in_thread(service, FakeSession(), "https://api.test/x")
        finally:
            unregister_plugin_directory("my-plugin")
        assert _counters(service, plugin="my-plugin")["requests"] == 1
        assert "core" not in service.snapshot()["plugins"]

    def test_the_executor_scopes_a_plugin_operation(self):
        from src.plugin_system.plugin_executor import PluginExecutor
        seen = PluginExecutor().execute_with_timeout(current_plugin_id, plugin_id="clock")
        assert seen == "clock"

    def test_background_fetches_count_against_the_submitter(self, global_service):
        from unittest.mock import MagicMock
        from src.background_data_service import BackgroundDataService

        cache = MagicMock()
        cache.get.return_value = None
        bds = BackgroundDataService(cache, max_workers=1, request_timeout=5)
        bds.session = FakeSession(lambda url, kw: make_response(body=b'{"events": []}', url=url))
        try:
            with plugin_scope("football-scoreboard"):
                request_id = bds.submit_fetch_request(
                    "nfl", 2026, "https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard",
                    cache_key="fs_test_nfl", params={"dates": "2026"})
            deadline = time.monotonic() + 5
            while not bds.is_request_complete(request_id) and time.monotonic() < deadline:
                time.sleep(0.01)
            assert bds.get_result(request_id).success
        finally:
            bds.shutdown(wait=True)
        assert _counters(global_service, plugin="football-scoreboard")["requests"] == 1

    def test_espn_chunks_on_worker_threads_count_against_the_caller(self, global_service):
        from src.common.espn_dates import espn_request_chunks, fetch_espn_date_chunks, parse_espn_date_range

        session = FakeSession(lambda url, kw: make_response(body=b'{"events": []}', url=url))
        dates = "20260801-20261015"
        with plugin_scope("baseball-scoreboard"):
            fetch_espn_date_chunks(session, "https://site.api.espn.com/s/scoreboard",
                                   params={"dates": dates})
        chunks = len(espn_request_chunks(*parse_espn_date_range(dates)))
        assert chunks > 1
        assert len(session.calls) == chunks
        assert _counters(global_service, plugin="baseball-scoreboard")["requests"] == chunks
        assert "core" not in global_service.snapshot()["plugins"]

    def test_api_helper_goes_through_the_service(self, global_service):
        from src.common.api_helper import APIHelper

        helper = APIHelper()
        helper.set_rate_limit(0)
        helper.session = FakeSession(lambda url, kw: make_response(body=b'{"a": 1}', url=url))
        with plugin_scope("nfl-draft"):
            assert helper.get("https://api.test/x") == {"a": 1}
        assert _counters(global_service, plugin="nfl-draft")["requests"] == 1

    def test_odds_go_through_the_service(self, global_service):
        from unittest.mock import MagicMock
        from src.base_odds_manager import BaseOddsManager

        cache = MagicMock()
        cache.get_with_auto_strategy.return_value = None
        manager = BaseOddsManager(cache)
        manager.session = FakeSession(
            lambda url, kw: make_response(body=b'{"count": 0, "items": []}', url=url))
        with plugin_scope("odds-ticker"):
            assert manager.get_odds("football", "nfl", "401") is None
        assert manager.session.calls[0][1] == {"timeout": manager.request_timeout}
        assert _counters(global_service, plugin="odds-ticker")["requests"] == 1


# --- pooling -------------------------------------------------------------------------------------------

class TestConnectionPool:

    def test_core_sessions_with_one_policy_share_one_adapter(self, global_service):
        from unittest.mock import MagicMock
        from src.background_data_service import BackgroundDataService
        from src.base_odds_manager import BaseOddsManager

        odds_a = BaseOddsManager(MagicMock()).session.get_adapter("https://x.test")
        odds_b = BaseOddsManager(MagicMock()).session.get_adapter("https://x.test")
        bds = BackgroundDataService(MagicMock(), max_workers=1)
        try:
            assert odds_a is odds_b is bds.session.get_adapter("https://x.test")
            assert odds_a.max_retries.total == 0
        finally:
            bds.shutdown(wait=False)

    def test_a_different_retry_policy_gets_its_own_adapter(self, global_service):
        from src.common.api_helper import APIHelper
        helper_adapter = APIHelper().session.get_adapter("https://x.test")
        assert helper_adapter is APIHelper().session.get_adapter("https://x.test")
        assert helper_adapter is not global_service.shared_adapter(0)
        assert helper_adapter.max_retries.total == 3
        assert helper_adapter.max_retries.status_forcelist == [429, 500, 502, 503, 504]
        assert APIHelper(max_retries=1).session.get_adapter("https://x.test") is not helper_adapter

    def test_the_pooled_session_keeps_no_cookies(self, service):
        import http.client
        import io
        from types import SimpleNamespace
        from requests.cookies import extract_cookies_to_jar

        def offer_cookie(session):
            msg = http.client.parse_headers(io.BytesIO(b"Set-Cookie: sid=1; Path=/" + b"\r\n" * 2))
            raw = SimpleNamespace(_original_response=SimpleNamespace(msg=msg))
            request = requests.Request("GET", "https://api.test/").prepare()
            extract_cookies_to_jar(session.cookies, request, raw)
            return len(session.cookies)

        assert offer_cookie(requests.Session()) == 1        # what a private Session does
        assert offer_cookie(service.session_for("https://api.test/")) == 0


# --- configuration ----------------------------------------------------------------------------------

class TestConfigure:

    def test_reapplying_the_same_section_keeps_the_validator_store(self, clock):
        config = {"rate_limits": {}}
        svc = FetchService(config, clock=clock.now, sleep=clock.sleep)
        svc.get(FakeSession(Versioned()), "https://api.test/x")
        svc.configure(dict(config))
        assert svc.snapshot()["validators"]["entries"] == 1
        svc.configure({"rate_limits": {"api.test": {"per_second": 1}}})
        assert svc.snapshot()["validators"]["entries"] == 0

    @pytest.mark.parametrize("bad", [
        "nonsense",
        {"rate_limits": "nonsense"},
        {"rate_limits": {"api.test": "fast"}},
        {"rate_limits": {"api.test": {"per_second": -1}}},
        {"max_wait_seconds": "long"},
    ])
    def test_bad_values_fall_back_without_raising(self, clock, bad):
        svc = FetchService(bad, clock=clock.now, sleep=clock.sleep)
        assert svc.describe_config()["max_wait_seconds"] == 2.0
        svc.get(FakeSession(), "https://api.test/x")

    def test_the_template_section_is_what_the_code_defaults_to(self):
        import os
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, "config", "config.template.json"), encoding="utf-8") as fh:
            template = json.load(fh)["fetch_service"]
        for key, value in template.items():
            assert fs.DEFAULT_CONFIG[key] == value


# --- publishing and reading -----------------------------------------------------------------------------

class SharedCache:
    def __init__(self):
        self.entries = {}
        self.writes = 0

    def get(self, key, max_age=None, memory_ttl=None):
        return self.entries.get(key)

    def set(self, key, value, *args, **kwargs):
        self.writes += 1
        self.entries[key] = json.loads(json.dumps(value))


class TestPublisher:

    def _publisher(self, service, clock, cache):
        return FetchStatsPublisher(cache, service, clock=clock.now, wall_clock=lambda: 5000.0)

    def test_on_change_at_most_once_a_minute(self, service, clock):
        cache = SharedCache()
        publisher = self._publisher(service, clock, cache)
        assert publisher.tick() is True                  # first publish
        assert publisher.tick() is False                 # nothing changed
        service.get(FakeSession(), "https://api.test/x")
        clock.advance(30)
        assert publisher.tick() is False                 # changed, but too soon
        clock.advance(30)
        assert publisher.tick() is True
        assert cache.writes == 2
        snap = cache.entries[fs.FETCH_STATS_KEY]
        assert snap["running"] is True
        assert snap["totals"]["requests"] == 1

    def test_heartbeat_when_nothing_changes(self, service, clock):
        cache = SharedCache()
        publisher = self._publisher(service, clock, cache)
        publisher.tick()
        clock.advance(fs.REFRESH_INTERVAL - 1)
        assert publisher.tick() is False
        clock.advance(1)
        assert publisher.tick() is True

    def test_stop_publishes_stopped(self, service, clock):
        cache = SharedCache()
        publisher = self._publisher(service, clock, cache)
        publisher.stop()
        assert cache.entries[fs.FETCH_STATS_KEY]["running"] is False

    def test_a_failing_cache_never_raises(self, service, clock):
        class Broken(SharedCache):
            def set(self, *a, **k):
                raise OSError("disk full")

        assert self._publisher(service, clock, Broken()).tick() is False

    def test_reader_statuses(self, service, clock):
        cache = SharedCache()
        assert read_fetch_stats(cache)["status"] == "unknown"
        assert read_fetch_stats(None)["status"] == "unknown"
        publisher = self._publisher(service, clock, cache)
        publisher.tick()
        assert read_fetch_stats(cache, now=5010.0)["status"] == "live"
        assert read_fetch_stats(cache, now=5000.0 + fs.STALE_AFTER + 1)["status"] == "stale"
        publisher.stop()
        view = read_fetch_stats(cache, now=5010.0)
        assert view["status"] == "stopped"
        assert view["data"]["totals"]["requests"] == 0


def test_the_web_route_returns_the_published_counters(clock):
    from test._api_v3_test_helpers import build_app
    from web_interface.blueprints import api_v3 as module

    svc = FetchService({"rate_limits": {}}, clock=clock.now, sleep=clock.sleep)
    with plugin_scope("weather"):
        svc.get(FakeSession(), "https://api.test/x")
    cache = SharedCache()
    FetchStatsPublisher(cache, svc, wall_clock=time.time).tick()

    original = getattr(module.api_v3, "cache_manager", None)
    module.api_v3.cache_manager = cache
    try:
        body = build_app(module.api_v3).test_client().get("/api/v3/plugins/fetch-stats").get_json()
    finally:
        module.api_v3.cache_manager = original
    assert body["status"] == "success"
    assert body["data"]["status"] == "live"
    assert body["data"]["data"]["plugins"]["weather"]["requests"] == 1


# --- response cache (stage 2: Cache-Control max-age) -------------------------------------------

def fresh_for(seconds, body=b'{"ok": 1}', **headers):
    """A handler answering 200 with ``Cache-Control: max-age=<seconds>``."""
    def handler(url, kwargs):
        return make_response(200, body, url=url, headers={
            "Cache-Control": f"max-age={seconds}", **headers})
    return handler


class NeverHits:
    """A cache that always misses, so APIHelper always reaches the network."""

    def get(self, key, max_age=None):
        return None

    def set(self, key, value, ttl=None):
        pass


class TestResponseCache:

    def test_an_identical_get_inside_max_age_is_not_sent(self, service):
        session = FakeSession(fresh_for(60))
        first = service.get(session, "https://api.test/x", params={"d": 1}, timeout=5)
        second = service.get(session, "https://api.test/x", params={"d": 1}, timeout=5)
        assert len(session.calls) == 1
        assert second.json() == first.json() == {"ok": 1}
        assert second is not first
        totals = _counters(service)
        assert (totals["requests"], totals["memo_hits"]) == (1, 1)

    def test_a_hit_is_a_copy_the_caller_may_change(self, service):
        session = FakeSession(fresh_for(60))
        service.get(session, "https://api.test/x").headers["X-Mine"] = "1"
        assert "X-Mine" not in service.get(session, "https://api.test/x").headers

    def test_past_max_age_the_network_is_asked_again(self, service, clock):
        session = FakeSession(fresh_for(10))
        service.get(session, "https://api.test/x")
        clock.advance(9.9)
        service.get(session, "https://api.test/x")
        assert len(session.calls) == 1
        clock.advance(0.2)
        service.get(session, "https://api.test/x")
        assert len(session.calls) == 2

    def test_the_age_header_shortens_the_lifetime(self, service, clock):
        session = FakeSession(fresh_for(10, Age="8"))
        service.get(session, "https://api.test/x")
        clock.advance(2.5)
        service.get(session, "https://api.test/x")
        assert len(session.calls) == 2

    def test_never_older_than_the_callers_own_ttl(self, service, clock):
        session = FakeSession(fresh_for(400))
        service.get(session, "https://api.test/x", cache_max_age=0)
        clock.advance(20)
        service.get(session, "https://api.test/x", cache_max_age=10)    # 20 s > 10
        assert len(session.calls) == 2
        clock.advance(20)
        service.get(session, "https://api.test/x", cache_max_age=30)    # 20 s <= 30
        assert len(session.calls) == 2
        service.get(session, "https://api.test/x", cache_max_age=19.9)  # 20 s > 19.9
        assert len(session.calls) == 3

    def test_a_caller_that_does_not_say_gets_the_default_limit(self, service, clock):
        session = FakeSession(fresh_for(450))
        service.get(session, "https://api.test/x")
        clock.advance(service.default_max_age - 0.5)
        service.get(session, "https://api.test/x")
        assert len(session.calls) == 1
        clock.advance(1)
        service.get(session, "https://api.test/x")
        assert len(session.calls) == 2
        # ...while a caller with a longer TTL still takes the server at its word.
        clock.advance(100)
        service.get(session, "https://api.test/x", cache_max_age=300)
        assert len(session.calls) == 2

    def test_zero_always_asks_but_still_fills_the_cache(self, service):
        session = FakeSession(fresh_for(60))
        service.get(session, "https://api.test/x", cache_max_age=0)
        service.get(session, "https://api.test/x", cache_max_age=0)
        assert len(session.calls) == 2
        service.get(session, "https://api.test/x")
        assert len(session.calls) == 2

    def test_cache_max_age_never_reaches_the_session(self, service):
        session = FakeSession(fresh_for(60))
        service.get(session, "https://api.test/x", timeout=5, cache_max_age=30)
        assert session.calls[0][1] == {"timeout": 5}

    @pytest.mark.parametrize("status,headers", [
        (200, {"Cache-Control": "no-store, max-age=60"}),
        (200, {"Cache-Control": "no-cache, max-age=60"}),
        (200, {"Cache-Control": "private, max-age=60"}),
        (200, {"Cache-Control": "max-age=60", "Vary": "*"}),
        (200, {"Cache-Control": "max-age=60", "Set-Cookie": "sid=1"}),
        (200, {"Cache-Control": "max-age=0"}),
        (200, {"Cache-Control": "max-age=soon"}),
        (200, {"Cache-Control": "max-age=60", "Age": "60"}),
        (200, {}),
        (404, {"Cache-Control": "max-age=60"}),
        (500, {"Cache-Control": "max-age=60"}),
    ])
    def test_responses_that_must_not_be_reused_are_not(self, service, status, headers):
        session = FakeSession(lambda url, kw: make_response(status, b"{}", headers=headers, url=url))
        service.get(session, "https://api.test/x")
        service.get(session, "https://api.test/x")
        assert len(session.calls) == 2
        assert service.snapshot()["response_cache"]["entries"] == 0

    def test_a_stream_is_never_kept(self, service):
        session = FakeSession(fresh_for(60))
        service.get(session, "https://api.test/x", stream=True)
        service.get(session, "https://api.test/x", stream=True)
        assert len(session.calls) == 2

    @pytest.mark.parametrize("second", [
        {"params": {"d": 2}},
        {"headers": {"Accept": "text/html"}},
        {"headers": {"If-None-Match": '"x"'}},   # the caller's own revalidation
    ])
    def test_requests_that_could_answer_differently_do_not_share(self, service, second):
        session = FakeSession(fresh_for(60))
        service.get(session, "https://api.test/x", params={"d": 1})
        service.get(session, "https://api.test/x", **{"params": {"d": 1}, **second})
        assert len(session.calls) == 2

    def test_another_timeout_or_retry_policy_still_shares(self, service):
        # A finished 200 is the same answer however long the caller would have
        # waited or however often retried; only in-flight merging needs those.
        plain, retrying = FakeSession(fresh_for(60)), FakeSession(fresh_for(60))
        retrying.mount("https://", requests.adapters.HTTPAdapter(max_retries=Retry(total=5)))
        service.get(plain, "https://api.test/x", timeout=5)
        service.get(retrying, "https://api.test/x", timeout=30)
        assert len(plain.calls) == 1 and retrying.calls == []

    def test_a_session_with_cookies_only_reuses_its_own(self, service):
        cookied, plain = FakeSession(fresh_for(60)), FakeSession(fresh_for(60))
        cookied.cookies.set("sid", "secret")
        service.get(cookied, "https://api.test/x")
        service.get(plain, "https://api.test/x")
        service.get(cookied, "https://api.test/x")
        assert len(cookied.calls) == len(plain.calls) == 1

    def test_size_bounds(self, clock):
        svc = FetchService({"rate_limits": {}, "response_cache": {
            "max_entry_bytes": 10, "max_bytes": 25, "max_entries": 10}},
            clock=clock.now, sleep=clock.sleep)
        big = FakeSession(fresh_for(60, body=b"x" * 11))
        svc.get(big, "https://api.test/big")
        assert svc.snapshot()["response_cache"]["entries"] == 0
        small = FakeSession(fresh_for(60, body=b"y" * 10))
        for name in "abc":
            svc.get(small, f"https://api.test/{name}")
        assert svc.snapshot()["response_cache"] == {"entries": 2, "bytes": 20}
        svc.get(small, "https://api.test/a")       # evicted, least recently used
        assert len(small.calls) == 4

    def test_expired_entries_are_dropped_on_insert(self, service, clock):
        session = FakeSession(fresh_for(5))
        service.get(session, "https://api.test/a")
        clock.advance(6)
        service.get(session, "https://api.test/b")
        assert service.snapshot()["response_cache"]["entries"] == 1

    def test_off_switch(self, clock):
        svc = FetchService({"rate_limits": {}, "response_cache": {"enabled": False}},
                           clock=clock.now, sleep=clock.sleep)
        session = FakeSession(fresh_for(60))
        svc.get(session, "https://api.test/x")
        svc.get(session, "https://api.test/x")
        assert len(session.calls) == 2
        assert svc.describe_config()["response_cache"] is False

    def test_merged_callers_and_the_cache_together(self, service):
        gate = threading.Event()
        session = FakeSession(fresh_for(60), gate=gate)
        a = threading.Thread(target=lambda: service.get(session, "https://api.test/x"))
        a.start()
        assert session.started.wait(5)
        b = threading.Thread(target=lambda: service.get(session, "https://api.test/x"))
        b.start()
        _wait_for_waiters(service, 1)
        gate.set()
        a.join(5)
        b.join(5)
        service.get(session, "https://api.test/x")
        assert len(session.calls) == 1
        totals = _counters(service)
        assert (totals["requests"], totals["merged"], totals["memo_hits"]) == (1, 1, 1)

    def test_hits_are_counted_per_plugin_and_host(self, service):
        session = FakeSession(fresh_for(60))
        with plugin_scope("odds-ticker"):
            service.get(session, "https://site.api.espn.com/x")
        with plugin_scope("football-scoreboard"):
            service.get(session, "https://site.api.espn.com/x")
        snap = service.snapshot()
        assert snap["plugins"]["football-scoreboard"]["memo_hits"] == 1
        assert snap["plugins"]["football-scoreboard"]["requests"] == 0
        assert snap["plugins"]["football-scoreboard"]["hosts"] == {"site.api.espn.com": 1}
        assert snap["hosts"]["site.api.espn.com"]["memo_hits"] == 1

    def test_the_odds_manager_never_takes_odds_older_than_its_interval(self, global_service, clock):
        from unittest.mock import MagicMock
        from src.base_odds_manager import BaseOddsManager

        cache = MagicMock()
        cache.get_with_auto_strategy.return_value = None
        manager = BaseOddsManager(cache)
        manager.session = FakeSession(fresh_for(450, body=b'{"count": 0, "items": []}'))
        manager.get_odds("football", "nfl", "401", update_interval_seconds=60)
        clock.advance(59)
        manager.get_odds("football", "nfl", "401", update_interval_seconds=60)
        assert len(manager.session.calls) == 1
        clock.advance(2)
        manager.get_odds("football", "nfl", "401", update_interval_seconds=60)
        assert len(manager.session.calls) == 2

    def test_the_api_helper_bounds_a_cached_get_by_its_ttl(self, global_service, clock):
        from src.common.api_helper import APIHelper

        helper = APIHelper()
        helper.set_rate_limit(0)
        helper.session = FakeSession(fresh_for(450, body=b'{"a": 1}'))
        helper.get("https://api.test/x")
        clock.advance(31)
        helper.get("https://api.test/x")              # no TTL: the 30 s default
        assert len(helper.session.calls) == 2
        clock.advance(31)
        helper.cache_manager = NeverHits()
        helper.get("https://api.test/x", cache_key="k", cache_ttl=60)   # 31 s <= 60
        assert len(helper.session.calls) == 2


class TestCacheHitCounters:

    def test_a_shared_cache_hit(self, service):
        with plugin_scope("odds-ticker"):
            service.note_cache_hit("https://site.api.espn.com/")
        counters = _counters(service, plugin="odds-ticker")
        assert (counters["cache_hits"], counters["legacy_cache_hits"]) == (1, 0)
        assert counters["hosts"] == {"site.api.espn.com": 1}

    def test_a_legacy_hit(self, service):
        service.note_cache_hit("https://site.api.espn.com/", legacy=True)
        totals = _counters(service)
        assert (totals["cache_hits"], totals["legacy_cache_hits"]) == (1, 1)

    def test_a_read_that_avoided_no_request(self, service):
        service.note_cache_hit("https://site.api.espn.com/", legacy=True, avoided_request=False)
        totals = _counters(service)
        assert (totals["cache_hits"], totals["legacy_cache_hits"]) == (0, 1)

    def test_every_counter_is_in_every_snapshot(self, service):
        snap = service.snapshot()
        for name in ("memo_hits", "cache_hits", "legacy_cache_hits"):
            assert snap["totals"][name] == 0
        assert snap["response_cache"] == {"entries": 0, "bytes": 0}
