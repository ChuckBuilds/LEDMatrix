"""Reusing an unchanged recent/upcoming strip (SportsScrollDisplayManager).

Building a strip draws every card on the render thread, and the panel stands
frozen for it -- 1.1-1.6s on a Pi 4 at the start of every turn. A strip whose
inputs have not changed is now rewound and shown again instead.

What these pin down:

* it is reused only when nothing it is drawn from changed, and drawn again on
  any doubt (live, a raised or failed build, clear_all, a strip replaced
  behind the manager's back, two builds overlapping, age, date, config,
  rankings, panel size);
* two leagues taking turns on one game type each keep their strip -- with one
  shared display, NFL then NCAA then NFL never found its own strip again;
* everything a caller reads (get_scroll_display, _scroll_displays,
  display_frame, is_complete, get_all_vegas_content_items) answers as it did
  when there was one display per game type;
* the extra strips kept are bounded, by count and by bytes.

All against the real ScrollHelper, so "reused" means the pixels on the panel.
"""

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
        overtaken = []

        def slow_then_failing(display, games, game_type, leagues, rankings_cache=None):
            if list(leagues) == ["nfl"] and not overtaken:
                overtaken.append(True)
                assert manager.prepare_and_display(NCAA, "recent", ["ncaa_fb"]) is True
                return False
            return original(display, games, game_type, leagues, rankings_cache)

        display_class.prepare_scroll_content = slow_then_failing
        assert manager.prepare_and_display(NFL, "recent", ["nfl"]) is False
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
        overtaken = []

        def draws_then_is_overtaken(display, games, game_type, leagues,
                                    rankings_cache=None):
            result = original(display, games, game_type, leagues, rankings_cache)
            if not overtaken:
                overtaken.append(True)
                assert manager.prepare_and_display(changed, "recent", ["nfl"]) is True
            return result

        display_class.prepare_scroll_content = draws_then_is_overtaken
        assert manager.prepare_and_display(NFL, "recent", ["nfl"]) is True
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
