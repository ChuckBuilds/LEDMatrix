"""Tests for src/common/espn_payload.py and its use by BackgroundDataService."""

import copy
import time
from unittest.mock import MagicMock, Mock, patch

import pytest

from src.background_data_service import BackgroundDataService, shutdown_background_service
from src.common.espn_payload import is_espn_scoreboard_url, slim_scoreboard_payload

SCOREBOARD = "https://site.api.espn.com/apis/site/v2/sports/baseball/mlb/scoreboard"


def _event():
    """One event carrying every key the slimming drops and a sample of the
    keys scoreboards read, at the depth ESPN puts them."""
    competitor = {
        "id": "10",
        "homeAway": "home",
        "score": "5",
        "team": {"abbreviation": "NYY", "logo": "https://a/l.png",
                 "links": [{"href": "https://espn.com/team"}]},
        "records": [{"summary": "90-60"}],
        "linescores": [{"value": 1}],
        "statistics": [{"name": "hits", "displayValue": "9"}],
        "leaders": [{"name": "avg", "leaders": [{"athlete": {"id": "1"}}]}],
        "probables": [{"athlete": {"id": "2"}, "statistics": []}],
    }
    return {
        "id": "401",
        "date": "2026-10-01T23:05Z",
        "links": [{"href": "https://espn.com/game"}],
        "status": {"type": {"state": "post"}},
        "competitions": [{
            "status": {"type": {"state": "post", "shortDetail": "Final"},
                       "featuredAthletes": [{"athlete": {"id": "3"}}]},
            "competitors": [competitor, dict(copy.deepcopy(competitor), homeAway="away")],
            "odds": [{"details": "NYY -150", "overUnder": 8.5}],
            "situation": {"outs": 2},
            "notes": [{"headline": "Game 1"}],
            "broadcasts": [{"names": ["FOX"]}],
            "venue": {"fullName": "Yankee Stadium"},
            "leaders": [{"name": "hits"}],
            "headlines": [{"description": "recap"}],
            "highlights": [{"links": {"source": {}}}],
            "geoBroadcasts": [{"media": {"shortName": "FOX"}}],
        }],
    }


class TestSlimScoreboardPayload:
    def test_drops_exactly_the_listed_keys(self):
        payload = {"leagues": [{"id": "10"}], "events": [_event()]}
        slim_scoreboard_payload(payload)
        event = payload["events"][0]
        competition = event["competitions"][0]
        assert "links" not in event
        for key in ("leaders", "headlines", "highlights", "geoBroadcasts"):
            assert key not in competition
        assert "featuredAthletes" not in competition["status"]
        for competitor in competition["competitors"]:
            assert "leaders" not in competitor
            assert "probables" not in competitor
            assert "links" not in competitor["team"]

    def test_keeps_everything_else_unchanged(self):
        """Removing the dropped keys from the original by hand gives exactly
        the slimmed payload: nothing else moved, changed or went missing."""
        original = {"leagues": [{"id": "10"}], "events": [_event(), _event()]}
        expected = copy.deepcopy(original)
        for event in expected["events"]:
            del event["links"]
            competition = event["competitions"][0]
            for key in ("leaders", "headlines", "highlights", "geoBroadcasts"):
                del competition[key]
            del competition["status"]["featuredAthletes"]
            for competitor in competition["competitors"]:
                del competitor["leaders"], competitor["probables"]
                del competitor["team"]["links"]
        assert slim_scoreboard_payload(original) == expected

    def test_in_place_and_returns_payload(self):
        payload = {"events": [_event()]}
        assert slim_scoreboard_payload(payload) is payload

    @pytest.mark.parametrize("payload", [
        None, [], "x", {}, {"events": None}, {"events": "x"},
        {"events": [None, 1, "x", {"competitions": None}]},
        {"events": [{"competitions": [None, {"status": None, "competitors": None}]}]},
        {"events": [{"competitions": [{"competitors": [None, {"team": None}]}]}]},
    ])
    def test_odd_shapes_pass_through(self, payload):
        before = copy.deepcopy(payload)
        assert slim_scoreboard_payload(payload) == before


class TestIsEspnScoreboardUrl:
    @pytest.mark.parametrize("url", [
        SCOREBOARD,
        SCOREBOARD + "/",
        "http://site.api.espn.com/apis/site/v2/sports/football/college-football/scoreboard",
    ])
    def test_scoreboards(self, url):
        assert is_espn_scoreboard_url(url)

    @pytest.mark.parametrize("url", [
        None, "", 12,
        "https://site.api.espn.com/apis/site/v2/sports/baseball/mlb/teams",
        "https://site.api.espn.com/apis/site/v2/sports/football/nfl/summary",
        "https://example.com/scoreboard",
        "https://espn.com.evil.example/apis/x/scoreboard",
        "https://notespn.com/apis/x/scoreboard",
    ])
    def test_not_scoreboards(self, url):
        assert not is_espn_scoreboard_url(url)


@pytest.fixture
def service():
    shutdown_background_service()
    cache = MagicMock()
    cache.get.return_value = None
    svc = BackgroundDataService(cache, max_workers=1, request_timeout=5)
    yield svc
    svc.shutdown(wait=False)
    shutdown_background_service()


def _run(service, url, **kwargs):
    response = Mock(status_code=200)
    response.json.return_value = {"events": [_event()]}
    response.raise_for_status.return_value = None
    delivered = []
    with patch.object(service.session, "get", return_value=response):
        req_id = service.submit_fetch_request(
            sport="mlb", year=2026, url=url, cache_key="mlb_schedule_window_14_7",
            callback=lambda result: delivered.append(result.data), **kwargs)
        deadline = time.time() + 5
        while not service.is_request_complete(req_id) and time.time() < deadline:
            time.sleep(0.02)
    cached = service.cache_manager.set.call_args[0][1]
    return cached, delivered


class TestBackgroundServiceSlims:
    def test_espn_scoreboard_is_cached_and_delivered_slimmed(self, service):
        cached, delivered = _run(service, SCOREBOARD)
        competition = cached["events"][0]["competitions"][0]
        assert "leaders" not in competition
        assert "probables" not in competition["competitors"][0]
        assert competition["odds"] and competition["situation"]
        # The callback sees the very payload that was cached.
        assert delivered and delivered[0] is cached

    def test_opt_out_caches_whole_response(self, service):
        cached, _ = _run(service, SCOREBOARD, slim_payload=False)
        assert cached == {"events": [_event()]}

    def test_other_urls_untouched(self, service):
        cached, _ = _run(service, "https://example.com/feed")
        assert cached == {"events": [_event()]}
