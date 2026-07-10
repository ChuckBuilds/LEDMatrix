"""Tests for adaptive image fitting (src/adaptive_images.py) and the
LayoutContext image cache."""

import pytest
from PIL import Image

from src.adaptive_images import (
    RESAMPLE_LANCZOS,
    RESAMPLE_NEAREST,
    ImageFitResult,
    draw_fitted_image,
    fit_image,
)
from src.adaptive_layout import LayoutContext, Region
from src.font_manager import FontManager


@pytest.fixture(scope="module")
def font_manager():
    return FontManager({})


@pytest.fixture
def ctx(font_manager):
    return LayoutContext(128, 32, font_manager)


def _solid(w, h, color=(255, 0, 0, 255)):
    return Image.new("RGBA", (w, h), color)


def _padded_logo(ink_w=10, ink_h=10, pad=10):
    """Transparent canvas with a solid ink block in the middle — models a
    logo shipped with generous transparent padding."""
    img = Image.new("RGBA", (ink_w + 2 * pad, ink_h + 2 * pad), (0, 0, 0, 0))
    img.paste(_solid(ink_w, ink_h), (pad, pad))
    return img


class TestFitModes:
    def test_contain_letterboxes_and_upscales(self):
        fit = fit_image(_solid(10, 5), (40, 40))
        assert (fit.width, fit.height) == (40, 20)  # aspect preserved
        assert fit.scale == 4.0

    def test_contain_no_upscale(self):
        fit = fit_image(_solid(10, 5), (40, 40), upscale=False)
        assert (fit.width, fit.height) == (10, 5)
        assert fit.scale == 1.0

    def test_cover_fills_and_crops(self):
        fit = fit_image(_solid(10, 20), (40, 40), mode="cover")
        assert (fit.width, fit.height) == (40, 40)

    def test_cover_top_anchor(self):
        # top half red, bottom half blue; cover-crop a wide box with top anchor
        img = Image.new("RGBA", (20, 40), (0, 0, 255, 255))
        img.paste(_solid(20, 20, (255, 0, 0, 255)), (0, 0))
        fit = fit_image(img, (20, 20), mode="cover", anchor="top")
        assert fit.image.getpixel((10, 5))[:3] == (255, 0, 0)  # kept the top

    def test_fill_height_matches_box_height(self):
        fit = fit_image(_solid(10, 10), (64, 32), mode="fill_height")
        assert fit.height == 32 and fit.width == 32

    def test_fill_height_capped_by_width(self):
        # very wide source: height-fill would overflow the box width
        fit = fit_image(_solid(100, 10), (40, 32), mode="fill_height")
        assert fit.width <= 40

    def test_stretch_exact(self):
        fit = fit_image(_solid(3, 7), (25, 13), mode="stretch")
        assert (fit.width, fit.height) == (25, 13)

    def test_crop_to_ink(self):
        fit = fit_image(_padded_logo(), (30, 30), crop_to_ink=True)
        # 10x10 ink upscaled to fill 30x30 (padding would have kept it small)
        assert (fit.width, fit.height) == (30, 30)
        no_crop = fit_image(_padded_logo(), (30, 30), crop_to_ink=False)
        assert no_crop.width == 30  # whole padded canvas scaled instead

    def test_fully_transparent_source(self):
        img = Image.new("RGBA", (10, 10), (0, 0, 0, 0))
        fit = fit_image(img, (20, 20), crop_to_ink=True)
        assert fit.is_empty

    def test_degenerate_box(self):
        assert fit_image(_solid(10, 10), (0, 20)).is_empty
        assert fit_image(_solid(10, 10), Region(0, 0, 20, 0)).is_empty

    def test_output_always_rgba(self):
        rgb = Image.new("RGB", (10, 10), (1, 2, 3))
        assert fit_image(rgb, (20, 20)).image.mode == "RGBA"

    def test_nearest_keeps_hard_edges(self):
        # 2x2 checker scaled 8x: NEAREST keeps pure colors, LANCZOS blends
        img = Image.new("RGBA", (2, 2), (0, 0, 0, 255))
        img.putpixel((0, 0), (255, 255, 255, 255))
        near = fit_image(img, (16, 16), mode="stretch", resample=RESAMPLE_NEAREST)
        colors = {near.image.getpixel((x, y))[:3] for x in range(16) for y in range(16)}
        assert colors == {(255, 255, 255), (0, 0, 0)}

    def test_unknown_mode_raises(self):
        with pytest.raises(ValueError):
            fit_image(_solid(4, 4), (8, 8), mode="tile")


class TestDrawFittedImage:
    class _DM:
        def __init__(self, w=64, h=32):
            self.image = Image.new("RGB", (w, h), (0, 0, 0))

    def test_pastes_aligned_in_region(self):
        dm = self._DM()
        box = Region(10, 4, 20, 20)
        fit = fit_image(_solid(10, 10), box)
        xy = draw_fitted_image(dm, fit, box)
        assert xy == box.align_xy(fit.width, fit.height)
        assert dm.image.getpixel((xy[0] + 1, xy[1] + 1)) == (255, 0, 0)

    def test_offset_translates(self):
        dm = self._DM()
        box = Region(0, 0, 20, 20)
        fit = fit_image(_solid(10, 10), box)
        x, y = draw_fitted_image(dm, fit, box, align="left", valign="top",
                                 offset=(3, 5))
        assert (x, y) == (3, 5)

    def test_empty_fit_noops(self):
        dm = self._DM()
        fit = fit_image(_solid(10, 10), (0, 0))
        assert draw_fitted_image(dm, fit, Region(0, 0, 10, 10)) is None


class TestContextImageCache:
    def test_size_keyed_hit_and_miss(self, ctx):
        img = _solid(10, 10)
        a = ctx.fit_image(img, (20, 20), cache_key="logo:A")
        assert ctx.fit_image(img, (20, 20), cache_key="logo:A") is a
        b = ctx.fit_image(img, (30, 30), cache_key="logo:A")
        assert b is not a and b.width == 30  # different box size = new entry

    def test_id_keyed_default(self, ctx):
        img = _solid(10, 10)
        a = ctx.fit_image(img, (20, 20))
        assert ctx.fit_image(img, (20, 20)) is a

    def test_id_safety_pins_source(self, ctx):
        # id()-keyed entries must pin the source image so a recycled id
        # can't alias a dead image's cache entry.
        img = _solid(10, 10)
        ctx.fit_image(img, (20, 20))
        pinned = [entry[1] for entry in ctx._image_cache.values()]
        assert img in pinned

    def test_cache_key_entries_do_not_pin(self, ctx):
        img = _solid(10, 10)
        ctx.fit_image(img, (20, 20), cache_key="logo:X")
        key = next(k for k in ctx._image_cache if k[1] == "logo:X")
        assert ctx._image_cache[key][1] is None

    def test_lru_eviction(self, ctx):
        for i in range(ctx._IMAGE_CACHE_MAX + 5):
            ctx.fit_image(_solid(4, 4), (8, 8), cache_key=f"k{i}")
        assert len(ctx._image_cache) == ctx._IMAGE_CACHE_MAX
        assert not any(k[1] == "k0" for k in ctx._image_cache)  # oldest evicted

    def test_clear_cache_clears_images(self, ctx):
        ctx.fit_image(_solid(4, 4), (8, 8), cache_key="k")
        ctx.clear_cache()
        assert len(ctx._image_cache) == 0


class TestBasePluginDrawImage:
    def test_draw_image_end_to_end(self):
        from src.plugin_system.base_plugin import BasePlugin
        from src.plugin_system.testing.mocks import (
            MockCacheManager, MockDisplayManager, MockPluginManager,
        )

        class _P(BasePlugin):
            def update(self):
                pass

            def display(self, force_clear=False):
                pass

        plugin = _P("t", {}, MockDisplayManager(64, 32),
                    MockCacheManager(), MockPluginManager())
        logo = _padded_logo()
        box = plugin.layout.bounds.left_col(32)
        ifit = plugin.draw_image(logo, box, mode="fill_height",
                                 crop_to_ink=True, cache_key="logo:T")
        assert ifit.height == 32
        # pasted onto the mock's canvas
        assert plugin.display_manager.image.getpixel((16, 16)) != (0, 0, 0)
