"""
Tests for src/font_manager.py — FontManager loading, caching, fallback,
and BDF handling, exercised against the real bundled fonts in assets/fonts.

This file replaces an earlier version whose tests were try/except blocks
ending in `assert True` — they executed the code but could not fail. Every
test here asserts observable behavior: returned font types, cache identity,
fallback selection, and BDF native-size reading.
"""

import freetype
import pytest
from PIL import ImageFont

from src.font_manager import FontManager


@pytest.fixture
def fm():
    """A FontManager over the real assets/fonts catalog."""
    return FontManager({})


class TestCatalog:
    def test_bundled_common_fonts_are_registered(self, fm):
        # These aliases are hardcoded in FontManager.common_fonts and the
        # files ship in assets/fonts — all three must resolve.
        for family in ("press_start", "four_by_six", "five_by_seven"):
            assert family in fm.font_catalog, f"{family} missing from catalog"

    def test_catalog_families_are_lowercase_filenames(self, fm):
        # _scan_fonts_directory lowercases the filename stem.
        assert all(name == name.lower() for name in fm.font_catalog)


class TestGetFont:
    def test_ttf_family_returns_usable_pil_font(self, fm):
        font = fm.get_font("press_start", 8)
        assert isinstance(font, ImageFont.FreeTypeFont)
        # Usable: it can measure text.
        bbox = font.getbbox("Hi")
        assert bbox[2] > bbox[0]

    def test_bdf_family_returns_freetype_face(self, fm):
        font = fm.get_font("five_by_seven", 7)
        assert isinstance(font, freetype.Face)

    def test_repeat_call_returns_cached_identity(self, fm):
        first = fm.get_font("press_start", 8)
        hits_before = fm.performance_stats["cache_hits"]
        second = fm.get_font("press_start", 8)
        assert second is first
        assert fm.performance_stats["cache_hits"] == hits_before + 1

    def test_different_sizes_get_distinct_cache_entries(self, fm):
        small = fm.get_font("press_start", 8)
        large = fm.get_font("press_start", 16)
        assert small is not large
        assert "press_start_8" in fm.font_cache
        assert "press_start_16" in fm.font_cache

    def test_unknown_family_falls_back_to_default_without_raising(self, fm):
        failed_before = fm.performance_stats["failed_loads"]
        font = fm.get_font("no-such-family", 10)
        # The documented fallback is PIL's default font (whose concrete type
        # varies across Pillow versions), recorded as a failed load. It must
        # still be usable for measurement.
        assert type(font) is type(ImageFont.load_default())
        assert font.getbbox("Hi")[2] > 0
        assert fm.performance_stats["failed_loads"] == failed_before + 1

    def test_corrupt_font_file_falls_back_to_default(self, fm, tmp_path):
        bad = tmp_path / "broken.ttf"
        bad.write_text("this is not a font file")
        fm.font_catalog["broken"] = str(bad)
        failed_before = fm.performance_stats["failed_loads"]
        font = fm.get_font("broken", 10)
        assert type(font) is type(ImageFont.load_default())
        assert font.getbbox("Hi")[2] > 0
        assert fm.performance_stats["failed_loads"] == failed_before + 1


class TestBdfNativeSize:
    def test_five_by_seven_reports_native_height(self, fm):
        # 5x7.bdf declares a 7px strike; requesting other sizes still renders
        # the native size, so callers need this to know the truth.
        assert fm.get_native_bdf_size("five_by_seven") == 7

    def test_ttf_family_has_no_native_size(self, fm):
        assert fm.get_native_bdf_size("press_start") is None

    def test_unknown_family_has_no_native_size(self, fm):
        assert fm.get_native_bdf_size("no-such-family") is None


class TestMeasureText:
    def test_ttf_measurement_is_positive_and_cached(self, fm):
        font = fm.get_font("press_start", 8)
        width, height, baseline = fm.measure_text("SCORE", font)
        assert width > 0 and height > 0
        # Cached: same result object path on second call.
        assert fm.measure_text("SCORE", font) == (width, height, baseline)
        assert ("SCORE", id(font)) in fm.metrics_cache

    def test_longer_text_measures_wider(self, fm):
        font = fm.get_font("press_start", 8)
        short, _, _ = fm.measure_text("AB", font)
        long, _, _ = fm.measure_text("ABCD", font)
        assert long > short


class TestCacheLifecycle:
    def test_clear_cache_empties_both_caches(self, fm):
        font = fm.get_font("press_start", 8)
        fm.measure_text("X", font)
        assert fm.font_cache and fm.metrics_cache
        fm.clear_cache()
        assert not fm.font_cache
        assert not fm.metrics_cache

    def test_reload_config_bumps_generation_and_clears(self, fm):
        fm.get_font("press_start", 8)
        gen_before = fm.cache_generation
        fm.reload_config({})
        assert fm.cache_generation == gen_before + 1
        assert not fm.font_cache
