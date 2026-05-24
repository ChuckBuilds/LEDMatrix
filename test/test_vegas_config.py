"""
Tests for src/vegas_mode/config.py

Covers VegasModeConfig: from_config, to_dict, get_frame_interval,
is_plugin_included, get_ordered_plugins, validate, update.
"""

import pytest
from src.vegas_mode.config import VegasModeConfig


# ---------------------------------------------------------------------------
# Default construction
# ---------------------------------------------------------------------------

class TestVegasModeConfigDefaults:
    def test_default_disabled(self):
        cfg = VegasModeConfig()
        assert cfg.enabled is False

    def test_default_scroll_speed(self):
        cfg = VegasModeConfig()
        assert cfg.scroll_speed == 50.0

    def test_default_separator_width(self):
        cfg = VegasModeConfig()
        assert cfg.separator_width == 32

    def test_default_target_fps(self):
        cfg = VegasModeConfig()
        assert cfg.target_fps == 125

    def test_default_plugin_order_empty(self):
        cfg = VegasModeConfig()
        assert cfg.plugin_order == []

    def test_default_excluded_plugins_empty(self):
        cfg = VegasModeConfig()
        assert len(cfg.excluded_plugins) == 0


# ---------------------------------------------------------------------------
# from_config
# ---------------------------------------------------------------------------

class TestFromConfig:
    def _cfg(self, **kwargs) -> dict:
        return {"display": {"vegas_scroll": kwargs}}

    def test_enabled_flag(self):
        cfg = VegasModeConfig.from_config(self._cfg(enabled=True))
        assert cfg.enabled is True

    def test_scroll_speed(self):
        cfg = VegasModeConfig.from_config(self._cfg(scroll_speed=80.0))
        assert cfg.scroll_speed == 80.0

    def test_separator_width(self):
        cfg = VegasModeConfig.from_config(self._cfg(separator_width=16))
        assert cfg.separator_width == 16

    def test_plugin_order(self):
        cfg = VegasModeConfig.from_config(self._cfg(plugin_order=["a", "b", "c"]))
        assert cfg.plugin_order == ["a", "b", "c"]

    def test_excluded_plugins(self):
        cfg = VegasModeConfig.from_config(self._cfg(excluded_plugins=["x", "y"]))
        assert "x" in cfg.excluded_plugins
        assert "y" in cfg.excluded_plugins

    def test_target_fps(self):
        cfg = VegasModeConfig.from_config(self._cfg(target_fps=60))
        assert cfg.target_fps == 60

    def test_buffer_ahead(self):
        cfg = VegasModeConfig.from_config(self._cfg(buffer_ahead=3))
        assert cfg.buffer_ahead == 3

    def test_min_max_cycle_duration(self):
        cfg = VegasModeConfig.from_config(self._cfg(min_cycle_duration=30, max_cycle_duration=120))
        assert cfg.min_cycle_duration == 30
        assert cfg.max_cycle_duration == 120

    def test_defaults_when_missing(self):
        cfg = VegasModeConfig.from_config({})
        assert cfg.enabled is False
        assert cfg.scroll_speed == 50.0

    def test_frame_based_scrolling(self):
        cfg = VegasModeConfig.from_config(self._cfg(frame_based_scrolling=False))
        assert cfg.frame_based_scrolling is False


# ---------------------------------------------------------------------------
# to_dict
# ---------------------------------------------------------------------------

class TestToDict:
    def test_roundtrip(self):
        original = VegasModeConfig(
            enabled=True,
            scroll_speed=75.0,
            separator_width=24,
            plugin_order=["a", "b"],
            excluded_plugins={"z"},
            target_fps=100,
        )
        d = original.to_dict()
        assert d["enabled"] is True
        assert d["scroll_speed"] == 75.0
        assert d["separator_width"] == 24
        assert d["plugin_order"] == ["a", "b"]
        assert "z" in d["excluded_plugins"]
        assert d["target_fps"] == 100

    def test_excluded_plugins_is_list(self):
        cfg = VegasModeConfig(excluded_plugins={"x"})
        d = cfg.to_dict()
        assert isinstance(d["excluded_plugins"], list)

    def test_all_keys_present(self):
        d = VegasModeConfig().to_dict()
        for key in ("enabled", "scroll_speed", "separator_width", "plugin_order",
                    "excluded_plugins", "target_fps", "buffer_ahead",
                    "frame_based_scrolling", "scroll_delay",
                    "dynamic_duration_enabled", "min_cycle_duration", "max_cycle_duration"):
            assert key in d


# ---------------------------------------------------------------------------
# get_frame_interval
# ---------------------------------------------------------------------------

class TestGetFrameInterval:
    def test_125fps(self):
        cfg = VegasModeConfig(target_fps=125)
        assert abs(cfg.get_frame_interval() - 1.0 / 125) < 1e-9

    def test_60fps(self):
        cfg = VegasModeConfig(target_fps=60)
        assert abs(cfg.get_frame_interval() - 1.0 / 60) < 1e-6

    def test_zero_fps_guarded(self):
        cfg = VegasModeConfig(target_fps=0)
        # Should not raise ZeroDivisionError (max(1, fps) guard)
        result = cfg.get_frame_interval()
        assert result == 1.0


# ---------------------------------------------------------------------------
# is_plugin_included
# ---------------------------------------------------------------------------

class TestIsPluginIncluded:
    def test_not_excluded_is_included(self):
        cfg = VegasModeConfig(excluded_plugins={"bad_plugin"})
        assert cfg.is_plugin_included("good_plugin") is True

    def test_excluded_plugin_not_included(self):
        cfg = VegasModeConfig(excluded_plugins={"bad_plugin"})
        assert cfg.is_plugin_included("bad_plugin") is False

    def test_empty_exclusions_all_included(self):
        cfg = VegasModeConfig()
        assert cfg.is_plugin_included("anything") is True


# ---------------------------------------------------------------------------
# get_ordered_plugins
# ---------------------------------------------------------------------------

class TestGetOrderedPlugins:
    def test_natural_order_when_no_order_configured(self):
        cfg = VegasModeConfig()
        available = ["a", "b", "c"]
        result = cfg.get_ordered_plugins(available)
        assert result == ["a", "b", "c"]

    def test_explicit_order_followed(self):
        cfg = VegasModeConfig(plugin_order=["c", "a", "b"])
        available = ["a", "b", "c"]
        result = cfg.get_ordered_plugins(available)
        assert result == ["c", "a", "b"]

    def test_unavailable_plugins_skipped(self):
        cfg = VegasModeConfig(plugin_order=["c", "x", "a"])
        available = ["a", "b", "c"]
        result = cfg.get_ordered_plugins(available)
        assert "x" not in result
        assert result[:2] == ["c", "a"]

    def test_excluded_plugins_removed(self):
        cfg = VegasModeConfig(excluded_plugins={"b"})
        available = ["a", "b", "c"]
        result = cfg.get_ordered_plugins(available)
        assert "b" not in result

    def test_unordered_available_appended(self):
        cfg = VegasModeConfig(plugin_order=["a"])
        available = ["a", "b", "c"]
        result = cfg.get_ordered_plugins(available)
        assert result[0] == "a"
        assert "b" in result
        assert "c" in result

    def test_empty_available(self):
        cfg = VegasModeConfig(plugin_order=["a"])
        result = cfg.get_ordered_plugins([])
        assert result == []


# ---------------------------------------------------------------------------
# validate
# ---------------------------------------------------------------------------

class TestValidate:
    def test_valid_config_no_errors(self):
        cfg = VegasModeConfig()
        errors = cfg.validate()
        assert errors == []

    def test_scroll_speed_too_low(self):
        cfg = VegasModeConfig(scroll_speed=0.5)
        errors = cfg.validate()
        assert any("scroll_speed" in e for e in errors)

    def test_scroll_speed_too_high(self):
        cfg = VegasModeConfig(scroll_speed=300.0)
        errors = cfg.validate()
        assert any("scroll_speed" in e for e in errors)

    def test_separator_width_negative(self):
        cfg = VegasModeConfig(separator_width=-1)
        errors = cfg.validate()
        assert any("separator_width" in e for e in errors)

    def test_separator_width_too_large(self):
        cfg = VegasModeConfig(separator_width=200)
        errors = cfg.validate()
        assert any("separator_width" in e for e in errors)

    def test_target_fps_too_low(self):
        cfg = VegasModeConfig(target_fps=10)
        errors = cfg.validate()
        assert any("target_fps" in e for e in errors)

    def test_target_fps_too_high(self):
        cfg = VegasModeConfig(target_fps=300)
        errors = cfg.validate()
        assert any("target_fps" in e for e in errors)

    def test_buffer_ahead_too_low(self):
        cfg = VegasModeConfig(buffer_ahead=0)
        errors = cfg.validate()
        assert any("buffer_ahead" in e for e in errors)

    def test_buffer_ahead_too_high(self):
        cfg = VegasModeConfig(buffer_ahead=10)
        errors = cfg.validate()
        assert any("buffer_ahead" in e for e in errors)

    def test_multiple_errors_returned(self):
        cfg = VegasModeConfig(scroll_speed=0.1, target_fps=5)
        errors = cfg.validate()
        assert len(errors) >= 2


# ---------------------------------------------------------------------------
# update
# ---------------------------------------------------------------------------

class TestUpdate:
    def _wrap(self, **kwargs) -> dict:
        return {"display": {"vegas_scroll": kwargs}}

    def test_update_enabled(self):
        cfg = VegasModeConfig(enabled=False)
        cfg.update(self._wrap(enabled=True))
        assert cfg.enabled is True

    def test_update_scroll_speed(self):
        cfg = VegasModeConfig(scroll_speed=50.0)
        cfg.update(self._wrap(scroll_speed=90.0))
        assert cfg.scroll_speed == 90.0

    def test_update_separator_width(self):
        cfg = VegasModeConfig(separator_width=32)
        cfg.update(self._wrap(separator_width=8))
        assert cfg.separator_width == 8

    def test_update_plugin_order(self):
        cfg = VegasModeConfig(plugin_order=[])
        cfg.update(self._wrap(plugin_order=["x", "y"]))
        assert cfg.plugin_order == ["x", "y"]

    def test_update_excluded_plugins(self):
        cfg = VegasModeConfig()
        cfg.update(self._wrap(excluded_plugins=["skip_me"]))
        assert "skip_me" in cfg.excluded_plugins

    def test_update_ignores_missing_keys(self):
        cfg = VegasModeConfig(scroll_speed=50.0)
        cfg.update(self._wrap(target_fps=80))  # only fps, not speed
        assert cfg.scroll_speed == 50.0
        assert cfg.target_fps == 80

    def test_empty_update_no_change(self):
        cfg = VegasModeConfig(scroll_speed=50.0)
        cfg.update({})
        assert cfg.scroll_speed == 50.0
