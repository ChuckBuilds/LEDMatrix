"""BackgroundDataService recovers from ESPN rejecting a date range.

This is the path that broke on the Pi: NFLRecentManager submits the whole
season as ``dates=20260801-20270301``, the service fetches it on a worker
thread, and from 2026-09-15 every one of those fetches came back
``400 Client Error: Bad Request``. The service must re-ask in chunks and cache
the merged season rather than mark it failed.
"""

import time
from unittest.mock import MagicMock, patch

import pytest

from src.background_data_service import BackgroundDataService
from src.common.espn_dates import ESPN_MAX_LIMIT, parse_espn_date_range

URL = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard"


class FakeResponse:
    def __init__(self, status_code, payload=None):
        self.status_code = status_code
        self._payload = payload if payload is not None else {"events": []}

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(str(self.status_code) + " Client Error: Bad Request")


class RangeRejectingSession:
    """Rejects day ranges the way ESPN has since 2026-09-15."""

    def __init__(self, events_by_chunk=None, fail_chunks=()):
        self.calls = []
        self.events_by_chunk = events_by_chunk or {}
        self.fail_chunks = set(fail_chunks)

    def get(self, url, params=None, headers=None, timeout=None):
        params = dict(params or {})
        dates = str(params.get("dates", ""))
        self.calls.append(params)
        if parse_espn_date_range(dates) is not None:
            return FakeResponse(400)
        if dates in self.fail_chunks:
            return FakeResponse(500)
        return FakeResponse(200, {"events": self.events_by_chunk.get(dates, [])})


@pytest.fixture
def cache():
    manager = MagicMock()
    manager.get.return_value = None  # always a miss: force the fetch path
    return manager


@pytest.fixture
def service(cache):
    svc = BackgroundDataService(cache, max_workers=1, request_timeout=5)
    yield svc
    svc.shutdown(wait=False)


def submit_and_wait(service, session, dates, limit=1000):
    results = []
    with patch.object(service, "session", session):
        request_id = service.submit_fetch_request(
            sport="nfl",
            year=2026,
            url=URL,
            cache_key="nfl_schedule_2026",
            params={"dates": dates, "limit": limit},
            max_retries=0,
            callback=results.append,
        )
        deadline = time.time() + 5
        while not service.is_request_complete(request_id) and time.time() < deadline:
            time.sleep(0.02)
    assert results, "the fetch never completed"
    return results[0]


def test_the_service_advertises_that_it_handles_ranges():
    # Plugins read this to decide whether to submit a season range here or
    # fetch it themselves on a core that predates the fix.
    assert BackgroundDataService.handles_espn_date_ranges is True


def test_a_rejected_season_is_recovered_and_cached(service, cache):
    session = RangeRejectingSession(
        {"202609": [{"id": "a"}, {"id": "b"}], "20261001": [{"id": "c"}]}
    )
    result = submit_and_wait(service, session, "20260901-20261001")

    assert result.success, result.error
    cached_key, cached_payload = cache.set.call_args[0][:2]
    assert cached_key == "nfl_schedule_2026"
    assert [event["id"] for event in cached_payload["events"]] == ["a", "b", "c"]


def test_a_full_season_costs_chunks_not_one_request_per_day(service):
    session = RangeRejectingSession({"202609": [{"id": "a"}]})
    submit_and_wait(service, session, "20260801-20270301")
    assert [call["dates"] for call in session.calls] == [
        "20260801-20270301",
        "202608",
        "202609",
        "202610",
        "202611",
        "202612",
        "202701",
        "202702",
        "20270301",
    ]


def test_the_limit_that_truncates_never_reaches_espn(service):
    session = RangeRejectingSession({"202609": [{"id": "a"}]})
    submit_and_wait(service, session, "20260901-20260930", limit=1000)
    assert all(call["limit"] == ESPN_MAX_LIMIT for call in session.calls)


def test_a_non_scoreboard_endpoint_keeps_its_limit(service):
    # /teams needs limit=1000: college football has 762 teams, and limit=500
    # returns 500 of them. Only scoreboards truncate above 500.
    session = RangeRejectingSession()
    with patch.object(service, "session", session):
        request_id = service.submit_fetch_request(
            sport="ncaa_fb",
            year=2026,
            url="https://site.api.espn.com/apis/site/v2/sports/football/college-football/teams",
            cache_key="ncaa_fb_teams",
            params={"limit": 1000},
            max_retries=0,
        )
        deadline = time.time() + 5
        while not service.is_request_complete(request_id) and time.time() < deadline:
            time.sleep(0.02)
    assert session.calls[0]["limit"] == 1000


def test_losing_every_chunk_is_a_failure_not_an_empty_season(service, cache):
    session = RangeRejectingSession(fail_chunks={"202609"})
    result = submit_and_wait(service, session, "20260901-20260930")
    # An empty payload would be cached and read as "no games all year".
    assert not result.success
    cache.set.assert_not_called()


def test_a_400_on_a_single_day_is_still_a_failure(service, cache):
    class AlwaysBad(RangeRejectingSession):
        def get(self, url, params=None, headers=None, timeout=None):
            self.calls.append(dict(params or {}))
            return FakeResponse(400)

    session = AlwaysBad()
    result = submit_and_wait(service, session, "20260913")
    assert not result.success
    assert len(session.calls) == 1
    cache.set.assert_not_called()
