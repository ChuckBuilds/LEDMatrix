"""Odds must be fetched for the games shown, not every game in the window.

SportsUpcoming.update() collected every upcoming game in the schedule window
and called _fetch_odds() on each one *inside* that collection loop, narrowing
to upcoming_games_to_show only afterwards. The comment there said odds were
fetched "only for games that will be displayed", but the sole narrowing it
applied was show_favorite_teams_only, which is not the default -- so in the
usual configuration nothing narrowed it at all.

Measured on a live rig: a college league produced 946 upcoming games in one
cycle and displayed 1 of them. The same shape on the football plugin produced
a burst of 467 sequential ESPN requests that ran for 35s and blew that
plugin's 30s update budget, and it repeats every time the 1h odds TTL expires.

SportsLive is deliberately different: it walks the raw event list because it
has to find which games are live, but only fetches odds for a game that has
already passed the is_live/is_halftime test, so the fan-out is bounded by how
many games are actually in progress.
"""
import ast
from pathlib import Path

import pytest

MODES = (Path(__file__).resolve().parent.parent
         / "src" / "base_classes" / "sports" / "modes.py")
TREE = ast.parse(MODES.read_text(encoding="utf-8"))


def _fetch_sites():
    """(class name, method name, lineno) for every self._fetch_odds(...) call."""
    calls = [n.lineno for n in ast.walk(TREE)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
             and n.func.attr == "_fetch_odds"]
    sites = []
    for cls in [n for n in ast.walk(TREE) if isinstance(n, ast.ClassDef)]:
        for fn in [n for n in cls.body if isinstance(n, ast.FunctionDef)]:
            for lineno in calls:
                if fn.lineno <= lineno <= (fn.end_lineno or fn.lineno):
                    sites.append((cls.name, fn.name, lineno))
    assert len(sites) == len(calls), "a _fetch_odds call sits outside any method"
    return sites


def _innermost_loop_iterable(lineno):
    best = None
    for node in ast.walk(TREE):
        if isinstance(node, ast.For) and \
                node.lineno <= lineno <= (node.end_lineno or node.lineno):
            if best is None or node.lineno > best.lineno:
                best = node
    return None if best is None else ast.unparse(best.iter)


def _guards_above(lineno):
    """Source of every `if` test enclosing this line."""
    out = []
    for node in ast.walk(TREE):
        if isinstance(node, ast.If) and \
                node.lineno <= lineno <= (node.end_lineno or node.lineno):
            out.append(ast.unparse(node.test))
    return out


def test_every_fetch_site_is_accounted_for():
    """A new call site must be classified deliberately, not inherited silently."""
    found = {(cls, fn) for cls, fn, _ in _fetch_sites()}
    assert found == {("SportsUpcoming", "update"), ("SportsLive", "update")}, (
        f"unexpected _fetch_odds call sites: {sorted(found)}. Each one is a "
        "sequential ESPN request per game -- classify it here on purpose.")


def test_upcoming_fetches_only_the_selected_games():
    for cls, _fn, lineno in _fetch_sites():
        if cls != "SportsUpcoming":
            continue
        iterable = _innermost_loop_iterable(lineno)
        assert iterable == "team_games", (
            f"SportsUpcoming._fetch_odds at line {lineno} iterates over "
            f"{iterable!r}. It must run over team_games -- already narrowed to "
            "upcoming_games_to_show -- not over every event in the schedule "
            "window. Each item costs one sequential ESPN request.")


def test_live_only_fetches_for_games_actually_in_progress():
    for cls, _fn, lineno in _fetch_sites():
        if cls != "SportsLive":
            continue
        guards = " ".join(_guards_above(lineno))
        assert "is_live" in guards and "is_halftime" in guards, (
            f"SportsLive._fetch_odds at line {lineno} is not gated on a game "
            f"being in progress; guards were: {guards!r}. Without that test it "
            "fans out across the whole event list.")
