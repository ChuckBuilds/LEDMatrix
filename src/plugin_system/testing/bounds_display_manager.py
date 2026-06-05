"""
Bounds-checking display manager.

A VisualTestDisplayManager that draws onto an oversized canvas (the declared
panel size plus a right/bottom margin) while still reporting the declared size
to the plugin. Content that a plugin draws past the right or bottom edge lands
in the margin instead of being silently clipped by PIL, so the harness can
detect overflow — the classic symptom of hardcoded coordinates or fonts/icons
that don't scale down to a smaller panel.

Limitations (documented on purpose):
- Overflow past the LEFT or TOP edge (negative coordinates) is still clipped by
  PIL and not detected here. The dominant real-world breakage is content that is
  too wide/tall for a smaller panel, which this catches.
- BDF text is clipped to the declared bounds by the parent's bitmap drawer, so
  BDF overflow is not flagged. Golden-image regression covers those plugins.
- If a plugin replaces the canvas with its own image (display_manager.image = ...),
  the margin can't be measured and overflow is reported as undetermined (None).
"""

from typing import Optional, Tuple

from .sizes import DEFAULT_TEST_SIZES
from .visual_display_manager import VisualTestDisplayManager, _MatrixProxy

# Smallest extra band kept on the right/bottom so a few pixels of overflow are
# still visible even on the largest panel in a run.
_BASE_MARGIN = 16
# Fallback overflow reference when a caller doesn't pass one: the largest shape
# in the default sample. We extend every (smaller) canvas out to at least this
# size so content drawn at a coordinate meant for a bigger build — e.g. x=200 on
# a 64-wide panel — lands in the padded region and is flagged, instead of being
# clipped off-canvas and read as a false pass.
_DEFAULT_EXTENT_WIDTH = max(w for w, _ in DEFAULT_TEST_SIZES)
_DEFAULT_EXTENT_HEIGHT = max(h for _, h in DEFAULT_TEST_SIZES)


class BoundsCheckingDisplayManager(VisualTestDisplayManager):
    """Detects drawing that overflows the declared panel size."""

    # Kept for backwards compatibility; real padding is computed per-axis below.
    MARGIN = _BASE_MARGIN

    def __init__(self, width: int = 128, height: int = 32,
                 overflow_extent: Optional[Tuple[int, int]] = None):
        self._declared_width = int(width)
        self._declared_height = int(height)
        # Pad the canvas out to at least `overflow_extent` (the largest panel
        # this run cares about) plus a base margin, so coordinates meant for a
        # bigger build are caught — not clipped — when rendering a smaller panel.
        # Defaults to the largest shape in the sample when no run is known.
        ext_w, ext_h = overflow_extent or (_DEFAULT_EXTENT_WIDTH, _DEFAULT_EXTENT_HEIGHT)
        self._canvas_width = max(self._declared_width, int(ext_w)) + _BASE_MARGIN
        self._canvas_height = max(self._declared_height, int(ext_h)) + _BASE_MARGIN
        # Parent builds the (oversized) backing canvas + fonts.
        super().__init__(self._canvas_width, self._canvas_height)
        # Plugins must see the DECLARED size, not the padded canvas size.
        self.matrix = _MatrixProxy(self._declared_width, self._declared_height)

    # -- declared dimensions (override parent's image-derived properties) --

    @property
    def width(self) -> int:
        return self._declared_width

    @property
    def height(self) -> int:
        return self._declared_height

    @property
    def display_width(self) -> int:
        return self._declared_width

    @property
    def display_height(self) -> int:
        return self._declared_height

    # -- overflow detection --

    def _canvas_is_padded(self) -> bool:
        return self.image.size == (self._canvas_width, self._canvas_height)

    def check_overflow(self) -> Optional[Tuple[int, int, int, int]]:
        """Bounding box (in full-canvas coords) of any drawing beyond the
        declared panel, or None if nothing overflowed / undetermined."""
        if not self._canvas_is_padded():
            return None

        exp_w = self._canvas_width
        exp_h = self._canvas_height
        boxes = []

        right = self.image.crop((self._declared_width, 0, exp_w, exp_h)).getbbox()
        if right:
            boxes.append((right[0] + self._declared_width, right[1],
                          right[2] + self._declared_width, right[3]))

        bottom = self.image.crop((0, self._declared_height, exp_w, exp_h)).getbbox()
        if bottom:
            boxes.append((bottom[0], bottom[1] + self._declared_height,
                          bottom[2], bottom[3] + self._declared_height))

        if not boxes:
            return None
        return (
            min(b[0] for b in boxes), min(b[1] for b in boxes),
            max(b[2] for b in boxes), max(b[3] for b in boxes),
        )

    # -- snapshot/image accessors return the cropped, true-panel image --

    def declared_image(self):
        """The visible panel: the canvas cropped to the declared size."""
        if self._canvas_is_padded():
            return self.image.crop((0, 0, self._declared_width, self._declared_height))
        return self.image

    def save_snapshot(self, path: str) -> None:
        self.declared_image().save(path, format='PNG')

    def get_image(self):
        return self.declared_image()

    def get_image_base64(self) -> str:
        import base64
        import io
        buffer = io.BytesIO()
        self.declared_image().save(buffer, format='PNG')
        return base64.b64encode(buffer.getvalue()).decode('utf-8')
