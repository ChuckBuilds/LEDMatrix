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
        # Two plugins, two rows each. Expect: row row [sep] row row. These rows
        # are drawn flush to their edges, so the intra-plugin gap is the full
        # min_content_separation.
        rows = [Image.new('RGB', (100, DISPLAY_H), (255, 255, 255)) for _ in range(4)]
        pipeline = self._pipeline(
            [('a', rows[:2]), ('b', rows[2:])],
            separator_width=32, intra_plugin_gap=8, min_content_separation=24,
            lead_in_width=0,
        )
        assert pipeline.compose_scroll_content()

        ink = column_has_ink(pipeline.scroll_helper.cached_image)
        assert ink[:100].all()
        assert not ink[100:124].any()      # measured gap inside plugin a
        assert ink[124:224].all()
        assert not ink[224:256].any()      # separator between a and b
        assert ink[256:356].all()
        assert not ink[356:380].any()      # measured gap inside plugin b
        assert ink[380:480].all()

    def test_total_width_uses_both_gap_sizes(self):
        rows = [Image.new('RGB', (100, DISPLAY_H), (255, 255, 255)) for _ in range(4)]
        pipeline = self._pipeline(
            [('a', rows[:2]), ('b', rows[2:])],
            separator_width=32, intra_plugin_gap=8, min_content_separation=24,
            lead_in_width=0,
        )
        pipeline.compose_scroll_content()
        # 4 rows + 2 measured intra gaps (24 each) + 1 separator
        assert pipeline.scroll_helper.cached_image.width == 400 + 48 + 32

    def test_f1_shaped_case_stays_below_the_separator_width(self):
        # 12 rows from one plugin. Previously each boundary got the full 32px
        # separator (352px of gap); rows are now spaced by measured separation,
        # which for flush rows is min_content_separation.
        rows = [Image.new('RGB', (128, DISPLAY_H), (255, 255, 255)) for _ in range(12)]
        pipeline = self._pipeline(
            [('f1-scoreboard', rows)],
            separator_width=32, intra_plugin_gap=8, min_content_separation=24,
            lead_in_width=0,
        )
        pipeline.compose_scroll_content()
        width = pipeline.scroll_helper.cached_image.width
        assert width == 12 * 128 + 11 * 24
        assert width < 12 * 128 + 11 * 32  # cheaper than the old flat separator

    def test_single_row_plugin_image_is_not_copied(self):
        row = Image.new('RGB', (100, DISPLAY_H), (255, 255, 255))
        pipeline = self._pipeline([('solo', [row])], lead_in_width=0)
        assert pipeline._join_plugin_rows([row]) is row

    def test_rows_butt_together_only_when_both_gap_settings_are_zero(self):
        # intra_plugin_gap alone no longer decides this: min_content_separation
        # would still push flush rows apart, which is the point of it.
        rows = [Image.new('RGB', (50, DISPLAY_H), (255, 255, 255)) for _ in range(3)]
        pipeline = self._pipeline(
            [('a', rows)], separator_width=32, intra_plugin_gap=0,
            min_content_separation=0, lead_in_width=0)
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


class TestApiBoundsMatchValidate:
    """
    The web API's accepted range for each Vegas setting must agree with
    VegasModeConfig.validate(), which is what actually gates Vegas starting.

    A looser API bound saves a value with a 200 and then makes
    VegasModeCoordinator.start() bail out with only a log line, so the ticker
    silently never runs. A tighter one rejects a legitimate value with a 400.
    Both happened before this test existed.
    """

    # (config key, min, max) as validate() enforces them.
    EXPECTED = {
        'scroll_speed': (1, 200),
        'separator_width': (0, 128),
        'intra_plugin_gap': (0, 128),
        'target_fps': (30, 200),
        'buffer_ahead': (1, 5),
        'trim_threshold': (0, 254),
        'content_padding': (0, 128),
        'min_plugin_width': (0, 512),
        'plugins_per_cycle': (1, 50),
    }

    def _api_numeric_fields(self):
        """Extract the numeric_fields map from api_v3 without importing Flask."""
        import ast
        import pathlib
        src = pathlib.Path('web_interface/blueprints/api_v3.py').read_text()
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign):
                continue
            targets = [t.id for t in node.targets if isinstance(t, ast.Name)]
            if 'numeric_fields' not in targets:
                continue
            if not isinstance(node.value, ast.Dict):
                continue
            found = {}
            for key, value in zip(node.value.keys, node.value.values):
                if not isinstance(key, ast.Constant):
                    continue
                if not str(key.value).startswith('vegas_'):
                    break
                cfg_key, lo, hi = [ast.literal_eval(e) for e in value.elts]
                found[cfg_key] = (lo, hi)
            if found:
                return found
        raise AssertionError("could not locate the vegas numeric_fields map")

    def test_every_bound_matches_validate(self):
        api = self._api_numeric_fields()
        mismatched = {
            key: (api[key], expected)
            for key, expected in self.EXPECTED.items()
            if key in api and api[key] != expected
        }
        assert not mismatched, f"API bounds disagree with validate(): {mismatched}"

    @pytest.mark.parametrize('key,bounds', sorted(EXPECTED.items()))
    def test_validate_accepts_both_endpoints(self, key, bounds):
        lo, hi = bounds
        for value in (lo, hi):
            cfg = VegasModeConfig(**{key: value})
            errors = [e for e in cfg.validate() if key in e]
            assert not errors, f"{key}={value} should be valid, got {errors}"

    @pytest.mark.parametrize('key,bounds', sorted(EXPECTED.items()))
    def test_validate_rejects_just_outside(self, key, bounds):
        lo, hi = bounds
        for value in (lo - 1, hi + 1):
            cfg = VegasModeConfig(**{key: value})
            errors = [e for e in cfg.validate() if key in e]
            assert errors, f"{key}={value} should be rejected"


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


class TestRenderWidthResolution:
    """Vegas asks plugins to render narrower so layouts compact, not crop."""

    class CfgPlugin:
        def __init__(self, cfg=None):
            self.config = cfg or {}

        def get_vegas_content(self):
            return None

    def test_defaults_to_full_width(self):
        adapter = adapter_with()
        assert adapter.resolve_render_width(self.CfgPlugin(), 'p') == DISPLAY_W

    def test_global_percentage_applies(self):
        adapter = adapter_with(render_width_pct=50)
        assert adapter.resolve_render_width(self.CfgPlugin(), 'p') == DISPLAY_W // 2

    def test_per_plugin_override_beats_global(self):
        adapter = adapter_with(render_width_pct=50)
        plugin = self.CfgPlugin({'vegas_width_pct': 30})
        assert adapter.resolve_render_width(plugin, 'p') == int(DISPLAY_W * 0.3)

    def test_per_plugin_can_opt_back_to_full_width(self):
        adapter = adapter_with(render_width_pct=30)
        plugin = self.CfgPlugin({'vegas_width_pct': 100})
        assert adapter.resolve_render_width(plugin, 'p') == DISPLAY_W

    @pytest.mark.parametrize('bad', [0, 5, 150, -10, 'wide', None, ''])
    def test_invalid_override_falls_back_to_global(self, bad):
        adapter = adapter_with(render_width_pct=50)
        plugin = self.CfgPlugin({'vegas_width_pct': bad})
        assert adapter.resolve_render_width(plugin, 'p') == DISPLAY_W // 2

    def test_plugin_without_config_is_safe(self):
        adapter = adapter_with(render_width_pct=50)

        class NoCfg:
            pass

        assert adapter.resolve_render_width(NoCfg(), 'p') == DISPLAY_W // 2

    def test_never_exceeds_panel_width(self):
        adapter = adapter_with(render_width_pct=100)
        assert adapter.resolve_render_width(self.CfgPlugin(), 'p') <= DISPLAY_W


class TestMeasuredSeparation:
    """Rows are spaced by measured blank, not a flat additive gap."""

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

    def test_flush_rows_are_pushed_to_the_target(self):
        # The reported problem: score cards drawn edge to edge sat 8px apart.
        rows = [Image.new('RGB', (100, DISPLAY_H), (255, 255, 255)) for _ in range(3)]
        p = self._pipeline([('scores', rows)],
                           intra_plugin_gap=8, min_content_separation=24,
                           lead_in_width=0)
        block = p._join_plugin_rows(rows)
        assert block.width == 300 + 24 * 2
        ink = column_has_ink(block)
        assert not ink[100:124].any()
        assert ink[124:224].all()

    def test_rows_with_margins_are_not_pushed_further(self):
        # Each row already carries 12px blank per side = 24px facing total,
        # which meets the target, so only the floor is added.
        rows = [canvas([(12, 88)], width=100) for _ in range(3)]
        p = self._pipeline([('padded', rows)],
                           intra_plugin_gap=0, min_content_separation=24,
                           lead_in_width=0)
        block = p._join_plugin_rows(rows)
        assert block.width == 300

    def test_floor_still_applies_when_target_is_met(self):
        rows = [canvas([(12, 88)], width=100) for _ in range(2)]
        p = self._pipeline([('padded', rows)],
                           intra_plugin_gap=6, min_content_separation=24,
                           lead_in_width=0)
        assert p._join_plugin_rows(rows).width == 200 + 6

    def test_gaps_are_per_pair_not_uniform(self):
        # Flush row then a padded row: the two gaps must differ.
        flush = Image.new('RGB', (100, DISPLAY_H), (255, 255, 255))
        padded = canvas([(20, 80)], width=100)
        p = self._pipeline([('mixed', [flush, padded, flush])],
                           intra_plugin_gap=0, min_content_separation=24,
                           lead_in_width=0)
        block = p._join_plugin_rows([flush, padded, flush])
        # gap1: flush right(0) + padded left(20) = 20 -> add 4
        # gap2: padded right(20) + flush left(0) = 20 -> add 4
        assert block.width == 300 + 4 + 4

    def test_zero_target_falls_back_to_the_floor(self):
        rows = [Image.new('RGB', (50, DISPLAY_H), (255, 255, 255)) for _ in range(2)]
        p = self._pipeline([('a', rows)],
                           intra_plugin_gap=5, min_content_separation=0,
                           lead_in_width=0)
        assert p._join_plugin_rows(rows).width == 100 + 5


class TestNewConfigKeys:
    def test_render_width_pct_parses(self):
        cfg = VegasModeConfig.from_config(
            {'display': {'vegas_scroll': {'render_width_pct': 40}}})
        assert cfg.render_width_pct == 40

    def test_min_content_separation_parses(self):
        cfg = VegasModeConfig.from_config(
            {'display': {'vegas_scroll': {'min_content_separation': 16}}})
        assert cfg.min_content_separation == 16

    def test_defaults(self):
        cfg = VegasModeConfig()
        assert cfg.render_width_pct == 100
        assert cfg.min_content_separation == 24

    @pytest.mark.parametrize('overrides,bad_key', [
        ({'render_width_pct': 5}, 'render_width_pct'),
        ({'render_width_pct': 101}, 'render_width_pct'),
        ({'min_content_separation': -1}, 'min_content_separation'),
        ({'min_content_separation': 300}, 'min_content_separation'),
    ])
    def test_validate_rejects_out_of_range(self, overrides, bad_key):
        errors = VegasModeConfig(**overrides).validate()
        assert any(bad_key in e for e in errors), errors
