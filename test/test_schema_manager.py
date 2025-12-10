"""
Tests for SchemaManager.

Tests schema loading, validation, default extraction, and caching.
"""

import pytest
import json
from pathlib import Path
from unittest.mock import MagicMock, patch, mock_open
from jsonschema import ValidationError
from src.plugin_system.schema_manager import SchemaManager


class TestSchemaManager:
    """Test SchemaManager functionality."""
    
    @pytest.fixture
    def tmp_project_root(self, tmp_path):
        """Create a temporary project root."""
        return tmp_path
    
    @pytest.fixture
    def schema_manager(self, tmp_project_root):
        """Create a SchemaManager instance."""
        return SchemaManager(project_root=tmp_project_root)
    
    @pytest.fixture
    def sample_schema(self):
        """Create a sample JSON schema."""
        return {
            "type": "object",
            "properties": {
                "enabled": {
                    "type": "boolean",
                    "default": True
                },
                "update_interval": {
                    "type": "integer",
                    "default": 300,
                    "minimum": 60
                },
                "api_key": {
                    "type": "string"
                }
            },
            "required": ["api_key"]
        }
    
    def test_init(self, tmp_project_root):
        """Test SchemaManager initialization."""
        sm = SchemaManager(project_root=tmp_project_root)
        
        assert sm.project_root == tmp_project_root
        assert sm._schema_cache == {}
        assert sm._defaults_cache == {}
    
    def test_get_schema_path_found(self, schema_manager, tmp_project_root, sample_schema):
        """Test finding schema path."""
        plugin_dir = tmp_project_root / "plugins" / "test_plugin"
        plugin_dir.mkdir(parents=True)
        schema_file = plugin_dir / "config_schema.json"
        schema_file.write_text(json.dumps(sample_schema))
        
        result = schema_manager.get_schema_path("test_plugin")
        
        assert result == schema_file
    
    def test_get_schema_path_not_found(self, schema_manager):
        """Test when schema path doesn't exist."""
        result = schema_manager.get_schema_path("nonexistent_plugin")
        
        assert result is None
    
    def test_load_schema(self, schema_manager, tmp_project_root, sample_schema):
        """Test loading a schema."""
        plugin_dir = tmp_project_root / "plugins" / "test_plugin"
        plugin_dir.mkdir(parents=True)
        schema_file = plugin_dir / "config_schema.json"
        schema_file.write_text(json.dumps(sample_schema))
        
        result = schema_manager.load_schema("test_plugin")
        
        assert result == sample_schema
        assert "test_plugin" in schema_manager._schema_cache
    
    def test_load_schema_cached(self, schema_manager, tmp_project_root, sample_schema):
        """Test loading schema from cache."""
        # Pre-populate cache
        schema_manager._schema_cache["test_plugin"] = sample_schema
        
        result = schema_manager.load_schema("test_plugin", use_cache=True)
        
        assert result == sample_schema
    
    def test_load_schema_not_found(self, schema_manager):
        """Test loading non-existent schema."""
        result = schema_manager.load_schema("nonexistent_plugin")
        
        assert result is None
    
    def test_invalidate_cache_specific_plugin(self, schema_manager):
        """Test invalidating cache for specific plugin."""
        schema_manager._schema_cache["plugin1"] = {}
        schema_manager._schema_cache["plugin2"] = {}
        schema_manager._defaults_cache["plugin1"] = {}
        schema_manager._defaults_cache["plugin2"] = {}
        
        schema_manager.invalidate_cache("plugin1")
        
        assert "plugin1" not in schema_manager._schema_cache
        assert "plugin1" not in schema_manager._defaults_cache
        assert "plugin2" in schema_manager._schema_cache
        assert "plugin2" in schema_manager._defaults_cache
    
    def test_invalidate_cache_all(self, schema_manager):
        """Test invalidating entire cache."""
        schema_manager._schema_cache["plugin1"] = {}
        schema_manager._schema_cache["plugin2"] = {}
        schema_manager._defaults_cache["plugin1"] = {}
        
        schema_manager.invalidate_cache()
        
        assert len(schema_manager._schema_cache) == 0
        assert len(schema_manager._defaults_cache) == 0
    
    def test_extract_defaults_from_schema(self, schema_manager, sample_schema):
        """Test extracting default values from schema."""
        defaults = schema_manager.extract_defaults_from_schema(sample_schema)
        
        assert defaults["enabled"] is True
        assert defaults["update_interval"] == 300
        assert "api_key" not in defaults  # No default value
    
    def test_extract_defaults_nested(self, schema_manager):
        """Test extracting defaults from nested schema."""
        nested_schema = {
            "type": "object",
            "properties": {
                "display": {
                    "type": "object",
                    "properties": {
                        "brightness": {
                            "type": "integer",
                            "default": 50
                        }
                    }
                }
            }
        }
        
        defaults = schema_manager.extract_defaults_from_schema(nested_schema)
        
        assert defaults["display"]["brightness"] == 50
    
    def test_generate_default_config(self, schema_manager, tmp_project_root, sample_schema):
        """Test generating default config from schema."""
        plugin_dir = tmp_project_root / "plugins" / "test_plugin"
        plugin_dir.mkdir(parents=True)
        schema_file = plugin_dir / "config_schema.json"
        schema_file.write_text(json.dumps(sample_schema))
        
        result = schema_manager.generate_default_config("test_plugin")
        
        assert result["enabled"] is True
        assert result["update_interval"] == 300
        assert "test_plugin" in schema_manager._defaults_cache
    
    def test_validate_config_against_schema_valid(self, schema_manager, sample_schema):
        """Test validating valid config against schema."""
        config = {
            "enabled": True,
            "update_interval": 300,
            "api_key": "test_key"
        }
        
        is_valid, errors = schema_manager.validate_config_against_schema(config, sample_schema)
        
        assert is_valid is True
        assert len(errors) == 0
    
    def test_validate_config_against_schema_invalid(self, schema_manager, sample_schema):
        """Test validating invalid config against schema."""
        config = {
            "enabled": "not a boolean",  # Wrong type
            "update_interval": 30,  # Below minimum
            # Missing required api_key
        }
        
        is_valid, errors = schema_manager.validate_config_against_schema(config, sample_schema)
        
        assert is_valid is False
        assert len(errors) > 0
    
    def test_validate_config_against_schema_with_errors(self, schema_manager, sample_schema):
        """Test validation with error collection."""
        config = {
            "enabled": "not a boolean",
            "update_interval": 30
        }
        
        is_valid, errors = schema_manager.validate_config_against_schema(config, sample_schema)
        
        assert is_valid is False
        assert len(errors) > 0
    
    def test_merge_with_defaults(self, schema_manager):
        """Test merging config with defaults."""
        config = {
            "enabled": False,
            "api_key": "custom_key"
        }
        defaults = {
            "enabled": True,
            "update_interval": 300
        }
        
        result = schema_manager.merge_with_defaults(config, defaults)
        
        assert result["enabled"] is False  # Config value takes precedence
        assert result["update_interval"] == 300  # Default value used
        assert result["api_key"] == "custom_key"  # Config value preserved
    
    def test_merge_with_defaults_nested(self, schema_manager):
        """Test merging nested config with defaults."""
        config = {
            "display": {
                "brightness": 75
            }
        }
        defaults = {
            "display": {
                "brightness": 50,
                "width": 64
            }
        }
        
        result = schema_manager.merge_with_defaults(config, defaults)
        
        assert result["display"]["brightness"] == 75  # Config takes precedence
        assert result["display"]["width"] == 64  # Default used
    
    def test_format_validation_error(self, schema_manager):
        """Test formatting validation error message."""
        error = ValidationError("Test error message", path=["enabled"])
        
        result = schema_manager._format_validation_error(error, "test_plugin")
        
        assert "test_plugin" in result or "enabled" in result
        assert isinstance(result, str)
    
    def test_merge_with_defaults_empty_config(self, schema_manager):
        """Test merging empty config with defaults."""
        config = {}
        defaults = {
            "enabled": True,
            "update_interval": 300
        }
        
        result = schema_manager.merge_with_defaults(config, defaults)
        
        assert result["enabled"] is True
        assert result["update_interval"] == 300
    
    def test_merge_with_defaults_empty_defaults(self, schema_manager):
        """Test merging config with empty defaults."""
        config = {
            "enabled": False,
            "api_key": "test"
        }
        defaults = {}
        
        result = schema_manager.merge_with_defaults(config, defaults)
        
        assert result["enabled"] is False
        assert result["api_key"] == "test"
    
    def test_load_schema_force_reload(self, schema_manager, tmp_project_root, sample_schema):
        """Test loading schema with cache disabled."""
        plugin_dir = tmp_project_root / "plugins" / "test_plugin"
        plugin_dir.mkdir(parents=True)
        schema_file = plugin_dir / "config_schema.json"
        schema_file.write_text(json.dumps(sample_schema))
        
        # Pre-populate cache with different data
        schema_manager._schema_cache["test_plugin"] = {"different": "data"}
        
        result = schema_manager.load_schema("test_plugin", use_cache=False)
        
        assert result == sample_schema  # Should load fresh, not from cache
    
    def test_generate_default_config_cached(self, schema_manager, tmp_project_root, sample_schema):
        """Test generating default config from cache."""
        plugin_dir = tmp_project_root / "plugins" / "test_plugin"
        plugin_dir.mkdir(parents=True)
        schema_file = plugin_dir / "config_schema.json"
        schema_file.write_text(json.dumps(sample_schema))
        
        # Pre-populate defaults cache
        schema_manager._defaults_cache["test_plugin"] = {"enabled": True, "update_interval": 300}
        
        result = schema_manager.generate_default_config("test_plugin", use_cache=True)
        
        assert result["enabled"] is True
        assert result["update_interval"] == 300
    
    def test_get_schema_path_plugin_repos(self, schema_manager, tmp_project_root, sample_schema):
        """Test finding schema in plugin-repos directory."""
        plugin_dir = tmp_project_root / "plugin-repos" / "test_plugin"
        plugin_dir.mkdir(parents=True)
        schema_file = plugin_dir / "config_schema.json"
        schema_file.write_text(json.dumps(sample_schema))
        
        result = schema_manager.get_schema_path("test_plugin")
        
        assert result == schema_file
    
    def test_extract_defaults_array(self, schema_manager):
        """Test extracting defaults from array schema."""
        array_schema = {
            "type": "object",
            "properties": {
                "items": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {
                                "type": "string",
                                "default": "item"
                            }
                        }
                    }
                }
            }
        }
        
        defaults = schema_manager.extract_defaults_from_schema(array_schema)
        
        assert "items" in defaults
        assert isinstance(defaults["items"], list)
