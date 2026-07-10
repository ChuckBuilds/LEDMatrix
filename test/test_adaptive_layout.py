"""Tests for the adaptive layout system (src/adaptive_layout.py)."""

import pytest

from src.adaptive_layout import (
    DEFAULT_DESIGN_SIZE,
    LADDER_ARCADE,
    LADDER_GRID,
    LayoutContext,
    Region,
    draw_fitted_text,
    measure_font_crispness,
    measure_ink,
    media_row,
    scoreboard_regions,
)
from src.plugin_system.testing.sizes import DEFAULT_TEST_SIZES
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

    def test_offset_translates_without_resizing(self):
        r = Region(5, 5, 20, 10).offset(3, -2)
        assert r == Region(8, 3, 20, 10)


class TestScoreboardRegions:
    @pytest.mark.parametrize("w,h", DEFAULT_TEST_SIZES + [(8, 8)])
    def test_invariants_at_all_sizes(self, w, h):
        regs = scoreboard_regions(Region(0, 0, w, h))
        assert regs.logo_slot == min(h, w // 2)
        # slots hug the edges and never overlap the center column
        assert regs.away_slot.x == 0 and regs.home_slot.right == w
        assert regs.away_slot.right <= regs.center_col.x or regs.center_col.w == 0
        assert regs.center_col.right <= regs.home_slot.x or regs.center_col.w == 0
        # bands stack inside the center column without overlap
        assert regs.status_band.bottom <= regs.score_area.y or regs.score_area.h == 0
        assert regs.score_area.bottom <= regs.detail_band.y or regs.score_area.h == 0
        # everything within bounds, nothing negative
        for reg in (regs.away_slot, regs.home_slot, regs.center_col,
                    regs.status_band, regs.score_area, regs.detail_band,
                    regs.bottom_left, regs.bottom_right):
            assert reg.w >= 0 and reg.h >= 0
            assert reg.x >= 0 and reg.y >= 0
            assert reg.right <= w and reg.bottom <= h

    def test_ctx_scales_band_heights(self, font_manager):
        small = scoreboard_regions(Region(0, 0, 128, 32),
                                   ctx=LayoutContext(128, 32, font_manager))
        big = scoreboard_regions(Region(0, 0, 256, 64),
                                 ctx=LayoutContext(256, 64, font_manager))
        assert big.status_band.h > small.status_band.h

    def test_works_on_offset_card_region(self):
        card = Region(10, 4, 100, 24)
        regs = scoreboard_regions(card)
        assert regs.away_slot.x == 10
        assert regs.home_slot.right == card.right


class TestMediaRow:
    def test_square_art_plus_body(self):
        row = media_row(Region(0, 0, 128, 32))
        assert row.art == Region(0, 0, 32, 32)
        assert row.body.x == 32 + 2 and row.body.right == 128

    def test_non_square(self):
        row = media_row(Region(0, 0, 100, 20), square=False, gap=4)
        assert row.art.w == 50
        assert row.body.x == 54

    def test_narrow_panel_clamps(self):
        row = media_row(Region(0, 0, 16, 32))
        assert row.art.w == 16 and row.body.w == 0


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

    def test_ladder_grid_is_crisp(self, font_manager):
        """LADDER_GRID's BDF fonts are real bitmaps — always 0% antialiased."""
        for step in LADDER_GRID:
            font = font_manager.get_font(step.family, step.size_px)
            assert measure_font_crispness(font, "Ay0") == 0.0

    def test_ladder_arcade_is_crisp(self, font_manager):
        """PressStart2P only rasterizes without antialiasing at exact
        multiples of its 8px design grid — every LADDER_ARCADE rung must
        land on one."""
        for step in LADDER_ARCADE:
            assert step.size_px % 8 == 0, f"{step} is not a multiple of 8"
            font = font_manager.get_font(step.family, step.size_px)
            assert measure_font_crispness(font, "17-21") == 0.0

    def test_crispness_catches_a_bad_size(self, font_manager):
        """Sanity check the measurement itself: a known-bad size for a
        pixel-grid font must NOT read as crisp."""
        font = font_manager.get_font("press_start", 10)  # not a multiple of 8
        assert measure_font_crispness(font, "17-21") > 0.1

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

    def test_fit_text_proportional_tracks_design_scale(self, font_manager):
        # design size 128x32, base_size_px=10 (a typical classic score size):
        # at 2x scale the target is 20px -> nearest LADDER_ARCADE rung <= 20
        # is 16px, not the largest that merely fits the box (32).
        ctx = LayoutContext(256, 64, font_manager)  # scale = min(2,2) = 2
        fit = ctx.fit_text_proportional("17-21", ctx.bounds, base_size_px=10,
                                        ladder=LADDER_ARCADE)
        assert fit.size_px == 16

    def test_fit_text_proportional_does_not_exceed_max_fit(self, ctx):
        # at scale=1 (128x32, the design size itself) the target equals
        # base_size_px, so proportional should never pick something LARGER
        # than plain fit_text would for the same box.
        prop = ctx.fit_text_proportional("17-21", ctx.bounds, base_size_px=10,
                                         ladder=LADDER_ARCADE)
        maxed = ctx.fit_text("17-21", ctx.bounds, ladder=LADDER_ARCADE)
        assert prop.size_px <= maxed.size_px

    def test_fit_text_proportional_floors_at_smallest_rung(self, font_manager):
        # scale so small the target is below every rung -> use the smallest
        # rung as a floor rather than refusing to render anything.
        ctx = LayoutContext(32, 8, font_manager)  # scale = min(32/128, 8/32) = 0.25
        fit = ctx.fit_text_proportional("HI", ctx.bounds, base_size_px=10,
                                        ladder=LADDER_ARCADE)
        assert fit.size_px == min(s.size_px for s in LADDER_ARCADE)

    def test_fit_text_proportional_falls_through_when_target_rung_overflows(self, font_manager):
        # a long string at the target rung might not fit a narrow box even
        # though the target size is "correct" -- must fall through to a
        # smaller rung exactly like fit_text does, not just refuse to fit.
        ctx = LayoutContext(256, 64, font_manager)
        narrow_box = Region(0, 0, 40, 64)
        fit = ctx.fit_text_proportional("A REALLY LONG STRING HERE", narrow_box,
                                        base_size_px=10, ladder=LADDER_ARCADE)
        assert fit.fits or fit.text.endswith("…")

    def test_fit_text_proportional_cached(self, ctx):
        first = ctx.fit_text_proportional("X", ctx.bounds, base_size_px=10)
        second = ctx.fit_text_proportional("X", ctx.bounds, base_size_px=10)
        assert first is second

    def test_fit_text_proportional_scale_override(self, font_manager):
        # 128x64 vs design 128x32: self.scale (min of both axes) is 1.0
        # since width didn't grow, but a caller whose composition scales by
        # HEIGHT alone (e.g. logo_slot = min(h, w//2)) should be able to
        # override the reference scale so text grows with it too.
        ctx = LayoutContext(128, 64, font_manager)
        assert ctx.scale == 1.0
        default_fit = ctx.fit_text_proportional("17-21", ctx.bounds, base_size_px=10,
                                                 ladder=LADDER_ARCADE)
        height_scale = 64 / 32  # matches design height
        scaled_fit = ctx.fit_text_proportional("17-21", ctx.bounds, base_size_px=10,
                                                ladder=LADDER_ARCADE, scale=height_scale)
        assert scaled_fit.size_px > default_fit.size_px

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
