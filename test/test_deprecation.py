"""@deprecated: plugin-facing APIs nothing in core, the monorepo or the
registry's third-party plugins calls, kept for one release with a warning."""

import logging
import os
import warnings

import pytest

os.environ.setdefault("EMULATOR", "true")

from src import deprecation
from src.deprecation import deprecated

#: Everything deprecated for removal in 3.7.0. Removing one of these, or
#: deprecating another, should be a deliberate edit here too.
DEPRECATED = {
    "src.cache_manager.CacheManager": [
        "has_data_changed", "update_cache", "setup_persistent_cache",
        "get_sport_live_interval", "get_sport_key_from_cache_key",
        "get_background_cached_data", "is_background_data_available",
        "record_cache_hit", "record_cache_miss", "record_fetch_time",
        "get_cache_metrics", "log_cache_metrics", "get_memory_cache_stats",
    ],
    "src.display_manager.DisplayManager": [
        "draw_sun", "draw_cloud", "draw_rain", "draw_snow", "draw_weather_icon",
        "draw_text_with_icons", "get_scrolling_stats",
    ],
    "src.font_manager.FontManager": [
        "get_manager_fonts", "get_detected_fonts", "unregister_plugin_fonts",
        "get_plugin_fonts", "set_override", "remove_override", "get_overrides",
        "get_available_fonts", "get_size_tokens", "get_performance_stats",
        "get_font_catalog", "add_font", "remove_font", "validate_font",
    ],
    "src.plugin_system.plugin_manager.PluginManager": ["get_enabled_plugins"],
}


def _cls(path):
    import importlib
    module, name = path.rsplit(".", 1)
    return getattr(importlib.import_module(module), name)


@pytest.mark.parametrize("path", sorted(DEPRECATED))
def test_exactly_these_methods_are_deprecated(path):
    cls = _cls(path)
    marked = sorted(name for name, value in vars(cls).items()
                    if hasattr(value, "__deprecated__"))
    assert marked == sorted(DEPRECATED[path])
    for name in marked:
        assert "3.7.0" in getattr(cls, name).__deprecated__


@pytest.fixture
def fresh(monkeypatch):
    monkeypatch.setattr(deprecation, "_warned", set())


def test_first_call_warns_and_logs_then_stays_quiet(fresh, caplog):
    @deprecated("9.9.9", "use other()")
    def old(x):
        """Doc."""
        return x * 2

    with warnings.catch_warnings(record=True) as caught, caplog.at_level(logging.WARNING):
        warnings.simplefilter("always")
        assert old(2) == 4
        assert old(3) == 6

    assert [str(w.message) for w in caught] == [
        "test_first_call_warns_and_logs_then_stays_quiet.<locals>.old() is deprecated "
        "and will be removed in LEDMatrix 9.9.9; use other()"]
    assert caught[0].category is DeprecationWarning
    assert caught[0].filename == __file__  # points at the caller
    assert sum("will be removed in LEDMatrix 9.9.9" in r.message for r in caplog.records) == 1
    assert old.__name__ == "old" and old.__doc__ == "Doc."


def test_decorated_methods_still_work(fresh):
    from src.font_manager import FontManager
    fm = FontManager({})
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        assert fm.get_font_catalog() == fm.font_catalog
