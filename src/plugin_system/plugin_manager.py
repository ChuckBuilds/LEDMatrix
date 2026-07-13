"""
Plugin Manager

Manages plugin discovery, loading, and lifecycle for the LEDMatrix system.
Handles dynamic plugin loading from the plugins/ directory.

API Version: 1.0.0
"""

import json
import sys
import time
import threading
import types
from pathlib import Path
from typing import Dict, List, Optional, Any
import logging
from src.exceptions import PluginError, ConfigError
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
        
        # Lock protecting plugin_manifests and plugin_directories from
        # concurrent mutation (background reconciliation) and reads (requests).
        self._discovery_lock = threading.RLock()

        # Lock protecting plugin_last_update from concurrent mutation/iteration.
        # It's written from run_scheduled_updates()/update_all_plugins() (main
        # loop) and read/diffed by run_scheduled_updates_with_changes(), which
        # Vegas mode calls from its own background update-tick thread.
        self._plugin_last_update_lock = threading.RLock()

        # Active plugins
        self.plugins: Dict[str, Any] = {}
        self.plugin_manifests: Dict[str, Dict[str, Any]] = {}
        self.plugin_modules: Dict[str, Any] = {}
        self.plugin_last_update: Dict[str, float] = {}

        # Cached data-fetch intervals per plugin_id.
        # _get_plugin_update_interval falls back to config_manager.get_config()
        # (a full dict copy) when the manifest lacks an interval — caching avoids
        # that copy on every 30-fps tick.  Cleared on load/unload.
        self._update_interval_cache: Dict[str, Optional[float]] = {}

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

        # Build new state locally before acquiring lock
        new_manifests: Dict[str, Dict[str, Any]] = {}
        new_directories: Dict[str, Path] = {}

        try:
            for item in directory.iterdir():
                if not item.is_dir():
                    continue
                # Skip backup directories so they don't overwrite live entries
                if '.standalone-backup-' in item.name:
                    continue

                manifest_path = item / "manifest.json"
                if manifest_path.exists():
                    try:
                        with open(manifest_path, 'r', encoding='utf-8') as f:
                            manifest = json.load(f)
                            plugin_id = manifest.get('id')
                            if plugin_id:
                                plugin_ids.append(plugin_id)
                                new_manifests[plugin_id] = manifest
                                new_directories[plugin_id] = item
                    except (json.JSONDecodeError, PermissionError, OSError) as e:
                        self.logger.warning("Error reading manifest from %s: %s", manifest_path, e, exc_info=True)
                        continue
        except (OSError, PermissionError) as e:
            self.logger.error("Error scanning directory %s: %s", directory, e, exc_info=True)

        # Replace shared state under lock so uninstalled plugins don't linger
        with self._discovery_lock:
            self.plugin_manifests.clear()
            self.plugin_manifests.update(new_manifests)
            if not hasattr(self, 'plugin_directories'):
                self.plugin_directories = {}
            else:
                self.plugin_directories.clear()
            self.plugin_directories.update(new_directories)

        return plugin_ids
    
    def discover_plugins(self) -> List[str]:
        """
        Discover all plugins in the plugins directory.

        Also checks for potential config key collisions and logs warnings.

        Returns:
            List of plugin IDs
        """
        self.logger.info("Discovering plugins in %s", self.plugins_dir)
        plugin_ids = self._scan_directory_for_plugins(self.plugins_dir)
        self.logger.info("Discovered %d plugin(s)", len(plugin_ids))

        # Check for config key collisions
        collisions = self.schema_manager.detect_config_key_collisions(plugin_ids)
        for collision in collisions:
            self.logger.warning(
                "Config collision detected: %s",
                collision.get('message', str(collision))
            )

        return plugin_ids

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
            
            # Check if plugin has a config schema
            schema_path = self.schema_manager.get_schema_path(plugin_id)
            if schema_path is None:
                # Schema file doesn't exist
                self.logger.warning(
                    f"Plugin '{plugin_id}' has no config_schema.json - configuration will not be validated. "
                    f"Consider adding a schema file for better error detection and user experience."
                )
            else:
                # Schema file exists, try to load it
                schema = self.schema_manager.load_schema(plugin_id)
                if schema is None:
                    # Schema exists but couldn't be loaded (likely invalid JSON or schema)
                    self.logger.warning(
                        f"Plugin '{plugin_id}' has a config_schema.json but it could not be loaded. "
                        f"The schema may be invalid. Please verify the schema file at: {schema_path}"
                    )

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
                install_deps=True,
                plugins_dir=self.plugins_dir,
            )
            
            # Store module
            self.plugin_modules[plugin_id] = module

            # Register plugin-shipped fonts with the FontManager (if any).
            # Plugin manifests can declare a "fonts" block that ships custom
            # fonts with the plugin; FontManager.register_plugin_fonts handles
            # the actual loading. Wired here so manifest declarations take
            # effect without requiring plugin code changes.
            font_manifest = manifest.get('fonts')
            if font_manifest and self.font_manager is not None and hasattr(
                self.font_manager, 'register_plugin_fonts'
            ):
                try:
                    self.font_manager.register_plugin_fonts(plugin_id, font_manifest)
                except Exception as e:
                    self.logger.warning(
                        "Failed to register fonts for plugin %s: %s", plugin_id, e
                    )

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

            # Schema validation (warn/degrade only — never blocks loading).
            # A config that violates the plugin's JSON schema is surfaced to the
            # user (log warning + degraded flag in the health tracker) but the
            # plugin still loads exactly as it does today. This deliberately does
            # NOT change load_plugin()'s pass/fail behaviour for any plugin that
            # loads under the current code.
            self._validate_config_schema_soft(plugin_id, config)

            # Store plugin instance
            self.plugins[plugin_id] = plugin_instance
            with self._plugin_last_update_lock:
                self.plugin_last_update[plugin_id] = 0.0
            # Invalidate cached interval so next tick re-derives it for this plugin
            self._update_interval_cache.pop(plugin_id, None)
            
            # Update state based on enabled status
            if config.get('enabled', True):
                self.state_manager.set_state(plugin_id, PluginState.ENABLED)
                # Call on_enable if plugin is enabled
                if hasattr(plugin_instance, 'on_enable'):
                    plugin_instance.on_enable()
            else:
                self.state_manager.set_state(plugin_id, PluginState.DISABLED)
            
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
    
    def _validate_config_schema_soft(self, plugin_id: str, config: Dict[str, Any]) -> None:
        """Validate a plugin's config against its JSON schema — warn/degrade only.

        On a schema violation this logs a warning and marks the plugin degraded
        in the health tracker (when one is wired), so the problem is visible in
        the web UI. It never raises, never changes plugin state, and never
        affects whether the plugin loads. ``config`` here has already been
        merged with schema defaults by the caller, so fields that ship a default
        never appear "missing" — only genuinely user-supplied required fields
        (e.g. an API key) can trip the required-field check.
        """
        try:
            schema = self.schema_manager.load_schema(plugin_id)
        except Exception as e:  # pragma: no cover - defensive
            self.logger.debug("Could not load schema for %s: %s", plugin_id, e)
            return

        if not schema:
            # No schema shipped — nothing to validate. Clear any stale flag.
            self._set_degraded_safe(plugin_id, None)
            return

        try:
            is_valid, errors = self.schema_manager.validate_config_against_schema(
                config, schema, plugin_id
            )
        except Exception as e:  # pragma: no cover - defensive
            # Validation machinery itself failed — do not penalise the plugin.
            self.logger.debug("Schema validation raised for %s: %s", plugin_id, e)
            return

        if is_valid or not errors:
            self._set_degraded_safe(plugin_id, None)
            return

        summary = "; ".join(errors[:5])
        if len(errors) > 5:
            summary += f" (+{len(errors) - 5} more)"
        self.logger.warning(
            "Plugin %s config does not match its schema (loading anyway): %s",
            plugin_id, summary,
        )
        self._set_degraded_safe(plugin_id, f"Config schema: {summary}")

    def _set_degraded_safe(self, plugin_id: str, reason: Optional[str]) -> None:
        """Best-effort ``health_tracker.set_degraded`` that never raises."""
        if not self.health_tracker:
            return
        try:
            self.health_tracker.set_degraded(plugin_id, reason)
        except Exception as e:  # pragma: no cover - defensive
            self.logger.debug("Could not set degraded flag for %s: %s", plugin_id, e)

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
            with self._plugin_last_update_lock:
                self.plugin_last_update.pop(plugin_id, None)
            self._update_interval_cache.pop(plugin_id, None)
            
            # Remove main module from sys.modules if present
            module_name = f"plugin_{plugin_id.replace('-', '_')}"
            sys.modules.pop(module_name, None)

            # Delegate sub-module and cached-module cleanup to the loader
            self.plugin_loader.unregister_plugin_modules(plugin_id)

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
                    manifest = json.load(f)
                with self._discovery_lock:
                    self.plugin_manifests[plugin_id] = manifest
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
        with self._discovery_lock:
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
        with self._discovery_lock:
            pids = list(self.plugin_manifests.keys())
        return [info for info in [self.get_plugin_info(pid) for pid in pids] if info]
    
    def get_plugin_directory(self, plugin_id: str) -> Optional[str]:
        """
        Get the directory path for a plugin.
        
        Args:
            plugin_id: Plugin identifier
            
        Returns:
            Directory path as string or None if not found
        """
        with self._discovery_lock:
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
        with self._discovery_lock:
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
        with self._discovery_lock:
            manifests_snapshot = dict(self.plugin_manifests)
        for plugin_id, manifest in manifests_snapshot.items():
            display_modes = manifest.get('display_modes')
            if isinstance(display_modes, list) and display_modes:
                if any(m.lower() == normalized_mode for m in display_modes):
                    return plugin_id

        return None

    def _get_plugin_update_interval(self, plugin_id: str, plugin_instance: Any) -> Optional[float]:
        """
        Get the data-fetch interval for a plugin (seconds between update() calls).

        Result is cached per plugin_id after the first lookup to avoid calling
        config_manager.get_config() — which returns a full dict copy — on every
        tick of the 30-fps display loop.  The cache is invalidated when a plugin
        is loaded or unloaded.
        """
        if plugin_id in self._update_interval_cache:
            return self._update_interval_cache[plugin_id]

        interval: Optional[float] = None

        # 1. Manifest (immutable after load — preferred source)
        manifest = self.plugin_manifests.get(plugin_id, {})
        raw = manifest.get('update_interval')
        if raw is not None:
            try:
                interval = float(raw)
            except (ValueError, TypeError):
                pass

        # 2. Plugin config (mutable; only read once and then cached)
        if interval is None and self.config_manager:
            try:
                config = self.config_manager.get_config()
                raw = config.get(plugin_id, {}).get('update_interval')
                if raw is not None:
                    try:
                        interval = float(raw)
                    except (ValueError, TypeError):
                        pass
            except (ConfigError, OSError, ValueError, TypeError) as e:
                self.logger.debug("Could not get update interval from config: %s", e)

        # 3. Default
        if interval is None:
            interval = 60.0

        self._update_interval_cache[plugin_id] = interval
        return interval

    def _record_update_failure(
        self,
        plugin_id: str,
        exc: Optional[Exception] = None,
    ) -> None:
        """Apply the standard failure-recovery path for a plugin update.

        Stamps plugin_last_update with the actual failure time so the full
        configured interval elapses before the next retry, then transitions
        the plugin back to ENABLED (not ERROR) with structured error context
        so automatic recovery happens on the next scheduled cycle.

        Args:
            plugin_id: Plugin identifier
            exc: The exception that caused the failure, if any.  When None a
                 synthetic ExecutionFailure exception is constructed from the
                 timeout/executor-error path.
        """
        failure_time = time.time()
        if exc is not None:
            err: Exception = exc
            error_type = type(exc).__name__
        else:
            err = Exception(f"Plugin {plugin_id} execution failed (timeout or executor error)")
            error_type = 'ExecutionFailure'

        error_info = {
            'error': str(err),
            'error_type': error_type,
            'timestamp': failure_time,
            'recoverable': True,
        }
        self.logger.warning("Plugin %s update() failed; will retry after interval", plugin_id)
        with self._plugin_last_update_lock:
            self.plugin_last_update[plugin_id] = failure_time
        self.state_manager.set_state_with_error(plugin_id, PluginState.ENABLED, error_info, error=err)
        if self.health_tracker:
            self.health_tracker.record_failure(plugin_id, err)

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

            with self._plugin_last_update_lock:
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
                        # SimpleNamespace stores `update` as an *instance*
                        # attribute, so attribute lookup returns the plain
                        # function object as-is. A dynamically-built class
                        # (`type(..., {'update': monitored_update})`) instead
                        # stores it as a *class* attribute, which the
                        # descriptor protocol turns into a bound method on
                        # access -- silently prepending the instance as an
                        # implicit first argument to a function that takes
                        # none, raising "monitored_update() takes 0
                        # positional arguments but 1 was given" on every call.
                        success = self.plugin_executor.execute_update(
                            types.SimpleNamespace(update=monitored_update),
                            plugin_id
                        )
                    else:
                        success = self.plugin_executor.execute_update(plugin_instance, plugin_id)
                    
                    if success:
                        with self._plugin_last_update_lock:
                            self.plugin_last_update[plugin_id] = current_time
                        self.state_manager.record_update(plugin_id)
                        # Update state back to ENABLED
                        self.state_manager.set_state(plugin_id, PluginState.ENABLED)
                        # Record success
                        if self.health_tracker:
                            self.health_tracker.record_success(plugin_id)
                    else:
                        self._record_update_failure(plugin_id)
                except Exception as exc:  # pylint: disable=broad-except
                    self.logger.exception("Error updating plugin %s: %s", plugin_id, exc)
                    self._record_update_failure(plugin_id, exc=exc)

    def run_scheduled_updates_with_changes(self, current_time: Optional[float] = None) -> List[str]:
        """
        Like run_scheduled_updates(), but also returns the plugin_ids whose
        plugin_last_update timestamp actually advanced during this call.

        The before/after snapshots and the update pass itself are each
        individually lock-protected against concurrent plugin_last_update
        mutation (Vegas mode calls this from its own background
        update-tick thread, racing the main render loop's plugin updates),
        so callers get an atomic "who got fresh data" answer without
        reaching into plugin_last_update themselves. The lock is not held
        across the update pass so slow/blocking plugin update() calls don't
        serialize against other plugin_last_update readers.
        """
        with self._plugin_last_update_lock:
            old_times = dict(self.plugin_last_update)

        self.run_scheduled_updates(current_time)

        with self._plugin_last_update_lock:
            return [
                plugin_id for plugin_id, new_time in self.plugin_last_update.items()
                if new_time > old_times.get(plugin_id, 0.0)
            ]

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
                    with self._plugin_last_update_lock:
                        self.plugin_last_update[plugin_id] = time.time()
                    self.state_manager.record_update(plugin_id)
                    self.state_manager.set_state(plugin_id, PluginState.ENABLED)
                else:
                    self._record_update_failure(plugin_id)
            except Exception as exc:  # pylint: disable=broad-except
                self.logger.exception("Error updating plugin %s: %s", plugin_id, exc)
                self._record_update_failure(plugin_id, exc=exc)
    
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
                health_info = self.health_tracker.get_health_summary(plugin_id)
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
                resource_info = self.resource_monitor.get_metrics_summary(plugin_id)
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
