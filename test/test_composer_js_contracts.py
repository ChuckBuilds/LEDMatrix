"""Structural checks on the composer's JavaScript.

There is no JS test runner in this repo, but three defects here are structural
enough to assert on the parse tree, and each was a real bug:

- Stroke widths inside _drawElement were left in canvas pixels while the
  geometry around them scaled by SCALE, so at SCALE>1 every outline rendered
  thinner than one LED pixel and the preview stopped matching the panel.
- The `line` branch drew raw el.x0/el.y0/el.x1/el.y1, ignoring the anchor that
  every other element type honours, so setting xAnchor moved everything except
  lines -- and getBoundingBox had the same omission, leaving the hit box behind.
- Four methods mutated exactly what _snapshot() serialises (metadata,
  currentPreset) without calling it. _snapshot is the only caller of
  _debouncedAutosave, so those changes were lost on reload and could not be
  undone.
"""
import re
from pathlib import Path

import pytest

tree_sitter = pytest.importorskip("tree_sitter")
tree_sitter_javascript = pytest.importorskip("tree_sitter_javascript")

JS_DIR = Path(__file__).resolve().parent.parent / "web_interface/static/v3/js/composer"
CANVAS = JS_DIR / "composer-canvas.js"
APP = JS_DIR / "composer-app.js"


def _function_source(path: Path, name: str) -> str:
    """Return the source of a top-level function declaration by name."""
    src = path.read_bytes()
    lang = tree_sitter.Language(tree_sitter_javascript.language())
    tree = tree_sitter.Parser(lang).parse(src)
    found = []

    def walk(node):
        if node.type == "function_declaration":
            ident = node.child_by_field_name("name")
            if ident is not None and src[ident.start_byte:ident.end_byte].decode() == name:
                found.append(src[node.start_byte:node.end_byte].decode())
        for c in node.children:
            walk(c)

    walk(tree.root_node)
    assert found, f"{name} not found in {path.name}"
    return found[0]


def _method_source(path: Path, name: str) -> str:
    """Return the source of a top-level object method by name."""
    src = path.read_bytes()
    lang = tree_sitter.Language(tree_sitter_javascript.language())
    tree = tree_sitter.Parser(lang).parse(src)
    found = []

    def walk(node):
        if node.type == "method_definition":
            ident = node.child_by_field_name("name")
            if ident is not None and src[ident.start_byte:ident.end_byte].decode() == name:
                found.append(src[node.start_byte:node.end_byte].decode())
        for c in node.children:
            walk(c)

    walk(tree.root_node)
    assert found, f"{name} not found in {path.name}"
    return found[0]


def test_both_files_parse():
    lang = tree_sitter.Language(tree_sitter_javascript.language())
    parser = tree_sitter.Parser(lang)
    for path in (CANVAS, APP):
        tree = parser.parse(path.read_bytes())
        errors = []

        def walk(node):
            if node.type == "ERROR" or node.is_missing:
                errors.append(node.start_point[0] + 1)
            for c in node.children:
                walk(c)

        walk(tree.root_node)
        assert not errors, f"{path.name} has parse errors at lines {errors}"


def test_element_strokes_scale_with_scale():
    """No bare `ctx.lineWidth = 1` inside _drawElement.

    Selection handles and the grid are drawn in canvas pixels deliberately and
    live in other functions, so this is scoped to the element drawing routine.
    """
    body = _function_source(CANVAS, "_drawElement")
    offenders = re.findall(r"ctx\.lineWidth\s*=\s*1\s*;", body)
    assert not offenders, f"{len(offenders)} unscaled stroke width(s) in _drawElement"


def test_line_branch_applies_the_anchor_offset():
    """Scoped to _drawElement.

    getBoundingBox has its own `case 'line': {` and appears first in the file,
    so searching the whole text found *that* branch -- this assertion passed
    with the draw branch's anchor offset removed. Verified: stripping it and
    re-running gave 11/11 green.
    """
    body = _function_source(CANVAS, "_drawElement")
    line_branch = body[body.index("case 'line': {"):]
    line_branch = line_branch[:line_branch.index("case 'divider'")]
    assert "ax - el.x0" in line_branch and "ay - el.y0" in line_branch, \
        "line drawing ignores xAnchor/yAnchor"
    assert "moveTo(el.x0 * s" not in line_branch, \
        "line still drawn from unanchored endpoints"


def test_line_bounding_box_applies_the_anchor_offset():
    """The companion to the above: scoped to getBoundingBox specifically, so
    the two tests cannot both be satisfied by the same branch."""
    body = _function_source(CANVAS, "getBoundingBox")
    box = body[body.index("case 'line'"):]
    box = box[:box.index("case 'divider'")]
    assert "ax - el.x0" in box, "line bounding box ignores the anchor"


@pytest.mark.parametrize("method", [
    "onBgColorChange",     # mutates metadata.bgColor
    "setCustomSize",       # mutates currentPreset / MATRIX_W / MATRIX_H
    "changePreset",        # mutates currentPreset / MATRIX_W / MATRIX_H
    "applyPresetLabel",    # same, for sizes not in DISPLAY_PRESETS
    "onColorChange",       # the one that was already fixed — keeps it fixed
])
def test_state_mutations_take_a_snapshot(method):
    body = _method_source(APP, method)
    assert "_snapshot()" in body, \
        f"{method} changes snapshotted state without calling _snapshot()"
    assert "isDirty = true" in body, f"{method} does not mark the design dirty"


@pytest.mark.parametrize("method", ["changePreset", "applyPresetLabel"])
def test_restore_path_stays_snapshot_free(method):
    """_applyState and loadTemplate call these with {silent: true} while
    restoring; snapshotting there would push restore steps onto the undo stack
    and re-autosave the state just loaded."""
    body = _method_source(APP, method)
    assert "opts.silent" in body, f"{method} lost its silent guard"
    snap = body.index("_snapshot()")
    guard = body.index("!opts.silent")
    assert guard < snap, f"{method} snapshots outside the !opts.silent guard"
