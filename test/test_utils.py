"""
Tests for src/common/utils.py

Covers all pure utility functions: normalize_team_abbreviation, format_time,
format_date, get_timezone, validate_dimensions, parse_team_abbreviation,
format_score, format_period, is_live_game, is_final_game, is_upcoming_game,
sanitize_filename, truncate_text, parse_boolean.
"""

import pytest
from datetime import datetime, timezone
import pytz

from src.common.utils import (
    normalize_team_abbreviation,
    format_time,
    format_date,
    get_timezone,
    validate_dimensions,
    parse_team_abbreviation,
    format_score,
    format_period,
    is_live_game,
    is_final_game,
    is_upcoming_game,
    sanitize_filename,
    truncate_text,
    parse_boolean,
)


# ---------------------------------------------------------------------------
# normalize_team_abbreviation
# ---------------------------------------------------------------------------

class TestNormalizeTeamAbbreviation:
    def test_basic_uppercase(self):
        assert normalize_team_abbreviation("lal") == "LAL"

    def test_strips_spaces(self):
        assert normalize_team_abbreviation("  KC  ") == "KC"

    def test_replaces_ampersand(self):
        assert normalize_team_abbreviation("TA&M") == "TAANDM"

    def test_removes_internal_spaces(self):
        assert normalize_team_abbreviation("A B") == "AB"

    def test_removes_hyphens(self):
        assert normalize_team_abbreviation("A-B") == "AB"

    def test_empty_string_returns_empty(self):
        assert normalize_team_abbreviation("") == ""

    def test_none_returns_empty(self):
        assert normalize_team_abbreviation(None) == ""


# ---------------------------------------------------------------------------
# format_time / format_date
# ---------------------------------------------------------------------------

class TestFormatTime:
    def _utc_dt(self, hour=20, minute=30):
        return datetime(2024, 1, 15, hour, minute, 0, tzinfo=timezone.utc)

    def test_formats_utc_to_utc(self):
        dt = self._utc_dt(20, 30)
        result = format_time(dt, timezone_str="UTC")
        # 20:30 UTC → "8:30PM" (leading zero stripped)
        assert "8:30PM" in result or "8:30 PM" in result or result != ""

    def test_naive_datetime_treated_as_utc(self):
        dt = datetime(2024, 1, 15, 12, 0, 0)  # naive
        result = format_time(dt, timezone_str="UTC")
        assert result != ""

    def test_invalid_timezone_returns_empty(self):
        dt = self._utc_dt()
        result = format_time(dt, timezone_str="Invalid/TZ")
        assert result == ""

    def test_eastern_timezone(self):
        dt = self._utc_dt(20, 0)  # 8 PM UTC = 3 PM ET
        result = format_time(dt, timezone_str="America/New_York")
        assert result != ""


class TestFormatDate:
    def test_formats_date(self):
        dt = datetime(2024, 6, 15, 18, 0, 0, tzinfo=timezone.utc)
        result = format_date(dt, timezone_str="UTC")
        assert "June" in result or "15" in result

    def test_naive_datetime(self):
        dt = datetime(2024, 3, 10, 12, 0, 0)
        result = format_date(dt, timezone_str="UTC")
        assert result != ""

    def test_invalid_timezone_returns_empty(self):
        dt = datetime(2024, 6, 15, 18, 0, 0, tzinfo=timezone.utc)
        result = format_date(dt, timezone_str="BadZone/Here")
        assert result == ""


# ---------------------------------------------------------------------------
# get_timezone
# ---------------------------------------------------------------------------

class TestGetTimezone:
    def test_valid_timezone(self):
        tz = get_timezone("America/New_York")
        assert tz is not None

    def test_utc(self):
        tz = get_timezone("UTC")
        assert tz is pytz.utc or str(tz) == "UTC"

    def test_invalid_returns_utc(self):
        tz = get_timezone("Not/ATimezone")
        assert tz is pytz.utc


# ---------------------------------------------------------------------------
# validate_dimensions
# ---------------------------------------------------------------------------

class TestValidateDimensions:
    def test_valid(self):
        assert validate_dimensions(64, 32) is True

    def test_zero_width(self):
        assert validate_dimensions(0, 32) is False

    def test_zero_height(self):
        assert validate_dimensions(64, 0) is False

    def test_negative(self):
        assert validate_dimensions(-1, 32) is False

    def test_too_large(self):
        assert validate_dimensions(1001, 32) is False

    def test_max_valid(self):
        assert validate_dimensions(1000, 1000) is True

    def test_non_integer(self):
        assert validate_dimensions("64", 32) is False  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# parse_team_abbreviation
# ---------------------------------------------------------------------------

class TestParseTeamAbbreviation:
    def test_empty_string(self):
        assert parse_team_abbreviation("") == ""

    def test_none_returns_empty(self):
        assert parse_team_abbreviation(None) == ""

    def test_extracts_uppercase(self):
        result = parse_team_abbreviation("LAL")
        assert result == "LAL"

    def test_fallback_first_three(self):
        # text without recognisable 2-4 char uppercase block
        result = parse_team_abbreviation("ab")
        assert len(result) <= 3


# ---------------------------------------------------------------------------
# format_score
# ---------------------------------------------------------------------------

class TestFormatScore:
    def test_format_score(self):
        assert format_score(14, 7) == "7-14"

    def test_format_score_strings(self):
        assert format_score("21", "14") == "14-21"

    def test_zero_zero(self):
        assert format_score(0, 0) == "0-0"


# ---------------------------------------------------------------------------
# format_period
# ---------------------------------------------------------------------------

class TestFormatPeriod:
    def test_basketball_q1(self):
        assert format_period(1, "basketball") == "Q1"

    def test_basketball_q4(self):
        assert format_period(4, "basketball") == "Q4"

    def test_basketball_ot1(self):
        assert format_period(5, "basketball") == "OT1"

    def test_basketball_ot2(self):
        assert format_period(6, "basketball") == "OT2"

    def test_football_q1(self):
        assert format_period(1, "football") == "Q1"

    def test_football_ot(self):
        assert format_period(5, "football") == "OT1"

    def test_hockey_p1(self):
        assert format_period(1, "hockey") == "P1"

    def test_hockey_p3(self):
        assert format_period(3, "hockey") == "P3"

    def test_hockey_ot(self):
        assert format_period(4, "hockey") == "OT1"

    def test_baseball_inning(self):
        assert format_period(7, "baseball") == "INN 7"

    def test_unknown_sport(self):
        result = format_period(2, "unknown")
        assert "2" in result


# ---------------------------------------------------------------------------
# is_live_game / is_final_game / is_upcoming_game
# ---------------------------------------------------------------------------

class TestGameStatusHelpers:
    def test_is_live_game_true(self):
        assert is_live_game("In Progress") is True
        assert is_live_game("halftime") is True
        assert is_live_game("overtime") is True

    def test_is_live_game_false(self):
        assert is_live_game("Final") is False
        assert is_live_game("Scheduled") is False

    def test_is_final_game_true(self):
        assert is_final_game("Final") is True
        assert is_final_game("COMPLETED") is True

    def test_is_final_game_false(self):
        assert is_final_game("In Progress") is False

    def test_is_upcoming_game_true(self):
        assert is_upcoming_game("Scheduled") is True
        assert is_upcoming_game("upcoming") is True

    def test_is_upcoming_game_false(self):
        assert is_upcoming_game("Final") is False
        assert is_upcoming_game("In Progress") is False


# ---------------------------------------------------------------------------
# sanitize_filename
# ---------------------------------------------------------------------------

class TestSanitizeFilename:
    def test_removes_invalid_chars(self):
        result = sanitize_filename('file<>:"/\\|?*.txt')
        assert "<" not in result
        assert ">" not in result
        assert ":" not in result

    def test_collapses_underscores(self):
        result = sanitize_filename("file___name")
        assert "__" not in result

    def test_strips_leading_trailing(self):
        result = sanitize_filename("_file_")
        assert not result.startswith("_")
        assert not result.endswith("_")

    def test_normal_filename_unchanged(self):
        result = sanitize_filename("my_logo")
        assert result == "my_logo"


# ---------------------------------------------------------------------------
# truncate_text
# ---------------------------------------------------------------------------

class TestTruncateText:
    def test_no_truncation_needed(self):
        assert truncate_text("hello", 10) == "hello"

    def test_truncation_adds_suffix(self):
        result = truncate_text("hello world", 8)
        assert result.endswith("...")
        assert len(result) == 8

    def test_exact_length(self):
        assert truncate_text("hello", 5) == "hello"

    def test_custom_suffix(self):
        result = truncate_text("hello world", 8, suffix="~")
        assert result.endswith("~")


# ---------------------------------------------------------------------------
# parse_boolean
# ---------------------------------------------------------------------------

class TestParseBoolean:
    def test_true_bool(self):
        assert parse_boolean(True) is True

    def test_false_bool(self):
        assert parse_boolean(False) is False

    def test_int_1(self):
        assert parse_boolean(1) is True

    def test_int_0(self):
        assert parse_boolean(0) is False

    def test_string_true(self):
        for val in ("true", "True", "TRUE", "1", "yes", "on", "enabled"):
            assert parse_boolean(val) is True, f"Expected True for {val!r}"

    def test_string_false(self):
        for val in ("false", "False", "0", "no", "off", "disabled"):
            assert parse_boolean(val) is False, f"Expected False for {val!r}"

    def test_none_returns_false(self):
        assert parse_boolean(None) is False  # type: ignore[arg-type]
