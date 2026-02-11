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
import threading
from pathlib import Path
from typing import Dict, Any, Optional, Tuple, Type
import logging

from src.exceptions import PluginError
from src.logging_config import get_logger
from src.common.permission_utils import (
    ensure_file_permissions,
    get_plugin_file_mode
)


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
        self._plugin_module_registry: Dict[str, set] = {}  # Maps plugin_id to set of module names
        # Lock to serialize module loading when plugins share module names
        # (e.g., scroll_display.py, game_renderer.py across sport plugins).
        # During exec_module, bare-name sub-modules temporarily appear in
        # sys.modules; the lock prevents concurrent plugins from seeing each
        # other's entries.  After exec_module, _namespace_plugin_modules
        # moves those bare names to namespaced keys (e.g.
        # _plg_basketball_scoreboard_scroll_display) so they never collide.
        self._module_load_lock = threading.Lock()
    
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
                [sys.executable, "-m", "pip", "install", "--break-system-packages", "-r", str(requirements_file)],
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False
            )
            
            if result.returncode == 0:
                # Mark as installed
                marker_path.touch()
                # Set proper file permissions after creating marker
                ensure_file_permissions(marker_path, get_plugin_file_mode())
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
        except (BrokenPipeError, OSError) as e:
            # Handle broken pipe errors (errno 32) which can occur during pip downloads
            # Often caused by network interruptions or output buffer issues
            if isinstance(e, OSError) and e.errno == 32:
                self.logger.error(
                    "Broken pipe error during dependency installation for %s. "
                    "This usually indicates a network interruption or pip output buffer issue. "
                    "Try installing again or check your network connection.", plugin_id
                )
            else:
                self.logger.error("OS error during dependency installation for %s: %s", plugin_id, e)
            return False
        except Exception as e:
            self.logger.error("Unexpected error installing dependencies for %s: %s", plugin_id, e, exc_info=True)
            return False
    
    @staticmethod
    def _iter_plugin_bare_modules(
        plugin_dir: Path, before_keys: set
    ) -> list:
        """Return bare-name modules from plugin_dir added after before_keys.

        Returns a list of (mod_name, module) tuples for modules that:
        - Were added to sys.modules after before_keys snapshot
        - Have bare names (no dots)
        - Have a ``__file__`` inside plugin_dir
        """
        resolved_dir = plugin_dir.resolve()
        result = []
        for key in set(sys.modules.keys()) - before_keys:
            if "." in key:
                continue
            mod = sys.modules.get(key)
            if mod is None:
                continue
            mod_file = getattr(mod, "__file__", None)
            if not mod_file:
                continue
            try:
                if Path(mod_file).resolve().is_relative_to(resolved_dir):
                    result.append((key, mod))
            except (ValueError, TypeError):
                continue
        return result

    def _namespace_plugin_modules(
        self, plugin_id: str, plugin_dir: Path, before_keys: set
    ) -> None:
        """
        Move bare-name plugin modules to namespaced keys in sys.modules.

        After exec_module loads a plugin's entry point, Python will have added
        the plugin's local modules (scroll_display, game_renderer, …) to
        sys.modules under their bare names.  This method renames them to
        ``_plg_<plugin_id>_<module>`` so they cannot collide with identically-
        named modules from other plugins.

        The plugin code keeps working because ``from scroll_display import X``
        binds ``X`` to the class *object*, not to the sys.modules entry.

        Args:
            plugin_id: Plugin identifier
            plugin_dir: Plugin directory path
            before_keys: Snapshot of sys.modules keys taken *before* exec_module
        """
        safe_id = plugin_id.replace("-", "_")
        namespaced_names: set = set()

        for mod_name, mod in self._iter_plugin_bare_modules(plugin_dir, before_keys):
            namespaced = f"_plg_{safe_id}_{mod_name}"
            sys.modules[namespaced] = mod
            # Keep sys.modules[mod_name] as an alias to the same object.
            # Removing it would cause lazy intra-plugin imports (e.g. a
            # deferred ``import scroll_display`` inside a method) to
            # re-import from disk and create a second, inconsistent copy
            # of the module.  The next plugin's exec_module will naturally
            # overwrite the bare entry with its own version.
            namespaced_names.add(namespaced)
            self.logger.debug(
                "Namespace-isolated module '%s' -> '%s' for plugin %s",
                mod_name, namespaced, plugin_id,
            )

        # Track for cleanup during unload
        self._plugin_module_registry[plugin_id] = namespaced_names

        if namespaced_names:
            self.logger.info(
                "Namespace-isolated %d module(s) for plugin %s",
                len(namespaced_names), plugin_id,
            )

    def unregister_plugin_modules(self, plugin_id: str) -> None:
        """Remove namespaced sub-modules and cached module for a plugin from sys.modules.

        Called by PluginManager during unload to clean up all module entries
        that were created when the plugin was loaded.
        """
        for ns_name in self._plugin_module_registry.pop(plugin_id, set()):
            sys.modules.pop(ns_name, None)
        self._loaded_modules.pop(plugin_id, None)

    def load_module(
        self,
        plugin_id: str,
        plugin_dir: Path,
        entry_point: str
    ) -> Optional[Any]:
        """
        Load a plugin module from file.

        Module loading is serialized via _module_load_lock because plugins are
        loaded in parallel (ThreadPoolExecutor) and multiple sport plugins
        share identically-named local modules (scroll_display.py,
        game_renderer.py, sports.py, etc.).

        After loading, bare-name modules from the plugin directory are moved
        to namespaced keys in sys.modules (e.g. ``_plg_basketball_scoreboard_scroll_display``)
        so they cannot collide with other plugins.

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

        with self._module_load_lock:
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

            # Snapshot AFTER inserting the main module so that
            # _namespace_plugin_modules and error cleanup only target
            # sub-modules, not the main module entry itself.
            before_keys = set(sys.modules.keys())
            try:
                spec.loader.exec_module(module)

                # Move bare-name plugin modules to namespaced keys so they
                # cannot collide with identically-named modules from other plugins
                self._namespace_plugin_modules(plugin_id, plugin_dir, before_keys)
            except Exception:
                # Clean up the partially-initialized main module and any
                # bare-name sub-modules that were added during exec_module
                # so they don't leak into subsequent plugin loads.
                sys.modules.pop(module_name, None)
                for key, _ in self._iter_plugin_bare_modules(plugin_dir, before_keys):
                    sys.modules.pop(key, None)
                raise

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

