"""Tests for the methods promoted onto SportsUpcoming / SportsRecent /
SportsLive from the nine plugin copies (phase B1 of the sports unification;
see docs/SPORTS_UNIFICATION.md).

Covered promotions:
- SportsRecent: `_get_zero_clock_duration` / `_clear_zero_clock_tracking`
  (+ the `_zero_clock_timestamps` initializer).
- SportsLive: `_is_game_really_over` / `_detect_stale_games`
  (+ `game_update_timestamps` / `stale_game_timeout`, and the
  `FINAL_PERIOD` / `CLOCK_COUNTS_DOWN` class attributes).
- SportsUpcoming: `_select_games_for_display`.
- SportsRecent: `_select_recent_games_for_display`.

The live pair is the risk centre: `_detect_stale_games` is the only caller
that *removes* games, so `_is_game_really_over` returning a false positive
silently drops a live game from the display. The canonical form deliberately
declines to treat a missing clock as 0:00 — the plugin variant that did so
dropped clockless sports (baseball) from the FINAL_PERIOD-th period onward.
That regression is pinned by
`test_missing_clock_at_late_period_is_not_over`.
"""

import logging
import sys
import time
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest
import requests
from freezegun import freeze_time
from PIL import Image

# src.base_classes.sports transitively imports the hardware matrix driver;
# stub it so these tests can import the sports base classes off-device.
sys.modules.setdefault("rgbmatrix", MagicMock())

from src.base_classes.hockey import Hockey, HockeyLive
from src.base_classes.sports import (
    SportsCore,
    SportsLive,
    SportsRecent,
    SportsUpcoming,
)


# ---------------------------------------------------------------------------
# Harnesses
# ---------------------------------------------------------------------------

class _UpcomingHarness(Hockey, SportsUpcoming):
    """Cheapest concrete SportsUpcoming: hockey extractor + cache-fed data.

    `_favorite_key` is inherited from SportsCore — these harnesses
    deliberately do NOT define it, so the selection tests exercise the real
    seam rather than a local stand-in.
    """

    def _fetch_data(self):
        return None


class _RecentHarness(Hockey, SportsRecent):
    def _fetch_data(self):
        return None


class _LiveHarness(HockeyLive):
    def _fetch_data(self):
        return None


class _ThreePeriodLiveHarness(_LiveHarness):
    """Hockey-shaped: regulation ends after period 3."""

    FINAL_PERIOD = 3


class _CountUpLiveHarness(_LiveHarness):
    """Soccer/AFL/NRL-shaped: the clock counts up, so 0:00 is kickoff."""

    CLOCK_COUNTS_DOWN = False


class _IdFavoriteUpcomingHarness(_UpcomingHarness):
    """NRL-shaped: abbreviations are ambiguous, so favorites match on team id."""

    def _favorite_key(self, game, side):
        team_id = game.get(f"{side}_id")
        return str(team_id) if team_id is not None else None


class _IdFavoriteRecentHarness(_RecentHarness):
    def _favorite_key(self, game, side):
        team_id = game.get(f"{side}_id")
        return str(team_id) if team_id is not None else None


@pytest.fixture
def build_manager(monkeypatch, tmp_path):
    """Factory for concrete sports managers: mocked display/cache managers,
    logo dir redirected to tmp, background service stubbed, and the requests
    session rigged to prove nothing hits the network."""
    monkeypatch.setattr(
        SportsCore, "_initialize_logo_dir", lambda self, configured: tmp_path)
    monkeypatch.setattr(
        "src.base_classes.sports.core.get_background_service",
        lambda *args, **kwargs: MagicMock())

    def build(cls, **mode_cfg):
        config = {
            "timezone": "UTC",
            "display": {},
            "nhl_scoreboard": {"enabled": True, **mode_cfg},
        }
        display_manager = MagicMock()
        display_manager.matrix.width = 128
        display_manager.matrix.height = 32
        display_manager.width = 128
        display_manager.height = 32
        display_manager.image = Image.new("RGB", (128, 32))
        cache_manager = MagicMock()
        cache_manager.get.return_value = None
        cache_manager.cache_dir = str(tmp_path)
        manager = cls(config, display_manager, cache_manager,
                      logging.getLogger("test_sports_modes_promotions"),
                      "nhl")
        manager.session = MagicMock()
        manager.session.get.side_effect = requests.exceptions.ConnectionError(
            "promotion tests are offline")
        return manager

    return build


def game(game_id="1", home="BOS", away="TOR", start=None, home_id=None,
         away_id=None, **extra):
    g = {
        "id": game_id,
        "home_abbr": home,
        "away_abbr": away,
        "home_id": home_id,
        "away_id": away_id,
        "start_time_utc": start,
    }
    g.update(extra)
    return g


def at(day, hour=12):
    return datetime(2026, 1, day, hour, tzinfo=timezone.utc)


def _ids(games):
    return [g["id"] for g in games]


# ---------------------------------------------------------------------------
# Tier 1 — zero-clock tracking (SportsRecent)
# ---------------------------------------------------------------------------

class TestZeroClockTracking:
    def test_initializer_present_and_empty(self, build_manager):
        manager = build_manager(_RecentHarness)
        assert manager._zero_clock_timestamps == {}

    def test_first_call_returns_zero_and_starts_tracking(self, build_manager):
        manager = build_manager(_RecentHarness)
        assert manager._get_zero_clock_duration("g1") == 0.0
        assert "g1" in manager._zero_clock_timestamps

    def test_subsequent_call_returns_elapsed_seconds(self, build_manager):
        manager = build_manager(_RecentHarness)
        with freeze_time("2026-01-20 12:00:00") as frozen:
            assert manager._get_zero_clock_duration("g1") == 0.0
            frozen.tick(45)
            assert manager._get_zero_clock_duration("g1") == pytest.approx(45.0)
            frozen.tick(15)
            assert manager._get_zero_clock_duration("g1") == pytest.approx(60.0)

    def test_tracking_is_per_game(self, build_manager):
        manager = build_manager(_RecentHarness)
        with freeze_time("2026-01-20 12:00:00") as frozen:
            manager._get_zero_clock_duration("g1")
            frozen.tick(30)
            assert manager._get_zero_clock_duration("g2") == 0.0
            assert manager._get_zero_clock_duration("g1") == pytest.approx(30.0)

    def test_clear_resets_tracking(self, build_manager):
        manager = build_manager(_RecentHarness)
        with freeze_time("2026-01-20 12:00:00") as frozen:
            manager._get_zero_clock_duration("g1")
            frozen.tick(30)
            manager._clear_zero_clock_tracking("g1")
            assert "g1" not in manager._zero_clock_timestamps
            # Restarts from zero after clearing.
            assert manager._get_zero_clock_duration("g1") == 0.0

    def test_clear_unknown_game_is_a_noop(self, build_manager):
        manager = build_manager(_RecentHarness)
        manager._clear_zero_clock_tracking("never-seen")  # must not raise
        assert manager._zero_clock_timestamps == {}


# ---------------------------------------------------------------------------
# Tier 2a — _is_game_really_over (SportsLive)
# ---------------------------------------------------------------------------

class TestIsGameReallyOver:
    def test_missing_clock_at_late_period_is_not_over(self, build_manager):
        """THE baseball regression: no `clock` key at all, period 7.

        The rejected variant coerced a missing clock to the literal "0:00" and
        declared the game over — dropping every MLB game from the 5th inning
        onward, since baseball has no game clock and `period` is the inning.
        A missing clock must fail safe.
        """
        manager = build_manager(_LiveHarness)
        g = game(period=7, period_text="Top 7th")
        assert "clock" not in g
        assert manager._is_game_really_over(g) is False

    def test_none_clock_at_late_period_is_not_over(self, build_manager):
        manager = build_manager(_LiveHarness)
        assert manager._is_game_really_over(
            game(clock=None, period=7, period_text="Top 7th")) is False

    def test_non_string_clock_at_late_period_is_not_over(self, build_manager):
        manager = build_manager(_LiveHarness)
        assert manager._is_game_really_over(
            game(clock=0, period=9, period_text="Bot 9th")) is False

    def test_blank_clock_at_late_period_is_not_over(self, build_manager):
        manager = build_manager(_LiveHarness)
        assert manager._is_game_really_over(
            game(clock="   ", period=5, period_text="5th")) is False

    @pytest.mark.parametrize("period_text", ["Final", "final", "Final/OT",
                                             "FINAL", "Final - SO"])
    def test_final_period_text_is_over(self, build_manager, period_text):
        manager = build_manager(_LiveHarness)
        assert manager._is_game_really_over(
            game(clock="12:00", period=2, period_text=period_text)) is True

    def test_none_period_text_does_not_raise(self, build_manager):
        """All nine plugin copies called `.lower()` on `game.get("period_text", "")`,
        which is None when the key is present-but-None; `_detect_stale_games`
        has no try/except around the call."""
        manager = build_manager(_LiveHarness)
        assert manager._is_game_really_over(
            game(clock="12:00", period=2, period_text=None)) is False

    def test_missing_period_text_does_not_raise(self, build_manager):
        manager = build_manager(_LiveHarness)
        assert manager._is_game_really_over(game(clock="12:00", period=2)) is False

    @pytest.mark.parametrize(
        "clock", ["0:00", ":00", "00", "000", " 0:00 ", "00:00", "0", "0000"])
    def test_expired_clock_at_final_period_is_over(self, build_manager, clock):
        """Every spelling of a zeroed clock counts, not a hand-listed few.

        "00:00" is the one that motivated comparing numerically: it normalizes
        to "0000", which matched none of the literals the plugin copies listed,
        so a two-digit-minute expired clock kept the game on screen forever.
        """
        manager = build_manager(_LiveHarness)
        assert manager._is_game_really_over(
            game(clock=clock, period=4, period_text="Q4")) is True

    def test_none_period_at_expired_clock_does_not_raise(self, build_manager):
        """`period` present-but-None: `None >= FINAL_PERIOD` is a TypeError, and
        `_detect_stale_games` has no try/except — the same failure shape as the
        `period_text` case above."""
        manager = build_manager(_LiveHarness)
        assert manager._is_game_really_over(
            game(clock="0:00", period=None, period_text="Q4")) is False

    def test_none_period_with_running_clock_does_not_raise(self, build_manager):
        manager = build_manager(_LiveHarness)
        assert manager._is_game_really_over(
            game(clock="8:12", period=None, period_text="Q2")) is False

    def test_expired_clock_after_final_period_is_over(self, build_manager):
        manager = build_manager(_LiveHarness)
        assert manager._is_game_really_over(
            game(clock="0:00", period=5, period_text="OT")) is True

    def test_expired_clock_before_final_period_is_not_over(self, build_manager):
        manager = build_manager(_LiveHarness)
        assert manager._is_game_really_over(
            game(clock="0:00", period=3, period_text="Q3")) is False

    @pytest.mark.parametrize("clock", [":40", "0:40", "1:00"])
    def test_running_clock_is_not_over(self, build_manager, clock):
        """Sub-minute clocks like ':40' are legitimate, not expired."""
        manager = build_manager(_LiveHarness)
        assert manager._is_game_really_over(
            game(clock=clock, period=4, period_text="Q4")) is False

    def test_defaults_are_four_period_countdown(self):
        assert SportsLive.FINAL_PERIOD == 4
        assert SportsLive.CLOCK_COUNTS_DOWN is True

    def test_final_period_override_three(self, build_manager):
        """Hockey-shaped subclass: regulation ends after period 3."""
        manager = build_manager(_ThreePeriodLiveHarness)
        assert manager.FINAL_PERIOD == 3
        assert manager._is_game_really_over(
            game(clock="0:00", period=3, period_text="P3")) is True
        assert manager._is_game_really_over(
            game(clock="0:00", period=2, period_text="P2")) is False
        # And the unmodified default still requires period 4.
        assert build_manager(_LiveHarness)._is_game_really_over(
            game(clock="0:00", period=3, period_text="P3")) is False

    def test_count_up_clock_never_expires(self, build_manager):
        """Soccer/AFL/NRL: 0:00 means kickoff, so the clock branch must not run."""
        manager = build_manager(_CountUpLiveHarness)
        assert manager.CLOCK_COUNTS_DOWN is False
        for period in (1, 2, 4, 9):
            assert manager._is_game_really_over(
                game(clock="0:00", period=period, period_text="1st Half")) is False

    def test_count_up_clock_still_honors_final_text(self, build_manager):
        manager = build_manager(_CountUpLiveHarness)
        assert manager._is_game_really_over(
            game(clock="0:00", period=2, period_text="Final")) is True


# ---------------------------------------------------------------------------
# Tier 2b — _detect_stale_games (SportsLive)
# ---------------------------------------------------------------------------

class TestDetectStaleGames:
    def test_initializer_defaults(self, build_manager):
        manager = build_manager(_LiveHarness)
        assert manager.game_update_timestamps == {}
        assert manager.stale_game_timeout == 300

    def test_stale_timeout_is_configurable(self, build_manager):
        manager = build_manager(_LiveHarness, stale_game_timeout=42)
        assert manager.stale_game_timeout == 42

    def test_mutates_caller_list_in_place_and_returns_none(self, build_manager):
        manager = build_manager(_LiveHarness)
        fresh = game("1", period_text="P2", clock="10:00", period=2)
        over = game("2", period_text="Final", clock="0:00", period=3)
        games = [fresh, over]
        original = games

        result = manager._detect_stale_games(games)

        assert result is None
        assert games is original  # same object, mutated in place
        assert _ids(games) == ["1"]

    def test_evicts_only_past_timeout_games(self, build_manager):
        manager = build_manager(_LiveHarness)
        with freeze_time("2026-01-20 12:00:00"):
            now = time.time()
            manager.game_update_timestamps = {
                "1": {"last_seen": now - 10},    # fresh
                "2": {"last_seen": now - 299},   # just inside the timeout
                "3": {"last_seen": now - 301},   # past the timeout
            }
            games = [game("1", period_text="P1", clock="10:00", period=1),
                     game("2", period_text="P1", clock="10:00", period=1),
                     game("3", period_text="P1", clock="10:00", period=1)]
            manager._detect_stale_games(games)

        assert _ids(games) == ["1", "2"]
        assert "3" not in manager.game_update_timestamps
        assert set(manager.game_update_timestamps) == {"1", "2"}

    def test_unknown_last_seen_is_never_stale(self, build_manager):
        """last_seen == 0 (or no entry) means 'never recorded', not 'ancient'."""
        manager = build_manager(_LiveHarness)
        games = [game("1", period_text="P1", clock="10:00", period=1),
                 game("2", period_text="P1", clock="10:00", period=1)]
        manager.game_update_timestamps = {"1": {"last_seen": 0}}
        manager._detect_stale_games(games)
        assert _ids(games) == ["1", "2"]

    def test_removes_games_that_are_really_over(self, build_manager):
        manager = build_manager(_LiveHarness)
        manager.game_update_timestamps = {"2": {"last_seen": 0}}
        games = [game("1", period_text="Q2", clock="5:00", period=2),
                 game("2", period_text="Final", clock="0:00", period=4)]
        manager._detect_stale_games(games)
        assert _ids(games) == ["1"]
        assert "2" not in manager.game_update_timestamps

    def test_keeps_clockless_late_game(self, build_manager):
        """The end-to-end form of the baseball regression: a clockless game in
        the 7th must survive the removal path."""
        manager = build_manager(_LiveHarness)
        games = [game("mlb-1", period=7, period_text="Top 7th")]
        manager._detect_stale_games(games)
        assert _ids(games) == ["mlb-1"]

    def test_games_without_id_are_skipped(self, build_manager):
        manager = build_manager(_LiveHarness)
        no_id = {"home_abbr": "BOS", "away_abbr": "TOR",
                 "period_text": "Final", "clock": "0:00", "period": 4}
        games = [no_id]
        manager._detect_stale_games(games)
        # `continue` fires before the "really over" check, so it stays.
        assert games == [no_id]

    def test_empty_list_is_tolerated(self, build_manager):
        manager = build_manager(_LiveHarness)
        games = []
        assert manager._detect_stale_games(games) is None
        assert games == []

    def test_removal_is_by_value_not_identity(self, build_manager):
        """Sharp edge worth pinning: `list.remove` compares with `dict.__eq__`,
        so two structurally-equal dicts drop the FIRST occurrence."""
        manager = build_manager(_LiveHarness)
        first = game("1", period_text="Final", clock="0:00", period=4)
        twin = dict(first)
        games = [first, twin]
        manager._detect_stale_games(games)
        # Both entries are removed here (two iterations, two removals), but the
        # first removal deletes `first`, not the dict being iterated.
        assert games == []


# ---------------------------------------------------------------------------
# Tier 3a — _select_games_for_display (SportsUpcoming)
# ---------------------------------------------------------------------------

class TestSelectGamesForDisplay:
    def test_no_favorites_returns_all_sorted_ascending(self, build_manager):
        manager = build_manager(_UpcomingHarness)
        games = [game("late", start=at(20)), game("early", start=at(10)),
                 game("mid", start=at(15))]
        assert _ids(manager._select_games_for_display(games, [])) == [
            "early", "mid", "late"]

    def test_missing_start_time_sorts_last(self, build_manager):
        manager = build_manager(_UpcomingHarness)
        games = [game("none", start=None), game("early", start=at(10))]
        assert _ids(manager._select_games_for_display(games, [])) == [
            "early", "none"]

    def test_filters_to_favorite_teams(self, build_manager):
        manager = build_manager(_UpcomingHarness)
        games = [game("1", home="BOS", away="TOR", start=at(10)),
                 game("2", home="NYR", away="PIT", start=at(11)),
                 game("3", home="MTL", away="BOS", start=at(12))]
        assert _ids(manager._select_games_for_display(games, ["BOS"])) == ["1", "3"]

    def test_respects_upcoming_games_to_show_per_team(self, build_manager):
        manager = build_manager(_UpcomingHarness, upcoming_games_to_show=2)
        games = [game(str(i), home="BOS", away="TOR", start=at(10 + i))
                 for i in range(5)]
        assert _ids(manager._select_games_for_display(games, ["BOS"])) == ["0", "1"]

    def test_game_between_two_favorites_counts_for_both(self, build_manager):
        manager = build_manager(_UpcomingHarness, upcoming_games_to_show=1)
        games = [game("shared", home="BOS", away="TOR", start=at(10)),
                 game("bos2", home="BOS", away="NYR", start=at(11)),
                 game("tor2", home="TOR", away="PIT", start=at(12))]
        # "shared" fills both BOS's and TOR's single slot, so nothing else fits.
        assert _ids(manager._select_games_for_display(
            games, ["BOS", "TOR"])) == ["shared"]

    def test_deduplicates_by_game_id(self, build_manager):
        manager = build_manager(_UpcomingHarness, upcoming_games_to_show=5)
        g = game("dupe", home="BOS", away="TOR", start=at(10))
        assert _ids(manager._select_games_for_display(
            [g, dict(g)], ["BOS"])) == ["dupe"]

    def test_non_favorite_games_excluded(self, build_manager):
        manager = build_manager(_UpcomingHarness)
        games = [game("1", home="NYR", away="PIT", start=at(10))]
        assert manager._select_games_for_display(games, ["BOS"]) == []

    def test_favorite_key_seam_supports_id_matching(self, build_manager):
        """The NRL case: two clubs share the abbreviation 'NEW', so favorites
        must be matched on team id. Only the seam changes — the promoted
        method is identical."""
        games = [
            game("knights", home="NEW", away="SYD", home_id=1, away_id=2,
                 start=at(10)),
            game("warriors", home="NEW", away="SYD", home_id=99, away_id=2,
                 start=at(11)),
        ]
        abbr_manager = build_manager(_UpcomingHarness)
        # Abbreviation matching cannot tell the two "NEW" clubs apart.
        assert _ids(abbr_manager._select_games_for_display(games, ["NEW"])) == [
            "knights", "warriors"]

        id_manager = build_manager(_IdFavoriteUpcomingHarness)
        assert _ids(id_manager._select_games_for_display(games, ["99"])) == [
            "warriors"]

    def test_favorite_key_none_never_matches(self, build_manager):
        """`_favorite_key` returning None (missing id) must not match, even
        against a favorites list holding the string 'None'."""
        manager = build_manager(_IdFavoriteUpcomingHarness)
        games = [game("1", home="BOS", away="TOR", start=at(10))]  # no ids
        assert manager._select_games_for_display(games, ["None"]) == []


# ---------------------------------------------------------------------------
# Tier 3b — _select_recent_games_for_display (SportsRecent)
# ---------------------------------------------------------------------------

class TestSelectRecentGamesForDisplay:
    def test_no_favorites_returns_all_sorted_descending(self, build_manager):
        manager = build_manager(_RecentHarness)
        games = [game("early", start=at(10)), game("late", start=at(20)),
                 game("mid", start=at(15))]
        assert _ids(manager._select_recent_games_for_display(games, [])) == [
            "late", "mid", "early"]

    def test_missing_start_time_sorts_last(self, build_manager):
        manager = build_manager(_RecentHarness)
        games = [game("none", start=None), game("late", start=at(20))]
        assert _ids(manager._select_recent_games_for_display(games, [])) == [
            "late", "none"]

    def test_respects_recent_games_to_show_per_team(self, build_manager):
        manager = build_manager(_RecentHarness, recent_games_to_show=2)
        games = [game(str(i), home="BOS", away="TOR", start=at(10 + i))
                 for i in range(5)]
        # Most recent first.
        assert _ids(manager._select_recent_games_for_display(
            games, ["BOS"])) == ["4", "3"]

    def test_game_between_two_favorites_counts_for_both(self, build_manager):
        manager = build_manager(_RecentHarness, recent_games_to_show=1)
        games = [game("shared", home="BOS", away="TOR", start=at(20)),
                 game("bos2", home="BOS", away="NYR", start=at(19)),
                 game("tor2", home="TOR", away="PIT", start=at(18))]
        assert _ids(manager._select_recent_games_for_display(
            games, ["BOS", "TOR"])) == ["shared"]

    def test_deduplicates_by_game_id(self, build_manager):
        manager = build_manager(_RecentHarness, recent_games_to_show=5)
        g = game("dupe", home="BOS", away="TOR", start=at(10))
        assert _ids(manager._select_recent_games_for_display(
            [g, dict(g)], ["BOS"])) == ["dupe"]

    def test_non_favorite_games_excluded(self, build_manager):
        manager = build_manager(_RecentHarness)
        games = [game("1", home="NYR", away="PIT", start=at(10))]
        assert manager._select_recent_games_for_display(games, ["BOS"]) == []

    def test_favorite_key_seam_supports_id_matching(self, build_manager):
        games = [
            game("knights", home="NEW", away="SYD", home_id=1, away_id=2,
                 start=at(10)),
            game("warriors", home="NEW", away="SYD", home_id=99, away_id=2,
                 start=at(11)),
        ]
        id_manager = build_manager(_IdFavoriteRecentHarness)
        assert _ids(id_manager._select_recent_games_for_display(
            games, ["99"])) == ["warriors"]


# ---------------------------------------------------------------------------
# Seam / inertness guards
# ---------------------------------------------------------------------------

class TestPromotionShape:
    def test_promoted_methods_live_on_the_right_classes(self):
        assert hasattr(SportsRecent, "_get_zero_clock_duration")
        assert hasattr(SportsRecent, "_clear_zero_clock_tracking")
        assert hasattr(SportsRecent, "_select_recent_games_for_display")
        assert hasattr(SportsUpcoming, "_select_games_for_display")
        assert hasattr(SportsLive, "_is_game_really_over")
        assert hasattr(SportsLive, "_detect_stale_games")

    def test_modes_does_not_define_the_favorite_key_seam(self):
        """`_favorite_key` is a SportsCore override point. modes.py must call
        it, never define it — this test fails if the seam is added in the
        wrong file."""
        import src.base_classes.sports.modes as modes

        for cls in (SportsUpcoming, SportsRecent, SportsLive):
            assert "_favorite_key" not in vars(cls)
        assert "_favorite_key" not in modes.__dict__
