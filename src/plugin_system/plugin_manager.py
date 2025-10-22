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
from pathlib import Path
from typing import Dict, List, Optional, Any
import logging


class PluginManager:
    """
    Manages plugin discovery, loading, and lifecycle.
    
    The PluginManager is responsible for:
    - Discovering plugins in the plugins/ directory
    - Loading plugin modules and instantiating plugin classes
    - Managing plugin lifecycle (load, unload, reload)
    - Providing access to loaded plugins
    - Maintaining plugin manifests
    """
    
    def __init__(self, plugins_dir: str = "plugins", 
                 config_manager=None, display_manager=None, cache_manager=None, font_manager=None):
        """
        Initialize the Plugin Manager.
        
        Args:
            plugins_dir: Path to the plugins directory
            config_manager: Configuration manager instance
            display_manager: Display manager instance
            cache_manager: Cache manager instance
            font_manager: Font manager instance
        """
        self.plugins_dir = Path(plugins_dir)
        self.config_manager = config_manager
        self.display_manager = display_manager
        self.cache_manager = cache_manager
        self.font_manager = font_manager
        self.logger = logging.getLogger(__name__)
        
        # Active plugins
        self.plugins: Dict[str, Any] = {}
        self.plugin_manifests: Dict[str, Dict] = {}
        self.plugin_modules: Dict[str, Any] = {}
        
        # Ensure plugins directory exists
        self.plugins_dir.mkdir(exist_ok=True)
        self.logger.info(f"Plugin Manager initialized with plugins directory: {self.plugins_dir}")
        
    def discover_plugins(self) -> List[str]:
        """
        Scan plugins directory for installed plugins.
        
        A valid plugin directory must contain a manifest.json file.
        
        Returns:
            List of plugin IDs that were discovered
        """
        discovered = []
        
        if not self.plugins_dir.exists():
            self.logger.warning(f"Plugins directory not found: {self.plugins_dir}")
            return discovered
        
        for item in self.plugins_dir.iterdir():
            if not item.is_dir():
                continue
            
            # Skip hidden directories and temp directories
            if item.name.startswith('.') or item.name.startswith('_'):
                continue
            
            manifest_path = item / "manifest.json"
            if manifest_path.exists():
                try:
                    with open(manifest_path, 'r', encoding='utf-8') as f:
                        manifest = json.load(f)
                    
                    plugin_id = manifest.get('id')
                    if not plugin_id:
                        self.logger.error(f"No 'id' field in manifest: {manifest_path}")
                        continue
                    
                    if plugin_id != item.name:
                        self.logger.warning(
                            f"Plugin ID '{plugin_id}' doesn't match directory name '{item.name}'"
                        )
                    
                    discovered.append(plugin_id)
                    self.plugin_manifests[plugin_id] = manifest
                    self.logger.info(f"Discovered plugin: {plugin_id} v{manifest.get('version', '?')}")
                    
                except json.JSONDecodeError as e:
                    self.logger.error(f"Invalid JSON in manifest {manifest_path}: {e}")
                except Exception as e:
                    self.logger.error(f"Error reading manifest in {item}: {e}")
        
        self.logger.info(f"Discovered {len(discovered)} plugin(s)")
        return discovered

    def _install_plugin_dependencies(self, requirements_file: Path) -> bool:
        """
        Install Python dependencies for a plugin.

        Args:
            requirements_file: Path to requirements.txt file

        Returns:
            True if successful or no dependencies needed, False on error
        """
        try:
            import subprocess
            import os

            # First, check if dependencies are already satisfied using --dry-run
            try:
                dry_run_result = subprocess.run(
                    ['pip3', 'install', '--dry-run', '-r', str(requirements_file)],
                    capture_output=True,
                    text=True,
                    timeout=10
                )
                # If nothing would be installed, dependencies are already satisfied
                # Check for "Requirement already satisfied" which indicates no action needed
                if 'Requirement already satisfied' in dry_run_result.stdout and 'Would install' not in dry_run_result.stdout:
                    self.logger.debug(f"Dependencies already satisfied for {requirements_file}")
                    return True
            except (subprocess.SubprocessError, FileNotFoundError):
                # If pip check fails or doesn't exist, proceed with installation
                pass

            self.logger.info(f"Installing dependencies for plugin from {requirements_file}")

            # Check if running as root (systemd service context)
            running_as_root = os.geteuid() == 0 if hasattr(os, 'geteuid') else False
            
            # Prepare environment to avoid permission issues
            env = os.environ.copy()
            env['PYTHONUNBUFFERED'] = '1'
            
            if running_as_root:
                # System-wide installation for root (systemd service)
                # Use --no-cache-dir to avoid cache permission issues
                cmd = [
                    'pip3', 'install', 
                    '--break-system-packages',
                    '--no-cache-dir',
                    '-r', str(requirements_file)
                ]
                self.logger.info("Installing plugin dependencies system-wide (running as root)")
            else:
                # User installation for development/testing
                # Note: If you're testing manually as a non-root user, these dependencies
                # will be installed to ~/.local/ and won't be accessible to the root service.
                # For production plugin installation, always use the web interface or restart the service.
                # Need --break-system-packages for Debian 12+ (PEP 668) even with --user
                cmd = [
                    'pip3', 'install', 
                    '--user', 
                    '--break-system-packages',
                    '--no-cache-dir',
                    '-r', str(requirements_file)
                ]
                self.logger.warning(
                    "Installing plugin dependencies for current user (not root). "
                    "These will NOT be accessible to the systemd service. "
                    "For production use, install plugins via the web interface or restart the ledmatrix service."
                )

            result = subprocess.run(
                cmd,
                check=True,
                capture_output=True,
                text=True,
                env=env
            )

            self.logger.info(f"Successfully installed dependencies for {requirements_file}")
            if result.stdout:
                self.logger.debug(f"pip output: {result.stdout}")
            return True

        except subprocess.CalledProcessError as e:
            self.logger.error(f"Error installing dependencies: {e.stderr}")
            self.logger.info("Dependencies may need to be installed manually. Plugin loading will continue.")
            # Don't fail plugin loading if dependencies fail to install
            # User can manually install them
            return True
        except FileNotFoundError as e:
            self.logger.warning(f"Command not found: {e}. Skipping dependency installation")
            return True
        except Exception as e:
            self.logger.error(f"Unexpected error installing dependencies: {e}")
            return True

    def load_plugin(self, plugin_id: str) -> bool:
        """
        Load a plugin by ID.
        
        This method:
        1. Checks if plugin is already loaded
        2. Validates the manifest exists
        3. Imports the plugin module
        4. Instantiates the plugin class
        5. Validates the plugin configuration
        6. Stores the plugin instance
        
        Args:
            plugin_id: Plugin identifier
            
        Returns:
            True if loaded successfully, False otherwise
        """
        if plugin_id in self.plugins:
            self.logger.warning(f"Plugin {plugin_id} already loaded")
            return True
        
        manifest = self.plugin_manifests.get(plugin_id)
        if not manifest:
            self.logger.error(f"No manifest found for plugin: {plugin_id}")
            return False
        
        try:
            # Get plugin directory - try both plugin_id and ledmatrix-plugin_id formats
            plugin_dir = self.plugins_dir / plugin_id
            if not plugin_dir.exists():
                # Try with ledmatrix- prefix
                plugin_dir = self.plugins_dir / f"ledmatrix-{plugin_id}"
                if not plugin_dir.exists():
                    self.logger.error(f"Plugin directory not found: {plugin_id} or ledmatrix-{plugin_id}")
                    return False
            
            # Get entry point
            entry_point = manifest.get('entry_point', 'manager.py')
            entry_file = plugin_dir / entry_point
            
            if not entry_file.exists():
                self.logger.error(f"Entry point not found: {entry_file}")
                return False

            # Install plugin dependencies if requirements.txt exists
            requirements_file = plugin_dir / "requirements.txt"
            if requirements_file.exists():
                self._install_plugin_dependencies(requirements_file)

            # Import the plugin module
            module_name = f"plugin_{plugin_id.replace('-', '_')}"
            spec = importlib.util.spec_from_file_location(module_name, entry_file)

            if spec is None or spec.loader is None:
                self.logger.error(f"Could not create module spec for {entry_file}")
                return False

            module = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = module
            spec.loader.exec_module(module)
            
            self.plugin_modules[plugin_id] = module
            
            # Get the plugin class
            class_name = manifest.get('class_name')
            if not class_name:
                self.logger.error(f"No class_name in manifest for {plugin_id}")
                return False
            
            if not hasattr(module, class_name):
                self.logger.error(f"Class {class_name} not found in {entry_file}")
                return False
            
            plugin_class = getattr(module, class_name)
            
            # Get plugin config
            if self.config_manager:
                config = self.config_manager.load_config().get(plugin_id, {})
            else:
                config = {}
            
            # Instantiate the plugin
            plugin_instance = plugin_class(
                plugin_id=plugin_id,
                config=config,
                display_manager=self.display_manager,
                cache_manager=self.cache_manager,
                plugin_manager=self
            )
            
            # Validate configuration
            if not plugin_instance.validate_config():
                self.logger.error(f"Config validation failed for {plugin_id}")
                return False
            
            # Store the plugin
            self.plugins[plugin_id] = plugin_instance
            self.logger.info(f"Loaded plugin: {plugin_id} v{manifest.get('version', '?')}")
            
            # Call on_enable if plugin is enabled
            if plugin_instance.enabled:
                plugin_instance.on_enable()
            
            return True
            
        except Exception as e:
            self.logger.error(f"Error loading plugin {plugin_id}: {e}", exc_info=True)
            return False
    
    def unload_plugin(self, plugin_id: str) -> bool:
        """
        Unload a plugin by ID.
        
        This method:
        1. Calls the plugin's cleanup() method
        2. Removes the plugin from active plugins
        3. Removes the plugin module from sys.modules
        
        Args:
            plugin_id: Plugin identifier
            
        Returns:
            True if unloaded successfully, False otherwise
        """
        if plugin_id not in self.plugins:
            self.logger.warning(f"Plugin {plugin_id} not loaded")
            return False
        
        try:
            plugin = self.plugins[plugin_id]
            
            # Call cleanup
            plugin.cleanup()
            
            # Call on_disable
            plugin.on_disable()
            
            # Remove from active plugins
            del self.plugins[plugin_id]
            
            # Remove module from sys.modules if present
            module_name = f"plugin_{plugin_id.replace('-', '_')}"
            if module_name in sys.modules:
                del sys.modules[module_name]
            
            # Remove from plugin_modules
            if plugin_id in self.plugin_modules:
                del self.plugin_modules[plugin_id]
            
            self.logger.info(f"Unloaded plugin: {plugin_id}")
            return True
            
        except Exception as e:
            self.logger.error(f"Error unloading plugin {plugin_id}: {e}", exc_info=True)
            return False
    
    def reload_plugin(self, plugin_id: str) -> bool:
        """
        Reload a plugin (unload and load).
        
        Useful for development and when plugin files have been updated.
        
        Args:
            plugin_id: Plugin identifier
            
        Returns:
            True if reloaded successfully, False otherwise
        """
        self.logger.info(f"Reloading plugin: {plugin_id}")
        
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
                self.logger.error(f"Error reading manifest: {e}")
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
        
        # Auto-extract version from versions array if not present at top level
        if 'version' not in info or not info['version']:
            versions = info.get('versions', [])
            if versions and isinstance(versions, list) and len(versions) > 0:
                # Get the latest version (first in array)
                latest = versions[0]
                if isinstance(latest, dict) and 'version' in latest:
                    info['version'] = latest['version']
        
        # Add runtime information if plugin is loaded
        plugin = self.plugins.get(plugin_id)
        if plugin:
            info['loaded'] = True
            info['runtime_info'] = plugin.get_info()
        else:
            info['loaded'] = False
        
        return info
    
    def get_all_plugin_info(self) -> List[Dict[str, Any]]:
        """
        Get information about all discovered plugins.
        
        Returns:
            List of plugin information dicts
        """
        result = []
        for plugin_id in self.plugin_manifests.keys():
            plugin_info = self.get_plugin_info(plugin_id)
            if plugin_info is not None:
                result.append(plugin_info)
        return result
    
    def get_plugin_directory(self, plugin_id: str) -> Optional[str]:
        """
        Get the filesystem directory path for a plugin.
        
        Args:
            plugin_id: Plugin identifier
            
        Returns:
            Path to plugin directory as string, or None if plugin not found
        """
        if plugin_id not in self.plugin_manifests:
            return None
        
        plugin_dir = self.plugins_dir / plugin_id
        if plugin_dir.exists():
            return str(plugin_dir)
        
        return None

