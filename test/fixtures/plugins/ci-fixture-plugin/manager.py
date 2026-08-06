"""
CI fixture plugin.

Exists so the plugin safety harness (test/plugins/test_plugin_matrix.py and
the plugin-safety CI job) always has at least one real plugin to load and
render — without it, an empty plugins/ directory turns the whole job into a
green no-op. The render is deliberately trivial and fully deterministic:
a border rectangle plus both diagonals, sized from the display manager's
declared dimensions. No fonts, no network, no time dependence, so golden
images are stable across platforms.
"""

from PIL import ImageDraw

from src.plugin_system.base_plugin import BasePlugin


class CIFixturePlugin(BasePlugin):
    def update(self) -> None:
        """Nothing to fetch — the render is self-contained."""

    def display(self, force_clear: bool = False) -> None:
        width = self.display_manager.matrix.width
        height = self.display_manager.matrix.height
        border = tuple(self.config.get("border_color", [0, 255, 0]))
        diagonal = tuple(self.config.get("diagonal_color", [255, 0, 0]))

        image = self.display_manager.image
        draw = ImageDraw.Draw(image)
        # Blank only the declared panel area, then draw edge-to-edge content:
        # the border proves the plugin reads dynamic dimensions (any overflow
        # or underfill at any size is a harness bug or a dimensions bug), the
        # diagonals make golden comparisons sensitive to size/offset drift.
        draw.rectangle([0, 0, width - 1, height - 1], fill=(0, 0, 0))
        draw.rectangle([0, 0, width - 1, height - 1], outline=border)
        draw.line([0, 0, width - 1, height - 1], fill=diagonal)
        draw.line([0, height - 1, width - 1, 0], fill=diagonal)
        self.display_manager.update_display()
