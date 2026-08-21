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


def _spans(body, lineno):
    """True when `lineno` falls inside this list of statements."""
    return any(n.lineno <= lineno <= (n.end_lineno or n.lineno) for n in body)


def _parents(tree):
    table = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            table[child] = node
    return table


PARENTS = _parents(TREE)


def _mentions_positively(test, names):
    """True when `test` references every name, none of them under a `not`.

    Structural, not textual. Matching the unparsed source would accept
    `not (details["is_live"] or details["is_halftime"])` -- which selects
    exactly the non-live games this guard exists to exclude -- because the
    names still appear in the text.
    """
    found = set()
    for node in ast.walk(test):
        if not (isinstance(node, ast.Constant) and node.value in names):
            continue
        negated = False
        cursor = node
        while cursor is not test and cursor in PARENTS:
            cursor = PARENTS[cursor]
            if isinstance(cursor, ast.UnaryOp) and isinstance(cursor.op, ast.Not):
                negated = True
                break
        if not negated:
            found.add(node.value)
    return found >= set(names)


def _guarded_by_positive(lineno, names):
    """True when some enclosing `if` runs this line only if `names` hold.

    Only the TRUE branch counts: an `if` whose `else` contains the call would
    otherwise look like a guard while doing the opposite.
    """
    for node in ast.walk(TREE):
        if isinstance(node, ast.If) and _spans(node.body, lineno) \
                and _mentions_positively(node.test, names):
            return True
    return False


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
        assert _guarded_by_positive(lineno, {"is_live", "is_halftime"}), (
            f"SportsLive._fetch_odds at line {lineno} does not sit in the true "
            "branch of a test requiring the game to be in progress. Without "
            "that, it fans out across the whole event list -- one sequential "
            "ESPN request per game.")
