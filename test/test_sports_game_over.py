"""src.common.sports_game_over: behaviour, host contract and base order.

The matrix is ledmatrix-plugins' ``scripts/test_game_over_check.py`` (the
table the family 5 reconcile was checked against) folded to the three
``FINAL_PERIOD`` values the nine scoreboards declare: None (afl, baseball,
nrl, soccer, ufc), 4 (basketball, football, lacrosse) and 3 (hockey).
Baseball's postponed/suspended override stays in its plugin and is not here.
"""

import ast
import logging
import time
from pathlib import Path

import pytest

from src.common import sports_game_over
from src.common.sports_game_over import SportsGameOverMixin
from src.common.sports_shared import SportsLiveSharedMixin

LOG = logging.getLogger("test_sports_game_over")


def host(final_period):
    """A live manager stand-in declaring ``FINAL_PERIOD`` as a plugin does."""
    cls = type("Live", (SportsGameOverMixin,), {"FINAL_PERIOD": final_period})
    h = cls()
    h.logger = LOG
    return h


MISSING = object()  # the key is absent from the game dict


def game(period_text="", period=MISSING, clock=MISSING, away="1", home="2"):
    g = {"away_abbr": "AWY", "home_abbr": "HOM", "away_score": away,
         "home_score": home, "period_text": period_text}
    if period is not MISSING:
        g["period"] = period
    if clock is not MISSING:
        g["clock"] = clock
    return g


# ---------------------------------------------------------------------------
# The matrix: clock x period, for each FINAL_PERIOD. Scores 1-2.
# ---------------------------------------------------------------------------

FINAL_PERIODS = (None, 4, 3)
PERIODS = (MISSING, 1, 2, 3, 4, 5, 6)
CLOCKS = {"12:00": "12:00", "0:00": "0:00", ":00": ":00", "0.0": "0.0",
          "-": "-", "''": "", "None": None, "missing": MISSING}

#: The period text each ESPN status carries. Only "Final" contains "final";
#: the method reads no status, so every other text answers the same row.
LIVE_TEXTS = {
    "in progress": lambda p: "" if p is MISSING else f"P{p}",
    "end of period": lambda p: "" if p is MISSING else f"End P{p}",
    "halftime": lambda p: "Halftime",
    "end of round": lambda p: "" if p is MISSING else f"End R{p}",
    "postponed": lambda p: "Postponed",
}

#: clock -> one cell per period (missing, 1..6) for FINAL_PERIOD None, 4, 3.
EXPECTED_LIVE = {
    "12:00": "....... ....... .......",
    "0:00": "....... ....YYY ...YYYY",
    ":00": "....... ....YYY ...YYYY",
    "0.0": "....... ....... .......",
    "-": "....... ....... .......",
    "''": "....... ....... .......",
    "None": "....... ....... .......",
    "missing": "....... ....... .......",
}


def row(text_for, clock):
    return " ".join(
        "".join("Y" if host(fp)._is_game_really_over(game(text_for(p), p, clock)) else "."
                for p in PERIODS)
        for fp in FINAL_PERIODS)


@pytest.mark.parametrize("status", sorted(LIVE_TEXTS))
@pytest.mark.parametrize("clock_label", sorted(CLOCKS))
def test_a_live_period_text(status, clock_label):
    assert row(LIVE_TEXTS[status], CLOCKS[clock_label]) == EXPECTED_LIVE[clock_label]


@pytest.mark.parametrize("clock_label", sorted(CLOCKS))
def test_a_final_period_text_is_always_over(clock_label):
    assert row(lambda p: "Final", CLOCKS[clock_label]) == "YYYYYYY YYYYYYY YYYYYYY"


#: label -> (game, one cell per FINAL_PERIOD None, 4, 3)
EDGES = {
    "period_text None, P4 0:00": (game(None, 4, "0:00"), ".YY"),
    "period None, 0:00": (game("", None, "0:00"), "..."),
    "period 'OT', 0:00": (game("OT", "OT", "0:00"), "..."),
    "period '4' (str), 0:00": (game("P4", "4", "0:00"), ".YY"),
    "clock int 0, P4": (game("P4", 4, 0), "..."),
    "clock float 0.0, P4": (game("P4", 4, 0.0), "..."),
    "clock ' 0:00 ', P4": (game("P4", 4, " 0:00 "), ".YY"),
    "clock '00:00', P4": (game("P4", 4, "00:00"), "..."),
    "period_text 'Final/OT', P5 0:00": (game("Final/OT", 5, "0:00"), "YYY"),
    "period_text 'FINAL', P1 12:00": (game("FINAL", 1, "12:00"), "YYY"),
}


@pytest.mark.parametrize("label", sorted(EDGES))
def test_edge_shapes(label):
    g, want = EDGES[label]
    got = "".join("Y" if host(fp)._is_game_really_over(dict(g)) else "." for fp in FINAL_PERIODS)
    assert got == want


# ---------------------------------------------------------------------------
# The tie guard: level at 0:00 is overtime, not the end.
# ---------------------------------------------------------------------------

class TestTieGuard:
    @pytest.mark.parametrize("fp,period", [(4, 4), (4, 5), (3, 3), (3, 4), (3, 5)])
    def test_level_at_zero_is_not_over(self, fp, period):
        assert host(fp)._is_game_really_over(game("", period, "0:00", "2", "2")) is False

    def test_level_scores_compare_as_numbers(self):
        assert host(4)._is_game_really_over(game("", 4, "0:00", 2, "2")) is False
        assert host(4)._is_game_really_over(game("", 4, "0:00", " 2 ", "2")) is False

    def test_a_game_that_ends_level_ends_on_final(self):
        assert host(4)._is_game_really_over(game("Final/OT", 5, "0:00", "2", "2")) is True

    def test_level_before_the_final_period_was_never_over(self):
        assert host(4)._is_game_really_over(game("", 3, "0:00", "2", "2")) is False

    @pytest.mark.parametrize("away,home", [
        (MISSING, MISSING), (None, None), ("", ""), ("2", None),
        ("2.0", "2.0"), ({"value": 2}, {"value": 2}), ("inf", "inf"),
    ])
    def test_an_unreadable_score_leaves_it_to_the_clock(self, away, home):
        g = game("", 4, "0:00")
        for key, value in (("away_score", away), ("home_score", home)):
            if value is MISSING:
                del g[key]
            else:
                g[key] = value
        assert host(4)._is_game_really_over(g) is True

    def test_float_infinity_does_not_raise(self):
        assert host(4)._is_game_really_over(
            game("", 4, "0:00", float("inf"), float("inf"))) is True


# ---------------------------------------------------------------------------
# ufc: ESPN MMA payloads, as ufc's _extract_game_details stores them
# (ledmatrix-plugins plugins/ufc-scoreboard/test/fixtures/espn_mma_round_states.json).
# ---------------------------------------------------------------------------

UFC_RECORDED = {
    "in_round_3_of_3": ("R3", 3, "1:21"),
    "break_after_round_1": ("R1", 1, "-"),
    "end_of_round_after_stoppage": ("R2", 2, "0:51"),
    "walkouts_five_rounder": ("", 0, "-"),
    "final_five_round_decision": ("R5", 5, "5:00"),
    "final_five_round_stoppage": ("R5", 5, "1:38"),
    "final_three_round_decision": ("R3", 3, "5:00"),
    "final_three_round_stoppage": ("R2", 2, "4:07"),
    "break_after_round_4_of_5": ("R4", 4, "-"),
    "end_of_round_5_awaiting_decision": ("R5", 5, "-"),
}


class TestUfc:
    @pytest.mark.parametrize("name", sorted(UFC_RECORDED))
    def test_no_recorded_state_is_over_here(self, name):
        """A finished bout leaves the live list on is_final, before this is asked."""
        text, period, clock = UFC_RECORDED[name]
        assert host(None)._is_game_really_over(game(text, period, clock, "0", "0")) is False

    @pytest.mark.parametrize("fp", FINAL_PERIODS)
    def test_a_round_break_dash_is_never_a_zero_clock(self, fp):
        assert host(fp)._is_game_really_over(game("R4", 4, "-", "1", "2")) is False

    @pytest.mark.parametrize("clock", ["0:00", None, MISSING])
    def test_the_horn_does_not_end_a_bout(self, clock):
        assert host(None)._is_game_really_over(game("R5", 5, clock, "1", "2")) is False


# ---------------------------------------------------------------------------
# Wiring: the default, overrides, and the live mixin's caller.
# ---------------------------------------------------------------------------

def test_the_default_is_no_clock_rule():
    assert SportsGameOverMixin.FINAL_PERIOD is None


def test_an_override_defers_through_super():
    """baseball's BaseballLive: its own check first, then the shared one."""

    class Baseballish(SportsGameOverMixin):
        logger = LOG

        def _is_game_really_over(self, game):
            if game.get("status") == "status_postponed":
                return True
            return super()._is_game_really_over(game)

    b = Baseballish()
    assert b._is_game_really_over(dict(game("", 6, "0:00"), status="status_postponed")) is True
    assert b._is_game_really_over(game("", 6, "0:00")) is False
    assert b._is_game_really_over(game("Final", 9, None)) is True


class _Live(SportsGameOverMixin, SportsLiveSharedMixin):
    """A SportsLive stand-in in the documented base order."""

    FINAL_PERIOD = 4

    def __init__(self):
        self.logger = LOG
        self.stale_game_timeout = 600
        self.game_update_timestamps = {}


class TestBaseOrder:
    def test_the_documented_order_resolves_this_method(self):
        assert _Live._is_game_really_over is SportsGameOverMixin._is_game_really_over
        mro = _Live.__mro__
        assert mro.index(SportsGameOverMixin) < mro.index(SportsLiveSharedMixin)

    def test_neither_shared_mixin_defines_it(self):
        """So the base order cannot change which body runs."""
        from src.common.sports_shared import SportsCoreSharedMixin
        for mixin in (SportsLiveSharedMixin, SportsCoreSharedMixin):
            assert "_is_game_really_over" not in vars(mixin)

    def test_detect_stale_games_drops_an_over_game_through_it(self):
        live = _Live()
        live.game_update_timestamps = {"over": {"last_seen": time.time()},
                                       "on": {"last_seen": time.time()}}
        games = [dict(game("", 4, "0:00"), id="over"),
                 dict(game("", 4, "0:00", "2", "2"), id="on")]
        live._detect_stale_games(games)
        assert [g["id"] for g in games] == ["on"]
        assert "over" not in live.game_update_timestamps

    def test_the_class_value_wins_over_the_default(self):
        assert _Live().FINAL_PERIOD == 4
        assert _Live()._is_game_really_over(game("", 4, "0:00")) is True


# ---------------------------------------------------------------------------
# Host contract
# ---------------------------------------------------------------------------

def _self_reads():
    tree = ast.parse(Path(sports_game_over.__file__).read_text(encoding="utf-8"))
    cls = next(n for n in tree.body
               if isinstance(n, ast.ClassDef) and n.name == "SportsGameOverMixin")
    return {node.attr for node in ast.walk(cls)
            if isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Load)
            and isinstance(node.value, ast.Name) and node.value.id == "self"}


class TestHostContract:
    def test_every_host_read_is_documented(self):
        undocumented = sorted(n for n in _self_reads()
                              if f"``{n}``" not in sports_game_over.__doc__)
        assert undocumented == [], f"read but not in the host contract: {undocumented}"

    def test_the_mixin_creates_no_state(self):
        assert "__init__" not in vars(SportsGameOverMixin)
        assert not hasattr(SportsGameOverMixin, "logger")
        assert sorted(n for n in vars(SportsGameOverMixin) if not n.startswith("__")) == [
            "FINAL_PERIOD", "_is_game_really_over"]
