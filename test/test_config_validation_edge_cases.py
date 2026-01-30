"""
Tests for configuration validation edge cases.

Tests scenarios that commonly cause user configuration errors:
- Invalid JSON in config files
- Missing required fields
- Type mismatches
- Nested object validation
- Array validation
"""

import pytest
import json
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock
import tempfile
import os

# Add project root to path
import sys
project_root = Path(__file__).parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from src.config_manager import ConfigManager
from src.plugin_system.schema_manager import SchemaManager


class TestInvalidJson:
    """Test handling of invalid JSON in config files."""

    def test_invalid_json_syntax(self, tmp_path):
        """Config with invalid JSON syntax should be handled gracefully."""
        config_file = tmp_path / "config.json"
        config_file.write_text("{ invalid json }")

        with patch.object(ConfigManager, '_get_config_path', return_value=str(config_file)):
            config_manager = ConfigManager(config_dir=str(tmp_path))
            # Should not raise, should return empty or default config
            config = config_manager.load_config()
            assert isinstance(config, dict)

    def test_truncated_json(self, tmp_path):
        """Config with truncated JSON should be handled gracefully."""
        config_file = tmp_path / "config.json"
        config_file.write_text('{"plugin": {"enabled": true')  # Missing closing braces

        with patch.object(ConfigManager, '_get_config_path', return_value=str(config_file)):
            config_manager = ConfigManager(config_dir=str(tmp_path))
            config = config_manager.load_config()
            assert isinstance(config, dict)

    def test_empty_config_file(self, tmp_path):
        """Empty config file should be handled gracefully."""
        config_file = tmp_path / "config.json"
        config_file.write_text("")

        with patch.object(ConfigManager, '_get_config_path', return_value=str(config_file)):
            config_manager = ConfigManager(config_dir=str(tmp_path))
            config = config_manager.load_config()
            assert isinstance(config, dict)


class TestTypeValidation:
    """Test type validation and coercion."""

    def test_string_where_number_expected(self):
        """String value where number expected should be handled."""
        schema_manager = SchemaManager()

        schema = {
            "type": "object",
            "properties": {
                "display_duration": {"type": "number", "default": 15}
            }
        }

        config = {"display_duration": "invalid_string"}

        # Validation should fail
        is_valid, errors = schema_manager.validate_config_against_schema(
            config, schema, "test-plugin"
        )
        assert not is_valid
        assert len(errors) > 0

    def test_number_where_string_expected(self):
        """Number value where string expected should be handled."""
        schema_manager = SchemaManager()

        schema = {
            "type": "object",
            "properties": {
                "team_name": {"type": "string", "default": ""}
            }
        }

        config = {"team_name": 12345}

        is_valid, errors = schema_manager.validate_config_against_schema(
            config, schema, "test-plugin"
        )
        assert not is_valid
        assert len(errors) > 0

    def test_null_value_for_required_field(self):
        """Null value for required field should be detected."""
        schema_manager = SchemaManager()

        # Schema that explicitly disallows null for api_key
        schema = {
            "type": "object",
            "properties": {
                "api_key": {"type": "string"}  # string type doesn't allow null
            },
            "required": ["api_key"]
        }

        config = {"api_key": None}

        is_valid, errors = schema_manager.validate_config_against_schema(
            config, schema, "test-plugin"
        )
        # JSON Schema Draft 7: null is not a valid string type
        assert not is_valid, "Null value should fail validation for string type"
        assert errors, "Should have validation errors"
        assert any("api_key" in str(e).lower() or "null" in str(e).lower() or "type" in str(e).lower() for e in errors), \
            f"Error should mention api_key, null, or type issue: {errors}"


class TestNestedValidation:
    """Test validation of nested configuration objects."""

    def test_nested_object_missing_required(self):
        """Missing required field in nested object should be detected."""
        schema_manager = SchemaManager()

        schema = {
            "type": "object",
            "properties": {
                "nfl": {
                    "type": "object",
                    "properties": {
                        "enabled": {"type": "boolean", "default": True},
                        "api_key": {"type": "string"}
                    },
                    "required": ["api_key"]
                }
            }
        }

        config = {"nfl": {"enabled": True}}  # Missing api_key

        is_valid, errors = schema_manager.validate_config_against_schema(
            config, schema, "test-plugin"
        )
        assert not is_valid

    def test_deeply_nested_validation(self):
        """Validation should work for deeply nested objects."""
        schema_manager = SchemaManager()

        schema = {
            "type": "object",
            "properties": {
                "level1": {
                    "type": "object",
                    "properties": {
                        "level2": {
                            "type": "object",
                            "properties": {
                                "value": {"type": "number", "minimum": 0}
                            }
                        }
                    }
                }
            }
        }

        config = {"level1": {"level2": {"value": -5}}}  # Invalid: negative

        is_valid, errors = schema_manager.validate_config_against_schema(
            config, schema, "test-plugin"
        )
        assert not is_valid


class TestArrayValidation:
    """Test validation of array configurations."""

    def test_array_min_items(self):
        """Array with fewer items than minItems should fail."""
        schema_manager = SchemaManager()

        schema = {
            "type": "object",
            "properties": {
                "teams": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 1
                }
            }
        }

        config = {"teams": []}  # Empty array, minItems is 1

        is_valid, errors = schema_manager.validate_config_against_schema(
            config, schema, "test-plugin"
        )
        assert not is_valid

    def test_array_max_items(self):
        """Array with more items than maxItems should fail."""
        schema_manager = SchemaManager()

        schema = {
            "type": "object",
            "properties": {
                "teams": {
                    "type": "array",
                    "items": {"type": "string"},
                    "maxItems": 2
                }
            }
        }

        config = {"teams": ["A", "B", "C", "D"]}  # 4 items, maxItems is 2

        is_valid, errors = schema_manager.validate_config_against_schema(
            config, schema, "test-plugin"
        )
        assert not is_valid


class TestCollisionDetection:
    """Test config key collision detection."""

    def test_reserved_key_collision(self):
        """Plugin IDs that conflict with reserved keys should be detected."""
        schema_manager = SchemaManager()

        plugin_ids = ["display", "custom-plugin", "schedule"]

        collisions = schema_manager.detect_config_key_collisions(plugin_ids)

        # Should detect 'display' and 'schedule' as collisions
        collision_types = [c["type"] for c in collisions]
        collision_plugins = [c["plugin_id"] for c in collisions]

        assert "reserved_key_collision" in collision_types
        assert "display" in collision_plugins
        assert "schedule" in collision_plugins

    def test_case_collision(self):
        """Plugin IDs that differ only in case should be detected."""
        schema_manager = SchemaManager()

        plugin_ids = ["football-scoreboard", "Football-Scoreboard", "other-plugin"]

        collisions = schema_manager.detect_config_key_collisions(plugin_ids)

        case_collisions = [c for c in collisions if c["type"] == "case_collision"]
        assert len(case_collisions) == 1

    def test_no_collisions(self):
        """Unique plugin IDs should not trigger collisions."""
        schema_manager = SchemaManager()

        plugin_ids = ["football-scoreboard", "odds-ticker", "weather-display"]

        collisions = schema_manager.detect_config_key_collisions(plugin_ids)

        assert len(collisions) == 0


class TestDefaultMerging:
    """Test default value merging with user config."""

    def test_defaults_applied_to_missing_fields(self):
        """Missing fields should get default values from schema."""
        schema_manager = SchemaManager()

        defaults = {
            "enabled": True,
            "display_duration": 15,
            "nfl": {"enabled": True}
        }

        config = {"display_duration": 30}  # Only override one field

        merged = schema_manager.merge_with_defaults(config, defaults)

        assert merged["enabled"] is True  # From defaults
        assert merged["display_duration"] == 30  # User override
        assert merged["nfl"]["enabled"] is True  # Nested default

    def test_user_values_not_overwritten(self):
        """User-provided values should not be overwritten by defaults."""
        schema_manager = SchemaManager()

        defaults = {"enabled": True, "display_duration": 15}
        config = {"enabled": False, "display_duration": 60}

        merged = schema_manager.merge_with_defaults(config, defaults)

        assert merged["enabled"] is False
        assert merged["display_duration"] == 60
