"""src.common.favorite_team_check: the messages a user sees for a bad team code.

Ported from the scoreboard plugins' own tests (hockey-scoreboard's
test_favorite_check.py and football-scoreboard's
test_schedule_note_uses_game_dates.py), which test their bundled
``<sport>_favorite_check.py`` copies. Core now owns a copy that can drift on
its own, and a plugin that deletes its copy loses those tests with it.

Everything here is offline: ESPN is replaced with a fixed roster, so the tests
pin the *messages* the user actually sees. They are the point of the feature --
the whole thing exists to turn a blank screen into a sentence that says why.
"""

import logging
import sys
import unittest
from datetime import datetime, timedelta, timezone

from src.common.favorite_team_check import FavoriteTeamCheck

NHL = {
    "BOS": "Boston Bruins",
    "TB": "Tampa Bay Lightning",
    "UTAH": "Utah Mammoth",
    "SEA": "Seattle Kraken",
    "VGK": "Vegas Golden Knights",
}

NCAA = {
    "ALA": "Alabama Crimson Tide",
    "UNA": "North Alabama Lions",
    "CONN": "UConn Huskies",
    "SC": "South Carolina Gamecocks",
    "RUTG": "Rutgers Scarlet Knights",
    "GS": "Golden State Warriors",
}


class RecordingLogger(logging.Logger):
    """Captures formatted records so tests can assert on the message text."""

    def __init__(self):
        super().__init__("test")
        self.records = []

    def handle(self, record):
        self.records.append((record.levelname, record.getMessage()))

    def messages(self, level=None):
        return [m for lvl, m in self.records if level is None or lvl == level]


class SuggestionTests(unittest.TestCase):
    """The suggestion has to name the right team, and name it first."""

    def assert_first_suggestion(self, code, teams, expected):
        message = FavoriteTeamCheck._suggest(code, teams)
        self.assertIn(expected, message,
                      "{!r} did not suggest {!r}: {}".format(code, expected, message))
        # Where several candidates are listed, the right one must lead, since
        # users act on the first thing they read.
        head = message.split("(")[0]
        self.assertIn(expected, head,
                      "{!r} buried {!r} behind another suggestion: {}".format(
                          code, expected, message))

    def test_retired_code_points_at_the_current_one(self):
        self.assert_first_suggestion("UTA", NHL, "UTAH")

    def test_common_wrong_guesses(self):
        self.assert_first_suggestion("GSW", NCAA, "GS")
        self.assert_first_suggestion("UCONN", NCAA, "CONN")

    def test_fragment_of_a_word_is_matched(self):
        # 'BAMA' abbreviates nothing in 'Alabama Crimson Tide' -- it is a chunk
        # out of the middle of a word -- so the initials rule alone misses it.
        self.assert_first_suggestion("BAMA", NCAA, "ALA")

    def test_first_word_beats_a_mid_name_match(self):
        # 'SCAR' fits Rutgers *Scar*let too, but South Carolina starts at the
        # first word, which is how people actually shorten a name.
        self.assert_first_suggestion("SCAR", NCAA, "SC")

    def test_wrong_case_says_so_rather_than_guessing(self):
        message = FavoriteTeamCheck._suggest("bos", NHL)
        self.assertIn("case-sensitive", message)
        self.assertIn("'BOS'", message)

    def test_valid_code_draws_no_suggestion(self):
        self.assertEqual(FavoriteTeamCheck._suggest("BOS", NHL), "")

    def test_unmatchable_code_is_not_forced_into_a_suggestion(self):
        # Nothing sensible to offer is better than something wrong.
        self.assertEqual(FavoriteTeamCheck._suggest("ZZZZZZ", NHL), "")

    def test_abbreviates_separates_codes_string_distance_ties(self):
        # The case that motivated this: 'MUN' is equidistant from 'MAN' and
        # 'SUN' by string similarity, so similarity cannot choose.
        self.assertTrue(FavoriteTeamCheck._abbreviates("MUN", "Manchester United"))
        self.assertFalse(FavoriteTeamCheck._abbreviates("MUN", "Sunderland"))


class CheckTests(unittest.TestCase):
    """The log output for each way a league can end up empty."""

    def setUp(self):
        self.logger = RecordingLogger()
        self.checker = FavoriteTeamCheck(self.logger, {"nhl": ("NHL", "hockey/nhl")})
        self.checker._fetch_teams = staticmethod(lambda path: dict(NHL))
        self.checker._schedule_note = staticmethod(lambda path: None)

    def test_bad_code_is_reported_with_a_suggestion(self):
        self.checker._check("nhl", ["UTA", "BOS"])
        warnings = self.logger.messages("WARNING")
        self.assertEqual(len(warnings), 1)
        self.assertIn("'UTA' is not a NHL team code", warnings[0])
        self.assertIn("UTAH", warnings[0])

    def test_all_codes_bad_says_nothing_will_show(self):
        self.checker._check("nhl", ["UTA", "NOPE"])
        joined = " ".join(self.logger.messages("WARNING"))
        self.assertIn("no recognised favorite teams", joined)
        self.assertIn("nothing will be shown", joined)

    def test_good_codes_out_of_season_explain_the_empty_screen(self):
        self.checker._schedule_note = staticmethod(
            lambda path: "the league has nothing on until 07 October 2026")
        self.checker._check("nhl", ["BOS"])
        info = " ".join(self.logger.messages("INFO"))
        self.assertIn("look correct", info)
        self.assertIn("07 October 2026", info)
        self.assertIn("not a configuration problem", info)
        self.assertEqual(self.logger.messages("WARNING"), [])

    def test_good_codes_in_season_stay_quiet(self):
        self.checker._check("nhl", ["BOS", "TB"])
        self.assertEqual(self.logger.messages("WARNING"), [])
        self.assertIn("recognised: BOS, TB", " ".join(self.logger.messages("INFO")))

    def test_dynamic_groups_are_not_treated_as_team_codes(self):
        self.checker._check("nhl", ["AP_TOP_25", "NCAA_MENS_TOP_10"])
        self.assertEqual(self.logger.messages("WARNING"), [])

    def test_empty_roster_draws_no_conclusion(self):
        # ESPN's college lacrosse endpoints return zero teams; a valid code
        # must not be called wrong just because the roster is unavailable.
        self.checker._fetch_teams = staticmethod(lambda path: {})
        self.checker._check("nhl", ["ANYTHING"])
        self.assertEqual(self.logger.records, [])

    def test_fetch_failure_is_swallowed(self):
        def boom(path):
            raise RuntimeError("network down")

        self.checker._fetch_teams = staticmethod(boom)
        self.checker._check("nhl", ["BOS"])  # must not raise
        self.assertEqual(self.logger.messages("WARNING"), [])


class ScheduleNoteTests(unittest.TestCase):
    """
    Reading ESPN's scoreboard for "is there anything on?".

    Both traps here are real API behaviour, confirmed against live endpoints:
    an out-of-season league rolls forward to its next fixtures rather than
    returning nothing, and a finished season returns its *last* game instead.
    """

    def note(self, payload):
        """Run _schedule_note against a fixed payload, with no network access.

        The method imports ``requests`` in its own body, so the fake has to go
        into ``sys.modules`` — patching the attribute on the module has
        no effect on a function-local import.
        """
        import types

        class Response:
            @staticmethod
            def json():
                return payload

        fake = types.ModuleType("requests")
        fake.get = lambda url, timeout=None: Response()

        real = sys.modules.get("requests")
        sys.modules["requests"] = fake
        try:
            return FavoriteTeamCheck._schedule_note("hockey/nhl")
        finally:
            if real is None:
                sys.modules.pop("requests", None)
            else:
                sys.modules["requests"] = real

    @staticmethod
    def iso(days):
        from datetime import datetime, timedelta, timezone
        return (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()

    def test_games_today_says_nothing(self):
        self.assertIsNone(self.note({"events": [{"date": self.iso(0)}]}))

    def test_a_game_already_under_way_counts_as_something_on(self):
        # Games that started earlier today are in the past by the clock. Reading
        # them as "not upcoming" made a live slate report the season as over.
        self.assertIsNone(self.note({"events": [{"date": self.iso(-0.3)}]}))

    def test_an_off_day_or_two_is_not_worth_mentioning(self):
        self.assertIsNone(self.note({"events": [{"date": self.iso(1)}]}))

    def test_out_of_season_reports_the_next_fixture(self):
        # ESPN rolls forward, so events exist but are months away.
        note = self.note({"events": [{"date": self.iso(52)}, {"date": self.iso(53)}]})
        self.assertIsNotNone(note)
        self.assertIn("nothing on until", note)

    def test_finished_season_is_reported_as_finished(self):
        # A completed season returns its last game, in the past.
        note = self.note({"events": [{"date": self.iso(-120)}],
                          "leagues": [{"calendar": [self.iso(-300)]}]})
        self.assertIsNotNone(note)
        self.assertIn("season has finished", note)

    def test_past_dates_never_read_as_imminent(self):
        # The bug this guards: taking the soonest of *all* dates makes a game
        # from last March look like one happening right now, so a finished
        # season silently reports itself as in progress.
        note = self.note({"events": [{"date": self.iso(-120)},
                                     {"date": self.iso(40)}]})
        self.assertIn("nothing on until", note)

    def test_calendar_is_used_when_there_are_no_events(self):
        note = self.note({"events": [],
                          "leagues": [{"calendar": [{"startDate": self.iso(30)}]}]})
        self.assertIn("nothing on until", note)

    def test_nothing_published_draws_no_conclusion(self):
        self.assertIsNone(self.note({"events": [], "leagues": [{"calendar": []}]}))

    def test_unparseable_dates_are_ignored_rather_than_fatal(self):
        self.assertIsNone(self.note(
            {"events": [{"date": "not a date"}, {"date": None}, {}]}))


class SchedulingTests(unittest.TestCase):
    """The check must run once, off the render path, and never raise."""

    def setUp(self):
        self.logger = RecordingLogger()
        self.checker = FavoriteTeamCheck(self.logger, {"nhl": ("NHL", "hockey/nhl")})
        self.calls = []
        self.checker._check = lambda key, favs: self.calls.append((key, list(favs)))

    def drain(self):
        import threading
        for thread in threading.enumerate():
            if thread.name == "favorite-team-check":
                thread.join(timeout=5)

    def test_runs_once_per_league(self):
        for _ in range(5):
            self.checker.schedule("nhl", ["BOS"])
        self.drain()
        self.assertEqual(len(self.calls), 1)

    def test_reset_allows_a_recheck_after_a_config_edit(self):
        self.checker.schedule("nhl", ["BOS"])
        self.drain()
        self.checker.reset()
        self.checker.schedule("nhl", ["TB"])
        self.drain()
        self.assertEqual([favs for _, favs in self.calls], [["BOS"], ["TB"]])

    def test_no_favorites_configured_does_nothing(self):
        self.checker.schedule("nhl", [])
        self.checker.schedule("nhl", None)
        self.checker.schedule("nhl", ["", "   "])
        self.drain()
        self.assertEqual(self.calls, [])

    def test_unknown_league_key_is_ignored(self):
        self.checker.schedule("not-a-league", ["BOS"])
        self.drain()
        self.assertEqual(self.calls, [])

    def test_thread_is_a_daemon_so_it_cannot_hold_up_shutdown(self):
        import threading
        started = threading.Event()
        seen = {}

        def record(key, favs):
            seen["daemon"] = threading.current_thread().daemon
            started.set()

        self.checker._check = record
        self.checker.schedule("nhl", ["BOS"])
        self.assertTrue(started.wait(timeout=5))
        self.assertTrue(seen["daemon"])


class ScheduleNoteUsesGameDatesTests(unittest.TestCase):
    """The "nothing on until" note reports a game date, not a calendar boundary.

    ``_schedule_note`` used to pool ESPN's rolled-forward event dates with the
    league calendar's week/phase startDates and take the earliest. Calendar
    weeks routinely open days before their first game, so the note reported
    "nothing on until 06 September" for a league whose first snap was the
    10th. Events now win; the calendar only speaks when the scoreboard has no
    events at all.
    """

    note = ScheduleNoteTests.note

    @staticmethod
    def iso(base, days_out):
        # Every date in a case derives from one captured *base*, so a UTC
        # midnight crossing mid-test cannot make the payload and the expected
        # strftime disagree about the day.
        return (base + timedelta(days=days_out)).strftime("%Y-%m-%dT%H:%MZ")

    def test_the_first_game_wins_over_an_earlier_calendar_boundary(self):
        base = datetime.now(timezone.utc)
        note = self.note({
            "events": [{"date": self.iso(base, 10)}, {"date": self.iso(base, 14)}],
            "leagues": [{"calendar": [{"startDate": self.iso(base, 6)}]}],
        })
        self.assertIn((base + timedelta(days=10)).strftime("%d %B %Y"), note)
        self.assertNotIn((base + timedelta(days=6)).strftime("%d %B %Y"), note)

    def test_with_no_events_the_calendar_still_gets_a_say(self):
        base = datetime.now(timezone.utc)
        note = self.note({"events": [], "leagues": [{"calendar": [self.iso(base, 20)]}]})
        self.assertIn((base + timedelta(days=20)).strftime("%d %B %Y"), note)

    def test_only_past_dates_reads_as_a_finished_season(self):
        base = datetime.now(timezone.utc)
        note = self.note({"events": [{"date": self.iso(base, -40)}],
                          "leagues": [{"calendar": []}]})
        self.assertIn("finished", note)

    def test_a_finished_season_is_not_dressed_up_by_an_offseason_calendar(self):
        # Past events mean the season is over; a future calendar boundary
        # (the draft, next season's week 1 shell) is not the next game.
        base = datetime.now(timezone.utc)
        note = self.note({"events": [{"date": self.iso(base, -40)}],
                          "leagues": [{"calendar": [{"startDate": self.iso(base, 45)}]}]})
        self.assertIn("finished", note)

    def test_an_imminent_slate_is_not_worth_a_note(self):
        base = datetime.now(timezone.utc)
        self.assertIsNone(self.note({
            "events": [{"date": self.iso(base, 1)}],
            "leagues": [{"calendar": [{"startDate": self.iso(base, 6)}]}],
        }))
