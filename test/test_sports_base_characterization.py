"""Characterization tests for src/base_classes/sports.py.

These tests PIN the current behavior of SportsCore / SportsUpcoming /
SportsRecent / SportsLive ahead of the sports-unification merge (features
from nine drifted plugin copies are about to be folded in). They assert
what the code DOES today, not what it should do — a few pinned behaviors
look like bugs and are flagged inline with "PINNED AS-IS".

Coverage:
- `_extract_game_details_common` + the four sport extractors
  (football/hockey/baseball/basketball) against realistic ESPN scoreboard
  events (adapted from the ledmatrix-plugins monorepo test fixtures).
  The output must remain a superset of the frozen skin view-model
  contract (GUARANTEED_KEYS, imported from test_skin_system).
- update() flow for concrete SportsUpcoming/SportsRecent/SportsLive
  subclasses: population, favorite-team filtering, empty/failed-fetch
  tolerance. All offline: `_fetch_data` reads a pre-seeded mocked cache
  and every instance's requests session raises ConnectionError.
- Rendering smoke: one `display()` per mode class at 128x32 draws
  non-zero ink onto a real PIL image.
- Guard rails: the skin-system seam methods on SportsCore must survive
  the merge.
"""

import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import pytz
import requests
from freezegun import freeze_time
from PIL import Image

# src.base_classes.sports transitively imports the hardware matrix driver;
# stub it so these tests can import the sports base classes off-device.
sys.modules.setdefault("rgbmatrix", MagicMock())

from src.base_classes.baseball import Baseball
from src.base_classes.basketball import Basketball
from src.base_classes.football import Football
from src.base_classes.hockey import Hockey, HockeyLive
from src.base_classes.sports import (
    SportsCore,
    SportsLive,
    SportsRecent,
    SportsUpcoming,
)

# Reuse the frozen v1.0 skin view-model contract rather than redeclaring it.
from test.test_skin_system import GUARANTEED_KEYS

SPORT_CLASSES = [Football, Hockey, Baseball, Basketball]
SPORT_IDS = ["football", "hockey", "baseball", "basketball"]

# All update()-flow tests run at this frozen instant so the 21-day
# SportsRecent window and time.time() interval gates are deterministic.
FROZEN_NOW = "2026-01-20 12:00:00"


# ---------------------------------------------------------------------------
# ESPN scoreboard event builders (shape adapted from the monorepo fixtures,
# e.g. ledmatrix-plugins/plugins/hockey-scoreboard/test/fixtures/mock.json:
# team-shaped competitors with status/score/records).
# ---------------------------------------------------------------------------

def _competitor(abbr, team_id, score, home_away, record="30-10-5"):
    return {
        "homeAway": home_away,
        "id": team_id,
        "score": score,
        "team": {
            "id": team_id,
            "abbreviation": abbr,
            "name": abbr.title(),
            "displayName": abbr.title(),
            "logo": None,
        },
        "records": [{"summary": record}],
        # The hockey extractor reads competitor["statistics"] for shot counts;
        # it now defaults to an empty list when the key is absent rather than
        # dropping the whole event (see
        # test_hockey_event_without_statistics_still_extracts).
        "statistics": [],
    }


def make_event(event_id, state, date, home=("TB", "20", "3"),
               away=("DAL", "9", "2"), period=2, clock="12:45",
               name=None, short_detail=None, situation=None,
               home_record="30-10-5", away_record="25-14-6"):
    """Build a realistic ESPN scoreboard event in the given state
    ('in' / 'post' / 'pre')."""
    defaults = {
        "in": ("STATUS_IN_PROGRESS", f"P{period} {clock}"),
        "post": ("STATUS_FINAL", "Final"),
        "pre": ("STATUS_SCHEDULED", "1/15 - 6:30 PM"),
    }
    default_name, default_detail = defaults[state]
    status = {
        "clock": 0.0,
        "displayClock": clock,
        "period": period,
        "type": {
            "id": "2",
            "name": name or default_name,
            "state": state,
            "completed": state == "post",
            "description": short_detail or default_detail,
            "detail": short_detail or default_detail,
            "shortDetail": short_detail or default_detail,
        },
    }
    competition = {
        "id": event_id,
        "date": date,
        "status": status,
        "competitors": [
            _competitor(home[0], home[1], home[2], "home", home_record),
            _competitor(away[0], away[1], away[2], "away", away_record),
        ],
    }
    if situation is not None:
        competition["situation"] = situation
    return {
        "id": event_id,
        "date": date,
        "name": f"{away[0]} at {home[0]}",
        "shortName": f"{away[0]} @ {home[0]}",
        "competitions": [competition],
        # Real ESPN payloads duplicate status at the event top level; the
        # baseball extractor reads it there for live innings.
        "status": status,
    }


def make_probe(favorites=None):
    """Bare-bones SportsCore stand-in for exercising the real extractors
    unbound (same pattern as TestViewModelContract in test_skin_system)."""
    probe = MagicMock()
    probe.logger = logging.getLogger("test_sports_base_characterization")
    probe.favorite_teams = list(favorites or [])
    probe.config = {}
    probe.logo_dir = Path("assets/logos")
    probe._get_timezone.return_value = pytz.utc
    probe.display_manager.format_date_with_ordinal.return_value = "Jan 15th"
    # The sport extractors call self._extract_game_details_common — route
    # it to the real implementation instead of a MagicMock.
    probe._extract_game_details_common = (
        lambda event: SportsCore._extract_game_details_common(probe, event))
    return probe


def extract(sport_cls, event, favorites=None):
    return sport_cls._extract_game_details(make_probe(favorites), event)


# ---------------------------------------------------------------------------
# 1. _extract_game_details_common contract, per wired sport
# ---------------------------------------------------------------------------

class TestExtractGameDetailsContract:
    @pytest.mark.parametrize("sport_cls", SPORT_CLASSES, ids=SPORT_IDS)
    def test_live_event_guaranteed_keys_and_values(self, sport_cls):
        event = make_event("401", "in", "2026-01-15T18:30:00Z")
        details = extract(sport_cls, event)
        assert details is not None
        missing = [k for k in GUARANTEED_KEYS if k not in details]
        assert not missing, (
            f"{sport_cls.__name__} extractor no longer emits {missing} — "
            "these keys are the frozen skin view-model contract.")
        assert details["id"] == "401"
        assert details["home_abbr"] == "TB"
        assert details["away_abbr"] == "DAL"
        assert details["home_id"] == "20"
        assert details["away_id"] == "9"
        assert details["home_score"] == "3"
        assert details["away_score"] == "2"
        assert details["home_record"] == "30-10-5"
        assert details["away_record"] == "25-14-6"
        assert details["is_live"] is True
        assert details["is_final"] is False
        assert details["is_upcoming"] is False
        assert details["status_text"] == "P2 12:45"
        assert details["start_time_utc"] == datetime(
            2026, 1, 15, 18, 30, tzinfo=timezone.utc)
        # Sport-specific formatting of the same event:
        if sport_cls in (Football, Basketball):
            assert details["period_text"] == "Q2"
            assert details["clock"] == "12:45"
        elif sport_cls is Hockey:
            assert details["period_text"] == "P2"
            assert details["clock"] == "12:45"
        else:  # Baseball keys inning/status instead of period_text
            assert details["inning"] == 2
            assert details["status_state"] == "in"

    @pytest.mark.parametrize("sport_cls", SPORT_CLASSES, ids=SPORT_IDS)
    def test_final_event_classification(self, sport_cls):
        event = make_event("402", "post", "2026-01-14T00:00:00Z",
                           home=("BOS", "1", "4"), away=("TOR", "21", "2"),
                           period=3, clock="0:00")
        details = extract(sport_cls, event)
        assert details is not None
        assert details["is_final"] is True
        assert details["is_live"] is False
        assert details["is_upcoming"] is False
        assert details["home_score"] == "4"
        assert details["away_score"] == "2"
        if sport_cls in (Football, Hockey, Basketball):
            assert details["period_text"] == "Final"

    @pytest.mark.parametrize("sport_cls", SPORT_CLASSES, ids=SPORT_IDS)
    def test_upcoming_event_classification(self, sport_cls):
        event = make_event("403", "pre", "2026-01-15T18:30:00Z",
                           home=("NYR", "13", "0"), away=("PIT", "16", "0"),
                           period=0, clock="0:00")
        details = extract(sport_cls, event)
        assert details is not None
        assert details["is_upcoming"] is True
        assert details["is_live"] is False
        assert details["is_final"] is False
        # Local time formatting (probe timezone is UTC): 18:30Z -> 6:30PM,
        # date rendered through display_manager.format_date_with_ordinal.
        assert details["game_time"] == "6:30PM"
        assert details["game_date"] == "Jan 15th"

    def test_halftime_state_flags(self):
        # is_halftime keys off name STATUS_HALFTIME (or state "halftime")
        # while state "in" still counts as live.
        event = make_event("404", "in", "2026-01-15T18:30:00Z",
                           name="STATUS_HALFTIME", short_detail="Halftime")
        details, *_ = SportsCore._extract_game_details_common(
            make_probe(), event)
        assert details["is_live"] is True
        assert details["is_halftime"] is True

    def test_state_name_conflict_is_both_final_and_upcoming(self):
        # PINNED AS-IS (looks like a bug): is_upcoming also matches on
        # status.type.name ('scheduled'/'pre-game'/'status_scheduled'), so
        # an event with state="post" but name="Scheduled" reports BOTH
        # is_final and is_upcoming True.
        event = make_event("405", "post", "2026-01-14T00:00:00Z",
                           name="Scheduled")
        details, *_ = SportsCore._extract_game_details_common(
            make_probe(), event)
        assert details["is_final"] is True
        assert details["is_upcoming"] is True

    def test_zero_zero_record_blanked(self):
        event = make_event("406", "pre", "2026-01-15T18:30:00Z",
                           home_record="0-0", away_record="0-0-0")
        details, *_ = SportsCore._extract_game_details_common(
            make_probe(), event)
        assert details["home_record"] == ""
        assert details["away_record"] == ""

    def test_missing_abbreviation_uses_name_prefix(self):
        event = make_event("407", "pre", "2026-01-15T18:30:00Z")
        for comp in event["competitions"][0]["competitors"]:
            del comp["team"]["abbreviation"]
            comp["team"]["name"] = "Sharks" if comp["homeAway"] == "home" \
                else "Penguins"
        details, *_ = SportsCore._extract_game_details_common(
            make_probe(), event)
        assert details["home_abbr"] == "Sha"
        assert details["away_abbr"] == "Pen"

    def test_empty_or_malformed_event_returns_none_tuple(self):
        probe = make_probe()
        assert SportsCore._extract_game_details_common(probe, {}) == \
            (None, None, None, None, None)
        assert SportsCore._extract_game_details_common(probe, None) == \
            (None, None, None, None, None)
        # Malformed event (no competitions) is swallowed, not raised.
        assert SportsCore._extract_game_details_common(
            probe, {"id": "999"}) == (None, None, None, None, None)

    def test_football_live_situation_fields(self):
        event = make_event(
            "408", "in", "2026-01-15T18:30:00Z",
            situation={
                "shortDownDistanceText": "3rd & 4",
                "downDistanceText": "3rd & 4 at TB 30",
                "isRedZone": False,
                "possession": "20",
                "homeTimeouts": 2,
                "awayTimeouts": 3,
            })
        details = extract(Football, event)
        assert details["down_distance_text"] == "3rd & 4"
        assert details["down_distance_text_long"] == "3rd & 4 at TB 30"
        assert details["possession"] == "20"
        assert details["possession_indicator"] == "home"  # matches home id
        assert details["home_timeouts"] == 2
        assert details["away_timeouts"] == 3

    def test_hockey_live_power_play_and_default_shots(self):
        event = make_event("409", "in", "2026-01-15T18:30:00Z",
                           situation={"isPowerPlay": True, "penalties": ""})
        details = extract(Hockey, event)
        assert details["power_play"] is True
        # Empty statistics arrays -> save-percentage math yields 0 shots.
        assert details["home_shots"] == 0
        assert details["away_shots"] == 0

    def test_hockey_event_without_statistics_still_extracts(self):
        # FIXED (was pinned as returning None): the hockey extractor used to
        # iterate competitor["statistics"] unguarded, so a competitor without
        # the key raised KeyError internally and the WHOLE event was dropped
        # despite valid scores and status. It now defaults to an empty list,
        # matching the behaviour already shipped in the hockey plugin, so the
        # event survives with zeroed shot counts -- the same values
        # test_hockey_live_power_play_and_default_shots already expects for an
        # EMPTY statistics array.
        event = make_event("410", "in", "2026-01-15T18:30:00Z")
        for comp in event["competitions"][0]["competitors"]:
            del comp["statistics"]
        details = extract(Hockey, event)
        assert details is not None
        assert details["home_abbr"] == "TB"
        assert details["away_abbr"] == "DAL"
        assert details["home_score"] == "3"
        assert details["home_shots"] == 0
        assert details["away_shots"] == 0

    def test_baseball_live_inning_and_count(self):
        event = make_event(
            "411", "in", "2026-07-16T23:05:00Z",
            home=("LAD", "19", "5"), away=("SF", "26", "3"),
            period=7, short_detail="Bot 7th",
            situation={
                "count": {"balls": 2, "strikes": 1},
                "outs": 2,
                "onFirst": True,
                "onSecond": False,
                "onThird": True,
            })
        details = extract(Baseball, event)
        assert details["inning"] == 7          # from top-level status period
        assert details["inning_half"] == "bottom"
        assert details["balls"] == 2
        assert details["strikes"] == 1
        assert details["outs"] == 2
        assert details["bases_occupied"] == [True, False, True]
        assert details["status"] == "status_in_progress"
        assert details["series_summary"] == ""

    def test_baseball_live_without_top_level_status_still_extracts(self):
        # FIXED (was pinned as returning None): the baseball extractor read
        # game_event["status"] -- the event TOP-LEVEL status -- for the
        # inning, so an otherwise-valid live event lacking that duplicate key
        # was dropped entirely. Real ESPN events carry status in both places,
        # but MiLB events (synthesized from the MLB Stats API into an
        # ESPN-like shape) populate only the competition-level one. It now
        # reads the competition-level `status` that
        # _extract_game_details_common has already validated, so it can never
        # be missing at that point.
        event = make_event("412", "in", "2026-07-16T23:05:00Z", period=7)
        del event["status"]
        details = extract(Baseball, event)
        assert details is not None
        assert details["inning"] == 7

    def test_baseball_live_without_top_level_status_extracts_for_favorites(self):
        # The favourite-team branch logs the status payload for diagnostics and
        # read the same event top-level key the test above proves can be
        # absent. So the identical MiLB event that extracts fine for a
        # non-favourite raised KeyError and was dropped once the team WAS a
        # favourite -- the worst shape for the bug, since it only hit the games
        # the user cared most about, and only on the diagnostic path that was
        # supposed to help debug them.
        event = make_event("413", "in", "2026-07-16T23:05:00Z", period=7)
        del event["status"]
        details = extract(Baseball, event, favorites=["TB"])
        assert details is not None
        assert details["inning"] == 7


# ---------------------------------------------------------------------------
# 2. update() flow on concrete subclasses (offline, cache-fed)
# ---------------------------------------------------------------------------

class _UpcomingHarness(Hockey, SportsUpcoming):
    """Cheapest concrete SportsUpcoming: hockey extractor + cache-fed data."""

    def _fetch_data(self):
        return self.cache_manager.get(f"{self.sport_key}_schedule")


class _RecentHarness(Hockey, SportsRecent):
    def _fetch_data(self):
        return self.cache_manager.get(f"{self.sport_key}_schedule")


class _LiveHarness(HockeyLive):
    def _fetch_data(self):
        return self.cache_manager.get(f"{self.sport_key}_schedule")


def make_schedule():
    """A mixed schedule around the frozen 'now' of 2026-01-20."""
    return {"events": [
        # Final 6 days ago — inside the recent 21-day window.
        make_event("9001", "post", "2026-01-14T00:00:00Z",
                   home=("BOS", "1", "4"), away=("TOR", "21", "2"),
                   period=3, clock="0:00"),
        # Live game.
        make_event("9002", "in", "2026-01-15T00:30:00Z",
                   home=("TB", "20", "3"), away=("DAL", "9", "2")),
        # Two scheduled games.
        make_event("9003", "pre", "2026-01-16T00:00:00Z",
                   home=("NYR", "13", "0"), away=("PIT", "16", "0"),
                   period=0),
        make_event("9004", "pre", "2026-01-17T00:00:00Z",
                   home=("BOS", "1", "0"), away=("MTL", "10", "0"),
                   period=0),
        # Final from November — outside the recent 21-day window.
        make_event("9005", "post", "2025-11-01T00:00:00Z",
                   home=("SEA", "124292", "1"), away=("VAN", "22", "5"),
                   period=3, clock="0:00"),
    ]}


@pytest.fixture
def build_manager(monkeypatch, tmp_path):
    """Factory for concrete sports managers: mocked display/cache managers,
    logo dir redirected to tmp, background service stubbed, and the
    requests session rigged to prove nothing hits the network."""
    monkeypatch.setattr(
        SportsCore, "_initialize_logo_dir", lambda self, configured: tmp_path)
    monkeypatch.setattr(
        "src.base_classes.sports.core.get_background_service",
        lambda *args, **kwargs: MagicMock())

    # Rig requests.Session BEFORE any manager is built. Construction creates
    # both SportsCore.session and the ESPNDataSource.session; replacing only
    # manager.session after the fact (below) leaves data_source.session real,
    # so an accidental fetch during or right after construction could reach
    # the network. Patching the class makes every session created here raise.
    def _offline_get(*args, **kwargs):
        raise requests.exceptions.ConnectionError(
            "characterization tests are offline")

    monkeypatch.setattr(requests.Session, "get", _offline_get)

    def build(cls, schedule, **mode_cfg):
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
        display_manager.format_date_with_ordinal.side_effect = (
            lambda dt: dt.strftime("%b %d"))
        cache_manager = MagicMock()
        cache_manager.get.return_value = schedule
        cache_manager.cache_dir = str(tmp_path)
        manager = cls(config, display_manager, cache_manager,
                      logging.getLogger("test_sports_base_characterization"),
                      "nhl")
        # Safety net: any accidental network fetch must fail loudly.
        manager.session = MagicMock()
        manager.session.get.side_effect = requests.exceptions.ConnectionError(
            "characterization tests are offline")
        return manager

    return build


def _ids(games):
    return [g["id"] for g in games]


@freeze_time(FROZEN_NOW)
class TestUpcomingUpdateFlow:
    def test_populates_games_list_sorted_by_start_time(self, build_manager):
        manager = build_manager(_UpcomingHarness, make_schedule())
        manager.update()
        # PINNED AS-IS: SportsUpcoming filters purely on is_upcoming
        # (state 'pre') — there is NO date filter, so 'pre' games whose
        # start time is already in the past (9003/9004 vs frozen 1/20)
        # are still shown.
        assert _ids(manager.games_list) == ["9003", "9004"]
        assert manager.current_game["id"] == "9003"

    def test_filters_by_favorite_teams(self, build_manager):
        manager = build_manager(_UpcomingHarness, make_schedule(),
                                show_favorite_teams_only=True,
                                favorite_teams=["BOS"])
        manager.update()
        assert _ids(manager.games_list) == ["9004"]
        assert manager.current_game["id"] == "9004"

    def test_favorites_only_with_no_favorites_shows_nothing(
            self, build_manager):
        # PINNED AS-IS: show_favorite_teams_only=True with an empty
        # favorite_teams list drops every game rather than falling back
        # to showing all games.
        manager = build_manager(_UpcomingHarness, make_schedule(),
                                show_favorite_teams_only=True,
                                favorite_teams=[])
        manager.update()
        assert manager.games_list == []
        assert manager.current_game is None

    def test_caps_at_upcoming_games_to_show(self, build_manager):
        manager = build_manager(_UpcomingHarness, make_schedule(),
                                upcoming_games_to_show=1)
        manager.update()
        assert _ids(manager.games_list) == ["9003"]

    def test_tolerates_empty_events_list(self, build_manager):
        manager = build_manager(_UpcomingHarness, {"events": []})
        manager.update()  # must not raise
        assert manager.games_list == []
        assert manager.current_game is None

    def test_tolerates_fetch_returning_none(self, build_manager):
        manager = build_manager(_UpcomingHarness, None)
        manager.update()  # must not raise
        assert manager.games_list == []
        assert manager.current_game is None

    def test_disabled_manager_update_is_noop(self, build_manager):
        manager = build_manager(_UpcomingHarness, make_schedule(),
                                enabled=False)
        manager.update()
        assert manager.games_list == []
        manager.cache_manager.get.assert_not_called()


@freeze_time(FROZEN_NOW)
class TestRecentUpdateFlow:
    def test_populates_only_finals_within_21_day_window(self, build_manager):
        manager = build_manager(_RecentHarness, make_schedule())
        manager.update()
        # 9001 (final, 6 days old) kept; 9005 (final, ~80 days old)
        # excluded by the 21-day cutoff; live/pre games excluded.
        assert _ids(manager.games_list) == ["9001"]
        assert manager.current_game["id"] == "9001"
        assert manager.current_game["is_final"] is True

    def test_filters_by_favorite_teams(self, build_manager):
        manager = build_manager(_RecentHarness, make_schedule(),
                                show_favorite_teams_only=True,
                                favorite_teams=["TOR"])
        manager.update()
        assert _ids(manager.games_list) == ["9001"]

        stranger = build_manager(_RecentHarness, make_schedule(),
                                 show_favorite_teams_only=True,
                                 favorite_teams=["XXX"])
        stranger.update()
        assert stranger.games_list == []
        assert stranger.current_game is None

    def test_tolerates_empty_events_list(self, build_manager):
        manager = build_manager(_RecentHarness, {"events": []})
        manager.update()  # must not raise
        assert manager.games_list == []
        assert manager.current_game is None


@freeze_time(FROZEN_NOW)
class TestLiveUpdateFlow:
    def test_selects_only_live_games(self, build_manager):
        manager = build_manager(_LiveHarness, make_schedule())
        manager.update()
        assert _ids(manager.live_games) == ["9002"]
        assert manager.current_game["id"] == "9002"
        assert manager.current_game["is_live"] is True

    def test_no_live_games_clears_current_game(self, build_manager):
        schedule = {"events": [
            make_event("9001", "post", "2026-01-14T00:00:00Z"),
            make_event("9003", "pre", "2026-01-16T00:00:00Z", period=0),
        ]}
        manager = build_manager(_LiveHarness, schedule)
        manager.update()
        assert manager.live_games == []
        assert manager.current_game is None


# ---------------------------------------------------------------------------
# 3. Rendering smoke — one display() per mode class at 128x32
# ---------------------------------------------------------------------------

def _fake_logo(*args, **kwargs):
    return Image.new("RGBA", (24, 24), (180, 30, 30, 255))


@freeze_time(FROZEN_NOW)
class TestRenderingSmoke:
    def _assert_rendered(self, manager):
        manager.display_manager.update_display.assert_called()
        assert manager.display_manager.image.convert("L").getbbox() is not None

    def test_upcoming_display_draws_ink(self, build_manager):
        manager = build_manager(_UpcomingHarness, make_schedule())
        manager.update()
        manager._load_and_resize_logo = _fake_logo
        assert manager.display(force_clear=True) is True
        self._assert_rendered(manager)

    def test_recent_display_draws_ink(self, build_manager):
        manager = build_manager(_RecentHarness, make_schedule())
        manager.update()
        manager._load_and_resize_logo = _fake_logo
        assert manager.display(force_clear=True) is True
        self._assert_rendered(manager)

    def test_live_display_draws_ink(self, build_manager):
        manager = build_manager(_LiveHarness, make_schedule())
        manager.update()
        manager._load_and_resize_logo = _fake_logo
        assert manager.display(force_clear=True) is True
        self._assert_rendered(manager)

    def test_draw_scorebug_layout_direct_call_does_not_raise(
            self, build_manager):
        # The base-class placeholder renderer must also stay callable.
        manager = build_manager(_UpcomingHarness, make_schedule())
        game = manager._extract_game_details(make_schedule()["events"][2])
        manager._load_and_resize_logo = _fake_logo
        SportsCore._draw_scorebug_layout(manager, game)
        assert manager.display_manager.image.convert("L").getbbox() is not None


# ---------------------------------------------------------------------------
# 4. Guard rails — seams the merge must not silently drop
# ---------------------------------------------------------------------------

class TestGuardRails:
    def test_skin_seam_methods_survive(self):
        for name in ("_resolve_skin_id", "_get_skin", "_render_game",
                     "render_skin_card"):
            assert callable(getattr(SportsCore, name, None)), (
                f"SportsCore.{name} is part of the skin-system seam "
                "(src/skin_system) — the sports-unification merge must "
                "keep it.")

    def test_skin_mode_per_class(self):
        assert SportsCore.SKIN_MODE == "live"
        assert SportsUpcoming.SKIN_MODE == "upcoming"
        assert SportsRecent.SKIN_MODE == "recent"
        assert SportsLive.SKIN_MODE == "live"  # inherits the default

    def test_core_display_and_extractor_seams_survive(self):
        for name in ("display", "_draw_scorebug_layout",
                     "_extract_game_details_common", "update"):
            owner = SportsCore if name != "update" else SportsUpcoming
            assert callable(getattr(owner, name, None)), name
