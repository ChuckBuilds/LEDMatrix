"""
Tests for src/base_classes/api_extractors.py

Covers ESPNFootballExtractor, ESPNBaseballExtractor, ESPNHockeyExtractor,
SoccerAPIExtractor, and the shared _extract_common_details logic.
"""

import logging
import pytest
from src.base_classes.api_extractors import (
    ESPNFootballExtractor,
    ESPNBaseballExtractor,
    ESPNHockeyExtractor,
    SoccerAPIExtractor,
)


# ---------------------------------------------------------------------------
# Shared test data factories
# ---------------------------------------------------------------------------

def _make_espn_event(state: str = "in", home_abbr: str = "KC", away_abbr: str = "BUF",
                     home_score: str = "14", away_score: str = "7",
                     date_str: str = "2024-01-15T20:00:00Z",
                     include_situation: bool = False,
                     situation: dict | None = None,
                     status_detail: str = "2nd Qtr 8:42",
                     period: int = 2) -> dict:
    """Build a minimal ESPN-style game event dict."""
    comp_status = {
        "type": {
            "state": state,
            "shortDetail": status_detail,
            "detail": status_detail,
            "name": "STATUS_IN_PROGRESS",
        },
        "period": period,
        "displayClock": "8:42",
    }
    comp = {
        "status": comp_status,
        "competitors": [
            {
                "homeAway": "home",
                "team": {"abbreviation": home_abbr, "displayName": f"{home_abbr} Team"},
                "score": home_score,
            },
            {
                "homeAway": "away",
                "team": {"abbreviation": away_abbr, "displayName": f"{away_abbr} Team"},
                "score": away_score,
            },
        ],
    }
    if include_situation:
        comp["situation"] = situation or {}
    return {
        "id": "test-game-1",
        "date": date_str,
        "competitions": [comp],
    }


def _make_logger() -> logging.Logger:
    return logging.getLogger("test_extractor")


# ---------------------------------------------------------------------------
# ESPNFootballExtractor
# ---------------------------------------------------------------------------

class TestESPNFootballExtractor:
    def setup_method(self):
        self.extractor = ESPNFootballExtractor(_make_logger())

    def test_extract_live_game_basic_fields(self):
        event = _make_espn_event(state="in", home_score="14", away_score="7")
        result = self.extractor.extract_game_details(event)
        assert result is not None
        assert result["home_abbr"] == "KC"
        assert result["away_abbr"] == "BUF"
        assert result["home_score"] == "14"
        assert result["away_score"] == "7"
        assert result["is_live"] is True
        assert result["is_final"] is False
        assert result["is_upcoming"] is False

    def test_extract_final_game(self):
        event = _make_espn_event(state="post")
        result = self.extractor.extract_game_details(event)
        assert result is not None
        assert result["is_final"] is True
        assert result["is_live"] is False

    def test_extract_upcoming_game(self):
        event = _make_espn_event(state="pre")
        result = self.extractor.extract_game_details(event)
        assert result is not None
        assert result["is_upcoming"] is True

    def test_sport_specific_fields_default_when_pregame(self):
        event = _make_espn_event(state="pre")
        fields = self.extractor.get_sport_specific_fields(event)
        assert "down" in fields
        assert "distance" in fields
        assert "possession" in fields
        assert "is_redzone" in fields
        assert fields["is_redzone"] is False

    def test_sport_specific_fields_live_with_situation(self):
        situation = {
            "down": 3,
            "distance": 7,
            "possession": "KC",
            "isRedZone": True,
            "homeTimeouts": 2,
            "awayTimeouts": 1,
        }
        event = _make_espn_event(state="in", include_situation=True, situation=situation)
        fields = self.extractor.get_sport_specific_fields(event)
        assert fields["down"] == 3
        assert fields["distance"] == 7
        assert fields["is_redzone"] is True
        assert fields["home_timeouts"] == 2
        assert fields["away_timeouts"] == 1

    def test_scoring_event_detected(self):
        # situation must be non-empty (truthy) for the live block to execute
        situation = {"down": 1, "distance": 10}
        event = _make_espn_event(
            state="in",
            include_situation=True,
            situation=situation,
            status_detail="touchdown scored",
        )
        fields = self.extractor.get_sport_specific_fields(event)
        assert "touchdown" in fields.get("scoring_event", "").lower()

    def test_returns_none_on_empty_event(self):
        assert self.extractor.extract_game_details({}) is None

    def test_returns_none_when_teams_missing(self):
        event = {
            "id": "x",
            "date": "2024-01-15T20:00:00Z",
            "competitions": [
                {
                    "status": {"type": {"state": "in", "shortDetail": "", "detail": "", "name": ""}},
                    "competitors": [],  # no competitors
                }
            ],
        }
        assert self.extractor.extract_game_details(event) is None

    def test_date_z_suffix_parsed(self):
        event = _make_espn_event(date_str="2024-01-15T20:00:00Z")
        result = self.extractor.extract_game_details(event)
        # Should not raise and should return a result
        assert result is not None

    def test_id_propagated(self):
        event = _make_espn_event()
        result = self.extractor.extract_game_details(event)
        assert result["id"] == "test-game-1"


# ---------------------------------------------------------------------------
# ESPNBaseballExtractor
# ---------------------------------------------------------------------------

class TestESPNBaseballExtractor:
    def setup_method(self):
        self.extractor = ESPNBaseballExtractor(_make_logger())

    def test_extract_live_game(self):
        event = _make_espn_event(
            state="in", home_abbr="NYY", away_abbr="BOS",
            home_score="3", away_score="2"
        )
        result = self.extractor.extract_game_details(event)
        assert result is not None
        assert result["home_abbr"] == "NYY"
        assert result["is_live"] is True

    def test_baseball_sport_fields_defaults(self):
        event = _make_espn_event(state="pre")
        fields = self.extractor.get_sport_specific_fields(event)
        assert "inning" in fields
        assert "outs" in fields
        assert "bases" in fields
        assert "strikes" in fields
        assert "balls" in fields

    def test_baseball_sport_fields_live(self):
        situation = {
            "inning": 7,
            "outs": 2,
            "bases": "110",
            "strikes": 2,
            "balls": 3,
            "pitcher": "Smith",
            "batter": "Jones",
        }
        event = _make_espn_event(state="in", include_situation=True, situation=situation)
        fields = self.extractor.get_sport_specific_fields(event)
        assert fields["inning"] == 7
        assert fields["outs"] == 2
        assert fields["strikes"] == 2
        assert fields["pitcher"] == "Smith"

    def test_returns_none_on_empty(self):
        assert self.extractor.extract_game_details({}) is None


# ---------------------------------------------------------------------------
# ESPNHockeyExtractor
# ---------------------------------------------------------------------------

class TestESPNHockeyExtractor:
    def setup_method(self):
        self.extractor = ESPNHockeyExtractor(_make_logger())

    def test_extract_live_game(self):
        event = _make_espn_event(
            state="in", home_abbr="BOS", away_abbr="TOR",
            home_score="2", away_score="1"
        )
        result = self.extractor.extract_game_details(event)
        assert result is not None
        assert result["is_live"] is True

    def test_hockey_period_text_p1(self):
        situation = {"isPowerPlay": False}
        event = _make_espn_event(
            state="in", include_situation=True, situation=situation, period=1
        )
        fields = self.extractor.get_sport_specific_fields(event)
        assert fields["period_text"] == "P1"

    def test_hockey_period_text_p2(self):
        situation = {"isPowerPlay": False}  # non-empty so the live block executes
        event = _make_espn_event(
            state="in", include_situation=True, situation=situation, period=2
        )
        fields = self.extractor.get_sport_specific_fields(event)
        assert fields["period_text"] == "P2"

    def test_hockey_period_text_p3(self):
        situation = {"isPowerPlay": False}
        event = _make_espn_event(
            state="in", include_situation=True, situation=situation, period=3
        )
        fields = self.extractor.get_sport_specific_fields(event)
        assert fields["period_text"] == "P3"

    def test_hockey_period_text_ot(self):
        situation = {"isPowerPlay": False}
        event = _make_espn_event(
            state="in", include_situation=True, situation=situation, period=4
        )
        fields = self.extractor.get_sport_specific_fields(event)
        assert fields["period_text"] == "OT1"

    def test_hockey_power_play(self):
        situation = {"isPowerPlay": True, "homeShots": 12, "awayShots": 8}
        event = _make_espn_event(state="in", include_situation=True, situation=situation, period=2)
        fields = self.extractor.get_sport_specific_fields(event)
        assert fields["power_play"] is True
        assert fields["shots_on_goal"]["home"] == 12
        assert fields["shots_on_goal"]["away"] == 8

    def test_hockey_fields_defaults_pregame(self):
        event = _make_espn_event(state="pre")
        fields = self.extractor.get_sport_specific_fields(event)
        assert "period" in fields
        assert "power_play" in fields
        assert fields["power_play"] is False

    def test_returns_none_on_empty(self):
        assert self.extractor.extract_game_details({}) is None


# ---------------------------------------------------------------------------
# SoccerAPIExtractor
# ---------------------------------------------------------------------------

class TestSoccerAPIExtractor:
    def setup_method(self):
        self.extractor = SoccerAPIExtractor(_make_logger())

    def _make_soccer_event(self, is_live: bool = True) -> dict:
        return {
            "id": "soccer-1",
            "home_team": {"abbreviation": "ARS", "name": "Arsenal"},
            "away_team": {"abbreviation": "CHE", "name": "Chelsea"},
            "home_score": "2",
            "away_score": "1",
            "status": "LIVE",
            "is_live": is_live,
            "is_final": not is_live,
            "is_upcoming": False,
            "half": "1",
            "stoppage_time": "2",
            "home_yellow_cards": 1,
            "away_yellow_cards": 2,
            "home_red_cards": 0,
            "away_red_cards": 0,
            "home_possession": 55,
            "away_possession": 45,
        }

    def test_extract_live_game(self):
        event = self._make_soccer_event(is_live=True)
        result = self.extractor.extract_game_details(event)
        assert result is not None
        assert result["home_abbr"] == "ARS"
        assert result["away_abbr"] == "CHE"
        assert result["is_live"] is True

    def test_sport_specific_cards(self):
        event = self._make_soccer_event()
        fields = self.extractor.get_sport_specific_fields(event)
        assert fields["cards"]["home_yellow"] == 1
        assert fields["cards"]["away_yellow"] == 2
        assert fields["cards"]["home_red"] == 0

    def test_sport_specific_possession(self):
        event = self._make_soccer_event()
        fields = self.extractor.get_sport_specific_fields(event)
        assert fields["possession"]["home"] == 55
        assert fields["possession"]["away"] == 45

    def test_sport_specific_half(self):
        event = self._make_soccer_event()
        fields = self.extractor.get_sport_specific_fields(event)
        assert fields["half"] == "1"

    def test_scores_as_strings(self):
        event = self._make_soccer_event()
        result = self.extractor.extract_game_details(event)
        assert result["home_score"] == "2"
        assert result["away_score"] == "1"
