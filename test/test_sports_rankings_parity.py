"""sports_rankings still matches every plugin copy.

``src.common.sports_rankings`` was copied from the scoreboards once family 8
had made each method one body in all nine ``SportsCore`` classes:
``_choose_poll``, ``_fetch_team_rankings``, ``_poll_season_ended``,
``_load_division_team_ids``, ``_ranked_positions``, ``_is_ranked_game``,
``_best_rank`` and ``_passes_other_filters``, with the class constants they
read. The plugins delete their copies once they floor on the release that
ships this module. Until each has, a copy that changes on its own is a fix one
side has and the other lacks.

Point LEDMATRIX_PLUGINS at a ledmatrix-plugins checkout and each method is
compared with every plugin copy using ``scripts/sports_drift_report.py``'s own
normalisation (the AST with docstrings and annotations dropped), plus the
decorators, and each constant a plugin still defines by its expression. A copy that is
gone counts as adopted when the plugin's ``sports.py`` names the module.
Without the variable this skips: core CI has no plugins checkout.
"""

import ast
import importlib.util
import os
from pathlib import Path

import pytest

from src.common import sports_rankings

REPO = Path(__file__).resolve().parents[1]
SPORTS = ("afl", "baseball", "basketball", "football", "hockey", "lacrosse",
          "nrl", "soccer", "ufc")
CARRIER, MIXIN = "SportsCore", "SportsRankingsMixin"
METHODS = ("_choose_poll", "_fetch_team_rankings", "_poll_season_ended",
           "_load_division_team_ids", "_ranked_positions", "_is_ranked_game",
           "_best_rank", "_passes_other_filters")
CONSTANTS = ("_NON_TOP_POLL_TYPES", "_NON_TOP_POLL_NAMES", "_DIVISION_GROUPS_BY_LEAGUE",
             "_DIVISION_CACHE_TTL", "_DIVISION_RETRY_SECONDS")


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


def _constant(cls, name):
    """The value a class body assigns to ``name`` (plain or annotated), or None."""
    for node in cls.body:
        if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == name for t in node.targets):
            return node.value
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) \
                and node.target.id == name:
            return node.value
    return None


def _fingerprint(node):
    return (DRIFT._digest(node, DRIFT._Canonical()),
            tuple(ast.unparse(d) for d in node.decorator_list))


def _plugin_tree(root, sport):
    source = (root / f"{sport}-scoreboard" / "sports.py").read_text(encoding="utf-8")
    return source, ast.parse(source)


OURS = _class(ast.parse(Path(sports_rankings.__file__).read_text(encoding="utf-8")), MIXIN)


@pytest.mark.parametrize("sport, name", [(s, n) for s in SPORTS for n in METHODS])
def test_every_remaining_plugin_copy_matches(sport, name):
    source, tree = _plugin_tree(_plugins_root(), sport)
    copy = _method(_class(tree, CARRIER), name)
    if copy is None:
        assert sports_rankings.__name__ in source, (
            f"{sport}: no {name} on {CARRIER} and no {sports_rankings.__name__} import")
    else:
        assert _fingerprint(copy) == _fingerprint(_method(OURS, name)), (
            f"{CARRIER}.{name} in {sport} differs from sports_rankings. "
            f"Port the change to both, or stop treating it as shared.")


@pytest.mark.parametrize("sport", SPORTS)
def test_the_plugin_constants_match(sport):
    _, tree = _plugin_tree(_plugins_root(), sport)
    core = _class(tree, CARRIER)
    for name in CONSTANTS:
        value = _constant(core, name)
        if value is not None:
            assert ast.dump(value) == ast.dump(_constant(OURS, name)), f"{sport}: {name}"


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
