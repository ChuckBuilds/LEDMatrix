"""The visual-test double must not drift from the real DisplayManager.

Both directions of drift are silent and both are damaging:

  * the double accepts an argument production does not -- the call passes
    every harness run and raises TypeError on the panel, which is precisely
    the failure a safety harness exists to prevent;
  * the double lacks an argument production has -- every plugin that
    legitimately uses it fails every render, and the harness blames the
    plugin.

`set_scrolling_state` has been each of those in turn across two branches, so
the parity is asserted rather than remembered.

Read with ast rather than imported: src/display_manager.py imports rgbmatrix
at module scope, which is absent anywhere without the panel library, and this
check should hold on a laptop and in CI as well as on a Pi.
"""

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
REAL = ROOT / "src" / "display_manager.py"
DOUBLE = ROOT / "src" / "plugin_system" / "testing" / "visual_display_manager.py"

#: Methods a plugin calls on whichever manager it is handed.
SHARED_METHODS = ["set_scrolling_state", "is_currently_scrolling"]


def _signature(path: Path, class_hint: str, method: str):
    """(name, default-repr) pairs for `method`, or None if it is absent."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef) or class_hint not in node.name:
            continue
        for item in node.body:
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name == method:
                args = [a.arg for a in item.args.args if a.arg != "self"]
                pad = [None] * (len(args) - len(item.args.defaults))
                defaults = pad + [ast.unparse(d) for d in item.args.defaults]
                return list(zip(args, defaults))
    return None


@pytest.mark.parametrize("method", SHARED_METHODS)
def test_the_double_matches_production(method):
    real = _signature(REAL, "DisplayManager", method)
    double = _signature(DOUBLE, "DisplayManager", method)

    assert real is not None, f"DisplayManager lost {method}"
    assert double is not None, \
        f"the test double is missing {method}, so every plugin using it fails to render"
    assert double == real, (
        f"{method} has drifted: production takes {real}, the double takes {double}. "
        "A double that is more permissive hides a production TypeError; one that "
        "is less permissive fails plugins that are actually correct."
    )


def test_frame_hold_is_accepted_by_both():
    """The specific argument that has drifted twice."""
    for path, label in ((REAL, "DisplayManager"), (DOUBLE, "VisualTestDisplayManager")):
        params = dict(_signature(path, "DisplayManager", "set_scrolling_state") or [])
        assert "frame_hold" in params, f"{label} does not accept frame_hold"
        assert params["frame_hold"] == "1", \
            f"{label} must default frame_hold to 1 so existing callers are unaffected"
