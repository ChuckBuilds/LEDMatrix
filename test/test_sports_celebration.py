"""src.common.sports_celebration: the palette, the takeover, and the host contract.

Ported from the scoreboards' own celebration tests (football's
test_score_celebration.py, hockey's and soccer's test_goal_celebration.py),
which drive the same code through a plugin's SportsLive. Here the host is a
stub carrying exactly the documented contract, and the crests are drawn by the
test, so every input is fixed. Pixel-exact goldens of every plugin's takeover
live in ledmatrix-plugins (scripts/test_celebration_renders.py).
"""

import ast
import logging
from pathlib import Path
from unittest import mock

import pytest
from PIL import Image, ImageChops, ImageDraw, ImageFont

from src.common import sports_celebration
from src.common.sports_celebration import (
    SportsCelebrationMixin,
    cap_luminance,
    lift_color,
    logo_palette,
    mix_color,
    rgb_luminance,
    rgb_saturation,
    scale_color,
)

FONTS = Path(__file__).resolve().parents[1] / "assets" / "fonts"
SIZES = [(64, 32), (128, 32), (64, 64), (96, 48),
         (128, 64), (256, 32), (128, 96), (256, 128)]


def crest(body, band, size=64):
    """A shield in ``body`` with a horizontal band in ``band``."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    s = size / 64
    draw.polygon([(6 * s, 4 * s), (58 * s, 4 * s), (58 * s, 34 * s),
                  (32 * s, 60 * s), (6 * s, 34 * s)], fill=body + (255,))
    draw.rectangle([(6 * s, 22 * s), (58 * s, 32 * s)], fill=band + (255,))
    return img


CRESTS = {
    "RED": crest((200, 16, 46), (255, 255, 255)),
    "NAV": crest((12, 35, 64), (255, 184, 28)),
    "SIL": crest((165, 172, 175), (0, 0, 0)),
}


class _DisplayManager:
    def __init__(self, width, height):
        self.width, self.height = width, height
        self.image = Image.new("RGB", (width, height))
        self.updates = 0

    def clear(self):
        self.image = Image.new("RGB", (self.width, self.height))

    def update_display(self):
        self.updates += 1


class Host(SportsCelebrationMixin):
    """The documented contract, and not one attribute more."""

    def __init__(self, width=128, height=32, **knobs):
        self.display_manager = _DisplayManager(width, height)
        self.display_width, self.display_height = width, height
        press = str(FONTS / "PressStart2P-Regular.ttf")
        self.fonts = {
            "time": ImageFont.truetype(press, 8),
            "status": ImageFont.truetype(str(FONTS / "4x6-font.ttf"), 6),
            "score": ImageFont.truetype(press, 16 if height >= 48 else 10),
        }
        self.logger = logging.getLogger("test.sports_celebration")
        for name, value in knobs.items():
            setattr(self, name, value)

    def _load_and_resize_logo(self, team_id, abbr, logo_path, logo_url):
        logo = CRESTS.get(abbr)
        if logo is None:
            return None
        logo = logo.copy()
        logo.thumbnail((self.display_height, self.display_height), Image.Resampling.LANCZOS)
        return logo

    def _draw_text_with_outline(self, draw, text, position, font,
                                fill=(255, 255, 255), outline_color=(0, 0, 0)):
        x, y = position
        for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
            draw.text((x + dx, y + dy), text, font=font, fill=outline_color)
        draw.text((x, y), text, font=font, fill=fill)


def celebration(scorer="RED", other="NAV", side="away", kind="score", motif="score"):
    away, home = (scorer, other) if side == "away" else (other, scorer)
    return {
        "kind": kind, "motif": motif,
        "game": {"id": "401", "away_abbr": away, "home_abbr": home},
        "scored_side": side, "team_abbr": scorer,
        "away_score": 3, "home_score": 2, "started_at": 1000.0,
        "phrase": f"{scorer} WINS!" if kind == "win" else f"{scorer} SCORES!",
    }


def render(host=None, elapsed=2.0, **kwargs):
    host = host or Host()
    with mock.patch("time.time", return_value=1000.0 + elapsed):
        host._draw_celebration_layout(celebration(**kwargs), force_clear=True)
    return host.display_manager.image


def brightest(img, box=None):
    region = img.crop(box) if box else img
    return max(region.convert("RGB").getextrema()[i][1] for i in range(3))


# ---------------------------------------------------------------------------
# Colour helpers
# ---------------------------------------------------------------------------

class TestColourHelpers:
    def test_mix_is_clamped_to_the_two_ends(self):
        assert mix_color((0, 0, 0), (200, 100, 50), 0.5) == (100, 50, 25)
        assert mix_color((0, 0, 0), (200, 100, 50), 2) == (200, 100, 50)
        assert mix_color((0, 0, 0), (200, 100, 50), -1) == (0, 0, 0)

    def test_scale_is_clamped_to_the_panel(self):
        assert scale_color((200, 100, 0), 2) == (255, 200, 0)

    def test_saturation_of_black_is_zero(self):
        assert rgb_saturation((0, 0, 0)) == 0.0

    def test_lifting_a_colour_keeps_its_hue(self):
        # Scaling channels turns Baltimore's navy-purple magenta; HSV does not.
        lifted = lift_color((39, 15, 98))
        assert rgb_luminance(lifted) >= 100
        assert lifted[2] > lifted[0] > lifted[1]

    def test_a_colour_that_already_reads_is_left_alone(self):
        assert lift_color((255, 208, 56)) == (255, 208, 56)

    def test_a_grey_is_just_made_bright(self):
        lifted = lift_color((40, 40, 40))
        assert lifted[0] == lifted[1] == lifted[2] and rgb_luminance(lifted) > 200

    def test_capping_keeps_the_hue_and_the_cap(self):
        capped = cap_luminance((248, 61, 1), 34)
        assert capped[0] > capped[1] > capped[2]
        assert rgb_luminance(capped) <= 35


class TestLogoPalette:
    def test_a_saturated_crest_is_its_own_headline(self):
        palette = logo_palette(CRESTS["RED"])
        r, g, b = palette["headline"]
        assert r > 150 and r > 2 * g and r > 2 * b
        assert rgb_luminance(palette["deep"]) <= 36

    def test_a_legible_band_beats_lifting_a_dark_body(self):
        palette = logo_palette(CRESTS["NAV"])
        r, g, b = palette["headline"]
        assert r > 150 and g > 110 and b < 110, f"{palette['headline']} is not the gold band"
        assert palette["deep"][2] >= palette["deep"][0], "the backdrop lost the navy"

    def test_a_crest_with_no_colour_falls_back_to_its_brightest_grey(self):
        palette = logo_palette(CRESTS["SIL"])
        assert palette is not None
        assert rgb_saturation(palette["headline"]) < 0.12

    def test_nothing_opaque_is_no_palette(self):
        assert logo_palette(Image.new("RGBA", (16, 16))) is None

    def test_an_unreadable_crest_is_no_palette(self):
        assert logo_palette(object()) is None

    def test_every_colour_has_three_channels(self):
        palette = logo_palette(CRESTS["RED"])
        assert set(palette) == {"deep", "glow", "headline", "accent"}
        assert all(len(c) == 3 for c in palette.values())


# ---------------------------------------------------------------------------
# The celebration's palette
# ---------------------------------------------------------------------------

class TestCelebrationPalette:
    def test_read_off_the_scoring_side(self):
        away = Host()._celebration_palette(celebration(side="away"))
        home = Host()._celebration_palette(celebration(scorer="NAV", other="RED", side="home"))
        assert away == logo_palette(Host()._load_and_resize_logo(None, "RED", None, None))
        assert home["headline"] != away["headline"]

    def test_worked_out_once_per_celebration(self):
        host, c = Host(), celebration()
        first = host._celebration_palette(c)
        host._load_and_resize_logo = mock.Mock(side_effect=AssertionError("reloaded"))
        assert host._celebration_palette(c) is first

    @pytest.mark.parametrize("loader", [lambda *a: None, mock.Mock(side_effect=OSError("bad png"))])
    def test_no_usable_crest_falls_back(self, loader):
        host = Host()
        host._load_and_resize_logo = loader
        assert host._celebration_palette(celebration()) == Host._DEFAULT_CELEBRATION_PALETTE

    def test_team_colours_off_is_the_default(self):
        host = Host(celebration_team_colors=False)
        assert host._celebration_palette(celebration()) == Host._DEFAULT_CELEBRATION_PALETTE


# ---------------------------------------------------------------------------
# The takeover
# ---------------------------------------------------------------------------

class TestTakeover:
    def test_frame_is_presented(self):
        host = Host()
        render(host)
        assert host.display_manager.updates == 1
        assert host.display_manager.image.size == (128, 32)

    def test_same_inputs_same_frame(self):
        assert render().tobytes() == render().tobytes()

    def test_the_highlight_follows_the_scoring_side(self):
        away = render(side="away", scorer="RED", other="RED")
        home = render(side="home", scorer="RED", other="RED")
        assert ImageChops.difference(away, home).getbbox() is not None

    def test_each_motif_paints_its_own_scenery(self):
        shots = {m: render(Host(celebration_confetti=False), motif=m).tobytes()
                 for m in ("score", "kick", "touchdown", "net", "win")}
        assert len(set(shots.values())) == len(shots)

    def test_an_unknown_motif_draws_the_score_scenery(self):
        host = Host(celebration_confetti=False)
        assert render(host, motif="bogus").tobytes() == render(Host(celebration_confetti=False),
                                                                motif="score").tobytes()

    @pytest.mark.parametrize("knob", ["celebration_team_colors", "celebration_confetti"])
    def test_switches_change_the_frame(self, knob):
        assert render(Host(**{knob: False})).tobytes() != render(Host()).tobytes()

    def test_the_matrix_size_wins_over_the_configured_one(self):
        host = Host(width=64, height=32)
        host.display_manager.matrix = type("M", (), {"width": 128, "height": 32})()
        assert render(host).size == (128, 32)

    @pytest.mark.parametrize("width,height", SIZES)
    def test_every_frame_is_a_finished_card(self, width, height):
        # A switch-mode board samples once a second: any frame may be the only
        # one seen, so each carries the headline and nothing is blank.
        for elapsed in [0.0] + [i + 0.5 for i in range(8)]:
            img = render(Host(width, height), elapsed=elapsed)
            assert brightest(img) > 40
            assert brightest(img, (0, 0, width, max(2, height // 4))) > 60

    def test_the_score_stays_on_a_tall_panel(self):
        # 16px digits at 48 tall used to run off the bottom row. The goal
        # line keeps the scenery off that row, so only the score could be.
        img = render(Host(192, 48, celebration_confetti=False), motif="touchdown")
        assert brightest(img, (48, 47, 144, 48)) < 10
        assert brightest(img, (48, 24, 144, 47)) > 10

    def test_the_scoring_side_breathes_rather_than_toggling(self):
        frames = {render(Host(celebration_confetti=False), elapsed=t).tobytes()
                  for t in (0.9, 1.9, 2.9, 3.9, 4.9, 5.9)}
        assert len(frames) > 2

    def test_confetti_is_seeded_from_the_game_not_the_clock(self):
        host, c = Host(), celebration()
        palette = host._celebration_palette(c)
        flakes = host._celebration_confetti(c, 128, 32, palette)
        again = Host()._celebration_confetti(celebration(), 128, 32, palette)
        assert flakes == again and 6 <= len(flakes) <= 22

    def test_confetti_is_gone_by_the_end(self):
        host, c = Host(), celebration()
        palette = host._celebration_palette(c)
        overlay = Image.new("RGBA", (128, 32), (0, 0, 0, 0))
        host._draw_celebration_confetti(ImageDraw.Draw(overlay), c, 128, 32, palette, 8.0, 1.0)
        assert overlay.getbbox() is None

    def test_the_side_that_did_not_score_is_dimmed(self):
        crests = Host()._celebration_crests(celebration(scorer="RED", other="RED"), 32)
        assert brightest(crests["home"]) < brightest(crests["away"])

    def test_a_crest_that_fails_to_load_is_left_out(self):
        host = Host()
        host._load_and_resize_logo = mock.Mock(side_effect=OSError("bad png"))
        assert host._celebration_crests(celebration(), 32) == {"away": None, "home": None}
        assert brightest(render(host)) > 40

    def test_fit_font_falls_back_to_the_smallest(self):
        host = Host()
        draw = ImageDraw.Draw(Image.new("RGB", (8, 8)))
        fonts = [host.fonts["time"], host.fonts["status"]]
        assert host._fit_font(draw, "A", 128, fonts) is fonts[0]
        assert host._fit_font(draw, "A VERY LONG HEADLINE", 8, fonts) is fonts[-1]


# ---------------------------------------------------------------------------
# Host contract
# ---------------------------------------------------------------------------

def _self_reads():
    """Every ``self.X`` / ``getattr(self, "X")`` the mixin reads, by parsing it."""
    tree = ast.parse(Path(sports_celebration.__file__).read_text(encoding="utf-8"))
    cls = next(n for n in tree.body
               if isinstance(n, ast.ClassDef) and n.name == "SportsCelebrationMixin")
    names = set()
    for node in ast.walk(cls):
        if (isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Load)
                and isinstance(node.value, ast.Name) and node.value.id == "self"):
            names.add(node.attr)
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "getattr" and len(node.args) >= 2
                and isinstance(node.args[0], ast.Name) and node.args[0].id == "self"
                and isinstance(node.args[1], ast.Constant)):
            names.add(node.args[1].value)
    return names


class TestHostContract:
    def test_every_host_read_is_documented(self):
        needed = _self_reads() - set(dir(SportsCelebrationMixin))
        undocumented = sorted(n for n in needed if f"``{n}" not in sports_celebration.__doc__)
        assert undocumented == [], f"read but not in the host contract: {undocumented}"

    def test_the_stub_host_is_enough(self):
        # Host above sets the contract and nothing else; it drew every test.
        needed = _self_reads() - set(dir(SportsCelebrationMixin))
        host = Host(celebration_duration=8, celebration_team_colors=True,
                    celebration_confetti=True)
        assert all(hasattr(host, n) for n in needed)

    def test_the_mixin_creates_no_attributes_of_its_own(self):
        # The annotations are for type checking; the host's values must win.
        for name in ("display_manager", "fonts", "logger", "_load_and_resize_logo"):
            assert not hasattr(SportsCelebrationMixin, name)
