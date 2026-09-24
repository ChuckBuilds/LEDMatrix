"""Startup validation runs each check once.

DisplayController ran StartupValidator.validate_all() twice -- once before the
plugin manager existed and again after -- so every config, cache, display and
systemd-unit warning was logged twice at every boot. The second pass only
needs the plugin checks.
"""

import logging
from unittest.mock import MagicMock, patch


class RecordingValidator:
    """Stands in for StartupValidator and records which checks ran."""

    calls = []

    def __init__(self, config_manager, plugin_manager=None, cache_manager=None):
        self.plugin_manager = plugin_manager
        self.errors = []
        self.warnings = []

    def validate_all(self):
        RecordingValidator.calls.append(('validate_all', self.plugin_manager is not None))
        self.warnings = ['config warning']
        return True, [], list(self.warnings)

    def _validate_plugins(self):
        RecordingValidator.calls.append(('plugins',))
        self.warnings = ['plugin warning']


def _build_controller(mock_config_manager, mock_display_manager,
                      mock_cache_manager, config):
    from src.display_controller import DisplayController

    mock_config_manager.get_config.return_value = config
    mock_config_manager.load_config.return_value = config
    mock_pm = MagicMock()
    mock_pm.discover_plugins.return_value = []
    mock_pm.plugins = {}
    mock_pm.plugin_manifests = {}
    mock_pm.plugin_last_update = {}
    mock_pm.health_tracker = None

    with patch('src.display_controller.ConfigManager', return_value=mock_config_manager), \
         patch('src.display_controller.DisplayManager', return_value=mock_display_manager), \
         patch('src.display_controller.CacheManager', return_value=mock_cache_manager), \
         patch('src.display_controller.FontManager'), \
         patch('src.plugin_system.PluginManager', return_value=mock_pm), \
         patch('src.startup_validator.StartupValidator', RecordingValidator):
        controller = DisplayController()
    return controller


def test_each_check_runs_once(mock_config_manager, mock_display_manager,
                              mock_cache_manager, test_config_with_plugins,
                              emulator_mode, caplog):
    RecordingValidator.calls = []
    with caplog.at_level(logging.WARNING, logger='src.display_controller'):
        controller = _build_controller(mock_config_manager, mock_display_manager,
                                       mock_cache_manager, test_config_with_plugins)
    try:
        full_passes = [c for c in RecordingValidator.calls if c[0] == 'validate_all']
        assert len(full_passes) == 1, RecordingValidator.calls

        plugin_checks = (
            sum(1 for c in RecordingValidator.calls if c[0] == 'plugins')
            + sum(1 for c in full_passes if c[1]))
        assert plugin_checks == 1, RecordingValidator.calls

        config_warnings = [r for r in caplog.records
                           if 'config warning' in r.getMessage()]
        assert len(config_warnings) == 1
        assert any('plugin warning' in r.getMessage() for r in caplog.records)
    finally:
        controller.cleanup()
