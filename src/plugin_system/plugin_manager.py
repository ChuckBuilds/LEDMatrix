"""
Plugin Manager

Manages plugin discovery, loading, and lifecycle for the LEDMatrix system.
Handles dynamic plugin loading from the plugins/ directory.

API Version: 1.0.0
"""

import os
import json
import importlib
import importlib.util
import sys
import subprocess
import time
from pathlib import Path
from typing import Dict, List, Optional, Any
import logging
from src.exceptions import PluginError
from src.logging_config import get_logger
from src.plugin_system.plugin_loader import PluginLoader
from src.plugin_system.plugin_executor import PluginExecutor
from src.plugin_system.plugin_state import PluginStateManager, PluginState
from src.plugin_system.schema_manager import SchemaManager
from src.common.permission_utils import (
    ensure_directory_permissions,
    get_plugin_dir_mode
)


class PluginManager:
    """
    Manages plugin discovery, loading, and lifecycle.
    
    The PluginManager is responsible for:
    - Discovering plugins in the plugins/ directory
    - Loading plugin modules and instantiating plugin classes
    - Managing plugin lifecycle (load, unload, reload)
    - Providing access to loaded plugins
    - Maintaining plugin manifests
    
    Uses composition with specialized components:
    - PluginLoader: Handles module loading and dependency installation
    - PluginExecutor: Handles plugin execution with timeout and error isolation
    - PluginStateManager: Manages plugin state machine
    """
    
    def __init__(self, plugins_dir: str = "plugins", 
                 config_manager: Optional[Any] = None, 
                 display_manager: Optional[Any] = None, 
                 cache_manager: Optional[Any] = None, 
                 font_manager: Optional[Any] = None) -> None:
        """
        Initialize the Plugin Manager.
        
        Args:
            plugins_dir: Path to the plugins directory
            config_manager: Configuration manager instance
            display_manager: Display manager instance
            cache_manager: Cache manager instance
            font_manager: Font manager instance
        """
        self.plugins_dir: Path = Path(plugins_dir)
        self.config_manager: Optional[Any] = config_manager
        self.display_manager: Optional[Any] = display_manager
        self.cache_manager: Optional[Any] = cache_manager
        self.font_manager: Optional[Any] = font_manager
        self.logger: logging.Logger = get_logger(__name__)
        
        # Initialize plugin system components
        self.plugin_loader = PluginLoader(logger=self.logger)
        self.plugin_executor = PluginExecutor(default_timeout=30.0, logger=self.logger)
        self.state_manager = PluginStateManager(logger=self.logger)
        self.schema_manager = SchemaManager(plugins_dir=self.plugins_dir, logger=self.logger)
        
        # Active plugins
        self.plugins: Dict[str, Any] = {}
        self.plugin_manifests: Dict[str, Dict[str, Any]] = {}
        self.plugin_modules: Dict[str, Any] = {}
        self.plugin_last_update: Dict[str, float] = {}
        
        # Health tracking (optional, set by display_controller if available)
        self.health_tracker = None
        self.resource_monitor = None
        
        # Ensure plugins directory exists with proper permissions
        try:
            ensure_directory_permissions(self.plugins_dir, get_plugin_dir_mode())
        except (OSError, PermissionError) as e:
            self.logger.error("Could not create plugins directory %s: %s", self.plugins_dir, e, exc_info=True)
            raise PluginError(f"Could not create plugins directory: {self.plugins_dir}", context={'error': str(e)}) from e

    def _scan_directory_for_plugins(self, directory: Path) -> List[str]:
        """
        Scan a directory for plugins.
        
        Args:
            directory: Directory to scan
            
        Returns:
            List of plugin IDs found
        """
        plugin_ids = []
        
        if not directory.exists():
            return plugin_ids
        
        try:
            for item in directory.iterdir():
                if not item.is_dir():
                    continue
                
                manifest_path = item / "manifest.json"
                if manifest_path.exists():
                    try:
                        with open(manifest_path, 'r', encoding='utf-8') as f:
                            manifest = json.load(f)
                            plugin_id = manifest.get('id')
                            if plugin_id:
                                plugin_ids.append(plugin_id)
                                self.plugin_manifests[plugin_id] = manifest
                                
                                # Store directory mapping
                                if not hasattr(self, 'plugin_directories'):
                                    self.plugin_directories = {}
                                self.plugin_directories[plugin_id] = item
                    except (json.JSONDecodeError, PermissionError, OSError) as e:
                        self.logger.warning("Error reading manifest from %s: %s", manifest_path, e, exc_info=True)
                        continue
        except (OSError, PermissionError) as e:
            self.logger.error("Error scanning directory %s: %s", directory, e, exc_info=True)
        
        return plugin_ids
    
    def discover_plugins(self) -> List[str]:
        """
        Discover all plugins in the plugins directory.
        
        Returns:
            List of plugin IDs
        """
        self.logger.info("Discovering plugins in %s", self.plugins_dir)
        plugin_ids = self._scan_directory_for_plugins(self.plugins_dir)
        self.logger.info("Discovered %d plugin(s)", len(plugin_ids))
        return plugin_ids

    def _get_dependency_marker_path(self, plugin_id: str) -> Path:
        """Get path to dependency installation marker file."""
        plugin_dir = self.plugins_dir / plugin_id
        if not plugin_dir.exists():
            # Try with ledmatrix- prefix
            plugin_dir = self.plugins_dir / f"ledmatrix-{plugin_id}"
        return plugin_dir / ".dependencies_installed"

    def _check_dependencies_installed(self, plugin_id: str) -> bool:
        """Check if dependencies are already installed for a plugin."""
        marker_path = self._get_dependency_marker_path(plugin_id)
        return marker_path.exists()

    def _mark_dependencies_installed(self, plugin_id: str) -> None:
        """Mark dependencies as installed for a plugin."""
        marker_path = self._get_dependency_marker_path(plugin_id)
        try:
            marker_path.touch()
            # Set proper file permissions after creating marker
            from src.common.permission_utils import (
                ensure_file_permissions,
                get_plugin_file_mode
            )
            ensure_file_permissions(marker_path, get_plugin_file_mode())
        except (OSError, PermissionError) as e:
            self.logger.warning("Could not create dependency marker for %s: %s", plugin_id, e)

    def _remove_dependency_marker(self, plugin_id: str) -> None:
        """Remove dependency installation marker."""
        marker_path = self._get_dependency_marker_path(plugin_id)
        try:
            if marker_path.exists():
                marker_path.unlink()
        except (OSError, PermissionError) as e:
            self.logger.warning("Could not remove dependency marker for %s: %s", plugin_id, e)

    def _install_plugin_dependencies(self, requirements_file: Path) -> bool:
        """
        Install plugin dependencies from requirements.txt.
        
        Args:
            requirements_file: Path to requirements.txt
            
        Returns:
            True if installation succeeded or not needed, False on error
        """
        try:
            self.logger.info("Installing dependencies from %s", requirements_file)
            result = subprocess.run(
                [sys.executable, "-m", "pip", "install", "--break-system-packages", "--no-cache-dir", "-r", str(requirements_file)],
                capture_output=True,
                text=True,
                timeout=300,
                check=False
            )
            
            if result.returncode == 0:
                self.logger.info("Dependencies installed successfully")
                return True
            else:
                self.logger.warning("Dependency installation returned non-zero exit code: %s", result.stderr)
                return False
        except subprocess.TimeoutExpired:
            self.logger.error("Dependency installation timed out")
            return False
        except FileNotFoundError as e:
            self.logger.warning("Command not found: %s. Skipping dependency installation", e)
            return True
        except Exception as e:
            self.logger.error("Unexpected error installing dependencies: %s", e, exc_info=True)
            return True

    def load_plugin(self, plugin_id: str) -> bool:
        """
        Load a plugin by ID.
        
        This method:
        1. Checks if plugin is already loaded
        2. Validates the manifest exists
        3. Uses PluginLoader to import module and instantiate plugin
        4. Validates the plugin configuration
        5. Stores the plugin instance
        6. Updates plugin state
        
        Args:
            plugin_id: Plugin identifier
            
        Returns:
            True if loaded successfully, False otherwise
        """
        if plugin_id in self.plugins:
            self.logger.warning("Plugin %s already loaded", plugin_id)
            return True
        
        manifest = self.plugin_manifests.get(plugin_id)
        if not manifest:
            self.logger.error("No manifest found for plugin: %s", plugin_id)
            self.state_manager.set_state(plugin_id, PluginState.ERROR)
            return False
        
        try:
            # Update state to LOADED
            self.state_manager.set_state(plugin_id, PluginState.LOADED)
            
            # Find plugin directory using PluginLoader
            plugin_directories = getattr(self, 'plugin_directories', None)
            plugin_dir = self.plugin_loader.find_plugin_directory(
                plugin_id,
                self.plugins_dir,
                plugin_directories
            )
            
            if plugin_dir is None:
                self.logger.error("Plugin directory not found: %s", plugin_id)
                self.logger.error("Searched in: %s", self.plugins_dir)
                self.state_manager.set_state(plugin_id, PluginState.ERROR)
                return False
            
            # Update mapping if found via search
            if plugin_directories is None or plugin_id not in plugin_directories:
                if not hasattr(self, 'plugin_directories'):
                    self.plugin_directories = {}
                self.plugin_directories[plugin_id] = plugin_dir
            
            # Get plugin config
            if self.config_manager:
                full_config = self.config_manager.load_config()
                config = full_config.get(plugin_id, {})
            else:
                config = {}
            
            # Merge config with schema defaults to ensure all defaults are applied
            try:
                defaults = self.schema_manager.generate_default_config(plugin_id, use_cache=True)
                config = self.schema_manager.merge_with_defaults(config, defaults)
                self.logger.debug(f"Merged config with schema defaults for {plugin_id}")
            except Exception as e:
                self.logger.warning(f"Could not apply schema defaults for {plugin_id}: {e}")
                # Continue with original config if defaults can't be applied
            
            # Use PluginLoader to load plugin
            plugin_instance, module = self.plugin_loader.load_plugin(
                plugin_id=plugin_id,
                manifest=manifest,
                plugin_dir=plugin_dir,
                config=config,
                display_manager=self.display_manager,
                cache_manager=self.cache_manager,
                plugin_manager=self,
                install_deps=True
            )
            
            # Store module
            self.plugin_modules[plugin_id] = module
            
            # Validate configuration
            if hasattr(plugin_instance, 'validate_config'):
                try:
                    if not plugin_instance.validate_config():
                        self.logger.error("Plugin %s configuration validation failed", plugin_id)
                        self.state_manager.set_state(plugin_id, PluginState.ERROR)
                        return False
                except Exception as e:
                    self.logger.error("Error validating plugin %s config: %s", plugin_id, e, exc_info=True)
                    self.state_manager.set_state(plugin_id, PluginState.ERROR, error=e)
                    return False
            
            # Store plugin instance
            self.plugins[plugin_id] = plugin_instance
            self.plugin_last_update[plugin_id] = 0.0
            
            # Update state based on enabled status
            if config.get('enabled', True):
                self.state_manager.set_state(plugin_id, PluginState.ENABLED)
                # Call on_enable if plugin is enabled
                if hasattr(plugin_instance, 'on_enable'):
                    plugin_instance.on_enable()
            else:
                self.state_manager.set_state(plugin_id, PluginState.DISABLED)
            
            version_info = manifest.get('version')
            if version_info:
                self.logger.info("Loaded plugin: %s (version %s)", plugin_id, version_info)
            else:
                self.logger.info("Loaded plugin: %s", plugin_id)
            
            return True
            
        except PluginError as e:
            self.logger.error("Plugin error loading %s: %s", plugin_id, e, exc_info=True)
            self.state_manager.set_state(plugin_id, PluginState.ERROR, error=e)
            return False
        except Exception as e:
            self.logger.error("Unexpected error loading plugin %s: %s", plugin_id, e, exc_info=True)
            self.state_manager.set_state(plugin_id, PluginState.ERROR, error=e)
            return False
    
    def unload_plugin(self, plugin_id: str) -> bool:
        """
        Unload a plugin by ID.
        
        Args:
            plugin_id: Plugin identifier
            
        Returns:
            True if unloaded successfully, False otherwise
        """
        if plugin_id not in self.plugins:
            self.logger.warning("Plugin %s not loaded", plugin_id)
            return False
        
        try:
            plugin = self.plugins[plugin_id]
            
            # Call cleanup if available
            if hasattr(plugin, 'cleanup'):
                try:
                    plugin.cleanup()
                except Exception as e:
                    self.logger.warning("Error during plugin cleanup: %s", e)
            
            # Call on_disable if available
            if hasattr(plugin, 'on_disable'):
                try:
                    plugin.on_disable()
                except Exception as e:
                    self.logger.warning("Error during plugin on_disable: %s", e)
            
            # Remove from active plugins
            del self.plugins[plugin_id]
            if plugin_id in self.plugin_last_update:
                del self.plugin_last_update[plugin_id]
            
            # Remove module from sys.modules if present
            module_name = f"plugin_{plugin_id.replace('-', '_')}"
            if module_name in sys.modules:
                del sys.modules[module_name]
            
            # Remove from plugin_modules
            self.plugin_modules.pop(plugin_id, None)
            
            # Update state
            self.state_manager.set_state(plugin_id, PluginState.UNLOADED)
            self.state_manager.clear_state(plugin_id)
            
            self.logger.info("Unloaded plugin: %s", plugin_id)
            return True
            
        except Exception as e:
            self.logger.error("Error unloading plugin %s: %s", plugin_id, e, exc_info=True)
            self.state_manager.set_state(plugin_id, PluginState.ERROR, error=e)
            return False
    
    def reload_plugin(self, plugin_id: str) -> bool:
        """
        Reload a plugin (unload and load).
        
        Args:
            plugin_id: Plugin identifier
            
        Returns:
            True if reloaded successfully, False otherwise
        """
        self.logger.info("Reloading plugin: %s", plugin_id)
        
        # Unload first
        if plugin_id in self.plugins:
            if not self.unload_plugin(plugin_id):
                return False
        
        # Re-discover to get updated manifest
        manifest_path = self.plugins_dir / plugin_id / "manifest.json"
        if manifest_path.exists():
            try:
                with open(manifest_path, 'r', encoding='utf-8') as f:
                    self.plugin_manifests[plugin_id] = json.load(f)
            except Exception as e:
                self.logger.error("Error reading manifest: %s", e, exc_info=True)
                return False
        
        return self.load_plugin(plugin_id)
    
    def get_plugin(self, plugin_id: str) -> Optional[Any]:
        """
        Get a loaded plugin instance by ID.
        
        Args:
            plugin_id: Plugin identifier
            
        Returns:
            Plugin instance or None if not loaded
        """
        return self.plugins.get(plugin_id)
    
    def get_all_plugins(self) -> Dict[str, Any]:
        """
        Get all loaded plugins.
        
        Returns:
            Dict of plugin_id: plugin_instance
        """
        return self.plugins.copy()
    
    def get_enabled_plugins(self) -> List[str]:
        """
        Get list of enabled plugin IDs.
        
        Returns:
            List of plugin IDs that are currently enabled
        """
        return [pid for pid, plugin in self.plugins.items() if plugin.enabled]
    
    def get_plugin_info(self, plugin_id: str) -> Optional[Dict[str, Any]]:
        """
        Get information about a plugin (manifest + runtime info).
        
        Args:
            plugin_id: Plugin identifier
            
        Returns:
            Dict with plugin information or None if not found
        """
        manifest = self.plugin_manifests.get(plugin_id)
        if not manifest:
            return None
        
        info = manifest.copy()
        
        # Add runtime information if plugin is loaded
        plugin = self.plugins.get(plugin_id)
        if plugin:
            info['loaded'] = True
            if hasattr(plugin, 'get_info'):
                info['runtime_info'] = plugin.get_info()
        else:
            info['loaded'] = False
        
        # Add state information
        info['state'] = self.state_manager.get_state_info(plugin_id)
        
        return info
    
    def get_all_plugin_info(self) -> List[Dict[str, Any]]:
        """
        Get information about all plugins.
        
        Returns:
            List of plugin info dictionaries
        """
        return [info for info in [self.get_plugin_info(pid) for pid in self.plugin_manifests.keys()] if info]
    
    def get_plugin_directory(self, plugin_id: str) -> Optional[str]:
        """
        Get the directory path for a plugin.
        
        Args:
            plugin_id: Plugin identifier
            
        Returns:
            Directory path as string or None if not found
        """
        if hasattr(self, 'plugin_directories') and plugin_id in self.plugin_directories:
            return str(self.plugin_directories[plugin_id])
        
        plugin_dir = self.plugins_dir / plugin_id
        if plugin_dir.exists():
            return str(plugin_dir)
        
        plugin_dir = self.plugins_dir / f"ledmatrix-{plugin_id}"
        if plugin_dir.exists():
            return str(plugin_dir)
        
        return None
    
    def get_plugin_display_modes(self, plugin_id: str) -> List[str]:
        """
        Get display modes provided by a plugin.
        
        Args:
            plugin_id: Plugin identifier
            
        Returns:
            List of display mode names
        """
        manifest = self.plugin_manifests.get(plugin_id)
        if not manifest:
            return []
        
        display_modes = manifest.get('display_modes', [])
        if isinstance(display_modes, list):
            return display_modes
        return []
    
    def find_plugin_for_mode(self, mode: str) -> Optional[str]:
        """
        Find which plugin provides a given display mode.
        
        Args:
            mode: Display mode identifier
            
        Returns:
            Plugin identifier or None if not found.
        """
        normalized_mode = mode.strip().lower()
        for plugin_id, manifest in self.plugin_manifests.items():
            display_modes = manifest.get('display_modes')
            if isinstance(display_modes, list) and display_modes:
                if any(m.lower() == normalized_mode for m in display_modes):
                    return plugin_id
        
        return None

    def _get_plugin_update_interval(self, plugin_id: str, plugin_instance: Any) -> Optional[float]:
        """
        Get the update interval for a plugin.
        
        Args:
            plugin_id: Plugin identifier
            plugin_instance: Plugin instance
            
        Returns:
            Update interval in seconds or None if not configured
        """
        # Check manifest first
        manifest = self.plugin_manifests.get(plugin_id, {})
        update_interval = manifest.get('update_interval')
        
        if update_interval:
            try:
                return float(update_interval)
            except (ValueError, TypeError):
                pass
        
        # Check plugin config
        if self.config_manager:
            try:
                config = self.config_manager.get_config()
                plugin_config = config.get(plugin_id, {})
                update_interval = plugin_config.get('update_interval')
                if update_interval:
                    try:
                        return float(update_interval)
                    except (ValueError, TypeError):
                        pass
            except Exception as e:
                self.logger.debug("Could not get update interval from config: %s", e)
        
        # Default: 60 seconds
        return 60.0

    def run_scheduled_updates(self, current_time: Optional[float] = None) -> None:
        """
        Trigger plugin updates based on their defined update intervals.
        Includes health tracking and circuit breaker logic.
        Uses PluginExecutor for safe execution with timeout.
        """
        if current_time is None:
            current_time = time.time()

        for plugin_id, plugin_instance in list(self.plugins.items()):
            if not getattr(plugin_instance, "enabled", True):
                continue

            if not hasattr(plugin_instance, "update"):
                continue

            # Check circuit breaker before attempting update
            if self.health_tracker and self.health_tracker.should_skip_plugin(plugin_id):
                continue

            # Check if plugin can execute
            if not self.state_manager.can_execute(plugin_id):
                continue

            interval = self._get_plugin_update_interval(plugin_id, plugin_instance)
            if interval is None:
                continue

            last_update = self.plugin_last_update.get(plugin_id, 0.0)

            if last_update == 0.0 or (current_time - last_update) >= interval:
                # Update state to RUNNING
                self.state_manager.set_state(plugin_id, PluginState.RUNNING)
                
                try:
                    # Use PluginExecutor for safe execution
                    success = False
                    if self.resource_monitor:
                        # If resource monitor exists, wrap the call
                        def monitored_update():
                            self.resource_monitor.monitor_call(plugin_id, plugin_instance.update)
                        success = self.plugin_executor.execute_update(
                            type('obj', (object,), {'update': monitored_update})(),
                            plugin_id
                        )
                    else:
                        success = self.plugin_executor.execute_update(plugin_instance, plugin_id)
                    
                    if success:
                        self.plugin_last_update[plugin_id] = current_time
                        self.state_manager.record_update(plugin_id)
                        # Update state back to ENABLED
                        self.state_manager.set_state(plugin_id, PluginState.ENABLED)
                        # Record success
                        if self.health_tracker:
                            self.health_tracker.record_success(plugin_id)
                    else:
                        # Execution failed (timeout or error)
                        self.state_manager.set_state(plugin_id, PluginState.ERROR)
                        if self.health_tracker:
                            self.health_tracker.record_failure(plugin_id, Exception("Plugin execution failed"))
                except Exception as exc:  # pylint: disable=broad-except
                    self.logger.exception("Error updating plugin %s: %s", plugin_id, exc)
                    self.state_manager.set_state(plugin_id, PluginState.ERROR, error=exc)
                    # Record failure
                    if self.health_tracker:
                        self.health_tracker.record_failure(plugin_id, exc)

    def update_all_plugins(self) -> None:
        """
        Update all enabled plugins.
        Calls update() on each enabled plugin using PluginExecutor.
        """
        for plugin_id, plugin_instance in list(self.plugins.items()):
            if not getattr(plugin_instance, "enabled", True):
                continue
            
            if not hasattr(plugin_instance, "update"):
                continue
            
            # Check if plugin can execute
            if not self.state_manager.can_execute(plugin_id):
                continue
            
            # Update state to RUNNING
            self.state_manager.set_state(plugin_id, PluginState.RUNNING)
            
            try:
                success = self.plugin_executor.execute_update(plugin_instance, plugin_id)
                if success:
                    self.plugin_last_update[plugin_id] = time.time()
                    self.state_manager.record_update(plugin_id)
                    # Update state back to ENABLED
                    self.state_manager.set_state(plugin_id, PluginState.ENABLED)
                else:
                    # Execution failed
                    self.state_manager.set_state(plugin_id, PluginState.ERROR)
            except Exception as exc:  # pylint: disable=broad-except
                self.logger.exception("Error updating plugin %s: %s", plugin_id, exc)
                self.state_manager.set_state(plugin_id, PluginState.ERROR, error=exc)
    
    def get_plugin_health_metrics(self) -> Dict[str, Any]:
        """
        Get health metrics for all plugins.
        
        Returns:
            Dictionary mapping plugin_id to health metrics
        """
        metrics = {}
        for plugin_id in self.plugins.keys():
            plugin_metrics = {}
            
            # Get state information
            state_info = self.state_manager.get_state_info(plugin_id)
            plugin_metrics.update(state_info)
            
            # Get health tracker metrics if available
            if self.health_tracker:
                health_info = self.health_tracker.get_plugin_health(plugin_id)
                plugin_metrics['health'] = health_info
            else:
                plugin_metrics['health'] = {'status': 'unknown'}
            
            metrics[plugin_id] = plugin_metrics
        return metrics
    
    def get_plugin_resource_metrics(self) -> Dict[str, Any]:
        """
        Get resource usage metrics for all plugins.
        
        Returns:
            Dictionary mapping plugin_id to resource metrics
        """
        metrics = {}
        for plugin_id in self.plugins.keys():
            plugin_metrics = {}
            
            # Get state information
            state_info = self.state_manager.get_state_info(plugin_id)
            plugin_metrics.update(state_info)
            
            # Get resource monitor metrics if available
            if self.resource_monitor:
                resource_info = self.resource_monitor.get_plugin_metrics(plugin_id)
                plugin_metrics['resources'] = resource_info
            else:
                plugin_metrics['resources'] = {'status': 'unknown'}
            
            metrics[plugin_id] = plugin_metrics
        return metrics
    
    def get_plugin_state(self, plugin_id: str) -> Dict[str, Any]:
        """
        Get comprehensive state information for a plugin.
        
        Args:
            plugin_id: Plugin identifier
            
        Returns:
            Dictionary with state information
        """
        return self.state_manager.get_state_info(plugin_id)
