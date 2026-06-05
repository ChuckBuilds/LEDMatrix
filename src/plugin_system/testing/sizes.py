"""
LED matrix sizes the plugin safety harness renders against.

There is no fixed set of "supported" panel sizes — an RGB matrix build can be
any width/height and configuration (square, rectangle, 2x2, 4x4, 8x2, long
strips, tall stacks, ...). Plugins are expected to read width/height
dynamically and lay themselves out accordingly, so the harness's job is to
prove a plugin survives a *spread* of shapes, not a canonical list.

`DEFAULT_TEST_SIZES` is therefore a representative SAMPLE chosen to span the
axes of variation (narrow, wide, square, tall, small, long), not an
exhaustive or authoritative list. Callers can override it entirely:

  - CLI:        scripts/check_plugin.py --sizes 8x16,64x64,256x32
  - pytest:     LEDMATRIX_TEST_SIZES="8x16,64x64" env var (all plugins), or
                per-plugin test/harness.json {"sizes": [[8, 16], [64, 64]]}

so anyone can point the harness at the exact panel(s) their build uses.
"""

import os
from typing import Iterable, List, Optional, Sequence, Tuple, Union

# A spread of real panel-grid arrangements (each module is 64x32), not a list of
# "blessed" sizes. Each entry exercises a different layout assumption a plugin
# might accidentally bake in. Annotations are the panel grid (cols x rows).
DEFAULT_TEST_SIZES: List[Tuple[int, int]] = [
    (64, 32),    # 1x1 — single panel, the tightest common rectangle
    (128, 32),   # 2x1 — the baseline most plugins are tuned for
    (64, 64),    # 1x2 — stacked, exercises tall-narrow centering
    (128, 64),   # 2x2 — block, icon scaling / vertical centering
    (256, 32),   # 4x1 — long strip, wide horizontal layout
    (128, 96),   # 2x3 — tall, exercises vertical overflow
    (256, 128),  # 4x4 — large block, both dimensions big at once
]

# Backwards-compatible alias. Prefer DEFAULT_TEST_SIZES in new code — the old
# name implied these were the only valid panel sizes, which they are not.
SUPPORTED_SIZES = DEFAULT_TEST_SIZES


def size_label(width: int, height: int) -> str:
    """Human/path-friendly label for a size, e.g. '128x32'."""
    return f"{width}x{height}"


def parse_size_token(token: str) -> Tuple[int, int]:
    """Parse a single 'WxH' token into an (int, int) pair.

    Raises ValueError (with a user-friendly message) on malformed input so
    callers can surface it however they like.
    """
    cleaned = token.strip().lower()
    if "x" not in cleaned:
        raise ValueError(f"Invalid size '{token}' (expected WxH, e.g. 128x32)")
    w, h = cleaned.split("x", 1)
    try:
        width, height = int(w), int(h)
    except ValueError as exc:
        raise ValueError(
            f"Invalid size '{token}' (expected numeric WxH, e.g. 128x32)"
        ) from exc
    if width <= 0 or height <= 0:
        raise ValueError(
            f"Invalid size '{token}' (width and height must be positive, e.g. 128x32)"
        )
    return (width, height)


def coerce_sizes(
    value: Union[str, Iterable[Sequence[int]], None]
) -> Optional[List[Tuple[int, int]]]:
    """Normalize a size spec into a list of (w, h) tuples, or None if empty.

    Accepts a comma-separated 'WxH,WxH' string (CLI / env var) or an iterable
    of [w, h] / (w, h) pairs (harness.json). Returns None when value is falsy
    so callers can fall back to the default sample.
    """
    if not value:
        return None
    if isinstance(value, str):
        return [parse_size_token(tok) for tok in value.split(",") if tok.strip()]
    sizes: List[Tuple[int, int]] = []
    for pair in value:
        w, h = pair  # raises if not a 2-element sequence
        width, height = int(w), int(h)
        if width <= 0 or height <= 0:
            raise ValueError(f"Invalid size pair {pair!r} (width and height must be positive)")
        sizes.append((width, height))
    return sizes or None


def resolve_test_sizes(
    spec_sizes: Union[str, Iterable[Sequence[int]], None] = None,
) -> List[Tuple[int, int]]:
    """Decide which sizes to render, by precedence:

    1. LEDMATRIX_TEST_SIZES env var — a global "test on my hardware" override
       that wins for every plugin.
    2. spec_sizes — e.g. a per-plugin harness.json "sizes" list.
    3. DEFAULT_TEST_SIZES — the representative sample.
    """
    env = coerce_sizes(os.environ.get("LEDMATRIX_TEST_SIZES"))
    if env:
        return env
    spec = coerce_sizes(spec_sizes)
    if spec:
        return spec
    return list(DEFAULT_TEST_SIZES)


def safe_mode_filename(mode: str) -> str:
    """A filesystem-safe basename for a plugin mode.

    Mode names come from plugin metadata/render state, so a value containing
    '/' or '..' could otherwise escape the intended output directory. Collapse
    anything that isn't alphanumeric / dash / underscore to '_'.
    """
    cleaned = "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in mode)
    return cleaned or "mode"
