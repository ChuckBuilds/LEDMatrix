"""
Canonical LED matrix sizes the plugin safety harness verifies against.

A single panel is 64x32. Panels chain horizontally and stack vertically, so
the common builds are one panel (64x32), two chained (128x32), a 2x2 block
(128x64), and four chained (256x32). Plugins must render correctly on all of
these because they read width/height dynamically at render time.
"""

from typing import List, Tuple

# (width, height) pairs. Keep widest/tallest extremes in the list so layout
# assumptions about a "normal" panel get exercised in both directions.
SUPPORTED_SIZES: List[Tuple[int, int]] = [
    (64, 32),    # single panel — tightest layout
    (128, 32),   # two chained — the common baseline most plugins are tuned for
    (128, 64),   # 2x2 — exercises vertical centering / icon scaling
    (256, 32),   # four chained — exercises wide horizontal layout
]


def size_label(width: int, height: int) -> str:
    """Human/path-friendly label for a size, e.g. '128x32'."""
    return f"{width}x{height}"


def safe_mode_filename(mode: str) -> str:
    """A filesystem-safe basename for a plugin mode.

    Mode names come from plugin metadata/render state, so a value containing
    '/' or '..' could otherwise escape the intended output directory. Collapse
    anything that isn't alphanumeric / dash / underscore to '_'.
    """
    cleaned = "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in mode)
    return cleaned or "mode"
