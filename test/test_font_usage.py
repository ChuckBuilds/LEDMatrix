"""The Fonts tab's "Used by" column, across the two services.

Plugins register fonts with FontManager in the display service; the web
interface lists font files from its own scan and has no FontManager. The
display side publishes {catalog key: [plugin ids]} to the shared cache
(src/font_usage.py) and GET /api/v3/fonts/catalog merges it in per request.

As in test_error_snapshot_cross_process.py, the two services are two
CacheManagers over one temporary directory.
"""
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.cache_manager import CacheManager  # noqa: E402
from src.font_manager import FontManager  # noqa: E402
from src import font_usage  # noqa: E402
from src.font_usage import (  # noqa: E402
    FONT_USAGE_KEY, REFRESH_INTERVAL, FontUsagePublisher, build_font_usage,
    catalog_key_for, read_font_usage, start_font_usage_publisher,
)
from test._api_v3_test_helpers import api_v3_client, api_v3_module  # noqa: F401,E402

REPO_FONTS = Path(__file__).resolve().parent.parent / "assets" / "fonts"


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


@pytest.fixture(scope="module")
def fm():
    return FontManager({})


@pytest.fixture
def shared_cache(tmp_path, monkeypatch):
    """Two cache managers over one directory: the display's and the web's."""
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    monkeypatch.setattr(CacheManager, "_get_writable_cache_dir", lambda self: str(cache_dir))
    display_cache, web_cache = CacheManager(), CacheManager()
    yield display_cache, web_cache
    display_cache.stop_cleanup_thread()
    web_cache.stop_cleanup_thread()


@pytest.fixture
def display(shared_cache):
    """A FontManager, the loaded-plugin table and a publisher over them."""
    display_cache, _ = shared_cache
    font_manager = FontManager({})
    plugin_manager = SimpleNamespace(plugins={})
    clock = FakeClock()
    publisher = FontUsagePublisher(display_cache, font_manager, plugin_manager, clock=clock)
    return font_manager, plugin_manager, publisher, clock


def _load(font_manager, plugin_manager, plugin_id, *families):
    """What a plugin's constructor does, then the plugin manager."""
    for i, family in enumerate(families):
        font_manager.register_manager_font(plugin_id, f"{plugin_id}.e{i}", family, 8)
    plugin_manager.plugins[plugin_id] = object()


def _published(web_cache):
    return web_cache.get(FONT_USAGE_KEY, max_age=None, memory_ttl=0)


# --- Normalisation to the catalog's keys ------------------------------------

class TestCatalogKeys:
    """The web catalog keys a font by its file name without the extension,
    as on disk. Plugins name fonts by FontManager family, alias or path."""

    @pytest.mark.parametrize("family, key", [
        ("press_start", "PressStart2P-Regular"),
        ("four_by_six", "4x6-font"),
        ("five_by_seven", "5x7"),
        ("tom_thumb", "tom-thumb"),
    ])
    def test_aliases(self, fm, family, key):
        assert catalog_key_for(family, fm.font_catalog) == key

    @pytest.mark.parametrize("family, key", [
        ("5x7", "5x7"),
        ("4x6", "4x6"),                        # the BDF, not 4x6-font.ttf
        ("6x13b", "6x13B"),                    # FontManager lower-cases stems
        ("6x13B", "6x13B"),
        ("PressStart2P-Regular", "PressStart2P-Regular"),
        ("5by7.regular", "5by7.regular"),      # a dot inside the stem
    ])
    def test_scanned_families_keep_the_file_names_case(self, fm, family, key):
        assert catalog_key_for(family, fm.font_catalog) == key

    @pytest.mark.parametrize("family, key", [
        ("assets/fonts/4x6-font.ttf", "4x6-font"),
        ("5x7.bdf", "5x7"),
        (str(REPO_FONTS / "MatrixChunky8.bdf"), "MatrixChunky8"),
    ])
    def test_paths(self, fm, family, key):
        assert catalog_key_for(family, fm.font_catalog) == key

    def test_absolute_path_through_the_catalog(self, fm, tmp_path):
        catalog = {"custom": str(tmp_path / "Custom-Face.ttf")}
        assert catalog_key_for("custom", catalog, fonts_dir=str(tmp_path)) == "Custom-Face"

    def test_a_font_outside_assets_fonts_has_no_row(self, fm, tmp_path):
        # A plugin's own font ("plugin::family") lives in its directory; a
        # file of the same name there is not the catalog's 5x7.
        catalog = {"weather::5x7": str(tmp_path / "5x7.bdf")}
        assert catalog_key_for("weather::5x7", catalog) is None
        assert catalog_key_for(str(tmp_path / "5x7.bdf"), fm.font_catalog) is None
        assert catalog_key_for("plugins/x/fonts/5x7.bdf", fm.font_catalog) is None

    @pytest.mark.parametrize("family", ["no_such_font", "", "   ", None, 8, "notafont.txt"])
    def test_unresolvable(self, fm, family):
        assert catalog_key_for(family, fm.font_catalog) is None

    def test_build_merges_aliases_and_names_of_one_file(self, fm):
        font_manager = FontManager({})
        font_manager.register_manager_font("calendar", "calendar.title", "press_start", 8)
        font_manager.register_manager_font("weather", "weather.t", "PressStart2P-Regular", 16)
        font_manager.register_manager_font("weather", "weather.c", "four_by_six", 8)
        font_manager.register_manager_font("weather", "weather.x", "no_such_font", 8)
        assert build_font_usage(font_manager) == {
            "4x6-font": ["weather"],
            "PressStart2P-Regular": ["calendar", "weather"],
        }


# --- Display side -----------------------------------------------------------

class TestPublishing:
    def test_publishes_after_plugins_load(self, display, shared_cache):
        font_manager, plugin_manager, publisher, _ = display
        _, web_cache = shared_cache
        _load(font_manager, plugin_manager, "calendar", "four_by_six", "press_start")
        _load(font_manager, plugin_manager, "of-the-day", "press_start")
        assert publisher.tick() is True
        snapshot = _published(web_cache)
        assert snapshot["fonts"] == {
            "4x6-font": ["calendar"],
            "PressStart2P-Regular": ["calendar", "of-the-day"],
        }
        assert snapshot["generated_at"]

    def test_a_plugin_that_failed_to_load_is_not_listed(self, display, shared_cache):
        # Its constructor registered fonts, then validation failed, so it
        # never reached plugin_manager.plugins.
        font_manager, plugin_manager, publisher, _ = display
        _, web_cache = shared_cache
        font_manager.register_manager_font("broken", "broken.x", "press_start", 8)
        _load(font_manager, plugin_manager, "calendar", "four_by_six")
        publisher.tick()
        assert _published(web_cache)["fonts"] == {"4x6-font": ["calendar"]}

    def test_load_and_unload_later_republish(self, display, shared_cache):
        font_manager, plugin_manager, publisher, _ = display
        _, web_cache = shared_cache
        _load(font_manager, plugin_manager, "calendar", "four_by_six")
        publisher.tick()

        _load(font_manager, plugin_manager, "weather", "four_by_six", "five_by_seven")
        assert publisher.tick() is True
        assert _published(web_cache)["fonts"] == {
            "4x6-font": ["calendar", "weather"], "5x7": ["weather"]}

        # What PluginManager.unload_plugin does.
        del plugin_manager.plugins["weather"]
        font_manager.forget_manager_fonts("weather")
        assert publisher.tick() is True
        assert _published(web_cache)["fonts"] == {"4x6-font": ["calendar"]}

    def test_first_tick_replaces_a_previous_runs_snapshot(self, display, shared_cache):
        _, web_cache = shared_cache
        web_cache.set(FONT_USAGE_KEY, {"generated_at": "old", "fonts": {"5x7": ["gone"]}})
        _, _, publisher, _ = display
        assert publisher.tick() is True
        assert _published(web_cache)["fonts"] == {}

    def test_nothing_is_written_when_nothing_changed(self, display):
        font_manager, plugin_manager, publisher, clock = display
        _load(font_manager, plugin_manager, "countdown", "press_start")
        publisher.tick()
        publisher.cache_manager = MagicMock(wraps=publisher.cache_manager)
        with patch.object(font_usage, "build_font_usage",
                          wraps=font_usage.build_font_usage) as build:
            for _ in range(20):
                # The countdown plugin registers per countdown at render time.
                font_manager.register_manager_font("countdown", "countdown.e0", "press_start", 8)
                clock.now += 5
                assert publisher.tick() is False
            build.assert_not_called()
        publisher.cache_manager.set.assert_not_called()

    def test_a_registration_that_leaves_usage_unchanged_is_not_written(self, display):
        font_manager, plugin_manager, publisher, _ = display
        _load(font_manager, plugin_manager, "countdown", "press_start")
        publisher.tick()
        publisher.cache_manager = MagicMock(wraps=publisher.cache_manager)
        # A new element, same font: the version moves, the usage does not.
        font_manager.register_manager_font("countdown", "countdown.c2.value", "press_start", 12)
        assert publisher.tick() is False
        publisher.cache_manager.set.assert_not_called()

    def test_an_unchanged_snapshot_is_refreshed_daily(self, display):
        # The cache's disk cleanup deletes entries older than 30 days.
        _, _, publisher, clock = display
        publisher.tick()
        publisher.cache_manager = MagicMock(wraps=publisher.cache_manager)
        clock.now += REFRESH_INTERVAL - 1
        assert publisher.tick() is False
        clock.now += 1
        assert publisher.tick() is True
        publisher.cache_manager.set.assert_called_once()


class TestFailureIsolation:
    def test_a_failing_cache_write_does_not_raise_and_is_retried(self, display, shared_cache):
        font_manager, plugin_manager, publisher, _ = display
        _, web_cache = shared_cache
        _load(font_manager, plugin_manager, "calendar", "four_by_six")
        real = publisher.cache_manager
        publisher.cache_manager = MagicMock()
        publisher.cache_manager.set.side_effect = PermissionError("read-only cache")
        assert publisher.tick() is False
        publisher.cache_manager = real
        assert publisher.tick() is True
        assert _published(web_cache)["fonts"] == {"4x6-font": ["calendar"]}

    def test_a_broken_font_manager_does_not_raise(self, shared_cache):
        display_cache, _ = shared_cache
        broken = SimpleNamespace(manager_fonts=None, font_catalog={})
        publisher = FontUsagePublisher(display_cache, broken, SimpleNamespace(plugins={}))
        assert publisher.tick() is False

    def test_start_never_raises(self):
        assert start_font_usage_publisher(MagicMock(), MagicMock(), None) is None
        with patch.object(font_usage.FontUsagePublisher, "start",
                          side_effect=RuntimeError("no threads")):
            assert start_font_usage_publisher(MagicMock(), MagicMock(), MagicMock()) is None

    def test_start_publishes_from_a_thread(self, shared_cache, monkeypatch):
        display_cache, web_cache = shared_cache
        font_manager = FontManager({})
        plugin_manager = SimpleNamespace(plugins={})
        _load(font_manager, plugin_manager, "calendar", "press_start")
        publisher = start_font_usage_publisher(display_cache, font_manager, plugin_manager)
        try:
            import time
            deadline = time.monotonic() + 5
            while _published(web_cache) is None and time.monotonic() < deadline:
                time.sleep(0.02)
            assert _published(web_cache)["fonts"] == {"PressStart2P-Regular": ["calendar"]}
        finally:
            publisher.stop()


class TestFontManagerRegistrations:
    def test_version_moves_only_when_a_family_changes(self):
        font_manager = FontManager({})
        v0 = font_manager.manager_fonts_version
        font_manager.register_manager_font("p", "p.a", "press_start", 8)
        v1 = font_manager.manager_fonts_version
        font_manager.register_manager_font("p", "p.a", "press_start", 10)  # size only
        assert font_manager.manager_fonts_version == v1 > v0
        font_manager.register_manager_font("p", "p.a", "four_by_six", 8)
        assert font_manager.manager_fonts_version > v1

    def test_forget_drops_every_registration_of_that_manager(self):
        font_manager = FontManager({})
        font_manager.register_manager_font("p", "p.a", "press_start", 8)
        font_manager.register_manager_font("q", "q.a", "press_start", 8)
        version = font_manager.manager_fonts_version
        font_manager.forget_manager_fonts("p")
        assert "p" not in font_manager.manager_fonts
        assert "p.a" not in font_manager.detected_fonts
        assert "q" in font_manager.manager_fonts and "q.a" in font_manager.detected_fonts
        assert font_manager.manager_fonts_version > version
        font_manager.forget_manager_fonts("never-registered")  # no error

    def test_unload_plugin_forgets_its_fonts(self, tmp_path):
        from src.plugin_system.plugin_manager import PluginManager
        font_manager = FontManager({})
        pm = PluginManager(plugins_dir=str(tmp_path), config_manager=None,
                           display_manager=None, cache_manager=None,
                           font_manager=font_manager)
        try:
            font_manager.register_manager_font("calendar", "calendar.t", "press_start", 8)
            pm.plugins["calendar"] = SimpleNamespace()
            assert pm.unload_plugin("calendar") is True
            assert "calendar" not in font_manager.manager_fonts
        finally:
            pm.stop_update_worker()

    def test_unload_survives_a_font_manager_that_raises(self, tmp_path):
        from src.plugin_system.plugin_manager import PluginManager
        font_manager = MagicMock()
        font_manager.forget_manager_fonts.side_effect = RuntimeError("boom")
        pm = PluginManager(plugins_dir=str(tmp_path), config_manager=None,
                           display_manager=None, cache_manager=None,
                           font_manager=font_manager)
        try:
            pm.plugins["calendar"] = SimpleNamespace()
            assert pm.unload_plugin("calendar") is True
        finally:
            pm.stop_update_worker()


def test_display_controller_starts_the_publisher(test_display_controller, mock_cache_manager):
    publisher = test_display_controller._font_usage_publisher
    assert isinstance(publisher, FontUsagePublisher)
    assert publisher.plugin_manager is test_display_controller.plugin_manager
    publisher.tick()
    assert "fonts" in mock_cache_manager._memory_cache[FONT_USAGE_KEY]


# --- Reading side -----------------------------------------------------------

class TestReading:
    def test_nothing_published(self, shared_cache):
        _, web_cache = shared_cache
        assert read_font_usage(web_cache) is None

    @pytest.mark.parametrize("junk", [
        "a string", {"fonts": "nope"}, {"generated_at": "x"}, [1, 2],
    ])
    def test_a_malformed_snapshot_reads_as_unknown(self, shared_cache, junk):
        _, web_cache = shared_cache
        web_cache.set(FONT_USAGE_KEY, junk)
        assert read_font_usage(web_cache) is None

    def test_bad_entries_are_dropped(self, shared_cache):
        _, web_cache = shared_cache
        web_cache.set(FONT_USAGE_KEY, {"generated_at": 5, "fonts": {
            "5x7": ["b", "a", 3, None, "", "a"], "4x6": "calendar", "tom-thumb": [],
        }})
        assert read_font_usage(web_cache) == {"generated_at": None, "fonts": {"5x7": ["a", "b"]}}

    def test_a_cache_that_raises_reads_as_unknown(self):
        cache = MagicMock()
        cache.get.side_effect = OSError("gone")
        assert read_font_usage(cache) is None


# --- Web side ---------------------------------------------------------------

@pytest.fixture
def web(api_v3_module, api_v3_client, shared_cache, tmp_path):  # noqa: F811
    """The real blueprint over a scratch assets/fonts and the shared cache."""
    _, web_cache = shared_cache
    api_v3_module.api_v3.cache_manager = web_cache
    fonts_dir = tmp_path / "root" / "assets" / "fonts"
    fonts_dir.mkdir(parents=True)
    for name in ("PressStart2P-Regular.ttf", "5x7.bdf", "my-custom.ttf"):
        (fonts_dir / name).write_bytes(b"not really a font")
    from web_interface.cache import delete_cached
    delete_cached("fonts_catalog")
    with patch("web_interface.blueprints.api_v3.fonts.PROJECT_ROOT", tmp_path / "root"):
        yield api_v3_client, web_cache, fonts_dir
    delete_cached("fonts_catalog")


def _catalog(client):
    response = client.get("/api/v3/fonts/catalog")
    assert response.status_code == 200, response.get_data(as_text=True)
    return response.get_json()["data"]


class TestCatalogEndpoint:
    def test_unknown_until_the_display_service_reports(self, web):
        client, _, _ = web
        data = _catalog(client)
        assert data["font_usage"] == {"available": False, "generated_at": None}
        assert {key: info["used_by"] for key, info in data["catalog"].items()} == {
            "PressStart2P-Regular": None, "5x7": None, "my-custom": None}

    def test_usage_is_merged_into_the_rows(self, web):
        client, web_cache, _ = web
        web_cache.set(FONT_USAGE_KEY, {"generated_at": "2026-09-23T10:00:00", "fonts": {
            "PressStart2P-Regular": ["calendar", "clock-simple"],
            "my-custom": ["football-scoreboard"],
            "not-in-the-catalog": ["weather"],
        }})
        data = _catalog(client)
        assert data["font_usage"] == {"available": True, "generated_at": "2026-09-23T10:00:00"}
        rows = data["catalog"]
        assert rows["PressStart2P-Regular"]["used_by"] == ["calendar", "clock-simple"]
        assert rows["my-custom"]["used_by"] == ["football-scoreboard"]
        assert rows["5x7"]["used_by"] == []
        assert "not-in-the-catalog" not in rows
        # The existing fields are still there.
        assert rows["5x7"]["type"] == "bdf" and rows["5x7"]["is_system"] is True

    def test_keys_match_case_insensitively(self, web):
        client, web_cache, _ = web
        web_cache.set(FONT_USAGE_KEY, {"fonts": {"pressstart2p-regular": ["calendar"],
                                                 "PressStart2P-Regular": ["weather"]}})
        rows = _catalog(client)["catalog"]
        assert rows["PressStart2P-Regular"]["used_by"] == ["calendar", "weather"]

    def test_the_scan_stays_cached_while_usage_is_read_per_request(self, web):
        client, web_cache, _ = web
        _catalog(client)  # fills the 5-minute cache
        from web_interface.blueprints.api_v3 import fonts as fonts_module
        with patch.object(fonts_module.os, "listdir",
                          side_effect=AssertionError("rescanned assets/fonts")):
            web_cache.set(FONT_USAGE_KEY, {"fonts": {"5x7": ["weather"]}})
            assert _catalog(client)["catalog"]["5x7"]["used_by"] == ["weather"]
            web_cache.set(FONT_USAGE_KEY, {"fonts": {"5x7": ["calendar"]}})
            assert _catalog(client)["catalog"]["5x7"]["used_by"] == ["calendar"]
        # ...and usage never leaks into the cached scan.
        from web_interface.cache import get_cached
        cached = get_cached("fonts_catalog", ttl_seconds=300)
        assert cached and all("used_by" not in info for info in cached.values())

    def test_end_to_end_from_the_display_publisher(self, web, display):
        client, _, fonts_dir = web
        font_manager, plugin_manager, publisher, _ = display
        _load(font_manager, plugin_manager, "calendar", "press_start", "five_by_seven")
        publisher.tick()
        rows = _catalog(client)["catalog"]
        assert rows["PressStart2P-Regular"]["used_by"] == ["calendar"]
        assert rows["5x7"]["used_by"] == ["calendar"]
        # A later change reaches the web process too: it reads the file, not
        # the copy its own memory tier kept from the first read.
        _load(font_manager, plugin_manager, "weather", "five_by_seven")
        publisher.tick()
        assert _catalog(client)["catalog"]["5x7"]["used_by"] == ["calendar", "weather"]


class TestDeleteInUseFont:
    """The UI warns with the catalog's used_by; the server does not block."""

    def test_the_catalog_names_the_plugins_and_delete_still_works(self, web):
        client, web_cache, fonts_dir = web
        web_cache.set(FONT_USAGE_KEY, {"fonts": {"my-custom": ["football-scoreboard", "clock-simple"]}})
        assert _catalog(client)["catalog"]["my-custom"]["used_by"] == [
            "clock-simple", "football-scoreboard"]
        response = client.delete("/api/v3/fonts/my-custom")
        assert response.status_code == 200, response.get_json()
        assert not (fonts_dir / "my-custom.ttf").exists()
        assert "my-custom" not in _catalog(client)["catalog"]

    def test_system_fonts_are_still_refused(self, web):
        client, web_cache, fonts_dir = web
        web_cache.set(FONT_USAGE_KEY, {"fonts": {}})
        assert client.delete("/api/v3/fonts/5x7").status_code == 403
        assert (fonts_dir / "5x7.bdf").exists()


class TestFontsTemplate:
    """fonts.html renders usage with DOM text APIs and warns before delete."""

    TEMPLATE = (Path(__file__).resolve().parent.parent / "web_interface" / "templates"
                / "v3" / "partials" / "fonts.html").read_text(encoding="utf-8")

    def test_used_by_is_never_written_as_html(self):
        assert "usedSpan.textContent = font.usedBy.join(', ')" in self.TEMPLATE
        assert "usedSpan.innerHTML" not in self.TEMPLATE

    def test_delete_confirms_with_the_plugins(self):
        assert "Used by: ${usedBy.join(', ')}" in self.TEMPLATE
        assert "confirm(deleteFontConfirmMessage(fontFamily, usedBy))" in self.TEMPLATE

    def test_catalog_fetches_bypass_the_browser_cache(self):
        # /api/v3 GETs carry max-age=5. The usage check just before a delete
        # would otherwise be the copy the post-delete reload is served, and
        # the deleted font would stay listed.
        fetches = [line for line in self.TEMPLATE.splitlines()
                   if "fetch(" in line and "/api/v3/fonts/catalog" in line]
        assert len(fetches) == 2
        assert all("cache: 'no-store'" in line for line in fetches)
