"""A delivered fetch payload must not stay resident on the stored result.

BackgroundDataService kept the fetched body on the FetchResult it filed in
`completed_requests`, which is swept only hourly and capped at 500 entries by
count. For status records that is free; for a season schedule it is not. NCAA
football's 2026 schedule is 946 games, and on a 1GB Pi 3B+ the parsed payload
measured ~90MB -- a tenth of the board's memory, pinned for an hour after the
consumer had already been handed it.

The cache-hit path was the worse of the two. It runs once per update interval
per sport, mints a fresh request_id each time, and hands back whatever the
cache returns -- so a memory-tier miss (the tier is capped at 150 entries)
re-parses the payload from disk into a genuinely new object. Those accumulate
as separate copies rather than shared references, which is the staircase seen
in the field: RSS stepping up ~90MB per sport as seasons loaded and never
coming back down.

Releasing is safe because the payload is written to the cache under the
request's cache_key before the result is built, and that is where consumers
read it from -- the callback is handed the object directly and the plugins use
it only in passing before reading the cache back.

Requests submitted *without* a callback keep their payload: polling
get_result() is then the only way to collect it, so releasing would break that
contract.

And the release must happen after EVERY callback, not after each one. Callers
that joined an in-flight fetch share a single FetchResult, so releasing per
delivery strips the payload out from under everyone still queued -- see
TestJoinersAllGetTheData.
"""

import threading
import time
import pytest
from unittest.mock import MagicMock, Mock, patch

from src.background_data_service import BackgroundDataService


PAYLOAD = {"events": [{"id": f"g{i}"} for i in range(50)]}


@pytest.fixture
def cache():
    m = MagicMock()
    m.get.return_value = None
    m.set.return_value = None
    return m


@pytest.fixture
def service(cache):
    svc = BackgroundDataService(cache, max_workers=2, request_timeout=5)
    yield svc
    svc.shutdown(wait=False)


def _wait(service, req_id, timeout=5):
    """Wait for the result to be FILED.

    Enough for anything that is true by the time the worker stores the result:
    its success flag, its error, the cache write that happened during the
    fetch.
    """
    deadline = time.time() + timeout
    while not service.is_request_complete(req_id) and time.time() < deadline:
        time.sleep(0.02)


def _wait_for_release(service, req_id, timeout=5):
    """Wait for the payload to be RELEASED, which is strictly later.

    The worker files the result, then runs the callback, then releases. So
    is_request_complete() goes true while the callback still has not run --
    waiting on it alone leaves a window in which `seen` is empty and the
    payload is still resident, and the assertions race the worker. It passes
    in practice only because a one-line callback usually beats the 20ms poll.

    Release happens after the callback returns, so a released payload also
    means the callback has finished: one wait covers both.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        result = service.get_result(req_id)
        if result is not None and result.data is None:
            return
        time.sleep(0.02)
    raise AssertionError(
        f"payload for {req_id} was never released (callback may not have run)")


def _resp():
    r = Mock()
    r.json.return_value = PAYLOAD
    r.raise_for_status.return_value = None
    return r


class TestFetchPath:
    def test_callback_receives_the_payload_then_it_is_released(self, service, cache):
        seen = {}

        def callback(result):
            # The consumer's one look at the data happens here.
            seen['events'] = len(result.data['events'])

        with patch.object(service.session, "get", return_value=_resp()):
            req_id = service.submit_fetch_request(
                sport="ncaa_fb", year=2026, url="https://example.com/s",
                cache_key="ncaa_fb_2026", callback=callback, max_retries=0,
            )
            _wait_for_release(service, req_id)

        assert seen['events'] == 50, "callback must still be handed the payload"

        stored = service.get_result(req_id)
        assert stored is not None
        assert stored.success is True
        assert stored.data is None, "payload must not stay on the stored result"

    def test_nothing_is_lost_the_cache_holds_it(self, service, cache):
        with patch.object(service.session, "get", return_value=_resp()):
            req_id = service.submit_fetch_request(
                sport="ncaa_fb", year=2026, url="https://example.com/s",
                cache_key="ncaa_fb_2026", callback=lambda r: None, max_retries=0,
            )
            _wait(service, req_id)

        cache.set.assert_called_once()
        key, written = cache.set.call_args[0][:2]
        assert key == "ncaa_fb_2026"
        assert written == PAYLOAD, "the payload must be persisted before release"

    def test_without_a_callback_the_payload_is_kept(self, service, cache):
        # Polling get_result() is then the only delivery mechanism.
        with patch.object(service.session, "get", return_value=_resp()):
            req_id = service.submit_fetch_request(
                sport="nfl", year=2026, url="https://example.com/s",
                cache_key="nfl_2026", max_retries=0,
            )
            _wait(service, req_id)

        assert service.get_result(req_id).data == PAYLOAD

    def test_a_failed_fetch_still_records_its_error(self, service, cache):
        with patch.object(service.session, "get", side_effect=Exception("boom")):
            req_id = service.submit_fetch_request(
                sport="nfl", year=2026, url="https://example.com/s",
                cache_key="nfl_2026", callback=lambda r: None, max_retries=0,
            )
            _wait(service, req_id)

        stored = service.get_result(req_id)
        assert stored.success is False
        assert stored.error is not None


class TestCacheHitPath:
    def test_cache_hit_releases_after_the_callback(self, service, cache):
        cache.get.return_value = PAYLOAD
        seen = {}

        req_id = service.submit_fetch_request(
            sport="ncaa_fb", year=2026, url="https://example.com/s",
            cache_key="ncaa_fb_2026",
            callback=lambda r: seen.update(events=len(r.data['events'])),
        )

        assert seen['events'] == 50
        assert service.get_result(req_id).data is None

    def test_repeated_cache_hits_do_not_accumulate_payloads(self, service, cache):
        # The staircase: one entry per update interval per sport, each one
        # potentially a freshly parsed copy after a memory-tier miss.
        cache.get.return_value = PAYLOAD

        for _ in range(25):
            service.submit_fetch_request(
                sport="ncaa_fb", year=2026, url="https://example.com/s",
                cache_key="ncaa_fb_2026", callback=lambda r: None,
            )

        retained = [r for r in service.completed_requests.values() if r.data is not None]
        assert retained == [], f"{len(retained)} payloads still resident"

    def test_cache_hit_without_a_callback_is_unchanged(self, service, cache):
        cache.get.return_value = PAYLOAD
        req_id = service.submit_fetch_request(
            sport="nfl", year=2026, url="https://example.com/s",
            cache_key="nfl_2026",
        )
        assert service.get_result(req_id).data == PAYLOAD


class TestJoinersAllGetTheData:
    """Deduplicated callers share one FetchResult; releasing between them
    empties it for the rest.

    Not a corner case. A sport's recent, upcoming and live managers all ask for
    the same season schedule, so the second and third are joiners on almost
    every cycle. Releasing inside the delivery loop handed the payload to
    whichever ran first and gave the others `result.data is None`.

    The consequence was worse than a quiet degradation, because consumers do
    `result.data.get('events')`: they raised AttributeError, the delivery loop
    caught it, and the whole failure surfaced as a single
    "Error in callback for request ..." line while that manager silently never
    received its schedule.
    """

    class _BlockingSession:
        """Holds the fetch open so a second submit lands while in flight."""

        def __init__(self):
            self.release = threading.Event()
            self.started = threading.Event()

        def get(self, *a, **k):
            self.started.set()
            self.release.wait(timeout=5)
            return _resp()

    def test_every_joiner_is_handed_the_payload(self, service):
        session = self._BlockingSession()
        seen = {}

        def record(name):
            # Read it the way the sport managers do. `result.data['events']`
            # would raise TypeError on None; `.get` raises AttributeError,
            # which is the error actually seen in the field.
            def cb(result):
                seen[name] = result.data.get('events') if result.data else None
            return cb

        with patch.object(service, "session", session):
            first = service.submit_fetch_request(
                sport="nhl", year=2026, url="https://x/s", cache_key="nhl_2026",
                callback=record("first"), max_retries=0)
            assert session.started.wait(timeout=5)

            joined = service.submit_fetch_request(
                sport="nhl", year=2026, url="https://x/s", cache_key="nhl_2026",
                callback=record("second"), max_retries=0)
            # Without coalescing these are two independent fetches that each
            # own their result, and the test proves nothing about sharing.
            assert joined == first, "the joiner should share the in-flight id"

            session.release.set()
            _wait(service, first)

        deadline = time.time() + 5
        while len(seen) < 2 and time.time() < deadline:
            time.sleep(0.02)

        assert set(seen) == {"first", "second"}, f"both must be called, got {seen}"
        for name, events in seen.items():
            assert events is not None, (
                f"{name!r} was handed a released payload: the result was "
                f"emptied before every callback had been delivered")
            assert len(events) == 50, f"{name!r} got {events!r}"

    def test_the_payload_is_still_released_once_they_have_all_had_it(self, service):
        """The memory fix must survive the ordering fix."""
        session = self._BlockingSession()

        with patch.object(service, "session", session):
            first = service.submit_fetch_request(
                sport="nhl", year=2026, url="https://x/s", cache_key="nhl_2026",
                callback=lambda r: None, max_retries=0)
            assert session.started.wait(timeout=5)
            service.submit_fetch_request(
                sport="nhl", year=2026, url="https://x/s", cache_key="nhl_2026",
                callback=lambda r: None, max_retries=0)
            session.release.set()
            _wait_for_release(service, first)

        stored = service.get_result(first)
        assert stored is not None and stored.data is None, (
            "the payload must still be dropped once every callback has run")
