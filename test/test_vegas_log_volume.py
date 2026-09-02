"""The Vegas content path must trace at DEBUG, not INFO.

plugin_adapter narrates every step of acquiring content from every plugin --
"Has get_vegas_content", "Native: calling get_vegas_content()", "Native content
returned None", "Has scroll_helper", the per-item sizes -- and it does that for
each plugin on each cycle.

Measured on a live rig: 13,408 log lines an hour, of which 13,366 were INFO and
35 were WARNING. plugin_adapter alone produced 2,457 of them. That is ~223
lines a minute of string formatting on a Pi that is also driving the panel, all
of it written through journald to the SD card, and it buries the 35 lines that
actually indicate a problem.

Nothing is lost by moving it to DEBUG: the 19 warning/error/exception calls in
the module are untouched, so real failures still surface at their own level.

One INFO call is deliberate and stays -- the padding-strip message chooses its
level at runtime (`logger.warning if (left and right) else logger.info`) and
test_vegas_plugin_adapter.py pins it.
"""
import ast
from pathlib import Path

import pytest

ADAPTER = (Path(__file__).resolve().parent.parent / "src" / "vegas_mode"
           / "plugin_adapter.py")


def _logger_calls(path, *levels):
    """Direct logger.<level>(...) call sites in a module."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found = []
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in levels
                and getattr(node.func.value, "id", None) == "logger"):
            found.append(node.lineno)
    return found


def _info_calls(path):
    """Direct logger.info(...) call sites in a module."""
    return _logger_calls(path, "info")


def test_the_content_path_does_not_trace_at_info():
    calls = _info_calls(ADAPTER)
    assert not calls, (
        "plugin_adapter should trace at DEBUG; found logger.info at lines "
        f"{calls}. This path runs per plugin per cycle and its output goes to "
        "the SD card via journald."
    )


def test_real_failures_still_have_a_level_of_their_own():
    """Demoting the trace must not have swept up the error reporting.

    Counted from the AST rather than with source.count(): the text form also
    matches comments, docstrings and string literals -- including this
    module's own docstring, which names these levels -- so a real
    logger.error() could be demoted while the tally stayed put.
    """
    loud = _logger_calls(ADAPTER, "warning", "error", "exception")
    assert len(loud) >= 15, \
        f"only {len(loud)} warning/error/exception calls remain: {loud}"


def test_the_deliberate_runtime_chosen_level_survives():
    """The padding-strip message picks its level at runtime; leave it alone."""
    source = ADAPTER.read_text(encoding="utf-8")
    assert "logger.warning if (left and right) else logger.info" in source
