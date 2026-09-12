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


TEMPLATE_HTML = (Path(__file__).resolve().parent.parent
                 / "web_interface/templates/v3/composer.html")

#: The six toolbar buttons and the wrapper each must call.
ALIGN_BUTTONS = ["alignLeft", "alignHCenter", "alignRight",
                 "alignTop", "alignVCenter", "alignBottom"]


def test_alignment_buttons_use_the_anchor_clearing_path():
    """Two alignment implementations existed and the toolbar used the wrong one.

    The legacy alignElement(dir) set el.x/el.y but left xAnchor/yAnchor in
    place. resolveAnchor turns anchor='right' into `dim - val`, so "align left"
    (el.x = 0) resolved to x = MATRIX_W -- the element jumped to the far right
    edge instead. _alignElement clears the anchor first, so the stored value is
    absolute, and it also updates el.x0/el.y0 so lines actually move.
    """
    html = TEMPLATE_HTML.read_text()
    for wrapper in ALIGN_BUTTONS:
        assert f"{wrapper}()" in html, f"toolbar does not call {wrapper}()"
    assert not re.search(r"[^_]alignElement\(", html), \
        "toolbar still calls the legacy alignElement()"


def test_the_legacy_alignelement_is_gone():
    """Leaving it in place invites the toolbar drifting back to it."""
    src = APP.read_text()
    assert not re.search(r"^\s{4}alignElement\(dir\)", src, re.M), \
        "legacy alignElement(dir) still defined"


def test_align_clears_the_anchor_and_moves_line_endpoints():
    body = _method_source(APP, "_alignElement")
    assert "xAnchor = null" in body and "yAnchor = null" in body, \
        "_alignElement no longer clears the anchor, so aligning an anchored " \
        "element resolves to the wrong edge"
    assert "el.x0" in body and "el.y0" in body, \
        "_alignElement no longer moves line endpoints"


def test_align_translates_both_line_endpoints_not_just_the_start():
    """Setting only x0 (or y0) left x1/y1 behind, so aligning a line changed
    its shape instead of moving it -- e.g. a line from x0=20 to x1=50 aligned
    right became x0=98, x1=50, stretching rather than translating it. Both
    endpoints must move by the same delta."""
    body = _method_source(APP, "_alignElement")
    x_branch = body[body.index("if (axis === 'x')"):body.index("} else {")]
    y_branch = body[body.index("} else {"):]
    assert "el.x1" in x_branch, \
        "_alignElement moves x0 but not x1 -- a line's shape changes, not its position"
    assert "el.y1" in y_branch, \
        "_alignElement moves y0 but not y1 -- a line's shape changes, not its position"


def test_divider_stroke_is_centered_in_led_pixels():
    """A 0.5 canvas-pixel offset (correct only at SCALE=1) was applied after
    scaling instead of before it, so at SCALE>1 the stroke bled into the
    preceding LED row/column instead of straddling its own."""
    body = _function_source(CANVAS, "_drawElement")
    divider_branch = body[body.index("case 'divider'"):]
    divider_branch = divider_branch[:divider_branch.index("case 'pips'")]
    assert "ay * s + 0.5" not in divider_branch, \
        "divider still offsets by 0.5 canvas pixels after scaling"
    assert "ax * s + 0.5" not in divider_branch, \
        "divider still offsets by 0.5 canvas pixels after scaling"
    assert "(ay + 0.5) * s" in divider_branch and "(ax + 0.5) * s" in divider_branch, \
        "divider stroke is not centered within its LED pixel"


def test_gauge_radii_are_clamped_to_zero():
    """An imported design can carry a small gauge with a wide lineWidth --
    width=1, height=1, lineWidth=3 sends a negative radius into
    ctx.ellipse(), which throws IndexSizeError and aborts render() for every
    element still to be drawn, not just the gauge."""
    body = _function_source(CANVAS, "_drawElement")
    gauge_branch = body[body.index("case 'gauge'"):]
    assert re.search(r"Math\.max\(0,\s*rx\s*-\s*lwPx\s*/\s*2\)", gauge_branch), \
        "gauge x-radius is not clamped to zero"
    assert re.search(r"Math\.max\(0,\s*ry\s*-\s*lwPx\s*/\s*2\)", gauge_branch), \
        "gauge y-radius is not clamped to zero"
    assert "rx - lwPx / 2" not in re.sub(r"Math\.max\(0,\s*rx\s*-\s*lwPx\s*/\s*2\)", "", gauge_branch), \
        "an unclamped gauge radius is still passed to ctx.ellipse()"


#: ELEMENT_DEFAULTS in composer-canvas.js gives exactly these types a
#: `binding` object; verified against that file rather than assumed.
BOUND_TYPES = ["dynamic_text", "progress_bar", "countdown", "pips", "sparkline", "gauge"]


def test_bound_types_constant_lists_every_bound_element_type():
    """ELEMENT_DEFAULTS in composer-canvas.js gives dynamic_text, progress_bar,
    countdown, pips, sparkline and gauge a `binding` object. If a type is added
    to (or removed from) that list without updating APP's BOUND_TYPES, the
    checks below drift out of sync silently -- this pins the two together."""
    src = APP.read_text()
    match = re.search(r"const BOUND_TYPES = \[([^\]]*)\];", src)
    assert match, "BOUND_TYPES constant not found in composer-app.js"
    declared = {t.strip().strip("'\"") for t in match.group(1).split(",") if t.strip()}
    assert declared == set(BOUND_TYPES), \
        f"BOUND_TYPES {declared} does not match the bound element types {set(BOUND_TYPES)}"


@pytest.mark.parametrize("method", ["_isBound", "removeConfigVar", "_validateBeforeExport"])
def test_binding_checks_cover_every_bound_element_type(method):
    """Only dynamic_text and progress_bar were checked, so a countdown, pips,
    sparkline or gauge element with an empty binding passed export validation
    silently, and deleting a config var still used by one of them gave no
    warning -- the plugin would read a now-missing key at runtime with no
    indication why."""
    body = _method_source(APP, method)
    assert "BOUND_TYPES.includes(e.type)" in body, \
        f"{method} does not check every bound element type via BOUND_TYPES"
    assert "e.type === 'dynamic_text'" not in body, \
        f"{method} still hardcodes only dynamic_text instead of BOUND_TYPES"
