"""Reusing an unchanged recent/upcoming strip (SportsScrollDisplayManager).

Building a strip draws every card while the render thread waits for it, the
panel frozen on its last frame -- ~1.4s for seven football cards at 192x48 on
a Pi 4, at the start of every turn. A strip whose inputs have not changed is
now rewound and shown again instead.

What these pin down:

* it is reused only when nothing it is drawn from changed, and drawn again on
  any doubt (live, a raised or failed build, clear_all, a strip replaced
  behind the manager's back, two builds overlapping, age, date, config,
  rankings, panel size, a game dict changed in place or while it was drawn);
* two leagues taking turns on one game type each keep their strip and their
  own league's pacing -- with one shared display, NFL then NCAA then NFL
  never found its own strip again;
* everything a caller reads (get_scroll_display, _scroll_displays,
  display_frame, is_complete, get_all_vegas_content_items) answers as it did
  when there was one display per game type;
* the extra strips kept are bounded, by count and by bytes, and the ones let
  go are the least recently shown.

All against the real ScrollHelper, so "reused" means the pixels on the panel;
with LEDMATRIX_PLUGINS set, against real football and hockey scoreboards too.
"""

import os
import subprocess
import sys
import time as real_time
from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.modules.setdefault("rgbmatrix", MagicMock())

from src.common import sports_scroll  # noqa: E402
from src.common.sports_scroll import (  # noqa: E402
    SportsScrollDisplay,
    SportsScrollDisplayManager,
)

W, H = 128, 32


class _Panel:
    """The display manager surface sports_scroll touches."""

    matrix = None
    refresh_hz = 100.0

    def __init__(self, width=W, height=H):
        self.width, self.height = width, height
        self.image = None
        self.frames = 0

    def set_scrolling_state(self, scrolling, frame_hold=1):
        pass

    def update_display(self):
        self.frames += 1


def _game(gid, league="nfl", home=7, away=3, state="post"):
    return {"id": gid, "league": league, "home_abbr": "HOM", "away_abbr": "AWY",
            "home_score": home, "away_score": away, "status": {"state": state}}


NFL = [_game("1"), _game("2", home=21)]
NCAA = [_game("10", league="ncaa_fb"), _game("11", league="ncaa_fb", away=14)]


@pytest.fixture
def display_class():
    """A scoreboard whose cards encode their game, counting every build."""

    class _Counting(SportsScrollDisplay):
        SCROLL_LEAGUE_KEYS = ("nfl", "ncaa_fb")
        builds = []
        constructed = 0
        fail_with = None          # an exception to raise, or False to return

        def __init__(self, *args, **kwargs):
            type(self).constructed += 1
            super().__init__(*args, **kwargs)

        def prepare_scroll_content(self, games, game_type, leagues, rankings_cache=None):
            games = list(games)
            type(self).builds.append((game_type, tuple(leagues), len(games)))
            if isinstance(type(self).fail_with, Exception):
                raise type(self).fail_with
            if type(self).fail_with is False:
                return False
            ranks = rankings_cache or {}
            cards = [Image.new("RGB", (40, self.display_height),
                               (int(g["home_score"]) * 9 % 256,
                                int(g["away_score"]) * 9 % 256,
                                ranks.get("HOM", 0) * 9 % 256))
                     for g in games]
            self._vegas_content_items = list(cards)
            self.scroll_helper.create_scrolling_image(cards, item_gap=8, element_gap=0)
            self._current_games = games
            self._current_game_type = game_type
            self._current_leagues = list(leagues)
            return True

    return _Counting


@pytest.fixture
def panel():
    return _Panel()


@pytest.fixture
def manager(display_class, panel):
    class _Manager(SportsScrollDisplayManager):
        pass

    _Manager.display_class = display_class
    return _Manager(panel, {"nfl": {"scroll_settings": {"scroll_speed": 100}}})


class _Clock:
    """sports_scroll's view of the time module, with the clock and the date
    movable. ScrollHelper keeps the real one."""

    def __init__(self):
        self.ahead = 0.0
        self.days = 0

    def monotonic(self):
        return real_time.monotonic() + self.ahead

    def localtime(self, *args):
        return real_time.localtime(real_time.time() + self.days * 86400)

    def __getattr__(self, name):
        return getattr(real_time, name)


@pytest.fixture
def clock(monkeypatch):
    fake = _Clock()
    monkeypatch.setattr(sports_scroll, "time", fake)
    return fake


def _scroll(manager, game_type, frames=50):
    for _ in range(frames):
        manager.display_frame(game_type)


def _pacing(display):
    """How a display's strip scrolls, and where it is."""
    helper = display.scroll_helper
    state = {name: getattr(helper, name) for name in (
        "scroll_speed", "fixed_pixels_per_frame", "frame_based_scrolling",
        "calculated_duration", "min_duration", "max_duration",
        "total_scroll_width", "scroll_position", "total_distance_scrolled",
        "scroll_complete")}
    state["frame_hold"] = display._scroll_frame_hold()
    state["settings_league"] = getattr(display, "_settings_league", None)
    return state


def _parked_bytes(manager):
    """What the strips of displays not on screen hold, as the budget counts it."""
    shown = {id(display) for display in manager._scroll_displays.values()}
    return sum(manager._strip_bytes(slot.display)
               for pool in manager._strip_pools.values()
               for slot in pool.values() if id(slot.display) not in shown)


# ---------------------------------------------------------------------------
# Reuse
# ---------------------------------------------------------------------------

class TestReuse:
    def test_the_same_slate_twice_builds_once(self, manager, display_class):
        assert manager.prepare_and_display(NFL, "recent", ["nfl"]) is True
        assert manager.prepare_and_display(NFL, "recent", ["nfl"]) is True
        assert len(display_class.builds) == 1

    def test_equal_games_in_new_dicts_still_match(self, manager, display_class):
        """A refresh hands over new dicts with the same content."""
        manager.prepare_and_display(NFL, "recent", ["nfl"])
        manager.prepare_and_display([dict(g) for g in NFL], "recent", ["nfl"])
        assert len(display_class.builds) == 1

    def test_two_leagues_taking_turns_each_keep_their_strip(self, manager, display_class):
        """nfl_recent, ncaa_fb_recent, nfl_recent, ... -- the rotation that a
        memo of one shared display's last build never hits."""
        for _ in range(3):
            assert manager.prepare_and_display(NFL, "recent", ["nfl"]) is True
            assert manager.prepare_and_display(NCAA, "recent", ["ncaa_fb"]) is True
        assert display_class.builds == [("recent", ("nfl",), 2),
                                        ("recent", ("ncaa_fb",), 2)]

    def test_upcoming_is_reused_too(self, manager, display_class):
        upcoming = [_game("5", state="pre")]
        manager.prepare_and_display(upcoming, "upcoming", ["nfl"])
        manager.prepare_and_display(upcoming, "upcoming", ["nfl"])
        assert len(display_class.builds) == 1

    def test_a_reused_strip_starts_like_a_fresh_one(self, manager):
        """Rewound to the start, a new cycle, this game type active -- what
        create_scrolling_image() leaves."""
        manager.prepare_and_display(NFL, "recent", ["nfl"])
        manager.prepare_and_display(NCAA, "upcoming", ["ncaa_fb"])
        helper = manager.get_scroll_display("recent").scroll_helper
        helper.scroll_position = 77.0
        helper.total_distance_scrolled = 9999.0
        helper.scroll_complete = True

        assert manager.prepare_and_display(NFL, "recent", ["nfl"]) is True
        assert helper.scroll_position == 0
        assert helper.total_distance_scrolled == 0
        assert manager.is_complete("recent") is False
        assert manager._current_game_type == "recent"

    def test_the_reused_strip_is_pixel_for_pixel_a_fresh_build(
            self, manager, display_class, panel):
        manager.prepare_and_display(NFL, "recent", ["nfl"])
        manager.prepare_and_display(NCAA, "recent", ["ncaa_fb"])
        manager.prepare_and_display(NFL, "recent", ["nfl"])          # reused
        assert len(display_class.builds) == 2
        _scroll(manager, "recent", frames=60)
        reused_frame = np.array(panel.image)

        fresh_panel = _Panel()
        fresh = type(manager)(fresh_panel, manager.config)
        fresh.prepare_and_display(NFL, "recent", ["nfl"])
        _scroll(fresh, "recent", frames=60)

        assert np.array_equal(
            manager.get_scroll_display("recent").scroll_helper.cached_array,
            fresh.get_scroll_display("recent").scroll_helper.cached_array)
        assert np.array_equal(reused_frame, np.array(fresh_panel.image))


# ---------------------------------------------------------------------------
# Anything in doubt is drawn again
# ---------------------------------------------------------------------------

class TestDrawnAgain:
    def test_a_changed_game(self, manager, display_class):
        manager.prepare_and_display(NFL, "recent", ["nfl"])
        manager.prepare_and_display([NFL[0], _game("2", home=24)], "recent", ["nfl"])
        assert len(display_class.builds) == 2

    def test_a_game_changed_in_place(self, manager, display_class):
        """A sport that updates its game dicts in place hands over the same
        objects again; the key is what they held when the strip was drawn."""
        games = [dict(g) for g in NFL]
        manager.prepare_and_display(games, "recent", ["nfl"])
        games[1]["home_score"] = 24
        manager.prepare_and_display(games, "recent", ["nfl"])
        assert len(display_class.builds) == 2

    def test_a_game_changed_while_its_strip_was_drawn(self, manager, display_class):
        """A background update can change a game dict while the strip is
        being drawn from it. Whether the card caught the change is then not
        known, so the next turn draws again -- and from then on reuses."""
        games = [dict(g) for g in NFL]
        build = display_class.prepare_scroll_content

        def updated_after_drawing(display, games_, game_type, leagues,
                                  rankings_cache=None):
            result = build(display, games_, game_type, leagues, rankings_cache)
            if len(display_class.builds) == 1:
                games[1]["home_score"] = 24
            return result

        display_class.prepare_scroll_content = updated_after_drawing
        for _ in range(3):
            assert manager.prepare_and_display(games, "recent", ["nfl"]) is True
        assert len(display_class.builds) == 2

    def test_a_reordered_slate(self, manager, display_class):
        manager.prepare_and_display(NFL, "recent", ["nfl"])
        manager.prepare_and_display(list(reversed(NFL)), "recent", ["nfl"])
        assert len(display_class.builds) == 2

    def test_changed_rankings(self, manager, display_class):
        manager.prepare_and_display(NFL, "recent", ["nfl"], {"HOM": 5})
        manager.prepare_and_display(NFL, "recent", ["nfl"], {"HOM": 4})
        manager.prepare_and_display(NFL, "recent", ["nfl"], {"HOM": 4})
        assert len(display_class.builds) == 2

    def test_a_config_edited_in_place(self, manager, display_class):
        """By value, not id(): a plugin that edits its config dict in place
        keeps the same object."""
        manager.prepare_and_display(NFL, "recent", ["nfl"])
        manager.config["nfl"]["scroll_settings"]["gap_between_games"] = 12
        manager.prepare_and_display(NFL, "recent", ["nfl"])
        assert len(display_class.builds) == 2

    def test_a_new_day(self, manager, display_class, clock):
        manager.prepare_and_display(NFL, "recent", ["nfl"])
        clock.days = 1
        manager.prepare_and_display(NFL, "recent", ["nfl"])
        assert len(display_class.builds) == 2

    def test_age(self, manager, display_class, clock):
        manager.prepare_and_display(NFL, "recent", ["nfl"])
        clock.ahead = manager.STRIP_MEMO_MAX_AGE_S - 5
        manager.prepare_and_display(NFL, "recent", ["nfl"])
        assert len(display_class.builds) == 1
        clock.ahead = manager.STRIP_MEMO_MAX_AGE_S + 1
        manager.prepare_and_display(NFL, "recent", ["nfl"])
        assert len(display_class.builds) == 2

    def test_a_different_panel_size(self, manager, display_class, panel):
        manager.prepare_and_display(NFL, "recent", ["nfl"])
        panel.width = 256
        manager.prepare_and_display(NFL, "recent", ["nfl"])
        assert len(display_class.builds) == 2

    def test_live_is_always_drawn(self, manager, display_class):
        live = [_game("1", state="in")]
        manager.prepare_and_display(live, "live", ["nfl"])
        manager.prepare_and_display(live, "live", ["nfl"])
        assert len(display_class.builds) == 2

    def test_a_game_type_outside_the_set_is_always_drawn(self, manager, display_class):
        manager.prepare_and_display(NFL, "mixed", ["nfl"])
        manager.prepare_and_display(NFL, "mixed", ["nfl"])
        assert len(display_class.builds) == 2

    def test_a_raised_build_leaves_nothing_to_reuse(self, manager, display_class):
        display_class.fail_with = KeyError("status")
        assert manager.prepare_and_display(NFL, "recent", ["nfl"]) is False
        display_class.fail_with = None
        assert manager.prepare_and_display(NFL, "recent", ["nfl"]) is True
        assert manager.prepare_and_display(NFL, "recent", ["nfl"]) is True
        assert len(display_class.builds) == 2

    def test_a_raised_rebuild_forgets_the_old_strip(self, manager, display_class):
        """The strip it was replacing is not the one asked for any more."""
        manager.prepare_and_display(NFL, "recent", ["nfl"])
        display_class.fail_with = KeyError("status")
        changed = [NFL[0], _game("2", home=24)]
        assert manager.prepare_and_display(changed, "recent", ["nfl"]) is False
        display_class.fail_with = None
        manager.prepare_and_display(NFL, "recent", ["nfl"])
        assert len(display_class.builds) == 3

    def test_a_build_that_returns_false_leaves_nothing_to_reuse(
            self, manager, display_class):
        display_class.fail_with = False
        manager.prepare_and_display(NFL, "recent", ["nfl"])
        display_class.fail_with = None
        manager.prepare_and_display(NFL, "recent", ["nfl"])
        assert len(display_class.builds) == 2

    def test_a_failed_rebuild_never_vouches_for_the_old_strip(
            self, manager, display_class):
        """A rebuild that gives up before drawing leaves the previous strip in
        the helper; that strip must not be recorded as the new slate's."""
        manager.prepare_and_display(NFL, "recent", ["nfl"])
        changed = [NFL[0], _game("2", home=24)]
        display_class.fail_with = False
        assert manager.prepare_and_display(changed, "recent", ["nfl"]) is False
        display_class.fail_with = None
        assert manager.prepare_and_display(changed, "recent", ["nfl"]) is True
        assert len(display_class.builds) == 3

    def test_clear_all(self, manager, display_class):
        manager.prepare_and_display(NFL, "recent", ["nfl"])
        manager.prepare_and_display(NCAA, "recent", ["ncaa_fb"])
        manager.clear_all()
        manager.prepare_and_display(NFL, "recent", ["nfl"])
        manager.prepare_and_display(NCAA, "recent", ["ncaa_fb"])
        assert len(display_class.builds) == 4

    def test_clear_all_forgets_even_a_strip_clear_kept(self, manager, display_class):
        """Whatever a sport's clear() does with its helper, nothing from
        before clear_all() is reused."""
        display_class.clear = lambda self: None
        manager.prepare_and_display(NFL, "recent", ["nfl"])
        manager.clear_all()
        manager.prepare_and_display(NFL, "recent", ["nfl"])
        assert len(display_class.builds) == 2

    def test_clear_all_clears_the_strips_not_on_screen_too(self, manager):
        manager.prepare_and_display(NFL, "recent", ["nfl"])
        parked = manager.get_scroll_display("recent")
        manager.prepare_and_display(NCAA, "recent", ["ncaa_fb"])
        assert parked.scroll_helper.has_strip()
        manager.clear_all()
        assert not parked.scroll_helper.has_strip()
        assert parked._vegas_content_items == []

    def test_a_display_cleared_directly(self, manager, display_class):
        manager.prepare_and_display(NFL, "recent", ["nfl"])
        manager.get_scroll_display("recent").clear()
        manager.prepare_and_display(NFL, "recent", ["nfl"])
        assert len(display_class.builds) == 2

    def test_a_strip_whose_image_was_dropped(self, manager, display_class):
        """Its array is still the one built, but without the image
        display_frame() draws nothing: reused, the panel would stay blank on
        every turn until the strip aged out."""
        manager.prepare_and_display(NFL, "recent", ["nfl"])
        manager.get_scroll_display("recent").scroll_helper.cached_image = None
        manager.prepare_and_display(NFL, "recent", ["nfl"])
        assert len(display_class.builds) == 2
        assert manager.display_frame("recent") is True

    def test_a_strip_replaced_behind_the_managers_back(self, manager, display_class):
        """A plugin's prepare_content builds on get_scroll_display(game_type)
        directly; the strip it leaves is not the one the memo recorded."""
        manager.prepare_and_display(NFL, "recent", ["nfl"])
        manager.get_scroll_display("recent").prepare_scroll_content(
            NCAA, "recent", ["ncaa_fb"])
        manager.prepare_and_display(NFL, "recent", ["nfl"])
        assert len(display_class.builds) == 3

    def test_no_games_is_the_old_path_exactly(self, manager, display_class):
        manager.prepare_and_display(NFL, "recent", ["nfl"])
        shown = manager.get_scroll_display("recent")
        manager.prepare_and_display([], "recent", ["nfl"])
        assert manager.get_scroll_display("recent") is shown
        manager.prepare_and_display(NFL, "recent", ["nfl"])
        assert [b[2] for b in display_class.builds] == [2, 0, 2]

    def test_an_empty_slate_is_drawn_every_time(self, manager, display_class):
        """Even when the sport draws something for it and returns True (this
        fake leaves a blank strip): no games is never keyed."""
        for _ in range(3):
            assert manager.prepare_and_display([], "recent", ["nfl"]) is True
        assert [b[2] for b in display_class.builds] == [0, 0, 0]

    def test_an_unkeyed_build_on_the_shown_display_forgets_its_strip(
            self, manager, display_class):
        """A build the memo cannot key still runs on the display on screen,
        and can change it (a sport re-applies another league's scroll
        settings) without replacing the strip."""
        manager.prepare_and_display(NFL, "recent", ["nfl"])
        display_class.fail_with = False
        manager.prepare_and_display(iter(NCAA), "recent", ["ncaa_fb"])
        display_class.fail_with = None
        manager.prepare_and_display(NFL, "recent", ["nfl"])
        assert len(display_class.builds) == 3

    def test_a_one_shot_iterable_reaches_the_build_whole(self, manager, display_class):
        """Fingerprinting it would have used it up before the build saw it."""
        manager.prepare_and_display(iter(NFL), "recent", ["nfl"])
        manager.prepare_and_display(iter(NFL), "recent", ["nfl"])
        assert display_class.builds == [("recent", ("nfl",), 2)] * 2

    def test_unfreezable_input_is_drawn_not_raised(self, manager, display_class):
        manager.prepare_and_display(NFL, "recent", [["nfl"]])     # unhashable
        manager.prepare_and_display(NFL, "recent", [["nfl"]])
        assert len(display_class.builds) == 2


# ---------------------------------------------------------------------------
# What callers see
# ---------------------------------------------------------------------------

class TestCallersSeeOneDisplayPerGameType:
    def test_get_scroll_display_is_the_strip_display_frame_draws(self, manager, panel):
        manager.prepare_and_display(NFL, "recent", ["nfl"])
        manager.prepare_and_display(NCAA, "recent", ["ncaa_fb"])
        shown = manager.get_scroll_display("recent")
        assert shown._current_leagues == ["ncaa_fb"]
        assert manager._scroll_displays["recent"] is shown

        _scroll(manager, "recent", frames=5)
        assert shown.scroll_helper.total_distance_scrolled > 0

        manager.prepare_and_display(NFL, "recent", ["nfl"])          # reused
        shown = manager.get_scroll_display("recent")
        assert shown._current_leagues == ["nfl"]
        assert manager._scroll_displays["recent"] is shown
        assert manager.display_frame("recent") is True
        assert shown.scroll_helper.total_distance_scrolled > 0

    def test_live_keeps_one_display_whatever_the_slate(self, manager):
        """sports_live_scroll rebuilds a live strip in place around
        get_scroll_display('live'); it must stay the same object."""
        manager.prepare_and_display([_game("1", state="in")], "live", ["nfl"])
        first = manager.get_scroll_display("live")
        manager.prepare_and_display(
            [_game("10", league="ncaa_fb", state="in")], "live", ["ncaa_fb"])
        assert manager.get_scroll_display("live") is first

    def test_a_display_fetched_before_the_first_prepare_is_the_one_drawn_on(
            self, manager, display_class):
        early = manager.get_scroll_display("recent")
        manager.prepare_and_display(NFL, "recent", ["nfl"])
        assert manager.get_scroll_display("recent") is early
        assert early._current_leagues == ["nfl"]

    def test_vegas_items_are_those_of_the_strips_on_screen(self, manager):
        """As with one display per game type: the slate shown last, not every
        strip kept."""
        manager.prepare_and_display(NFL + NFL[:1], "recent", ["nfl"])
        manager.prepare_and_display(NCAA, "recent", ["ncaa_fb"])
        assert len(manager.get_all_vegas_content_items()) == len(NCAA)
        manager.prepare_and_display(NFL + NFL[:1], "recent", ["nfl"])
        assert len(manager.get_all_vegas_content_items()) == 3

    def test_a_failed_build_of_a_new_slate_leaves_the_previous_strip_showing(
            self, manager, display_class):
        """What a failed build did to the one shared display: nothing drawn,
        the previous strip still there."""
        manager.prepare_and_display(NFL, "recent", ["nfl"])
        shown = manager.get_scroll_display("recent")
        display_class.fail_with = KeyError("status")
        assert manager.prepare_and_display(NCAA, "recent", ["ncaa_fb"]) is False
        assert manager.get_scroll_display("recent") is shown
        assert manager.display_frame("recent") is True
        display_class.fail_with = None
        manager.prepare_and_display(NFL, "recent", ["nfl"])
        assert len(display_class.builds) == 2, "the previous strip is still reusable"

    def test_a_build_overtaken_by_the_next_prepare_keeps_the_newer_strip_shown(
            self, manager, display_class):
        """A display() call that outlived its timeout can still be building
        when the next one starts. If the slower, older build then fails, it
        must not put back the strip from before it: the newer prepare's strip
        is the one the plugin goes on to draw."""
        manager.prepare_and_display([_game("20", league="cfl")], "recent", ["cfl"])
        original = display_class.prepare_scroll_content
        # The overtaking prepare's result, checked after the outer one: an
        # assert in here would be caught by _prepare_on's except and read as
        # the build failing, which this one does anyway.
        overtaken = []

        def slow_then_failing(display, games, game_type, leagues, rankings_cache=None):
            if list(leagues) == ["nfl"] and not overtaken:
                overtaken.append(
                    manager.prepare_and_display(NCAA, "recent", ["ncaa_fb"]))
                return False
            return original(display, games, game_type, leagues, rankings_cache)

        display_class.prepare_scroll_content = slow_then_failing
        assert manager.prepare_and_display(NFL, "recent", ["nfl"]) is False
        assert overtaken == [True], "the overtaking prepare drew its strip"
        assert manager.get_scroll_display("recent")._current_leagues == ["ncaa_fb"]
        assert manager._scroll_displays["recent"]._current_leagues == ["ncaa_fb"]

    def test_a_build_overtaken_on_its_own_display_vouches_for_nothing(
            self, manager, display_class):
        """Two builds of one slate overlapping on its display: the older one
        draws first, the newer one (changed games) draws over it and records
        its strip, then the older one finishes. The strip in the helper is
        the newer one's, so the older one's games must not reuse it."""
        original = display_class.prepare_scroll_content
        changed = [NFL[0], _game("2", home=24)]
        overtaken = []                  # checked after, as in the test above

        def draws_then_is_overtaken(display, games, game_type, leagues,
                                    rankings_cache=None):
            result = original(display, games, game_type, leagues, rankings_cache)
            if not overtaken:
                overtaken.append(None)  # set first: the nested build comes back here
                overtaken[0] = manager.prepare_and_display(changed, "recent", ["nfl"])
            return result

        display_class.prepare_scroll_content = draws_then_is_overtaken
        assert manager.prepare_and_display(NFL, "recent", ["nfl"]) is True
        assert overtaken == [True], "the overtaking prepare drew its strip"
        assert len(display_class.builds) == 2

        manager.prepare_and_display(NFL, "recent", ["nfl"])
        assert len(display_class.builds) == 3, "NFL's key vouched for the changed strip"

    def test_dynamic_duration_follows_the_strip_on_screen(self, manager):
        """The plugins' own get_dynamic_duration(game_type) reads
        _scroll_displays[game_type] directly."""
        # Slow enough that neither strip is clamped to min_duration.
        manager.config["nfl"]["scroll_settings"]["scroll_speed"] = 10
        manager.prepare_and_display(NFL * 3, "recent", ["nfl"])
        long_duration = manager._scroll_displays["recent"].get_dynamic_duration()
        manager.prepare_and_display(NCAA[:1], "recent", ["ncaa_fb"])
        assert manager._scroll_displays["recent"].get_dynamic_duration() != long_duration
        manager.prepare_and_display(NFL * 3, "recent", ["nfl"])     # reused
        assert manager._scroll_displays["recent"].get_dynamic_duration() == long_duration


# ---------------------------------------------------------------------------
# What real scoreboards do that the plain fake does not
# ---------------------------------------------------------------------------

class TestSportsHabits:
    def test_each_slate_keeps_its_own_leagues_pacing(self, manager, display_class):
        """A sport re-applies a league's scroll settings to its display when
        the league changes (the plugins' _settings_league), which is why each
        slate has a display of its own. A reused strip must scroll exactly as
        a fresh build for its own league does."""

        class _PerLeague(display_class):
            _settings_league = None

            def _get_scroll_settings(self, league=None):
                return super()._get_scroll_settings(league or self._settings_league)

            def prepare_scroll_content(self, games, game_type, leagues,
                                       rankings_cache=None):
                if leagues and leagues[0] != self._settings_league:
                    self._settings_league = leagues[0]
                    self._configure_scroll_helper()
                return super().prepare_scroll_content(
                    games, game_type, leagues, rankings_cache)

        type(manager).display_class = _PerLeague
        manager.config["nfl"]["scroll_settings"].update(
            scroll_speed=30, min_duration=2, max_duration=400)
        manager.config["ncaa_fb"] = {"scroll_settings": {
            "scroll_speed": 120, "min_duration": 1, "max_duration": 90}}
        slates = ((NFL * 2, ["nfl"]), (NCAA, ["ncaa_fb"]))
        for _ in range(2):
            for games, leagues in slates:
                manager.prepare_and_display(games, "recent", leagues)
        assert len(display_class.builds) == 2

        paces = []
        for games, leagues in slates:
            builds = len(display_class.builds)
            assert manager.prepare_and_display(games, "recent", leagues) is True
            assert len(display_class.builds) == builds, "reused"
            fresh_panel = _Panel()
            fresh = type(manager)(fresh_panel, manager.config)
            fresh.prepare_and_display(games, "recent", leagues)
            reused = _pacing(manager.get_scroll_display("recent"))
            assert reused == _pacing(fresh.get_scroll_display("recent"))
            paces.append(reused)
            _scroll(manager, "recent", frames=30)
            _scroll(fresh, "recent", frames=30)
            assert np.array_equal(np.array(manager.display_manager.image),
                                  np.array(fresh_panel.image))
        assert paces[0]["scroll_speed"] != paces[1]["scroll_speed"], \
            "the two leagues must pace differently for this to show anything"

    def test_a_build_that_fills_in_its_game_dicts_settles(self, manager, display_class):
        """Hockey's renderer fills in fields on the game dicts it is handed.
        Keyed on what they held before that, the next turn draws once more;
        then the strip is reused, and is what a fresh build draws."""
        build = display_class.prepare_scroll_content

        def filling_in(display, games, game_type, leagues, rankings_cache=None):
            for game in games:
                game.setdefault("status_text", "Final")
            return build(display, games, game_type, leagues, rankings_cache)

        display_class.prepare_scroll_content = filling_in
        slates = (([dict(g) for g in NFL], ["nfl"]),
                  ([dict(g) for g in NCAA], ["ncaa_fb"]))
        builds = []
        for _ in range(3):
            for games, leagues in slates:
                assert manager.prepare_and_display(games, "recent", leagues) is True
            builds.append(len(display_class.builds))
        assert builds[0] == 2 and builds[2] == builds[1] <= 4, builds

        games, leagues = slates[1]
        fresh = type(manager)(_Panel(), manager.config)
        fresh.prepare_and_display(games, "recent", leagues)
        assert np.array_equal(
            manager.get_scroll_display("recent").scroll_helper.cached_array,
            fresh.get_scroll_display("recent").scroll_helper.cached_array)


# ---------------------------------------------------------------------------
# Bounds
# ---------------------------------------------------------------------------

class TestBounds:
    def _slate(self, n):
        return [_game(str(n * 10 + i), league=f"l{n}") for i in range(2)], [f"l{n}"]

    def test_displays_per_game_type_are_capped(self, manager, display_class):
        cap = manager.STRIP_MEMO_SLATES_PER_TYPE
        for n in range(cap + 3):
            games, leagues = self._slate(n)
            manager.prepare_and_display(games, "recent", leagues)
        assert display_class.constructed == cap
        assert len(manager._strip_pools["recent"]) == cap

    def test_the_least_recently_shown_slate_is_the_one_redrawn(
            self, manager, display_class):
        cap = manager.STRIP_MEMO_SLATES_PER_TYPE
        for n in range(cap + 1):                  # slate 0 drops out
            games, leagues = self._slate(n)
            manager.prepare_and_display(games, "recent", leagues)
        builds = len(display_class.builds)
        for n in range(1, cap + 1):               # all still kept
            games, leagues = self._slate(n)
            manager.prepare_and_display(games, "recent", leagues)
        assert len(display_class.builds) == builds
        games, leagues = self._slate(0)
        manager.prepare_and_display(games, "recent", leagues)
        assert len(display_class.builds) == builds + 1

    def test_a_slate_shown_again_by_reuse_is_not_the_next_let_go(
            self, manager, display_class):
        """Reuse counts as being shown: the slate whose display draws a new
        one is the least recently shown, not the least recently drawn."""
        cap = manager.STRIP_MEMO_SLATES_PER_TYPE
        slates = [self._slate(n) for n in range(cap + 1)]
        for games, leagues in slates[:cap]:
            manager.prepare_and_display(games, "recent", leagues)
        manager.prepare_and_display(slates[0][0], "recent", slates[0][1])   # reused
        assert len(display_class.builds) == cap

        manager.prepare_and_display(slates[cap][0], "recent", slates[cap][1])
        manager.prepare_and_display(slates[0][0], "recent", slates[0][1])
        assert len(display_class.builds) == cap + 1, "slate 0, just shown, was let go"
        manager.prepare_and_display(slates[1][0], "recent", slates[1][1])
        assert len(display_class.builds) == cap + 2, "slate 1 was the one let go"

    def test_the_byte_budget_lets_go_of_the_least_recently_shown(
            self, manager, display_class, clock):
        """Reuse counts as being shown here too."""
        a, b, c, d = (self._slate(n) for n in range(4))
        shown = []
        for games, leagues in (a, b, c):
            clock.ahead += 1
            manager.prepare_and_display(games, "recent", leagues)
            shown.append(manager.get_scroll_display("recent"))
        shown_a, shown_b, shown_c = shown
        manager.STRIP_MEMO_MAX_PARKED_BYTES = 2 * manager._strip_bytes(shown_a)
        for games, leagues in (a, b):             # shown again, by reuse
            clock.ahead += 1
            manager.prepare_and_display(games, "recent", leagues)
        assert len(display_class.builds) == 3

        clock.ahead += 1
        manager.prepare_and_display(d[0], "recent", d[1])   # a, b, c parked: one too many
        assert not shown_c.scroll_helper.has_strip(), "c was shown longest ago"
        assert shown_a.scroll_helper.has_strip()
        assert shown_b.scroll_helper.has_strip()
        manager.prepare_and_display(a[0], "recent", a[1])
        assert len(display_class.builds) == 4

    def test_a_reuse_that_parks_a_bigger_strip_stays_inside_the_budget(
            self, manager, display_class, clock):
        """Not only a build trims. A reuse can park a bigger strip than the one
        it brings back, and with only reuses after it the budget would stay
        exceeded until the strips aged out."""
        a, b = self._slate(0), self._slate(1)
        big = ([_game(str(90 + i), league="big") for i in range(4)], ["big"])
        manager.prepare_and_display(a[0], "recent", a[1])
        small = manager._strip_bytes(manager.get_scroll_display("recent"))
        manager.STRIP_MEMO_MAX_PARKED_BYTES = 2 * small       # room for a and b
        for games, leagues in (b, big):
            clock.ahead += 1
            manager.prepare_and_display(games, "recent", leagues)
        shown_big = manager.get_scroll_display("recent")
        assert manager._strip_bytes(shown_big) > small
        assert _parked_bytes(manager) <= manager.STRIP_MEMO_MAX_PARKED_BYTES

        clock.ahead += 1
        manager.prepare_and_display(a[0], "recent", a[1])     # reused; big is parked
        assert len(display_class.builds) == 3
        assert _parked_bytes(manager) <= manager.STRIP_MEMO_MAX_PARKED_BYTES
        assert shown_big.scroll_helper.has_strip(), "b went first, shown longer ago"
        manager.prepare_and_display(big[0], "recent", big[1])
        assert len(display_class.builds) == 3

    def test_parked_strips_stay_inside_the_byte_budget(self, manager, display_class):
        manager.prepare_and_display(NFL, "recent", ["nfl"])
        one_strip = manager._strip_bytes(manager.get_scroll_display("recent"))
        manager.STRIP_MEMO_MAX_PARKED_BYTES = one_strip     # room for one
        first = manager.get_scroll_display("recent")
        manager.prepare_and_display(NCAA, "recent", ["ncaa_fb"])
        second = manager.get_scroll_display("recent")
        third_games = [_game("20", league="cfl"), _game("21", league="cfl")]
        manager.prepare_and_display(third_games, "recent", ["cfl"])

        assert not first.scroll_helper.has_strip(), "the oldest was released"
        assert first._vegas_content_items == []
        assert second.scroll_helper.has_strip(), "the newest parked one fits"
        builds = len(display_class.builds)
        manager.prepare_and_display(NCAA, "recent", ["ncaa_fb"])
        assert len(display_class.builds) == builds
        manager.prepare_and_display(NFL, "recent", ["nfl"])
        assert len(display_class.builds) == builds + 1

    def test_a_strip_that_can_never_be_reused_is_released_first(
            self, manager, display_class):
        """A failed build on the least recently shown display leaves that
        older slate's strip behind, keyed to nothing. It goes before a
        parked strip that its slate can still reuse, however recent."""
        manager.STRIP_MEMO_SLATES_PER_TYPE = 3
        a, b, c, d = (self._slate(n) for n in range(4))
        for games, leagues in (a, b, c):
            manager.prepare_and_display(games, "recent", leagues)
        stale = manager._strip_pools["recent"][("recent", tuple(a[1]))].display
        kept = manager._strip_pools["recent"][("recent", tuple(b[1]))].display
        manager.STRIP_MEMO_MAX_PARKED_BYTES = manager._strip_bytes(stale)  # room for one

        display_class.fail_with = False           # gives up before drawing
        assert manager.prepare_and_display(d[0], "recent", d[1]) is False
        display_class.fail_with = None

        assert not stale.scroll_helper.has_strip()
        assert kept.scroll_helper.has_strip()
        builds = len(display_class.builds)
        manager.prepare_and_display(b[0], "recent", b[1])
        assert len(display_class.builds) == builds

    def test_the_strip_on_screen_is_never_released(self, manager, display_class):
        manager.STRIP_MEMO_MAX_PARKED_BYTES = 0
        manager.prepare_and_display(NFL, "recent", ["nfl"])
        manager.prepare_and_display(NCAA, "upcoming", ["ncaa_fb"])
        for game_type in ("recent", "upcoming"):
            assert manager.get_scroll_display(game_type).scroll_helper.has_strip()
        manager.prepare_and_display(NFL, "recent", ["nfl"])
        assert len(display_class.builds) == 2
        assert manager.display_frame("recent") is True

    def test_release_leaves_the_scrolling_state_alone(self, manager, panel):
        """clear() would also tell the display manager nothing is scrolling,
        which is not a parked display's to say."""
        panel.set_scrolling_state = MagicMock()
        manager.STRIP_MEMO_MAX_PARKED_BYTES = 0
        manager.prepare_and_display(NFL, "recent", ["nfl"])
        manager.prepare_and_display(NCAA, "recent", ["ncaa_fb"])
        panel.set_scrolling_state.assert_not_called()


# ---------------------------------------------------------------------------
# Real scoreboards (LEDMATRIX_PLUGINS)
# ---------------------------------------------------------------------------
#
# The fakes above draw only from what the key holds. A real sport's cards are
# drawn by its own renderer in ledmatrix-plugins, so a renderer that started
# drawing something outside the key would pass all of them. Point
# LEDMATRIX_PLUGINS at a checkout to run two real scoreboards through the memo.

REPO = Path(__file__).resolve().parent.parent

#: Two leagues per sport. Every team has a logo under assets/sports.
REAL_SLATES = {
    "football": (("nfl", "nfl_logos", ("DAL", "PHI", "KC", "BUF", "SF", "SEA")),
                 ("ncaa_fb", "ncaa_logos", ("ALA", "UGA", "OSU", "MICH"))),
    # Its renderer fills in the game dicts it is handed.
    "hockey": (("nhl", "nhl_logos", ("BOS", "TOR", "MTL", "NYR", "PIT", "CHI")),
               ("ncaa_mens", "ncaa_logos", ("BU", "BC", "MICH", "MINN"))),
}

#: Each league's own scroll settings, so a display that kept the other
#: league's pacing would show.
REAL_SETTINGS = (
    {"scroll_speed": 30, "gap_between_games": 20, "min_duration": 15, "max_duration": 400},
    {"scroll_speed": 120, "gap_between_games": 64, "min_duration": 40, "max_duration": 90},
)


@pytest.mark.parametrize("sport", sorted(REAL_SLATES))
def test_a_real_scoreboard_reuses_exactly_what_it_would_draw(sport):
    """Two leagues taking turns through the sport's own ScrollDisplayManager:
    the builds stay bounded, and each reused strip -- its pixels, Vegas items,
    dynamic duration, pacing and the frame on the panel -- equals a fresh
    build for its league. In a child process, since every plugin has its own
    top-level scroll_display and game_renderer modules."""
    raw = os.environ.get("LEDMATRIX_PLUGINS")
    root = Path(raw) if raw else None
    if root is not None and (root / "plugins").is_dir():
        root = root / "plugins"
    plugin = root / f"{sport}-scoreboard" if root is not None else None
    if plugin is None or not (plugin / "scroll_display.py").is_file():
        pytest.skip("set LEDMATRIX_PLUGINS to a ledmatrix-plugins checkout to run "
                    "real scoreboards through the strip memo")
    result = subprocess.run(
        [sys.executable, __file__, str(plugin), sport],
        cwd=str(REPO), capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=600,
        env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    assert result.returncode == 0, (result.stdout + result.stderr)[-6000:]
    assert result.stdout.strip().endswith("OK"), result.stdout[-6000:]


def _real_scoreboard_check(plugin, sport):
    """The child process's half of the test above."""
    import json
    import logging
    from datetime import datetime, timedelta, timezone

    logging.basicConfig(level=logging.ERROR)
    sys.path.insert(0, str(plugin))
    sys.dont_write_bytecode = True
    from scroll_display import ScrollDisplayManager

    def defaults(schema):
        if "default" in schema:
            return json.loads(json.dumps(schema["default"]))
        if schema.get("type") == "object":
            return {key: defaults(sub) for key, sub in (schema.get("properties") or {}).items()
                    if "default" in sub or sub.get("type") == "object"}
        return None

    config = defaults(json.loads((plugin / "config_schema.json").read_text(encoding="utf-8")))

    def game(i, league, logo_dir, home, away):
        logos = REPO / "assets" / "sports" / logo_dir
        for team in (home, away):
            assert (logos / f"{team}.png").is_file(), f"no logo for {team}"
        start = datetime(2026, 9, 28, 17, 0, tzinfo=timezone.utc) + timedelta(hours=i)
        return {
            "id": f"{league}{i}", "league": league, "game_time": "1:00PM",
            "game_date": "09/28", "start_time_utc": start, "status_text": "Final",
            "is_live": False, "is_final": True, "is_upcoming": False,
            "is_halftime": False, "is_period_break": False, "broadcast": "CBS",
            "home_abbr": home, "home_id": str(100 + i), "home_score": str(10 + i),
            "home_logo_path": logos / f"{home}.png", "home_logo_url": None,
            "home_record": "3-1", "away_abbr": away, "away_id": str(200 + i),
            "away_score": str(7 + i), "away_logo_path": logos / f"{away}.png",
            "away_logo_url": None, "away_record": "2-2", "is_within_window": True,
            "favorite_teams": [], "period": 4, "period_text": "Final",
            "clock": "0:00", "status": {"state": "post"},
        }

    slates = []
    for (league, logo_dir, teams), settings in zip(REAL_SLATES[sport], REAL_SETTINGS):
        block = config.setdefault(league, {})
        block["enabled"] = True
        block.setdefault("scroll_settings", {}).update(settings)
        slates.append(([game(i, league, logo_dir, teams[2 * i % len(teams)],
                             teams[(2 * i + 1) % len(teams)]) for i in range(4)],
                       [league]))
    rankings = {teams[0]: 5 for _, _, teams in REAL_SLATES[sport]}

    calls = []
    build = ScrollDisplayManager.display_class.prepare_scroll_content

    def counting(self, games, game_type, leagues, rankings_cache=None):
        calls.append(tuple(leagues))
        return build(self, games, game_type, leagues, rankings_cache)

    ScrollDisplayManager.display_class.prepare_scroll_content = counting
    log = logging.getLogger("strip_reuse")

    def new_manager():
        return ScrollDisplayManager(_Panel(192, 48), config, log, global_config={})

    manager = new_manager()
    rounds = []
    for _ in range(3):
        for games, leagues in slates:
            assert manager.prepare_and_display(games, "recent", leagues, rankings) is True
        rounds.append(len(calls))
    # A sport that fills in its game dicts draws each slate once more.
    assert calls[:2] == [tuple(slates[0][1]), tuple(slates[1][1])], calls
    assert rounds[2] == rounds[1] <= 4, (rounds, calls)

    paces = []
    for games, leagues in slates:
        builds = len(calls)
        assert manager.prepare_and_display(games, "recent", leagues, rankings) is True
        assert len(calls) == builds, f"{leagues} was drawn again"
        fresh = new_manager()
        assert fresh.prepare_and_display(games, "recent", leagues, rankings) is True
        reused, built = manager.get_scroll_display("recent"), fresh.get_scroll_display("recent")
        assert np.array_equal(reused.scroll_helper.cached_array, built.scroll_helper.cached_array)
        assert len(reused._vegas_content_items) == len(built._vegas_content_items)
        for mine, theirs in zip(reused._vegas_content_items, built._vegas_content_items):
            assert np.array_equal(np.array(mine), np.array(theirs))
        assert manager.get_dynamic_duration("recent") == fresh.get_dynamic_duration("recent")
        assert _pacing(reused) == _pacing(built), (_pacing(reused), _pacing(built))
        paces.append(_pacing(reused))
        _scroll(manager, "recent", frames=150)
        _scroll(fresh, "recent", frames=150)
        assert np.array_equal(np.array(manager.display_manager.image),
                              np.array(fresh.display_manager.image))
    assert paces[0]["scroll_speed"] != paces[1]["scroll_speed"], paces
    print("OK")


if __name__ == "__main__":
    _real_scoreboard_check(Path(sys.argv[1]).resolve(), sys.argv[2])
