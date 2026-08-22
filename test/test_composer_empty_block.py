"""An element the template cannot draw must not produce an empty `if` block.

manager.py.j2 wraps each element in `if width >= N:` (breakpoint) and/or
`if int(time.time() * 2) % 2:` (blink), and the body comes from the per-type
branches. A type with no branch contributed nothing, so the wrapper opened a
block with no statements in it. ast.parse in _generate_plugin_files then
failed and the caller was told only:

    Generated code has a syntax error: expected an indented block after
    'if' statement on line 49

which names a line of generated source the user never sees. Confirmed against
the code before the fix with a `group` element carrying minWidth.

Two defences, both covered here: _preprocess_elements drops types the template
has no branch for, and the template emits a `pass` fallback so a type added to
the canvas before its branch exists degrades to a no-op instead of a broken
plugin.
"""
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from web_interface.blueprints import composer as C  # noqa: E402

TEMPLATE = (Path(__file__).resolve().parent.parent
            / "web_interface/templates/v3/composer/manager.py.j2")

BASE_META = {"id": "test-plugin", "name": "Clock", "author": "a",
             "version": "1.0.0", "description": "d"}


def generate(element):
    return C._generate_plugin_files({
        "metadata": BASE_META,
        "elements": [element],
        "dataModel": {"configVars": []},
    })


@pytest.mark.parametrize("wrapper", [
    {"minWidth": 64},          # breakpoint block
    {"blink": True},           # blink block
    {"minWidth": 64, "blink": True},   # both, nested
])
@pytest.mark.parametrize("etype", ["group", "widget_9000", "section"])
def test_undrawable_element_does_not_break_generation(etype, wrapper):
    element = {"type": etype, "x": 0, "y": 0, "color": "#ffffff", **wrapper}
    files = generate(element)          # must not raise ComposerInputError
    assert "manager.py" in files


def test_drawable_element_still_renders_inside_a_breakpoint():
    files = generate({"type": "text", "text": "hi", "x": 0, "y": 0,
                      "minWidth": 64, "color": "#ffffff"})
    src = files["manager.py"]
    assert "if width >= 64:" in src
    assert "draw_text" in src


def test_renderable_types_match_the_template_branches():
    """The constant and the template must agree.

    A type listed in the constant with no branch emits an empty block (the bug
    above); a type with a branch but missing from the constant is silently
    dropped from every generated plugin. Neither is visible without this check.
    """
    branches = set(re.findall(r"el\.type == '([a-z_]+)'", TEMPLATE.read_text()))
    assert branches == set(C._RENDERABLE_ELEMENT_TYPES)


def test_template_closes_the_branch_chain_with_a_fallback():
    """Belt and braces: even if the constant drifts, no empty block escapes."""
    text = TEMPLATE.read_text()
    assert "{% else %}" in text
    assert "pass  # element type" in text
