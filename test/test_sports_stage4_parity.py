"""The stage 4 sports modules still match every plugin copy that remains.

``sports_plugin_host``, ``sports_live_scroll`` and ``sports_display_rules``
were copied from the scoreboard plugins, which delete their copies once they
floor on the release that ships these. Until each has, a copy that changes on
its own is a fix one side has and the other lacks.

Point LEDMATRIX_PLUGINS at a ledmatrix-plugins checkout and every method here
is compared with every plugin copy using ``scripts/sports_drift_report.py``'s
own normalisation -- the AST with docstrings, decorators and annotations
dropped, which is how the report decided these families are identical -- and,
because that normalisation drops them, the decorators are compared as well
(``@staticmethod`` vs ``@classmethod`` vs ``@contextmanager`` is behaviour).
Class constants are compared by value. A copy that is gone counts as adopted
when the plugin's file names the module. Without the variable this skips:
core CI has no plugins checkout.

``sports_font_path`` is compared by behaviour instead (its body is the
plugins' probe with the dead branches removed); see test_sports_font_path.py.
"""

import ast
import importlib.util
import os
from pathlib import Path

import pytest

from src.common import sports_display_rules, sports_live_scroll, sports_plugin_host

REPO = Path(__file__).resolve().parents[1]

ALL = ("afl", "baseball", "basketball", "football", "hockey", "lacrosse",
       "nrl", "soccer", "ufc")
NO_UFC = tuple(s for s in ALL if s != "ufc")


def _is_plugin_class(name: str) -> bool:
    return name.endswith("ScoreboardPlugin")


#: (module, mixin, plugin file, which plugin classes may hold a copy,
#:  {promoted name: the plugins that carry it}).
#: A name's carriers are the plugins whose copy was compared when it moved;
#: the others never had one, and must not grow one either.
PROMOTED = [
    (sports_plugin_host, "SportsPluginHostMixin", "manager.py", _is_plugin_class,
     {name: ALL for name in (
         "_SWITCH_REFRESH_MIN_GAP_SECONDS", "_dispatch_switch_refresh",
         "get_vegas_priority_weight", "_favorite_team_is_live",
         "_favorite_scan_targets", "_favorite_scan_games", "_game_involves",
         "get_vegas_content_type", "_dynamic_feature_enabled",
         "_get_total_games_for_manager", "_build_manager_key")}),
    (sports_live_scroll, "SportsLiveScrollMixin", "manager.py", _is_plugin_class,
     {name: NO_UFC for name in (
         "LIVE_SCROLL_REBUILD_MIN_SECONDS", "LIVE_SCROLL_REBUILD_DUTY_DIVISOR",
         "_live_scroll_managers", "_refresh_live_scroll_managers",
         "_live_scroll_fields", "_fingerprint_games", "_live_scroll_fingerprint",
         "_live_scroll_needs_rebuild", "_note_live_scroll_built",
         "_preserving_scroll_position")}),
    (sports_display_rules, "SportsCardOptionsMixin", "sports.py",
     lambda name: name == "SportsCore",
     {"_card_option": NO_UFC, "_recent_date_text": NO_UFC}),
    (sports_display_rules, "SportsGameRulesMixin", "sports.py",
     lambda name: name in ("SportsCore", "SportsLive"),
     {"_filtered_or_all": tuple(s for s in ALL if s != "football"),
      "_effective_live_duration": NO_UFC}),
]


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
                    "compare these modules against the plugin copies")
    return root


def _members(tree, wanted):
    """{name: node} for the functions and constants of the classes ``wanted`` accepts."""
    found = {}
    for node in tree.body:
        if not (isinstance(node, ast.ClassDef) and wanted(node.name)):
            continue
        for item in node.body:
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                found.setdefault(item.name, []).append(item)
            elif isinstance(item, (ast.Assign, ast.AnnAssign)) and item.value is not None:
                target = item.targets[0] if isinstance(item, ast.Assign) else item.target
                if isinstance(target, ast.Name):
                    found.setdefault(target.id, []).append(item)
    return found


def _fingerprint(node):
    """What must agree: the drift report's body digest plus the decorators,
    or a constant's value."""
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return ("def", DRIFT._digest(node, DRIFT._Canonical()),
                tuple(ast.unparse(d) for d in node.decorator_list))
    return ("value", ast.dump(node.value))


def _core_members(module, mixin):
    tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
    return {name: nodes[0] for name, nodes in _members(tree, lambda n: n == mixin).items()}


CASES = [(module.__name__.rsplit(".", 1)[1], mixin, name)
         for module, mixin, _file, _cls, carriers in PROMOTED
         for name in sorted(carriers)]


def test_every_promoted_name_has_a_parity_case():
    """A method added to a mixin without a row above would go unchecked."""
    for module, mixin, _file, _cls, carriers in PROMOTED:
        assert sorted(_core_members(module, mixin)) == sorted(carriers), mixin


@pytest.mark.parametrize("module_name,mixin,name", CASES, ids=lambda v: str(v))
def test_every_remaining_plugin_copy_matches(module_name, mixin, name):
    root = _plugins_root()
    module, _mixin, filename, wanted, by_name = next(
        row for row in PROMOTED if row[1] == mixin)
    carriers = by_name[name]
    ours = _fingerprint(_core_members(module, mixin)[name])
    drifted, missing, extra = [], [], []
    for sport in ALL:
        path = root / f"{sport}-scoreboard" / filename
        source = path.read_text(encoding="utf-8")
        copies = _members(ast.parse(source), wanted).get(name, [])
        if sport not in carriers:
            if copies:
                extra.append(sport)
            continue
        if not copies:
            # Gone is fine once the plugin uses the module; otherwise the
            # finder is not seeing its copy.
            if module.__name__ not in source:
                missing.append(sport)
            continue
        drifted += [sport for c in copies if _fingerprint(c) != ours]
    assert missing == [], f"{name} not found in: {missing}"
    assert extra == [], (
        f"{name} appeared in {extra}, which had no copy when it moved; "
        f"decide whether {module_name} should cover it")
    assert drifted == [], (
        f"{name} in {module_name} differs from the copy in: {drifted}. "
        f"Port the change to both, or stop treating it as shared.")


def test_the_drift_report_still_calls_them_identical():
    """The report's own verdict, per family, while any copy is left."""
    root = _plugins_root()
    families = DRIFT.build(root, ("sports.py", "manager.py"))
    rows = {(r["file"], r["family"]): r
            for r in (DRIFT.summarise(k, v) for k, v in families.items())}
    not_identical = []
    for _module, _mixin, filename, _cls, carriers in PROMOTED:
        for name in carriers:
            row = rows.get((filename, name))
            if row is not None and row["worst_class_variants"] != 1:
                not_identical.append(f"{filename}::{name}")
    assert not_identical == []
