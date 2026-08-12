"""Tests the startup screen that shows while plugins fetch their first data.

That screen is on the panel for the whole initial-update window, and on a
headless Pi it is the only place the device's address appears without going
looking for it -- so it now carries the address as well as "Initializing".

Two things have to hold. It must fit every supported panel: the old fixed
8px PressStart2P drew "Initializing" 96px wide at x=10, which ran off the
side of a 64px panel before an address was ever added. And the lookup must be
cheap, because this runs on the startup path that the rest of this change
exists to shorten.
"""

import os
import time

from PIL import Image, ImageDraw, ImageFont
import pytest

os.environ.setdefault("EMULATOR", "true")

from src.display_manager import DisplayManager  # noqa: E402

SIZES = [(64, 32), (128, 32), (128, 64), (256, 32), (512, 64)]


class FakeMatrix:
    def __init__(self, width, height):
        self.width, self.height = width, height


def _manager(width, height):
    dm = DisplayManager.__new__(DisplayManager)
    dm.image = Image.new('RGB', (width, height))
    dm.draw = ImageDraw.Draw(dm.image)
    dm.matrix = FakeMatrix(width, height)
    dm.font = ImageFont.truetype('assets/fonts/PressStart2P-Regular.ttf', 8)
    return dm


def _layout(dm, lines):
    """The geometry _draw_startup_banner uses."""
    font = dm._fitting_font(lines, dm.matrix.width - 2)
    line_height = dm.draw.textbbox((0, 0), "Ag", font=font)[3] + 1
    top = max(1, (dm.matrix.height - line_height * len(lines)) // 2)
    widths = [dm.draw.textlength(t, font=font) for t in lines]
    return font, widths, top, top + line_height * len(lines)


def _render_over_pattern(width, height, lines):
    """Draw the test pattern, then the banner over it, as startup does."""
    dm = _manager(width, height)
    dm.draw.rectangle([0, 0, width - 1, height - 1], outline=(255, 0, 0))
    dm.draw.line([0, 0, width - 1, height - 1], fill=(0, 255, 0))
    dm._draw_startup_banner(lines, width, height)
    return dm


class TestTheAddressLookup:
    def test_it_never_reports_loopback(self):
        # A loopback address on the panel would be actively misleading -- it is
        # not something anyone can browse to.
        ip = DisplayManager._local_ip()
        assert ip is None or not ip.startswith("127."), ip

    def test_it_looks_like_an_address_when_there_is_one(self):
        ip = DisplayManager._local_ip()
        if ip is None:
            pytest.skip("host has no routable address")
        parts = ip.split(".")
        assert len(parts) == 4 and all(p.isdigit() for p in parts), ip

    def test_it_is_cheap_enough_for_the_startup_path(self):
        DisplayManager._local_ip()          # warm anything cacheable
        started = time.perf_counter()
        for _ in range(20):
            DisplayManager._local_ip()
        per_call = (time.perf_counter() - started) / 20
        # `hostname -I` with its 2s timeout, which the web launcher uses, would
        # be thousands of times this.
        assert per_call < 0.05, "%.1f ms per call" % (per_call * 1000)

    def test_it_returns_none_rather_than_raising(self, monkeypatch):
        import src.display_manager as mod

        def no_network(*a, **k):
            raise OSError("network is unreachable")

        monkeypatch.setattr(mod.socket, "socket", no_network)
        assert DisplayManager._local_ip() is None


class TestItFitsEveryPanel:
    @pytest.mark.parametrize("width,height", SIZES)
    def test_both_lines_fit_with_an_address(self, width, height):
        dm = _manager(width, height)
        _font, widths, top, bottom = _layout(dm, ["Initializing", "255.255.255.255"])
        assert all(w <= width - 2 for w in widths), (width, widths)
        assert bottom <= height and top >= 0, (top, bottom, height)

    @pytest.mark.parametrize("width,height", SIZES)
    def test_it_still_fits_with_no_address(self, width, height):
        dm = _manager(width, height)
        _font, widths, top, bottom = _layout(dm, ["Initializing"])
        assert all(w <= width - 2 for w in widths), (width, widths)
        assert bottom <= height, (bottom, height)

    def test_the_smallest_panel_drops_to_a_narrower_font(self):
        # The regression this guards: PressStart2P at 8px is 96px wide for
        # "Initializing", which does not fit 64px however it is positioned.
        dm = _manager(64, 32)
        font, widths, _t, _b = _layout(dm, ["Initializing", "10.0.20.104"])
        assert font is not dm.font, "kept a font that cannot fit"
        assert max(widths) <= 62, widths

    def test_a_roomy_panel_keeps_the_larger_font(self):
        dm = _manager(256, 32)
        font, _w, _t, _b = _layout(dm, ["Initializing", "10.0.20.104"])
        assert font is dm.font, "needlessly shrank on a panel with room"


class TestPlacement:
    @pytest.mark.parametrize("width,height", SIZES)
    def test_the_lines_are_centred(self, width, height):
        dm = _manager(width, height)
        lines = ["Initializing", "10.0.20.104"]
        _font, widths, _t, _b = _layout(dm, lines)
        for w in widths:
            left = max(0, (width - w) // 2)
            assert abs((left + (left + w)) - width) <= 2, (left, w, width)

    def test_the_address_sits_under_the_word(self):
        dm = _manager(128, 64)
        font, _w, top, bottom = _layout(dm, ["Initializing", "10.0.20.104"])
        line_height = dm.draw.textbbox((0, 0), "Ag", font=font)[3] + 1
        assert bottom - top == line_height * 2


class TestItIsActuallyReadable:
    """The point of the address is that someone can read it off the wall."""

    @pytest.mark.parametrize("width,height", SIZES)
    def test_the_diagonal_does_not_cross_the_text(self, width, height):
        lines = ["Initializing", "10.0.20.104"]
        dm = _render_over_pattern(width, height, lines)
        _font, widths, top, bottom = _layout(dm, lines)
        # textlength returns a float, so these must be floored before they
        # can index pixels.
        block_width = int(max(widths))
        left = int(max(0, (width - block_width) // 2))

        px = dm.image.load()
        green = 0
        for y in range(int(top), min(int(bottom), height)):
            for x in range(left, min(left + block_width, width)):
                r, g, b = px[x, y]
                if g > 128 and r < 128 and b < 128:
                    green += 1
        assert green == 0, "%d green pixels behind the text at %dx%d" % (
            green, width, height)

    @pytest.mark.parametrize("width,height", SIZES)
    def test_the_text_stays_pure_blue(self, width, height):
        # Not a style choice. The pattern lights one channel per element --
        # red border, green diagonal, blue text -- so a glance says whether
        # led_rgb_sequence is right: wire it BGR and the border comes up blue
        # and this text red. White text would light all three and destroy the
        # only blue reference on the screen.
        dm = _render_over_pattern(width, height, ["Initializing", "10.0.20.104"])
        px = dm.image.load()
        blue = sum(1 for y in range(height) for x in range(width)
                   if px[x, y] == (0, 0, 255))
        assert blue > 20, "only %d blue pixels at %dx%d" % (blue, width, height)
        white = sum(1 for y in range(height) for x in range(width)
                    if px[x, y] == (255, 255, 255))
        assert white == 0, "%d white pixels would muddy the channel check" % white

    def test_each_element_lights_one_channel(self):
        # The whole point of the pattern: three pure primaries on screen.
        dm = _render_over_pattern(128, 64, ["Initializing", "10.0.20.104"])
        seen = set(dm.image.getdata())
        assert (255, 0, 0) in seen, "no pure red border"
        assert (0, 255, 0) in seen, "no pure green diagonal"
        assert (0, 0, 255) in seen, "no pure blue text"

    def test_nothing_is_drawn_for_no_lines(self):
        dm = _manager(128, 64)
        before = dm.image.tobytes()
        dm._draw_startup_banner([], 128, 64)
        assert dm.image.tobytes() == before
