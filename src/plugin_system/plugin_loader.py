"""
Plugin Loader

Handles plugin module imports, dependency installation, and class instantiation.
Extracted from PluginManager to improve separation of concerns.
"""

import json
import importlib
import importlib.util
import sys
import subprocess
from pathlib import Path
from typing import Dict, Any, Optional, Tuple, Type
import logging

from src.exceptions import PluginError
from src.logging_config import get_logger


class PluginLoader:
    """Handles plugin module loading and class instantiation."""
    
    def __init__(self, logger: Optional[logging.Logger] = None) -> None:
        """
        Initialize the plugin loader.
        
        Args:
            logger: Optional logger instance
        """
        self.logger = logger or get_logger(__name__)
        self._loaded_modules: Dict[str, Any] = {}
    
    def find_plugin_directory(
        self,
        plugin_id: str,
        plugins_dir: Path,
        plugin_directories: Optional[Dict[str, Path]] = None
    ) -> Optional[Path]:
        """
        Find the plugin directory for a given plugin ID.
        
        Tries multiple strategies:
        1. Use plugin_directories mapping if available
        2. Direct path matching
        3. Case-insensitive directory matching
        4. Manifest-based search
        
        Args:
            plugin_id: Plugin identifier
            plugins_dir: Base plugins directory
            plugin_directories: Optional mapping of plugin_id to directory
            
        Returns:
            Path to plugin directory or None if not found
        """
        # Strategy 1: Use mapping from discovery
        if plugin_directories and plugin_id in plugin_directories:
            plugin_dir = plugin_directories[plugin_id]
            if plugin_dir.exists():
                self.logger.debug("Using plugin directory from discovery mapping: %s", plugin_dir)
                return plugin_dir
        
        # Strategy 2: Direct paths
        plugin_dir = plugins_dir / plugin_id
        if plugin_dir.exists():
            return plugin_dir
        
        plugin_dir = plugins_dir / f"ledmatrix-{plugin_id}"
        if plugin_dir.exists():
            return plugin_dir
        
        # Strategy 3: Case-insensitive search
        normalized_id = plugin_id.lower()
        for item in plugins_dir.iterdir():
            if not item.is_dir():
                continue
            
            item_name = item.name
            if item_name.lower() == normalized_id:
                return item
            
            if item_name.lower() == f"ledmatrix-{plugin_id}".lower():
                return item
        
        # Strategy 4: Manifest-based search
        self.logger.debug("Directory name search failed for %s, searching by manifest...", plugin_id)
        for item in plugins_dir.iterdir():
            if not item.is_dir():
                continue
            
            # Skip if already checked
            if item.name.lower() == normalized_id or item.name.lower() == f"ledmatrix-{plugin_id}".lower():
                continue
            
            manifest_path = item / "manifest.json"
            if manifest_path.exists():
                try:
                    with open(manifest_path, 'r', encoding='utf-8') as f:
                        item_manifest = json.load(f)
                        item_manifest_id = item_manifest.get('id')
                        if item_manifest_id == plugin_id:
                            self.logger.info(
                                "Found plugin %s in directory %s (manifest ID matches)",
                                plugin_id,
                                item.name
                            )
                            return item
                except (json.JSONDecodeError, Exception) as e:
                    self.logger.debug("Skipping %s due to manifest error: %s", item.name, e)
                    continue
        
        return None
    
    def install_dependencies(
        self,
        plugin_dir: Path,
        plugin_id: str,
        timeout: int = 300
    ) -> bool:
        """
        Install plugin dependencies from requirements.txt.
        
        Args:
            plugin_dir: Plugin directory path
            plugin_id: Plugin identifier
            timeout: Installation timeout in seconds
            
        Returns:
            True if dependencies installed or not needed, False on error
        """
        requirements_file = plugin_dir / "requirements.txt"
        if not requirements_file.exists():
            return True  # No dependencies needed
        
        # Check if already installed
        marker_path = plugin_dir / ".dependencies_installed"
        if marker_path.exists():
            self.logger.debug("Dependencies already installed for %s", plugin_id)
            return True
        
        try:
            self.logger.info("Installing dependencies for plugin %s...", plugin_id)
            result = subprocess.run(
                [sys.executable, "-m", "pip", "install", "-r", str(requirements_file)],
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False
            )
            
            if result.returncode == 0:
                # Mark as installed
                marker_path.touch()
                self.logger.info("Dependencies installed successfully for %s", plugin_id)
                return True
            else:
                self.logger.warning(
                    "Dependency installation returned non-zero exit code for %s: %s",
                    plugin_id,
                    result.stderr
                )
                return False
        except subprocess.TimeoutExpired:
            self.logger.error("Dependency installation timed out for %s", plugin_id)
            return False
        except FileNotFoundError:
            self.logger.warning("pip not found. Skipping dependency installation for %s", plugin_id)
            return True
        except Exception as e:
            self.logger.error("Unexpected error installing dependencies for %s: %s", plugin_id, e, exc_info=True)
            return False
    
    def load_module(
        self,
        plugin_id: str,
        plugin_dir: Path,
        entry_point: str
    ) -> Optional[Any]:
        """
        Load a plugin module from file.
        
        Args:
            plugin_id: Plugin identifier
            plugin_dir: Plugin directory path
            entry_point: Entry point filename (e.g., 'manager.py')
            
        Returns:
            Loaded module or None on error
        """
        entry_file = plugin_dir / entry_point
        if not entry_file.exists():
            error_msg = f"Entry point file not found: {entry_file} for plugin {plugin_id}"
            self.logger.error(error_msg)
            raise PluginError(error_msg, plugin_id=plugin_id, context={'entry_file': str(entry_file)})
        
        # Add plugin directory to sys.path if not already there
        plugin_dir_str = str(plugin_dir)
        if plugin_dir_str not in sys.path:
            sys.path.insert(0, plugin_dir_str)
            self.logger.debug("Added plugin directory to sys.path: %s", plugin_dir_str)
        
        # Import the plugin module
        module_name = f"plugin_{plugin_id.replace('-', '_')}"
        
        # Check if already loaded
        if module_name in sys.modules:
            self.logger.debug("Module %s already loaded, reusing", module_name)
            return sys.modules[module_name]
        
        spec = importlib.util.spec_from_file_location(module_name, entry_file)
        if spec is None or spec.loader is None:
            error_msg = f"Could not create module spec for {entry_file}"
            self.logger.error(error_msg)
            raise PluginError(error_msg, plugin_id=plugin_id, context={'entry_file': str(entry_file)})
        
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        
        self._loaded_modules[plugin_id] = module
        self.logger.debug("Loaded module %s for plugin %s", module_name, plugin_id)
        
        return module
    
    def get_plugin_class(
        self,
        plugin_id: str,
        module: Any,
        class_name: str
    ) -> Type[Any]:
        """
        Get the plugin class from a loaded module.
        
        Args:
            plugin_id: Plugin identifier
            module: Loaded module
            class_name: Name of the plugin class
            
        Returns:
            Plugin class
            
        Raises:
            PluginError: If class not found
        """
        if not hasattr(module, class_name):
            error_msg = f"Class {class_name} not found in module for plugin {plugin_id}"
            self.logger.error(error_msg)
            raise PluginError(
                error_msg,
                plugin_id=plugin_id,
                context={'class_name': class_name, 'module': module.__name__}
            )
        
        plugin_class = getattr(module, class_name)
        
        # Verify it's a class
        if not isinstance(plugin_class, type):
            error_msg = f"{class_name} is not a class in module for plugin {plugin_id}"
            self.logger.error(error_msg)
            raise PluginError(error_msg, plugin_id=plugin_id, context={'class_name': class_name})
        
        return plugin_class
    
    def instantiate_plugin(
        self,
        plugin_id: str,
        plugin_class: Type[Any],
        config: Dict[str, Any],
        display_manager: Any,
        cache_manager: Any,
        plugin_manager: Any
    ) -> Any:
        """
        Instantiate a plugin class.
        
        Args:
            plugin_id: Plugin identifier
            plugin_class: Plugin class to instantiate
            config: Plugin configuration
            display_manager: Display manager instance
            cache_manager: Cache manager instance
            plugin_manager: Plugin manager instance
            
        Returns:
            Plugin instance
            
        Raises:
            PluginError: If instantiation fails
        """
        try:
            plugin_instance = plugin_class(
                plugin_id=plugin_id,
                config=config,
                display_manager=display_manager,
                cache_manager=cache_manager,
                plugin_manager=plugin_manager
            )
            self.logger.debug("Instantiated plugin %s", plugin_id)
            return plugin_instance
        except Exception as e:
            error_msg = f"Failed to instantiate plugin {plugin_id}: {e}"
            self.logger.error(error_msg, exc_info=True)
            raise PluginError(error_msg, plugin_id=plugin_id) from e
    
    def load_plugin(
        self,
        plugin_id: str,
        manifest: Dict[str, Any],
        plugin_dir: Path,
        config: Dict[str, Any],
        display_manager: Any,
        cache_manager: Any,
        plugin_manager: Any,
        install_deps: bool = True
    ) -> Tuple[Any, Any]:
        """
        Complete plugin loading process.
        
        Args:
            plugin_id: Plugin identifier
            manifest: Plugin manifest
            plugin_dir: Plugin directory path
            config: Plugin configuration
            display_manager: Display manager instance
            cache_manager: Cache manager instance
            plugin_manager: Plugin manager instance
            install_deps: Whether to install dependencies
            
        Returns:
            Tuple of (plugin_instance, module)
            
        Raises:
            PluginError: If loading fails
        """
        # Install dependencies if needed
        if install_deps:
            self.install_dependencies(plugin_dir, plugin_id)
        
        # Load module
        entry_point = manifest.get('entry_point', 'manager.py')
        module = self.load_module(plugin_id, plugin_dir, entry_point)
        if module is None:
            raise PluginError(f"Failed to load module for plugin {plugin_id}", plugin_id=plugin_id)
        
        # Get plugin class
        class_name = manifest.get('class_name')
        if not class_name:
            raise PluginError(f"No class_name in manifest for plugin {plugin_id}", plugin_id=plugin_id)
        
        plugin_class = self.get_plugin_class(plugin_id, module, class_name)
        
        # Instantiate plugin
        plugin_instance = self.instantiate_plugin(
            plugin_id,
            plugin_class,
            config,
            display_manager,
            cache_manager,
            plugin_manager
        )
        
        return (plugin_instance, module)

