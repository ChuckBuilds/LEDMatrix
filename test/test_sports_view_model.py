"""src.common.sports_view_model: the game dict built from one ESPN event.

The cases follow ledmatrix-plugins' ``scripts/test_view_model.py`` (the table
the family 10 reconcile was checked against); the expected values are its
``EXPECTED_COMMON`` cells, which agree across all nine scoreboards once the
reconcile landed (nrl differs only through the ``_logo_filename`` seam and
its id favourites, which are host data). Each row is one change to one base
event, a scheduled AAA (home, id 1) v BBB (away, id 2) at 7:00 PM EDT, and
lists only the keys whose value differs from the base game. The host shows
times in America/New_York. Nothing reaches the network.
"""

import ast
import copy
import logging
from datetime import datetime
from pathlib import Path

import pytest
import pytz

from src.common import sports_view_model
from src.common.sports_view_model import SportsViewModelMixin

LOGO_DIR = Path("logos")


class Host(SportsViewModelMixin):
    def __init__(self, config=None):
        self.logger = logging.getLogger("test_sports_view_model")
        self.config = config or {}
        self.logo_dir = LOGO_DIR
        self.favorite_teams = ["AAA"]

    def _get_timezone(self):
        return pytz.timezone("America/New_York")


def competitor(home_away, tid, abbr, name, record):
    return {"id": tid, "homeAway": home_away, "score": "0",
            "team": {"id": tid, "abbreviation": abbr, "name": name,
                     "logo": f"https://a.espncdn.com/i/teamlogos/{abbr.lower()}.png"},
            "records": [{"type": "total", "summary": record}]}


def base_event():
    return {"id": "401", "date": "2026-10-12T23:00Z", "competitions": [{
        "id": "4011", "broadcast": "ESPN",
        "status": {"displayClock": "0:00", "period": 0, "type": {
            "name": "STATUS_SCHEDULED", "state": "pre", "completed": False,
            "shortDetail": "10/12 - 7:00 PM EDT"}},
        "competitors": [competitor("home", "1", "AAA", "Alpha", "3-1"),
                        competitor("away", "2", "BBB", "Bravo", "2-2")]}]}


BASE = {
    "id": "401", "game_time": "7:00PM", "game_date": "10/12",
    "start_time_utc": datetime(2026, 10, 12, 23, 0, tzinfo=pytz.UTC),
    "status_text": "10/12 - 7:00 PM EDT", "is_live": False, "is_final": False,
    "is_upcoming": True, "is_halftime": False, "is_period_break": False,
    "broadcast": "ESPN", "home_abbr": "AAA", "home_id": "1", "home_score": "0",
    "home_logo_path": LOGO_DIR / "AAA.png",
    "home_logo_url": "https://a.espncdn.com/i/teamlogos/aaa.png",
    "home_record": "3-1", "away_record": "2-2", "away_abbr": "BBB", "away_id": "2",
    "away_score": "0", "away_logo_path": LOGO_DIR / "BBB.png",
    "away_logo_url": "https://a.espncdn.com/i/teamlogos/bbb.png",
    "is_within_window": True, "favorite_teams": ["AAA"],
    "period": 0, "clock": "",
}


def comp(e):
    return e["competitions"][0]


def side(e, home_away):
    return next(c for c in comp(e)["competitors"] if c["homeAway"] == home_away)


def status(state, name, short, completed=False, drop_completed=False):
    def edit(e):
        st = {"name": name, "state": state, "completed": completed, "shortDetail": short}
        if drop_completed:
            del st["completed"]
        comp(e)["status"] = {"displayClock": "5:32", "period": 2, "type": st}
    return edit


def scores(home, away):
    def edit(e):
        side(e, "home")["score"], side(e, "away")["score"] = home, away
    return edit


def drop_scores(e):
    for c in comp(e)["competitors"]:
        del c["score"]


def both(fn):
    def edit(e):
        for c in comp(e)["competitors"]:
            fn(c)
    return edit


def dated(date):
    def edit(e):
        e["date"] = date
    return edit


def chain(*edits):
    def edit(e):
        for fn in edits:
            fn(e)
    return edit


def utc(*args):
    return datetime(*args, tzinfo=pytz.UTC)


FINAL = status("post", "STATUS_FINAL", "Final", completed=True)
POST = {"is_upcoming": False, "is_final": True, "status_text": "Final"}

#: label -> (edit, extra host config, keys that differ from BASE; None: no game)
ROWS = {
    "pre": (None, None, {}),
    "short date format": (dated("2026-03-05T23:00Z"), {"display": {"use_short_date_format": True}},
                          {"game_date": "3/5", "game_time": "6:00PM",
                           "start_time_utc": utc(2026, 3, 5, 23, 0)}),
    "in progress": (status("in", "STATUS_IN_PROGRESS", "5:32 - 2nd"), None,
                    {"is_live": True, "is_upcoming": False, "status_text": "5:32 - 2nd"}),
    "halftime": (status("in", "STATUS_HALFTIME", "Halftime"), None,
                 {"is_live": True, "is_halftime": True, "is_upcoming": False,
                  "status_text": "Halftime"}),
    "end of period": (status("in", "STATUS_END_PERIOD", "End of 1st"), None,
                      {"is_live": True, "is_period_break": True, "is_upcoming": False,
                       "status_text": "End of 1st"}),
    "suspended while in": (status("in", "STATUS_SUSPENDED", "Suspended"), None,
                           {"is_upcoming": False, "status_text": "Suspended"}),
    "final": (FINAL, None, POST),
    "final, no completed flag": (status("post", "STATUS_FINAL", "Final", drop_completed=True),
                                 None, POST),
    "final, completed false": (status("post", "STATUS_FINAL", "Final"), None,
                               {"is_upcoming": False, "status_text": "Final"}),
    "postponed": (status("post", "STATUS_POSTPONED", "Postponed"), None,
                  {"is_upcoming": False, "status_text": "Postponed"}),
    "canceled": (status("post", "STATUS_CANCELED", "Canceled"), None,
                 {"is_upcoming": False, "status_text": "Canceled"}),
    "delayed, completed": (status("post", "STATUS_DELAYED", "Delayed", completed=True), None,
                           {"is_upcoming": False, "status_text": "Delayed"}),
    "forfeit, completed": (status("post", "STATUS_FORFEIT", "Forfeit", completed=True), None,
                           {**POST, "status_text": "Forfeit"}),
    "forfeit pending": (status("post", "STATUS_FORFEIT_PENDING", "Forfeit", completed=True),
                        None, {"is_upcoming": False, "status_text": "Forfeit"}),
    "scores int": (chain(FINAL, scores(21, 3)), None, {**POST, "home_score": "21", "away_score": "3"}),
    "scores '21.0'": (chain(FINAL, scores("21.0", " 3 ")), None,
                      {**POST, "home_score": "21", "away_score": "3"}),
    "scores dict": (chain(FINAL, scores({"value": 21.0, "displayValue": "21"},
                                        {"value": 0, "displayValue": "4"})), None,
                    {**POST, "home_score": "21", "away_score": "4"}),
    "scores None and ''": (chain(FINAL, scores(None, "")), None, POST),
    "scores missing": (chain(FINAL, drop_scores), None, POST),
    "scores not numbers": (chain(FINAL, scores("SO", "12 (3)")), None, {**POST, "away_score": "12"}),
    "records 0-0 and 0-0-0": (lambda e: (side(e, "home")["records"][0].update(summary="0-0"),
                                         side(e, "away")["records"][0].update(summary="0-0-0")),
                              None, {"home_record": "", "away_record": ""}),
    "records missing": (both(lambda c: c.pop("records")), None,
                        {"home_record": "", "away_record": ""}),
    "record, team-schedule shape": (both(lambda c: (c.pop("records"),
                                                    c.update(record=[{"displayValue": "7-0"}]))),
                                    None, {"home_record": "7-0", "away_record": "7-0"}),
    "no abbreviation": (both(lambda c: c["team"].pop("abbreviation")), None,
                        {"home_abbr": "Alp", "away_abbr": "Bra",
                         "home_logo_path": LOGO_DIR / "ALP.png",
                         "away_logo_path": LOGO_DIR / "BRA.png"}),
    "logos array only": (both(lambda c: c["team"].update(
        logo=None, logos=[{"href": "https://a.espncdn.com/x.png"}])), None,
        {"home_logo_url": "https://a.espncdn.com/x.png", "away_logo_url": "https://a.espncdn.com/x.png"}),
    "logo and logos": (both(lambda c: c["team"].update(logos=[{"href": "https://a.espncdn.com/x.png"}])),
                       None, {}),
    "no logos": (both(lambda c: c["team"].pop("logo")), None,
                 {"home_logo_url": None, "away_logo_url": None}),
    "competitor without id": (lambda e: side(e, "home").pop("id"), None, {}),
    "no ids at all": (lambda e: (side(e, "home").pop("id"), side(e, "home")["team"].pop("id")),
                      None, {"home_id": None}),
    "broadcast object": (lambda e: comp(e).update(broadcast={"names": ["ESPN"]}), None,
                         {"broadcast": "{'names': ['ESPN']}"}),
    "broadcast missing": (lambda e: comp(e).pop("broadcast"), None, {"broadcast": ""}),
    "date 'TBD'": (dated("TBD"), None, {"game_date": "", "game_time": "", "start_time_utc": None}),
    "date +10:00": (dated("2026-10-13T19:40+10:00"), None,
                    {"game_date": "10/13", "game_time": "5:40AM",
                     "start_time_utc": utc(2026, 10, 13, 9, 40)}),
    "date naive, read as UTC": (dated("2026-10-12T23:00:00"), None, {}),
    "late UTC, previous local day": (dated("2026-10-13T03:30Z"), None,
                                     {"game_time": "11:30PM", "start_time_utc": utc(2026, 10, 13, 3, 30)}),
    "DST ends, 05:30Z (EDT)": (dated("2026-11-01T05:30Z"), None,
                               {"game_date": "11/01", "game_time": "1:30AM",
                                "start_time_utc": utc(2026, 11, 1, 5, 30)}),
    "DST ends, 06:30Z (EST)": (dated("2026-11-01T06:30Z"), None,
                               {"game_date": "11/01", "game_time": "1:30AM",
                                "start_time_utc": utc(2026, 11, 1, 6, 30)}),
    "DST starts, 07:30Z": (dated("2026-03-08T07:30Z"), None,
                           {"game_date": "03/08", "game_time": "3:30AM",
                            "start_time_utc": utc(2026, 3, 8, 7, 30)}),
    "naive, repeated DST hour": (dated("2026-11-01T01:30:00"), None,
                                 {"game_date": "10/31", "game_time": "9:30PM",
                                  "start_time_utc": utc(2026, 11, 1, 1, 30)}),
    "one competitor": (lambda e: comp(e)["competitors"].pop(), None, None),
    "no competitors": (lambda e: comp(e).pop("competitors"), None, None),
    "no competitions": (lambda e: e.update(competitions=[]), None, None),
    "no status": (lambda e: comp(e).pop("status"), None, None),
    "no date": (lambda e: e.pop("date"), None, None),
}


@pytest.mark.parametrize("label", ROWS)
def test_the_game_dict(label):
    edit, config, changed = ROWS[label]
    event = base_event()
    if edit:
        edit(event)
    details, home, away, st, situation = Host(config)._extract_game_details_common(event)
    if changed is None:
        assert (details, home, away, st, situation) == (None,) * 5
        return
    assert details == {**BASE, **changed}
    assert home is side(event, "home") and away is side(event, "away")
    assert st is comp(event)["status"] and situation is None


def test_an_empty_event_is_no_game():
    assert Host()._extract_game_details_common({}) == (None,) * 5


def test_the_situation_is_handed_back():
    event = base_event()
    comp(event)["situation"] = {"possession": "1"}
    assert Host()._extract_game_details_common(event)[4] == {"possession": "1"}


def test_the_logo_filename_seam():
    class ById(Host):
        def _logo_filename(self, abbr, team_id):
            return f"{abbr}_{team_id}.png"

    details = ById()._extract_game_details_common(base_event())[0]
    assert (details["home_logo_path"], details["away_logo_path"]) == (
        LOGO_DIR / "AAA_1.png", LOGO_DIR / "BBB_2.png")


def test_favourites_are_a_copy():
    host = Host()
    host.favorite_teams = None
    assert host._extract_game_details_common(base_event())[0]["favorite_teams"] == []
    host.favorite_teams = ["AAA"]
    details = host._extract_game_details_common(base_event())[0]
    assert details["favorite_teams"] == ["AAA"] and details["favorite_teams"] is not host.favorite_teams


@pytest.mark.parametrize("status_type, final", [
    ({"state": "post", "name": "STATUS_FINAL", "completed": True}, True),
    ({"state": "post", "name": "STATUS_FINAL"}, True),
    ({"state": "post", "name": "status_final", "completed": True}, True),
    ({"state": "post", "name": "STATUS_FINAL", "completed": False}, False),
    ({"state": "post", "name": "STATUS_FORFEIT", "completed": True}, True),
    ({"state": "in", "name": "STATUS_IN_PROGRESS"}, False),
    ({"state": "post"}, True),
    (None, False),
    ("post", False),
] + [({"state": "post", "name": n, "completed": True}, False)
     for n in sorted(SportsViewModelMixin._NOT_PLAYED_STATUS_NAMES)])
def test_status_is_final(status_type, final):
    assert SportsViewModelMixin._status_is_final(status_type) is final


def test_the_status_lists():
    assert SportsViewModelMixin._CALLED_OFF_STATUS_NAMES == {
        "STATUS_POSTPONED", "STATUS_CANCELED", "STATUS_CANCELLED",
        "STATUS_SUSPENDED", "STATUS_ABANDONED"}
    assert SportsViewModelMixin._NOT_PLAYED_STATUS_NAMES == (
        SportsViewModelMixin._CALLED_OFF_STATUS_NAMES
        | {"STATUS_DELAYED", "STATUS_FORFEIT_PENDING", "STATUS_NO_CONTEST_PENDING"})


@pytest.mark.parametrize("score, text", [
    (21, "21"), (21.9, "21"), ("21", "21"), ("21.0", "21"), (" 7 ", "7"), ("", "0"),
    (None, "0"), ("SO", "0"), ("12 (3)", "12"), ({"value": 21.0}, "21"),
    ({"value": 0, "displayValue": "4"}, "4"), ({"value": None}, "0"), ([], "0"),
    (float("nan"), "0"), (float("inf"), "0"),
])
def test_score_text(score, text):
    assert Host()._score_text(score) == text


def test_records_do_not_mutate_the_event():
    event = base_event()
    before = copy.deepcopy(event)
    Host()._extract_game_details_common(event)
    assert event == before


# ---------------------------------------------------------------------------
# Carrier and host contract
# ---------------------------------------------------------------------------

MEMBERS = ["_CALLED_OFF_STATUS_NAMES", "_NOT_PLAYED_STATUS_NAMES",
           "_extract_game_details_common", "_extract_team_record", "_logo_filename",
           "_score_text", "_status_is_final", "_team_logo_url"]


def _self_reads():
    tree = ast.parse(Path(sports_view_model.__file__).read_text(encoding="utf-8"))
    return {node.attr for node in ast.walk(tree)
            if isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Load)
            and isinstance(node.value, ast.Name) and node.value.id in ("self", "cls")}


class TestHostContract:
    def test_the_mixin_carries_only_the_family(self):
        assert sorted(n for n in vars(SportsViewModelMixin) if not n.startswith("__")) == MEMBERS

    def test_every_host_read_is_documented(self):
        undocumented = sorted(n for n in _self_reads() - set(MEMBERS)
                              if f"``{n}``" not in sports_view_model.__doc__)
        assert undocumented == [], f"read but not in the host contract: {undocumented}"

    def test_every_key_is_in_the_key_table(self):
        doc = sports_view_model.__doc__
        table = doc[doc.index("THE KEYS"):doc.index("THE RULES")]
        missing = [k for k in BASE if k not in table]
        assert missing == []

    def test_no_other_shared_mixin_defines_these(self):
        from src.common import (sports_display_rules, sports_favorites, sports_fetch,
                                sports_helpers, sports_live_fetch, sports_rankings,
                                sports_rotation, sports_shared)
        others = [sports_shared.SportsCoreSharedMixin, sports_shared.SportsRecentSharedMixin,
                  sports_shared.SportsLiveSharedMixin, sports_helpers.SportsHelpersMixin,
                  sports_favorites.SportsFavoritesMixin, sports_rotation.SportsRotationMixin,
                  sports_rankings.SportsRankingsMixin, sports_fetch.SportsFetchMixin,
                  sports_live_fetch.SportsLiveFetchMixin,
                  sports_display_rules.SportsGameRulesMixin]
        assert [m for m in MEMBERS if any(m in vars(o) for o in others)] == []
