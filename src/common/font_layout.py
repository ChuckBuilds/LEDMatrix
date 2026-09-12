"""One text layout engine, everywhere.

``PIL.ImageFont.truetype`` picks its layout engine at load time: Raqm when the
host Pillow was built with libraqm, Basic otherwise. The two disagree about
fractional glyph advances, so the *same* Pillow version renders the *same*
string differently depending on a build option of the host.

That is invisible for ``PressStart2P-Regular.ttf`` at 8px, whose advances are
whole pixels either way — which is why most of the fleet's golden images
matched on every machine. It is not invisible for ``4x6-font.ttf`` at 6px,
where the advances are fractional: glyph positions drift cumulatively along a
run, and the committed goldens for geochron, of-the-day, christmas-countdown
and ledmatrix-weather's almanac passed on the machine that generated them and
failed everywhere else (ChuckBuilds/ledmatrix-plugins#371, #375, #378, #391).

Pinning the Basic engine makes a render depend on the font file and the size,
and nothing else. Basic gives up complex-script shaping (Arabic, Indic) and
kerning pairs; neither applies to the bitmap-grid faces this project draws
with on an LED panel.

Use :func:`load_truetype` in place of ``ImageFont.truetype`` anywhere the
result is drawn to a panel or compared against a golden image.

The module also owns the other two things that decide whether a bundled face
renders reproducibly, for the same reason — they are properties of the font
file, not of whoever is drawing with it:

* :func:`crisp_size` and :data:`FONT_PIXEL_GRID` — the size each face renders
  on whole pixels at. ``4x6-font.ttf`` has a 7px grid, which is why the 6 that
  reads as its natural size is the wrong number everywhere it appears.
* :func:`resolve_asset_path` — ``assets/fonts/...`` resolved against the
  install root rather than the process cwd.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, Union

from PIL import ImageFont

#: The engine every core font load pins. Named once so the reason above has a
#: single referent, and so a future change is one line.
LAYOUT_ENGINE = ImageFont.Layout.BASIC


def load_truetype(font: Union[str, Any], size: int, **kwargs: Any) -> ImageFont.FreeTypeFont:
    """``ImageFont.truetype`` with the layout engine pinned.

    Same signature and same exceptions as the PIL call it replaces, so it is a
    drop-in at every call site.
    """
    kwargs.setdefault("layout_engine", LAYOUT_ENGINE)
    return ImageFont.truetype(font, size, **kwargs)


# --------------------------------------------------------------------------
# Bundled-asset path resolution
# --------------------------------------------------------------------------

#: The install root, derived from this module's own location
#: (``<root>/src/common/font_layout.py``) rather than from the process cwd.
_INSTALL_ROOT = Path(__file__).resolve().parents[2]


def resolve_asset_path(relative_path: str) -> str:
    """Resolve a repo-relative asset path independently of the process cwd.

    Prefers the path as given — so an absolute path is returned untouched and
    behaviour is unchanged wherever the cwd already happened to be the install
    root — then the install root derived above, then the original string so a
    caller that wants to raise and fall back still can.

    Without the fallback, any process started outside the install root (the
    plugin safety harness, a manual ``python run.py`` from ``$HOME``, a unit
    file written without ``WorkingDirectory``) silently loses every font and
    degrades to PIL's default face.
    """
    if os.path.exists(relative_path):
        return relative_path
    candidate = _INSTALL_ROOT / relative_path
    if candidate.exists():
        return str(candidate)
    return relative_path


# --------------------------------------------------------------------------
# Pixel-grid snapping
# --------------------------------------------------------------------------

#: Family aliases the web UI may write, mapped to the shipped filename.
FONT_NAME_ALIASES: Dict[str, str] = {
    "press_start": "PressStart2P-Regular.ttf",
    "four_by_six": "4x6-font.ttf",
}

#: Pixel grid each face renders crisply on. Off-grid sizes anti-alias, which
#: on an LED matrix is a dim lamp rather than a soft edge — and worse under
#: ``draw.fontmode = "1"``, where the mono rasteriser thresholds each glyph at
#: 50% coverage: an off-grid 4x6 glyph renders 3px wide instead of 4, so W/M
#: and 0/8 stop being distinguishable. Off-grid sizes also make ``getlength``
#: return a FreeType-dependent fractional advance, which is how two panels on
#: one config centre the same string differently.
FONT_PIXEL_GRID: Dict[str, int] = {
    "PressStart2P-Regular.ttf": 8,
    "4x6-font.ttf": 7,
}


def crisp_size(font_file, desired, aliases=None, grid_table=None):
    """Snap *desired* to the nearest size *font_file* renders crisply at.

    A face with no known grid is returned unchanged, so a user-supplied font is
    never second-guessed.

    ``aliases`` and ``grid_table`` default to the shared tables; a plugin that
    ships an extra face can pass its own without forking this.
    """
    aliases = FONT_NAME_ALIASES if aliases is None else aliases
    grid_table = FONT_PIXEL_GRID if grid_table is None else grid_table
    font_file = aliases.get(font_file, font_file)
    grid = grid_table.get(font_file)
    if not grid or not desired or desired <= 0:
        return desired
    return max(grid, int(round(float(desired) / grid)) * grid)
