"""The twins: ``SportsCoreSharedMixin`` methods vs ``sports_card`` functions.

Every scoreboard draws the same game twice over: switch mode through its
``sports.py`` (``SportsCoreSharedMixin``, ``self._recent_score_color(...)``)
and scroll/Vegas mode through its ``game_renderer.py``
(``sports_card``, ``_card.recent_score_color(...)``). The two modules grew
same-named helpers independently, so this file calls each pair with the same
inputs and says which ones agree.

Two kinds of test live here, and the difference matters:

* ``TestIdentical`` -- pairs that agree on every input below. Most mixin
  methods in this set are now thin wrappers over the ``sports_card`` function,
  so the check is also what keeps a future "fix" to one side from quietly
  becoming a divergence (a mixin body re-grown, a wrapper given different
  arguments).
* ``TestPinnedDivergence`` -- pairs that do NOT agree. Their current behaviour
  is pinned on purpose, with the minimal input that shows the difference. A
  divergence here is user-visible (a colour, a weekday) in one display mode, and
  which side is right is an owner decision, not a refactor. When that decision
  is made, the test that pins it is the one to edit, deliberately.

The game corpus is the plugins' own harness fixtures
(``plugins/*/test/fixtures/mock.json`` in ledmatrix-plugins), reduced to the
keys these helpers read and embedded below, in the three payload shapes the
helpers are handed: flat (what ``_extract_game_details_common`` builds -- the
switch-mode input), flat plus nested (what the renderers'
``_normalize_game_payload`` hands the scroll card), and nested only. Point
``LEDMATRIX_PLUGINS`` at a ledmatrix-plugins checkout to add every event in
those fixtures to the corpus.
"""

import itertools
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from src.common import sports_card as C
from src.common.font_layout import load_truetype, resolve_asset_path
from src.common.sports_shared import SportsCoreSharedMixin

LOG = logging.getLogger("test_sports_twins")

# ---------------------------------------------------------------------------
# Corpus
# ---------------------------------------------------------------------------

#: (plugin, start, home abbr, home id, home score, away abbr, away id,
#: away score, state) -- one row per distinct event in the eight scoreboards'
#: test/fixtures/mock.json.
FIXTURE_EVENTS = [
    ("afl", "2026-07-10T09:40Z", "COLL", "17", "89", "NMFC", "5", "85", "post"),
    ("afl", "2026-07-11T03:15Z", "STK", "18", "0", "PORT", "7", "0", "pre"),
    ("afl", "2026-07-10T11:30Z", "FRE", "1", "54", "SYD", "4", "48", "in"),
    ("baseball", "2026-07-09T02:10Z", "LAD", "19", "5", "SF", "26", "3", "post"),
    ("baseball", "2026-07-10T10:05Z", "NYY", "10", "4", "BOS", "2", "3", "in"),
    ("baseball", "2026-07-11T23:10Z", "NYM", "21", "0", "ATL", "15", "0", "pre"),
    ("basketball", "2026-01-14T00:30Z", "BOS", "2", "112", "NY", "18", "104", "post"),
    ("basketball", "2026-01-15T03:30Z", "LAL", "13", "78", "GS", "9", "72", "in"),
    ("basketball", "2026-01-16T02:00Z", "DEN", "7", "0", "DAL", "6", "0", "pre"),
    ("football", "2026-01-14T01:15Z", "KC", "12", "27", "BUF", "2", "24", "post"),
    ("football", "2026-01-15T10:30Z", "PHI", "21", "17", "DAL", "6", "14", "in"),
    ("football", "2026-01-18T23:30Z", "DET", "8", "0", "GB", "9", "0", "pre"),
    ("hockey", "2026-01-14T00:00Z", "BOS", "1", "4", "TOR", "21", "2", "post"),
    ("hockey", "2026-01-15T10:30Z", "TB", "20", "3", "DAL", "9", "2", "in"),
    ("hockey", "2026-01-16T00:00Z", "CHI", "4", "0", "NYR", "13", "0", "pre"),
    ("lacrosse", "2026-04-14T18:00Z", "DUKE", "150", "14", "SYR", "183", "11", "post"),
    ("lacrosse", "2026-04-15T10:30Z", "JHU", "2305", "8", "UVA", "258", "7", "in"),
    ("lacrosse", "2026-04-16T22:00Z", "COR", "172", "0", "PSU", "213", "0", "pre"),
    ("nrl", "2026-07-10T09:00Z", "BRI", "16", "18", "PEN", "18", "12", "in"),
    ("nrl", "2026-07-09T09:00Z", "MEL", "12", "24", "SYD", "20", "10", "post"),
    ("nrl", "2026-07-12T09:00Z", "PAR", "14", "0", "PEN", "18", "0", "pre"),
    ("soccer", "2026-01-14T20:00Z", "ARS", "359", "2", "CHE", "363", "1", "post"),
    ("soccer", "2026-01-15T11:30Z", "LIV", "364", "1", "MNC", "382", "1", "in"),
    ("soccer", "2026-01-16T20:00Z", "TOT", "367", "0", "MAN", "360", "0", "pre"),
]

_LEAGUE = {"afl": "afl", "baseball": "mlb", "basketball": "nba", "football": "nfl",
           "hockey": "nhl", "lacrosse": "ncaa_mens_lacrosse", "nrl": "nrl",
           "soccer": "eng.1"}


def _extra_fixture_events():
    """Every event in a ledmatrix-plugins checkout, when one is named."""
    raw = os.environ.get("LEDMATRIX_PLUGINS")
    if not raw:
        return []
    root = Path(raw)
    if (root / "plugins").is_dir():
        root = root / "plugins"
    rows = []
    for path in sorted(root.glob("*-scoreboard/test/fixtures/mock.json")):
        plugin = path.parts[-4].replace("-scoreboard", "")
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for block in data.values():
            for ev in (block.get("events") or []) if isinstance(block, dict) else []:
                try:
                    comp = ev["competitions"][0]
                    sides = {c["homeAway"]: c for c in comp["competitors"]}
                    rows.append((plugin, ev["date"],
                                 sides["home"]["team"]["abbreviation"],
                                 sides["home"]["team"]["id"], sides["home"].get("score"),
                                 sides["away"]["team"]["abbreviation"],
                                 sides["away"]["team"]["id"], sides["away"].get("score"),
                                 ""))
                except (KeyError, IndexError, TypeError):
                    continue
    return rows


def _flat(row):
    plugin, start, ha, hid, hs, aa, aid, as_, _state = row
    return {
        "league": _LEAGUE.get(plugin, plugin),
        "home_abbr": ha, "home_id": hid, "home_score": hs,
        "away_abbr": aa, "away_id": aid, "away_score": as_,
        "start_time_utc": datetime.fromisoformat(start.replace("Z", "+00:00")),
    }


def _with_nested(game):
    """The scroll card's input: flat keys kept, nested team dicts added."""
    out = dict(game)
    for side in ("home", "away"):
        out[f"{side}_team"] = {"abbrev": game.get(f"{side}_abbr"),
                               "id": game.get(f"{side}_id"),
                               "score": game.get(f"{side}_score")}
    return out


def _nested_only(game):
    out = {k: v for k, v in _with_nested(game).items()
           if not k.startswith(("home_abbr", "home_id", "home_score",
                                "away_abbr", "away_id", "away_score"))}
    return out


_ROWS = FIXTURE_EVENTS + _extra_fixture_events()
FLAT_GAMES = [_flat(r) for r in _ROWS]
EDGE_FLAT_GAMES = [
    # NRL: abbreviations are not unique, ids are.
    {"league": "nrl", "home_abbr": "NEW", "home_id": "4", "home_score": "20",
     "away_abbr": "NEW", "away_id": "12", "away_score": "10"},
    {"league": "nfl", "home_abbr": "KC", "away_abbr": "BUF"},
    {"league": "nfl", "home_abbr": "KC", "away_abbr": "BUF", "home_score": "", "away_score": ""},
    {"league": "nfl", "home_abbr": "KC", "away_abbr": "BUF", "home_score": "-", "away_score": "-"},
    {"league": "nfl", "home_abbr": "KC", "away_abbr": "BUF", "home_score": "5.0",
     "away_score": "2.0"},
    {"league": "nfl", "home_abbr": "KC", "home_id": 12, "away_abbr": "BUF", "away_id": 2,
     "home_score": 3, "away_score": 3},
    {"league": "nfl", "home_abbr": None, "away_abbr": "BUF", "home_score": "1",
     "away_score": "2"},
    {"league": "nfl", "home_abbr": " kc ", "away_abbr": "BUF", "home_score": "1",
     "away_score": "2"},
]
ALL_FLAT = FLAT_GAMES + EDGE_FLAT_GAMES
SCROLL_SHAPED = [_with_nested(g) for g in ALL_FLAT]


def _favorite_choices(game):
    """Every way a favourites list can relate to this game."""
    out = [[], ["AP_TOP_25"], ["NOBODY"]]
    for side in ("home", "away"):
        for key in ("abbr", "id"):
            value = game.get(f"{side}_{key}")
            if value is not None:
                out.append([str(value)])
                out.append([" " + str(value).lower() + " "])
    if game.get("home_abbr") and game.get("away_abbr"):
        out.append([game["home_abbr"], game["away_abbr"]])
    return out


# ---------------------------------------------------------------------------
# Hosts
# ---------------------------------------------------------------------------

class _Host(SportsCoreSharedMixin):
    """The mixin with just the state these helpers read."""

    def __init__(self, config=None, favorites=None, tz=timezone.utc, fonts=None):
        self.config = config
        self.favorite_teams = favorites
        self.logger = LOG
        self.fonts = fonts or {}
        self._tz = tz

    def _get_timezone(self):
        return self._tz


#: The map seven of the eight scoreboards' sports.py declare over the mixin's
#: default (football is the one that inherits the default).
PLUGIN_ELEMENT_FOR_FONT = {
    "odds": "odds_text", "score": "score_text", "time": "period_text",
    "team": "team_name", "status": "status_text", "detail": "detail_text",
    "rank": "rank_text",
}


def _call(fn, *args):
    """Result or the exception type, so a raise on one side is a difference."""
    try:
        return fn(*args)
    except Exception as exc:  # noqa: BLE001 - the type is the result here
        return f"<raises {type(exc).__name__}>"


def _mismatches(pairs):
    return [(label, a, b) for label, a, b in pairs if a != b]


RESULT_COLOURS = [
    {"enabled": True},
    {"enabled": True, "win_color": [1, 2, 3], "loss_color": "123",
     "tie_color": [999, -1, "7"]},
    {"enabled": False},
    {},
]

SCROLL_CARD_CONFIGS = [
    None, {}, {"scroll_card": None}, {"scroll_card": {}}, {"scroll_card": "notadict"},
    {"scroll_card": {"vs_text": "@", "date_format": "weekday", "time_format": "24h",
                     "switch_date_format": "inherit"}},
    {"scroll_card": {"vs_text": None, "date_format": "day_first", "time_format": "12h",
                     "switch_date_format": "inherit"}},
    {"scroll_card": {"vs_text": 7, "date_format": "numeric", "switch_date_format": "inherit"}},
    {"scroll_card": {"date_format": "numeric_day_first", "time_format": "24h",
                     "switch_date_format": "inherit"}},
    {"scroll_card": {"date_format": "abbrev", "switch_date_format": "inherit"}},
    {"scroll_card": {"date_format": "bogus", "switch_date_format": "inherit"}},
]
TIMES = ["7:30 PM", "12:00 AM", "12:05pm", "7 PM", "13:00 PM", "TBD", "", None,
         "7:61 PM", "x:30 PM", " 9:05 am ", "12:00 PM", "0:15 AM"]
DATES = ["9/19", "09-19", "13/19", "Sep 19", "", None, "9/19/2026", " 1/2 ", "0/5"]
STARTS = [datetime(2026, 9, 19, 23, 30, tzinfo=timezone.utc), "2026-09-19T23:30Z",
          "2026-09-20T02:00:00+00:00", "garbage", None, "", datetime(2026, 1, 1)]
TIMEZONES = ["America/New_York", "Australia/Sydney", "UTC", "Not/AZone", None]

PS = resolve_asset_path("assets/fonts/PressStart2P-Regular.ttf")
F46 = resolve_asset_path("assets/fonts/4x6-font.ttf")


def _font_sets():
    a, b, c = load_truetype(PS, 8), load_truetype(PS, 16), load_truetype(F46, 7)
    keys = ("odds", "score", "time", "team", "status", "detail", "rank")
    return {
        "distinct": {"score": a, "time": b, "team": c, "status": load_truetype(PS, 8),
                     "detail": load_truetype(F46, 7), "rank": load_truetype(F46, 14),
                     "odds": load_truetype(F46, 7)},
        "score+time share": {"score": a, "time": a, "team": c},
        "team+rank share": {"score": a, "time": b, "team": c, "rank": c},
        "odds+score share": {"score": a, "odds": a, "time": b},
        "all share": {k: a for k in keys},
    }


def _partition(fonts):
    """Which keys still share one face object -- what unsharing decides."""
    groups = {}
    for key, font in fonts.items():
        groups.setdefault(id(font), []).append(key)
    return sorted(sorted(keys) for keys in groups.values())


def _faces(fonts):
    return {k: (getattr(f, "path", None), getattr(f, "size", None)) for k, f in fonts.items()}


def _schema_dir(tmp_path, name, text):
    d = tmp_path / name
    d.mkdir()
    if text is not None:
        (d / "config_schema.json").write_text(text)
    return d


SCHEMAS = {
    "good": json.dumps({"properties": {"customization": {"properties": {
        "score_text": {"properties": {"font_size": {"default": 10}}},
        "period_text": {"properties": {"font_size": {"default": 8}}},
        "detail_text": {"properties": {"font_size": {"default": "6"}}},
        "team_name": {"properties": {"font": {"default": "x"}}}}}}}),
    "bad_json": "{not json",
    "bad_default": json.dumps({"properties": {"customization": {"properties": {
        "score_text": {"properties": {"font_size": {"default": "big"}}}}}}}),
    "missing": None,
}


# ---------------------------------------------------------------------------
# Identical pairs
# ---------------------------------------------------------------------------

class TestIdentical:
    """Pairs that agree on every input. Keep it that way."""

    def test_scroll_card_option(self):
        pairs = []
        for i, cfg in enumerate(SCROLL_CARD_CONFIGS):
            host = _Host(cfg)
            for key, default in itertools.product(
                    ("vs_text", "date_format", "time_format", "missing"), (None, "D", 0)):
                pairs.append((f"cfg{i} {key} {default!r}",
                              _call(host._card_option, key, default),
                              _call(C.scroll_card_option, cfg, key, default)))
        assert not _mismatches(pairs)

    def test_vs_text(self):
        pairs = [(f"cfg{i}", _call(_Host(cfg)._vs_text), _call(C.vs_text, cfg))
                 for i, cfg in enumerate(SCROLL_CARD_CONFIGS)]
        assert not _mismatches(pairs)

    def test_format_game_time(self):
        pairs = [(f"cfg{i} {t!r}", _call(_Host(cfg)._format_game_time, t),
                  _call(C.format_game_time, cfg, t))
                 for (i, cfg), t in itertools.product(enumerate(SCROLL_CARD_CONFIGS), TIMES)]
        assert not _mismatches(pairs)

    def test_coerce_rgb(self):
        values = [[1, 2, 3], (300, -4, "5"), "123", [1, 2], [1, 2, 3, 4], None, 42,
                  {"r": 1, "g": 2, "b": 3}, ["a", 1, 2], [1.9, 2, 3], [None, 1, 2]]
        pairs = [(repr(v), _call(_Host._coerce_rgb, v, (4, 5, 6)),
                  _call(C.coerce_rgb, v, (4, 5, 6))) for v in values]
        assert not _mismatches(pairs)

    def test_crisp_size(self):
        names = ["PressStart2P-Regular.ttf", "4x6-font.ttf", "press_start", "four_by_six",
                 "5by7.regular.ttf", "user.ttf", None]
        sizes = [None, 0, -3, 1, 4, 6, 7, 8, 9, 10, 11, 12, 13, 14, 16, 20, 7.5, "8"]
        pairs = [(f"{n} {s!r}", _call(_Host._crisp_size, n, s), _call(C.crisp_size, n, s))
                 for n, s in itertools.product(names, sizes)]
        assert not _mismatches(pairs)

    def test_crisp_size_honours_a_hosts_own_tables(self):
        """A class that declares extra faces keeps them through the wrapper."""
        cls = type("Extra", (_Host,), {"_FONT_PIXEL_GRID": {"extra.ttf": 5},
                                       "_FONT_NAME_ALIASES": {"x": "extra.ttf"}})
        assert cls._crisp_size("x", 12) == C.crisp_size("x", 12, {"x": "extra.ttf"},
                                                         {"extra.ttf": 5}) == 10

    def test_constant_tables(self):
        assert SportsCoreSharedMixin.FAVORITE_RESULT_COLOR_DEFAULTS == \
            C.FAVORITE_RESULT_COLOR_DEFAULTS
        assert SportsCoreSharedMixin._MONTH_ABBR == C.MONTH_ABBR
        assert SportsCoreSharedMixin._WEEKDAY_ABBR == C.WEEKDAY_ABBR
        assert SportsCoreSharedMixin._FONT_PIXEL_GRID == C.FONT_PIXEL_GRID
        assert SportsCoreSharedMixin._FONT_NAME_ALIASES == C.FONT_NAME_ALIASES

    def test_constant_dicts_are_not_aliased(self):
        # Equal, but separate objects: a caller mutating one table (tests do)
        # must not reach into the other module.
        assert SportsCoreSharedMixin.FAVORITE_RESULT_COLOR_DEFAULTS is not \
            C.FAVORITE_RESULT_COLOR_DEFAULTS
        assert SportsCoreSharedMixin._FONT_PIXEL_GRID is not C.FONT_PIXEL_GRID
        assert SportsCoreSharedMixin._FONT_NAME_ALIASES is not C.FONT_NAME_ALIASES

    @pytest.mark.parametrize("schema", sorted(SCHEMAS))
    def test_schema_font_size_and_resolve_font_size(self, tmp_path, schema):
        d = _schema_dir(tmp_path, schema, SCHEMAS[schema])
        host = type("H_" + schema, (_Host,), {"_PLUGIN_DIR": str(d)})()
        path = str(d / "config_schema.json")
        pairs = []
        for key in ("score_text", "period_text", "detail_text", "team_name", "nope", "", None):
            pairs.append((f"schema {key!r}", _call(host._schema_font_size, key),
                          _call(C.schema_font_size, path, key)))
            for ec, name, size in itertools.product(
                    (None, {}, {"font_size": 10}, {"font_size": "10"}, {"font_size": 11},
                     {"font_size": "big"}, {"font_size": None}, {"font_size": 8.7}),
                    ("PressStart2P-Regular.ttf", "4x6-font.ttf", "press_start", "user.ttf"),
                    (6, 8, 10, None)):
                pairs.append((f"resolve {key!r} {ec} {name} {size}",
                              _call(host._resolve_font_size, ec, key, size, name),
                              _call(C.resolve_font_size, path, ec, key, size, name)))
        assert not _mismatches(pairs)

    @pytest.mark.parametrize("element_map", ["mixin default", "plugin sports.py"])
    def test_unshare_element_fonts_given_the_same_map(self, element_map):
        """Same map in, same faces out. (The maps themselves differ; pinned below.)"""
        mapping = (SportsCoreSharedMixin._ELEMENT_FOR_FONT if element_map == "mixin default"
                   else PLUGIN_ELEMENT_FOR_FONT)
        host = type("H", (_Host,), {"_ELEMENT_FOR_FONT": mapping})()
        for name, fonts in _font_sets().items():
            mine, theirs = dict(fonts), dict(fonts)
            host._unshare_element_fonts(mine)
            C.unshare_element_fonts(LOG, theirs, mapping)
            assert _partition(mine) == _partition(theirs), name
            assert _faces(mine) == _faces(theirs), name

    def test_unshare_element_fonts_default_map_is_unchanged(self):
        """Omitting the new argument keeps the card's own map."""
        for name, fonts in _font_sets().items():
            default, explicit = dict(fonts), dict(fonts)
            C.unshare_element_fonts(LOG, default)
            C.unshare_element_fonts(LOG, explicit, C.ELEMENT_FOR_FONT)
            assert _partition(default) == _partition(explicit), name

    def test_format_game_date_when_both_read_the_same_setting_and_zone(self):
        """With ``switch_date_format: inherit`` the scorebug reads the card's
        ``date_format``; given the same zone the two then format identically."""
        pairs = []
        for (i, cfg), tzname in itertools.product(enumerate(SCROLL_CARD_CONFIGS[5:]),
                                                   TIMEZONES):
            conf = dict(cfg, timezone=tzname) if tzname else dict(cfg)
            host = _Host(conf, tz=C.card_tzinfo(conf, LOG))
            for d, start in itertools.product(DATES, STARTS):
                game = {"start_time_utc": start} if start is not None else {}
                pairs.append((f"cfg{i} tz={tzname} {d!r} {start!r}",
                              _call(host._format_game_date, d, game),
                              _call(C.format_game_date, conf, LOG, d, game)))
                pairs.append((f"weekday cfg{i} tz={tzname} {start!r}",
                              _call(host._weekday_for, game),
                              _call(C.weekday_for, conf, LOG, game)))
        assert not _mismatches(pairs)

    def test_format_game_date_honours_a_hosts_month_table(self):
        """The scoreboards redeclare _MONTH_ABBR; the shared body must read it."""
        cls = type("Months", (_Host,), {"_MONTH_ABBR": tuple(f"M{i}" for i in range(1, 13))})
        host = cls({"scroll_card": {"switch_date_format": "abbrev"}})
        assert host._format_game_date("9/19") == "M9 19"

    def test_favorite_result_on_the_games_the_scoreboards_build(self):
        """Production shape: the extractor stamps ``favorite_teams`` (the
        manager's resolved list) on every game, and the manager holds the same
        list. On those games -- flat for switch mode, flat plus nested for the
        scroll card -- the two sides agree on every result and every colour."""
        pairs = []
        for game in ALL_FLAT:
            for favs in _favorite_choices(game):
                stamped = dict(game, favorite_teams=list(favs))
                for colours in RESULT_COLOURS:
                    cfg = {"customization": {"favorite_result_colors": colours}}
                    host = _Host(cfg, favorites=list(favs))
                    pairs.append((f"{game} {favs}",
                                  _call(host._favorite_result, stamped),
                                  _call(C.favorite_result, cfg, _with_nested(stamped))))
                    pairs.append((f"{game} {favs} {colours}",
                                  _call(host._recent_score_color, stamped, (9, 9, 9)),
                                  _call(C.recent_score_color, cfg, LOG,
                                        _with_nested(stamped), (9, 9, 9))))
        assert not _mismatches(pairs)
        # And the corpus is not vacuous: every verdict actually occurs.
        verdicts = {a for _, a, _ in pairs if isinstance(a, str) or a is None}
        assert {"win", "loss", "tie", None} <= verdicts

    def test_side_is_favorite_on_flat_games(self):
        pairs = []
        for game in ALL_FLAT:
            for favs in _favorite_choices(game):
                fav_set = {str(f).strip().upper() for f in favs if str(f).strip()}
                for side in ("home", "away"):
                    pairs.append((f"{game} {side} {fav_set}",
                                  _call(_Host._side_is_favorite, game, side, fav_set),
                                  _call(C.side_is_favorite, game, side, fav_set)))
        assert not _mismatches(pairs)

    def test_nrl_collision_is_resolved_the_same_way_on_both_sides(self):
        """The _favorite_key seam: NRL's "NEW" is two clubs. Neither helper
        calls the seam; both match abbreviation OR id, so an id favourite picks
        one club and an abbreviation favourite picks both (no verdict)."""
        game = EDGE_FLAT_GAMES[0]
        for favs, expected in ((["4"], "win"), (["12"], "loss"), (["NEW"], None)):
            host = _Host({}, favorites=favs)
            assert host._favorite_result(game) == expected
            assert C.favorite_result({"favorite_teams": favs}, game) == expected

    def test_an_ambiguous_nrl_abbreviation_tints_on_both_sides(self):
        """Agreed -- and at odds with NRL's own favourite rule.

        NRL's resolver logs a shared abbreviation ("NEW") as an error and
        passes it through unchanged, and its _is_favorite_game matches ids
        only, so selection never treats "NEW" as a favourite. Both colour
        helpers match on abbreviation too, so both modes tint a Knights result
        (and a Warriors one) for a user who typed "NEW". Not a twin
        divergence; a seam neither helper consults.
        """
        on = {"customization": {"favorite_result_colors": {"enabled": True}}}
        game = {"league": "3", "home_abbr": "NEW", "home_id": "4", "home_score": "20",
                "away_abbr": "MEL", "away_id": "12", "away_score": "10",
                "favorite_teams": ["NEW"]}
        cfg = dict(on, favorite_teams=["NEW"])
        assert _Host(cfg, favorites=["NEW"])._recent_score_color(game, (9, 9, 9)) \
            == C.recent_score_color(cfg, LOG, game, (9, 9, 9)) == (0, 255, 0)


# ---------------------------------------------------------------------------
# Pinned divergences -- owner decision pending. Edit deliberately.
# ---------------------------------------------------------------------------

class TestPinnedDivergence:
    """Each test pins one difference between the twins as it stands today.

    None of these is changed by the consolidation that made the identical pairs
    wrappers: each one is a colour, a weekday or a font face that one display
    mode shows differently from the other, so choosing a side is a product
    decision. If you are here because one of these failed, you changed which
    side wins -- make sure that was the decision, then update the pin.
    """

    NESTED_WIN = {"league": "nhl",
                  "home_team": {"abbrev": "TB", "score": "4"},
                  "away_team": {"abbrev": "BOS", "score": "1"}}

    def test_side_is_favorite_nested_payload(self):
        # DIVERGENCE: the mixin reads only the flat <side>_abbr / <side>_id keys;
        # the card also reads <side>_team.{abbrev,abbreviation,id}. Unreachable
        # from the scoreboards' own extractors (always flat), reachable from a
        # nested-only payload.
        assert _Host._side_is_favorite(self.NESTED_WIN, "home", {"TB"}) is False
        assert C.side_is_favorite(self.NESTED_WIN, "home", {"TB"}) is True

    def test_favorite_result_nested_payload(self):
        # DIVERGENCE: follows from the one above, plus score source: the mixin
        # reads home_score/away_score only; the card prefers the nested score.
        host = _Host({}, favorites=["TB"])
        game = dict(self.NESTED_WIN, favorite_teams=["TB"])
        assert host._favorite_result(game) is None
        assert C.favorite_result({}, game) == "win"

    def test_favorite_result_when_nested_and_flat_scores_disagree(self):
        # DIVERGENCE: same game, two score sources. The mixin uses the flat
        # score, the card the nested one. The renderers' normaliser only fills
        # a nested score that is missing, so this needs a payload that already
        # carried both.
        game = {"home_abbr": "TB", "away_abbr": "BOS", "home_score": "1",
                "away_score": "4", "home_team": {"abbrev": "TB", "score": "4"},
                "away_team": {"abbrev": "BOS", "score": "1"}, "favorite_teams": ["TB"]}
        assert _Host({}, favorites=["TB"])._favorite_result(game) == "loss"
        assert C.favorite_result({}, game) == "win"

    def test_favorite_result_favourite_sources(self):
        # DIVERGENCE: where the favourites come from. The mixin reads only
        # self.favorite_teams (the manager's list, resolved at construction);
        # the card reads the game's stamped favorite_teams plus the config's
        # league block (or root). All eight scoreboards stamp the game, so in
        # production both see the same list -- this pins the hand-built case.
        game = {"league": "mlb", "home_abbr": "ATL", "away_abbr": "NYM",
                "home_score": "5", "away_score": "2"}
        on = {"customization": {"favorite_result_colors": {"enabled": True}}}
        # Host favourites only, nothing stamped, nothing in config:
        assert _Host(on, favorites=["ATL"])._favorite_result(game) == "win"
        assert C.favorite_result(on, game) is None
        # Config league block only, host list empty:
        cfg = dict(on, mlb={"favorite_teams": ["ATL"]})
        assert _Host(cfg, favorites=[])._favorite_result(game) is None
        assert C.favorite_result(cfg, game) == "win"
        # Stamped on the game only, host list empty:
        stamped = dict(game, favorite_teams=["ATL"])
        assert _Host(on, favorites=[])._favorite_result(stamped) is None
        assert C.favorite_result(on, stamped) == "win"
        # ...which is what reaches the colour:
        assert _Host(on, favorites=["ATL"])._recent_score_color(game, (9, 9, 9)) == (0, 255, 0)
        assert C.recent_score_color(on, LOG, game, (9, 9, 9)) == (9, 9, 9)

    def test_weekday_zone_source(self):
        # DIVERGENCE, user-visible: the scorebug asks the plugin's
        # _get_timezone() (plugin setting -> global setting -> system zone);
        # the card reads only config["timezone"] and falls back to UTC. The
        # scoreboards' schemas default that key to "", and the scroll display
        # hands the renderer the plugin config, so a board that sets only the
        # global zone gets UTC weekdays in scroll mode: an evening kickoff in
        # New York is labelled with the next day.
        game = {"start_time_utc": "2026-09-20T00:30:00+00:00"}  # Sat 20:30 EDT
        host = _Host({}, tz=ZoneInfo("America/New_York"))
        assert host._weekday_for(game) == "Sat"
        assert C.weekday_for({}, LOG, game) == "Sun"
        cfg = {"scroll_card": {"date_format": "weekday", "switch_date_format": "inherit"}}
        host = _Host(cfg, tz=ZoneInfo("America/New_York"))
        assert host._format_game_date("9/19", game) == "Sat Sep 19"
        assert C.format_game_date(cfg, LOG, "9/19", game) == "Sun Sep 19"

    def test_weekday_out_of_range_start(self):
        # DIVERGENCE: the mixin catches OverflowError from astimezone() and
        # drops the weekday; the card lets it escape to its caller.
        game = {"start_time_utc": "9999-12-31T23:59:00+00:00"}
        sydney = {"timezone": "Australia/Sydney"}
        assert _Host(sydney, tz=ZoneInfo("Australia/Sydney"))._weekday_for(game) == ""
        with pytest.raises(OverflowError):
            C.weekday_for(sydney, LOG, game)

    def test_date_format_setting(self):
        # DIVERGENCE BY DESIGN (documented on _switch_date_format): the scorebug
        # reads scroll_card.switch_date_format (default "numeric", the "9/19"
        # it has always drawn); the card reads scroll_card.date_format (default
        # "abbrev"). "inherit" opts the scorebug into the card's setting.
        assert _Host({})._format_game_date("9/19") == "9/19"
        assert C.format_game_date({}, LOG, "9/19") == "Sep 19"

    def test_upcoming_centre_setting(self):
        # DIVERGENCE BY DESIGN: not a same-named twin, but the same question.
        # switch_upcoming_center defaults to "date_time"; the card's
        # upcoming_center to "vs". "inherit" opts the scorebug in.
        assert _Host({})._switch_upcoming_center() == "date_time"
        assert C.upcoming_center_mode({}) == "vs"
        cfg = {"scroll_card": {"switch_upcoming_center": "inherit"}}
        assert _Host(cfg)._switch_upcoming_center() == C.upcoming_center_mode(cfg) == "vs"

    def test_element_for_font_maps(self):
        # DIVERGENCE: the element vocabulary. The mixin default says team_text
        # and has no rank/odds; the card says team_name and has rank but no
        # odds. Seven scoreboards override the mixin map in sports.py with
        # PLUGIN_ELEMENT_FOR_FONT (team_name, rank, odds); football inherits
        # the default, and its schema declares team_name, not team_text.
        assert SportsCoreSharedMixin._ELEMENT_FOR_FONT == {
            "score": "score_text", "time": "period_text", "team": "team_text",
            "detail": "detail_text", "status": "status_text"}
        assert C.ELEMENT_FOR_FONT == {
            "score": "score_text", "time": "period_text", "team": "team_name",
            "status": "status_text", "detail": "detail_text", "rank": "rank_text"}

    def test_font_color_team_element(self):
        # DIVERGENCE (consequence of the maps): a colour set on team_name
        # reaches the card's team face but not the mixin-default one.
        team = load_truetype(F46, 7)
        fonts = {"score": load_truetype(PS, 8), "team": team}
        cfg = {"customization": {"team_name": {"text_color": [1, 1, 1]}}}
        assert _Host(cfg, fonts=fonts)._font_color(team, (7, 7, 7)) == (7, 7, 7)
        assert C.font_color(cfg, fonts, team, (7, 7, 7)) == (1, 1, 1)
        plugin_host = type("P", (_Host,), {"_ELEMENT_FOR_FONT": PLUGIN_ELEMENT_FOR_FONT})
        assert plugin_host(cfg, fonts=fonts)._font_color(team, (7, 7, 7)) == (1, 1, 1)

    def test_unshare_element_fonts_odds_face(self):
        # DIVERGENCE (consequence of the maps): the scoreboards' sports.py map
        # includes "odds", so switch mode gives the odds face its own object;
        # the card's map has no "odds", so scroll mode leaves it sharing the
        # score's face (and _card.font_color then colours it as score_text).
        shared = load_truetype(PS, 8)
        mine = {"score": shared, "odds": shared}
        theirs = dict(mine)
        type("P", (_Host,), {"_ELEMENT_FOR_FONT": PLUGIN_ELEMENT_FOR_FONT})() \
            ._unshare_element_fonts(mine)
        C.unshare_element_fonts(LOG, theirs)
        assert mine["odds"] is not mine["score"]
        assert theirs["odds"] is theirs["score"]

    def test_schema_default_cache_lifetimes(self, tmp_path):
        # DELIBERATE, and the reason there are still two caches: the mixin
        # caches per class, the card per schema path. The display service
        # builds new classes when it reloads a plugin, so switch mode picks up
        # an edited schema then; the card's module-level cache does not. One
        # shared cache would change what switch mode does after a reload.
        d = _schema_dir(tmp_path, "reload", SCHEMAS["good"])
        path = str(d / "config_schema.json")
        first = type("First", (_Host,), {"_PLUGIN_DIR": str(d)})()
        assert first._schema_font_size("score_text") == 10
        assert C.schema_font_size(path, "score_text") == 10
        (d / "config_schema.json").write_text(SCHEMAS["good"].replace("10", "16"))
        reloaded = type("Reloaded", (_Host,), {"_PLUGIN_DIR": str(d)})()
        assert reloaded._schema_font_size("score_text") == 16
        assert first._schema_font_size("score_text") == 10
        assert C.schema_font_size(path, "score_text") == 10

    def test_element_color_mode(self):
        # DIVERGENCE at the call site, not in a body: both resolve through
        # src.element_style, but the mixin passes the instance's SKIN_MODE
        # ("live"/"recent"/"upcoming", set by all eight scoreboards) and the
        # renderers call _card.element_color with no mode, so a per-mode colour
        # override applies in switch mode only.
        cfg = {"customization": {"score_text": {"text_color": [255, 0, 0]},
                                 "modes": {"recent": {"score_text": {"text_color": [0, 0, 255]}}}}}
        host = _Host(cfg)
        host.SKIN_MODE = "recent"
        assert host._element_color("score_text") == (0, 0, 255)
        assert C.element_color(cfg, "score_text") == (255, 0, 0)
