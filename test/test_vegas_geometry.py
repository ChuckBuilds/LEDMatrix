"""Tests for Vegas mode geometry primitives."""

import numpy as np
import pytest
from PIL import Image

from src.vegas_mode.geometry import (
    DEFAULT_INK_THRESHOLD,
    column_has_ink,
    content_bounds,
    dead_window_stats,
    edge_blank,
    separation_gap,
    trim_to_content,
    window_coverage_stats,
)


def make_img(width, height=8, fill=(0, 0, 0)):
    return Image.new('RGB', (width, height), fill)


def paint(img, x0, x1, color=(255, 255, 255)):
    """Fill columns [x0, x1) with a colour."""
    block = Image.new('RGB', (x1 - x0, img.height), color)
    img.paste(block, (x0, 0))
    return img


class TestColumnHasInk:
    def test_all_black_has_no_ink(self):
        assert not column_has_ink(make_img(16)).any()

    def test_marks_only_painted_columns(self):
        img = paint(make_img(16), 4, 8)
        ink = column_has_ink(img)
        assert ink.tolist() == [False] * 4 + [True] * 4 + [False] * 8

    def test_threshold_is_exclusive(self):
        # A pixel exactly at the threshold is not ink; one above it is.
        at = paint(make_img(4), 0, 4, (DEFAULT_INK_THRESHOLD,) * 3)
        above = paint(make_img(4), 0, 4, (DEFAULT_INK_THRESHOLD + 1,) * 3)
        assert not column_has_ink(at).any()
        assert column_has_ink(above).all()

    def test_single_bright_channel_counts(self):
        img = paint(make_img(4), 1, 2, (0, 0, 200))
        assert column_has_ink(img).tolist() == [False, True, False, False]

    def test_one_lit_pixel_lights_the_column(self):
        img = make_img(4, height=8)
        img.putpixel((2, 5), (255, 255, 255))
        assert column_has_ink(img).tolist() == [False, False, True, False]


class TestContentBounds:
    def test_blank_returns_none(self):
        assert content_bounds(make_img(16)) is None

    def test_finds_inclusive_bounds(self):
        assert content_bounds(paint(make_img(20), 5, 12)) == (5, 11)

    def test_full_width_content(self):
        assert content_bounds(paint(make_img(10), 0, 10)) == (0, 9)

    def test_spans_interior_gap(self):
        img = paint(make_img(30), 2, 5)
        paint(img, 20, 25)
        assert content_bounds(img) == (2, 24)


class TestTrimToContent:
    def test_blank_image_reports_blank(self):
        result = trim_to_content(make_img(512))
        assert result.is_blank
        assert result.image is None
        assert result.width == 0
        assert result.original_width == 512

    def test_trims_both_edges(self):
        result = trim_to_content(paint(make_img(512), 100, 150))
        assert not result.is_blank
        assert result.width == 50
        assert result.trimmed_left == 100
        assert result.trimmed_right == 362
        assert result.removed == 462

    def test_preserves_interior_gap(self):
        # Two content blocks with a wide blank between them: the gap is the
        # plugin's layout and must survive trimming.
        img = paint(make_img(400), 50, 80)
        paint(img, 300, 330)
        result = trim_to_content(img)
        assert result.width == 280  # 50..329 inclusive
        assert column_has_ink(result.image).sum() == 60

    def test_full_width_content_is_returned_unchanged(self):
        img = paint(make_img(128), 0, 128)
        result = trim_to_content(img)
        assert result.image is img
        assert result.removed == 0

    def test_non_black_background_is_never_trimmed(self):
        # A plugin drawing on a dark-but-not-black background fills every
        # column with ink, so there is nothing to reclaim.
        result = trim_to_content(make_img(256, fill=(0, 0, 40)))
        assert result.removed == 0
        assert result.width == 256

    def test_padding_keeps_margin_up_to_what_exists(self):
        result = trim_to_content(paint(make_img(512), 100, 150), padding=8)
        assert result.trimmed_left == 92
        assert result.width == 66  # 50 content + 8 each side

    def test_padding_cannot_widen_beyond_original(self):
        # Content starts 2px in; padding of 8 can only reclaim the 2 available.
        result = trim_to_content(paint(make_img(64), 2, 60), padding=8)
        assert result.trimmed_left == 0
        assert result.trimmed_right == 0
        assert result.width == 64

    def test_height_is_preserved(self):
        result = trim_to_content(paint(make_img(200, height=64), 10, 20))
        assert result.image.height == 64

    def test_real_world_of_the_day_case(self):
        # Measured on devpi: "No Data" occupying 35px of a 512px canvas.
        result = trim_to_content(paint(make_img(512, height=64), 4, 39))
        assert result.width == 35
        assert result.removed == 477


class TestDeadWindowStats:
    def test_fully_inked_ticker_has_no_dead_windows(self):
        stats = dead_window_stats(paint(make_img(400), 0, 400), viewport_width=100)
        assert stats.dead_windows == 0
        assert stats.dead_ratio == 0.0
        assert stats.longest_dead_run == 0

    def test_fully_blank_ticker_is_all_dead(self):
        stats = dead_window_stats(make_img(400), viewport_width=100)
        assert stats.total_windows == 301
        assert stats.dead_windows == 301
        assert stats.dead_ratio == 1.0
        assert stats.longest_dead_run == 301

    def test_leading_blank_run_is_measured(self):
        # 512px of black then solid content: windows fully inside the black
        # stretch are dead. With a 100px viewport, starts 0..412 exist and a
        # window is dead while it holds >=95 blank columns.
        img = paint(make_img(1024), 512, 1024)
        stats = dead_window_stats(img, viewport_width=100)
        assert stats.dead_windows == 418  # starts 0..417 keep >=95 blank cols
        assert stats.longest_dead_run == 418

    def test_narrow_content_island_still_leaves_dead_windows(self):
        # 35px of content in a 512px field, viewed 100px at a time: no window
        # can be 95% blank once it overlaps 35 lit columns, but the windows
        # clear of it are dead.
        img = paint(make_img(512), 100, 135)
        stats = dead_window_stats(img, viewport_width=100)
        assert stats.dead_windows > 0
        assert stats.dead_ratio == pytest.approx(
            stats.dead_windows / stats.total_windows
        )

    def test_step_reduces_sampling(self):
        img = paint(make_img(1000), 500, 1000)
        exact = dead_window_stats(img, viewport_width=100, step=1)
        strided = dead_window_stats(img, viewport_width=100, step=10)
        assert strided.total_windows < exact.total_windows
        # Same underlying shape, so the ratios should stay close.
        assert strided.dead_ratio == pytest.approx(exact.dead_ratio, abs=0.02)

    def test_image_narrower_than_viewport_is_one_window(self):
        stats = dead_window_stats(make_img(50), viewport_width=100)
        assert stats.total_windows == 1
        assert stats.dead_windows == 1

    def test_zero_viewport_is_handled(self):
        stats = dead_window_stats(make_img(50), viewport_width=0)
        assert stats.total_windows == 0
        assert stats.dead_ratio == 0.0

    def test_longest_run_picks_the_larger_of_two_gaps(self):
        # Short blank gap, content, then a long blank gap.
        img = make_img(1000)
        paint(img, 150, 400)
        paint(img, 500, 520)
        stats = dead_window_stats(img, viewport_width=100)
        # The 400..500 gap is only 100 wide; the tail from 520 is 480 wide.
        assert stats.longest_dead_run >= 380


class TestWindowCoverageStats:
    def test_solid_content_is_fully_covered(self):
        stats = window_coverage_stats(paint(make_img(600), 0, 600), viewport_width=100)
        assert stats.mean_ink_ratio == 1.0
        assert stats.min_ink_ratio == 1.0
        assert stats.sparse_windows == 0

    def test_blank_strip_is_entirely_sparse(self):
        stats = window_coverage_stats(make_img(600), viewport_width=100)
        assert stats.mean_ink_ratio == 0.0
        assert stats.sparse_ratio == 1.0

    def test_catches_sliver_windows_that_dead_ratio_misses(self):
        # Narrow content islands separated by more than the viewport. A window
        # holding one whole 40px island carries 472 blank columns — under the
        # 486 needed to count as "dead" — yet only 7.8% ink, so it still reads
        # as an empty panel. Coverage must flag strictly more positions than
        # the dead-window scan does.
        img = paint(make_img(2000), 0, 40)
        paint(img, 1000, 1040)
        dead = dead_window_stats(img, viewport_width=512)
        cover = window_coverage_stats(img, viewport_width=512, sparse_ink_ratio=0.10)
        assert cover.sparse_windows > dead.dead_windows
        assert cover.min_ink_ratio == 0.0

    def test_adjacent_full_width_segments_stay_partially_covered(self):
        # Documents why the dead-window scan alone understated the problem:
        # two 512px segments with mid-canvas content never fully blank the
        # viewport, they just hold it at a thin ~28%.
        img = paint(make_img(1024), 185, 330)
        paint(img, 697, 842)
        dead = dead_window_stats(img, viewport_width=512)
        cover = window_coverage_stats(img, viewport_width=512)
        assert dead.dead_windows == 0
        assert cover.mean_ink_ratio == pytest.approx(0.283, abs=0.01)

    def test_min_ink_ratio_finds_the_worst_position(self):
        # A wide blank tail guarantees at least one totally empty viewport.
        img = paint(make_img(1200), 0, 200)
        stats = window_coverage_stats(img, viewport_width=200)
        assert stats.min_ink_ratio == 0.0
        assert stats.mean_ink_ratio > 0.0

    def test_sparse_threshold_is_respected(self):
        # 40 inked columns in a 200px viewport = 20% coverage everywhere the
        # island is fully inside the window.
        img = paint(make_img(400), 100, 140)
        lenient = window_coverage_stats(img, viewport_width=200, sparse_ink_ratio=0.05)
        strict = window_coverage_stats(img, viewport_width=200, sparse_ink_ratio=0.50)
        assert strict.sparse_windows > lenient.sparse_windows

    def test_step_approximates_exact_scan(self):
        img = paint(make_img(2000), 300, 500)
        paint(img, 1200, 1400)
        exact = window_coverage_stats(img, viewport_width=512, step=1)
        strided = window_coverage_stats(img, viewport_width=512, step=4)
        assert strided.mean_ink_ratio == pytest.approx(exact.mean_ink_ratio, abs=0.01)

    def test_zero_viewport_is_handled(self):
        stats = window_coverage_stats(make_img(50), viewport_width=0)
        assert stats.total_windows == 0
        assert stats.sparse_ratio == 0.0

    def test_image_narrower_than_viewport(self):
        stats = window_coverage_stats(paint(make_img(50), 0, 50), viewport_width=100)
        assert stats.total_windows == 1
        assert stats.mean_ink_ratio == pytest.approx(0.5)


class TestLongestRunHelper:
    @pytest.mark.parametrize("flags,expected", [
        ([], 0),
        ([False, False], 0),
        ([True], 1),
        ([True, True, False, True], 2),
        ([False, True, True, True, False, True], 3),
        ([True, True, True], 3),
    ])
    def test_run_lengths(self, flags, expected):
        from src.vegas_mode.geometry import _longest_true_run
        assert _longest_true_run(np.array(flags, dtype=bool)) == expected


class TestEdgeBlank:
    def test_measures_both_edges(self):
        assert edge_blank(paint(make_img(100), 20, 60)) == (20, 40)

    def test_flush_content_has_no_blank(self):
        assert edge_blank(paint(make_img(50), 0, 50)) == (0, 0)

    def test_blank_image_reports_full_width_both_sides(self):
        # No ink means nothing to be close to.
        assert edge_blank(make_img(64)) == (64, 64)


class TestSeparationGap:
    def test_flush_edges_get_the_full_target(self):
        a = paint(make_img(50), 0, 50)
        b = paint(make_img(50), 0, 50)
        assert separation_gap(a, b, target=24) == 24

    def test_existing_margins_reduce_the_added_gap(self):
        # 8px blank on each facing edge already covers 16 of the 24 target.
        a = paint(make_img(50), 0, 42)
        b = paint(make_img(50), 8, 50)
        assert separation_gap(a, b, target=24) == 8

    def test_ample_existing_margin_adds_nothing(self):
        a = paint(make_img(100), 0, 60)
        b = paint(make_img(100), 40, 100)
        assert separation_gap(a, b, target=24) == 0

    def test_minimum_is_a_floor(self):
        a = paint(make_img(100), 0, 60)
        b = paint(make_img(100), 40, 100)
        assert separation_gap(a, b, target=24, minimum=4) == 4

    def test_never_negative(self):
        a = paint(make_img(200), 0, 10)
        b = paint(make_img(200), 190, 200)
        assert separation_gap(a, b, target=8) == 0

    def test_sports_card_case_gets_real_separation(self):
        # The reported problem: cards drawn edge to edge sat 8px apart under a
        # flat gap; measured separation lifts them to the 24px target.
        card = paint(make_img(150), 0, 150)
        assert separation_gap(card, card, target=24, minimum=8) == 24
