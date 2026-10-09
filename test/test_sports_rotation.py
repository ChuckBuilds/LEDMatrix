"""src.common.sports_rotation: behaviour, the lock, the seam, host contract.

The cases follow ledmatrix-plugins' ``scripts/test_other_games_rotation.py``
(the tables the family 7 reconcile was checked against): the expected values
are its columns, for the abbreviation-ranked sports and, where the
``_rankings_loaded`` seam is overridden, for football. The host is the mixin
over core's ``SportsCoreSharedMixin`` (``_favorites_first`` and
``_compose_selection`` call into it) with the family-8 ranking reads copied in,
and ``_favorites_first`` stands in for the plugins' ``update()``.
"""

import ast
import logging
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.common import sports_rotation
from src.common.sports_favorites import SportsFavoritesMixin
from src.common.sports_helpers import SportsHelpersMixin
from src.common.sports_rotation import SportsRotationMixin
from src.common.sports_shared import SportsCoreSharedMixin

LOG = logging.getLogger("test_sports_rotation")
NOW = datetime(2026, 10, 9, 12, tzinfo=timezone.utc)
T0 = 100_000.0     # not 0: the window reads a zero stamp as "never cut"

TEAM = {str(n): chr(64 + n) * 3 for n in range(1, 17)}       # 1 AAA ... 16 PPP
ID_OF = {abbr: int(tid) for tid, abbr in TEAM.items()}


def game(gid, home, away, hours, recent=False):
    g = {"id": gid, "home_id": home, "away_id": away,
         "home_abbr": TEAM[home], "away_abbr": TEAM[away]}
    if hours is not None:
        g["start_time_utc"] = NOW + timedelta(hours=-hours if recent else hours)
    return g


class Host(SportsRotationMixin, SportsCoreSharedMixin, SportsFavoritesMixin,
           SportsHelpersMixin):
    """A scoreboard SportsCore as far as the rotation reaches."""

    league = sport = "test"
    sport_key = "test"

    def __init__(self, favorites=(), interval=60, quality="any", rankings=None):
        self.logger = LOG
        self.favorite_teams = list(favorites)
        self.other_rotation_interval_seconds = interval
        self.other_games_min_quality = quality
        self.other_games_divisions = []
        self._team_rankings_cache = dict(rankings or {})
        self._ranking_coverage_logged_at = 0.0
        self._other_window_start = 0
        self._other_window_rotated_at = 0.0
        self._games_lock = threading.RLock()
        self.games_list, self.current_game, self.current_game_index = [], None, 0
        self.last_game_switch = 0.0
        self.mode_config = {}

    # Family 8, still per plugin: the abbreviation sports' bodies.
    def _is_ranked_game(self, game):
        rankings = self._team_rankings_cache
        return bool(rankings.get(game.get("home_abbr"), 0)
                    or rankings.get(game.get("away_abbr"), 0))

    def _best_rank(self, game):
        rankings = self._team_rankings_cache
        ranked = [r for r in (rankings.get(game.get("home_abbr"), 0),
                              rankings.get(game.get("away_abbr"), 0)) if r]
        return min(ranked) if ranked else 99

    def _passes_other_filters(self, game):
        return not (self.other_games_min_quality == "ranked"
                    and self._team_rankings_cache and not self._is_ranked_game(game))


class IdRankedHost(Host):
    """football's override of the seam, and its by-id first rank read."""

    def __init__(self, ranked_ids=None, **kw):
        super().__init__(**kw)
        self._ranked_team_ids = dict(ranked_ids or {})

    def _rankings_loaded(self):
        return bool(self._ranked_team_ids or self._team_rankings_cache)

    def _best_rank(self, game):
        try:
            ids = [int(game["home_id"]), int(game["away_id"])]
        except (KeyError, TypeError, ValueError):
            ids = []
        if self._ranked_team_ids and ids:
            found = [r for r in (self._ranked_team_ids.get(i, 0) for i in ids) if r]
            return min(found) if found else 99
        return super()._best_rank(game)


class Clock:
    def __init__(self):
        self.now = T0

    def __call__(self):
        return self.now


@pytest.fixture
def clock(monkeypatch):
    c = Clock()
    monkeypatch.setattr(sports_rotation.time, "monotonic", c)
    monkeypatch.setattr(sports_rotation.time, "time", c)
    return c


def ids(games):
    return ",".join(g["id"] for g in games) or "none"


# ---------------------------------------------------------------------------
# _by_importance and the _rankings_loaded seam
# ---------------------------------------------------------------------------

def importance_slate():
    no_abbr = game("n9", "15", "16", 9)
    del no_abbr["home_abbr"], no_abbr["away_abbr"]
    return [game("i3", "6", "7", 3), game("i1", "1", "3", 1), game("i2", "4", "5", 2),
            game("i4", "8", "9", 4), game("i5", "2", "10", 5), game("i6", "11", "12", 6),
            game("i7", "4", "11", 7), game("i8", "13", "14", None), no_abbr,
            game("i10", "2", "15", 10)]


RANKED = {"HHH": 1, "FFF": 2, "DDD": 3, "BBB": 4}
TIED = {"HHH": 1, "FFF": 2, "DDD": 2, "BBB": 4}
AS_LISTED = "i3,i1,i2,i4,i5,i6,i7,i8,n9,i10"


@pytest.mark.parametrize("rankings, newest, expected", [
    ({}, False, AS_LISTED),
    ({}, True, AS_LISTED),
    (RANKED, False, "i4,i3,i2,i5,i1,i6,n9,i8"),
    (RANKED, True, "i4,i3,i7,i10,n9,i1,i8"),
    (TIED, False, "i4,i2,i3,i5,i1,i6,n9,i8"),       # a tie keeps kickoff order
    (TIED, True, "i4,i7,i3,i10,n9,i1,i8"),
])
def test_by_importance(rankings, newest, expected):
    assert ids(Host(rankings=rankings)._by_importance(importance_slate(), newest)) == expected


BY_ID = {ID_OF[a]: r for a, r in RANKED.items()}


@pytest.mark.parametrize("abbr, by_id, newest, default, overridden", [
    ({}, BY_ID, False, AS_LISTED, "i4,i3,i2,i5,i1,i6,n9,i8"),
    ({}, BY_ID, True, AS_LISTED, "i4,i3,i7,i10,n9,i1,i8"),
    ({"HHH": 1, "FFF": 2}, {ID_OF["CCC"]: 1}, False,
     "i4,i3,i1,i2,i5,i6,n9,i8", "i1,i2,i3,i4,i5,i6,n9,i8"),
    ({"HHH": 1, "FFF": 2}, {ID_OF["CCC"]: 1}, True,
     "i4,i3,i10,n9,i7,i1,i8", "i1,i10,n9,i7,i4,i3,i8"),
])
def test_the_rankings_loaded_seam(abbr, by_id, newest, default, overridden):
    """The default asks the abbreviation table; football counts its id table too."""
    plain = Host(rankings=abbr)
    plain._ranked_team_ids = by_id             # read by nothing in the default
    assert ids(plain._by_importance(importance_slate(), newest)) == default
    football = IdRankedHost(ranked_ids=by_id, rankings=abbr)
    assert ids(football._by_importance(importance_slate(), newest)) == overridden


def test_rankings_loaded_default():
    assert Host()._rankings_loaded() is False
    assert Host(rankings={"AAA": 1})._rankings_loaded() is True


# ---------------------------------------------------------------------------
# _other_games_window
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("size, limit, interval, expected", [
    (0, 3, 60, "none / none / none / none / none / none"),
    (7, 0, 60, "none / none / none / none / none / none"),
    (1, 3, 60, "o1 / o1 / o1 / o1 / o1 / o1"),
    (2, 2, 60, "o1,o2 / o1,o2 / o1,o2 / o1,o2 / o1,o2 / o1,o2"),
    (7, 3, 0, "o1,o2,o3 / o1,o2,o3 / o1,o2,o3 / o1,o2,o3 / o1,o2,o3 / o1,o2,o3"),
    (7, 2, 60, "o1,o2 / o1,o2 / o3,o4 / o3,o4 / o5,o6 / o4,o5"),
    (7, 3, 60, "o1,o2,o3 / o1,o2,o3 / o4,o5,o6 / o4,o5,o6 / o7,o1,o2 / o2,o3,o4"),
])
def test_the_window_advances_catches_up_and_wraps(clock, size, limit, interval, expected):
    """At 0, 30, 60, 119, 120 and 300 s: 300 is three intervals after 120."""
    host = Host(interval=interval)
    pool = [{"id": f"o{n}"} for n in range(1, size + 1)]
    seen = []
    for t in (0, 30, 60, 119, 120, 300):
        clock.now = T0 + t
        seen.append(ids(host._other_games_window(pool, limit)))
    assert " / ".join(seen) == expected


# ---------------------------------------------------------------------------
# The display path: _advance_other_games_if_due, _rotate_other_games_on_display
# ---------------------------------------------------------------------------

UPDATE_SLATE = (("u1", "1", "2", 1), ("u2", "3", "4", 2), ("u3", "5", "6", 3),
                ("u4", "7", "8", 4), ("u5", "2", "3", 5), ("u6", "4", "5", 6),
                ("u7", "6", "7", 7), ("u8", "1", "8", 8))


def updated(clock, favorites, favorite_limit=2, other_limit=2, recent=False, **kw):
    """What the plugins' update() leaves: _favorites_first's pick, its last card on screen."""
    host = Host(favorites=favorites, **kw)
    clock.now = T0
    games = [game(*row, recent=recent) for row in UPDATE_SLATE]
    host.games_list = host._favorites_first(games, favorite_limit, other_limit,
                                            newest_first=recent)
    if host.games_list:
        host.current_game_index = len(host.games_list) - 1
        host.current_game = host.games_list[-1]
    return host


def ticks(clock, host):
    """games_list@on screen after update() and each tick (^: redraw), and #composes."""
    calls = []
    compose = host._compose_selection

    def counted():
        calls.append(1)
        return compose()
    host._compose_selection = counted

    def shown():
        return f"{ids(host.games_list)}@{(host.current_game or {}).get('id')}"
    out = [shown()]
    for t in (30, 60, 90, 125, 305):
        clock.now = T0 + t
        redraw = host._rotate_other_games_on_display()
        out.append(shown() + ("^" if redraw else ""))
    return f"{' > '.join(out)} #{len(calls)}"


ROTATION = {
    "AAA, 2 others, 60s": (
        {"favorites": ["AAA"]},
        "u1,u2,u3,u8@u8 > u1,u2,u3,u8@u8 > u1,u4,u5,u8@u8^ > u1,u4,u5,u8@u8 > "
        "u1,u6,u7,u8@u8^ > u1,u6,u7,u8@u8 #3"),
    "pinned (0s)": (
        {"favorites": ["AAA"], "interval": 0},
        "u1,u2,u3,u8@u8 > u1,u2,u3,u8@u8 > u1,u2,u3,u8@u8 > u1,u2,u3,u8@u8 > "
        "u1,u2,u3,u8@u8 > u1,u2,u3,u8@u8 #0"),
    "0 others": (
        {"favorites": ["AAA"], "other_limit": 0},
        "u1,u8@u8 > u1,u8@u8 > u1,u8@u8 > u1,u8@u8 > u1,u8@u8 > u1,u8@u8 #0"),
    "no favourite slots, 3 others": (
        {"favorites": [], "favorite_limit": 0, "other_limit": 3},
        "u1,u2,u3@u3 > u1,u2,u3@u3 > u4,u5,u6@u4^ > u4,u5,u6@u4 > u1,u7,u8@u1^ > "
        "u1,u2,u8@u1^ #3"),
    "ranked, poll on the slate": (
        {"favorites": ["AAA"], "quality": "ranked", "rankings": RANKED},
        "u1,u3,u4,u8@u8 > u1,u3,u4,u8@u8 > u1,u2,u4,u8@u8^ > u1,u2,u4,u8@u8 > "
        "u1,u2,u3,u8@u8^ > u1,u2,u3,u8@u8 #3"),
    # The favourite still fills the board: no recompose on any frame.
    "ranked, poll matches nothing, favourite playing": (
        {"favorites": ["AAA"], "quality": "ranked", "rankings": {"ZZZ": 1}},
        "u1,u8@u8 > u1,u8@u8 > u1,u8@u8 > u1,u8@u8 > u1,u8@u8 > u1,u8@u8 #0"),
    # Nothing survived, so compose cuts the unfiltered fallback: it rotates too.
    "ranked, poll matches nothing, favourite not playing": (
        {"favorites": ["ZZZ"], "quality": "ranked", "rankings": {"ZZZ": 1}},
        "u1,u2@u2 > u1,u2@u2 > u3,u4@u3^ > u3,u4@u3 > u1,u2@u1^ > u3,u4@u3^ #3"),
}


@pytest.mark.parametrize("recent", [False, True], ids=["upcoming", "recent"])
@pytest.mark.parametrize("scenario", list(ROTATION))
def test_the_display_path_rotation(clock, scenario, recent):
    settings, expected = ROTATION[scenario]
    assert ticks(clock, updated(clock, recent=recent, **settings)) == expected


def test_no_pools_no_rotation(clock):
    """A manager whose update() never built the pools (ufc's MMA ones) never rotates."""
    host = Host()
    clock.now = T0 + 3600
    assert host._advance_other_games_if_due() == []
    assert host._rotate_other_games_on_display() is False


# ---------------------------------------------------------------------------
# update() and display() both advancing: the lock
# ---------------------------------------------------------------------------

def _racing(host):
    """Run display()'s rotation on a second thread inside update()'s advance.

    The first time this thread reads ``_other_window_start`` -- the advance's
    read-modify-write, after the interval was found elapsed -- the display path
    starts; it either finishes first (no lock) or waits on ``_games_lock``.
    """
    owner, threads = threading.get_ident(), []

    class Interleaved(type(host)):
        @property
        def _other_window_start(self):
            if threading.get_ident() == owner and not threads:
                display = threading.Thread(target=self._rotate_other_games_on_display,
                                           daemon=True)
                threads.append(display)
                display.start()
                display.join(2.0)
            return self.__dict__["_other_window_start"]

        @_other_window_start.setter
        def _other_window_start(self, value):
            self.__dict__["_other_window_start"] = value

    host.__class__ = Interleaved
    return threads


class _NoLock:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _update_again(host):
    host.games_list = host._favorites_first(
        [game(*row) for row in UPDATE_SLATE], 2, 2)


@pytest.mark.parametrize("lock, windows, shown", [
    (True, 1, "u1,u4,u5,u8"),
    (False, 2, "u1,u6,u7,u8"),       # what the eight plugins did before
])
def test_display_inside_updates_advance(clock, lock, windows, shown):
    host = updated(clock, ["AAA"])
    if not lock:
        host._games_lock = _NoLock()
    clock.now = T0 + 61
    threads = _racing(host)
    _update_again(host)
    for t in threads:
        t.join(10)
    assert threads, "update() never advanced the window"
    assert (host.__dict__["_other_window_start"] // 2, ids(host.games_list)) == (windows, shown)


@pytest.mark.parametrize("display_first", [False, True])
def test_update_and_display_in_sequence(clock, display_first):
    host = updated(clock, ["AAA"])
    clock.now = T0 + 61
    if display_first:
        host._rotate_other_games_on_display()
    _update_again(host)
    if not display_first:
        host._rotate_other_games_on_display()
    assert (host._other_window_start // 2, ids(host.games_list)) == (1, "u1,u4,u5,u8")


# ---------------------------------------------------------------------------
# Odds for rotated-in games
# ---------------------------------------------------------------------------

def _rotate_with_odds(clock, show_odds=True, manager=True, preset=None):
    host = updated(clock, ["AAA"])
    host.show_odds = show_odds
    host.odds_manager = MagicMock() if manager else None
    if manager:
        host.odds_manager.get_odds.side_effect = lambda **k: {"details": k["event_id"]}
    for g in host._selection_pools["favorites"] + host._selection_pools["others"]:
        if g["id"] in (preset or ()):
            g["odds"] = {"details": "already"}
    clock.now = T0 + 61
    assert host._rotate_other_games_on_display()
    for t in threading.enumerate():
        if t.name == "test-rotated-odds":
            t.join(10)
    return host


def test_rotated_in_games_get_odds(clock):
    host = _rotate_with_odds(clock, preset={"u1"})
    assert ids(host.games_list) == "u1,u4,u5,u8"
    assert {g["id"]: g["odds"]["details"] for g in host.games_list} == {
        "u1": "already", "u4": "u4", "u5": "u5", "u8": "u8"}
    asked = [c.kwargs["event_id"] for c in host.odds_manager.get_odds.call_args_list]
    assert asked == ["u4", "u5", "u8"]


@pytest.mark.parametrize("show_odds, manager", [(False, True), (True, False)])
def test_no_odds_without_show_odds_or_a_manager(clock, show_odds, manager):
    host = _rotate_with_odds(clock, show_odds=show_odds, manager=manager)
    assert not any(g.get("odds") for g in host.games_list)
    if manager:
        host.odds_manager.get_odds.assert_not_called()


# ---------------------------------------------------------------------------
# Carrier and host contract
# ---------------------------------------------------------------------------

METHODS = ["_advance_other_games_if_due", "_attach_odds_to_rotated_games",
           "_by_importance", "_other_games_window", "_rankings_loaded",
           "_rotate_other_games_on_display"]


def _self_reads():
    tree = ast.parse(Path(sports_rotation.__file__).read_text(encoding="utf-8"))
    return {node.attr for node in ast.walk(tree)
            if isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Load)
            and isinstance(node.value, ast.Name) and node.value.id == "self"}


class TestHostContract:
    def test_the_mixin_carries_only_the_family(self):
        """Every scoreboard's SportsCore has all six, so adopting it adds none."""
        assert sorted(n for n in vars(SportsRotationMixin)
                      if not n.startswith("__")) == METHODS

    def test_every_host_read_is_documented(self):
        undocumented = sorted(n for n in _self_reads()
                              if f"``{n}``" not in sports_rotation.__doc__)
        assert undocumented == [], f"read but not in the host contract: {undocumented}"

    def test_no_other_shared_mixin_defines_these(self):
        from src.common import sports_display_rules, sports_shared
        others = [sports_shared.SportsCoreSharedMixin, sports_shared.SportsRecentSharedMixin,
                  sports_shared.SportsLiveSharedMixin, SportsHelpersMixin,
                  SportsFavoritesMixin, sports_display_rules.SportsGameRulesMixin]
        assert [m for m in METHODS if any(m in vars(o) for o in others)] == []

    def test_the_host_class_wins(self):
        """football keeps its _rankings_loaded; the plugin's own method runs."""
        assert IdRankedHost(ranked_ids={1: 1})._rankings_loaded() is True
        assert Host()._rankings_loaded() is False
