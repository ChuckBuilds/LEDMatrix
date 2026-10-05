"""
Tests for src/font_manager.py — FontManager loading, caching, fallback,
and BDF handling, exercised against the real bundled fonts in assets/fonts.

This file replaces an earlier version whose tests were try/except blocks
ending in `assert True` — they executed the code but could not fail. Every
test here asserts observable behavior: returned font types, cache identity,
fallback selection, and BDF native-size reading.
"""

import hashlib
import io
import json
import shutil
import zipfile
from unittest.mock import MagicMock

import freetype
import pytest
from PIL import ImageFont

import src.font_manager as fm_module
from src.common.font_layout import resolve_asset_path
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

    def test_bdf_at_a_size_it_lacks_uses_its_native_strike(self, fm):
        # FreeType rejects any size but the strike's own. This used to hand
        # back PIL's default font, a different typeface, for 5x7 at 8 or 10.
        for size in (8, 10):
            font = fm.get_font("five_by_seven", size)
            assert isinstance(font, freetype.Face), size
            assert font.size.y_ppem == 7

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

    def test_clear_cache_bumps_generation(self, fm):
        # Layout contexts and font-usage results are keyed off
        # cache_generation; clear_cache used to drop the fonts without
        # telling them.
        gen_before = fm.cache_generation
        fm.clear_cache()
        assert fm.cache_generation == gen_before + 1


class TestPluginFonts:
    """plugin:// sources resolve against the plugin's own directory, which
    by default lives under plugin-repos/, not a cwd-relative plugins/."""

    MANIFEST = {"fonts": [{"family": "bundled", "source": "plugin://fonts/Bundled.ttf"}]}

    @staticmethod
    def _plugin_with_font(root, name="my-plugin"):
        plugin_dir = root / name
        (plugin_dir / "fonts").mkdir(parents=True)
        (plugin_dir / "manifest.json").write_text(json.dumps({"id": "my-plugin"}))
        shutil.copy(resolve_asset_path("assets/fonts/PressStart2P-Regular.ttf"),
                    plugin_dir / "fonts" / "Bundled.ttf")
        return plugin_dir

    def test_font_resolves_under_the_given_plugin_dir(self, fm, tmp_path):
        plugin_dir = self._plugin_with_font(tmp_path / "plugin-repos")

        assert fm.register_plugin_fonts("my-plugin", self.MANIFEST, plugin_dir=plugin_dir)

        assert fm.font_catalog["my-plugin::bundled"] == str(plugin_dir / "fonts" / "Bundled.ttf")
        font = fm.resolve_font("x.y", "bundled", 8, plugin_id="my-plugin")
        assert isinstance(font, ImageFont.FreeTypeFont)

    def test_without_a_plugin_dir_the_configured_directory_is_searched(self, tmp_path):
        plugins_root = tmp_path / "installed"
        plugin_dir = self._plugin_with_font(plugins_root, name="ledmatrix-my-plugin")
        fm = FontManager({"plugin_system": {"plugins_directory": str(plugins_root)}})

        assert fm.register_plugin_fonts("my-plugin", self.MANIFEST)

        assert fm.font_catalog["my-plugin::bundled"] == str(plugin_dir / "fonts" / "Bundled.ttf")


class TestForgetPluginFonts:
    """forget_plugin_fonts drops what a plugin's manifest registered. Before
    it, unloading a plugin left its fonts resolvable and its cached font
    objects alive until a restart."""

    @staticmethod
    def _register(fm, root, plugin_id, family="bundled"):
        plugin_dir = root / plugin_id
        (plugin_dir / "fonts").mkdir(parents=True, exist_ok=True)
        font_file = plugin_dir / "fonts" / f"{family}.ttf"
        if not font_file.exists():  # a loaded font may hold it open (Windows)
            shutil.copy(resolve_asset_path("assets/fonts/PressStart2P-Regular.ttf"), font_file)
        manifest = {"fonts": [{"family": family, "source": f"plugin://fonts/{family}.ttf"}]}
        assert fm.register_plugin_fonts(plugin_id, manifest, plugin_dir=plugin_dir)
        return plugin_dir

    @staticmethod
    def _entries_of(fm, plugin_id):
        prefix = f"{plugin_id}::"
        return {
            "plugin_fonts": plugin_id in fm.plugin_fonts,
            "plugin_font_catalogs": plugin_id in fm.plugin_font_catalogs,
            "font_catalog": [k for k in fm.font_catalog if k.startswith(prefix)],
            "font_cache": [k for k in fm.font_cache if k.startswith(prefix)],
        }

    NONE = {"plugin_fonts": False, "plugin_font_catalogs": False,
            "font_catalog": [], "font_cache": []}

    def test_unload_leaves_no_plugin_entries(self, fm, tmp_path):
        self._register(fm, tmp_path, "alpha")
        fm.resolve_font("alpha.title", "bundled", 8, plugin_id="alpha")
        fm.get_font("alpha::bundled", 10)
        assert self._entries_of(fm, "alpha")["font_cache"]  # cached before
        gen = fm.cache_generation

        assert fm.forget_plugin_fonts("alpha") is True

        assert self._entries_of(fm, "alpha") == self.NONE
        assert fm.cache_generation == gen + 1
        # The family no longer resolves to the plugin's file.
        assert fm.font_catalog.get("alpha::bundled") is None

    def test_other_plugins_and_core_fonts_are_untouched(self, fm, tmp_path):
        self._register(fm, tmp_path, "alpha")
        self._register(fm, tmp_path, "beta")
        # A plugin whose id is a prefix of another's must not take it along.
        self._register(fm, tmp_path, "alpha-two")
        for pid in ("alpha", "beta", "alpha-two"):
            fm.get_font(f"{pid}::bundled", 8)
        core_font = fm.get_font("press_start", 8)
        beta_before = self._entries_of(fm, "beta")
        alpha_two_before = self._entries_of(fm, "alpha-two")

        fm.forget_plugin_fonts("alpha")

        assert self._entries_of(fm, "beta") == beta_before
        assert self._entries_of(fm, "alpha-two") == alpha_two_before
        assert fm.get_font("press_start", 8) is core_font

    def test_reload_re_registers_cleanly(self, fm, tmp_path):
        plugin_dir = self._register(fm, tmp_path, "alpha")
        old = fm.get_font("alpha::bundled", 8)
        fm.forget_plugin_fonts("alpha")

        self._register(fm, tmp_path, "alpha")

        assert fm.font_catalog["alpha::bundled"] == str(plugin_dir / "fonts" / "bundled.ttf")
        font = fm.resolve_font("alpha.title", "bundled", 8, plugin_id="alpha")
        assert isinstance(font, ImageFont.FreeTypeFont)
        assert font is not old  # loaded fresh, not the dropped cache entry

    def test_a_family_the_new_manifest_drops_stops_resolving(self, fm, tmp_path):
        self._register(fm, tmp_path, "alpha", family="old_face")
        fm.forget_plugin_fonts("alpha")
        self._register(fm, tmp_path, "alpha", family="new_face")

        assert "alpha::old_face" not in fm.font_catalog
        assert "alpha::new_face" in fm.font_catalog

    def test_unknown_plugin_is_a_no_op(self, fm):
        catalog = dict(fm.font_catalog)
        gen = fm.cache_generation

        assert fm.forget_plugin_fonts("never-registered") is False

        assert fm.font_catalog == catalog
        assert fm.cache_generation == gen


class TestPluginManagerReloadFonts:
    """Through PluginManager: unloading a plugin forgets its manifest fonts,
    and reload_plugin (unload + load) registers them again so they resolve."""

    PLUGIN_ID = "font-reload-demo"
    MODULE = "plugin_font_reload_demo"

    def test_unload_forgets_and_reload_resolves(self, tmp_path):
        import sys
        from src.plugin_system.plugin_manager import PluginManager

        plugins_dir = tmp_path / "plugins"
        plugin_dir = plugins_dir / self.PLUGIN_ID
        (plugin_dir / "fonts").mkdir(parents=True)
        shutil.copy(resolve_asset_path("assets/fonts/PressStart2P-Regular.ttf"),
                    plugin_dir / "fonts" / "Bundled.ttf")
        manifest = {"id": self.PLUGIN_ID, "name": "Demo", "class_name": "Demo",
                    "entry_point": "manager.py",
                    "fonts": {"fonts": [{"family": "bundled",
                                         "source": "plugin://fonts/Bundled.ttf"}]}}
        (plugin_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        (plugin_dir / "manager.py").write_text(
            "class Demo:\n"
            "    def __init__(self, plugin_id, config, display_manager, cache_manager, plugin_manager):\n"
            "        self.enabled = True\n", encoding="utf-8")

        pm = PluginManager(plugins_dir=str(plugins_dir))
        fm = FontManager({})
        pm.font_manager = fm
        pm.plugin_manifests[self.PLUGIN_ID] = manifest
        key = f"{self.PLUGIN_ID}::bundled"
        try:
            assert pm.load_plugin(self.PLUGIN_ID) is True
            assert key in fm.font_catalog
            fm.register_manager_font(self.PLUGIN_ID, "demo.title", "bundled", 8)
            old = fm.resolve_font("demo.title", "bundled", 8, plugin_id=self.PLUGIN_ID)

            assert pm.unload_plugin(self.PLUGIN_ID) is True
            assert self.PLUGIN_ID not in fm.plugin_fonts
            assert self.PLUGIN_ID not in fm.plugin_font_catalogs
            assert key not in fm.font_catalog
            assert not [k for k in fm.font_cache if k.startswith(f"{self.PLUGIN_ID}::")]
            assert self.PLUGIN_ID not in fm.manager_fonts

            assert pm.reload_plugin(self.PLUGIN_ID) is True
            assert fm.font_catalog[key] == str(plugin_dir / "fonts" / "Bundled.ttf")
            font = fm.resolve_font("demo.title", "bundled", 8, plugin_id=self.PLUGIN_ID)
            assert isinstance(font, ImageFont.FreeTypeFont)
            assert font is not old
        finally:
            sys.modules.pop(self.MODULE, None)


class TestDownloadFont:
    """_download_font: plugin fonts declared by URL, cached in temp_font_dir."""

    URL = "https://fonts.example/pack.zip"

    @staticmethod
    def _zip_bytes():
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("MyFont.ttf", b"not really a font")
        return buf.getvalue()

    @staticmethod
    def _response(chunks):
        response = MagicMock()
        response.raise_for_status.return_value = None
        response.iter_content.side_effect = lambda chunk_size: iter(chunks)
        return response

    def test_a_zip_is_served_as_its_extracted_font_after_a_restart(self, fm, tmp_path):
        # The state a previous run leaves: the .zip and its extracted font.
        # The cache check used to find the .zip first and register the
        # archive itself as the font.
        fm.temp_font_dir = tmp_path
        url_hash = hashlib.sha256(self.URL.encode()).hexdigest()[:16]
        zip_path = tmp_path / f"pack_{url_hash}.zip"
        zip_path.write_bytes(self._zip_bytes())
        extract_dir = tmp_path / f"pack_{url_hash}"
        with zipfile.ZipFile(zip_path) as zf:
            zf.extractall(extract_dir)

        path = fm._download_font(self.URL, {"family": "pack"})

        assert path == str(extract_dir / "MyFont.ttf")

    def test_download_has_a_timeout_and_lands_atomically(self, fm, tmp_path, monkeypatch):
        fm.temp_font_dir = tmp_path
        get = MagicMock(return_value=self._response([self._zip_bytes()]))
        monkeypatch.setattr(fm_module.requests, "get", get)

        path = fm._download_font(self.URL, {"family": "pack"})

        assert path is not None and path.endswith("MyFont.ttf")
        assert get.call_args.kwargs.get("timeout")
        assert not list(tmp_path.glob("*.part"))

    def test_an_interrupted_download_leaves_nothing_to_be_served(self, fm, tmp_path, monkeypatch):
        fm.temp_font_dir = tmp_path

        def chunks():
            yield b"partial"
            raise OSError("connection reset")
        response = self._response([])
        response.iter_content.side_effect = lambda chunk_size: chunks()
        monkeypatch.setattr(fm_module.requests, "get", MagicMock(return_value=response))

        assert fm._download_font("https://fonts.example/Font.ttf", {"family": "f"}) is None
        assert list(tmp_path.iterdir()) == []
