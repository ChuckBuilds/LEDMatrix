"""sports_game_over still matches every plugin copy, and each plugin's FINAL_PERIOD.

``SportsGameOverMixin._is_game_really_over`` was copied from the scoreboards'
``SportsLive._is_game_really_over`` once family 5 had made the nine copies one
body. The plugins delete their copies once they floor on the release that
ships this module. Until each has, a copy that changes on its own is a fix one
side has and the other lacks.

Point LEDMATRIX_PLUGINS at a ledmatrix-plugins checkout and the method is
compared with every plugin copy using ``scripts/sports_drift_report.py``'s own
normalisation (the AST with docstrings and annotations dropped), plus the
decorators. A copy that is gone counts as adopted when the plugin's
``sports.py`` names the module. Each plugin's ``SportsLive.FINAL_PERIOD`` is
compared with the value the owner decided for its sport, which stays in the
plugin after adoption. Without the variable this skips: core CI has no plugins
checkout.
"""

import ast
import importlib.util
import os
from pathlib import Path

import pytest

from src.common import sports_game_over

REPO = Path(__file__).resolve().parents[1]

#: The owner's decision (docs/SPORTS_UNIFICATION.md, family 5): the period
#: from which a 0:00 clock ends a game, None where the clock never does.
FINAL_PERIOD = {
    "afl": None, "baseball": None, "basketball": 4, "football": 4,
    "hockey": 3, "lacrosse": 4, "nrl": None, "soccer": None, "ufc": None,
}
NAME = "_is_game_really_over"


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


def _method(cls):
    return next((n for n in cls.body
                 if isinstance(n, ast.FunctionDef) and n.name == NAME), None)


def _fingerprint(node):
    return (DRIFT._digest(node, DRIFT._Canonical()),
            tuple(ast.unparse(d) for d in node.decorator_list))


def _final_period(cls):
    for node in cls.body:
        if (isinstance(node, (ast.Assign, ast.AnnAssign)) and node.value is not None):
            target = node.targets[0] if isinstance(node, ast.Assign) else node.target
            if isinstance(target, ast.Name) and target.id == "FINAL_PERIOD":
                return ast.literal_eval(node.value)
    raise AssertionError("SportsLive declares no FINAL_PERIOD")


def _ours():
    tree = ast.parse(Path(sports_game_over.__file__).read_text(encoding="utf-8"))
    return _class(tree, "SportsGameOverMixin")


def test_the_mixin_holds_one_method_and_the_default():
    names = sorted(n.name if isinstance(n, ast.FunctionDef) else n.target.id
                   for n in _ours().body if isinstance(n, (ast.FunctionDef, ast.AnnAssign))
                   and (isinstance(n, ast.FunctionDef) or n.value is not None))
    assert names == ["FINAL_PERIOD", NAME]
    assert _final_period(_ours()) is None


@pytest.mark.parametrize("sport", sorted(FINAL_PERIOD))
def test_every_remaining_plugin_copy_matches(sport):
    root = _plugins_root()
    source = (root / f"{sport}-scoreboard" / "sports.py").read_text(encoding="utf-8")
    live = _class(ast.parse(source), "SportsLive")
    copy = _method(live)
    if copy is None:
        assert sports_game_over.__name__ in source, (
            f"{sport}: no {NAME} on SportsLive and no {sports_game_over.__name__} import")
    else:
        assert _fingerprint(copy) == _fingerprint(_method(_ours())), (
            f"{NAME} in {sport} differs from sports_game_over. "
            f"Port the change to both, or stop treating it as shared.")


@pytest.mark.parametrize("sport", sorted(FINAL_PERIOD))
def test_every_plugin_declares_its_final_period(sport):
    root = _plugins_root()
    source = (root / f"{sport}-scoreboard" / "sports.py").read_text(encoding="utf-8")
    assert _final_period(_class(ast.parse(source), "SportsLive")) == FINAL_PERIOD[sport]


def test_the_drift_report_still_calls_it_identical():
    root = _plugins_root()
    families = DRIFT.build(root, ("sports.py",))
    rows = {(r["file"], r["family"]): r
            for r in (DRIFT.summarise(k, v) for k, v in families.items())}
    row = rows.get(("sports.py", NAME))
    assert row is None or row["worst_class_variants"] == 1
