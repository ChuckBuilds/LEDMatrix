"""
Tests for TextHelper class.

Tests text rendering, font loading, and text positioning utilities.

The second half covers ``draw_text_outlined``, which rasterizes outlined text
once and stamps the mask at each outline offset instead of calling
``draw.text`` once per offset. Its contract is that the pixels are exactly
those of the loop it replaced (``_reference`` below), so that loop is the
oracle for every combination the stamping path accepts, and every case it
does not accept must still go through ``draw.text``.
"""

from pathlib import Path

import pytest
from unittest.mock import MagicMock, patch
from PIL import Image, ImageChops, ImageDraw, ImageFont, features
from src.common import text_helper as text_helper_module
from src.common.font_layout import load_truetype
from src.common.text_helper import (
    OUTLINE_CROSS, OUTLINE_SQUARE, TextHelper, draw_text_outlined)


class TestTextHelper:
    """Test TextHelper functionality."""
    
    @pytest.fixture
    def text_helper(self, tmp_path):
        """Create a TextHelper instance."""
        return TextHelper(font_dir=str(tmp_path))
    
    def test_init(self, tmp_path):
        """Test TextHelper initialization."""
        th = TextHelper(font_dir=str(tmp_path))
        assert th.font_dir == tmp_path
        assert th._font_cache == {}
    
    def test_init_default_font_dir(self, tmp_path, monkeypatch):
        """The default is the install's assets/fonts, not a cwd-relative path."""
        from pathlib import Path
        monkeypatch.chdir(tmp_path)
        th = TextHelper()
        assert th.font_dir == Path(__file__).resolve().parents[1] / "assets" / "fonts"
        assert isinstance(th.load_fonts()["score"], ImageFont.FreeTypeFont)
    
    @patch('PIL.ImageFont.truetype')
    @patch('PIL.ImageFont.load_default')
    def test_load_fonts_success(self, mock_default, mock_truetype, text_helper, tmp_path):
        """Test loading fonts successfully."""
        font_file = tmp_path / "test_font.ttf"
        font_file.write_text("fake font")
        
        mock_font = MagicMock()
        mock_truetype.return_value = mock_font
        
        font_config = {
            "regular": {
                "file": "test_font.ttf",
                "size": 12
            }
        }
        
        fonts = text_helper.load_fonts(font_config)
        
        assert "regular" in fonts
        assert fonts["regular"] == mock_font
    
    @patch('PIL.ImageFont.load_default')
    def test_load_fonts_file_not_found(self, mock_default, text_helper):
        """Test loading fonts when file doesn't exist."""
        mock_font = MagicMock()
        mock_default.return_value = mock_font
        
        font_config = {
            "regular": {
                "file": "nonexistent.ttf",
                "size": 12
            }
        }
        
        fonts = text_helper.load_fonts(font_config)
        
        assert "regular" in fonts
        assert fonts["regular"] == mock_font  # Should use default
    
    def test_draw_text_with_outline(self, text_helper):
        """Test drawing text with outline."""
        # Create a mock image and draw object
        mock_image = Image.new('RGB', (100, 100))
        mock_draw = ImageDraw.Draw(mock_image)
        mock_font = ImageFont.load_default()
        
        # Should not raise an exception
        text_helper.draw_text_with_outline(
            mock_draw, "Hello", (10, 10), mock_font
        )
    
    def test_get_text_dimensions(self, text_helper):
        """Test getting text dimensions."""
        from PIL import Image, ImageDraw
        mock_image = Image.new('RGB', (100, 100))
        mock_draw = ImageDraw.Draw(mock_image)
        mock_font = ImageFont.load_default()
        
        # Patch the draw object in the method
        with patch.object(text_helper, 'get_text_width', return_value=50), \
             patch.object(text_helper, 'get_text_height', return_value=10):
            width, height = text_helper.get_text_dimensions("Hello", mock_font)
            assert width == 50
            assert height == 10
    
    def test_center_text(self, text_helper):
        """Test centering text position."""
        mock_font = ImageFont.load_default()
        
        with patch.object(text_helper, 'get_text_dimensions', return_value=(50, 10)):
            x, y = text_helper.center_text("Hello", mock_font, 100, 20)
            assert x == 25  # (100 - 50) / 2
            assert y == 5   # (20 - 10) / 2
    
    def test_wrap_text(self, text_helper):
        """Test wrapping text to width."""
        mock_font = ImageFont.load_default()
        text = "This is a long line of text"
        
        with patch.object(text_helper, 'get_text_width') as mock_width:
            # Simulate width calculation
            def width_side_effect(text, font):
                return len(text) * 5  # Simple width calculation
            mock_width.side_effect = width_side_effect
            
            lines = text_helper.wrap_text(text, mock_font, max_width=20)
            
            assert isinstance(lines, list)
            assert len(lines) > 0
    
    def test_get_default_font_config(self, text_helper):
        """Test getting default font configuration."""
        config = text_helper._get_default_font_config()

        assert isinstance(config, dict)
        assert len(config) > 0

    def test_each_font_file_and_size_is_loaded_once(self):
        th = TextHelper()
        first = th.load_fonts()
        second = th.load_fonts()
        # Six names, three (file, size) pairs: PressStart2P at 10 and 8, 4x6 at 6.
        assert first["score"] is second["score"] is first["rank"]
        assert th.get_font_cache_stats()["cached_fonts"] == 3
        th.clear_font_cache()
        assert th.get_font_cache_stats()["cached_fonts"] == 0
        assert th.load_fonts()["score"] is not first["score"]


# ---------------------------------------------------------------------------
# draw_text_outlined
# ---------------------------------------------------------------------------

FONTS_DIR = Path(__file__).resolve().parents[1] / "assets" / "fonts"

#: Every bundled TrueType face at the sizes the scoreboards draw, plus sizes
#: off their pixel grid (anti-aliased under fontmode "L"); BDF strikes, which
#: FreeType also loads as a FreeTypeFont at their native size; and Pillow's
#: own default face. 4x6 at 8 and the default face are the ones whose glyph
#: masks start left of or below the draw position (getmask2's offset), which
#: every stamp has to add back.
FONT_CASES = [
    ("PressStart2P-Regular.ttf", 8),
    ("PressStart2P-Regular.ttf", 10),
    ("PressStart2P-Regular.ttf", 16),
    ("4x6-font.ttf", 6),
    ("4x6-font.ttf", 7),
    ("4x6-font.ttf", 8),
    ("4x6-font.ttf", 14),
    ("5by7.regular.ttf", 7),
    ("5by7.regular.ttf", 8),
    ("4x6.bdf", 6),
    ("5x7.bdf", 7),
    ("tom-thumb.bdf", 6),
    ("<default>", None),
]

#: (fill, outline_color) for RGB/RGBA canvases: the scoreboard default, a
#: translucent outline, translucent text, and draw.text's own default ink.
RGB_COLOURS = [
    ((255, 200, 0), (0, 0, 0)),
    ((250, 250, 250), (0, 0, 0, 128)),
    ((10, 220, 30, 200), (200, 0, 0)),
    (None, (0, 0, 0)),
]

#: (image mode, Draw mode, background, colours). Backgrounds are not black,
#: so a black outline shows; the RGBA one is part transparent; Draw(img,
#: "RGBA") on an RGB image blends.
SETUPS = [
    ("RGB", None, (40, 80, 120), RGB_COLOURS),
    ("RGBA", None, (40, 80, 120, 100), RGB_COLOURS),
    ("RGB", "RGBA", (40, 80, 120), RGB_COLOURS),
    ("L", None, 90, [(230, 0), (200, 60), (None, 0)]),
]

CANVAS = (48, 24)
#: Inside, both corners, past the far edge, and wholly off the canvas; then
#: whole-pixel floats, which is what the scoreboards pass (``(w - tw) // 2``
#: on a measured width). draw.text gives the offsets of (-1.0, -1.0) a
#: sub-pixel start of -0.0 where they go negative; the stamp uses 0.
POSITIONS = [(5, 7), (0, 0), (-3, -2), (44, 20), (-60, -40),
             (5.0, 7), (-1.0, -1.0)]
TEXTS = ["", " ", "21-17", "Q4 2:35", "(12-3)", "/x", "jgy|", "72°"]


def _font(name, size, engine=ImageFont.Layout.BASIC):
    if name == "<default>":
        return ImageFont.load_default()
    return load_truetype(str(FONTS_DIR / name), size, layout_engine=engine)


def _reference(draw, xy, text, font, fill, outline_color=(0, 0, 0),
               offsets=OUTLINE_SQUARE):
    """The loop draw_text_outlined replaced, as every caller wrote it.

    Frozen on purpose: it defines the pixels the stamping path must produce.
    """
    x, y = xy
    for dx, dy in offsets:
        draw.text((x + dx, y + dy), text, font=font, fill=outline_color)
    draw.text((x, y), text, font=font, fill=fill)


def _canvas(mode="RGB", draw_mode=None, background=(40, 80, 120),
            fontmode="1", size=CANVAS):
    image = Image.new(mode, size, background)
    draw = ImageDraw.Draw(image, draw_mode)
    draw.fontmode = fontmode
    return image, draw


def _same_pixels(a, b):
    """Every band of every pixel equal.

    ``alpha_only=False`` matters: on an RGBA image getbbox otherwise looks at
    the alpha band alone and would miss a colour difference.
    """
    assert (a.mode, a.size) == (b.mode, b.size)
    return ImageChops.difference(a, b).getbbox(alpha_only=False) is None


def _count_rasterizations(monkeypatch, font):
    """Record each getmask2 call on ``font``, the one place draw.text and the
    stamping path rasterize. Patched on the instance, so the font's type (which
    the fast path checks) is unchanged."""
    calls = []
    real = font.getmask2

    def getmask2(*args, **kwargs):
        calls.append(args[0] if args else kwargs.get("text"))
        return real(*args, **kwargs)

    monkeypatch.setattr(font, "getmask2", getmask2)
    return calls


def _font_id(case):
    name, size = case
    return f"{name}@{size}"


class TestDrawTextOutlinedMatchesTheLoop:
    """The stamping path against the nine-draw loop, case by case."""

    @pytest.mark.parametrize("fontmode", ["1", "L"])
    @pytest.mark.parametrize("setup", SETUPS,
                             ids=lambda s: f"{s[0]}-draw{s[1] or s[0]}")
    @pytest.mark.parametrize("font_case", FONT_CASES, ids=_font_id)
    def test_same_pixels_from_one_rasterization(self, monkeypatch, font_case,
                                                setup, fontmode):
        font = _font(*font_case)
        calls = _count_rasterizations(monkeypatch, font)
        mode, draw_mode, background, colours = setup
        blank = Image.new(mode, CANVAS, background).tobytes()
        visible = 0
        for fill, outline in colours:
            for xy in POSITIONS:
                for text in TEXTS:
                    for offsets in (OUTLINE_SQUARE, OUTLINE_CROSS):
                        case = (fill, outline, xy, text, len(offsets))
                        expected, ref_draw = _canvas(mode, draw_mode, background, fontmode)
                        _reference(ref_draw, xy, text, font, fill, outline, offsets)
                        got, draw = _canvas(mode, draw_mode, background, fontmode)
                        before = len(calls)
                        draw_text_outlined(draw, xy, text, font, fill, outline, offsets)
                        # Once, not once per offset: a silent fallback to the
                        # loop would still match the pixels and lose the point.
                        assert len(calls) - before == 1, case
                        assert _same_pixels(got, expected), case
                        visible += expected.tobytes() != blank
        # Not vacuous: most of these cases put pixels on the canvas.
        assert visible > len(colours) * len(TEXTS)

    @pytest.mark.skipif(not features.check("raqm"),
                        reason="this Pillow has no libraqm")
    @pytest.mark.parametrize("fontmode", ["1", "L"])
    @pytest.mark.parametrize("font_case", [("PressStart2P-Regular.ttf", 8),
                                           ("4x6-font.ttf", 6)], ids=_font_id)
    def test_the_raqm_layout_engine_too(self, monkeypatch, font_case, fontmode):
        """Core pins Basic (font_layout), but a plugin may load with Raqm."""
        font = _font(*font_case, engine=ImageFont.Layout.RAQM)
        calls = _count_rasterizations(monkeypatch, font)
        for xy in POSITIONS:
            for text in TEXTS:
                expected, ref_draw = _canvas(fontmode=fontmode)
                _reference(ref_draw, xy, text, font, (255, 200, 0))
                got, draw = _canvas(fontmode=fontmode)
                before = len(calls)
                draw_text_outlined(draw, xy, text, font, (255, 200, 0))
                assert len(calls) - before == 1, (xy, text)
                assert _same_pixels(got, expected), (xy, text)

    @pytest.mark.parametrize("mode,background,fill,outline", [
        ("RGB", (40, 80, 120), "white", "black"),
        ("RGB", (40, 80, 120), "#ffc800", "#00000080"),
        ("RGBA", (40, 80, 120, 100), "yellow", "#00000080"),
        ("L", 90, "white", "black"),
        ("RGB", (40, 80, 120), 0xFFC800, 0),
    ])
    @pytest.mark.parametrize("fontmode", ["1", "L"])
    def test_colour_names_and_ints(self, monkeypatch, mode, background, fill,
                                   outline, fontmode):
        """Colours draw.text resolves itself (ImageColor names, packed ints)
        resolve the same way when stamped."""
        font = _font("PressStart2P-Regular.ttf", 10)
        calls = _count_rasterizations(monkeypatch, font)
        for xy in POSITIONS:
            expected, ref_draw = _canvas(mode, None, background, fontmode)
            _reference(ref_draw, xy, "Q4 2:35", font, fill, outline)
            got, draw = _canvas(mode, None, background, fontmode)
            before = len(calls)
            draw_text_outlined(draw, xy, "Q4 2:35", font, fill, outline)
            assert len(calls) - before == 1, xy
            assert _same_pixels(got, expected), xy

    def test_no_offsets_is_just_the_text(self, monkeypatch):
        font = _font("PressStart2P-Regular.ttf", 8)
        calls = _count_rasterizations(monkeypatch, font)
        expected, ref_draw = _canvas()
        ref_draw.text((5, 7), "21-17", font=font, fill=(255, 200, 0))
        got, draw = _canvas()
        before = len(calls)
        draw_text_outlined(draw, (5, 7), "21-17", font, (255, 200, 0), offsets=())
        assert len(calls) - before == 1
        assert _same_pixels(got, expected)

    def test_the_outline_shapes(self):
        assert OUTLINE_SQUARE == ((-1, -1), (-1, 0), (-1, 1), (0, -1),
                                  (0, 1), (1, -1), (1, 0), (1, 1))
        assert OUTLINE_CROSS == ((-1, 0), (1, 0), (0, -1), (0, 1))


class TestDrawTextOutlinedFallsBackToTheLoop:
    """Every case the stamping path is not proven for draws through draw.text,
    nine times, exactly as before."""

    def _assert_loop(self, monkeypatch, font, xy, text, *, mode="RGB",
                     background=(40, 80, 120), fill=(255, 200, 0),
                     outline=(0, 0, 0, 128), offsets=OUTLINE_SQUARE,
                     fontmode="1"):
        calls = _count_rasterizations(monkeypatch, font)
        expected, ref_draw = _canvas(mode, None, background, fontmode)
        _reference(ref_draw, xy, text, font, fill, outline, offsets)
        loop_calls = len(calls)
        got, draw = _canvas(mode, None, background, fontmode)
        draw_text_outlined(draw, xy, text, font, fill, outline, offsets)
        assert len(calls) - loop_calls == loop_calls, "expected the draw.text loop"
        assert _same_pixels(got, expected)

    @pytest.mark.parametrize("xy", [(0.5, 0.5), (2.5, 3.5), (5.0, 7.5),
                                    (-0.5, 0.75), (3, 2.25)])
    @pytest.mark.parametrize("fontmode", ["1", "L"])
    def test_fractional_positions(self, monkeypatch, xy, fontmode):
        # Pillow rasterizes at the sub-pixel start and truncates x + dx, so a
        # mask made for x does not fit x + dx.
        for font_case in (("PressStart2P-Regular.ttf", 10), ("4x6-font.ttf", 6)):
            self._assert_loop(monkeypatch, _font(*font_case), xy, "21-17",
                              fontmode=fontmode)

    @pytest.mark.parametrize("text", ["a\nb", "21\n17", "a\rb"])
    def test_multiline_text(self, monkeypatch, text):
        self._assert_loop(monkeypatch, _font("PressStart2P-Regular.ttf", 8),
                          (5, 2), text)

    def test_bytes_text(self, monkeypatch):
        self._assert_loop(monkeypatch, _font("PressStart2P-Regular.ttf", 8),
                          (5, 7), b"21-17")

    @pytest.mark.parametrize("mode,background,fill,outline", [
        ("1", 0, 1, 0),
        ("P", 0, (255, 0, 0), (0, 0, 255)),
    ])
    def test_image_modes_not_proven(self, monkeypatch, mode, background, fill,
                                    outline):
        self._assert_loop(monkeypatch, _font("PressStart2P-Regular.ttf", 8),
                          (5, 7), "21-17", mode=mode, background=background,
                          fill=fill, outline=outline)

    def test_colour_glyph_fontmode(self, monkeypatch):
        self._assert_loop(monkeypatch, _font("PressStart2P-Regular.ttf", 8),
                          (5, 7), "21-17", mode="RGBA",
                          background=(40, 80, 120, 100), fontmode="RGBA")

    @pytest.mark.parametrize("offsets", [((0.5, 0), (-1, 0)), [[-1, 0], [1, 0.0]]])
    def test_offsets_that_are_not_whole_pixels(self, monkeypatch, offsets):
        self._assert_loop(monkeypatch, _font("PressStart2P-Regular.ttf", 8),
                          (5, 7), "21-17", offsets=offsets)

    def test_a_non_freetype_font(self):
        font = ImageFont.load_default_imagefont()
        assert not isinstance(font, ImageFont.FreeTypeFont)
        expected, ref_draw = _canvas()
        _reference(ref_draw, (5, 7), "21-17", font, (255, 200, 0), (0, 0, 0, 128))
        got, draw = _canvas()
        draw_text_outlined(draw, (5, 7), "21-17", font, (255, 200, 0), (0, 0, 0, 128))
        assert _same_pixels(got, expected)
        assert expected.tobytes() != _canvas()[0].tobytes()

    def test_a_transposed_font(self, monkeypatch):
        base = _font("PressStart2P-Regular.ttf", 8)
        font = ImageFont.TransposedFont(base, Image.Transpose.ROTATE_90)
        calls = _count_rasterizations(monkeypatch, base)
        expected, ref_draw = _canvas()
        _reference(ref_draw, (5, 2), "21", font, (255, 200, 0))
        loop_calls = len(calls)
        got, draw = _canvas()
        draw_text_outlined(draw, (5, 2), "21", font, (255, 200, 0))
        assert len(calls) - loop_calls == loop_calls
        assert _same_pixels(got, expected)

    def test_a_draw_subclass_keeps_its_text_override(self):
        seen = []

        class Recording(ImageDraw.ImageDraw):
            def text(self, xy, text, *args, **kwargs):
                seen.append(xy)
                return super().text(xy, text, *args, **kwargs)

        font = _font("PressStart2P-Regular.ttf", 8)
        draw = Recording(Image.new("RGB", CANVAS))
        draw_text_outlined(draw, (5, 7), "21-17", font, (255, 200, 0))
        assert seen == [(5 + dx, 7 + dy) for dx, dy in OUTLINE_SQUARE] + [(5, 7)]

    @pytest.mark.parametrize("where", ["class", "instance"])
    def test_a_replaced_draw_text_still_sees_every_call(self, monkeypatch, where):
        """Plugin tests record the strings drawn by swapping ImageDraw.text
        (ledmatrix-flights' overhead-card tests do it on the class)."""
        font = _font("PressStart2P-Regular.ttf", 8)
        expected, ref_draw = _canvas()
        _reference(ref_draw, (5, 7), "21-17", font, (255, 200, 0), (0, 0, 0, 128))
        got, draw = _canvas()
        seen = []
        pillow_text = ImageDraw.ImageDraw.text

        def recording_text(self, xy, text, *args, **kwargs):
            seen.append((xy, text))
            return pillow_text(self, xy, text, *args, **kwargs)

        if where == "class":
            monkeypatch.setattr(ImageDraw.ImageDraw, "text", recording_text)
        else:
            draw.text = recording_text.__get__(draw)
        draw_text_outlined(draw, (5, 7), "21-17", font, (255, 200, 0), (0, 0, 0, 128))
        assert seen == ([((5 + dx, 7 + dy), "21-17") for dx, dy in OUTLINE_SQUARE]
                        + [((5, 7), "21-17")])
        assert _same_pixels(got, expected)

    def test_a_duck_typed_draw_gets_the_same_calls(self):
        """Test doubles that record draw.text (the scoreboard tests use them)."""
        class Recorder:
            fontmode = "1"

            def __init__(self):
                self.calls = []

            def text(self, position, text, font=None, fill=None):
                self.calls.append((position, text, font, fill))

        draw = Recorder()
        draw_text_outlined(draw, (5, 7), "21", None, (1, 2, 3), (9, 9, 9),
                           OUTLINE_CROSS)
        assert draw.calls == [((4, 7), "21", None, (9, 9, 9)),
                              ((6, 7), "21", None, (9, 9, 9)),
                              ((5, 6), "21", None, (9, 9, 9)),
                              ((5, 8), "21", None, (9, 9, 9)),
                              ((5, 7), "21", None, (1, 2, 3))]

    def test_offsets_may_be_a_generator(self):
        font = _font("PressStart2P-Regular.ttf", 8)
        expected, ref_draw = _canvas()
        _reference(ref_draw, (5, 7), "21", font, (255, 200, 0), (0, 0, 0, 128))
        got, draw = _canvas()
        draw_text_outlined(draw, (5, 7), "21", font, (255, 200, 0), (0, 0, 0, 128),
                           (o for o in OUTLINE_SQUARE))
        assert _same_pixels(got, expected)


class TestDrawTextOutlinedErrors:
    """A bad argument fails where the loop failed, with the same pixels drawn."""

    @pytest.mark.parametrize("fill,outline,error", [
        ("nocolor", (0, 0, 0), ValueError),        # after the outline is drawn
        ([1, 2, 3], (0, 0, 0), TypeError),         # after the outline is drawn
        ((255, 200, 0), "nocolor", ValueError),    # before anything is drawn
    ])
    def test_bad_colours(self, fill, outline, error):
        font = _font("PressStart2P-Regular.ttf", 8)
        expected, ref_draw = _canvas()
        with pytest.raises(error):
            _reference(ref_draw, (5, 7), "21", font, fill, outline)
        got, draw = _canvas()
        with pytest.raises(error):
            draw_text_outlined(draw, (5, 7), "21", font, fill, outline)
        assert _same_pixels(got, expected)

    def test_a_position_that_is_not_a_pair(self):
        font = _font("PressStart2P-Regular.ttf", 8)
        with pytest.raises(ValueError):
            draw_text_outlined(_canvas()[1], (1, 2, 3), "21", font, (255, 255, 255))

    @pytest.mark.parametrize("x", [2 ** 31, 2.0 ** 31, 2.0 ** 60])
    def test_a_position_pillow_cannot_draw_at(self, x):
        font = _font("PressStart2P-Regular.ttf", 8)
        expected, ref_draw = _canvas()
        with pytest.raises(Exception) as loop_error:
            _reference(ref_draw, (x, 0), "21", font, (255, 200, 0))
        got, draw = _canvas()
        with pytest.raises(Exception) as error:
            draw_text_outlined(draw, (x, 0), "21", font, (255, 200, 0))
        assert type(error.value) is type(loop_error.value)
        assert _same_pixels(got, expected)


class _DrawProxy:
    """Stands in for ``ImageDraw.draw`` (the C drawing object) and fails
    draw_bitmap on the calls listed, as a changed Pillow internal might."""

    def __init__(self, real, fail_on):
        self._real = real
        self._fail_on = set(fail_on)
        self.bitmaps = 0

    def draw_bitmap(self, *args):
        self.bitmaps += 1
        if self.bitmaps in self._fail_on:
            raise TypeError("simulated: draw_bitmap signature changed")
        return self._real.draw_bitmap(*args)

    def __getattr__(self, name):
        return getattr(self._real, name)


class TestDrawTextOutlinedGuards:
    """The stamping path leans on Pillow internals; if one changes, the loop
    runs instead -- but never after a stamp has landed, or a translucent
    outline would be drawn twice."""

    def test_a_failing_rasterization_call_takes_the_loop(self, monkeypatch):
        font = _font("PressStart2P-Regular.ttf", 8)
        real = font.getmask2
        state = {"calls": 0}

        def getmask2(*args, **kwargs):
            state["calls"] += 1
            if state["calls"] == 1:
                raise TypeError("simulated: getmask2 signature changed")
            return real(*args, **kwargs)

        expected, ref_draw = _canvas()
        _reference(ref_draw, (5, 7), "21", font, (255, 200, 0), (0, 0, 0, 128))
        monkeypatch.setattr(font, "getmask2", getmask2)
        got, draw = _canvas()
        draw_text_outlined(draw, (5, 7), "21", font, (255, 200, 0), (0, 0, 0, 128))
        assert state["calls"] == 1 + 9
        assert _same_pixels(got, expected)

    def test_a_failing_first_stamp_takes_the_loop(self):
        font = _font("PressStart2P-Regular.ttf", 8)
        expected, ref_draw = _canvas()
        _reference(ref_draw, (5, 7), "21", font, (255, 200, 0), (0, 0, 0, 128))
        got, draw = _canvas()
        draw.draw = _DrawProxy(draw.draw, fail_on={1})
        draw_text_outlined(draw, (5, 7), "21", font, (255, 200, 0), (0, 0, 0, 128))
        assert draw.draw.bitmaps == 1 + 9
        assert _same_pixels(got, expected)

    def test_a_failure_after_a_stamp_is_raised_not_redrawn(self):
        font = _font("PressStart2P-Regular.ttf", 8)
        _, draw = _canvas()
        draw.draw = _DrawProxy(draw.draw, fail_on={2})
        with pytest.raises(TypeError):
            draw_text_outlined(draw, (5, 7), "21", font, (255, 200, 0), (0, 0, 0, 128))
        assert draw.draw.bitmaps == 2

    def test_the_guarded_internals_exist_on_this_pillow(self):
        """If this fails, every outlined draw is silently back on the slow loop."""
        _, draw = _canvas()
        assert callable(getattr(draw, "_getink", None))
        assert callable(getattr(draw.draw, "draw_bitmap", None))
        assert text_helper_module._can_stamp(
            draw, 0, 0, "21", _font("PressStart2P-Regular.ttf", 8), OUTLINE_SQUARE)


class TestTheOutlinedDrawSitesUseIt:
    """The two core draw sites: same pixels as their old loops, one
    rasterization each."""

    # A whole-pixel float x is what the scorebug's centring arithmetic
    # (``(width - draw.textlength(...)) // 2``) actually passes.
    @pytest.mark.parametrize("position", [(2, 1), (2.0, 1)])
    def test_the_scoreboard_mixin(self, monkeypatch, position):
        from src.common.sports_shared import SportsCoreSharedMixin

        font = _font("PressStart2P-Regular.ttf", 10)
        calls = _count_rasterizations(monkeypatch, font)
        # The mixin forces fontmode "1" however the draw arrives.
        expected, ref_draw = _canvas(fontmode="1")
        _reference(ref_draw, position, "6", font, (255, 200, 0))
        got, draw = _canvas(fontmode="L")
        before = len(calls)
        # Unbound with self=None, as the plugins' anti-aliasing tests call it:
        # an explicit fill reads nothing from the host.
        SportsCoreSharedMixin._draw_text_with_outline(
            None, draw, "6", position, font, fill=(255, 200, 0))
        assert draw.fontmode == "1"
        assert len(calls) - before == 1
        assert _same_pixels(got, expected)

    def test_the_scoreboard_mixin_by_element(self, monkeypatch):
        import logging
        from src.common.sports_shared import SportsCoreSharedMixin

        class Host(SportsCoreSharedMixin):
            config = {"customization": {"score_text": {"text_color": [1, 2, 3]}}}
            fonts = {}
            logger = logging.getLogger("test_text_helper")

        font = _font("4x6-font.ttf", 7)
        calls = _count_rasterizations(monkeypatch, font)
        expected, ref_draw = _canvas()
        _reference(ref_draw, (3, 4), "21-17", font, (1, 2, 3))
        got, draw = _canvas()
        before = len(calls)
        Host()._draw_text_with_outline(draw, "21-17", (3, 4), font,
                                       element="score_text")
        assert len(calls) - before == 1
        assert _same_pixels(got, expected)

    @pytest.mark.parametrize("width", [-1, 0, 1, 2])
    def test_text_helper_draw_text_with_outline(self, monkeypatch, width):
        font = _font("PressStart2P-Regular.ttf", 8)
        calls = _count_rasterizations(monkeypatch, font)
        expected, ref_draw = _canvas()
        # TextHelper's own loop before it was routed: every offset up to
        # outline_width away on each axis, dx outer, dy inner, centre skipped.
        for dx in range(-width, width + 1):
            for dy in range(-width, width + 1):
                if dx != 0 or dy != 0:
                    ref_draw.text((5 + dx, 7 + dy), "Q4", font=font,
                                  fill=(0, 0, 0, 128))
        ref_draw.text((5, 7), "Q4", font=font, fill=(255, 255, 255))
        got, draw = _canvas()
        before = len(calls)
        TextHelper().draw_text_with_outline(draw, "Q4", (5, 7), font,
                                            outline_color=(0, 0, 0, 128),
                                            outline_width=width)
        assert len(calls) - before == 1
        assert _same_pixels(got, expected)
