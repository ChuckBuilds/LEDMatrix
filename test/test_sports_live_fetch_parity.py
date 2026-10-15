"""sports_live_fetch still matches every plugin copy.

``src.common.sports_live_fetch`` was copied from the scoreboards once family 9
had made each method one body in all nine ``SportsCore`` classes:
``_fetch_todays_games``, ``_fetch_odds`` and ``_drain_odds_queue``. The plugins delete their copies once they floor on the release that
ships this module. Until each has, a copy that changes on its own is a fix one
side has and the other lacks.

Point LEDMATRIX_PLUGINS at a ledmatrix-plugins checkout and each method is
compared with every plugin copy using ``scripts/sports_drift_report.py``'s own
normalisation (the AST with docstrings and annotations dropped), plus the
decorators. A copy that is gone counts as adopted when the plugin's ``sports.py`` names the module.
Without the variable this skips: core CI has no plugins checkout.
"""

import ast
import importlib.util
import os
from pathlib import Path

import pytest

from src.common import sports_live_fetch

REPO = Path(__file__).resolve().parents[1]
SPORTS = ("afl", "baseball", "basketball", "football", "hockey", "lacrosse",
          "nrl", "soccer", "ufc")
CARRIER, MIXIN = "SportsCore", "SportsLiveFetchMixin"
METHODS = ("_fetch_todays_games", "_fetch_odds", "_drain_odds_queue")


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


def _plugin_tree(root, sport):
    source = (root / f"{sport}-scoreboard" / "sports.py").read_text(encoding="utf-8")
    return source, ast.parse(source)


OURS = _class(ast.parse(Path(sports_live_fetch.__file__).read_text(encoding="utf-8")), MIXIN)


@pytest.mark.parametrize("sport, name", [(s, n) for s in SPORTS for n in METHODS])
def test_every_remaining_plugin_copy_matches(sport, name):
    source, tree = _plugin_tree(_plugins_root(), sport)
    copy = _method(_class(tree, CARRIER), name)
    if copy is None:
        assert sports_live_fetch.__name__ in source, (
            f"{sport}: no {name} on {CARRIER} and no {sports_live_fetch.__name__} import")
    else:
        assert _fingerprint(copy) == _fingerprint(_method(OURS, name)), (
            f"{CARRIER}.{name} in {sport} differs from sports_live_fetch. "
            f"Port the change to both, or stop treating it as shared.")



@pytest.mark.parametrize("sport", SPORTS)
def test_no_other_plugin_class_carries_a_copy(sport):
    """A copy on another class would shadow the shared one for that class."""
    _, tree = _plugin_tree(_plugins_root(), sport)
    strays = [f"{node.name}.{name}" for node in tree.body if isinstance(node, ast.ClassDef)
              for name in METHODS if node.name != CARRIER and _method(node, name) is not None]
    assert strays == []


def test_the_drift_report_calls_them_identical():
    families = DRIFT.build(_plugins_root(), ("sports.py",))
    rows = {(r["file"], r["family"]): r
            for r in (DRIFT.summarise(k, v) for k, v in families.items())}
    for name in METHODS:
        row = rows.get(("sports.py", name))
        assert row is None or row["worst_class_variants"] == 1, name
