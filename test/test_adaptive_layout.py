"""Tests for the adaptive layout system (src/adaptive_layout.py)."""

import pytest

from src.adaptive_layout import (
    DEFAULT_DESIGN_SIZE,
    LADDER_ARCADE,
    LADDER_GRID,
    LayoutContext,
    Region,
    draw_fitted_text,
    measure_ink,
)
from src.font_manager import FontManager


@pytest.fixture(scope="module")
def font_manager():
    """Real FontManager over assets/fonts — the ladders depend on it."""
    return FontManager({})


@pytest.fixture
def ctx(font_manager):
    return LayoutContext(128, 32, font_manager)


class TestRegion:
    """Pure integer rect algebra."""

    def test_bands_partition_without_overlap(self):
        r = Region(0, 0, 128, 32)
        top = r.top_band(7)
        bottom = r.bottom_band(7)
        middle = r.middle(7, 7)
        assert top.bottom == middle.y
        assert middle.bottom == bottom.y
        assert top.h + middle.h + bottom.h == r.h

    def test_bands_clamp_on_short_panel(self):
        # The classic failure: y=1 top band and y=height-7 bottom band
        # overlapping on a short panel. Bands can't exceed the region.
        r = Region(0, 0, 32, 8)
        assert r.top_band(16).h == 8
        assert r.bottom_band(16).h == 8
        assert r.middle(8, 8).h == 0  # degenerate, never negative

    def test_split_v_weights_sum_to_height(self):
        r = Region(0, 0, 64, 33)
        rows = r.split_v(3, 1, 1, gap=1)
        assert len(rows) == 3
        assert sum(row.h for row in rows) == 33 - 2  # two 1px gaps
        assert rows[0].h > rows[1].h
        assert rows[-1].bottom == r.bottom

    def test_split_h_columns_advance(self):
        r = Region(0, 0, 100, 32)
        cols = r.split_h(1, 1, gap=2)
        assert cols[0].right + 2 == cols[1].x
        assert cols[1].right == r.right

    def test_degenerate_sizes_never_negative(self):
        for w, h in [(8, 8), (32, 16), (1, 1)]:
            r = Region(0, 0, w, h).inset(4)
            assert r.w >= 0 and r.h >= 0
            for sub in r.split_v(1, 1) + r.split_h(1, 1, gap=3):
                assert sub.w >= 0 and sub.h >= 0

    def test_align_xy(self):
        r = Region(10, 10, 100, 20)
        assert r.align_xy(20, 10, "left", "top") == (10, 10)
        assert r.align_xy(20, 10, "right", "bottom") == (90, 20)
        assert r.align_xy(20, 10) == (50, 15)

    def test_left_right_cols(self):
        r = Region(0, 0, 128, 32)
        assert r.left_col(32) == Region(0, 0, 32, 32)
        assert r.right_col(32) == Region(96, 0, 32, 32)


class TestLayoutContext:
    def test_tiers(self, font_manager):
        assert LayoutContext(128, 32, font_manager).tier == "sm"
        assert LayoutContext(96, 48, font_manager).tier == "md"
        assert LayoutContext(128, 64, font_manager).tier == "lg"
        assert LayoutContext(64, 16, font_manager).tier == "xs"
        assert LayoutContext(256, 128, font_manager).tier == "xl"

    def test_wide_short_flag(self, font_manager):
        assert LayoutContext(128, 32, font_manager).is_wide_short
        assert not LayoutContext(128, 64, font_manager).is_wide_short

    def test_scale_against_design_size(self, font_manager):
        assert LayoutContext(128, 32, font_manager).scale == 1.0
        assert LayoutContext(256, 64, font_manager).scale == 2.0
        # min() of the two axes: don't overscale the constrained one
        assert LayoutContext(256, 32, font_manager).scale == 1.0
        assert DEFAULT_DESIGN_SIZE == (128, 32)

    def test_px_scales_and_clamps(self, font_manager):
        big = LayoutContext(256, 64, font_manager)
        assert big.px(4) == 8
        assert big.px(4, maximum=6) == 6
        tiny = LayoutContext(32, 16, font_manager)
        assert tiny.px(4, minimum=2) == 2

    def test_by_tier_nearest_at_or_below(self, font_manager):
        mapping = {"sm": 10, "lg": 18}
        assert LayoutContext(128, 32, font_manager).by_tier(mapping) == 10
        assert LayoutContext(96, 48, font_manager).by_tier(mapping) == 10  # md -> sm
        assert LayoutContext(128, 64, font_manager).by_tier(mapping) == 18
        assert LayoutContext(256, 128, font_manager).by_tier(mapping) == 18  # xl -> lg
        # nothing at-or-below: fall forward to smallest defined above
        assert LayoutContext(64, 16, font_manager).by_tier(mapping) == 10


class TestFontFitting:
    def test_ladder_monotonic(self, font_manager):
        """Each ladder rung must render no taller than the one before it."""
        for ladder in (LADDER_GRID, LADDER_ARCADE):
            heights = []
            for step in ladder:
                font = font_manager.get_font(step.family, step.size_px)
                heights.append(measure_ink("Ay0", font)[1])
            assert heights == sorted(heights, reverse=True), (
                f"ladder not monotonically shrinking: {heights}")

    def test_fit_text_grows_on_taller_panel(self, font_manager):
        small = LayoutContext(64, 32, font_manager)
        large = LayoutContext(128, 64, font_manager)
        text = "12:34"
        fit_small = small.fit_text(text, small.bounds, ladder=LADDER_ARCADE)
        fit_large = large.fit_text(text, large.bounds, ladder=LADDER_ARCADE)
        assert fit_small.fits and fit_large.fits
        assert fit_large.size_px > fit_small.size_px

    def test_fit_text_fits_the_box(self, ctx):
        box = ctx.bounds.inset(1)
        fit = ctx.fit_text("HELLO WORLD", box)
        assert fit.fits
        assert fit.width <= box.w and fit.height <= box.h

    def test_fit_text_ellipsizes_overlong_text(self, font_manager):
        tiny = LayoutContext(32, 16, font_manager)
        fit = tiny.fit_text("SUPERCALIFRAGILISTIC", tiny.bounds)
        assert fit.text != "SUPERCALIFRAGILISTIC"
        assert fit.text.endswith("…")
        assert fit.width <= tiny.bounds.w

    def test_fit_text_cached(self, ctx):
        first = ctx.fit_text("CACHED", ctx.bounds)
        second = ctx.fit_text("CACHED", ctx.bounds)
        assert first is second
        ctx.clear_cache()
        assert ctx.fit_text("CACHED", ctx.bounds) is not first

    def test_fit_lines_stacks_within_height(self, ctx):
        box = ctx.bounds
        lines = ["LINE ONE", "LINE TWO", "LINE THREE"]
        fit = ctx.fit_lines(lines, box, spacing=1)
        assert fit.fits
        assert 3 * fit.line_height + 2 <= box.h

    def test_font_for_rows(self, ctx):
        fit = ctx.font_for_rows(4, 32)
        assert fit.fits
        assert 4 * fit.line_height <= 32

    def test_ellipsize_returns_original_when_it_fits(self, ctx):
        font = ctx.font_manager.get_font("4x6", 6)
        assert ctx.ellipsize("HI", font, 1000) == "HI"


class TestDrawFittedText:
    def test_draws_within_region(self, ctx):
        calls = []

        class _DM:
            def draw_text(self, text, x=None, y=None, color=None, font=None):
                calls.append((text, x, y))

        box = Region(10, 4, 100, 24)
        fit = ctx.fit_text("SCORE", box)
        draw_fitted_text(_DM(), fit, box)
        text, x, y = calls[0]
        assert text == "SCORE"
        assert box.x <= x <= box.right - fit.width
        # the ink (y + y_offset .. + height) must land inside the box
        assert box.y <= y + fit.y_offset
        assert y + fit.y_offset + fit.height <= box.bottom


class TestBasePluginIntegration:
    def test_layout_property_and_draw_fit(self):
        from src.plugin_system.base_plugin import BasePlugin
        from src.plugin_system.testing.mocks import (
            MockCacheManager, MockDisplayManager, MockPluginManager,
        )

        class _Plugin(BasePlugin):
            def update(self):
                pass

            def display(self, force_clear=False):
                pass

        plugin = _Plugin("test-plugin", {}, MockDisplayManager(96, 48),
                         MockCacheManager(), MockPluginManager())
        assert (plugin.layout.width, plugin.layout.height) == (96, 48)
        assert plugin.layout is plugin.layout  # cached
        fit = plugin.draw_fit("HELLO", plugin.layout.bounds.inset(1))
        assert fit.fits
        assert plugin.display_manager.draw_calls  # actually drew

    def test_layout_rebuilds_on_size_change(self):
        from src.plugin_system.base_plugin import BasePlugin
        from src.plugin_system.testing.mocks import (
            MockCacheManager, MockDisplayManager, MockPluginManager,
        )

        class _Plugin(BasePlugin):
            def update(self):
                pass

            def display(self, force_clear=False):
                pass

        dm = MockDisplayManager(128, 32)
        plugin = _Plugin("test-plugin", {}, dm,
                         MockCacheManager(), MockPluginManager())
        assert plugin.layout.tier == "sm"
        dm.width, dm.height = 128, 64
        assert plugin.layout.tier == "lg"

    def test_design_size_from_manifest(self):
        from src.plugin_system.base_plugin import BasePlugin
        from src.plugin_system.testing.mocks import (
            MockCacheManager, MockDisplayManager, MockPluginManager,
        )

        class _Plugin(BasePlugin):
            def update(self):
                pass

            def display(self, force_clear=False):
                pass

        pm = MockPluginManager()
        pm.plugin_manifests["test-plugin"] = {
            "display": {"design_size": {"width": 64, "height": 32}}
        }
        plugin = _Plugin("test-plugin", {}, MockDisplayManager(128, 64),
                         MockCacheManager(), pm)
        assert plugin.layout.design_size == (64, 32)
        assert plugin.layout.scale == 2.0
