"""
Tests for src/vegas_mode/plugin_adapter.py

Covers PluginAdapter._strip_scroll_padding(): the heuristic that crops a
plugin's own baked-in leading/trailing blank margins before Vegas mode
composites the content, so vegas_scroll.separator_width is the only gap
applied between items.
"""

import logging

import pytest
from PIL import Image

from src.common.scroll_helper import ScrollHelper
from src.vegas_mode.plugin_adapter import PluginAdapter


class FakeDisplayManager:
    width = 64
    height = 32


class FakePlugin:
    def __init__(self, scroll_helper):
        self.scroll_helper = scroll_helper


@pytest.fixture
def adapter():
    return PluginAdapter(FakeDisplayManager())


def _solid(width, height, color):
    return Image.new('RGB', (width, height), color)


class TestStripScrollPadding:
    def test_leading_pad_from_create_scrolling_image_is_stripped(self, adapter):
        sh = ScrollHelper(64, 32)
        item = _solid(40, 32, (200, 50, 50))
        sh.create_scrolling_image([item], item_gap=10, element_gap=0)

        images = adapter._get_scroll_helper_content(FakePlugin(sh), "ticker")
        assert images[0].width == 40
        assert images[0].getpixel((0, 0)) == (200, 50, 50)

    def test_leading_and_trailing_pad_both_stripped(self, adapter):
        sh = ScrollHelper(64, 32)
        content_w = 80
        full = _solid(64 + content_w + 64, 32, (0, 0, 0))
        full.paste(_solid(content_w, 32, (10, 220, 30)), (64, 0))
        sh.set_scrolling_image(full)

        images = adapter._get_scroll_helper_content(FakePlugin(sh), "ticker")
        assert images[0].width == content_w
        assert images[0].getpixel((0, 0)) == (10, 220, 30)
        assert images[0].getpixel((content_w - 1, 0)) == (10, 220, 30)

    def test_leading_only_pad_stripped_trailing_content_kept(self, adapter):
        sh = ScrollHelper(64, 32)
        content_w = 80
        full = _solid(64 + content_w, 32, (0, 0, 0))
        full.paste(_solid(content_w, 32, (5, 5, 250)), (64, 0))
        sh.set_scrolling_image(full)

        images = adapter._get_scroll_helper_content(FakePlugin(sh), "ticker")
        assert images[0].width == content_w
        assert images[0].getpixel((0, 0)) == (5, 5, 250)

    def test_trailing_only_pad_stripped_leading_content_kept(self, adapter):
        sh = ScrollHelper(64, 32)
        content_w = 80
        full = _solid(content_w + 64, 32, (0, 0, 0))
        full.paste(_solid(content_w, 32, (5, 5, 250)), (0, 0))
        sh.set_scrolling_image(full)

        images = adapter._get_scroll_helper_content(FakePlugin(sh), "ticker")
        assert images[0].width == content_w
        assert images[0].getpixel((0, 0)) == (5, 5, 250)

    def test_no_margin_image_left_untouched(self, adapter):
        sh = ScrollHelper(64, 32)
        raw = _solid(150, 32, (5, 5, 5))
        raw.paste(_solid(50, 32, (123, 45, 67)), (0, 0))
        sh.set_scrolling_image(raw)

        images = adapter._get_scroll_helper_content(FakePlugin(sh), "no_margin")
        assert images[0].width == 150

    def test_degenerate_all_black_image_left_untouched(self, adapter):
        sh = ScrollHelper(64, 32)
        sh.set_scrolling_image(_solid(50, 32, (0, 0, 0)))

        images = adapter._get_scroll_helper_content(FakePlugin(sh), "all_black")
        assert images[0].width == 50

    def test_missing_display_width_attribute_left_untouched(self, adapter):
        sh = ScrollHelper(64, 32)
        item = _solid(40, 32, (200, 50, 50))
        sh.create_scrolling_image([item], item_gap=10, element_gap=0)
        original_width = sh.cached_image.width
        del sh.display_width

        images = adapter._get_scroll_helper_content(FakePlugin(sh), "ticker")
        assert images[0].width == original_width

    def test_pad_width_not_smaller_than_image_left_untouched(self, adapter):
        sh = ScrollHelper(64, 32)
        sh.set_scrolling_image(_solid(64, 32, (0, 0, 0)))

        images = adapter._get_scroll_helper_content(FakePlugin(sh), "narrow")
        assert images[0].width == 64

    def test_both_edges_matching_logs_warning(self, adapter, caplog):
        sh = ScrollHelper(64, 32)
        content_w = 80
        full = _solid(64 + content_w + 64, 32, (0, 0, 0))
        full.paste(_solid(content_w, 32, (10, 220, 30)), (64, 0))
        sh.set_scrolling_image(full)

        with caplog.at_level(logging.WARNING, logger="src.vegas_mode.plugin_adapter"):
            adapter._get_scroll_helper_content(FakePlugin(sh), "ticker")

        assert any("Stripping scroll_helper padding" in r.message for r in caplog.records)

    def test_single_edge_match_logs_info_not_warning(self, adapter, caplog):
        sh = ScrollHelper(64, 32)
        item = _solid(40, 32, (200, 50, 50))
        sh.create_scrolling_image([item], item_gap=10, element_gap=0)

        with caplog.at_level(logging.INFO, logger="src.vegas_mode.plugin_adapter"):
            adapter._get_scroll_helper_content(FakePlugin(sh), "ticker")

        strip_records = [r for r in caplog.records if "Stripping scroll_helper padding" in r.message]
        assert len(strip_records) == 1
        assert strip_records[0].levelno == logging.INFO
