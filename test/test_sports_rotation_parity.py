"""sports_rotation still matches every plugin copy, and only football overrides the seam.

``src.common.sports_rotation`` was copied from the scoreboards once family 7
had made each method one body in all nine ``SportsCore`` classes:
``_by_importance``, ``_other_games_window``, ``_advance_other_games_if_due``,
``_rotate_other_games_on_display``, ``_attach_odds_to_rotated_games`` and the
default ``_rankings_loaded``. The plugins delete their copies once they floor
on the release that ships this module. Until each has, a copy that changes on
its own is a fix one side has and the other lacks.

Point LEDMATRIX_PLUGINS at a ledmatrix-plugins checkout and each method is
compared with every plugin copy using ``scripts/sports_drift_report.py``'s own
normalisation (the AST with docstrings and annotations dropped), plus the
decorators. A copy that is gone counts as adopted when the plugin's
``sports.py`` names the module. football's ``_rankings_loaded`` is the
decided override (it counts its rankings keyed by team id), checked as the only
one; it stays in the plugin after adoption. Without the variable this skips:
core CI has no plugins checkout.
"""

import ast
import importlib.util
import os
from pathlib import Path

import pytest

from src.common import sports_rotation

REPO = Path(__file__).resolve().parents[1]
SPORTS = ("afl", "baseball", "basketball", "football", "hockey", "lacrosse",
          "nrl", "soccer", "ufc")
CARRIER, MIXIN = "SportsCore", "SportsRotationMixin"
METHODS = ("_by_importance", "_other_games_window", "_advance_other_games_if_due",
           "_rotate_other_games_on_display", "_attach_odds_to_rotated_games",
           "_rankings_loaded")
SEAM = "_rankings_loaded"

#: The owner's decision (docs/SPORTS_UNIFICATION.md, family 7): the sports
#: whose own _rankings_loaded replaces the default.
OVERRIDES_RANKINGS_LOADED = {"football"}


def _drift_report():
    """scripts/sports_drift_report.py, loaded by path (scripts/ is no package)."""
    spec = importlib.util.spec_from_file_location(
        "sports_drift_report", REPO / "scripts" / "sports_drift_report.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


DRIFT = _drift_report()


def _plugins_root():
    root = DRIFT.resolve_plugins_dir(os.environ.get("LEDMATRIX_PLUGINS"))
    if root is None:
        pytest.skip("set LEDMATRIX_PLUGINS to a ledmatrix-plugins checkout to "
                    "compare this module against the plugin copies")
    return root


def _class(tree, name):
    return next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == name)


def _method(cls, name):
    return next((n for n in cls.body
                 if isinstance(n, ast.FunctionDef) and n.name == name), None)


def _fingerprint(node):
    return (DRIFT._digest(node, DRIFT._Canonical()),
            tuple(ast.unparse(d) for d in node.decorator_list))


def _ours(name):
    tree = ast.parse(Path(sports_rotation.__file__).read_text(encoding="utf-8"))
    return _method(_class(tree, MIXIN), name)


def _plugin_tree(root, sport):
    source = (root / f"{sport}-scoreboard" / "sports.py").read_text(encoding="utf-8")
    return source, ast.parse(source)


CASES = [(sport, name) for sport in SPORTS for name in METHODS
         if not (name == SEAM and sport in OVERRIDES_RANKINGS_LOADED)]


@pytest.mark.parametrize("sport, name", CASES)
def test_every_remaining_plugin_copy_matches(sport, name):
    source, tree = _plugin_tree(_plugins_root(), sport)
    copy = _method(_class(tree, CARRIER), name)
    if copy is None:
        assert sports_rotation.__name__ in source, (
            f"{sport}: no {name} on {CARRIER} and no {sports_rotation.__name__} import")
    else:
        assert _fingerprint(copy) == _fingerprint(_ours(name)), (
            f"{CARRIER}.{name} in {sport} differs from sports_rotation. "
            f"Port the change to both, or stop treating it as shared.")


@pytest.mark.parametrize("sport", SPORTS)
def test_no_other_plugin_class_carries_a_copy(sport):
    """A copy on another class would shadow the shared one for that class."""
    _, tree = _plugin_tree(_plugins_root(), sport)
    strays = [f"{node.name}.{name}" for node in tree.body if isinstance(node, ast.ClassDef)
              for name in METHODS if node.name != CARRIER and _method(node, name) is not None]
    assert strays == []


def test_only_the_decided_sports_override_the_seam():
    root = _plugins_root()
    ours = _fingerprint(_ours(SEAM))
    overriding = set()
    for sport in SPORTS:
        copy = _method(_class(_plugin_tree(root, sport)[1], CARRIER), SEAM)
        if copy is not None and _fingerprint(copy) != ours:
            overriding.add(sport)
    assert overriding == OVERRIDES_RANKINGS_LOADED


def test_the_drift_report_still_calls_them_identical():
    root = _plugins_root()
    families = DRIFT.build(root, ("sports.py",))
    rows = {(r["file"], r["family"]): r
            for r in (DRIFT.summarise(k, v) for k, v in families.items())}
    for name in METHODS:
        if name == SEAM:
            continue            # the default and football's override: two by design
        row = rows.get(("sports.py", name))
        assert row is None or row["worst_class_variants"] == 1, name
