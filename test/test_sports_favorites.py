"""src.common.sports_favorites: behaviour, the _favorite_key seam, host contract.

The cases follow ledmatrix-plugins' ``scripts/test_favourite_matching.py``
(the tables the family 6 reconcile was checked against), with the favourites
given as each plugin's resolver hands them over: as typed for the
abbreviation sports, as ESPN team ids for an NRL-style host that overrides
``_favorite_key``.
"""

import ast
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from src.common import sports_favorites
from src.common.sports_favorites import (
    SportsFavoritesMixin,
    SportsRecentFavoritesMixin,
    SportsUpcomingFavoritesMixin,
)
from src.common.sports_helpers import SportsHelpersMixin

LOG = logging.getLogger("test_sports_favorites")


def _id_key(self, game, side):
    """NRL's override: the ESPN team id, None when it is missing."""
    team_id = game.get(f"{side}_id")
    return None if team_id is None else str(team_id)


def host(favorites, by_id=False, limit=3):
    """A manager stand-in: the three mixins over SportsHelpersMixin's default key."""
    attrs = {"_favorite_key": _id_key} if by_id else {}
    cls = type("Host", (SportsUpcomingFavoritesMixin, SportsRecentFavoritesMixin,
                        SportsFavoritesMixin, SportsHelpersMixin), attrs)
    h = cls()
    h.logger = LOG
    h.favorite_teams = favorites
    h.upcoming_games_to_show = h.recent_games_to_show = limit
    return h


TEAM = {"1": "AAA", "2": "BBB", "3": "CCC", "4": "DDD", "41": "NEW", "42": "NEW"}


def match(home, away, **extra):
    g = {"home_id": home, "home_abbr": TEAM[home], "away_id": away, "away_abbr": TEAM[away]}
    g.update(extra)
    return g


GAMES = {
    "AAA home v BBB": match("1", "2"),
    "BBB home v AAA": match("2", "1"),
    "CCC v DDD": match("3", "4"),
    "Knights (NEW 41) v CCC": match("41", "3"),
    "Warriors (NEW 42) v CCC": match("42", "3"),
    "AAA v BBB, no ids": {"home_abbr": "AAA", "away_abbr": "BBB"},
    "ids 1 v 2, no abbrs": {"home_id": "1", "away_id": "2"},
    "AAA v BBB, int ids": match("1", "2", home_id=1, away_id=2),
    "lower-case abbrs": {"home_abbr": "aaa ", "away_abbr": "bbb"},
    "empty game": {},
}

#: label -> favorite_teams as the resolver hands it over.
FAVORITES = {
    "none": [],
    "AAA": ["AAA"],
    "aaa": ["aaa"],
    "' AAA '": [" AAA "],
    "1": ["1"],
    "int 1": [1],
    "AAA,CCC": ["AAA", "CCC"],
    "NEW": ["NEW"],
    "41": ["41"],
    "'None'": ["None"],
    "blank": ["", "  "],
}

#: (favourites, game) -> answer with the abbreviation key, then the id key.
EXPECTED_IS_FAVORITE = {
    "AAA home v BBB": {"AAA": "Y.", "aaa": "Y.", "' AAA '": "Y.", "1": ".Y", "int 1": ".Y",
                       "AAA,CCC": "Y."},
    "BBB home v AAA": {"AAA": "Y.", "aaa": "Y.", "' AAA '": "Y.", "1": ".Y", "int 1": ".Y",
                       "AAA,CCC": "Y."},
    "CCC v DDD": {"AAA,CCC": "Y."},
    "Knights (NEW 41) v CCC": {"AAA,CCC": "Y.", "NEW": "Y.", "41": ".Y"},
    "Warriors (NEW 42) v CCC": {"AAA,CCC": "Y.", "NEW": "Y."},
    "AAA v BBB, no ids": {"AAA": "Y.", "aaa": "Y.", "' AAA '": "Y.", "AAA,CCC": "Y."},
    "ids 1 v 2, no abbrs": {"1": ".Y", "int 1": ".Y"},
    "AAA v BBB, int ids": {"AAA": "Y.", "aaa": "Y.", "' AAA '": "Y.", "1": ".Y",
                           "int 1": ".Y", "AAA,CCC": "Y."},
    "lower-case abbrs": {"AAA": "Y.", "aaa": "Y.", "' AAA '": "Y.", "AAA,CCC": "Y."},
    "empty game": {},
}


@pytest.mark.parametrize("game_label", sorted(GAMES))
@pytest.mark.parametrize("fav_label", sorted(FAVORITES))
def test_is_favorite_game(fav_label, game_label):
    want = EXPECTED_IS_FAVORITE[game_label].get(fav_label, "..")
    got = "".join("Y" if host(FAVORITES[fav_label], by_id)._is_favorite_game(dict(GAMES[game_label]))
                  else "." for by_id in (False, True))
    assert got == want


class TestFavoriteCode:
    @pytest.mark.parametrize("value, code", [
        ("bos", "BOS"), (" BOS ", "BOS"), ("BOS", "BOS"), (41, "41"),
        ("", None), ("   ", None), (None, None),
    ])
    def test_normalises(self, value, code):
        assert SportsFavoritesMixin._favorite_code(value) == code

    def test_a_missing_id_is_not_the_string_none(self):
        """str(None) would match a favourite typed "None"; None matches nothing."""
        h = host(["None"], by_id=True)
        assert h._is_favorite_game({"home_abbr": "AAA", "away_abbr": "BBB"}) is False
        assert h._is_favorite_game({}) is False

    def test_a_none_favorites_list_matches_nothing(self):
        assert host(None)._is_favorite_game(dict(GAMES["AAA home v BBB"])) is False


# ---------------------------------------------------------------------------
# Selection. A shuffled slate: two games share id s2, two have no id, s9 has
# no start time. Hours from now; Recent gets the same slate in the past.
# ---------------------------------------------------------------------------

NOW = datetime(2026, 10, 5, 15, tzinfo=timezone.utc)
SLATE = (("s5", "41", "2", 5), ("s1", "1", "2", 1), ("s3", "4", "3", 3),
         ("s2", "3", "1", 2), ("s7", "2", "3", 7), ("s4", "1", "4", 4),
         ("s6", "42", "4", 6), ("s2", "1", "4", 8), ("s9", "1", "3", None),
         (None, "3", "1", 9), (None, "2", "1", 10))


def slate(recent):
    sign = -1 if recent else 1
    games = []
    for gid, home, away, hours in SLATE:
        g = match(home, away, id=gid)
        if hours is not None:
            g["start_time_utc"] = NOW + timedelta(hours=sign * hours)
        games.append(g)
    return games


def pick(favorites, limit, recent, by_id=False):
    h = host(favorites, by_id, limit)
    method = h._select_recent_games_for_display if recent else h._select_games_for_display
    return ",".join(g["id"] or "~" for g in method(slate(recent), favorites)) or "none"


ALL = "s1,s2,s3,s4,s5,s6,s7,s2,~,~,s9"

#: (favourites, per-team limit) -> picked ids, the same for Upcoming and Recent.
EXPECTED_SELECT = {
    (("AAA",), 1): "s1", (("AAA",), 2): "s1,s2", (("AAA",), 5): "s1,s2,s4,~,~",
    (("aaa",), 5): "s1,s2,s4,~,~", ((" AAA ",), 2): "s1,s2",
    (("AAA", "CCC"), 1): "s1,s2", (("AAA", "CCC"), 2): "s1,s2,s3",
    (("AAA", "CCC"), 5): "s1,s2,s3,s4,s7,~,~,s9",
    (("AAA", "ZZZ"), 2): "s1,s2", (("NEW",), 2): "s5,s6",
    (("ZZZ",), 2): "none", ((), 1): ALL,
}


@pytest.mark.parametrize("recent", [False, True], ids=["upcoming", "recent"])
@pytest.mark.parametrize("favorites, limit", sorted(EXPECTED_SELECT))
def test_select(favorites, limit, recent):
    assert pick(list(favorites), limit, recent) == EXPECTED_SELECT[(favorites, limit)]


class TestSelectByTeamId:
    """The NRL-style host: the key is the team id, so NEW is two teams."""

    @pytest.mark.parametrize("recent", [False, True])
    def test_one_club_of_a_shared_abbreviation(self, recent):
        assert pick(["41"], 2, recent, by_id=True) == "s5"

    @pytest.mark.parametrize("recent", [False, True])
    def test_an_unresolved_abbreviation_matches_nothing(self, recent):
        assert pick(["NEW"], 2, recent, by_id=True) == "none"

    def test_ids_select_like_abbreviations(self):
        assert pick(["1"], 5, False, by_id=True) == "s1,s2,s4,~,~"


class TestSelectionRules:
    def test_a_game_between_two_favourites_counts_for_both(self):
        h = host(["AAA", "BBB"], limit=1)
        picked = h._select_games_for_display(slate(False), ["AAA", "BBB"])
        assert [g["id"] for g in picked] == ["s1"]

    def test_games_without_an_id_are_never_duplicates(self):
        games = [match("1", "2"), match("1", "3")]
        assert len(host(["AAA"])._select_games_for_display(games, ["AAA"])) == 2

    def test_a_reused_id_is_a_duplicate(self):
        games = [match("1", "2", id="x"), match("1", "3", id="x")]
        assert len(host(["AAA"])._select_games_for_display(games, ["AAA"])) == 1

    def test_upcoming_is_soonest_first_and_recent_newest_first(self):
        assert pick(["CCC"], 5, False) == "s2,s3,s7,~,s9"
        assert pick(["CCC"], 5, True) == "s2,s3,s7,~,s9"

    def test_the_handed_list_is_used_not_favorite_teams(self):
        h = host(["CCC"], limit=1)
        assert [g["id"] for g in h._select_games_for_display(slate(False), ["AAA"])] == ["s1"]

    @pytest.mark.parametrize("recent", [False, True])
    def test_the_summary_is_logged_at_info(self, recent, caplog):
        with caplog.at_level(logging.INFO, logger=LOG.name):
            pick(["AAA"], 1, recent)
        name = "_select_recent_games_for_display" if recent else "_select_games_for_display"
        assert [r.levelno for r in caplog.records if r.funcName == name
                and r.levelno >= logging.INFO] == [logging.INFO]


# ---------------------------------------------------------------------------
# Carriers and host contract
# ---------------------------------------------------------------------------

MIXINS = {
    "SportsFavoritesMixin": ["_favorite_code", "_is_favorite_game"],
    "SportsUpcomingFavoritesMixin": ["_select_games_for_display"],
    "SportsRecentFavoritesMixin": ["_select_recent_games_for_display"],
}


def _classes():
    tree = ast.parse(Path(sports_favorites.__file__).read_text(encoding="utf-8"))
    return {n.name: n for n in tree.body if isinstance(n, ast.ClassDef)}


def _self_reads(cls):
    return {node.attr for node in ast.walk(cls)
            if isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Load)
            and isinstance(node.value, ast.Name) and node.value.id == "self"}


class TestHostContract:
    def test_each_mixin_carries_only_its_class_methods(self):
        """So adopting one gives no manager a method it did not have."""
        for name, methods in MIXINS.items():
            mixin = getattr(sports_favorites, name)
            assert sorted(n for n in vars(mixin) if not n.startswith("__")) == methods

    def test_every_host_read_is_documented(self):
        reads = set().union(*(_self_reads(c) for c in _classes().values()))
        undocumented = sorted(n for n in reads if f"``{n}``" not in sports_favorites.__doc__)
        assert undocumented == [], f"read but not in the host contract: {undocumented}"

    def test_the_key_comes_from_sports_helpers(self):
        """The seam stays where 3.5.0 put it; this module only calls it."""
        assert "_favorite_key" in vars(SportsHelpersMixin)
        assert all("_favorite_key" not in vars(getattr(sports_favorites, n)) for n in MIXINS)

    def test_no_other_shared_mixin_defines_these(self):
        from src.common import sports_display_rules, sports_shared
        others = [sports_shared.SportsCoreSharedMixin, sports_shared.SportsRecentSharedMixin,
                  sports_shared.SportsLiveSharedMixin, SportsHelpersMixin,
                  sports_display_rules.SportsGameRulesMixin]
        for methods in MIXINS.values():
            for name in methods:
                assert not any(name in vars(o) for o in others), name

    def test_the_shared_callers_reach_it(self):
        """_favorites_first and the live dwell ask _is_favorite_game; one body answers."""
        from src.common.sports_display_rules import SportsGameRulesMixin

        class Host(SportsGameRulesMixin, SportsFavoritesMixin, SportsHelpersMixin):
            favorite_teams = ["aaa"]
            game_display_duration = 15
            non_favorite_live_game_duration = 5

        assert Host()._effective_live_duration(dict(GAMES["AAA home v BBB"])) == 15
        assert Host()._effective_live_duration(dict(GAMES["CCC v DDD"])) == 5
