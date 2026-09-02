"""A second request for a key already being fetched must join, not duplicate.

request_id embeds a millisecond timestamp and active_requests is keyed by it,
so every submit looked new and nothing compared what was actually being
fetched. On a real board the season-schedule cache_key is requested by both
the Recent and the Upcoming manager: they miss the cache in the same
millisecond and each start a full download and parse of the same payload.
Measured on a running board, 138 background fetches in 24 hours arriving in
pairs at identical timestamps -- half of them redundant.

The cost of a duplicate is a second download, a second JSON parse (the
expensive part on a Pi), and a second parsed copy resident at the same time.
Schedules on that board run from 256KB to 20MB. It also consumes a second of
the three executor slots with identical work, which is what makes two large
parses peak simultaneously.
"""

import threading
import time
from unittest.mock import MagicMock, Mock, patch

import pytest
import requests

from src.background_data_service import BackgroundDataService, FetchStatus


PAYLOAD = {"events": [{"id": f"g{i}"} for i in range(20)]}


@pytest.fixture
def cache():
    m = MagicMock()
    m.get.return_value = None          # always a miss: force the fetch path
    m.set.return_value = None
    return m


@pytest.fixture
def service(cache):
    svc = BackgroundDataService(cache, max_workers=3, request_timeout=5)
    yield svc
    svc.shutdown(wait=False)


def _resp():
    r = Mock()
    r.json.return_value = PAYLOAD
    r.raise_for_status.return_value = None
    return r


def _wait(service, req_id, timeout=5):
    deadline = time.time() + timeout
    while not service.is_request_complete(req_id) and time.time() < deadline:
        time.sleep(0.02)


class _BlockingSession:
    """Holds the first fetch open so a second can be submitted mid-flight."""

    def __init__(self):
        self.calls = 0
        self.release = threading.Event()
        self.started = threading.Event()

    def get(self, *a, **k):
        self.calls += 1
        self.started.set()
        self.release.wait(timeout=5)
        return _resp()


def test_a_second_submit_for_the_same_key_does_not_fetch_twice(service):
    session = _BlockingSession()
    with patch.object(service, "session", session):
        first = service.submit_fetch_request(
            sport="nba", year=2026, url="https://x/s", cache_key="nba_2026",
            callback=lambda r: None, max_retries=0)
        assert session.started.wait(timeout=5)

        second = service.submit_fetch_request(
            sport="nba", year=2026, url="https://x/s", cache_key="nba_2026",
            callback=lambda r: None, max_retries=0)

        assert second == first, "the joiner should share the in-flight request id"
        session.release.set()
        _wait(service, first)

    assert session.calls == 1, f"the payload was fetched {session.calls} times"


def test_the_joiner_still_gets_its_callback(service):
    session = _BlockingSession()
    seen = []
    with patch.object(service, "session", session):
        first = service.submit_fetch_request(
            sport="nba", year=2026, url="https://x/s", cache_key="k",
            callback=lambda r: seen.append("first"), max_retries=0)
        assert session.started.wait(timeout=5)
        joined = service.submit_fetch_request(
            sport="nba", year=2026, url="https://x/s", cache_key="k",
            callback=lambda r: seen.append("second"), max_retries=0)
        # Assert the coalescing happened, otherwise this passes trivially:
        # two independent requests would each fire their own callback and the
        # test would say nothing about the joined path.
        assert joined == first
        session.release.set()
        _wait(service, first)

    deadline = time.time() + 5
    while len(seen) < 2 and time.time() < deadline:
        time.sleep(0.02)
    assert sorted(seen) == ["first", "second"], (
        f"both submitters must be called back, got {seen}")


def test_one_callback_raising_does_not_silence_the_other(service):
    session = _BlockingSession()
    seen = []

    def boom(result):
        raise RuntimeError("consumer blew up")

    with patch.object(service, "session", session):
        first = service.submit_fetch_request(
            sport="nba", year=2026, url="https://x/s", cache_key="k",
            callback=boom, max_retries=0)
        assert session.started.wait(timeout=5)
        joined = service.submit_fetch_request(
            sport="nba", year=2026, url="https://x/s", cache_key="k",
            callback=lambda r: seen.append("survivor"), max_retries=0)
        # Same reason: without coalescing these are separate requests and
        # neither callback can affect the other.
        assert joined == first
        session.release.set()
        _wait(service, first)

    deadline = time.time() + 5
    while not seen and time.time() < deadline:
        time.sleep(0.02)
    assert seen == ["survivor"]


def test_different_keys_are_not_coalesced(service):
    session = _BlockingSession()
    with patch.object(service, "session", session):
        a = service.submit_fetch_request(
            sport="nba", year=2026, url="https://x/a", cache_key="key_a",
            callback=lambda r: None, max_retries=0)
        assert session.started.wait(timeout=5)
        b = service.submit_fetch_request(
            sport="nhl", year=2026, url="https://x/b", cache_key="key_b",
            callback=lambda r: None, max_retries=0)
        assert a != b, "different cache keys must not share a request"
        session.release.set()
        _wait(service, a)
        _wait(service, b)
    assert session.calls == 2


def test_a_later_submit_after_completion_fetches_again(service):
    """Dedupe is for concurrent requests only, not a second cache layer."""
    with patch.object(service.session, "get", side_effect=[_resp(), _resp()]) as get:
        first = service.submit_fetch_request(
            sport="nba", year=2026, url="https://x/s", cache_key="k",
            callback=lambda r: None, max_retries=0)
        _wait(service, first)
        second = service.submit_fetch_request(
            sport="nba", year=2026, url="https://x/s", cache_key="k",
            callback=lambda r: None, max_retries=0)
        _wait(service, second)
    assert first != second
    assert get.call_count == 2


def test_cancelling_releases_the_key(service):
    """A cancelled request must not wedge its key against future fetches."""
    session = _BlockingSession()
    with patch.object(service, "session", session):
        first = service.submit_fetch_request(
            sport="nba", year=2026, url="https://x/s", cache_key="k",
            callback=lambda r: None, max_retries=0)
        assert session.started.wait(timeout=5)
        service.cancel_request(first)
        assert "k" not in service._inflight_by_cache_key
        session.release.set()


def test_a_stranded_index_entry_cannot_wedge_a_key(service):
    """Defensive: the request is looked up, not trusted from the id alone."""
    service._inflight_by_cache_key["ghost"] = "no_such_request"
    with patch.object(service.session, "get", return_value=_resp()):
        req = service.submit_fetch_request(
            sport="nba", year=2026, url="https://x/s", cache_key="ghost",
            callback=lambda r: None, max_retries=0)
        _wait(service, req)
    assert service.get_result(req).success is True


def test_the_deduplicated_count_is_reported(service):
    session = _BlockingSession()
    with patch.object(service, "session", session):
        first = service.submit_fetch_request(
            sport="nba", year=2026, url="https://x/s", cache_key="k",
            callback=lambda r: None, max_retries=0)
        assert session.started.wait(timeout=5)
        service.submit_fetch_request(
            sport="nba", year=2026, url="https://x/s", cache_key="k",
            max_retries=0)
        session.release.set()
        _wait(service, first)
    assert service.get_statistics().get("deduplicated_requests") == 1


def test_a_cancelled_worker_cannot_overwrite_its_replacement(service, cache):
    """Cancelling frees the key, so a replacement may already own it.

    The worker cannot abort an HTTP call in flight, so when the cancelled one
    finally returns it must discard its response rather than write it. Without
    that, the sequence is: cancel A, submit B for the same key, B fetches and
    caches fresh data, A returns and overwrites it with the response nobody
    wanted -- and calls A's callbacks too.
    """
    slow = _BlockingSession()
    stale = {"events": [{"id": "STALE"}]}
    slow_resp = Mock()
    slow_resp.json.return_value = stale
    slow_resp.raise_for_status.return_value = None

    def blocked_get(*a, **k):
        slow.calls += 1
        slow.started.set()
        slow.release.wait(timeout=5)
        return slow_resp

    called = []
    with patch.object(service.session, "get", side_effect=blocked_get):
        first = service.submit_fetch_request(
            sport="nba", year=2026, url="https://x/s", cache_key="k",
            callback=lambda r: called.append("cancelled_one"), max_retries=0)
        assert slow.started.wait(timeout=5)

        service.cancel_request(first)
        assert "k" not in service._inflight_by_cache_key

    # The replacement writes the fresh value while the cancelled fetch is held.
    fresh = {"events": [{"id": "FRESH"}]}
    fresh_resp = Mock()
    fresh_resp.json.return_value = fresh
    fresh_resp.raise_for_status.return_value = None
    with patch.object(service.session, "get", return_value=fresh_resp):
        second = service.submit_fetch_request(
            sport="nba", year=2026, url="https://x/s", cache_key="k",
            callback=lambda r: called.append("replacement"), max_retries=0)
        _wait(service, second)

    assert cache.set.call_args[0][1] == fresh, "replacement must own the cache"

    # Now let the cancelled fetch finish. It must write nothing and call nobody.
    # Wait for the worker to actually finish rather than sleeping: a fixed
    # sleep is a race under load, and a slow worker would make this pass for
    # the wrong reason. A cancelled request is still filed in
    # completed_requests, so that is the signal it has run to completion.
    writes_before = cache.set.call_count
    slow.release.set()
    deadline = time.time() + 5
    while first not in service.completed_requests and time.time() < deadline:
        time.sleep(0.02)
    assert first in service.completed_requests, "cancelled worker never finished"

    assert cache.set.call_count == writes_before, (
        "the cancelled worker wrote to the cache after its replacement")
    assert cache.set.call_args[0][1] == fresh, "stale data overwrote fresh"
    assert "cancelled_one" not in called, (
        "a cancelled request must not deliver callbacks")


def test_request_ids_are_unique_within_a_millisecond(service):
    """request_id was sport_year_milliseconds, which collides.

    Two submits inside the same millisecond produced the SAME id, so one
    silently replaced the other in active_requests and completed_requests.
    Dedupe hands this id back to every joiner as their handle for
    get_result(), so uniqueness is now load-bearing rather than incidental.
    """
    # Stub the executor rather than the session: this is about what submit
    # hands back, and letting 50 workers loose would outlive the patch and
    # make real network calls.
    with patch.object(service.executor, "submit"):
        ids = [
            service.submit_fetch_request(
                sport="nba", year=2026, url="https://x/s",
                cache_key=f"key_{i}",           # distinct keys: no dedupe
                callback=lambda r: None, max_retries=0)
            for i in range(50)
        ]
    assert len(set(ids)) == len(ids), "request ids collided"


# --- cancellation must be terminal ------------------------------------------
#
# Cancelling used to be advisory: three separate paths wrote request.status
# without checking whether the request had already been cancelled, so a cancel
# could be silently undone and the work it was meant to stop went ahead.

URL = "http://example.invalid/scores"
KEY = "sched_nfl_2025"


class _CountingSession:
    """Records whether an HTTP fetch was ever attempted."""

    def __init__(self):
        self.calls = 0

    def get(self, *a, **k):
        self.calls += 1
        return _resp()


class _BlockingFailingSession(_CountingSession):
    """Holds the fetch open, then fails it.

    Cancelling while the worker is parked inside the HTTP call is the only way
    to reach the exception handler as a cancelled request. Cancel it before
    the call starts and the worker returns at the pre-start branch instead,
    which would leave the except path untested.
    """

    def __init__(self):
        super().__init__()
        self.started = threading.Event()
        self.release = threading.Event()

    def get(self, *a, **k):
        self.calls += 1
        self.started.set()
        self.release.wait(timeout=5)
        raise requests.RequestException("connection reset")


def _fill_every_worker_slot(service, slots=3):
    """Occupy the pool so the next submit is queued rather than started.

    This is what makes "cancel before the worker runs" deterministic instead
    of a race the test would win only sometimes. Returns the gate that
    releases the pool.
    """
    gate = threading.Event()
    for _ in range(slots):
        service.executor.submit(gate.wait, 5)
    return gate


def test_cancelling_before_the_worker_starts_stops_the_fetch(service, cache):
    """The queued worker must honour a cancel, not overwrite it with IN_PROGRESS.

    Between submit and the worker picking the job up, the request sits in the
    executor queue. Cancelling there is the cheapest possible cancel -- nothing
    has been downloaded yet -- and it was the one that did not work.
    """
    gate = _fill_every_worker_slot(service)
    session = _CountingSession()
    delivered = []

    with patch.object(service, 'session', session):
        rid = service.submit_fetch_request(
            "nfl", 2025, URL, KEY, max_retries=0, callback=delivered.append
        )
        assert service.cancel_request(rid) is True
        gate.set()                       # let the queued worker run
        _wait(service, rid)

    assert session.calls == 0, (
        "cancelled before it started, yet the worker still downloaded the payload"
    )
    assert cache.set.call_count == 0, "a cancelled request wrote to the cache"
    assert delivered == [], "a cancelled request invoked its callbacks"


def test_a_cancel_during_the_commit_is_refused(service, cache):
    """Once the worker has claimed the commit, cancelling is too late.

    The claim and the cancelled-check happen in one critical section, so a
    cancel arriving after it cannot retroactively abandon data already on its
    way to the cache. Letting it through stranded every joiner: the payload
    landed in the cache but the callbacks were suppressed, so a manager that
    joined this fetch waited for a call that never came.
    """
    gate = _fill_every_worker_slot(service)
    late = {}
    delivered = []

    def cancel_mid_write(key, data, *a, **k):
        late['returned'] = service.cancel_request(late['rid'])

    cache.set.side_effect = cancel_mid_write

    # Read the payload inside the callback. The service releases result.data
    # once every callback has been delivered, so inspecting the FetchResult
    # afterwards sees the released object, not what the caller was handed.
    def record(result):
        delivered.append((result.success, result.data))

    with patch.object(service, 'session', _CountingSession()):
        late['rid'] = service.submit_fetch_request(
            "nfl", 2025, URL, KEY, max_retries=0, callback=record
        )
        gate.set()                       # only now can the worker reach the commit
        _wait(service, late['rid'])

    assert late.get('returned') is False, (
        "cancelled a request that had already committed"
    )
    assert cache.set.call_count == 1, "the commit itself was lost"
    assert delivered == [(True, PAYLOAD)], (
        "data reached the cache but the callbacks were suppressed -- "
        "every joined submitter is left waiting forever"
    )


def test_a_failure_after_cancelling_stays_cancelled(service, cache):
    """A cancelled request that then errors must not resurface as FAILED.

    The except path overwrote CANCELLED with FAILED, and the callback gate in
    the finally block only suppresses callbacks for CANCELLED -- so cancelling
    a request that was about to time out delivered a spurious error callback.
    """
    session = _BlockingFailingSession()
    delivered = []

    with patch.object(service, 'session', session):
        rid = service.submit_fetch_request(
            "nfl", 2025, URL, KEY, max_retries=0, callback=delivered.append
        )
        # Assert the worker is inside the HTTP call before cancelling,
        # otherwise this silently degrades into the pre-start case and the
        # exception handler is never exercised.
        assert session.started.wait(timeout=5)
        assert service.cancel_request(rid) is True
        session.release.set()
        _wait(service, rid)

    assert session.calls == 1, "the fetch never started, so nothing could fail"

    assert delivered == [], "a cancelled request delivered a failure callback"
    assert service.get_request_status(rid) is FetchStatus.CANCELLED, (
        "a cancelled request that then errored was reported as FAILED"
    )
