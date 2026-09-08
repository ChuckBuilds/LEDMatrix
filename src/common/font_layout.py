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
"""

from __future__ import annotations

from typing import Any, Union

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
