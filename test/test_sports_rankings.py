"""src.common.sports_rankings: the poll, the tables, the season, the filter.

The cases follow ledmatrix-plugins' ``scripts/test_rankings.py`` (the tables
the family 8 reconcile was checked against); the expected values are its
columns, which agree across all nine scoreboards for everything this module
does. The host is the mixin over core's ``SportsCoreSharedMixin``
(``_league_has_rankings``, ``_game_divisions``) and ``SportsRotationMixin``
(``_rankings_loaded``), with a data source and an HTTP session that answer
from fixtures shaped like ESPN's payloads. Nothing reaches the network.
"""

import ast
import logging
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.common import sports_rankings
from src.common.sports_favorites import SportsFavoritesMixin
from src.common.sports_helpers import SportsHelpersMixin
from src.common.sports_rankings import SportsRankingsMixin, choose_top_division_poll
from src.common.sports_rotation import SportsRotationMixin
from src.common.sports_shared import SportsCoreSharedMixin

LOG = logging.getLogger("test_sports_rankings")
CORE_API = "https://sports.core.api.espn.com/v2/sports/"

UGA, ND, OSU, TEX, VAN, SDSU = ("61", "UGA"), ("87", "ND"), ("194", "OSU"), \
    ("251", "TEX"), ("238", "VAN"), ("21", "SDSU")
SDST, NDSU, NEWARK, FRST, DEN = ("2571", "SDST"), ("2449", "NDSU"), \
    ("9194", "OSU"), ("9001", "FRST"), ("2172", "DEN")
FBS_IDS, FCS_IDS = (UGA, ND, OSU, TEX, VAN, SDSU), (SDST, NDSU)


def poll(name, kind, *teams, **extra):
    return {"name": name, "shortName": name, "type": kind, **extra, "ranks": [
        {"current": n, "previous": n, "team": {"id": tid, "abbreviation": abbr}}
        for n, (tid, abbr) in enumerate(teams, 1)]}


AP = poll("AP Top 25", "ap", UGA, ND, OSU, SDSU)
COACHES = poll("AFCA Coaches Poll", "usa", ND, UGA, TEX)
CFP = poll("College Football Playoff Rankings", "cfp", OSU, UGA)
FCS = poll("FCS Coaches Poll", "fcs", NDSU, SDST)
D2 = poll("AFCA Division II Coaches Poll", "afca", FRST)
SEEDS = poll("NCAA Men's Hockey Tournament Seedings", "tournament", DEN)
USCHO = poll("USCHO Men's Poll", "USCHOMENSPOLL", DEN, ND)
AP_TABLE = {"ND": 2, "OSU": 3, "SDSU": 4, "UGA": 1}
AP_IDS = {194: 3, 21: 4, 61: 1, 87: 2}


def at(text):
    return datetime.fromisoformat(text).replace(tzinfo=timezone.utc).timestamp()


class Clock:
    def __init__(self):
        self.now = 100_000.0

    def __call__(self):
        return self.now


@pytest.fixture
def clock(monkeypatch):
    c = Clock()
    monkeypatch.setattr(sports_rankings.time, "time", c)
    monkeypatch.setattr(sports_rankings.time, "monotonic", c)
    return c


class Rosters:
    """The core API's division rosters, published up to ``last`` season."""

    def __init__(self, last=2026):
        self.last, self.urls = last, []

    def get(self, url, params=None, timeout=None):
        self.urls.append(url[len(CORE_API):])
        season, group = url.split("/seasons/")[1].split("/")[0], url.split("/groups/")[1][:2]
        ids = {"80": FBS_IDS, "81": FCS_IDS}[group] if int(season) <= self.last else ()
        resp = MagicMock()
        resp.json.return_value = {"items": [{"$ref": f"{CORE_API}x/teams/{t}?lang=en"}
                                            for t, _ in ids]}
        return resp


class Host(SportsRankingsMixin, SportsRotationMixin, SportsCoreSharedMixin,
           SportsFavoritesMixin, SportsHelpersMixin):
    """A scoreboard SportsCore as far as the rankings reach."""

    sport = "football"

    def __init__(self, league="college-football", payload=None, quality="any",
                 divisions=(), rosters=None):
        self.logger = LOG
        self.league = league
        self.data_source = MagicMock()
        self.data_source.fetch_standings.return_value = (
            {"rankings": [AP, COACHES]} if payload is None else payload)
        self.session = rosters or Rosters()
        self.cache_manager = MagicMock()
        self.cache_manager.get.return_value = None
        self._team_rankings_cache, self._ranked_team_ids = {}, {}
        self._rankings_cache_timestamp, self._rankings_cache_duration = 0, 3600
        self._division_team_ids, self._division_loaded_at = None, 0.0
        self.other_games_min_quality = quality
        self.other_games_divisions = list(divisions)


def tables(host):
    return host._team_rankings_cache, host._ranked_team_ids


# ---------------------------------------------------------------------------
# Which poll
# ---------------------------------------------------------------------------

POLL_SETS = [
    ([AP, COACHES], "AP Top 25"),
    ([COACHES, AP], "AFCA Coaches Poll"),
    ([CFP, AP, COACHES], "College Football Playoff Rankings"),
    ([FCS, D2, AP, COACHES], "AP Top 25"),
    ([FCS, D2], None),
    ([poll("Coaches Poll", "fcs", NDSU), AP], "AP Top 25"),     # by type alone
    ([SEEDS, USCHO], "USCHO Men's Poll"),
    ([], None),
    (None, None),
]


@pytest.mark.parametrize("blocks, expected", POLL_SETS)
def test_the_poll_chosen(blocks, expected):
    assert Host()._choose_poll(blocks).get("name") == expected
    assert choose_top_division_poll(blocks).get("name") == expected


def test_an_untyped_unnamed_block_counts():
    """An exclude list: a block ESPN invents is a poll unless it says otherwise."""
    assert choose_top_division_poll([{"ranks": []}]) == {"ranks": []}


# ---------------------------------------------------------------------------
# The tables
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("league, asked", [
    ("college-football", True), ("mens-college-basketball", True),
    ("usa.ncaa.m.1", True), ("nfl", False), ("nba", False), ("eng.1", False)])
def test_only_a_league_with_a_poll_is_asked(clock, league, asked):
    host = Host(league)
    host._fetch_team_rankings()
    assert host.data_source.fetch_standings.called is asked
    assert tables(host) == ((AP_TABLE, AP_IDS) if asked else ({}, {}))


@pytest.mark.parametrize("payload, abbr, ids", [
    ({}, {}, {}),
    ({"rankings": []}, {}, {}),
    ({"rankings": None}, {}, {}),
    ({"rankings": [{"name": "AP Top 25", "type": "ap"}]}, {}, {}),
    ({"rankings": [{"name": "AP Top 25", "type": "ap", "ranks": [
        {"current": 0, "team": {"id": "61", "abbreviation": "UGA"}},
        {"current": -1, "team": {"id": "87", "abbreviation": "ND"}},
        {"team": {"id": "194", "abbreviation": "OSU"}},
        {"current": 4, "team": {"id": "251", "abbreviation": "TEX"}}]}]},
     {"TEX": 4}, {251: 4}),
    ({"rankings": [{"name": "AP Top 25", "type": "ap", "ranks": [
        {"current": 1},
        {"current": 2, "team": {"id": "87"}},
        {"current": 3, "team": {"id": "abc", "abbreviation": "OSU"}},
        {"current": 4, "team": {"abbreviation": "TEX"}}]}]},
     {"OSU": 3, "TEX": 4}, {87: 2}),
    ({"rankings": [{"name": "AP Top 25", "type": "ap", "ranks": [     # #789: one bad entry
        {"current": None, "team": {"id": "61", "abbreviation": "UGA"}},
        {"current": "n/a", "team": {"id": "87", "abbreviation": "ND"}},
        {"current": 3, "team": {"id": "194", "abbreviation": "OSU"}}]}]},
     {"OSU": 3}, {194: 3}),
])
def test_payload_shapes(clock, payload, abbr, ids):
    host = Host(payload=payload)
    host._fetch_team_rankings()
    assert tables(host) == (abbr, ids)
    assert host._rankings_cache_timestamp == clock.now     # an empty answer is cached too


def test_a_failed_fetch_is_not_cached(clock):
    host = Host(payload={"rankings": {"name": "AP Top 25"}})   # not a list
    assert host._fetch_team_rankings() == {}
    assert host._rankings_cache_timestamp == 0


@pytest.mark.parametrize("payload", [{"rankings": [AP, COACHES]}, {"rankings": []}])
def test_rankings_are_fetched_hourly(clock, payload):
    host = Host(payload=payload)
    calls = []
    for step in (0, 60, 3599, 3601):
        clock.now = 100_000.0 + step
        before = host.data_source.fetch_standings.call_count
        host._fetch_team_rankings()
        calls.append(host.data_source.fetch_standings.call_count - before)
    assert calls == [1, 0, 0, 1]


# ---------------------------------------------------------------------------
# A poll from a season that has ended
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("end, now, kept", [
    ("2026-04-08T06:59Z", "2026-10-12T12:00", False),     # college basketball in October
    ("2026-04-08T06:59Z", "2026-04-08T06:58", True),
    ("2026-04-08T06:59Z", "2026-04-08T06:59", True),      # at endDate: not yet over
    ("2026-04-08T06:59Z", "2026-04-08T07:00", False),
    ("2027-01-28T07:59Z", "2027-01-10T12:00", True),      # bowl week
    ("2027-04-08T06:59Z", "2026-11-03T12:00", True),      # the new season's first poll
    ("2026-04-08T06:59:00Z", "2026-10-12T12:00", False),
    ("April", "2026-10-12T12:00", True),                  # unreadable: current
    (None, "2026-10-12T12:00", True),                     # no season block
])
def test_a_finished_seasons_poll_loads_nothing(clock, end, now, kept):
    block = dict(AP, season={"year": 2026, "endDate": end}) if end else AP
    host = Host(payload={"rankings": [block, COACHES]})
    clock.now = at(now)
    host._fetch_team_rankings()
    assert tables(host) == ((AP_TABLE, AP_IDS) if kept else ({}, {}))


# ---------------------------------------------------------------------------
# Division rosters
# ---------------------------------------------------------------------------

class Frozen(datetime):
    at = datetime(2026, 10, 12, 12)

    @classmethod
    def now(cls, tz=None):
        return cls.at


@pytest.mark.parametrize("today, season, sizes", [
    ("2026-01-15", 2025, (6, 2)), ("2026-06-30", 2025, (6, 2)),
    ("2026-07-01", 2026, (6, 2)), ("2026-10-12", 2026, (6, 2)),
    ("2026-12-31", 2026, (6, 2)), ("2027-01-15", 2026, (6, 2))])
def test_the_rosters_are_for_the_season_year(clock, monkeypatch, today, season, sizes):
    monkeypatch.setattr(sports_rankings, "datetime", Frozen)
    Frozen.at = datetime.fromisoformat(today)
    host = Host()
    rosters = host._load_division_team_ids()
    assert host.session.urls == [
        f"football/leagues/college-football/seasons/{season}/types/2/groups/{g}/teams"
        for g in (80, 81)]
    assert (len(rosters["fbs"]), len(rosters["fcs"])) == sizes


def test_no_rosters_outside_college_football(clock):
    host = Host("mens-college-basketball")
    assert host._load_division_team_ids() == {}
    assert host.session.urls == []


@pytest.mark.parametrize("last, cached, calls, sizes", [
    (2026, None, [2, 0, 0, 2], (6, 2)),          # a day
    (0, None, [2, 0, 2, 2], (0, 0)),             # empty: retried after 10 minutes
    (2026, "cache", [0, 0, 0, 0], (2, 1)),       # the cache manager's copy
])
def test_roster_caching(clock, last, cached, calls, sizes):
    host = Host(rosters=Rosters(last))
    if cached:
        host.cache_manager.get.side_effect = lambda key, *a, **k: (
            ["61", "87"] if key.endswith("_80") else ["2571"])
    made = []
    for step in (0, 60, 601, 86_401):
        clock.now = 100_000.0 + step
        before = len(host.session.urls)
        host._load_division_team_ids()
        made.append(len(host.session.urls) - before)
    rosters = host._division_team_ids
    assert made == calls
    assert (len(rosters["fbs"]), len(rosters["fcs"])) == sizes


# ---------------------------------------------------------------------------
# Ranked games and the filter
# ---------------------------------------------------------------------------

GAMES = {
    "UGA v TEX": (UGA, TEX),
    "TEX v VAN": (TEX, VAN),
    "OSU v VAN": (OSU, VAN),
    "Newark 'OSU' v VAN": (NEWARK, VAN),
    "SDSU v SDST": (SDSU, SDST),
    "SDST v NDSU": (SDST, NDSU),
    "'OSU' v VAN, no ids": ((None, "OSU"), (None, "VAN")),
    "61 v 238, no abbrs": (("61", None), ("238", None)),
}


def game(label):
    g = {"id": "g"}
    for side, (tid, abbr) in zip(("home", "away"), GAMES[label]):
        if tid is not None:
            g[f"{side}_id"] = tid
        if abbr is not None:
            g[f"{side}_abbr"] = abbr
    return g


def loaded(**kwargs):
    host = Host(**kwargs)
    host._fetch_team_rankings()
    return host


@pytest.mark.parametrize("label, best", [
    ("UGA v TEX", 1), ("TEX v VAN", 99), ("OSU v VAN", 3),
    ("Newark 'OSU' v VAN", 99),          # same abbreviation, another school: by id
    ("SDSU v SDST", 4), ("SDST v NDSU", 99),
    ("'OSU' v VAN, no ids", 3),          # no ids: by abbreviation
    ("61 v 238, no abbrs", 1)])
def test_ranked_and_best_rank(clock, label, best):
    host = loaded()
    assert host._best_rank(game(label)) == best
    assert host._is_ranked_game(game(label)) is (best != 99)


@pytest.mark.parametrize("home, away, best", [
    (NEWARK, (None, "VAN"), 99),         # Newark's id is unranked; "OSU" must not rank it
    (("61", None), (None, "VAN"), 1),    # UGA's id still counts with no id on the other side
])
def test_each_side_is_ranked_by_its_own_id(clock, home, away, best):
    """#789: one side's missing id no longer sends both sides to abbreviations."""
    g = {f"{side}_{key}": value for side, team in (("home", home), ("away", away))
         for key, value in zip(("id", "abbr"), team) if value is not None}
    assert loaded()._best_rank(g) == best


#: filter -> (quality, divisions, poll loaded?, kept: one Y/. per GAMES entry)
FILTERS = {
    "any": ("any", (), True, "YYYYYYYY"),
    "ranked": ("ranked", (), True, "Y.Y.Y.YY"),
    "ranked, no poll loaded": ("ranked", (), False, "YYYYYYYY"),
    "any, fbs": ("any", ("fbs",), True, "YYYYY.YY"),
    "ranked, fbs": ("ranked", ("fbs",), True, "Y.Y.Y.YY"),
    "any, fcs": ("any", ("fcs",), True, "....YYY."),
}


@pytest.mark.parametrize("name", FILTERS)
def test_passes_other_filters(clock, monkeypatch, name):
    quality, divisions, with_poll, kept = FILTERS[name]
    monkeypatch.setattr(sports_rankings, "datetime", Frozen)
    Frozen.at = datetime(2026, 10, 12, 12)
    host = loaded(payload={"rankings": [AP, COACHES] if with_poll else []},
                  quality=quality, divisions=divisions)
    assert "".join("Y" if host._passes_other_filters(game(g)) else "." for g in GAMES) == kept


# ---------------------------------------------------------------------------
# Carrier and host contract
# ---------------------------------------------------------------------------

MEMBERS = ["_DIVISION_CACHE_TTL", "_DIVISION_GROUPS_BY_LEAGUE", "_DIVISION_RETRY_SECONDS",
           "_NON_TOP_POLL_NAMES", "_NON_TOP_POLL_TYPES", "_best_rank", "_choose_poll",
           "_fetch_team_rankings", "_is_ranked_game", "_load_division_team_ids",
           "_passes_other_filters", "_poll_season_ended", "_ranked_positions"]


def _self_reads():
    tree = ast.parse(Path(sports_rankings.__file__).read_text(encoding="utf-8"))
    return {node.attr for node in ast.walk(tree)
            if isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Load)
            and isinstance(node.value, ast.Name) and node.value.id == "self"}


class TestHostContract:
    def test_the_mixin_carries_only_the_family(self):
        """Every scoreboard's SportsCore has all of them, so adopting it adds none."""
        assert sorted(n for n in vars(SportsRankingsMixin)
                      if not n.startswith("__")) == MEMBERS

    def test_every_host_read_is_documented(self):
        own = set(MEMBERS)
        undocumented = sorted(n for n in _self_reads() - own
                              if f"``{n}``" not in sports_rankings.__doc__)
        assert undocumented == [], f"read but not in the host contract: {undocumented}"

    def test_no_other_shared_mixin_defines_these(self):
        from src.common import sports_display_rules, sports_shared
        others = [sports_shared.SportsCoreSharedMixin, sports_shared.SportsRecentSharedMixin,
                  sports_shared.SportsLiveSharedMixin, SportsHelpersMixin,
                  SportsFavoritesMixin, SportsRotationMixin,
                  sports_display_rules.SportsGameRulesMixin]
        assert [m for m in MEMBERS if any(m in vars(o) for o in others)] == []
