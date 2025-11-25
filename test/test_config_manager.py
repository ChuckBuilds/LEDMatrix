"""
Tests for ConfigManager.

Tests configuration loading, migration, secrets handling, and validation.
"""

import pytest
import json
import os
import tempfile
from pathlib import Path
from unittest.mock import Mock, patch, mock_open
from src.config_manager import ConfigManager


class TestConfigManagerInitialization:
    """Test ConfigManager initialization."""
    
    def test_init_with_default_paths(self):
        """Test initialization with default paths."""
        manager = ConfigManager()
        assert manager.config_path == "config/config.json"
        assert manager.secrets_path == "config/config_secrets.json"
        assert manager.template_path == "config/config.template.json"
        assert manager.config == {}
    
    def test_init_with_custom_paths(self):
        """Test initialization with custom paths."""
        manager = ConfigManager(
            config_path="custom/config.json",
            secrets_path="custom/secrets.json"
        )
        assert manager.config_path == "custom/config.json"
        assert manager.secrets_path == "custom/secrets.json"
    
    def test_get_config_path(self):
        """Test getting config path."""
        manager = ConfigManager(config_path="test/config.json")
        assert manager.get_config_path() == "test/config.json"
    
    def test_get_secrets_path(self):
        """Test getting secrets path."""
        manager = ConfigManager(secrets_path="test/secrets.json")
        assert manager.get_secrets_path() == "test/secrets.json"


class TestConfigLoading:
    """Test configuration loading."""
    
    def test_load_config_from_existing_file(self, tmp_path):
        """Test loading config from existing file."""
        config_file = tmp_path / "config.json"
        config_data = {"timezone": "UTC", "display": {"hardware": {"rows": 32}}}
        
        with open(config_file, 'w') as f:
            json.dump(config_data, f)
        
        manager = ConfigManager(config_path=str(config_file))
        loaded = manager.load_config()
        
        assert loaded["timezone"] == "UTC"
        assert loaded["display"]["hardware"]["rows"] == 32
    
    def test_load_config_creates_from_template(self, tmp_path):
        """Test that config is created from template if missing."""
        template_file = tmp_path / "template.json"
        config_file = tmp_path / "config.json"
        template_data = {"timezone": "UTC", "display": {}}
        
        with open(template_file, 'w') as f:
            json.dump(template_data, f)
        
        manager = ConfigManager(
            config_path=str(config_file),
            secrets_path=str(tmp_path / "secrets.json")
        )
        manager.template_path = str(template_file)
        
        loaded = manager.load_config()
        
        assert os.path.exists(config_file)
        assert loaded["timezone"] == "UTC"
    
    def test_load_config_merges_secrets(self, tmp_path):
        """Test that secrets are merged into config."""
        config_file = tmp_path / "config.json"
        secrets_file = tmp_path / "secrets.json"
        
        config_data = {"timezone": "UTC", "plugin1": {"enabled": True}}
        secrets_data = {"plugin1": {"api_key": "secret123"}}
        
        with open(config_file, 'w') as f:
            json.dump(config_data, f)
        with open(secrets_file, 'w') as f:
            json.dump(secrets_data, f)
        
        manager = ConfigManager(
            config_path=str(config_file),
            secrets_path=str(secrets_file)
        )
        loaded = manager.load_config()
        
        assert loaded["plugin1"]["enabled"] is True
        assert loaded["plugin1"]["api_key"] == "secret123"
    
    def test_load_config_handles_missing_secrets_gracefully(self, tmp_path):
        """Test that missing secrets file doesn't cause error."""
        config_file = tmp_path / "config.json"
        config_data = {"timezone": "UTC"}
        
        with open(config_file, 'w') as f:
            json.dump(config_data, f)
        
        manager = ConfigManager(
            config_path=str(config_file),
            secrets_path=str(tmp_path / "nonexistent.json")
        )
        loaded = manager.load_config()
        
        assert loaded["timezone"] == "UTC"
    
    def test_load_config_handles_invalid_json(self, tmp_path):
        """Test that invalid JSON raises appropriate error."""
        config_file = tmp_path / "config.json"
        
        with open(config_file, 'w') as f:
            f.write("invalid json {")
        
        manager = ConfigManager(config_path=str(config_file))
        
        with pytest.raises(json.JSONDecodeError):
            manager.load_config()
    
    def test_get_config_loads_if_not_loaded(self, tmp_path):
        """Test that get_config loads config if not already loaded."""
        config_file = tmp_path / "config.json"
        config_data = {"timezone": "America/New_York"}
        
        with open(config_file, 'w') as f:
            json.dump(config_data, f)
        
        manager = ConfigManager(config_path=str(config_file))
        config = manager.get_config()
        
        assert config["timezone"] == "America/New_York"


class TestConfigMigration:
    """Test configuration migration."""
    
    def test_migration_adds_new_keys(self, tmp_path):
        """Test that migration adds new keys from template."""
        config_file = tmp_path / "config.json"
        template_file = tmp_path / "template.json"
        
        current_data = {"timezone": "UTC"}
        template_data = {
            "timezone": "UTC",
            "display": {"hardware": {"rows": 32}},
            "new_key": "new_value"
        }
        
        with open(config_file, 'w') as f:
            json.dump(current_data, f)
        with open(template_file, 'w') as f:
            json.dump(template_data, f)
        
        manager = ConfigManager(config_path=str(config_file))
        manager.template_path = str(template_file)
        manager.config = current_data.copy()
        
        manager._migrate_config()
        
        assert "new_key" in manager.config
        assert manager.config["new_key"] == "new_value"
        assert manager.config["display"]["hardware"]["rows"] == 32
    
    def test_migration_creates_backup(self, tmp_path):
        """Test that migration creates backup file."""
        config_file = tmp_path / "config.json"
        template_file = tmp_path / "template.json"
        backup_file = tmp_path / "config.json.backup"
        
        current_data = {"timezone": "UTC"}
        template_data = {"timezone": "UTC", "new_key": "new_value"}
        
        with open(config_file, 'w') as f:
            json.dump(current_data, f)
        with open(template_file, 'w') as f:
            json.dump(template_data, f)
        
        manager = ConfigManager(config_path=str(config_file))
        manager.template_path = str(template_file)
        manager.config = current_data.copy()
        
        manager._migrate_config()
        
        assert backup_file.exists()
        with open(backup_file, 'r') as f:
            backup_data = json.load(f)
            assert backup_data == current_data
    
    def test_migration_skips_if_not_needed(self, tmp_path):
        """Test that migration is skipped if config is up to date."""
        config_file = tmp_path / "config.json"
        template_file = tmp_path / "template.json"
        
        config_data = {"timezone": "UTC", "display": {}}
        template_data = {"timezone": "UTC", "display": {}}
        
        with open(config_file, 'w') as f:
            json.dump(config_data, f)
        with open(template_file, 'w') as f:
            json.dump(template_data, f)
        
        manager = ConfigManager(config_path=str(config_file))
        manager.template_path = str(template_file)
        manager.config = config_data.copy()
        
        # Should not raise or create backup
        manager._migrate_config()
        
        backup_file = tmp_path / "config.json.backup"
        assert not backup_file.exists()


class TestConfigSaving:
    """Test configuration saving."""
    
    def test_save_config_strips_secrets(self, tmp_path):
        """Test that save_config strips secrets from saved file."""
        config_file = tmp_path / "config.json"
        secrets_file = tmp_path / "secrets.json"
        
        config_data = {
            "timezone": "UTC",
            "plugin1": {
                "enabled": True,
                "api_key": "secret123"
            }
        }
        secrets_data = {
            "plugin1": {
                "api_key": "secret123"
            }
        }
        
        with open(secrets_file, 'w') as f:
            json.dump(secrets_data, f)
        
        manager = ConfigManager(
            config_path=str(config_file),
            secrets_path=str(secrets_file)
        )
        manager.config = config_data.copy()
        
        manager.save_config(config_data)
        
        # Verify secrets were stripped
        with open(config_file, 'r') as f:
            saved_data = json.load(f)
            assert "api_key" not in saved_data["plugin1"]
            assert saved_data["plugin1"]["enabled"] is True
    
    def test_save_config_updates_in_memory_config(self, tmp_path):
        """Test that save_config updates in-memory config."""
        config_file = tmp_path / "config.json"
        config_data = {"timezone": "America/New_York"}
        
        with open(config_file, 'w') as f:
            json.dump({"timezone": "UTC"}, f)
        
        manager = ConfigManager(config_path=str(config_file))
        manager.load_config()
        
        manager.save_config(config_data)
        
        assert manager.config["timezone"] == "America/New_York"
    
    def test_save_raw_file_content(self, tmp_path):
        """Test saving raw file content."""
        config_file = tmp_path / "config.json"
        config_data = {"timezone": "UTC", "display": {}}
        
        manager = ConfigManager(config_path=str(config_file))
        manager.save_raw_file_content('main', config_data)
        
        assert config_file.exists()
        with open(config_file, 'r') as f:
            saved_data = json.load(f)
            assert saved_data == config_data
    
    def test_save_raw_file_content_invalid_type(self):
        """Test that invalid file type raises ValueError."""
        manager = ConfigManager()
        
        with pytest.raises(ValueError, match="Invalid file_type"):
            manager.save_raw_file_content('invalid', {})


class TestSecretsHandling:
    """Test secrets handling."""
    
    def test_get_secret(self, tmp_path):
        """Test getting a secret value."""
        secrets_file = tmp_path / "secrets.json"
        secrets_data = {"api_key": "secret123", "token": "token456"}
        
        with open(secrets_file, 'w') as f:
            json.dump(secrets_data, f)
        
        manager = ConfigManager(secrets_path=str(secrets_file))
        
        assert manager.get_secret("api_key") == "secret123"
        assert manager.get_secret("token") == "token456"
        assert manager.get_secret("nonexistent") is None
    
    def test_get_secret_handles_missing_file(self):
        """Test that get_secret handles missing secrets file."""
        manager = ConfigManager(secrets_path="nonexistent.json")
        
        assert manager.get_secret("api_key") is None
    
    def test_get_secret_handles_invalid_json(self, tmp_path):
        """Test that get_secret handles invalid JSON gracefully."""
        secrets_file = tmp_path / "secrets.json"
        
        with open(secrets_file, 'w') as f:
            f.write("invalid json {")
        
        manager = ConfigManager(secrets_path=str(secrets_file))
        
        # Should return None on error
        assert manager.get_secret("api_key") is None


class TestConfigHelpers:
    """Test helper methods."""
    
    def test_get_timezone(self, tmp_path):
        """Test getting timezone."""
        config_file = tmp_path / "config.json"
        config_data = {"timezone": "America/New_York"}
        
        with open(config_file, 'w') as f:
            json.dump(config_data, f)
        
        manager = ConfigManager(config_path=str(config_file))
        manager.load_config()
        
        assert manager.get_timezone() == "America/New_York"
    
    def test_get_timezone_default(self, tmp_path):
        """Test that get_timezone returns default if not set."""
        config_file = tmp_path / "config.json"
        config_data = {}
        
        with open(config_file, 'w') as f:
            json.dump(config_data, f)
        
        manager = ConfigManager(config_path=str(config_file))
        manager.load_config()
        
        assert manager.get_timezone() == "UTC"
    
    def test_get_display_config(self, tmp_path):
        """Test getting display config."""
        config_file = tmp_path / "config.json"
        config_data = {"display": {"hardware": {"rows": 32}}}
        
        with open(config_file, 'w') as f:
            json.dump(config_data, f)
        
        manager = ConfigManager(config_path=str(config_file))
        manager.load_config()
        
        display_config = manager.get_display_config()
        assert display_config["hardware"]["rows"] == 32
    
    def test_get_clock_config(self, tmp_path):
        """Test getting clock config."""
        config_file = tmp_path / "config.json"
        config_data = {"clock": {"format": "12h"}}
        
        with open(config_file, 'w') as f:
            json.dump(config_data, f)
        
        manager = ConfigManager(config_path=str(config_file))
        manager.load_config()
        
        clock_config = manager.get_clock_config()
        assert clock_config["format"] == "12h"


class TestPluginConfigManagement:
    """Test plugin configuration management."""
    
    def test_cleanup_plugin_config(self, tmp_path):
        """Test cleaning up plugin configuration."""
        config_file = tmp_path / "config.json"
        secrets_file = tmp_path / "secrets.json"
        
        config_data = {
            "plugin1": {"enabled": True},
            "plugin2": {"enabled": False}
        }
        secrets_data = {
            "plugin1": {"api_key": "secret123"}
        }
        
        with open(config_file, 'w') as f:
            json.dump(config_data, f)
        with open(secrets_file, 'w') as f:
            json.dump(secrets_data, f)
        
        manager = ConfigManager(
            config_path=str(config_file),
            secrets_path=str(secrets_file)
        )
        manager.cleanup_plugin_config("plugin1")
        
        with open(config_file, 'r') as f:
            saved_config = json.load(f)
            assert "plugin1" not in saved_config
            assert "plugin2" in saved_config
        
        with open(secrets_file, 'r') as f:
            saved_secrets = json.load(f)
            assert "plugin1" not in saved_secrets
    
    def test_cleanup_orphaned_plugin_configs(self, tmp_path):
        """Test cleaning up orphaned plugin configs."""
        config_file = tmp_path / "config.json"
        secrets_file = tmp_path / "secrets.json"
        
        config_data = {
            "plugin1": {"enabled": True},
            "plugin2": {"enabled": False},
            "orphaned_plugin": {"enabled": True}
        }
        secrets_data = {
            "orphaned_plugin": {"api_key": "secret"}
        }
        
        with open(config_file, 'w') as f:
            json.dump(config_data, f)
        with open(secrets_file, 'w') as f:
            json.dump(secrets_data, f)
        
        manager = ConfigManager(
            config_path=str(config_file),
            secrets_path=str(secrets_file)
        )
        removed = manager.cleanup_orphaned_plugin_configs(["plugin1", "plugin2"])
        
        assert "orphaned_plugin" in removed
        
        with open(config_file, 'r') as f:
            saved_config = json.load(f)
            assert "orphaned_plugin" not in saved_config
            assert "plugin1" in saved_config
            assert "plugin2" in saved_config


class TestErrorHandling:
    """Test error handling scenarios."""
    
    def test_load_config_file_not_found_without_template(self, tmp_path):
        """Test that missing config file raises error if no template."""
        manager = ConfigManager(config_path=str(tmp_path / "nonexistent.json"))
        manager.template_path = str(tmp_path / "nonexistent_template.json")
        
        with pytest.raises(FileNotFoundError):
            manager.load_config()
    
    def test_get_raw_file_content_invalid_type(self):
        """Test that invalid file type raises ValueError."""
        manager = ConfigManager()
        
        with pytest.raises(ValueError, match="Invalid file_type"):
            manager.get_raw_file_content('invalid')
    
    def test_get_raw_file_content_missing_main_file(self, tmp_path):
        """Test that missing main config file raises FileNotFoundError."""
        manager = ConfigManager(config_path=str(tmp_path / "nonexistent.json"))
        
        with pytest.raises(FileNotFoundError):
            manager.get_raw_file_content('main')
    
    def test_get_raw_file_content_missing_secrets_returns_empty(self, tmp_path):
        """Test that missing secrets file returns empty dict."""
        manager = ConfigManager(secrets_path=str(tmp_path / "nonexistent.json"))
        
        result = manager.get_raw_file_content('secrets')
        assert result == {}

