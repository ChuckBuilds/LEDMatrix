"""
Tests for src/startup_validator.py — pins the StartupValidator contract.

Covers: required-key/config error reporting (errors never propagate out of
validate_all), the load_config/get_config accessor split, cache-directory
error-vs-warning downgrade behavior, plugin discovery/manifest checks with
reserved config keys skipped, idempotent validate_all (fresh error/warning
lists each run — the pre-fix behavior duplicated messages), and the
exception classification precedence in raise_on_errors (config > cache >
plugin > fallback ConfigError).
"""

import copy
import os
from unittest.mock import MagicMock

import pytest

from src.exceptions import CacheError, ConfigError, PluginError
from src.startup_validator import StartupValidator

GOOD_CONFIG = {
    'display': {'hardware': {'rows': 32, 'cols': 64}},
    'timezone': 'UTC',
}


def make_config_manager(config):
    """Config manager whose load_config() and get_config() return `config`."""
    mgr = MagicMock()
    mgr.load_config.return_value = copy.deepcopy(config)
    mgr.get_config.return_value = copy.deepcopy(config)
    return mgr


@pytest.fixture
def good_cache(monkeypatch, tmp_path):
    """Patch CacheManager so cache validation sees an existing writable dir.

    _validate_cache_directory does `from src.cache_manager import CacheManager`
    at call time, so patching the attribute on the module is picked up.
    """
    mock_cls = MagicMock()
    mock_cls.return_value.get_cache_dir.return_value = str(tmp_path)
    monkeypatch.setattr("src.cache_manager.CacheManager", mock_cls)
    return tmp_path


class TestValidateConfig:
    """Configuration validation via load_config()."""

    def test_happy_path(self, good_cache):
        validator = StartupValidator(make_config_manager(GOOD_CONFIG))
        is_valid, errors, warnings = validator.validate_all()
        assert is_valid is True
        assert errors == []
        assert warnings == []

    def test_missing_required_keys(self, good_cache):
        validator = StartupValidator(make_config_manager({}))
        is_valid, errors, warnings = validator.validate_all()
        assert is_valid is False
        assert "Missing required configuration key: display" in errors
        assert "Missing required configuration key: timezone" in errors

    def test_config_error_does_not_propagate(self, good_cache):
        mgr = make_config_manager(GOOD_CONFIG)
        mgr.load_config.side_effect = ConfigError("bad json")
        validator = StartupValidator(mgr)
        is_valid, errors, warnings = validator.validate_all()
        assert is_valid is False
        config_errors = [e for e in errors if e.startswith("Configuration error:")]
        assert len(config_errors) == 1
        assert "bad json" in config_errors[0]

    def test_unexpected_error_does_not_propagate(self, good_cache):
        mgr = make_config_manager(GOOD_CONFIG)
        mgr.load_config.side_effect = RuntimeError("kapow")
        validator = StartupValidator(mgr)
        is_valid, errors, warnings = validator.validate_all()
        assert is_valid is False
        unexpected = [e for e in errors
                      if e.startswith("Unexpected error validating configuration:")]
        assert len(unexpected) == 1
        assert "kapow" in unexpected[0]

    def test_accessor_split_get_config_failure_is_warning_only(self, good_cache):
        # _validate_config uses load_config(); _validate_display_config uses
        # get_config(). A broken get_config must degrade to a warning, not
        # crash or produce a config error.
        mgr = make_config_manager(GOOD_CONFIG)
        mgr.get_config.side_effect = RuntimeError("accessor broken")
        validator = StartupValidator(mgr)
        is_valid, errors, warnings = validator.validate_all()
        assert is_valid is True
        assert errors == []
        assert any(w.startswith("Could not validate display configuration:")
                   for w in warnings)
        assert mgr.load_config.called
        assert mgr.get_config.called


class TestDisplayConfig:
    """Display hardware validation via get_config()."""

    def test_missing_hardware_section_is_error(self, good_cache):
        config = {'display': {'runtime': {'gpio_slowdown': 2}}, 'timezone': 'UTC'}
        validator = StartupValidator(make_config_manager(config))
        is_valid, errors, warnings = validator.validate_all()
        assert is_valid is False
        assert "Display hardware configuration is missing" in errors

    def test_missing_rows_cols_are_warnings_not_errors(self, good_cache):
        config = {'display': {'hardware': {'brightness': 90}}, 'timezone': 'UTC'}
        validator = StartupValidator(make_config_manager(config))
        is_valid, errors, warnings = validator.validate_all()
        assert is_valid is True
        assert errors == []
        assert "Display hardware setting 'rows' not specified, using default" in warnings
        assert "Display hardware setting 'cols' not specified, using default" in warnings


class TestCacheDirectory:
    """Cache directory validation error/warning split."""

    def _patch_cache_dir(self, monkeypatch, cache_dir):
        mock_cls = MagicMock()
        mock_cls.return_value.get_cache_dir.return_value = cache_dir
        monkeypatch.setattr("src.cache_manager.CacheManager", mock_cls)

    def test_nonexistent_cache_dir_is_error(self, monkeypatch, tmp_path):
        missing = str(tmp_path / "does_not_exist")
        self._patch_cache_dir(monkeypatch, missing)
        validator = StartupValidator(make_config_manager(GOOD_CONFIG))
        is_valid, errors, warnings = validator.validate_all()
        assert is_valid is False
        assert any("does not exist" in e and missing in e for e in errors)

    def test_writable_cache_dir_no_errors(self, monkeypatch, tmp_path):
        self._patch_cache_dir(monkeypatch, str(tmp_path))
        validator = StartupValidator(make_config_manager(GOOD_CONFIG))
        is_valid, errors, warnings = validator.validate_all()
        assert is_valid is True
        assert not any('cache' in e.lower() for e in errors)

    def test_unwritable_cache_dir_is_error(self, monkeypatch, tmp_path):
        # Root (common in CI) can write anywhere, so chmod tricks don't
        # work — force os.access to deny writes for the cache dir only.
        cache_dir = str(tmp_path)
        self._patch_cache_dir(monkeypatch, cache_dir)
        real_access = os.access

        def fake_access(path, mode):
            if str(path) == cache_dir and mode == os.W_OK:
                return False
            return real_access(path, mode)

        monkeypatch.setattr(os, "access", fake_access)
        validator = StartupValidator(make_config_manager(GOOD_CONFIG))
        is_valid, errors, warnings = validator.validate_all()
        assert is_valid is False
        assert any("is not writable" in e for e in errors)

    def test_none_cache_dir_is_warning_not_error(self, monkeypatch):
        self._patch_cache_dir(monkeypatch, None)
        validator = StartupValidator(make_config_manager(GOOD_CONFIG))
        is_valid, errors, warnings = validator.validate_all()
        assert is_valid is True
        assert errors == []
        assert "Cache directory not available - caching will be disabled" in warnings

    def test_cache_manager_constructor_failure_is_warning(self, monkeypatch):
        mock_cls = MagicMock(side_effect=RuntimeError("no disk"))
        monkeypatch.setattr("src.cache_manager.CacheManager", mock_cls)
        validator = StartupValidator(make_config_manager(GOOD_CONFIG))
        is_valid, errors, warnings = validator.validate_all()
        assert is_valid is True
        assert errors == []
        assert any(w.startswith("Could not validate cache directory:") for w in warnings)


class TestPlugins:
    """Plugin validation with a plugin manager present."""

    def _config_with_plugins(self):
        return {
            'display': {'hardware': {'rows': 32, 'cols': 64}},
            'schedule': {'enabled': True},  # reserved key that LOOKS enabled
            'timezone': 'UTC',
            'plugin_system': {},
            'known': {'enabled': True},
            'ghost': {'enabled': True},
        }

    def test_ghost_plugin_warns_and_reserved_keys_skipped(self, good_cache, tmp_path):
        pm = MagicMock()
        pm.discover_plugins.return_value = ['known']
        known_dir = tmp_path / "known"
        known_dir.mkdir()
        (known_dir / "manifest.json").write_text("{}")
        pm.get_plugin_directory.return_value = str(known_dir)

        validator = StartupValidator(make_config_manager(self._config_with_plugins()), pm)
        is_valid, errors, warnings = validator.validate_all()
        assert is_valid is True
        assert "Plugin 'ghost' is enabled but not found in plugins directory" in warnings
        # Reserved sections are never treated as plugins, even when they
        # contain an 'enabled' flag (schedule above).
        for reserved in ('display', 'schedule', 'timezone', 'plugin_system'):
            assert not any(f"'{reserved}'" in w for w in warnings)

    def test_enabled_plugin_missing_manifest_is_error(self, good_cache, tmp_path):
        pm = MagicMock()
        pm.discover_plugins.return_value = ['known']
        plugin_dir = tmp_path / "known"
        plugin_dir.mkdir()  # exists, but no manifest.json inside
        pm.get_plugin_directory.return_value = str(plugin_dir)

        config = dict(GOOD_CONFIG, known={'enabled': True})
        validator = StartupValidator(make_config_manager(config), pm)
        is_valid, errors, warnings = validator.validate_all()
        assert is_valid is False
        assert "Plugin 'known' manifest.json not found" in errors

    def test_disabled_plugin_not_checked_for_manifest(self, good_cache, tmp_path):
        pm = MagicMock()
        pm.discover_plugins.return_value = ['known']
        pm.get_plugin_directory.return_value = str(tmp_path / "nowhere")

        config = dict(GOOD_CONFIG, known={'enabled': False})
        validator = StartupValidator(make_config_manager(config), pm)
        is_valid, errors, warnings = validator.validate_all()
        assert is_valid is True
        assert errors == []


class TestIdempotence:
    """validate_all() resets error/warning state each run (the fixed bug)."""

    def test_repeated_runs_do_not_accumulate(self, good_cache):
        validator = StartupValidator(make_config_manager({}))
        first = validator.validate_all()
        second = validator.validate_all()
        assert first == second
        assert len(second[1]) == len(first[1])
        assert len(second[2]) == len(first[2])


class TestRaiseOnErrors:
    """Exception classification and precedence in raise_on_errors()."""

    def _validator(self, errors):
        validator = StartupValidator(make_config_manager(GOOD_CONFIG))
        validator.errors = list(errors)
        return validator

    def test_no_errors_returns_none(self):
        assert self._validator([]).raise_on_errors() is None

    def test_config_error(self):
        msg = "Missing required configuration key: display"
        with pytest.raises(ConfigError) as excinfo:
            self._validator([msg]).raise_on_errors()
        assert excinfo.value.message == "Configuration validation failed"
        assert msg in excinfo.value.context['errors']

    def test_cache_error(self):
        msg = "Cache directory does not exist: /nope"
        with pytest.raises(CacheError) as excinfo:
            self._validator([msg]).raise_on_errors()
        assert msg in excinfo.value.context['errors']

    def test_plugin_error(self):
        msg = "Plugin 'known' manifest.json not found"
        with pytest.raises(PluginError) as excinfo:
            self._validator([msg]).raise_on_errors()
        assert msg in excinfo.value.context['errors']

    def test_unclassified_error_falls_back_to_config_error(self):
        msg = "Something entirely else went wrong"
        with pytest.raises(ConfigError) as excinfo:
            self._validator([msg]).raise_on_errors()
        assert excinfo.value.message == "Startup validation failed"
        assert msg in excinfo.value.context['errors']

    def test_precedence_config_beats_cache(self):
        # A message matching both 'config' and 'cache' substrings raises
        # ConfigError because config classification is checked first.
        msg = "config problem touching the cache layer"
        with pytest.raises(ConfigError) as excinfo:
            self._validator([msg]).raise_on_errors()
        assert excinfo.value.message == "Configuration validation failed"
        assert msg in excinfo.value.context['errors']
