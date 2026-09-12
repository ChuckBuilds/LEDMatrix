"""The bundled faces load on their pixel grid, from any working directory.

`4x6-font.ttf` renders crisply at multiples of 7, not at the 6 its name
suggests. Plugins draw it with ``draw.fontmode = "1"``, and the mono rasteriser
thresholds each glyph at 50% coverage: a glyph asked for at 6 loses its fourth
column, so "UNTIL" rendered as "VM1JL" and "CHRISTMAS" as "CHAJS1MAS" on real
panels. The committed golden images for christmas-countdown encoded exactly
that, because the harness sized the face independently of the display core and
so agreed with it about the wrong number.

These tests pin the three things that failure needed:

* the size comes from `crisp_size`, not a literal;
* `VisualTestDisplayManager` -- the harness's deliberate fork of
  `DisplayManager` -- agrees with it, so a golden blessed by the harness
  matches what the panel draws;
* the paths resolve against the install root, so a run from another directory
  loads the real face instead of silently falling back to
  `ImageFont.load_default()`.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

os.environ.setdefault("EMULATOR", "true")

from src.common.font_layout import (  # noqa: E402
    FONT_PIXEL_GRID, crisp_size, load_truetype, resolve_asset_path,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
FOUR_BY_SIX = "4x6-font.ttf"
PRESS_START = "PressStart2P-Regular.ttf"


class TestCrispSize:
    """The snapping rule itself."""

    def test_the_natural_looking_six_is_off_grid(self):
        # The whole bug in one line: 6 is not a size this face renders on.
        assert crisp_size(FOUR_BY_SIX, 6) == 7

    def test_press_start_is_unchanged_at_eight(self):
        assert crisp_size(PRESS_START, 8) == 8

    def test_an_unknown_face_is_never_second_guessed(self):
        assert crisp_size("SomeUserUpload.ttf", 11) == 11

    @pytest.mark.parametrize("face,grid", sorted(FONT_PIXEL_GRID.items()))
    def test_every_known_face_snaps_to_its_own_grid(self, face, grid):
        for desired in range(1, grid * 4):
            assert crisp_size(face, desired) % grid == 0


class TestGlyphsKeepTheirFourthColumn:
    """Why the grid matters, measured rather than asserted from the docs."""

    @staticmethod
    def _ink_width(font, ch):
        img = Image.new("L", (32, 16), 0)
        draw = ImageDraw.Draw(img)
        draw.fontmode = "1"  # what the plugins draw with
        draw.text((2, 2), ch, font=font, fill=255)
        box = img.getbbox()
        return 0 if box is None else box[2] - box[0]

    @pytest.mark.parametrize("ch", list("WM08"))
    def test_on_grid_is_a_full_four_pixels_wide(self, ch):
        path = resolve_asset_path(f"assets/fonts/{FOUR_BY_SIX}")
        on_grid = load_truetype(path, crisp_size(FOUR_BY_SIX, 6))
        off_grid = load_truetype(path, 6)
        assert self._ink_width(on_grid, ch) == 4
        # The regression this guards: the same glyph one pixel narrower.
        assert self._ink_width(off_grid, ch) == 3


class TestAssetPathsIgnoreTheWorkingDirectory:
    """A run from anywhere else must not degrade to the default face."""

    def test_resolves_from_an_unrelated_directory(self, tmp_path):
        # Resolution must not depend on the cwd of the *test* process either,
        # so this runs in a child with tmp_path as its working directory.
        code = (
            "from src.common.font_layout import resolve_asset_path;"
            "import os;"
            "p = resolve_asset_path('assets/fonts/4x6-font.ttf');"
            "print(os.path.isabs(p) and os.path.exists(p))"
        )
        env = dict(os.environ, PYTHONPATH=str(PROJECT_ROOT))
        out = subprocess.run(  # nosec B603 - fixed argv, no shell
            [sys.executable, "-c", code],
            cwd=tmp_path, env=env, capture_output=True, text=True, timeout=120,
        )
        assert out.stdout.strip() == "True", out.stderr

    def test_an_absolute_path_is_returned_untouched(self):
        absolute = str(PROJECT_ROOT / "assets" / "fonts" / FOUR_BY_SIX)
        assert resolve_asset_path(absolute) == absolute

    def test_font_manager_shares_the_one_definition(self):
        # Plugins probe for this method by name to borrow the core's notion of
        # "install root"; it must stay, and must agree with the free function.
        from src.font_manager import FontManager
        rel = f"assets/fonts/{FOUR_BY_SIX}"
        assert FontManager._resolve_asset_path(rel) == resolve_asset_path(rel)


class TestTheHarnessForkAgreesWithTheCore:
    """The divergence that let the wrong rendering be blessed as golden.

    `VisualTestDisplayManager` is a deliberate fork of `DisplayManager` that
    runs without hardware, and the harness renders every golden through it. It
    carries its own `_load_fonts`, so a size fixed in one and not the other
    means CI compares panels against a face no panel uses.
    """

    @staticmethod
    def _sizes(dm):
        return {name: getattr(dm, name).size
                for name in ("regular_font", "small_font", "extra_small_font")}

    def test_extra_small_font_is_on_grid_in_the_harness(self):
        from src.plugin_system.testing.visual_display_manager import (
            VisualTestDisplayManager,
        )
        dm = VisualTestDisplayManager(width=128, height=32)
        assert dm.extra_small_font.size == crisp_size(FOUR_BY_SIX, 6) == 7

    def test_the_fork_and_the_core_load_the_same_sizes(self):
        from unittest.mock import MagicMock, patch

        from src.display_manager import DisplayManager
        from src.plugin_system.testing.visual_display_manager import (
            VisualTestDisplayManager,
        )

        fork = VisualTestDisplayManager(width=128, height=32)

        with patch("src.display_manager.RGBMatrix") as matrix, \
                patch("src.display_manager.RGBMatrixOptions"):
            instance = MagicMock()
            instance.width, instance.height = 128, 32
            matrix.return_value = instance
            DisplayManager._instance = None
            core = DisplayManager.__new__(DisplayManager)
            core._text_width_cache = {}
            core._load_fonts()

        assert self._sizes(core) == self._sizes(fork)
