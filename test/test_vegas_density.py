"""
Tests for the Vegas mode density work: dead-space trimming in PluginAdapter
and the configurable lead-in gap in ScrollHelper.
"""

import pytest
from PIL import Image

from src.common.scroll_helper import ScrollHelper
from src.vegas_mode.config import VegasModeConfig
from src.vegas_mode.geometry import column_has_ink
from src.vegas_mode.plugin_adapter import PluginAdapter

DISPLAY_W = 512
DISPLAY_H = 64


class FakeDisplayManager:
    """Minimal stand-in; the native content path never touches the canvas."""

    width = DISPLAY_W
    height = DISPLAY_H

    def __init__(self):
        self.image = Image.new('RGB', (DISPLAY_W, DISPLAY_H))


class NativePlugin:
    """Plugin that returns pre-rendered Vegas content."""

    def __init__(self, images):
        self._images = images

    def get_vegas_content(self):
        return self._images


def canvas(content_spans, width=DISPLAY_W, height=DISPLAY_H):
    """Full-display canvas with white content in the given [x0, x1) spans."""
    img = Image.new('RGB', (width, height), (0, 0, 0))
    for x0, x1 in content_spans:
        img.paste(Image.new('RGB', (x1 - x0, height), (255, 255, 255)), (x0, 0))
    return img


def adapter_with(**overrides):
    cfg = VegasModeConfig(**overrides)
    return PluginAdapter(FakeDisplayManager(), cfg)


class TestAdapterTrimming:
    def test_of_the_day_case_is_reclaimed(self):
        # Measured on devpi: "No Data" occupying 35px of a 512px canvas bought
        # 9.5s of black at 50px/s.
        adapter = adapter_with(content_padding=0)
        plugin = NativePlugin([canvas([(4, 39)])])
        images = adapter.get_content(plugin, 'of-the-day')
        assert len(images) == 1
        assert images[0].width == 35

    def test_youtube_stats_case_is_reclaimed(self):
        # Centred 142px of content on a 512px canvas: 185px black each side.
        adapter = adapter_with(content_padding=0)
        plugin = NativePlugin([canvas([(185, 327)])])
        images = adapter.get_content(plugin, 'youtube-stats')
        assert images[0].width == 142

    def test_padding_is_applied_within_available_margin(self):
        adapter = adapter_with(content_padding=8)
        plugin = NativePlugin([canvas([(185, 327)])])
        images = adapter.get_content(plugin, 'youtube-stats')
        assert images[0].width == 142 + 16

    def test_interior_layout_gap_survives(self):
        # A logo far left and a score far right is deliberate layout; closing
        # the gap would corrupt the design rather than reclaim dead space.
        adapter = adapter_with(content_padding=0)
        plugin = NativePlugin([canvas([(10, 40), (400, 460)])])
        images = adapter.get_content(plugin, 'scoreboard')
        assert images[0].width == 450  # 10..459
        assert int(column_has_ink(images[0]).sum()) == 90

    def test_wide_legitimate_content_is_left_alone(self):
        # geochron uses 444 of 512 columns; only the real tail should go.
        adapter = adapter_with(content_padding=0)
        plugin = NativePlugin([canvas([(0, 444)])])
        images = adapter.get_content(plugin, 'geochron')
        assert images[0].width == 444

    def test_non_black_background_is_untouched(self):
        adapter = adapter_with(content_padding=0)
        bg = Image.new('RGB', (DISPLAY_W, DISPLAY_H), (0, 0, 40))
        plugin = NativePlugin([bg])
        images = adapter.get_content(plugin, 'weather')
        assert images[0].width == DISPLAY_W

    def test_each_image_of_a_multi_item_segment_is_trimmed(self):
        # compose_scroll_content treats every image as its own item, so a
        # per-image trim is what makes separator_width the real gap.
        adapter = adapter_with(content_padding=0)
        plugin = NativePlugin([
            canvas([(168, 344)]),
            canvas([(200, 300)]),
            canvas([(0, 512)]),
        ])
        images = adapter.get_content(plugin, 'ledmatrix-flights')
        assert [img.width for img in images] == [176, 100, 512]

    def test_fully_blank_segment_contributes_nothing(self):
        adapter = adapter_with()
        plugin = NativePlugin([Image.new('RGB', (DISPLAY_W, DISPLAY_H))])
        assert adapter.get_content(plugin, 'empty') is None

    def test_blank_images_are_dropped_but_others_kept(self):
        adapter = adapter_with(content_padding=0)
        plugin = NativePlugin([
            canvas([(100, 150)]),
            Image.new('RGB', (DISPLAY_W, DISPLAY_H)),
            canvas([(200, 260)]),
        ])
        images = adapter.get_content(plugin, 'mixed')
        assert [img.width for img in images] == [50, 60]

    def test_min_plugin_width_rejects_noise(self):
        adapter = adapter_with(content_padding=0, min_plugin_width=32)
        plugin = NativePlugin([canvas([(10, 14)])])
        assert adapter.get_content(plugin, 'sliver') is None

    def test_min_plugin_width_of_zero_keeps_everything(self):
        adapter = adapter_with(content_padding=0, min_plugin_width=0)
        plugin = NativePlugin([canvas([(10, 14)])])
        images = adapter.get_content(plugin, 'sliver')
        assert images[0].width == 4

    def test_auto_trim_off_preserves_original_behaviour(self):
        adapter = adapter_with(auto_trim=False)
        plugin = NativePlugin([canvas([(4, 39)])])
        images = adapter.get_content(plugin, 'of-the-day')
        assert images[0].width == DISPLAY_W

    def test_trim_threshold_ignores_near_black_noise(self):
        # A very dark band should not be mistaken for content.
        img = Image.new('RGB', (DISPLAY_W, DISPLAY_H), (0, 0, 0))
        img.paste(Image.new('RGB', (100, DISPLAY_H), (3, 3, 3)), (0, 0))
        img.paste(Image.new('RGB', (50, DISPLAY_H), (255, 255, 255)), (200, 0))
        adapter = adapter_with(content_padding=0, trim_threshold=10)
        images = adapter.get_content(NativePlugin([img]), 'noisy')
        assert images[0].width == 50

    def test_height_is_preserved_through_trim(self):
        adapter = adapter_with(content_padding=0)
        plugin = NativePlugin([canvas([(100, 200)])])
        images = adapter.get_content(plugin, 'x')
        assert images[0].height == DISPLAY_H

    def test_trimmed_result_is_cached(self):
        adapter = adapter_with(content_padding=0)
        plugin = NativePlugin([canvas([(100, 200)])])
        first = adapter.get_content(plugin, 'cached')
        # Swap the plugin's content; the cache should still serve the old size.
        plugin._images = [canvas([(0, 512)])]
        second = adapter.get_content(plugin, 'cached')
        assert first[0].width == second[0].width == 100

    def test_default_adapter_construction_still_works(self):
        # Existing callers pass only the display manager.
        adapter = PluginAdapter(FakeDisplayManager())
        assert adapter.config.auto_trim is True


class TestWidthBudget:
    def test_segment_within_budget_is_untouched(self):
        adapter = adapter_with(content_padding=0, max_plugin_width_ratio=3.0)
        plugin = NativePlugin([canvas([(0, 400)])])
        assert adapter.get_content(plugin, 'small')[0].width == 400

    def test_oversized_multi_item_segment_is_capped(self):
        # 10 items of 400px = 4000px against a 1536px budget (3 x 512).
        adapter = adapter_with(content_padding=0, max_plugin_width_ratio=3.0)
        items = [canvas([(0, 400)], width=400) for _ in range(10)]
        images = adapter.get_content(NativePlugin(items), 'stock-news')
        assert sum(i.width for i in images) <= 3 * DISPLAY_W
        assert len(images) == 3  # 1200px; a 4th would exceed 1536

    def test_deferred_items_appear_on_the_next_cycle(self):
        adapter = adapter_with(content_padding=0, max_plugin_width_ratio=1.0)
        # Distinguish items by width so the rotation is observable.
        items = [canvas([(0, w)], width=w) for w in (200, 210, 220, 230, 240)]
        plugin = NativePlugin(items)

        first = adapter.get_content(plugin, 'ticker')
        adapter.invalidate_cache('ticker')
        second = adapter.get_content(plugin, 'ticker')
        assert [i.width for i in first] != [i.width for i in second]

    def test_rotation_eventually_covers_every_item(self):
        adapter = adapter_with(content_padding=0, max_plugin_width_ratio=1.0)
        widths = (200, 210, 220, 230, 240)
        items = [canvas([(0, w)], width=w) for w in widths]
        plugin = NativePlugin(items)

        seen = set()
        for _ in range(10):
            adapter.invalidate_cache('ticker')
            for img in adapter.get_content(plugin, 'ticker'):
                seen.add(img.width)
        assert seen == set(widths)

    def test_single_oversized_image_is_cropped_to_budget(self):
        adapter = adapter_with(content_padding=0, max_plugin_width_ratio=1.0)
        # A 6898px leaderboard strip, as measured on devpi. Solid ink means
        # there is no blank column to snap to, so the cut lands on the budget.
        plugin = NativePlugin([canvas([(0, 6898)], width=6898)])
        images = adapter.get_content(plugin, 'ledmatrix-leaderboard')
        assert len(images) == 1
        assert images[0].width == DISPLAY_W

    def test_single_image_crop_snaps_to_a_blank_column(self):
        adapter = adapter_with(content_padding=0, max_plugin_width_ratio=1.0)
        # Content blocks with gaps; the cut should land in a gap, not mid-block.
        spans = [(x, x + 90) for x in range(0, 2000, 100)]
        plugin = NativePlugin([canvas(spans, width=2000)])
        images = adapter.get_content(plugin, 'gapped')
        assert images[0].width != DISPLAY_W
        assert abs(images[0].width - DISPLAY_W) <= DISPLAY_W // 16 + 1

    def test_single_image_window_advances_across_cycles(self):
        adapter = adapter_with(content_padding=0, max_plugin_width_ratio=1.0)
        plugin = NativePlugin([canvas([(0, 3000)], width=3000)])
        adapter.get_content(plugin, 'strip')
        assert adapter._item_offsets['strip'] == DISPLAY_W

    def test_budget_of_zero_disables_the_cap(self):
        adapter = adapter_with(content_padding=0, max_plugin_width_ratio=0)
        plugin = NativePlugin([canvas([(0, 6898)], width=6898)])
        assert adapter.get_content(plugin, 'huge')[0].width == 6898

    def test_rotation_resets_when_content_shrinks_to_fit(self):
        adapter = adapter_with(content_padding=0, max_plugin_width_ratio=1.0)
        big = [canvas([(0, 300)], width=300) for _ in range(5)]
        adapter.get_content(NativePlugin(big), 'shrink')
        assert 'shrink' in adapter._item_offsets

        adapter.invalidate_cache('shrink')
        adapter.get_content(NativePlugin([canvas([(0, 100)], width=100)]), 'shrink')
        assert 'shrink' not in adapter._item_offsets

    def test_one_item_wider_than_budget_is_still_shown(self):
        # Never return nothing just because the first whole item overflows.
        adapter = adapter_with(content_padding=0, max_plugin_width_ratio=1.0)
        items = [canvas([(0, 900)], width=900), canvas([(0, 100)], width=100)]
        images = adapter.get_content(NativePlugin(items), 'wide-first')
        assert len(images) >= 1
        assert images[0].width == 900


class TestPluginBoundaryGaps:
    """separator_width belongs between plugins; intra_plugin_gap within one."""

    def _pipeline(self, grouped, **cfg):
        from src.vegas_mode.render_pipeline import RenderPipeline

        class FakeStream:
            def get_grouped_content_for_composition(self):
                return grouped

            def get_active_plugin_ids(self):
                return [pid for pid, _ in grouped]

        class DM:
            width = DISPLAY_W
            height = DISPLAY_H

            def set_scrolling_state(self, *a):
                pass

        return RenderPipeline(VegasModeConfig(**cfg), DM(), FakeStream())

    def test_separator_only_at_plugin_boundaries(self):
        # Two plugins, two rows each. Expect: row row [sep] row row, with the
        # small intra gap inside each pair.
        rows = [Image.new('RGB', (100, DISPLAY_H), (255, 255, 255)) for _ in range(4)]
        pipeline = self._pipeline(
            [('a', rows[:2]), ('b', rows[2:])],
            separator_width=32, intra_plugin_gap=8, lead_in_width=0,
        )
        assert pipeline.compose_scroll_content()

        ink = column_has_ink(pipeline.scroll_helper.cached_image)
        assert ink[:100].all()
        assert not ink[100:108].any()      # intra gap inside plugin a
        assert ink[108:208].all()
        assert not ink[208:240].any()      # separator between a and b
        assert ink[240:340].all()
        assert not ink[340:348].any()      # intra gap inside plugin b
        assert ink[348:448].all()

    def test_total_width_uses_both_gap_sizes(self):
        rows = [Image.new('RGB', (100, DISPLAY_H), (255, 255, 255)) for _ in range(4)]
        pipeline = self._pipeline(
            [('a', rows[:2]), ('b', rows[2:])],
            separator_width=32, intra_plugin_gap=8, lead_in_width=0,
        )
        pipeline.compose_scroll_content()
        # 4 rows + 2 intra gaps + 1 separator
        assert pipeline.scroll_helper.cached_image.width == 400 + 16 + 32

    def test_f1_shaped_case_reclaims_the_chasms(self):
        # 12 rows from one plugin: previously 11 separators at 32px = 352px of
        # gap; now 11 intra gaps at 8px = 88px.
        rows = [Image.new('RGB', (128, DISPLAY_H), (255, 255, 255)) for _ in range(12)]
        pipeline = self._pipeline(
            [('f1-scoreboard', rows)],
            separator_width=32, intra_plugin_gap=8, lead_in_width=0,
        )
        pipeline.compose_scroll_content()
        assert pipeline.scroll_helper.cached_image.width == 12 * 128 + 11 * 8

    def test_single_row_plugin_image_is_not_copied(self):
        row = Image.new('RGB', (100, DISPLAY_H), (255, 255, 255))
        pipeline = self._pipeline([('solo', [row])], lead_in_width=0)
        assert pipeline._join_plugin_rows([row]) is row

    def test_zero_intra_gap_butts_rows_together(self):
        rows = [Image.new('RGB', (50, DISPLAY_H), (255, 255, 255)) for _ in range(3)]
        pipeline = self._pipeline(
            [('a', rows)], separator_width=32, intra_plugin_gap=0, lead_in_width=0)
        pipeline.compose_scroll_content()
        assert pipeline.scroll_helper.cached_image.width == 150
        assert column_has_ink(pipeline.scroll_helper.cached_image).all()

    def test_empty_grouping_fails_composition(self):
        pipeline = self._pipeline([])
        assert pipeline.compose_scroll_content() is False


class TestWidthBudgetCountsGaps:
    def test_budget_accounts_for_intra_plugin_gaps(self):
        # 8 rows of 200px = 1600px of pixels, but with 8px gaps the real
        # occupancy is 1600 + 56 = 1656px. Against a 512px budget the row count
        # must be chosen using the gap-inclusive cost.
        adapter = adapter_with(
            content_padding=0, max_plugin_width_ratio=1.0, intra_plugin_gap=8)
        items = [canvas([(0, 200)], width=200) for _ in range(8)]
        images = adapter.get_content(NativePlugin(items), 'rows')
        n = len(images)
        assert 200 * n + 8 * (n - 1) <= DISPLAY_W

    def test_gap_free_config_fits_more_rows(self):
        items = [canvas([(0, 200)], width=200) for _ in range(8)]
        with_gap = adapter_with(
            content_padding=0, max_plugin_width_ratio=1.0, intra_plugin_gap=64)
        without = adapter_with(
            content_padding=0, max_plugin_width_ratio=1.0, intra_plugin_gap=0)
        assert len(without.get_content(NativePlugin(items), 'r')) >= \
            len(with_gap.get_content(NativePlugin(items), 'r'))


class TestStreamGrouping:
    def _stream(self, segments):
        from src.vegas_mode.stream_manager import StreamManager
        from collections import deque

        sm = StreamManager.__new__(StreamManager)
        import threading
        sm._buffer_lock = threading.RLock()
        sm._active_buffer = deque(segments)
        return sm

    def _seg(self, plugin_id, count, mode=None):
        from src.vegas_mode.stream_manager import ContentSegment
        from src.plugin_system.base_plugin import VegasDisplayMode
        imgs = [Image.new('RGB', (10, 8)) for _ in range(count)]
        return ContentSegment(
            plugin_id=plugin_id, images=imgs, total_width=10 * count,
            display_mode=mode or VegasDisplayMode.SCROLL)

    def test_grouping_preserves_plugin_boundaries(self):
        sm = self._stream([self._seg('a', 3), self._seg('b', 1)])
        grouped = sm.get_grouped_content_for_composition()
        assert [(pid, len(imgs)) for pid, imgs in grouped] == [('a', 3), ('b', 1)]

    def test_static_segments_are_skipped(self):
        from src.plugin_system.base_plugin import VegasDisplayMode
        sm = self._stream([
            self._seg('a', 2),
            self._seg('paused', 1, VegasDisplayMode.STATIC),
            self._seg('b', 1),
        ])
        assert [pid for pid, _ in sm.get_grouped_content_for_composition()] == ['a', 'b']

    def test_imageless_segments_are_skipped(self):
        sm = self._stream([self._seg('a', 0), self._seg('b', 2)])
        assert [pid for pid, _ in sm.get_grouped_content_for_composition()] == ['b']

    def test_flat_accessor_still_matches_grouped_total(self):
        sm = self._stream([self._seg('a', 3), self._seg('b', 2)])
        assert len(sm.get_all_content_for_composition()) == 5


class TestCycleSizing:
    def test_plugins_per_cycle_defaults_above_buffer_ahead(self):
        cfg = VegasModeConfig()
        assert cfg.plugins_per_cycle == 6
        assert cfg.plugins_per_cycle > cfg.buffer_ahead + 1

    def test_plugins_per_cycle_parses(self):
        cfg = VegasModeConfig.from_config(
            {'display': {'vegas_scroll': {'plugins_per_cycle': 10}}})
        assert cfg.plugins_per_cycle == 10

    def test_max_plugin_width_ratio_parses(self):
        cfg = VegasModeConfig.from_config(
            {'display': {'vegas_scroll': {'max_plugin_width_ratio': 1.5}}})
        assert cfg.max_plugin_width_ratio == 1.5

    @pytest.mark.parametrize('overrides,bad_key', [
        ({'plugins_per_cycle': 0}, 'plugins_per_cycle'),
        ({'plugins_per_cycle': 99}, 'plugins_per_cycle'),
        ({'max_plugin_width_ratio': -1.0}, 'max_plugin_width_ratio'),
    ])
    def test_validate_rejects_out_of_range(self, overrides, bad_key):
        errors = VegasModeConfig(**overrides).validate()
        assert any(bad_key in e for e in errors), errors


class TestScrollHelperLeadGap:
    def test_default_lead_gap_is_display_width(self):
        # Standalone tickers rely on scrolling in from off-screen; that
        # behaviour must not change for the many non-Vegas callers.
        sh = ScrollHelper(128, 32)
        sh.create_scrolling_image([Image.new('RGB', (100, 32), (255, 0, 0))],
                                  item_gap=0, element_gap=0)
        assert sh.cached_image.width == 128 + 100
        assert not column_has_ink(sh.cached_image)[:128].any()

    def test_zero_lead_gap_starts_on_content(self):
        sh = ScrollHelper(128, 32)
        sh.create_scrolling_image([Image.new('RGB', (100, 32), (255, 0, 0))],
                                  item_gap=0, element_gap=0, lead_gap=0)
        assert sh.cached_image.width == 100
        assert column_has_ink(sh.cached_image)[0]

    def test_explicit_lead_gap_is_honoured(self):
        sh = ScrollHelper(128, 32)
        sh.create_scrolling_image([Image.new('RGB', (100, 32), (255, 0, 0))],
                                  item_gap=0, element_gap=0, lead_gap=16)
        assert sh.cached_image.width == 116
        ink = column_has_ink(sh.cached_image)
        assert not ink[:16].any()
        assert ink[16:].all()

    def test_negative_lead_gap_is_clamped(self):
        sh = ScrollHelper(128, 32)
        sh.create_scrolling_image([Image.new('RGB', (100, 32), (255, 0, 0))],
                                  item_gap=0, element_gap=0, lead_gap=-50)
        assert sh.cached_image.width == 100

    def test_total_scroll_width_matches_image(self):
        # The cycle-complete check compares against total_scroll_width, so a
        # mismatch here would cut cycles short or overrun them.
        sh = ScrollHelper(128, 32)
        items = [Image.new('RGB', (60, 32), (255, 0, 0)) for _ in range(3)]
        sh.create_scrolling_image(items, item_gap=32, element_gap=0, lead_gap=0)
        assert sh.total_scroll_width == sh.cached_image.width
        assert sh.cached_image.width == 60 * 3 + 32 * 2

    def test_item_gaps_are_unaffected_by_lead_gap(self):
        sh = ScrollHelper(128, 32)
        items = [Image.new('RGB', (10, 32), (255, 0, 0)) for _ in range(2)]
        sh.create_scrolling_image(items, item_gap=20, element_gap=0, lead_gap=0)
        ink = column_has_ink(sh.cached_image)
        assert ink[:10].all()
        assert not ink[10:30].any()
        assert ink[30:40].all()


class TestConfigSurface:
    def test_new_keys_parse_from_config(self):
        cfg = VegasModeConfig.from_config({'display': {'vegas_scroll': {
            'auto_trim': False,
            'trim_threshold': 25,
            'content_padding': 4,
            'min_plugin_width': 64,
            'lead_in_width': 32,
        }}})
        assert cfg.auto_trim is False
        assert cfg.trim_threshold == 25
        assert cfg.content_padding == 4
        assert cfg.min_plugin_width == 64
        assert cfg.lead_in_width == 32

    def test_defaults_favour_trimming(self):
        cfg = VegasModeConfig.from_config({})
        assert cfg.auto_trim is True
        assert cfg.lead_in_width == 0
        assert cfg.content_padding == 8

    def test_round_trips_through_to_dict(self):
        cfg = VegasModeConfig(trim_threshold=20, lead_in_width=64)
        restored = VegasModeConfig.from_config(
            {'display': {'vegas_scroll': cfg.to_dict()}})
        assert restored.trim_threshold == 20
        assert restored.lead_in_width == 64

    def test_update_applies_new_keys(self):
        cfg = VegasModeConfig()
        cfg.update({'display': {'vegas_scroll': {'content_padding': 16}}})
        assert cfg.content_padding == 16

    @pytest.mark.parametrize('overrides,bad_key', [
        ({'trim_threshold': 300}, 'trim_threshold'),
        ({'trim_threshold': -1}, 'trim_threshold'),
        ({'content_padding': -5}, 'content_padding'),
        ({'content_padding': 500}, 'content_padding'),
        ({'min_plugin_width': -1}, 'min_plugin_width'),
        ({'lead_in_width': -1}, 'lead_in_width'),
    ])
    def test_validate_rejects_out_of_range(self, overrides, bad_key):
        errors = VegasModeConfig(**overrides).validate()
        assert any(bad_key in e for e in errors), errors

    def test_valid_config_has_no_errors(self):
        assert VegasModeConfig(
            trim_threshold=10, content_padding=8,
            min_plugin_width=8, lead_in_width=0,
        ).validate() == []
