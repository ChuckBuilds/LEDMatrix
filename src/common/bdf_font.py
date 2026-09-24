"""Loading and drawing BDF bitmap fonts: one loader, one rasterizer.

BDF fonts are fixed-size bitmap strikes. FreeType renders them at the size
baked into the file and rejects any other size, and PIL cannot draw a
``freetype.Face`` at all, so the core draws BDF text itself, glyph by glyph.

This used to be done in several places that drifted apart:
``FontManager``, ``element_style`` and ``DisplayManager`` each loaded faces
their own way, and ``DisplayManager`` and the plugin test harness
(``VisualTestDisplayManager``) each had a copy of the glyph drawing loop. The
harness renders plugin golden images and ``check_plugin`` / ``dev_server``
previews, so a copy that differs from the panel's shows something the panel
never draws. Everything now goes through the two functions here:

* :func:`load_bdf_face` -- a ``freetype.Face`` at the requested pixel size,
  or at the file's native strike when the file has no strike at that size.
* :func:`draw_bdf_text` -- draw a string in a ``freetype.Face`` onto a PIL
  ``ImageDraw``, top-left anchored like ``ImageDraw.text``.

Only PIL and freetype-py are imported, so the module is as cheap to import
from the test harness as from core.
"""

from __future__ import annotations

import ctypes
import logging
import os
import threading
from collections import OrderedDict
from typing import Any, Optional, Sequence, Tuple

from PIL import Image

try:
    import freetype
except ImportError:  # pragma: no cover - freetype-py is a core requirement
    freetype = None

logger = logging.getLogger(__name__)

__all__ = ["read_bdf_native_size", "load_bdf_face", "draw_bdf_text"]


# --------------------------------------------------------------------------
# Loading
# --------------------------------------------------------------------------

def read_bdf_native_size(bdf_path: str) -> Optional[int]:
    """A BDF file's one true pixel size, read from its header, or None.

    Prefers the PIXEL_SIZE property, which states the real pixel height
    directly; falls back to the SIZE line's point-size only if PIXEL_SIZE is
    absent, since point-size only equals pixel height at exactly 100dpi --
    several bundled fonts (e.g. 6x13.bdf, 5x8.bdf) are defined at 75dpi, where
    the two values genuinely differ. Stops at the first STARTCHAR.
    """
    size_line_value = None
    try:
        with open(bdf_path, "r", encoding="ascii", errors="ignore") as f:
            for line in f:
                if line.startswith("PIXEL_SIZE"):
                    parts = line.split()
                    if len(parts) >= 2:
                        return int(float(parts[1]))
                elif line.startswith("SIZE") and size_line_value is None:
                    # Format: "SIZE <point_size> <xres> <yres>"
                    parts = line.split()
                    if len(parts) >= 2:
                        size_line_value = int(float(parts[1]))
                elif line.startswith("STARTCHAR"):
                    break
    except (OSError, ValueError):
        return None
    return size_line_value


#: Loaded faces, keyed on (absolute path, requested size, mtime_ns, file size)
#: so a font file replaced on disk under the same name is loaded afresh.
#: Bounded LRU: the display process runs for weeks and every config save can
#: introduce a new (font, size) pair, but a panel draws from a handful.
_FACE_CACHE_MAX = 256
_face_cache: "OrderedDict[tuple, Tuple[Any, int]]" = OrderedDict()
_face_cache_lock = threading.Lock()


def _face_at(path: str, size_px: int) -> Any:
    face = freetype.Face(path)
    # Character size in 1/64th points at 72dpi == pixel size.
    face.set_char_size(size_px * 64, size_px * 64, 72, 72)
    return face


def load_bdf_face(path: str, size_px: int) -> Tuple[Any, int]:
    """``(face, realised_px)`` for the BDF file at ``path``.

    ``realised_px`` is ``size_px`` when the file has a strike at that size,
    otherwise the file's native size: FreeType refuses any other size for a
    bitmap font, and answering that with some other typeface (which both
    ``FontManager`` and ``element_style`` once did) is worse than drawing the
    font that was asked for at the size it can do. Callers that lay out by
    size need ``realised_px``, not the size they asked for.

    Faces are cached per thread. A ``freetype.Face`` holds per-glyph state
    (``load_char`` rewrites its glyph slot), and FreeType does not allow two
    threads to use one face at once, so the display thread and a plugin's
    update thread must never be handed the same object. Within a thread the
    face is shared by every caller. Raises if the file can't be loaded at
    either size.
    """
    if freetype is None:
        raise RuntimeError("freetype-py is not installed; BDF fonts need it")
    size_px = int(size_px)
    abs_path = os.path.abspath(path)
    try:
        st = os.stat(abs_path)
        key = (threading.get_ident(), abs_path, size_px,
               st.st_mtime_ns, st.st_size)
    except OSError:
        key = None  # let freetype raise its own error below

    if key is not None:
        with _face_cache_lock:
            cached = _face_cache.get(key)
            if cached is not None:
                _face_cache.move_to_end(key)
                return cached

    try:
        entry = (_face_at(abs_path, size_px), size_px)
    except Exception:
        native = read_bdf_native_size(abs_path)
        if not native or native == size_px:
            raise
        # A fresh Face: the first one already took a failed set_char_size.
        entry = (_face_at(abs_path, native), native)
        logger.debug(
            "BDF font %s requested at %spx renders at its native %spx "
            "(the file has no strike at the requested size)",
            abs_path, size_px, native,
        )

    if key is not None:
        with _face_cache_lock:
            _face_cache[key] = entry
            _face_cache.move_to_end(key)
            while len(_face_cache) > _FACE_CACHE_MAX:
                _face_cache.popitem(last=False)
    return entry


def clear_face_cache() -> None:
    """Drop every cached face (tests; a font directory swapped wholesale)."""
    with _face_cache_lock:
        _face_cache.clear()


# --------------------------------------------------------------------------
# Drawing
# --------------------------------------------------------------------------

def _bitmap_bytes(bitmap: Any, nbytes: int) -> bytes:
    """The first ``nbytes`` of a glyph bitmap's buffer, zero-padded.

    ``bitmap.buffer`` builds a Python list one byte at a time; reading the
    underlying FT_Bitmap directly is the same bytes without that cost.
    """
    raw = getattr(bitmap, "_FT_Bitmap", None)
    if raw is not None and raw.buffer:
        return ctypes.string_at(raw.buffer, nbytes)
    buf = bytes(bitmap.buffer[:nbytes])
    if len(buf) < nbytes:
        buf += bytes(nbytes - len(buf))
    return buf


def _glyph_points(bitmap: Any, left: int, top: int,
                  clip_w: int, clip_h: int) -> list:
    """Every lit pixel of a glyph, clipped, as ``(x, y)`` pairs.

    The reference definition of which pixels a glyph lights: the MSB-first
    bit ``j`` of byte ``i * pitch + j // 8``. Used only where the fast path
    below can't express exactly the same thing.
    """
    buffer = bitmap.buffer
    pitch = bitmap.pitch
    points = []
    for i in range(bitmap.rows):
        for j in range(bitmap.width):
            byte_index = i * pitch + (j // 8)
            if byte_index < len(buffer) and buffer[byte_index] & (1 << (7 - (j % 8))):
                px = left + j
                py = top + i
                if 0 <= px < clip_w and 0 <= py < clip_h:
                    points.append((px, py))
    return points


def draw_bdf_text(draw: Any, text: str, x: int, y: int, face: Any,
                  color: Any = (255, 255, 255),
                  clip: Optional[Sequence[int]] = None) -> int:
    """Draw ``text`` in a ``freetype.Face`` with ``draw``; return the pen x.

    ``(x, y)`` is the top-left of the line, as for ``ImageDraw.text``: the
    baseline is ``y`` plus the face's ascender. Each glyph's lit bits are set
    to ``color`` exactly -- no blending, no anti-aliasing -- and pixels
    outside ``[0, clip_w) x [0, clip_h)`` are skipped (``clip`` defaults to
    the image size). The pen advances by each glyph's advance width.

    Glyphs are drawn as 1-bit masks with ``ImageDraw.bitmap`` rather than a
    point at a time, which is pixel-identical and far faster. A ``draw`` that
    blends (``ImageDraw.Draw(rgb_image, "RGBA")``) is drawn point by point, so
    a translucent colour still blends exactly as it always has.

    Errors (a non-BDF ``face``, a bad colour) propagate after any glyphs
    before the failing one are drawn; callers decide whether to log them.
    """
    try:
        ascender_px = face.size.ascender >> 6
    except Exception:
        ascender_px = 0
    baseline_y = y + ascender_px

    if clip is None:
        clip_w, clip_h = draw.im.size
    else:
        clip_w, clip_h = int(clip[0]), int(clip[1])
    blending = draw.mode != draw.im.mode

    for char in text:
        face.load_char(char)
        glyph = face.glyph
        bitmap = glyph.bitmap
        rows, width, pitch = bitmap.rows, bitmap.width, bitmap.pitch
        left = x + glyph.bitmap_left
        top = baseline_y - glyph.bitmap_top

        if rows > 0 and width > 0:
            if blending or pitch <= 0:
                points = _glyph_points(bitmap, left, top, clip_w, clip_h)
                if points:
                    draw.point(points, fill=color)
            else:
                # The visible part of the glyph box, in glyph coordinates.
                x0, y0 = max(0, -left), max(0, -top)
                x1, y1 = min(width, clip_w - left), min(rows, clip_h - top)
                if x0 < x1 and y0 < y1:
                    # Raw mode "1" with stride=pitch reads exactly the bits
                    # _glyph_points does, whatever the glyph's pixel mode.
                    mask = Image.frombytes(
                        "1", (width, rows), _bitmap_bytes(bitmap, rows * pitch),
                        "raw", "1", pitch)
                    if (x0, y0, x1, y1) != (0, 0, width, rows):
                        mask = mask.crop((x0, y0, x1, y1))
                    # An all-blank glyph draws nothing -- and, as before,
                    # never touches the colour.
                    if mask.getbbox() is not None:
                        draw.bitmap((left + x0, top + y0), mask, fill=color)

        x += glyph.advance.x >> 6
    return x
