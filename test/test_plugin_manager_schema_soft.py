"""
Tests for PluginManager._validate_config_schema_soft (Phase 1, warn-only schema
validation).

Contract:
- A schema violation logs a warning and marks the plugin degraded in the health
  tracker, but never raises and never changes load pass/fail behaviour.
- A valid config (or no schema) clears any stale degraded flag.
- The method is safe when no health tracker is wired.
"""

import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.plugin_system.plugin_manager import PluginManager


@pytest.fixture
def pm():
    with tempfile.TemporaryDirectory() as tmp:
        manager = PluginManager(plugins_dir=str(Path(tmp) / "plugins"))
        manager.schema_manager = MagicMock()
        yield manager


def test_invalid_config_marks_degraded_without_raising(pm):
    pm.health_tracker = MagicMock()
    pm.schema_manager.load_schema.return_value = {"type": "object"}
    pm.schema_manager.validate_config_against_schema.return_value = (
        False,
        ["Missing required field: 'api_key'"],
    )

    pm._validate_config_schema_soft("youtube-stats", {})

    pm.health_tracker.set_degraded.assert_called_once()
    plugin_id, reason = pm.health_tracker.set_degraded.call_args[0]
    assert plugin_id == "youtube-stats"
    assert "api_key" in reason


def test_valid_config_clears_degraded(pm):
    pm.health_tracker = MagicMock()
    pm.schema_manager.load_schema.return_value = {"type": "object"}
    pm.schema_manager.validate_config_against_schema.return_value = (True, [])

    pm._validate_config_schema_soft("p", {"api_key": "x"})

    pm.health_tracker.set_degraded.assert_called_once_with("p", None)


def test_no_schema_clears_degraded(pm):
    pm.health_tracker = MagicMock()
    pm.schema_manager.load_schema.return_value = None

    pm._validate_config_schema_soft("p", {})

    pm.health_tracker.set_degraded.assert_called_once_with("p", None)


def test_validation_exception_is_swallowed(pm):
    pm.health_tracker = MagicMock()
    pm.schema_manager.load_schema.return_value = {"type": "object"}
    pm.schema_manager.validate_config_against_schema.side_effect = RuntimeError("boom")

    # Must not raise — the validation machinery failing must never break loading.
    pm._validate_config_schema_soft("p", {})


def test_safe_without_health_tracker(pm):
    pm.health_tracker = None
    pm.schema_manager.load_schema.return_value = {"type": "object"}
    pm.schema_manager.validate_config_against_schema.return_value = (False, ["err"])

    # Must not raise even though there is no tracker to record against.
    pm._validate_config_schema_soft("p", {})
