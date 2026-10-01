"""src.common.sports_display_rules: behaviour and host contract.

Ported from the scoreboards' tests of the same methods (hockey's
test_switch_show_date_time.py, baseball's test_recent_game_date.py, the
test_non_favorite_live_duration.py copies), against stub hosts composed the
way the plugins compose ``SportsCore``: the new mixins first, then
``SportsCoreSharedMixin``.
"""

import ast
from pathlib import Path

import pytest

from src.common import sports_display_rules
from src.common.sports_display_rules import SportsCardOptionsMixin, SportsGameRulesMixin
from src.common.sports_shared import SportsCoreSharedMixin


class Core(SportsCardOptionsMixin, SportsGameRulesMixin, SportsCoreSharedMixin):
    """A SportsCore stand-in in the documented base order."""

    def __init__(self, scroll_card=None, favorites=(), non_fav=0, duration=15,
                 passes=lambda g: True, quality="any"):
        self.config = {"scroll_card": dict(scroll_card or {})}
        self.favorite_teams = list(favorites)
        self.non_favorite_live_game_duration = non_fav
        self.game_display_duration = duration
        self._passes = passes
        self.other_games_min_quality = quality
        self.coverage_checked = []

    def _passes_other_filters(self, game):
        return self._passes(game)

    def _check_ranking_coverage(self, games):
        self.coverage_checked.append(list(games))

    def _is_favorite_game(self, game):
        return game.get("home_abbr") in self.favorite_teams


# ---------------------------------------------------------------------------
# _card_option
# ---------------------------------------------------------------------------

class TestCardOption:
    def test_ordinary_keys_read_through(self):
        core = Core({"vs_text": "@"})
        assert core._card_option("vs_text", "VS") == "@"
        assert core._card_option("missing", 7) == 7

    def test_both_lines_off_under_date_time_reads_as_both_on(self):
        core = Core({"switch_show_date": False, "switch_show_time": False})
        assert core._card_option("switch_show_date", True) is True
        assert core._card_option("switch_show_time", True) is True

    @pytest.mark.parametrize("center", ["vs", "none"])
    def test_both_off_is_honoured_without_the_stack(self, center):
        core = Core({"switch_show_date": False, "switch_show_time": False,
                     "switch_upcoming_center": center})
        assert core._card_option("switch_show_date", True) is False
        assert core._card_option("switch_show_time", True) is False

    def test_one_line_off_is_honoured(self):
        core = Core({"switch_show_date": False, "switch_show_time": True})
        assert core._card_option("switch_show_date", True) is False
        assert core._card_option("switch_show_time", True) is True

    def test_inherit_follows_the_card_center(self):
        core = Core({"switch_show_date": False, "switch_show_time": False,
                     "switch_upcoming_center": "inherit", "upcoming_center": "vs"})
        assert core._card_option("switch_show_date", True) is False

    def test_it_must_come_before_the_shared_mixin(self):
        """In the other order the shared reader wins and the rescue is lost."""

        class Wrong(SportsCoreSharedMixin, SportsCardOptionsMixin):
            pass

        wrong = Wrong()
        wrong.config = {"scroll_card": {"switch_show_date": False, "switch_show_time": False}}
        assert wrong._card_option("switch_show_date", True) is False
        assert Core._card_option is SportsCardOptionsMixin._card_option

    def test_works_lifted_onto_a_stand_in(self):
        """Plugin tests lift it onto classes that are not SportsCore subclasses."""

        class StandIn:
            config = {"scroll_card": {"switch_show_date": False, "switch_show_time": False}}
            _card_option = SportsCardOptionsMixin._card_option
            _switch_upcoming_center = SportsCoreSharedMixin._switch_upcoming_center

        assert StandIn()._card_option("switch_show_time", True) is True


class TestRecentDateText:
    def test_numeric_default_is_the_extractors_text(self):
        assert Core()._recent_date_text({"game_date": "9/23"}) == "9/23"

    def test_follows_switch_date_format(self):
        core = Core({"switch_date_format": "abbrev"})
        assert core._recent_date_text({"game_date": "9/23"}) == "Sep 23"

    def test_the_off_switch(self):
        core = Core({"switch_recent_show_date": False})
        assert core._recent_date_text({"game_date": "9/23"}) == ""

    @pytest.mark.parametrize("game", [None, {}, {"game_date": None}])
    def test_no_date_is_empty(self, game):
        assert Core()._recent_date_text(game) == ""


# ---------------------------------------------------------------------------
# _filtered_or_all
# ---------------------------------------------------------------------------

class TestFilteredOrAll:
    def test_keeps_what_passes(self):
        games = [{"id": 1, "ok": True}, {"id": 2, "ok": False}]
        core = Core(passes=lambda g: g["ok"])
        assert core._filtered_or_all(games) == [games[0]]

    def test_fails_open_when_nothing_passes(self):
        games = [{"id": 1}, {"id": 2}]
        assert Core(passes=lambda g: False)._filtered_or_all(games) == games

    def test_ranking_coverage_is_checked_on_every_game(self):
        games = [{"id": 1}, {"id": 2}]
        core = Core(passes=lambda g: g["id"] == 1)
        core._filtered_or_all(games)
        assert core.coverage_checked == [games]


# ---------------------------------------------------------------------------
# _effective_live_duration
# ---------------------------------------------------------------------------

class TestEffectiveLiveDuration:
    FAV = {"home_abbr": "DAL"}
    OTHER = {"home_abbr": "NYG"}

    def test_non_favourite_gets_the_shorter_dwell(self):
        core = Core(favorites=["DAL"], non_fav=5, duration=20)
        assert core._effective_live_duration(self.OTHER) == 5
        assert core._effective_live_duration(self.FAV) == 20

    def test_no_favourites_means_one_duration(self):
        assert Core(non_fav=5, duration=20)._effective_live_duration(self.OTHER) == 20

    @pytest.mark.parametrize("knob", [0, None])
    def test_the_knob_off(self, knob):
        core = Core(favorites=["DAL"], non_fav=knob, duration=20)
        assert core._effective_live_duration(self.OTHER) == 20

    def test_no_game(self):
        assert Core(favorites=["DAL"], non_fav=5, duration=20)._effective_live_duration(None) == 20

    def test_a_host_without_the_knob(self):
        core = Core(favorites=["DAL"], duration=20)
        del core.non_favorite_live_game_duration
        assert core._effective_live_duration(self.OTHER) == 20


# ---------------------------------------------------------------------------
# Host contract
# ---------------------------------------------------------------------------

def _self_reads(class_name):
    tree = ast.parse(Path(sports_display_rules.__file__).read_text(encoding="utf-8"))
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == class_name)
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


class TestHostContract:
    @pytest.mark.parametrize("mixin", [SportsCardOptionsMixin, SportsGameRulesMixin])
    def test_every_host_read_is_documented(self, mixin):
        needed = _self_reads(mixin.__name__) - set(dir(mixin))
        undocumented = sorted(n for n in needed if f"``{n}" not in sports_display_rules.__doc__)
        assert undocumented == [], f"read but not in the host contract: {undocumented}"

    def test_the_mixins_create_no_attributes(self):
        for name in ("_format_game_date", "favorite_teams", "game_display_duration",
                     "_passes_other_filters", "_check_ranking_coverage", "_is_favorite_game"):
            assert not hasattr(SportsCardOptionsMixin, name)
            assert not hasattr(SportsGameRulesMixin, name)
        for mixin in (SportsCardOptionsMixin, SportsGameRulesMixin):
            assert "__init__" not in vars(mixin)

    def test_the_two_define_no_name_in_common(self):
        a = {n for n in vars(SportsCardOptionsMixin) if not n.startswith("__")}
        b = {n for n in vars(SportsGameRulesMixin) if not n.startswith("__")}
        assert a & b == set()
