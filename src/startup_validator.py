"""
Startup Validator

Validates system configuration, plugins, and dependencies on startup.
Fails fast with clear error messages to prevent runtime issues.
"""

import os
import logging
from typing import Dict, Any, List, Optional, Tuple
from pathlib import Path
from src.exceptions import ConfigError, PluginError, CacheError
from src.logging_config import get_logger


class StartupValidator:
    """Validates system state on startup."""
    
    def __init__(self, config_manager: Any, plugin_manager: Optional[Any] = None) -> None:
        """
        Initialize the startup validator.
        
        Args:
            config_manager: ConfigManager instance
            plugin_manager: Optional PluginManager instance
        """
        self.config_manager = config_manager
        self.plugin_manager = plugin_manager
        self.logger = get_logger(__name__)
        self.errors: List[str] = []
        self.warnings: List[str] = []
    
    def validate_all(self) -> Tuple[bool, List[str], List[str]]:
        """
        Run all validation checks.
        
        Returns:
            Tuple of (is_valid, errors, warnings)
        """
        self.logger.info("Starting startup validation...")
        
        # Validate configuration
        self._validate_config()
        
        # Validate cache directory
        self._validate_cache_directory()
        
        # Validate display configuration
        self._validate_display_config()
        
        # Validate plugins if plugin manager is available
        if self.plugin_manager:
            self._validate_plugins()
        
        is_valid = len(self.errors) == 0
        
        if is_valid:
            self.logger.info("Startup validation passed")
            if self.warnings:
                self.logger.warning(f"Startup validation completed with {len(self.warnings)} warning(s)")
        else:
            self.logger.error(f"Startup validation failed with {len(self.errors)} error(s)")
        
        return (is_valid, self.errors.copy(), self.warnings.copy())
    
    def _validate_config(self) -> None:
        """Validate configuration files."""
        try:
            config = self.config_manager.load_config()
            
            # Check for required top-level keys
            required_keys = ['display', 'timezone']
            for key in required_keys:
                if key not in config:
                    self.errors.append(f"Missing required configuration key: {key}")
            
            # Validate display configuration
            display_config = config.get('display', {})
            if not display_config:
                self.errors.append("Display configuration is missing or empty")
            
        except ConfigError as e:
            self.errors.append(f"Configuration error: {e}")
        except Exception as e:
            self.errors.append(f"Unexpected error validating configuration: {e}")
    
    def _validate_cache_directory(self) -> None:
        """Validate cache directory permissions."""
        try:
            from src.cache_manager import CacheManager
            cache_manager = CacheManager()
            cache_dir = cache_manager.get_cache_dir()
            
            if not cache_dir:
                self.warnings.append("Cache directory not available - caching will be disabled")
                return
            
            # Check if directory exists and is writable
            if not os.path.exists(cache_dir):
                self.errors.append(f"Cache directory does not exist: {cache_dir}")
                return
            
            if not os.access(cache_dir, os.W_OK):
                self.errors.append(f"Cache directory is not writable: {cache_dir}")
                return
            
            # Test write access
            test_file = os.path.join(cache_dir, '.startup_test')
            try:
                with open(test_file, 'w') as f:
                    f.write('test')
                os.remove(test_file)
            except (IOError, OSError) as e:
                self.errors.append(f"Cannot write to cache directory {cache_dir}: {e}")
        
        except Exception as e:
            self.warnings.append(f"Could not validate cache directory: {e}")
    
    def _validate_display_config(self) -> None:
        """Validate display configuration."""
        try:
            config = self.config_manager.get_config()
            display_config = config.get('display', {})
            
            if not display_config:
                self.errors.append("Display configuration is missing")
                return
            
            hardware_config = display_config.get('hardware', {})
            if not hardware_config:
                self.errors.append("Display hardware configuration is missing")
                return
            
            # Check required hardware settings
            required_hardware = ['rows', 'cols']
            for key in required_hardware:
                if key not in hardware_config:
                    self.warnings.append(f"Display hardware setting '{key}' not specified, using default")
        
        except Exception as e:
            self.warnings.append(f"Could not validate display configuration: {e}")
    
    def _validate_plugins(self) -> None:
        """Validate plugin configurations and dependencies."""
        if not self.plugin_manager:
            return
        
        try:
            # Get enabled plugins from config
            config = self.config_manager.get_config()
            discovered_plugins = self.plugin_manager.discover_plugins()
            
            # Check for enabled plugins that don't exist
            for plugin_id, plugin_config in config.items():
                # Skip non-plugin config sections
                if plugin_id in ['display', 'schedule', 'timezone', 'plugin_system']:
                    continue
                
                if not isinstance(plugin_config, dict):
                    continue
                
                if plugin_config.get('enabled', False):
                    if plugin_id not in discovered_plugins:
                        self.warnings.append(f"Plugin '{plugin_id}' is enabled but not found in plugins directory")
            
            # Validate plugin configurations
            for plugin_id in discovered_plugins:
                plugin_config = config.get(plugin_id, {})
                if plugin_config.get('enabled', False):
                    # Check if plugin can be loaded (without actually loading it)
                    plugin_dir = self.plugin_manager.get_plugin_directory(plugin_id)
                    if plugin_dir:
                        manifest_path = Path(plugin_dir) / "manifest.json"
                        if not manifest_path.exists():
                            self.errors.append(f"Plugin '{plugin_id}' manifest.json not found")
        
        except Exception as e:
            self.warnings.append(f"Could not validate plugins: {e}")
    
    def raise_on_errors(self) -> None:
        """
        Raise exceptions if validation errors exist.
        
        Raises:
            ConfigError: If configuration validation fails
            CacheError: If cache validation fails
            PluginError: If plugin validation fails
        """
        if not self.errors:
            return
        
        # Group errors by type
        config_errors = [e for e in self.errors if 'configuration' in e.lower() or 'config' in e.lower()]
        cache_errors = [e for e in self.errors if 'cache' in e.lower()]
        plugin_errors = [e for e in self.errors if 'plugin' in e.lower()]
        other_errors = [e for e in self.errors if e not in config_errors + cache_errors + plugin_errors]
        
        # Raise appropriate exceptions
        if config_errors:
            raise ConfigError("Configuration validation failed", context={'errors': config_errors})
        
        if cache_errors:
            raise CacheError("Cache validation failed", context={'errors': cache_errors})
        
        if plugin_errors:
            raise PluginError("Plugin validation failed", context={'errors': plugin_errors})
        
        if other_errors:
            raise ConfigError("Startup validation failed", context={'errors': other_errors})

