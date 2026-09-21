"""
Tests for src/base_classes/data_sources.py

Covers ESPNDataSource, MLBAPIDataSource, SoccerAPIDataSource.
All HTTP calls are mocked to avoid network access.
"""

import logging
from datetime import datetime, date
from unittest.mock import MagicMock, patch, Mock
import pytest
import requests

from src.base_classes.data_sources import ESPNDataSource, MLBAPIDataSource, SoccerAPIDataSource


def _make_logger() -> logging.Logger:
    return logging.getLogger("test_data_sources")


def _mock_response(json_data: dict, status_code: int = 200):
    resp = Mock(spec=requests.Response)
    resp.status_code = status_code
    resp.json.return_value = json_data
    resp.raise_for_status = Mock()
    if status_code >= 400:
        resp.raise_for_status.side_effect = requests.HTTPError(response=resp)
    return resp


# ---------------------------------------------------------------------------
# ESPNDataSource
# ---------------------------------------------------------------------------

class TestESPNDataSource:
    def setup_method(self):
        self.source = ESPNDataSource(_make_logger())

    def test_get_headers(self):
        headers = self.source.get_headers()
        assert headers["Accept"] == "application/json"
        assert "LEDMatrix" in headers["User-Agent"]

    def test_fetch_live_games_returns_live_events(self):
        live_event = {
            "competitions": [{"status": {"type": {"state": "in"}}}]
        }
        non_live_event = {
            "competitions": [{"status": {"type": {"state": "pre"}}}]
        }
        payload = {"events": [live_event, non_live_event]}

        with patch.object(self.source.session, "get", return_value=_mock_response(payload)):
            result = self.source.fetch_live_games("football", "nfl")

        assert len(result) == 1
        assert result[0] is live_event

    def test_fetch_live_games_empty_when_none_live(self):
        payload = {"events": [
            {"competitions": [{"status": {"type": {"state": "post"}}}]}
        ]}
        with patch.object(self.source.session, "get", return_value=_mock_response(payload)):
            result = self.source.fetch_live_games("football", "nfl")
        assert result == []

    def test_fetch_live_games_returns_empty_on_error(self):
        with patch.object(self.source.session, "get", side_effect=Exception("network failure")):
            result = self.source.fetch_live_games("football", "nfl")
        assert result == []

    def test_fetch_schedule_returns_all_events(self):
        events = [{"id": "1"}, {"id": "2"}]
        payload = {"events": events}
        start = datetime(2024, 1, 1)
        end = datetime(2024, 1, 7)

        with patch.object(self.source.session, "get", return_value=_mock_response(payload)):
            result = self.source.fetch_schedule("football", "nfl", (start, end))

        assert len(result) == 2

    def test_fetch_schedule_returns_empty_on_error(self):
        with patch.object(self.source.session, "get", side_effect=Exception("timeout")):
            result = self.source.fetch_schedule("football", "nfl", (datetime.now(), datetime.now()))
        assert result == []

    def test_fetch_standings_success(self):
        payload = {"standings": []}
        with patch.object(self.source.session, "get", return_value=_mock_response(payload)):
            result = self.source.fetch_standings("football", "nfl")
        assert result == payload

    def test_fetch_standings_returns_empty_on_error(self):
        # A transport failure is a RequestException, not a bare Exception.
        # The old stand-in passed only because the handler caught everything,
        # including bugs in the method under test.
        with patch.object(self.source.session, "get",
                          side_effect=requests.ConnectionError("error")):
            result = self.source.fetch_standings("football", "nfl")
        assert result == {}

    # ------------------------------------------------------------------
    # fetch_standings endpoint selection
    #
    # College leagues publish a poll at /rankings and a records table at
    # /standings; professional leagues have only /standings. Probing them in
    # the wrong order still returns 200 -- just without a poll in it -- so
    # nothing failed and the rank badge simply never appeared. Order is the
    # behaviour here, so these tests assert it directly.
    # ------------------------------------------------------------------

    @staticmethod
    def _requested_endpoints(mock_get):
        """The endpoint names probed, in the order they were requested."""
        return [call.args[0].rsplit("/", 1)[-1] for call in mock_get.call_args_list]

    def test_professional_league_asks_standings_first(self):
        payload = {"standings": []}
        with patch.object(self.source.session, "get",
                          return_value=_mock_response(payload)) as mock_get:
            result = self.source.fetch_standings("football", "nfl")
        assert result == payload
        assert self._requested_endpoints(mock_get) == ["standings"]

    def test_college_league_asks_rankings_first(self):
        poll = {"rankings": [{"name": "AP Top 25"}]}
        with patch.object(self.source.session, "get",
                          return_value=_mock_response(poll)) as mock_get:
            result = self.source.fetch_standings("football", "college-football")
        assert result == poll
        assert self._requested_endpoints(mock_get) == ["rankings"]

    def test_rankings_200_without_a_poll_falls_through_to_standings(self):
        """A 200 is not the same as an answer.

        This is the case the old code could not see: the endpoint responded,
        so nothing raised, but the body carried no poll.
        """
        empty_poll = _mock_response({"rankings": []})
        table = _mock_response({"standings": [{"entries": []}]})
        with patch.object(self.source.session, "get",
                          side_effect=[empty_poll, table]) as mock_get:
            result = self.source.fetch_standings(
                "basketball", "mens-college-basketball")
        assert result == {"standings": [{"entries": []}]}
        assert self._requested_endpoints(mock_get) == ["rankings", "standings"]

    def test_404_on_the_first_endpoint_falls_through_quietly(self):
        missing = _mock_response({}, status_code=404)
        table = _mock_response({"standings": []})
        with patch.object(self.source.session, "get",
                          side_effect=[missing, table]) as mock_get:
            result = self.source.fetch_standings("baseball", "college-baseball")
        assert result == {"standings": []}
        assert self._requested_endpoints(mock_get) == ["rankings", "standings"]

    def test_recovers_from_a_non_404_failure_on_the_first_endpoint(self):
        table = _mock_response({"standings": [{"entries": []}]})
        with patch.object(self.source.session, "get",
                          side_effect=[requests.ConnectionError("reset"), table]) as mock_get:
            result = self.source.fetch_standings("football", "college-football")
        assert result == {"standings": [{"entries": []}]}
        assert self._requested_endpoints(mock_get) == ["rankings", "standings"]

    def test_both_endpoints_failing_returns_empty(self):
        with patch.object(self.source.session, "get",
                          side_effect=requests.ConnectionError("down")) as mock_get:
            result = self.source.fetch_standings("football", "nfl")
        assert result == {}
        assert self._requested_endpoints(mock_get) == ["standings", "rankings"]

    def test_a_non_object_payload_is_treated_as_a_miss(self):
        odd = _mock_response(["not", "an", "object"])
        table = _mock_response({"standings": []})
        with patch.object(self.source.session, "get",
                          side_effect=[odd, table]) as mock_get:
            result = self.source.fetch_standings("football", "college-football")
        assert result == {"standings": []}
        assert self._requested_endpoints(mock_get) == ["rankings", "standings"]

    def test_a_bug_in_this_method_is_not_swallowed_as_a_failed_endpoint(self):
        """The guard for the narrowed handler.

        An error raised while reading the payload used to be caught by the
        endpoint handler and reported as 'no poll here', which would silently
        drop rankings for a league that has them. It must surface instead.
        """
        boom = Mock(spec=requests.Response)
        boom.status_code = 200
        boom.raise_for_status = Mock()
        boom.json.side_effect = TypeError("a bug, not a network failure")
        with patch.object(self.source.session, "get", return_value=boom):
            with pytest.raises(TypeError):
                self.source.fetch_standings("football", "nfl")

    def test_base_url_set_correctly(self):
        assert "espn.com" in self.source.base_url


# ---------------------------------------------------------------------------
# MLBAPIDataSource
# ---------------------------------------------------------------------------

class TestMLBAPIDataSource:
    def setup_method(self):
        self.source = MLBAPIDataSource(_make_logger())

    def test_fetch_live_games_filters_live(self):
        live_game = {"status": {"abstractGameState": "Live"}}
        final_game = {"status": {"abstractGameState": "Final"}}
        payload = {"dates": [{"games": [live_game, final_game]}]}

        with patch.object(self.source.session, "get", return_value=_mock_response(payload)):
            result = self.source.fetch_live_games("baseball", "mlb")

        assert len(result) == 1
        assert result[0] is live_game

    def test_fetch_live_games_empty_dates(self):
        payload = {"dates": []}
        with patch.object(self.source.session, "get", return_value=_mock_response(payload)):
            result = self.source.fetch_live_games("baseball", "mlb")
        assert result == []

    def test_fetch_live_games_returns_empty_on_error(self):
        with patch.object(self.source.session, "get", side_effect=Exception("err")):
            result = self.source.fetch_live_games("baseball", "mlb")
        assert result == []

    def test_fetch_schedule_aggregates_all_dates(self):
        payload = {
            "dates": [
                {"games": [{"id": "1"}, {"id": "2"}]},
                {"games": [{"id": "3"}]},
            ]
        }
        with patch.object(self.source.session, "get", return_value=_mock_response(payload)):
            result = self.source.fetch_schedule("baseball", "mlb", (datetime.now(), datetime.now()))
        assert len(result) == 3

    def test_fetch_schedule_returns_empty_on_error(self):
        with patch.object(self.source.session, "get", side_effect=Exception("err")):
            result = self.source.fetch_schedule("baseball", "mlb", (datetime.now(), datetime.now()))
        assert result == []

    def test_fetch_standings_success(self):
        payload = {"records": []}
        with patch.object(self.source.session, "get", return_value=_mock_response(payload)):
            result = self.source.fetch_standings("baseball", "mlb")
        assert result == payload

    def test_fetch_standings_returns_empty_on_error(self):
        with patch.object(self.source.session, "get", side_effect=Exception("err")):
            result = self.source.fetch_standings("baseball", "mlb")
        assert result == {}


# ---------------------------------------------------------------------------
# SoccerAPIDataSource
# ---------------------------------------------------------------------------

class TestSoccerAPIDataSource:
    def setup_method(self):
        self.source = SoccerAPIDataSource(_make_logger(), api_key="test-key-123")

    def test_headers_include_api_key(self):
        headers = self.source.get_headers()
        assert headers["X-Auth-Token"] == "test-key-123"

    def test_headers_without_api_key(self):
        source = SoccerAPIDataSource(_make_logger())
        headers = source.get_headers()
        assert "X-Auth-Token" not in headers

    def test_fetch_live_games_success(self):
        payload = {"matches": [{"id": "m1"}, {"id": "m2"}]}
        with patch.object(self.source.session, "get", return_value=_mock_response(payload)):
            result = self.source.fetch_live_games("soccer", "eng.1")
        assert len(result) == 2

    def test_fetch_live_games_returns_empty_on_error(self):
        with patch.object(self.source.session, "get", side_effect=Exception("err")):
            result = self.source.fetch_live_games("soccer", "eng.1")
        assert result == []

    def test_fetch_schedule_success(self):
        payload = {"matches": [{"id": "m1"}]}
        with patch.object(self.source.session, "get", return_value=_mock_response(payload)):
            result = self.source.fetch_schedule("soccer", "eng.1", (datetime.now(), datetime.now()))
        assert len(result) == 1

    def test_fetch_schedule_returns_empty_on_error(self):
        with patch.object(self.source.session, "get", side_effect=Exception("err")):
            result = self.source.fetch_schedule("soccer", "eng.1", (datetime.now(), datetime.now()))
        assert result == []

    def test_fetch_standings_success(self):
        payload = {"standings": []}
        with patch.object(self.source.session, "get", return_value=_mock_response(payload)):
            result = self.source.fetch_standings("soccer", "PL")
        assert result == payload

    def test_fetch_standings_returns_empty_on_error(self):
        with patch.object(self.source.session, "get", side_effect=Exception("err")):
            result = self.source.fetch_standings("soccer", "PL")
        assert result == {}
