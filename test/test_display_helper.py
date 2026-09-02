"""Tests for src/common/display_helper.py (DisplayHelper).

Pure-PIL tests, no hardware or mocks required. Pixel assertions rely on
getbbox()/getpixel() rather than exact text pixel counts, because the
default-font metrics vary across Pillow versions.

These tests pin the FIXED behaviors on this branch:
- draw_error_message / draw_no_data_message return a rendered image
  (they previously crashed with AttributeError),
- draw_scorebug_layout draws period/status/clock as one combined top
  line (previously overprinted at the same y),
- draw_ticker_layout draws at x=0 (previously started at
  x=display_width, i.e. entirely off-canvas -> blank frames).
"""

from PIL import Image, ImageDraw, ImageFont

from src.common.display_helper import DisplayHelper


def default_font():
    return ImageFont.load_default()


def make_helper(width=128, height=32):
    return DisplayHelper(width, height)


class TestCreateBaseImage:
    def test_default_is_black_rgb_display_sized(self):
        helper = make_helper()
        img = helper.create_base_image()
        assert img.size == (128, 32)
        assert img.mode == 'RGB'
        assert img.getpixel((0, 0)) == (0, 0, 0)
        assert img.getpixel((127, 31)) == (0, 0, 0)
        # Entirely black -> no bounding box in luminance
        assert img.convert('L').getbbox() is None

    def test_custom_background_color(self):
        helper = make_helper()
        img = helper.create_base_image(background_color=(10, 20, 30))
        assert img.getpixel((0, 0)) == (10, 20, 30)
        assert img.getpixel((64, 16)) == (10, 20, 30)

    def test_mode_rgba_is_honored(self):
        helper = make_helper()
        img = helper.create_base_image(mode='RGBA')
        assert img.mode == 'RGBA'
        assert img.size == (128, 32)


class TestCreateOverlay:
    def test_overlay_is_transparent_rgba(self):
        helper = make_helper()
        overlay = helper.create_overlay()
        assert overlay.mode == 'RGBA'
        assert overlay.size == (128, 32)
        assert overlay.getpixel((0, 0)) == (0, 0, 0, 0)
        assert overlay.getpixel((127, 31)) == (0, 0, 0, 0)


class TestCompositeImages:
    def test_rgb_inputs_are_upconverted_and_result_is_rgba(self):
        helper = make_helper()
        base = Image.new('RGB', (128, 32), (0, 0, 0))
        overlay = Image.new('RGB', (128, 32), (255, 0, 0))
        result = helper.composite_images(base, overlay)
        assert result.mode == 'RGBA'
        assert result.size == base.size
        # RGB->RGBA conversion yields a fully opaque overlay
        assert result.getpixel((0, 0)) == (255, 0, 0, 255)

    def test_transparent_overlay_leaves_base_visible(self):
        helper = make_helper()
        base = Image.new('RGB', (128, 32), (5, 6, 7))
        overlay = helper.create_overlay()
        result = helper.composite_images(base, overlay)
        assert result.mode == 'RGBA'
        assert result.getpixel((64, 16)) == (5, 6, 7, 255)


class TestScorebugLayout:
    def test_full_game_data_renders(self):
        helper = make_helper()
        font = default_font()
        fonts = {'time': font, 'status': font, 'score': font, 'team': font}
        game_data = {
            'home_score': 3, 'away_score': 2,
            'home_abbr': 'NYY', 'away_abbr': 'BOS',
            'status_text': 'LIVE', 'period_text': 'T9', 'clock': '2:30',
        }
        img = helper.draw_scorebug_layout(game_data, fonts)
        assert img.mode == 'RGB'
        assert img.size == (128, 32)
        assert img.convert('L').getbbox() is not None

    def test_empty_game_data_uses_defaults_without_raising(self):
        helper = make_helper()
        font = default_font()
        fonts = {'time': font, 'status': font, 'score': font, 'team': font}
        img = helper.draw_scorebug_layout({}, fonts)
        assert img.mode == 'RGB'
        assert img.size == (128, 32)
        # Defaults '0'/'HOME'/'AWAY' actually render something
        assert img.convert('L').getbbox() is not None

    def test_empty_fonts_dict_falls_back_to_default_font(self):
        # Pin: fonts={} must not raise — PIL falls back to the default
        # font when font=None is passed through.
        helper = make_helper()
        img = helper.draw_scorebug_layout(
            {'status_text': 'FINAL', 'period_text': 'Q4', 'clock': '0:00'}, {})
        assert img.size == (128, 32)
        assert img.convert('L').getbbox() is not None

    def test_top_line_is_one_combined_centered_draw(self):
        # FIXED behavior: period/status/clock are joined into a single
        # top line drawn once at y=1 instead of three overprinted draws.
        helper = make_helper()
        calls = []
        original = helper._draw_centered_text

        def spy(draw, text, font, y_position):
            calls.append({'text': text, 'y_position': y_position})
            original(draw, text, font, y_position)

        helper._draw_centered_text = spy
        font = default_font()
        fonts = {'time': font, 'status': font, 'score': font, 'team': font}
        helper.draw_scorebug_layout(
            {'period_text': 'Q4', 'status_text': 'LIVE', 'clock': '2:30'},
            fonts)

        top_calls = [c for c in calls if c['y_position'] == 1]
        assert len(top_calls) == 1
        text = top_calls[0]['text']
        assert 'Q4' in text
        assert 'LIVE' in text
        assert '2:30' in text

    def test_no_top_line_when_all_parts_empty(self):
        helper = make_helper()
        calls = []
        original = helper._draw_centered_text

        def spy(draw, text, font, y_position):
            calls.append(y_position)
            original(draw, text, font, y_position)

        helper._draw_centered_text = spy
        font = default_font()
        helper.draw_scorebug_layout({}, {'score': font, 'team': font})
        assert 1 not in calls  # no combined top line drawn

    def test_logo_positions_bleed_off_edges(self):
        # Home logo pastes at x = width - logo.width + 10 (right edge,
        # bleeding off-screen right); away at x = -10 (bleeding left).
        helper = make_helper()
        home_logo = Image.new('RGBA', (20, 20), (0, 0, 255, 255))   # blue
        away_logo = Image.new('RGBA', (20, 20), (255, 0, 0, 255))   # red
        # Empty abbrs/status so text can't land on the probed pixels.
        game_data = {'home_abbr': '', 'away_abbr': ''}
        font = default_font()
        img = helper.draw_scorebug_layout(game_data, {'score': font},
                                          home_logo=home_logo,
                                          away_logo=away_logo)
        # center_y = 16; logos span y 6..25 -> probe y=16 at both edges.
        assert img.getpixel((0, 16)) == (255, 0, 0)     # away (left edge)
        assert img.getpixel((127, 16)) == (0, 0, 255)   # home (right edge)
        # And the off-screen parts are truly clipped: image is still 128 wide
        assert img.size == (128, 32)


class TestTickerLayout:
    def test_frame_is_not_blank(self):
        # FIXED behavior: text now starts at x=0. Previously it was drawn
        # at x=display_width, entirely off-canvas, so frames were blank.
        helper = make_helper()
        img = helper.draw_ticker_layout('HELLO WORLD', default_font())
        assert img.size == (128, 32)
        assert img.mode == 'RGB'
        assert img.convert('L').getbbox() is not None

    def test_text_starts_at_left_edge(self):
        helper = make_helper()
        img = helper.draw_ticker_layout('HELLO', default_font())
        bbox = img.convert('L').getbbox()
        assert bbox is not None
        # Text is positioned at x=0 (outline extends 1px left, clipped),
        # so ink begins hugging the left edge. Allow a couple of pixels of
        # slack for font-dependent left-side bearing.
        assert bbox[0] <= 2

    def test_scroll_speed_does_not_affect_frame(self):
        # Pin: scroll_speed is accepted for API compatibility only.
        helper = make_helper()
        font = default_font()
        img1 = helper.draw_ticker_layout('SCROLLING', font, scroll_speed=1)
        img5 = helper.draw_ticker_layout('SCROLLING', font, scroll_speed=5)
        assert img1.tobytes() == img5.tobytes()

    def test_custom_colors(self):
        helper = make_helper()
        img = helper.draw_ticker_layout('X', default_font(),
                                        background_color=(0, 0, 40),
                                        text_color=(0, 255, 0))
        assert img.getpixel((127, 0)) == (0, 0, 40)  # background corner
        colors = {img.getpixel((x, y))
                  for x in range(img.width) for y in range(img.height)}
        # Text color appears somewhere (anti-aliasing may blend it, so
        # check for a green-dominant pixel rather than the exact color).
        assert any(g > 150 and r < 100 for (r, g, b) in colors)


class TestCenteredText:
    def test_renders_centered_text_on_background(self):
        helper = make_helper()
        img = helper.draw_centered_text('HI', default_font(),
                                        background_color=(0, 0, 60),
                                        text_color=(255, 255, 0))
        assert img.size == (128, 32)
        assert img.convert('L').getbbox() is not None
        # Corners stay pure background
        assert img.getpixel((0, 0)) == (0, 0, 60)
        assert img.getpixel((127, 0)) == (0, 0, 60)
        assert img.getpixel((0, 31)) == (0, 0, 60)
        assert img.getpixel((127, 31)) == (0, 0, 60)


class TestErrorAndNoDataMessages:
    def test_draw_error_message_returns_rendered_image(self):
        # FIXED behavior: used to crash with AttributeError; now returns
        # a rendered image on a dark red background.
        helper = make_helper()
        img = helper.draw_error_message('Boom')
        assert img.size == (128, 32)
        assert img.mode == 'RGB'
        assert img.convert('L').getbbox() is not None
        assert img.getpixel((0, 0)) == (50, 0, 0)  # dark red background

    def test_draw_error_message_default_text(self):
        helper = make_helper()
        img = helper.draw_error_message()
        assert img.size == (128, 32)
        assert img.getpixel((127, 31)) == (50, 0, 0)

    def test_draw_no_data_message_returns_rendered_image(self):
        helper = make_helper()
        img = helper.draw_no_data_message()
        assert img.size == (128, 32)
        assert img.mode == 'RGB'
        assert img.convert('L').getbbox() is not None
        assert img.getpixel((0, 0)) == (0, 0, 0)  # black background


class TestDrawTextWithOutline:
    def test_fill_color_appears_in_output(self):
        helper = make_helper()
        img = Image.new('RGB', (40, 20), (0, 0, 255))
        draw = ImageDraw.Draw(img)
        helper._draw_text_with_outline(draw, 'X', (5, 2), default_font(),
                                       fill=(255, 0, 0))
        pixels = {img.getpixel((x, y))
                  for x in range(img.width) for y in range(img.height)}
        # Anti-aliased fonts blend edge pixels, so look for red-dominant
        # (fill) and near-black (outline) pixels rather than exact colors.
        assert any(r > 150 and g < 50 for (r, g, b) in pixels)   # fill
        assert any(max(p) < 80 for p in pixels)                  # outline

    def test_default_fill_is_white(self):
        helper = make_helper()
        img = Image.new('RGB', (40, 20), (0, 0, 255))
        draw = ImageDraw.Draw(img)
        helper._draw_text_with_outline(draw, 'X', (5, 2), default_font())
        pixels = {img.getpixel((x, y))
                  for x in range(img.width) for y in range(img.height)}
        # White-dominant pixel present (exact white may be anti-aliased)
        assert any(r > 200 and g > 200 for (r, g, b) in pixels)


class TestOrientationAndDimensions:
    def test_landscape_display(self):
        helper = DisplayHelper(128, 32)
        assert helper.is_landscape() is True
        assert helper.is_portrait() is False

    def test_portrait_display(self):
        helper = DisplayHelper(32, 128)
        assert helper.is_portrait() is True
        assert helper.is_landscape() is False

    def test_square_display_is_neither(self):
        # Pin: a square display is neither portrait nor landscape.
        helper = DisplayHelper(64, 64)
        assert helper.is_portrait() is False
        assert helper.is_landscape() is False

    def test_get_center_position(self):
        assert DisplayHelper(128, 32).get_center_position() == (64, 16)

    def test_get_center_position_floors_odd_dimensions(self):
        assert DisplayHelper(65, 33).get_center_position() == (32, 16)

    def test_get_display_dimensions(self):
        assert DisplayHelper(128, 32).get_display_dimensions() == (128, 32)
        assert DisplayHelper(64, 64).get_display_dimensions() == (64, 64)
