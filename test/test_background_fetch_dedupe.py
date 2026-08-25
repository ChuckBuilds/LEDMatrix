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

from src.background_data_service import BackgroundDataService


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
