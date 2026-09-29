"""The stage 3 sports modules still match every plugin copy that remains.

``sports_celebration``, ``sports_fetch`` and ``sports_card_wrappers`` were
copied from the scoreboard plugins, which delete their copies once they floor
on the release that ships these. Until each has, a copy that changes on its
own is a fix one side has and the other lacks. Point LEDMATRIX_PLUGINS at a
ledmatrix-plugins checkout and every body here is compared, as an AST with
docstrings and type annotations removed and public names folded to the
plugins' private spelling, against every plugin copy. A copy that is gone
counts as adopted. Without the variable this skips: core CI has no plugins
checkout.
"""

import ast
import os
from pathlib import Path

import pytest

from src.common import sports_card_wrappers, sports_celebration, sports_fetch

#: module -> (its mixin, plugin file, plugin class, carriers,
#: {plugin: names it deliberately overrides}).
MODULES = {
    sports_celebration: ("SportsCelebrationMixin", "sports.py", "SportsLive",
                         ("afl", "football", "hockey", "nrl", "soccer"), {}),
    sports_fetch: ("SportsFetchMixin", "sports.py", "SportsCore",
                   ("afl", "baseball", "basketball", "football", "hockey",
                    "lacrosse", "nrl", "soccer", "ufc"), {}),
    sports_card_wrappers: ("SportsCardWrappersMixin", "game_renderer.py", "GameRenderer",
                           ("afl", "baseball", "basketball", "football", "hockey",
                            "lacrosse", "nrl", "soccer"),
                           {"football": {"_format_game_date", "_upcoming_center_mode"}}),
}

#: Type aliases the modules declare for annotations; nothing to compare.
TYPE_ALIASES = {"Color", "Palette", "Flake", "_Buckets"}

#: Public here, private in the plugins.
RENAMES = {name: "_" + name for name in (
    "rgb_luminance", "rgb_saturation", "color_distance", "mix_color",
    "scale_color", "lift_color", "cap_luminance", "dim_rgba", "logo_palette")}


def _plugins_root():
    raw = os.environ.get("LEDMATRIX_PLUGINS")
    if not raw:
        pytest.skip("set LEDMATRIX_PLUGINS to a ledmatrix-plugins checkout to "
                    "compare these modules against the plugin copies")
    root = Path(raw)
    if (root / "plugins").is_dir():
        root = root / "plugins"
    if not (root / "football-scoreboard" / "sports.py").is_file():
        pytest.skip(f"LEDMATRIX_PLUGINS={raw} has no football-scoreboard/sports.py")
    return root


class _Normalise(ast.NodeTransformer):
    """Drop docstrings and annotations; fold public names to private ones."""

    def visit_Name(self, node):
        node.id = RENAMES.get(node.id, node.id)
        return node

    def visit_arg(self, node):
        node.annotation = None
        return node

    def visit_AnnAssign(self, node):
        return self.visit(ast.Assign(targets=[node.target], value=node.value, lineno=0))

    def visit_FunctionDef(self, node):
        node.name = RENAMES.get(node.name, node.name)
        node.returns = None
        body = node.body
        if (body and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)):
            node.body = body[1:] or [ast.Pass()]
        self.generic_visit(node)
        return node


def _dump(node):
    node = ast.parse(ast.unparse(node)).body[0]  # detach and copy
    return ast.dump(_Normalise().visit(node))


def _definitions(tree, class_name):
    """Module-level functions and assignments, plus ``class_name``'s members."""
    found = {}

    def add(node, owner):
        if isinstance(node, ast.FunctionDef):
            found[(owner, node.name)] = node
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            target = node.targets[0] if isinstance(node, ast.Assign) else node.target
            if isinstance(target, ast.Name) and node.value is not None:
                found[(owner, target.id)] = node

    for node in tree.body:
        add(node, "module")
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            for item in node.body:
                add(item, "class")
    return found


def _promoted(module, mixin):
    """What the module moved: its functions and ``_PALETTE_*``-style constants,
    and its mixin's methods and constants (not the host-contract annotations)."""
    tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
    ours = {}
    for (owner, name), node in _definitions(tree, mixin).items():
        if name in TYPE_ALIASES:
            continue
        ours[(owner, RENAMES.get(name, name))] = node
    return ours


CASES = [(module.__name__.rsplit(".", 1)[1], key)
         for module, (mixin, *_rest) in MODULES.items()
         for key in sorted(_promoted(module, mixin))]


@pytest.mark.parametrize("module_name,key", CASES, ids=lambda v: str(v))
def test_every_remaining_plugin_copy_matches(module_name, key):
    root = _plugins_root()
    module = next(m for m in MODULES if m.__name__.endswith("." + module_name))
    mixin, filename, class_name, carriers, overrides = MODULES[module]
    ours = _dump(_promoted(module, mixin)[key])
    drifted, missing = [], []
    for sport in carriers:
        source = (root / f"{sport}-scoreboard" / filename).read_text(encoding="utf-8")
        theirs = _definitions(ast.parse(source), class_name).get(key)
        if key[1] in overrides.get(sport, ()):
            continue
        if theirs is None:
            # Gone is fine once the plugin uses the module; otherwise the
            # finder is not seeing its copy.
            if module.__name__ not in source:
                missing.append(sport)
        elif _dump(theirs) != ours:
            drifted.append(sport)
    assert missing == [], f"{key[1]} not found in: {missing}"
    assert drifted == [], (
        f"{key[1]} in {module_name} differs from the copy in: {drifted}. "
        f"Port the change to both, or stop treating it as shared.")
