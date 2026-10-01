"""Live elements: Vegas content that can change while it is on screen.

A plugin's ``get_vegas_content()`` hands the Vegas ticker pictures, and the
ticker bakes them into its strip: a score drawn when the plugin's turn was
prefetched scrolls past with that score, however many goals are scored while
it crosses the panel. A plugin that returns **elements** instead gives each
picture a name and a fixed width. The ticker then keeps track of where each
one is in the strip, and when the plugin's data changes it asks for just the
changed elements and swaps their pixels in place -- on screen included,
between two frames, without anything next to them moving.

A plugin opts in by implementing ``BasePlugin.get_vegas_elements()``, and, for
content that changes with time rather than with data (an aircraft moving
between position reports), ``BasePlugin.redraw_vegas_element()``. See "Live
Vegas elements" in docs/PLUGIN_API_REFERENCE.md.

Added in LEDMatrix 3.8.0. Import it guarded, so the plugin still loads on an
older core (which never calls the hooks)::

    try:
        from src.plugin_system.vegas_elements import VegasElement
    except ImportError:          # core older than 3.8.0
        VegasElement = None

    def get_vegas_elements(self):
        if VegasElement is None:
            return None
        return [VegasElement(key=f"game:{g['id']}", image=self._card(g),
                             version=self._fingerprint(g))
                for g in self._games]
"""

from dataclasses import dataclass
from typing import Hashable, Optional

from PIL import Image


@dataclass(frozen=True, eq=False)
class VegasElement:
    """One named, fixed-width piece of a plugin's Vegas content.

    Attributes:
        key: Names the element across redraws, unique within one list the
            plugin returns: ``"game:nfl:401547417"``, ``"sep:0:nfl"``,
            ``"map"``. The ticker matches a redraw to the pixels already in
            its strip by this key, so it must stay the same for the same
            logical thing and must not be reused for a different one.
        image: The element as drawn now, at the display's height. For a
            ``live`` element its width is fixed for as long as the key is on
            the strip: a redraw at a different width is never swapped in (it
            appears the next time the plugin comes round instead), because
            nothing on screen may move. Draw live elements at a width that
            does not depend on the data -- a fixed card width, not the
            width of the text.
        version: Anything hashable that changes exactly when the pixels
            would, such as the tuple of fields the element draws. The ticker
            skips work for an unchanged version. ``None`` means "compare the
            pixels", which is always correct and costs a checksum.
        live: False places the element exactly as plain content is placed
            (trimmed to its ink, never refreshed): separators, decoration.
        refresh_hz: More than 0 asks for ``redraw_vegas_element()`` about
            this often while the element is on or near the screen, for
            content that changes with time rather than with data. The ticker
            caps the rate (``vegas_scroll.live_max_hz``, 1 Hz on a display
            without the rebuilt rgbmatrix binding) and slows it for an
            element that is slow to draw.
    """
    key: str
    image: Image.Image
    version: Optional[Hashable] = None
    live: bool = True
    refresh_hz: float = 0.0
