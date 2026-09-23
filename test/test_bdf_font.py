"""The one BDF loader and the one BDF rasterizer (src/common/bdf_font.py).

DisplayManager, the plugin test harness (VisualTestDisplayManager), FontManager
and element_style used to carry their own copies of both. Plugin golden images
depend on the exact pixels, so the rasterizer is checked against a frozen copy
of the per-pixel loop DisplayManager._draw_bdf_text ran before it was shared
(``_reference_draw`` below) for every bundled BDF font, at native and off-strike
sizes, clipped and unclipped. Pixels are compared as raw bytes, never PNG
hashes, so a Pillow upgrade can't fake or mask a difference.
"""

import os
import sys
import types
from pathlib import Path

import freetype
import pytest
from PIL import Image, ImageDraw

os.environ.setdefault("EMULATOR", "true")

from src.common import bdf_font
from src.common.bdf_font import draw_bdf_text, load_bdf_face, read_bdf_native_size

FONTS_DIR = Path(__file__).resolve().parent.parent / "assets" / "fonts"
BDF_FONTS = sorted(p.name for p in FONTS_DIR.glob("*.bdf"))

STRINGS = [
    "Hello, World!", "0123456789", "12:34 PM", "!\"#$%&'()*+,-./:;<=>?@[\\]^_`{|}~",
    "AaBbGgJjQqYy|", "", " ", "café üñ 72°F —€",
]


def _reference_draw(draw, width, height, text, x, y, face, color):
    """DisplayManager._draw_bdf_text as it was before the shared rasterizer.

    Frozen on purpose: it is the definition of the panel's output that the
    fast path must reproduce. Do not "fix" it.
    """
    try:
        ascender_px = face.size.ascender >> 6
    except Exception:
        ascender_px = 0
    baseline_y = y + ascender_px
    for char in text:
        face.load_char(char)
        bitmap = face.glyph.bitmap
        glyph_left = face.glyph.bitmap_left
        glyph_top = face.glyph.bitmap_top
        for i in range(bitmap.rows):
            for j in range(bitmap.width):
                byte_index = i * bitmap.pitch + (j // 8)
                if byte_index < len(bitmap.buffer):
                    byte = bitmap.buffer[byte_index]
                    if byte & (1 << (7 - (j % 8))):
                        pixel_x = x + glyph_left + j
                        pixel_y = baseline_y - glyph_top + i
                        if 0 <= pixel_x < width and 0 <= pixel_y < height:
                            draw.point((pixel_x, pixel_y), fill=color)
        x += face.glyph.advance.x >> 6


def _pair(size=(64, 32), mode="RGB", draw_mode=None):
    a = Image.new(mode, size)
    b = Image.new(mode, size)
    return a, ImageDraw.Draw(a, draw_mode), b, ImageDraw.Draw(b, draw_mode)


def _assert_same(expected, actual, what):
    assert expected.tobytes() == actual.tobytes(), what


def _sizes(name):
    native = read_bdf_native_size(str(FONTS_DIR / name))
    return sorted({native, native + 3, max(1, native - 2)})


# ---------------------------------------------------------------- rasterizer

@pytest.mark.parametrize("name", BDF_FONTS)
def test_every_bundled_font_matches_the_reference_raster(name):
    path = str(FONTS_DIR / name)
    w, h = 96, 24
    cases = [((0, 0), (255, 255, 255), text) for text in STRINGS]
    # Clipped on every edge, in a colour that isn't all-or-nothing per channel.
    cases += [(xy, (12, 200, 77), text)
              for xy in ((-4, -3), (w - 11, h - 5), (w // 2, -9))
              for text in STRINGS[:2]]
    for size in _sizes(name):
        face, _ = load_bdf_face(path, size)
        for (x, y), color, text in cases:
            ref, rdraw, new, ndraw = _pair((w, h))
            _reference_draw(rdraw, w, h, text, x, y, face, color)
            draw_bdf_text(ndraw, text, x, y, face, color)
            _assert_same(ref, new, (name, size, x, y, color, text))


def test_returns_the_pen_position():
    face, _ = load_bdf_face(str(FONTS_DIR / "5x7.bdf"), 7)
    _, _, _, draw = _pair()
    assert draw_bdf_text(draw, "", 3, 0, face) == 3
    assert draw_bdf_text(draw, "12:34", 3, 0, face) == 3 + 5 * 5


def test_clip_smaller_than_the_canvas_matches_the_reference():
    # DisplayManager clips to its logical size, which is the canvas size in
    # practice but not by construction; honour the clip as the loop did.
    face, _ = load_bdf_face(str(FONTS_DIR / "6x10.bdf"), 10)
    for clip in ((20, 7), (1, 1), (0, 0), (200, 200)):
        ref, rdraw, new, ndraw = _pair()
        _reference_draw(rdraw, clip[0], clip[1], "Mixed 123", -2, -1, face, (1, 2, 3))
        draw_bdf_text(ndraw, "Mixed 123", -2, -1, face, (1, 2, 3), clip=clip)
        _assert_same(ref, new, clip)


def test_blending_draw_blends_exactly_like_the_reference():
    # ImageDraw.Draw(rgb, "RGBA") blends a translucent colour; a mask fill
    # would not, so this path must fall back to points.
    face, _ = load_bdf_face(str(FONTS_DIR / "7x13B.bdf"), 13)
    ref, rdraw, new, ndraw = _pair(draw_mode="RGBA")
    for img in (ref, new):
        img.paste((40, 80, 120), (0, 0, *img.size))
    _reference_draw(rdraw, 64, 32, "Blend", 1, 1, face, (255, 0, 0, 128))
    draw_bdf_text(ndraw, "Blend", 1, 1, face, (255, 0, 0, 128))
    _assert_same(ref, new, "blend")
    colours = {c for _, c in new.getcolors()}
    assert (255, 0, 0) not in colours and len(colours) == 2, colours  # blended


@pytest.mark.parametrize("mode,color", [("L", 200), ("P", (255, 0, 0)), ("RGBA", (9, 8, 7, 255)), ("1", 1)])
def test_other_canvas_modes_match_the_reference(mode, color):
    face, _ = load_bdf_face(str(FONTS_DIR / "5x8.bdf"), 8)
    ref, rdraw, new, ndraw = _pair(mode=mode)
    _reference_draw(rdraw, 64, 32, "Mode 42", 2, 2, face, color)
    draw_bdf_text(ndraw, "Mode 42", 2, 2, face, color)
    _assert_same(ref, new, mode)


def test_a_non_mono_face_reads_the_same_bits_as_the_reference():
    # A plugin can hand draw_text a freetype.Face of a TTF: 8-bit gray glyphs
    # whose pitch is not ceil(width/8). The loop read them as packed bits, and
    # so must the fast path -- same (odd) pixels, not "better" ones.
    face = freetype.Face(str(FONTS_DIR / "PressStart2P-Regular.ttf"))
    face.set_char_size(8 * 64, 8 * 64, 72, 72)
    ref, rdraw, new, ndraw = _pair()
    _reference_draw(rdraw, 64, 32, "Gray", 0, 0, face, (255, 255, 255))
    draw_bdf_text(ndraw, "Gray", 0, 0, face, (255, 255, 255))
    _assert_same(ref, new, "gray face")


def test_errors_propagate_after_earlier_glyphs_are_drawn():
    face, _ = load_bdf_face(str(FONTS_DIR / "5x7.bdf"), 7)
    _, _, img, draw = _pair()
    with pytest.raises(Exception):
        draw_bdf_text(draw, "A", 0, 0, face, "not-a-colour")
    # Blank glyphs never touch the colour, as before.
    draw_bdf_text(draw, "   ", 0, 0, face, "not-a-colour")
    assert img.getbbox() is None
    with pytest.raises(Exception):
        draw_bdf_text(draw, "A", 0, 0, object())


# ------------------------------------------------------------------- callers

def _dm_stub(img):
    from src.display_manager import DisplayManager
    stub = types.SimpleNamespace(width=img.width, height=img.height,
                                 draw=ImageDraw.Draw(img), calendar_font=None)
    return DisplayManager, stub


@pytest.mark.parametrize("name", ["5x7.bdf", "tom-thumb.bdf", "9x18B.bdf", "MatrixChunky8X.bdf"])
def test_display_manager_and_test_harness_draw_the_reference_pixels(name):
    from src.plugin_system.testing.visual_display_manager import VisualTestDisplayManager

    face, _ = load_bdf_face(str(FONTS_DIR / name), 20)  # off-strike for all four
    for text in STRINGS:
        ref, rdraw, dm_img, _ = _pair((64, 32))
        _reference_draw(rdraw, 64, 32, text, -1, 3, face, (255, 128, 0))

        DisplayManager, stub = _dm_stub(dm_img)
        DisplayManager._draw_bdf_text(stub, text, -1, 3, (255, 128, 0), face)
        _assert_same(ref, dm_img, ("DisplayManager", name, text))

        vt = VisualTestDisplayManager(64, 32)
        vt.draw_text(text, -1, 3, (255, 128, 0), font=face)
        _assert_same(ref, vt.image, ("VisualTestDisplayManager", name, text))


def test_harness_calendar_font_is_the_panels():
    # The harness built a bare freetype.Face with no size: ascender 0, so
    # every calendar_font line drew a baseline too high, and its
    # get_font_height() returned 0.
    from src.plugin_system.testing.visual_display_manager import VisualTestDisplayManager

    vt = VisualTestDisplayManager(64, 32)
    panel_face, _ = load_bdf_face(str(FONTS_DIR / "5x7.bdf"), 7)
    assert vt.calendar_font is panel_face
    assert vt.get_font_height(vt.calendar_font) == panel_face.size.height >> 6 > 0


# -------------------------------------------------------------------- loader

def test_off_strike_size_loads_the_native_strike():
    path = str(FONTS_DIR / "5x7.bdf")
    face, realised = load_bdf_face(path, 10)
    assert isinstance(face, freetype.Face)
    assert realised == 7 and face.size.y_ppem == 7
    assert load_bdf_face(path, 7)[1] == 7


def test_faces_are_cached_and_shared_by_every_loader():
    from src.element_style import _load_bdf
    from src.font_manager import FontManager

    path = str(FONTS_DIR / "6x10.bdf")
    face, realised = load_bdf_face(path, 12)
    assert load_bdf_face(path, 12)[0] is face
    assert _load_bdf(path, 12) == (face, realised)
    assert FontManager({})._load_bdf_font(path, 12) is face


def test_touched_file_is_reloaded(tmp_path):
    # The cache key carries the file's mtime, so a font re-uploaded under the
    # same name is not served from a stale face.
    target = tmp_path / "f.bdf"
    target.write_bytes((FONTS_DIR / "5x7.bdf").read_bytes())
    first, _ = load_bdf_face(str(target), 7)
    assert load_bdf_face(str(target), 7)[0] is first
    os.utime(target, ns=(10**9, 10**9))
    assert load_bdf_face(str(target), 7)[0] is not first


@pytest.mark.skipif(sys.platform == "win32",
                    reason="FreeType holds the font file open; Windows refuses to overwrite it")
def test_replaced_file_is_reloaded(tmp_path):
    target = tmp_path / "f.bdf"
    target.write_bytes((FONTS_DIR / "5x7.bdf").read_bytes())
    first, _ = load_bdf_face(str(target), 7)
    target.write_bytes((FONTS_DIR / "6x10.bdf").read_bytes())
    os.utime(target, ns=(1, 1))
    second, realised = load_bdf_face(str(target), 7)
    assert second is not first and realised == 10


def test_unloadable_file_raises(tmp_path):
    with pytest.raises(Exception):
        load_bdf_face(str(tmp_path / "missing.bdf"), 7)
    bad = tmp_path / "bad.bdf"
    bad.write_text("not a font")
    with pytest.raises(Exception):
        load_bdf_face(str(bad), 7)


def test_cache_is_bounded(monkeypatch):
    monkeypatch.setattr(bdf_font, "_FACE_CACHE_MAX", 2)
    bdf_font.clear_face_cache()
    path = str(FONTS_DIR / "5x7.bdf")
    for size in (7, 8, 9):
        load_bdf_face(path, size)
    assert len(bdf_font._face_cache) == 2
    bdf_font.clear_face_cache()


def test_native_size_prefers_pixel_size_over_point_size():
    # 6x13.bdf is defined at 75dpi: SIZE says 12 (points), PIXEL_SIZE 13.
    assert read_bdf_native_size(str(FONTS_DIR / "6x13.bdf")) == 13
    assert read_bdf_native_size(str(FONTS_DIR / "nope.bdf")) is None
