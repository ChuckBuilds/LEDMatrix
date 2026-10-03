"""
Vegas live-element stub.

A fixture, not a product: it exercises every part of the live-element contract
(src/plugin_system/vegas_elements.py) with content whose changes are easy to
see and to assert on, and nothing else -- no fonts, no network.

- ``card:<n>`` -- fixed-width cards. Each draws the bits of ``tick + n`` as
  lit bars, so every update() changes every card's pixels but never its width.
- ``sep`` -- a separator, ``live=False``: placed and trimmed like plain content.
- ``map`` -- one full-render-width element with ``refresh_hz``: a dot that
  moves across it with time, drawn by redraw_vegas_element() from state
  published in a single attribute store, so it is safe to call without the
  plugin's lock.

update() only advances the tick. display() draws the tick's bars full screen
so the plugin also passes the ordinary rendering harness.
"""

import time
from typing import List, Optional, Tuple

from PIL import Image, ImageDraw

from src.plugin_system.base_plugin import BasePlugin

try:
    from src.plugin_system.vegas_elements import VegasElement
except ImportError:  # core older than 3.8.0: the hooks are never called
    VegasElement = None

_COLOURS = [(255, 64, 64), (64, 255, 64), (64, 128, 255), (255, 200, 0),
            (255, 64, 255), (0, 220, 220)]


class VegasLiveStub(BasePlugin):
    """Keyed cards, a separator and an animated element for the Vegas ticker."""

    def __init__(self, plugin_id, config, display_manager, cache_manager, plugin_manager):
        super().__init__(plugin_id, config, display_manager, cache_manager, plugin_manager)
        self.tick = 0
        # Everything the lock-free redraw reads, published in one store.
        self._snapshot: Tuple[int, float] = (0, time.monotonic())

    # -- data -------------------------------------------------------------

    def update(self) -> None:
        self.tick += 1
        self._snapshot = (self.tick, time.monotonic())

    # -- drawing ----------------------------------------------------------

    def _bars(self, image: Image.Image, value: int, colour, box) -> None:
        x0, y0, x1, y1 = box
        draw = ImageDraw.Draw(image)
        draw.rectangle([x0, y0, x1, y1], outline=colour)
        bits = 8
        span = max(1, (x1 - x0 - 2) // bits)
        for bit in range(bits):
            if value >> bit & 1:
                left = x0 + 1 + bit * span
                draw.rectangle([left, y0 + 2, left + max(0, span - 2), y1 - 2],
                               fill=colour)

    def _card_width(self) -> int:
        configured = int(self.config.get('card_width', 0) or 0)
        if configured > 0:
            return configured
        return max(24, min(64, self.get_vegas_render_width() // 4))

    def _card(self, index: int, tick: int) -> Image.Image:
        width, height = self._card_width(), self.display_manager.height
        image = Image.new('RGB', (width, height), (0, 0, 0))
        self._bars(image, tick + index, _COLOURS[index % len(_COLOURS)],
                   (0, 0, width - 1, height - 1))
        return image

    def _map(self, width: int, height: int, at: float) -> Image.Image:
        tick, _published = self._snapshot
        image = Image.new('RGB', (width, height), (0, 0, 16))
        draw = ImageDraw.Draw(image)
        draw.rectangle([0, 0, width - 1, height - 1], outline=(40, 40, 80))
        speed = float(self.config.get('dot_speed', 20) or 0)
        x = int(at * speed) % max(1, width - 4) + 2
        y = 2 + tick % max(1, height - 4)
        draw.rectangle([x - 1, y - 1, x + 1, y + 1], fill=(255, 255, 255))
        return image

    def _dot_column(self, width: int, at: float) -> int:
        speed = float(self.config.get('dot_speed', 20) or 0)
        return int(at * speed) % max(1, width - 4) + 2

    def display(self, force_clear: bool = False) -> bool:
        width, height = self.display_manager.width, self.display_manager.height
        self.display_manager.clear()
        self._bars(self.display_manager.image, self.tick, _COLOURS[0],
                   (0, 0, width - 1, height - 1))
        self.display_manager.update_display()
        return True

    # -- Vegas ------------------------------------------------------------

    def get_vegas_content(self) -> Optional[List[Image.Image]]:
        cards = int(self.config.get('cards', 6))
        return [self._card(i, self.tick) for i in range(cards)] or None

    def get_vegas_elements(self):
        if VegasElement is None:
            return None
        tick = self.tick
        elements = []
        for i in range(int(self.config.get('cards', 6))):
            elements.append(VegasElement(
                key=f"card:{i}", image=self._card(i, tick),
                version=(tick, i, self._card_width())))
            if i == 0:
                separator = Image.new('RGB', (4, self.display_manager.height), (0, 0, 0))
                ImageDraw.Draw(separator).rectangle(
                    [1, 0, 2, self.display_manager.height - 1], fill=(90, 90, 90))
                elements.append(VegasElement(key="sep", image=separator, live=False))
        hz = float(self.config.get('map_hz', 4) or 0)
        if hz > 0:
            width, height = self.get_vegas_render_width(), self.display_manager.height
            now = time.monotonic()
            elements.append(VegasElement(
                key="map", image=self._map(width, height, now),
                version=(tick, width, self._dot_column(width, now)),
                refresh_hz=hz))
        return elements

    def redraw_vegas_element(self, key, width, height, at):
        if key != "map":
            return None
        return self._map(width, height, at)
