"""
Config Helper

Handles configuration management and validation for LED matrix plugins.
Extracted from LEDMatrix core to provide reusable functionality for plugins.
"""

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Union


class ConfigHelper:
    """
    Helper class for configuration management and validation.
    
    Provides functionality for:
    - Loading and saving configuration files
    - Validating configuration against schemas
    - Merging configurations
    - Getting configuration values with defaults
    - Configuration schema validation
    """
    
    def __init__(self, logger: Optional[logging.Logger] = None):
        """
        Initialize the ConfigHelper.
        
        Args:
            logger: Optional logger instance
        """
        self.logger = logger or logging.getLogger(__name__)
    
    def load_config(self, config_path: Union[str, Path]) -> Dict[str, Any]:
        """
        Load configuration from a JSON file.
        
        Args:
            config_path: Path to configuration file
            
        Returns:
            Configuration dictionary
        """
        config_path = Path(config_path)
        
        try:
            if not config_path.exists():
                self.logger.warning(f"Configuration file not found: {config_path}")
                return {}
            
            with open(config_path, 'r', encoding='utf-8') as f:
                config = json.load(f)
            
            self.logger.debug(f"Loaded configuration from {config_path}")
            return config
            
        except json.JSONDecodeError as e:
            self.logger.error(f"Invalid JSON in configuration file {config_path}: {e}")
            return {}
        except Exception as e:
            self.logger.error(f"Error loading configuration from {config_path}: {e}")
            return {}
    
    def save_config(self, config: Dict[str, Any], config_path: Union[str, Path]) -> bool:
        """
        Save configuration to a JSON file.
        
        Args:
            config: Configuration dictionary to save
            config_path: Path to save configuration file
            
        Returns:
            True if successful, False otherwise
        """
        config_path = Path(config_path)
        
        try:
            # Ensure directory exists
            config_path.parent.mkdir(parents=True, exist_ok=True)
            
            with open(config_path, 'w', encoding='utf-8') as f:
                json.dump(config, f, indent=2, ensure_ascii=False)
            
            self.logger.debug(f"Saved configuration to {config_path}")
            return True
            
        except Exception as e:
            self.logger.error(f"Error saving configuration to {config_path}: {e}")
            return False
    
    def get_config_value(self, config: Dict[str, Any], key: str, 
                        default: Any = None, required: bool = False) -> Any:
        """
        Get a configuration value with optional default.
        
        Args:
            config: Configuration dictionary
            key: Configuration key (supports dot notation like 'display.width')
            default: Default value if key not found
            required: If True, raise error if key not found
            
        Returns:
            Configuration value or default
        """
        try:
            # Support dot notation for nested keys
            keys = key.split('.')
            value = config
            
            for k in keys:
                if isinstance(value, dict) and k in value:
                    value = value[k]
                else:
                    if required:
                        raise KeyError(f"Required configuration key not found: {key}")
                    return default
            
            return value
            
        except Exception as e:
            if required:
                raise
            self.logger.warning(f"Error getting config value for {key}: {e}")
            return default
    
    def set_config_value(self, config: Dict[str, Any], key: str, value: Any) -> None:
        """
        Set a configuration value.
        
        Args:
            config: Configuration dictionary to modify
            key: Configuration key (supports dot notation)
            value: Value to set
        """
        try:
            # Support dot notation for nested keys
            keys = key.split('.')
            current = config
            
            # Navigate to parent of target key
            for k in keys[:-1]:
                if k not in current:
                    current[k] = {}
                current = current[k]
            
            # Set the value
            current[keys[-1]] = value
            
        except Exception as e:
            self.logger.error(f"Error setting config value for {key}: {e}")
    
    def merge_configs(self, base_config: Dict[str, Any], 
                     override_config: Dict[str, Any]) -> Dict[str, Any]:
        """
        Merge two configuration dictionaries.
        
        Args:
            base_config: Base configuration
            override_config: Configuration to merge in (takes precedence)
            
        Returns:
            Merged configuration dictionary
        """
        merged = base_config.copy()
        
        for key, value in override_config.items():
            if key in merged and isinstance(merged[key], dict) and isinstance(value, dict):
                # Recursively merge nested dictionaries
                merged[key] = self.merge_configs(merged[key], value)
            else:
                # Override with new value
                merged[key] = value
        
        return merged
    
    def validate_config(self, config: Dict[str, Any], 
                       schema: Optional[Dict[str, Any]] = None) -> bool:
        """
        Validate configuration against a schema.
        
        Args:
            config: Configuration to validate
            schema: Validation schema (optional)
            
        Returns:
            True if valid, False otherwise
        """
        if schema is None:
            # Basic validation - just check if it's a dictionary
            return isinstance(config, dict)
        
        try:
            return self._validate_against_schema(config, schema)
        except Exception as e:
            self.logger.error(f"Configuration validation error: {e}")
            return False
    
    def get_plugin_config(self, config: Dict[str, Any], plugin_id: str) -> Dict[str, Any]:
        """
        Get plugin-specific configuration.
        
        Args:
            config: Full configuration dictionary
            plugin_id: Plugin identifier
            
        Returns:
            Plugin-specific configuration
        """
        plugin_key = f"{plugin_id}_config"
        return config.get(plugin_key, {})
    
    def create_default_config(self, plugin_id: str, 
                            default_values: Dict[str, Any]) -> Dict[str, Any]:
        """
        Create a default configuration for a plugin.
        
        Args:
            plugin_id: Plugin identifier
            default_values: Default configuration values
            
        Returns:
            Default configuration dictionary
        """
        return {
            f"{plugin_id}_config": default_values
        }
    
    def validate_required_keys(self, config: Dict[str, Any], 
                             required_keys: List[str]) -> List[str]:
        """
        Validate that required keys are present in configuration.
        
        Args:
            config: Configuration to validate
            required_keys: List of required keys
            
        Returns:
            List of missing keys
        """
        missing_keys = []
        
        for key in required_keys:
            if not self._has_key(config, key):
                missing_keys.append(key)
        
        return missing_keys
    
    def get_display_config(self, config: Dict[str, Any]) -> Dict[str, Any]:
        """
        Get display-related configuration.
        
        Args:
            config: Full configuration dictionary
            
        Returns:
            Display configuration
        """
        return config.get('display', {})
    
    def get_sports_config(self, config: Dict[str, Any], sport: str) -> Dict[str, Any]:
        """
        Get sport-specific configuration.
        
        Args:
            config: Full configuration dictionary
            sport: Sport name (e.g., 'basketball', 'football')
            
        Returns:
            Sport-specific configuration
        """
        return config.get(f"{sport}_scoreboard", {})
    
    def is_plugin_enabled(self, config: Dict[str, Any], plugin_id: str) -> bool:
        """
        Check if a plugin is enabled.
        
        Args:
            config: Full configuration dictionary
            plugin_id: Plugin identifier
            
        Returns:
            True if plugin is enabled
        """
        plugin_config = self.get_plugin_config(config, plugin_id)
        return plugin_config.get('enabled', True)
    
    def get_favorite_teams(self, config: Dict[str, Any], sport: str) -> List[str]:
        """
        Get favorite teams for a sport.
        
        Args:
            config: Full configuration dictionary
            sport: Sport name
            
        Returns:
            List of favorite team abbreviations
        """
        sport_config = self.get_sports_config(config, sport)
        return sport_config.get('favorite_teams', [])
    
    def get_display_modes(self, config: Dict[str, Any], sport: str) -> Dict[str, bool]:
        """
        Get display modes for a sport.
        
        Args:
            config: Full configuration dictionary
            sport: Sport name
            
        Returns:
            Dictionary of display modes and their enabled status
        """
        sport_config = self.get_sports_config(config, sport)
        return sport_config.get('display_modes', {})
    
    def _validate_against_schema(self, config: Dict[str, Any], 
                                schema: Dict[str, Any]) -> bool:
        """Validate configuration against a schema."""
        # This is a simplified schema validation
        # In a real implementation, you might use a library like jsonschema
        
        for key, schema_info in schema.items():
            if key not in config:
                if schema_info.get('required', False):
                    self.logger.error(f"Missing required configuration key: {key}")
                    return False
                continue
            
            value = config[key]
            expected_type = schema_info.get('type')
            
            if expected_type and not isinstance(value, expected_type):
                self.logger.error(f"Configuration key {key} has wrong type. Expected {expected_type}, got {type(value)}")
                return False
            
            # Validate allowed values
            allowed_values = schema_info.get('allowed_values')
            if allowed_values and value not in allowed_values:
                self.logger.error(f"Configuration key {key} has invalid value: {value}. Allowed: {allowed_values}")
                return False
        
        return True
    
    def _has_key(self, config: Dict[str, Any], key: str) -> bool:
        """Check if a key exists in configuration (supports dot notation)."""
        try:
            keys = key.split('.')
            current = config
            
            for k in keys:
                if not isinstance(current, dict) or k not in current:
                    return False
                current = current[k]
            
            return True
        except Exception:
            return False
