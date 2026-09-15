"""src.common.sports_helpers: behaviour, host contract, and parity with the plugins.

The bodies were copied from the scoreboard plugins' sports.py, so the plugins'
own tests already cover them there. They are ported here for two reasons: core
now owns a copy that can drift on its own, and a plugin that deletes its copy
loses those tests with it.

The parity class is what keeps "byte-identical" true after this lands. Point
LEDMATRIX_PLUGINS at a ledmatrix-plugins checkout and every promoted body is
compared, as a docstring-stripped AST, against every plugin copy that carries
it. Without the variable it skips rather than fails, since core CI has no
plugins checkout; ledmatrix-plugins CI runs the same comparison against core
(scripts/check_sports_helpers_parity.py, ledmatrix-plugins#495).
"""

import ast
import logging
import math
import os
import time
from pathlib import Path

import pytest
from PIL import Image
from PIL.PngImagePlugin import PngInfo

from src.common import sports_helpers
from src.common.sports_helpers import (
    MAX_WINDOW_DAYS,
    MIN_WINDOW_DAYS,
    SportsHelpersMixin,
    clamp_seconds,
    clamp_window,
    logo_needs_refresh,
    spread_weighted_order,
)
from src.common.sports_shared import SportsCoreSharedMixin


# ---------------------------------------------------------------------------
# Free functions
# ---------------------------------------------------------------------------

class TestClampWindow:
    def test_within_range_is_kept(self):
        assert clamp_window(14, 7) == 14
        assert clamp_window("30", 7) == 30

    def test_clamped_to_bounds(self):
        assert (MIN_WINDOW_DAYS, MAX_WINDOW_DAYS) == (1, 60)
        assert clamp_window(0, 7) == 1
        assert clamp_window(-5, 7) == 1
        assert clamp_window(365, 7) == 60

    @pytest.mark.parametrize("bad", [None, "abc", [], {}, float("nan")])
    def test_unusable_falls_back(self, bad):
        assert clamp_window(bad, 7) == 7

    @pytest.mark.parametrize("inf", [float("inf"), float("-inf")])
    def test_infinity_falls_back_instead_of_raising(self, inf):
        # json.loads accepts a bare Infinity; int(inf) raises OverflowError.
        with pytest.raises(OverflowError):
            int(inf)
        assert clamp_window(inf, 7) == 7


class TestClampSeconds:
    def test_defaults_bound_five_seconds_to_a_day(self):
        assert clamp_seconds(1, 60) == 5
        assert clamp_seconds(10 ** 9, 60) == 86400
        assert clamp_seconds(300, 60) == 300

    def test_custom_bounds(self):
        assert clamp_seconds(1, 60, low=30, high=900) == 30
        assert clamp_seconds(5000, 60, low=30, high=900) == 900

    @pytest.mark.parametrize("bad", [None, "x", float("nan"), float("inf")])
    def test_unusable_falls_back(self, bad):
        assert clamp_seconds(bad, 60) == 60


def _png(path, size=(64, 64), color=(100, 100, 100, 255), marker=None):
    img = Image.new("RGBA", size, color)
    info = None
    if marker is not None:
        info = PngInfo()
        info.add_text("ledmatrix_placeholder", marker)
    img.save(path, "PNG", pnginfo=info)
    return path


class TestLogoNeedsRefresh:
    def test_stale_placeholder_is_retried(self, tmp_path):
        from src.logo_downloader import PLACEHOLDER_RETRY_SECONDS
        stamp = str(time.time() - PLACEHOLDER_RETRY_SECONDS - 60)
        assert logo_needs_refresh(_png(tmp_path / "A.png", marker=stamp)) is True

    def test_fresh_placeholder_is_trusted(self, tmp_path):
        assert logo_needs_refresh(_png(tmp_path / "A.png", marker=str(time.time()))) is False

    def test_real_logo_is_never_refreshed(self, tmp_path):
        assert logo_needs_refresh(_png(tmp_path / "A.png", size=(32, 32),
                                       color=(200, 0, 0, 255))) is False

    def test_missing_file_is_not_a_placeholder(self, tmp_path):
        assert logo_needs_refresh(tmp_path / "nope.png") is False

    def test_core_without_placeholder_marking_keeps_old_behaviour(self, monkeypatch, tmp_path):
        # A None entry in sys.modules makes the import raise ImportError, the
        # same thing an older core without these names does.
        monkeypatch.setitem(__import__("sys").modules, "src.logo_downloader", None)
        stamp = str(time.time() - 10 ** 7)
        assert logo_needs_refresh(_png(tmp_path / "A.png", marker=stamp)) is False

    def test_the_import_is_deferred(self):
        tree = ast.parse(Path(sports_helpers.__file__).read_text(encoding="utf-8"))
        top = [n for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom))]
        assert not any(isinstance(n, ast.ImportFrom) and n.module == "src.logo_downloader"
                       for n in top)


def _back_to_back(cycle):
    return any(cycle[i] == cycle[(i + 1) % len(cycle)] for i in range(len(cycle)))


class TestSpreadWeightedOrder:
    def test_equal_weights_are_plain_order(self):
        assert spread_weighted_order([1, 1, 1]) == [0, 1, 2]
        assert spread_weighted_order([]) == []

    def test_a_favourite_at_the_end_wraps_its_extra_turn(self):
        order = spread_weighted_order([1, 1, 1, 2])
        assert sorted(order) == [0, 1, 2, 3, 3]
        assert not _back_to_back(order)

    @pytest.mark.parametrize("weights,expected", [
        ([3, 1, 1], [0, 1, 0, 2, 0]),
        ([1, 3, 1], [0, 1, 1, 2, 1]),
        ([5, 1], None),
        ([2, 1], None),
    ])
    def test_ratio_wins_over_spacing(self, weights, expected):
        order = spread_weighted_order(weights)
        assert [order.count(i) for i in range(len(weights))] == weights
        if expected is not None:
            assert order == expected

    def test_mixin_exposes_it_as_a_staticmethod(self):
        assert SportsHelpersMixin._spread_weighted_order([1, 2]) == spread_weighted_order([1, 2])
        assert isinstance(SportsHelpersMixin.__dict__["_spread_weighted_order"], staticmethod)


# ---------------------------------------------------------------------------
# Mixin methods, against stand-in hosts carrying only what each reads
# ---------------------------------------------------------------------------

class _Dwell(SportsHelpersMixin):
    def __init__(self, last_game_switch):
        self._last_display_call_monotonic = 0.0
        self.last_game_switch = last_game_switch


class TestResetDwellOnReentry:
    def test_first_frame_resets(self):
        mgr = _Dwell(time.time() - 6.0)
        assert mgr._reset_dwell_on_reentry() is True
        assert time.time() - mgr.last_game_switch < 1.0

    def test_frames_inside_one_stint_do_not(self):
        mgr = _Dwell(time.time() - 6.0)
        mgr._reset_dwell_on_reentry()
        stamp = mgr.last_game_switch
        assert [mgr._reset_dwell_on_reentry() for _ in range(3)] == [False] * 3
        assert mgr.last_game_switch == stamp

    def test_returning_after_a_long_gap_resets(self):
        mgr = _Dwell(time.time() - 150.0)
        mgr._last_display_call_monotonic = time.monotonic() - 150.0
        assert mgr._reset_dwell_on_reentry() is True

    def test_just_under_the_threshold_is_the_same_stint(self):
        mgr = _Dwell(time.time() - 150.0)
        mgr._last_display_call_monotonic = time.monotonic() - (
            SportsHelpersMixin._DWELL_REENTRY_GAP_SECONDS - 1.0)
        stamp = mgr.last_game_switch
        assert mgr._reset_dwell_on_reentry() is False
        assert mgr.last_game_switch == stamp

    def test_live_zero_sentinel_survives(self):
        mgr = _Dwell(0)
        assert mgr._reset_dwell_on_reentry() is False
        assert mgr.last_game_switch == 0

    def test_host_without_any_state(self):
        mgr = SportsHelpersMixin()
        assert mgr._reset_dwell_on_reentry() is False  # no last_game_switch
        assert mgr._last_display_call_monotonic > 0


def _game(gid, home="X", away="Y"):
    return {"id": gid, "home_abbr": home, "away_abbr": away}


class _Switch(SportsHelpersMixin):
    def __init__(self, games, boost=None, favorites=("UF",)):
        self.games_list = games
        self.favorite_teams = list(favorites)
        if boost is not None:
            self.favorite_rotation_boost = boost
        self.current_game_index = 0

    def _is_favorite_game(self, g):
        return g["home_abbr"] in self.favorite_teams or g["away_abbr"] in self.favorite_teams

    def walk(self, steps):
        shown = []
        for _ in range(steps):
            self.current_game_index = self._next_switch_index()
            shown.append(self.games_list[self.current_game_index]["id"])
        return shown


class TestNextSwitchIndex:
    def test_no_boost_attribute_is_the_plain_rotation(self):
        s = _Switch([_game("a"), _game("b", "UF"), _game("c")])
        assert s.walk(6) == ["b", "c", "a", "b", "c", "a"]

    def test_boost_without_a_favourite_changes_nothing(self):
        assert _Switch([_game("a"), _game("b"), _game("c")], boost=3).walk(3) == ["b", "c", "a"]

    def test_boost_two_spreads_the_favourite(self):
        s = _Switch([_game("a"), _game("uf", "UF"), _game("c"), _game("d")], boost=2)
        cycle = s.walk(5)
        assert sorted(cycle) == ["a", "c", "d", "uf", "uf"]
        assert not _back_to_back(cycle)
        assert s.walk(5) == cycle

    def test_boost_three_two_favourites(self):
        s = _Switch([_game("a", "UF"), _game("b"), _game("c", "FSU"), _game("d"), _game("e")],
                    boost=3, favorites=("UF", "FSU"))
        cycle = s.walk(9)
        assert cycle.count("a") == 3 and cycle.count("c") == 3

    def test_index_reset_by_update_is_followed(self):
        s = _Switch([_game("a"), _game("uf", "UF"), _game("c"), _game("d")], boost=2)
        s.walk(3)
        s.current_game_index = 0
        assert s.walk(1)[0] == "uf"

    def test_single_card_stays_put(self):
        assert _Switch([_game("uf", "UF")], boost=5).walk(3) == ["uf", "uf", "uf"]


class _Settings(SportsHelpersMixin):
    def __init__(self, mode_config):
        self.mode_config = mode_config
        self.logger = logging.getLogger("test.sports_helpers")
        self.league = "nfl"


class TestSettingInt:
    def test_reads_and_clamps(self):
        assert _Settings({"n": 3})._setting_int("n", 1, 1, 5) == 3
        assert _Settings({"n": "4"})._setting_int("n", 1, 1, 5) == 4
        assert _Settings({"n": 99})._setting_int("n", 1, 1, 5) == 5
        assert _Settings({"n": -2})._setting_int("n", 1, 1, 5) == 1
        assert _Settings({})._setting_int("n", 2, 1, 5) == 2

    @pytest.mark.parametrize("bad", ["abc", None, [], float("inf"), float("nan")])
    def test_unusable_falls_back_with_a_warning(self, bad, caplog):
        with caplog.at_level(logging.WARNING, logger="test.sports_helpers"):
            assert _Settings({"n": bad})._setting_int("n", 2, 1, 5) == 2
        assert "ignoring unusable n" in caplog.text


class _Custom(SportsHelpersMixin):
    def __init__(self, config, mode=None):
        self.config = config
        if mode is not None:
            self.SKIN_MODE = mode


class TestModeCustomization:
    def test_no_mode_returns_block_as_is(self):
        cust = {"score": {"font_size": 10}}
        assert _Custom({"customization": cust})._mode_customization() is cust

    def test_non_dict_customization(self):
        assert _Custom({"customization": "junk"}, "live")._mode_customization() == {}
        assert _Custom({}, "live")._mode_customization() == {}

    def test_mode_overrides_merge_and_none_inherits(self):
        cfg = {"customization": {
            "score": {"font_size": 10, "text_color": [1, 1, 1]},
            "layout": {"score": {"x_offset": 2, "y_offset": 3}},
            "modes": {"live": {
                "score": {"font_size": 12, "text_color": None},
                "status": "not-a-dict",
                "layout": {"score": {"y_offset": 0, "x_offset": None}, "bad": 5},
            }},
        }}
        merged = _Custom(cfg, "live")._mode_customization()
        assert merged["score"] == {"font_size": 12, "text_color": [1, 1, 1]}
        assert merged["layout"]["score"] == {"x_offset": 2, "y_offset": 0}
        assert "bad" not in merged["layout"]
        # The stored config is not mutated.
        assert cfg["customization"]["score"]["font_size"] == 10
        assert cfg["customization"]["layout"]["score"]["y_offset"] == 3

    def test_other_mode_is_untouched(self):
        cust = {"modes": {"live": {"score": {"font_size": 12}}}}
        assert _Custom({"customization": cust}, "recent")._mode_customization() is cust


class TestOddsColor:
    def test_without_element_color_is_green(self):
        assert SportsHelpersMixin()._odds_color() == (0, 255, 0)

    def test_raising_getter_is_green(self):
        class Host(SportsHelpersMixin):
            def _element_color(self, element, default):
                raise RuntimeError("boom")
        assert Host()._odds_color() == (0, 255, 0)

    def test_asks_for_odds_text_with_green_default(self):
        seen = []

        class Host(SportsHelpersMixin):
            def _element_color(self, element, default):
                seen.append((element, default))
                return (9, 9, 9)
        assert Host()._odds_color() == (9, 9, 9)
        assert seen == [("odds_text", (0, 255, 0))]


class _Scorebug(SportsHelpersMixin):
    def __init__(self, scroll_card):
        self.config = {"scroll_card": scroll_card}

    def _card_option(self, key, default=None):
        return SportsCoreSharedMixin._card_option(self, key, default)

    def _format_game_date(self, game_date, game=None):
        return "DATE"

    def _format_game_time(self, game_time):
        return "TIME"


class TestUpcomingDateAndTimeText:
    def _text(self, block):
        return _Scorebug(block)._upcoming_date_and_time_text("2026-10-07", "19:00")

    def test_defaults_draw_both(self):
        assert self._text({}) == ("DATE", "TIME")

    def test_scroll_card_toggles_do_not_reach_the_scorebug(self):
        assert self._text({"show_date": False, "show_time": False}) == ("DATE", "TIME")

    def test_switch_toggles(self):
        assert self._text({"switch_show_date": False}) == ("", "TIME")
        assert self._text({"switch_show_time": False}) == ("DATE", "")


class TestFavoriteKey:
    def test_defaults_to_abbreviation(self):
        assert SportsHelpersMixin()._favorite_key({"home_abbr": "UF"}, "home") == "UF"

    def test_missing_abbreviation_never_matches(self):
        assert SportsHelpersMixin()._favorite_key({}, "away") is None


# ---------------------------------------------------------------------------
# Host contract
# ---------------------------------------------------------------------------

def _self_reads():
    """Every ``self.X`` / ``getattr(self, "X")`` the mixin reads, by parsing it."""
    tree = ast.parse(Path(sports_helpers.__file__).read_text(encoding="utf-8"))
    cls = next(n for n in tree.body
               if isinstance(n, ast.ClassDef) and n.name == "SportsHelpersMixin")
    names = set()
    for node in ast.walk(cls):
        if (isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Load)
                and isinstance(node.value, ast.Name) and node.value.id == "self"):
            names.add(node.attr)
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "getattr" and len(node.args) >= 2
                and isinstance(node.args[0], ast.Name) and node.args[0].id == "self"
                and isinstance(node.args[1], ast.Constant)):
            names.add(node.args[1].value)
    return names


class _Host(SportsCoreSharedMixin, SportsHelpersMixin):
    """The smallest thing a scoreboard's SportsCore has to be for these to run."""

    def __init__(self, config, mode_config=None):
        self.config = config
        self.mode_config = mode_config or {}
        self.logger = logging.getLogger("test.sports_helpers.host")
        self.games_list = []
        self.current_game_index = 0
        self.last_game_switch = 0

    def _is_favorite_game(self, game):
        return game.get("home_abbr") == "UF"


class TestHostContract:
    def test_every_host_read_is_documented(self):
        defined = set(dir(SportsHelpersMixin))
        # Written by the mixin itself before (or instead of) being read.
        own_state = {"_last_display_call_monotonic", "_switch_order",
                     "_switch_order_key", "_switch_position"}
        needed = _self_reads() - defined - own_state
        doc = sports_helpers.__doc__
        undocumented = sorted(n for n in needed | own_state if f"``{n}``" not in doc)
        assert undocumented == [], (
            f"self attributes read but not in the module docstring's host "
            f"contract: {undocumented}")

    def test_every_constant_read_has_a_default(self):
        # SKIN_MODE is the one exception: the host's mode name, read with
        # getattr(self, 'SKIN_MODE', None) where absence means "no mode". A
        # default here would say nothing the getattr does not already.
        missing = sorted(n for n in _self_reads() - {"SKIN_MODE"}
                         if n.upper() == n and not hasattr(SportsHelpersMixin, n))
        assert missing == []

    def test_defaults_match_what_the_plugins_ship(self):
        assert SportsHelpersMixin._DWELL_REENTRY_GAP_SECONDS == 5.0
        assert SportsHelpersMixin.favorite_rotation_boost == 1

    def test_stateless_and_constructor_free(self):
        assert "__init__" not in SportsHelpersMixin.__dict__
        assert SportsHelpersMixin().__dict__ == {}

    def test_base_order_does_not_matter(self):
        class Reversed(SportsHelpersMixin, SportsCoreSharedMixin):
            __init__ = _Host.__init__
            _is_favorite_game = _Host._is_favorite_game

        for cls in (_Host, Reversed):
            h = cls({"scroll_card": {"switch_show_time": False, "time_format": "24h"}})
            assert h._upcoming_date_and_time_text("10/7", "7:00 PM") == ("10/7", "")

    def test_shared_mixin_supplies_the_card_helpers(self):
        h = _Host({"scroll_card": {"switch_date_format": "abbrev", "time_format": "24h"}})
        assert h._upcoming_date_and_time_text("10/7", "7:05 PM") == ("Oct 7", "19:05")

    def test_odds_color_resolves_through_element_style(self):
        h = _Host({"customization": {"odds_text": {"text_color": [1, 2, 3]}}})
        assert tuple(h._odds_color()) == (1, 2, 3)
        assert tuple(_Host({})._odds_color()) == (0, 255, 0)

    def test_rotation_settings_and_dwell_on_one_host(self):
        h = _Host({}, mode_config={"favorite_rotation_boost": "2"})
        h.favorite_rotation_boost = h._setting_int("favorite_rotation_boost", 1, 1, 5)
        h.games_list = [_game("a"), _game("uf", "UF"), _game("c")]
        seen = []
        for _ in range(4):
            h.current_game_index = h._next_switch_index()
            seen.append(h.games_list[h.current_game_index]["id"])
        assert sorted(seen) == ["a", "c", "uf", "uf"]
        h.last_game_switch = time.time() - 100
        assert h._reset_dwell_on_reentry() is True


# ---------------------------------------------------------------------------
# Parity with the plugin copies
# ---------------------------------------------------------------------------

SCOREBOARDS = ("afl", "baseball", "basketball", "football", "hockey",
               "lacrosse", "nrl", "soccer", "ufc")

#: core name -> (where it lives in the plugins' sports.py, plugin name,
#: plugins expected to carry it).
_ALL = SCOREBOARDS
_NOT_UFC = tuple(p for p in SCOREBOARDS if p != "ufc")
PROMOTED = {
    "clamp_window": ("module", "_clamp_window", _ALL),
    "clamp_seconds": ("module", "_clamp_seconds", _ALL),
    "logo_needs_refresh": ("module", "_logo_needs_refresh", _ALL),
    "spread_weighted_order": ("SportsCore", "_spread_weighted_order", _ALL),
    "_mode_customization": ("SportsCore", "_mode_customization", _ALL),
    "_setting_int": ("SportsCore", "_setting_int", _ALL),
    "_reset_dwell_on_reentry": ("SportsCore", "_reset_dwell_on_reentry", _ALL),
    "_next_switch_index": ("SportsCore", "_next_switch_index", _ALL),
    "_odds_color": ("SportsCore", "_odds_color", _NOT_UFC),
    "_upcoming_date_and_time_text": ("SportsCore", "_upcoming_date_and_time_text", _NOT_UFC),
}

#: Public names here that are private in the plugins.
_RENAMES = {
    "clamp_window": "_clamp_window",
    "clamp_seconds": "_clamp_seconds",
    "logo_needs_refresh": "_logo_needs_refresh",
    "spread_weighted_order": "_spread_weighted_order",
    "MIN_WINDOW_DAYS": "_MIN_WINDOW_DAYS",
    "MAX_WINDOW_DAYS": "_MAX_WINDOW_DAYS",
}


def _plugins_root():
    raw = os.environ.get("LEDMATRIX_PLUGINS")
    if not raw:
        pytest.skip("set LEDMATRIX_PLUGINS to a ledmatrix-plugins checkout to "
                    "compare sports_helpers against the plugin copies; in CI "
                    "this parity check runs in ledmatrix-plugins "
                    "scripts/check_sports_helpers_parity.py (#495)")
    root = Path(raw)
    if (root / "plugins").is_dir():
        root = root / "plugins"
    if not (root / "football-scoreboard" / "sports.py").is_file():
        pytest.skip(f"LEDMATRIX_PLUGINS={raw} has no football-scoreboard/sports.py")
    return root


class _Normalise(ast.NodeTransformer):
    def visit_Name(self, node):
        node.id = _RENAMES.get(node.id, node.id)
        return node

    def _func(self, node):
        node.name = _RENAMES.get(node.name, node.name)
        node.decorator_list = []
        body = node.body
        if (body and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)):
            node.body = body[1:] or [ast.Pass()]
        self.generic_visit(node)
        return node

    visit_FunctionDef = _func


def _dump(node):
    node = ast.parse(ast.unparse(node)).body[0]   # detach and copy
    return ast.dump(_Normalise().visit(node))


def _definitions(tree):
    module, core = {}, {}
    for node in tree.body:
        if isinstance(node, ast.FunctionDef):
            module[node.name] = node
        elif isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name):
            module[node.targets[0].id] = node
        elif isinstance(node, ast.ClassDef) and node.name == "SportsCore":
            for item in node.body:
                if isinstance(item, ast.FunctionDef):
                    core[item.name] = item
                elif isinstance(item, (ast.Assign, ast.AnnAssign)):
                    target = item.targets[0] if isinstance(item, ast.Assign) else item.target
                    if isinstance(target, ast.Name):
                        core[target.id] = item
    return {"module": module, "SportsCore": core}


def _core_definitions():
    tree = ast.parse(Path(sports_helpers.__file__).read_text(encoding="utf-8"))
    out = {}
    for node in tree.body:
        if isinstance(node, ast.FunctionDef):
            out[node.name] = node
        elif isinstance(node, ast.ClassDef) and node.name == "SportsHelpersMixin":
            for item in node.body:
                if isinstance(item, ast.FunctionDef):
                    out[item.name] = item
    return out


class TestParityWithPlugins:
    @pytest.mark.parametrize("name", sorted(PROMOTED))
    def test_body_matches_every_plugin_copy(self, name):
        root = _plugins_root()
        where, plugin_name, carriers = PROMOTED[name]
        ours = _dump(_core_definitions()[name])
        drifted, missing = [], []
        for sport in carriers:
            defs = _definitions(ast.parse(
                (root / f"{sport}-scoreboard" / "sports.py").read_text(encoding="utf-8")))
            theirs = defs[where].get(plugin_name)
            if theirs is None:
                missing.append(sport)
            elif _dump(theirs) != ours:
                drifted.append(sport)
        assert missing == [], f"{plugin_name} no longer in: {missing}"
        assert drifted == [], (
            f"{plugin_name} differs from src/common/sports_helpers.py in: {drifted}. "
            f"Port the change to both, or stop treating it as shared.")

    @pytest.mark.parametrize("sport", SCOREBOARDS)
    def test_constants_match(self, sport):
        root = _plugins_root()
        defs = _definitions(ast.parse(
            (root / f"{sport}-scoreboard" / "sports.py").read_text(encoding="utf-8")))
        assert ast.literal_eval(defs["module"]["_MIN_WINDOW_DAYS"].value) == MIN_WINDOW_DAYS
        assert ast.literal_eval(defs["module"]["_MAX_WINDOW_DAYS"].value) == MAX_WINDOW_DAYS
        gap = defs["SportsCore"]["_DWELL_REENTRY_GAP_SECONDS"].value
        assert math.isclose(ast.literal_eval(gap), SportsHelpersMixin._DWELL_REENTRY_GAP_SECONDS)
