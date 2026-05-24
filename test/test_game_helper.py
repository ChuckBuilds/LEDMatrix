"""
Tests for src/common/game_helper.py

Covers GameHelper: extract_game_details, filter_*, sort_games_by_time,
process_games, get_game_summary, and all private helpers.
"""

import logging
import pytest
from datetime import datetime, timezone, timedelta

from src.common.game_helper import GameHelper


def _make_logger() -> logging.Logger:
    return logging.getLogger("test_game_helper")


def _make_espn_event(
    state: str = "in",
    home_abbr: str = "LAL",
    away_abbr: str = "BOS",
    home_score: str = "105",
    away_score: str = "98",
    date_str: str = "2024-01-15T20:00:00Z",
    period: int = 4,
    status_name: str = "STATUS_IN_PROGRESS",
    home_record: str = "30-10",
    away_record: str = "25-15",
    event_id: str = "game-1",
) -> dict:
    return {
        "id": event_id,
        "date": date_str,
        "competitions": [
            {
                "status": {
                    "type": {
                        "state": state,
                        "shortDetail": "Q4 2:30",
                        "name": status_name,
                    },
                    "period": period,
                    "displayClock": "2:30",
                },
                "competitors": [
                    {
                        "homeAway": "home",
                        "id": "h1",
                        "team": {"abbreviation": home_abbr, "displayName": f"{home_abbr} Team"},
                        "score": home_score,
                        "records": [{"summary": home_record}],
                    },
                    {
                        "homeAway": "away",
                        "id": "a1",
                        "team": {"abbreviation": away_abbr, "displayName": f"{away_abbr} Team"},
                        "score": away_score,
                        "records": [{"summary": away_record}],
                    },
                ],
            }
        ],
    }


@pytest.fixture
def helper():
    return GameHelper(timezone_str="UTC", logger=_make_logger())


# ---------------------------------------------------------------------------
# extract_game_details
# ---------------------------------------------------------------------------

class TestExtractGameDetails:
    def test_live_game(self, helper):
        event = _make_espn_event(state="in")
        result = helper.extract_game_details(event)
        assert result is not None
        assert result["is_live"] is True
        assert result["is_final"] is False
        assert result["is_upcoming"] is False

    def test_final_game(self, helper):
        event = _make_espn_event(state="post")
        result = helper.extract_game_details(event)
        assert result["is_final"] is True

    def test_upcoming_game(self, helper):
        event = _make_espn_event(state="pre")
        result = helper.extract_game_details(event)
        assert result["is_upcoming"] is True

    def test_halftime_detection(self, helper):
        event = _make_espn_event(state="halftime", status_name="STATUS_HALFTIME")
        result = helper.extract_game_details(event)
        assert result["is_halftime"] is True

    def test_basic_fields_present(self, helper):
        event = _make_espn_event()
        result = helper.extract_game_details(event)
        for key in ("id", "home_abbr", "away_abbr", "home_score", "away_score",
                    "home_record", "away_record", "start_time_utc"):
            assert key in result

    def test_team_abbreviations(self, helper):
        event = _make_espn_event(home_abbr="MIA", away_abbr="PHX")
        result = helper.extract_game_details(event)
        assert result["home_abbr"] == "MIA"
        assert result["away_abbr"] == "PHX"

    def test_scores_as_strings(self, helper):
        event = _make_espn_event(home_score="110", away_score="99")
        result = helper.extract_game_details(event)
        assert result["home_score"] == "110"
        assert result["away_score"] == "99"

    def test_returns_none_on_empty(self, helper):
        assert helper.extract_game_details({}) is None
        assert helper.extract_game_details(None) is None

    def test_returns_none_when_no_competitors(self, helper):
        event = _make_espn_event()
        event["competitions"][0]["competitors"] = []
        assert helper.extract_game_details(event) is None

    def test_date_z_suffix_parsed(self, helper):
        event = _make_espn_event(date_str="2024-06-01T19:30:00Z")
        result = helper.extract_game_details(event)
        assert result["start_time_utc"] is not None
        assert result["start_time_utc"].tzinfo is not None

    def test_zero_zero_record_suppressed(self, helper):
        event = _make_espn_event(home_record="0-0", away_record="0-0-0")
        result = helper.extract_game_details(event)
        assert result["home_record"] == ""
        assert result["away_record"] == ""

    def test_basketball_sport_fields(self, helper):
        event = _make_espn_event(period=3)
        result = helper.extract_game_details(event, sport="basketball")
        assert result["period_text"] == "Q3"
        assert "clock" in result

    def test_basketball_overtime_period(self, helper):
        event = _make_espn_event(period=5)
        result = helper.extract_game_details(event, sport="basketball")
        assert result["period_text"] == "OT1"

    def test_football_sport_fields(self, helper):
        event = _make_espn_event(period=2)
        result = helper.extract_game_details(event, sport="football")
        assert result["period_text"] == "Q2"

    def test_hockey_sport_fields_period_1(self, helper):
        event = _make_espn_event(period=1)
        result = helper.extract_game_details(event, sport="hockey")
        assert result["period_text"] == "P1"

    def test_hockey_sport_fields_ot(self, helper):
        event = _make_espn_event(period=4)
        result = helper.extract_game_details(event, sport="hockey")
        assert result["period_text"] == "OT1"

    def test_baseball_sport_fields(self, helper):
        event = _make_espn_event(period=7)
        result = helper.extract_game_details(event, sport="baseball")
        assert result["period_text"] == "INN 7"


# ---------------------------------------------------------------------------
# Filter methods
# ---------------------------------------------------------------------------

class TestFilterMethods:
    def _make_games(self):
        now = datetime.now(timezone.utc)
        return [
            {"is_live": True, "is_final": False, "is_upcoming": False, "home_abbr": "LAL", "away_abbr": "BOS", "start_time_utc": now},
            {"is_live": False, "is_final": True, "is_upcoming": False, "home_abbr": "MIA", "away_abbr": "PHX", "start_time_utc": now - timedelta(hours=3)},
            {"is_live": False, "is_final": False, "is_upcoming": True, "home_abbr": "DAL", "away_abbr": "CHI", "start_time_utc": now + timedelta(hours=2)},
        ]

    def test_filter_live_games(self, helper):
        games = self._make_games()
        result = helper.filter_live_games(games)
        assert len(result) == 1
        assert result[0]["home_abbr"] == "LAL"

    def test_filter_final_games(self, helper):
        games = self._make_games()
        result = helper.filter_final_games(games)
        assert len(result) == 1
        assert result[0]["home_abbr"] == "MIA"

    def test_filter_upcoming_games(self, helper):
        games = self._make_games()
        result = helper.filter_upcoming_games(games)
        assert len(result) == 1
        assert result[0]["home_abbr"] == "DAL"

    def test_filter_favorite_teams_match(self, helper):
        games = self._make_games()
        result = helper.filter_favorite_teams(games, ["LAL"])
        assert len(result) == 1
        assert result[0]["home_abbr"] == "LAL"

    def test_filter_favorite_teams_empty_list_returns_all(self, helper):
        games = self._make_games()
        result = helper.filter_favorite_teams(games, [])
        assert len(result) == 3

    def test_filter_favorite_teams_away_match(self, helper):
        games = self._make_games()
        result = helper.filter_favorite_teams(games, ["BOS"])
        assert len(result) == 1

    def test_filter_recent_games_within_window(self, helper):
        now = datetime.now(timezone.utc)
        games = [
            {"start_time_utc": now - timedelta(days=2), "is_final": True},
            {"start_time_utc": now - timedelta(days=10), "is_final": True},
        ]
        result = helper.filter_recent_games(games, days_back=7)
        assert len(result) == 1

    def test_filter_recent_games_all_within(self, helper):
        now = datetime.now(timezone.utc)
        games = [
            {"start_time_utc": now - timedelta(days=1)},
            {"start_time_utc": now - timedelta(days=3)},
        ]
        result = helper.filter_recent_games(games, days_back=7)
        assert len(result) == 2

    def test_sort_games_ascending(self, helper):
        now = datetime.now(timezone.utc)
        games = [
            {"start_time_utc": now + timedelta(hours=2), "id": "late"},
            {"start_time_utc": now + timedelta(hours=1), "id": "early"},
        ]
        result = helper.sort_games_by_time(games)
        assert result[0]["id"] == "early"

    def test_sort_games_descending(self, helper):
        now = datetime.now(timezone.utc)
        games = [
            {"start_time_utc": now + timedelta(hours=1), "id": "early"},
            {"start_time_utc": now + timedelta(hours=2), "id": "late"},
        ]
        result = helper.sort_games_by_time(games, reverse=True)
        assert result[0]["id"] == "late"


# ---------------------------------------------------------------------------
# process_games
# ---------------------------------------------------------------------------

class TestProcessGames:
    def test_processes_valid_events(self, helper):
        events = [
            _make_espn_event(event_id="1"),
            _make_espn_event(event_id="2"),
        ]
        result = helper.process_games(events)
        assert len(result) == 2

    def test_skips_invalid_events(self, helper):
        events = [
            _make_espn_event(event_id="1"),
            {},  # invalid
        ]
        result = helper.process_games(events)
        assert len(result) == 1

    def test_empty_events(self, helper):
        assert helper.process_games([]) == []


# ---------------------------------------------------------------------------
# get_game_summary
# ---------------------------------------------------------------------------

class TestGetGameSummary:
    def test_live_summary(self, helper):
        game = {
            "home_abbr": "LAL", "away_abbr": "BOS",
            "home_score": "105", "away_score": "98",
            "status_text": "Q4 2:30",
            "is_live": True, "is_final": False,
        }
        summary = helper.get_game_summary(game)
        assert "BOS" in summary
        assert "LAL" in summary
        assert "98" in summary
        assert "105" in summary

    def test_final_summary(self, helper):
        game = {
            "home_abbr": "LAL", "away_abbr": "BOS",
            "home_score": "110", "away_score": "102",
            "status_text": "Final",
            "is_live": False, "is_final": True,
        }
        summary = helper.get_game_summary(game)
        assert "Final" in summary

    def test_upcoming_summary(self, helper):
        game = {
            "home_abbr": "LAL", "away_abbr": "BOS",
            "home_score": "0", "away_score": "0",
            "status_text": "7:30 PM",
            "is_live": False, "is_final": False,
        }
        summary = helper.get_game_summary(game)
        assert "7:30 PM" in summary
