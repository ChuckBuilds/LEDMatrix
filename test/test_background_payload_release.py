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
"""

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
    deadline = time.time() + timeout
    while not service.is_request_complete(req_id) and time.time() < deadline:
        time.sleep(0.02)


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
            _wait(service, req_id)

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
