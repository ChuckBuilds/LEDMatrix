"""sports_favorites still matches every plugin copy, and only nrl overrides the key.

``src.common.sports_favorites`` was copied from the scoreboards once family 6
had made each method one body in all nine: ``SportsCore._favorite_code`` and
``_is_favorite_game``, ``SportsUpcoming._select_games_for_display`` and
``SportsRecent._select_recent_games_for_display``. The plugins delete their
copies once they floor on the release that ships this module. Until each has, a
copy that changes on its own is a fix one side has and the other lacks.

Point LEDMATRIX_PLUGINS at a ledmatrix-plugins checkout and each method is
compared with every plugin copy using ``scripts/sports_drift_report.py``'s own
normalisation (the AST with docstrings and annotations dropped), plus the
decorators. A copy that is gone counts as adopted when the plugin's
``sports.py`` names the module. The owner's decision that only nrl overrides
``_favorite_key`` (with the team id) is checked too; that override stays in
the plugin after adoption. Without the variable this skips: core CI has no
plugins checkout.
"""

import ast
import importlib.util
import os
from pathlib import Path

import pytest

from src.common import sports_favorites

REPO = Path(__file__).resolve().parents[1]
SPORTS = ("afl", "baseball", "basketball", "football", "hockey", "lacrosse",
          "nrl", "soccer", "ufc")

#: plugin class -> (our mixin, the methods it carries)
CARRIERS = {
    "SportsCore": ("SportsFavoritesMixin", ("_favorite_code", "_is_favorite_game")),
    "SportsUpcoming": ("SportsUpcomingFavoritesMixin", ("_select_games_for_display",)),
    "SportsRecent": ("SportsRecentFavoritesMixin", ("_select_recent_games_for_display",)),
}

#: The owner's decision (docs/SPORTS_UNIFICATION.md, family 6): the sports
#: that name a team by something other than its abbreviation.
OVERRIDES_FAVORITE_KEY = {"nrl"}


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


def _ours(mixin):
    tree = ast.parse(Path(sports_favorites.__file__).read_text(encoding="utf-8"))
    return _class(tree, mixin)


def _plugin_tree(root, sport):
    source = (root / f"{sport}-scoreboard" / "sports.py").read_text(encoding="utf-8")
    return source, ast.parse(source)


CASES = [(sport, cls, name) for sport in SPORTS
         for cls, (_, names) in CARRIERS.items() for name in names]


@pytest.mark.parametrize("sport, cls, name", CASES)
def test_every_remaining_plugin_copy_matches(sport, cls, name):
    source, tree = _plugin_tree(_plugins_root(), sport)
    copy = _method(_class(tree, cls), name)
    if copy is None:
        assert sports_favorites.__name__ in source, (
            f"{sport}: no {name} on {cls} and no {sports_favorites.__name__} import")
    else:
        ours = _method(_ours(CARRIERS[cls][0]), name)
        assert _fingerprint(copy) == _fingerprint(ours), (
            f"{cls}.{name} in {sport} differs from sports_favorites. "
            f"Port the change to both, or stop treating it as shared.")


@pytest.mark.parametrize("sport", SPORTS)
def test_no_other_plugin_class_carries_a_copy(sport):
    """A copy on another class (afl's old SportsUpcoming._is_favorite_game) would shadow the shared one."""
    _, tree = _plugin_tree(_plugins_root(), sport)
    shared = {name: cls for cls, (_, names) in CARRIERS.items() for name in names}
    strays = [f"{node.name}.{name}" for node in tree.body if isinstance(node, ast.ClassDef)
              for name, home in shared.items()
              if node.name != home and _method(node, name) is not None]
    assert strays == []


def test_only_the_decided_sports_override_the_key():
    root = _plugins_root()
    overriding = {sport for sport in SPORTS
                  if any(_method(node, "_favorite_key") is not None
                         for node in _plugin_tree(root, sport)[1].body
                         if isinstance(node, ast.ClassDef))}
    assert overriding == OVERRIDES_FAVORITE_KEY


def test_the_drift_report_still_calls_them_identical():
    root = _plugins_root()
    families = DRIFT.build(root, ("sports.py",))
    rows = {(r["file"], r["family"]): r
            for r in (DRIFT.summarise(k, v) for k, v in families.items())}
    for _, names in CARRIERS.values():
        for name in names:
            row = rows.get(("sports.py", name))
            assert row is None or row["worst_class_variants"] == 1, name
