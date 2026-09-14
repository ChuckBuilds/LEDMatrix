"""Regression: the native color input in the color-picker widget must have
an accessible name independent of showHexInput.

CodeRabbit flagged (PR #568) that the <input type="color"> only carried a
`title` attribute -- screen readers don't reliably announce `title`, and
when showHexInput is false there is no other label naming the control.
"""
from pathlib import Path

WIDGET_JS = (
    Path(__file__).resolve().parent.parent
    / "web_interface" / "static" / "v3" / "js" / "widgets" / "color-picker.js"
)


def test_native_color_input_has_aria_label():
    source = WIDGET_JS.read_text(encoding="utf-8")
    start = source.index('<input type="color"')
    end = source.index(">", start)
    tag = source[start:end]
    assert 'aria-label=' in tag, (
        "native color <input> lost its accessible name; screen readers need "
        "aria-label since it has no associated <label> and showHexInput can "
        "be false"
    )
