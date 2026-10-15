"""sports_view_model still matches every plugin copy.

``src.common.sports_view_model`` was copied from the scoreboards once family
10 had made ``_extract_game_details_common`` one body in all nine
``SportsCore`` classes, with the helpers it calls (``_status_is_final``,
``_score_text``, ``_team_logo_url``, ``_extract_team_record``, the
``_logo_filename`` seam) and the two status-name sets. The plugins delete
their copies once they floor on the release that ships this module. nrl
overrides the seam on ``BaseNrlManager``, not on ``SportsCore``. Until each has, a copy that changes on its own is a fix one
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

from src.common import sports_view_model

REPO = Path(__file__).resolve().parents[1]
SPORTS = ("afl", "baseball", "basketball", "football", "hockey", "lacrosse",
          "nrl", "soccer", "ufc")
CARRIER, MIXIN = "SportsCore", "SportsViewModelMixin"
METHODS = ("_extract_game_details_common", "_status_is_final", "_score_text",
           "_team_logo_url", "_extract_team_record", "_logo_filename")
CONSTANTS = ("_CALLED_OFF_STATUS_NAMES", "_NOT_PLAYED_STATUS_NAMES")


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
    """The value assigned to ``name`` in the class body, as an AST dump."""
    return next((ast.dump(n.value) for n in cls.body
                 if isinstance(n, ast.AnnAssign) and getattr(n.target, "id", None) == name),
                None)



def _adopted(source, tree):
    """The plugin imports core's module and its carrier inherits the mixin."""
    bases = _class(tree, CARRIER).bases
    return (sports_view_model.__name__ in source
            and any(getattr(b, "id", getattr(b, "attr", None)) == MIXIN for b in bases))


def _fingerprint(node):
    return (DRIFT._digest(node, DRIFT._Canonical()),
            tuple(ast.unparse(d) for d in node.decorator_list))


def _plugin_tree(root, sport):
    source = (root / f"{sport}-scoreboard" / "sports.py").read_text(encoding="utf-8")
    return source, ast.parse(source)


OURS = _class(ast.parse(Path(sports_view_model.__file__).read_text(encoding="utf-8")), MIXIN)


@pytest.mark.parametrize("sport, name", [(s, n) for s in SPORTS for n in METHODS])
def test_every_remaining_plugin_copy_matches(sport, name):
    source, tree = _plugin_tree(_plugins_root(), sport)
    copy = _method(_class(tree, CARRIER), name)
    if copy is None:
        assert _adopted(source, tree), (
            f"{sport}: no {name} on {CARRIER}, and {CARRIER} does not inherit {MIXIN}")
    else:
        assert _fingerprint(copy) == _fingerprint(_method(OURS, name)), (
            f"{CARRIER}.{name} in {sport} differs from sports_view_model. "
            f"Port the change to both, or stop treating it as shared.")



@pytest.mark.parametrize("sport, name", [(s, n) for s in SPORTS for n in CONSTANTS])
def test_every_remaining_status_set_matches(sport, name):
    source, tree = _plugin_tree(_plugins_root(), sport)
    copy = _constant(_class(tree, CARRIER), name)
    if copy is None:
        assert _adopted(source, tree), (
            f"{sport}: no {name} on {CARRIER}, and {CARRIER} does not inherit {MIXIN}")
    else:
        assert copy == _constant(OURS, name), f"{CARRIER}.{name} in {sport} differs"


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
