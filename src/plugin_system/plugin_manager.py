"""
Plugin Manager

Manages plugin discovery, loading, and lifecycle for the LEDMatrix system.
Loads plugins from the configured plugins directory
(``plugin_system.plugins_directory``, ``plugin-repos/`` by default).

API Version: 1.0.0
"""

import json
import math
import queue
import sys
import time
import threading
import types
from pathlib import Path
from typing import Callable, Dict, List, NamedTuple, Optional, Any, Tuple, Union
import logging
from src import display_watchdog
from src.exceptions import PluginError, ConfigError
from src.logging_config import get_logger
from src.plugin_system.plugin_loader import PluginLoader
from src.plugin_system.plugin_executor import (
    PluginBusyError, PluginExecutor, PluginTimeoutError,
)
from src.plugin_system.plugin_state import PluginStateManager, PluginState
from src.plugin_system.schema_manager import (
    CORE_VEGAS_TUNING_KEYS, SchemaManager, normalize_legacy_booleans,
)
from src.plugin_system.plugin_dirs import (
    ManifestStatus, PluginDirectoryIndex, resolve_plugin_dir,
)
from src.common.permission_utils import (
    ensure_directory_permissions,
    get_plugin_dir_mode
)


class _DeferredConfigChange(NamedTuple):
    """Update-queue item: apply the config change parked for ``plugin_id``.

    Queued by apply_config_change() when the plugin's lock was busy; the
    change itself waits in ``PluginManager._deferred_config_changes`` so only
    the latest one is ever applied.
    """
    plugin_id: str


class PluginManager:
    """
    Manages plugin discovery, loading, and lifecycle.
    
    The PluginManager is responsible for:
    - Discovering plugins in the configured plugins directory
    - Loading plugin modules and instantiating plugin classes
    - Managing plugin lifecycle (load, unload, reload)
    - Providing access to loaded plugins
    - Maintaining plugin manifests
    
    Uses composition with specialized components:
    - PluginLoader: Handles module loading and dependency installation
    - PluginExecutor: Handles plugin execution with timeout and error isolation
    - PluginStateManager: Manages plugin state machine
    """

    # How long unload_plugin() waits for an in-flight update() to finish
    # before tearing the instance down anyway.
    UNLOAD_LOCK_TIMEOUT = 5.0

    # How long the update worker and apply_config_change() wait for a
    # plugin's lock -- the same bound unload already uses for the same lock.
    # A display() frame holds it for milliseconds, so this only runs out when
    # the holder is hung or pathologically slow. The worker then skips that
    # plugin (recorded as a hang, so repeats open its circuit breaker)
    # instead of stalling every other plugin's update behind it.
    PLUGIN_LOCK_TIMEOUT = UNLOAD_LOCK_TIMEOUT

    # Minimum seconds between repeats of the same hang/slow-call warning for
    # one plugin. A hung plugin is re-detected every interval; a slow
    # display() can be re-detected every frame.
    HANG_LOG_INTERVAL = 60.0
    
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
        self.schema_manager = SchemaManager(plugins_dir=self.plugins_dir, logger=self.logger,
                                           config_manager=self.config_manager)
        
        # Lock protecting plugin_manifests and plugin_directories from
        # concurrent mutation (background reconciliation) and reads (requests).
        self._discovery_lock = threading.RLock()
        #: Directories already reported as unloadable, so the warning is
        #: emitted once rather than on every discovery scan.
        self._skip_reported: set = set()

        # Lock protecting plugin_last_update from concurrent mutation/iteration.
        # It's written from run_scheduled_updates() (main loop) and read/diffed by run_scheduled_updates_with_changes(), which
        # Vegas mode calls from its own background update-tick thread.
        self._plugin_last_update_lock = threading.RLock()

        # Active plugins
        self.plugins: Dict[str, Any] = {}
        self.plugin_manifests: Dict[str, Dict[str, Any]] = {}
        self.plugin_directories: Dict[str, Path] = {}
        self.plugin_last_update: Dict[str, float] = {}

        # Cached static data-fetch intervals per plugin_id, so the render
        # loop's scheduling tick does not repeat the manifest/config lookup
        # for every plugin. Cleared on load/unload.
        self._update_interval_cache: Dict[str, Optional[float]] = {}

        # Health tracking (optional, set by display_controller if available)
        self.health_tracker = None
        self.resource_monitor = None

        # --- Asynchronous plugin updates -------------------------------
        # Run inline in the render loop, one slow plugin HTTP fetch in
        # update() freezes scrolling for the whole fetch. Scheduling happens
        # on the render thread (run_scheduled_updates); execution happens on
        # this single background worker. Per-plugin locks keep a plugin's
        # update() and display() mutually exclusive, including across the
        # post-timeout window.
        # Kill switch: plugin_system.synchronous_updates: true restores the
        # inline path.
        #
        # Which thread runs each plugin hook, and what it holds:
        #   __init__, on_enable   the loading thread (main thread at startup,
        #                         the render thread on a live enable).
        #   update()              plugin-update-worker, under the plugin lock,
        #                         via PluginExecutor (whose daemon thread runs
        #                         the call; if it outlives the executor's
        #                         timeout it keeps the lock until it returns).
        #                         Exceptions: the startup pass
        #                         (DisplayController._run_initial_updates, main
        #                         thread, before the display loop starts) and
        #                         the synchronous_updates kill switch (render
        #                         thread) run it without the lock.
        #   display()             the render thread, under a try-lock: a busy
        #                         lock skips the frame. The first frame of a
        #                         screen goes through PluginExecutor. Vegas
        #                         mode's adapter and coordinator take the lock
        #                         with a bounded wait.
        #   on_config_change()    ConfigService's watcher thread, under the
        #                         plugin lock via apply_config_change(); if the
        #                         lock stays busy it is deferred to the update
        #                         worker, which applies it under the lock.
        #   cleanup(), on_disable()  whoever calls unload_plugin(), under the
        #                         lock with UNLOAD_LOCK_TIMEOUT.
        # No wait on a plugin lock is unbounded, so one hung plugin can only
        # cost the worker PLUGIN_LOCK_TIMEOUT per attempt.
        self._update_queue: "queue.Queue[Union[None, Tuple[str, float], _DeferredConfigChange]]" = queue.Queue()
        self._pending_updates: set = set()
        self._pending_lock = threading.Lock()
        # Serializes the "is this plugin eligible?" -> "claim it (RUNNING)"
        # transition. Two schedulers run concurrently in practice — the render
        # loop's _tick_plugin_updates() and Vegas mode's vegas-plugin-tick
        # daemon thread, which is never joined — so without this both can
        # observe ENABLED and both call update() on the same plugin. Held only
        # across the check and the state transition, never across update()
        # itself: that would serialize slow plugins behind each other and
        # reintroduce the stall the async worker exists to avoid.
        self._reservation_lock = threading.Lock()
        self._plugin_locks: Dict[str, threading.Lock] = {}
        self._plugin_locks_guard = threading.Lock()
        self._update_worker: Optional[threading.Thread] = None
        # Plugin ids whose update() has finished since the last time anyone
        # asked. Updates are dispatched to a worker thread, so a caller that
        # wants to know "whose data just changed" cannot learn it by diffing
        # plugin_last_update around run_scheduled_updates() -- that call only
        # enqueues, and the timestamp is stamped later, on the worker. See
        # run_scheduled_updates_with_changes().
        self._completed_updates: set = set()
        self._completed_updates_lock = threading.Lock()
        # Called with a plugin id the moment its data may have changed: its
        # update() completed, or it called notify_vegas_data_changed(). See
        # add_update_listener(). A tuple, replaced rather than mutated, so the
        # worker can iterate it without a lock.
        self._update_listeners: Tuple[Callable[[str], None], ...] = ()
        # Config changes that found the plugin's lock busy, latest per plugin,
        # with the instance they were meant for. See apply_config_change().
        self._deferred_config_changes: Dict[str, Tuple[Any, Dict[str, Any]]] = {}
        self._deferred_config_lock = threading.Lock()
        # key -> (monotonic time last logged, repeats suppressed since)
        self._rate_limited_warnings: Dict[str, Tuple[float, int]] = {}
        self._synchronous_updates = False
        if self.config_manager is not None:
            try:
                cfg = self.config_manager.get_config() or {}
            except (OSError, ValueError) as exc:
                self.logger.warning(
                    "Could not load config to check plugin_system.synchronous_updates "
                    "(%s: %s); defaulting to synchronous updates", type(exc).__name__, exc)
                self._synchronous_updates = True
            else:
                plugin_system_cfg = cfg.get('plugin_system', {})
                if not isinstance(plugin_system_cfg, dict):
                    self.logger.warning(
                        "config plugin_system must be a mapping, got %s; "
                        "defaulting to synchronous updates",
                        type(plugin_system_cfg).__name__)
                    self._synchronous_updates = True
                else:
                    sync_value = plugin_system_cfg.get('synchronous_updates', False)
                    if not isinstance(sync_value, bool):
                        self.logger.warning(
                            "config plugin_system.synchronous_updates must be a boolean, "
                            "got %r; defaulting to synchronous updates", sync_value)
                        self._synchronous_updates = True
                    else:
                        self._synchronous_updates = sync_value
        
        # Ensure plugins directory exists with proper permissions
        try:
            ensure_directory_permissions(self.plugins_dir, get_plugin_dir_mode())
        except (OSError, PermissionError) as e:
            self.logger.error("Could not create plugins directory %s: %s", self.plugins_dir, e, exc_info=True)
            raise PluginError(f"Could not create plugins directory: {self.plugins_dir}", context={'error': str(e)}) from e

    def _report_skip_once(self, key: str, message: str, *args: Any) -> None:
        """Warn about a skipped directory once per process, not per scan.

        Discovery runs on every web UI page load and every config reconcile,
        so warning unconditionally would put a line in the journal each time
        someone opened a page -- the same log-volume problem this is meant to
        help diagnose.
        """
        # setdefault rather than self._skip_reported: tests build a bare
        # scanner with PluginManager.__new__ and skip __init__.
        reported = self.__dict__.setdefault('_skip_reported', set())
        if key in reported:
            return
        reported.add(key)
        self.logger.warning(message, *args)

    def _scan_directory_for_plugins(self, directory: Path) -> List[str]:
        """
        Scan a directory for plugins.

        Which directories count and how an id maps to one is decided by
        :class:`PluginDirectoryIndex` (``src/plugin_system/plugin_dirs.py``),
        shared with the loader, the store and reconciliation. Only
        ``directory`` is scanned: discovery has no fallback to ``plugins/``.
        Directories set aside mid-install (``BACKUP_MARKER`` in the name) are
        skipped so they don't overwrite live entries.

        Args:
            directory: Directory to scan

        Returns:
            List of plugin IDs found
        """
        if not directory.exists():
            return []

        # Build new state locally before acquiring lock
        index = PluginDirectoryIndex.scan(directory)
        if index.error is not None:
            self.logger.error("Error scanning directory %s: %s", directory,
                              index.error, exc_info=index.error)

        for entry in index.entries:
            if entry.status == ManifestStatus.MISSING:
                # A directory here that carries no manifest is not a plugin.
                # Said once, because the alternative is a plugin that is
                # enabled in config, enabled in plugin state, present on disk,
                # and simply absent from the running process with nothing
                # anywhere to say why. Working that out afterwards means
                # reading cache-file mtimes.
                self._report_skip_once(
                    entry.name, "Skipping %s: no manifest.json, so it cannot be "
                    "loaded as a plugin", entry.name)
            elif entry.status == ManifestStatus.UNREADABLE:
                self.logger.warning("Error reading manifest from %s: %s",
                                    entry.path / "manifest.json", entry.error,
                                    exc_info=entry.error)
            elif entry.status == ManifestStatus.NOT_OBJECT:
                # json.load accepts any JSON value, so a manifest holding
                # null, [] or "text" parses. It once raised AttributeError on
                # .get() and aborted the whole scan, so every other plugin on
                # disk, however healthy, silently failed to register.
                self._report_skip_once(
                    entry.name, "Skipping %s: its manifest.json is %s, not a "
                    "JSON object", entry.name, type(entry.manifest).__name__)
            elif entry.status == ManifestStatus.NO_ID:
                # Parsed but unusable. This was the quietest path of all: the
                # manifest is read successfully and then dropped.
                self._report_skip_once(
                    entry.name, "Skipping %s: its manifest.json has no \"id\", "
                    "so there is nothing to register it under", entry.name)

        plugins = index.plugins()
        for plugin_id, entries in index.duplicates().items():
            self._report_skip_once(
                "duplicate:" + plugin_id,
                "Plugin id %r is declared by %d directories (%s); using %s",
                plugin_id, len(entries), ", ".join(e.name for e in entries),
                plugins[plugin_id].name)

        new_manifests: Dict[str, Dict[str, Any]] = {
            plugin_id: entry.manifest for plugin_id, entry in plugins.items()}
        new_directories: Dict[str, Path] = {
            plugin_id: entry.path for plugin_id, entry in plugins.items()}

        # Replace shared state under lock so uninstalled plugins don't linger
        with self._discovery_lock:
            self.plugin_manifests.clear()
            self.plugin_manifests.update(new_manifests)
            self.plugin_directories.clear()
            self.plugin_directories.update(new_directories)

        return list(plugins)

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

    def load_plugin(self, plugin_id: str, force_enabled: bool = False) -> bool:
        """Load a plugin by ID; see _load_plugin.

        Loading can install the plugin's dependencies with pip -- minutes,
        not seconds. When that happens on the display's render thread (a
        plugin enabled from the web UI, or loaded for on-demand), its
        systemd watchdog gets a longer limit for the duration. Start-up
        loads, on a thread pool, are covered by the start-up allowance.
        """
        with display_watchdog.extended(display_watchdog.PLUGIN_LOAD_ALLOWANCE_SECONDS,
                                       f'loading plugin {plugin_id}'):
            return self._load_plugin(plugin_id, force_enabled)

    def _load_plugin(self, plugin_id: str, force_enabled: bool = False) -> bool:
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
            force_enabled: Run the plugin enabled even though config.json has
                it disabled. On-demand uses this to show a disabled plugin
                (DisplayController._load_plugin_for_on_demand). Only the
                instance's config says enabled; config.json is not written.
            
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
            plugin_dir = self.plugin_loader.find_plugin_directory(
                plugin_id,
                self.plugins_dir,
                self.plugin_directories
            )
            
            if plugin_dir is None:
                self.logger.error("Plugin directory not found: %s", plugin_id)
                self.logger.error("Searched in: %s", self.plugins_dir)
                self.state_manager.set_state(plugin_id, PluginState.ERROR)
                return False
            
            # Update mapping if found via search
            if plugin_id not in self.plugin_directories:
                self.plugin_directories[plugin_id] = plugin_dir
            
            # Get plugin config
            if self.config_manager:
                full_config = self.config_manager.load_config()
                config = full_config.get(plugin_id, {})
            else:
                config = {}
            
            # Check if plugin has a config schema
            schema = None
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

            # Legacy booleans read as objects, then schema defaults: the same
            # preparation saves, GET /plugins/config and hot reload apply
            # (prepare_plugin_config). In memory only: config.json is written
            # by saves, never by loading a plugin.
            config = self.prepare_plugin_config(plugin_id, config, schema=schema)
            if force_enabled:
                # A copy: prepare_plugin_config can hand back the section from
                # config_manager's cached config, and setting the flag there
                # would read as enabled to everything else in this process.
                config = dict(config)
                config['enabled'] = True
            
            # Use PluginLoader to load plugin
            plugin_instance, _module = self.plugin_loader.load_plugin(
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
                    self.font_manager.register_plugin_fonts(
                        plugin_id, font_manifest, plugin_dir=plugin_dir)
                except Exception as e:
                    self.logger.warning(
                        "Failed to register fonts for plugin %s: %s", plugin_id, e
                    )

            # Validate configuration
            if hasattr(plugin_instance, 'validate_config'):
                try:
                    if not plugin_instance.validate_config():
                        self.logger.error("Plugin %s configuration validation failed", plugin_id)
                        self._discard_failed_load(plugin_id)
                        self.state_manager.set_state(plugin_id, PluginState.ERROR)
                        return False
                except Exception as e:
                    self.logger.error("Error validating plugin %s config: %s", plugin_id, e, exc_info=True)
                    self._discard_failed_load(plugin_id)
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
                    try:
                        plugin_instance.on_enable()
                    except Exception:
                        # Undo the registration above before the outer
                        # handler marks it ERROR: left in self.plugins, the
                        # next load_plugin() would return True as "already
                        # loaded" for a plugin that never enabled.
                        self.plugins.pop(plugin_id, None)
                        with self._plugin_last_update_lock:
                            self.plugin_last_update.pop(plugin_id, None)
                        self._update_interval_cache.pop(plugin_id, None)
                        raise
            else:
                self.state_manager.set_state(plugin_id, PluginState.DISABLED)

            # The version this instance runs, for the runtime snapshot the
            # web UI reads: the manifest on disk can move on after an update.
            version = manifest.get('version')
            self.state_manager.record_loaded(
                plugin_id, version if isinstance(version, str) else None)

            self.logger.info("Loaded plugin: %s", plugin_id)
            
            return True
            
        except PluginError as e:
            self.logger.error("Plugin error loading %s: %s", plugin_id, e, exc_info=True)
            self._discard_failed_load(plugin_id)
            self.state_manager.set_state(plugin_id, PluginState.ERROR, error=e)
            return False
        except Exception as e:
            self.logger.error("Unexpected error loading plugin %s: %s", plugin_id, e, exc_info=True)
            self._discard_failed_load(plugin_id)
            self.state_manager.set_state(plugin_id, PluginState.ERROR, error=e)
            return False

    def _discard_failed_load(self, plugin_id: str) -> None:
        """Forget a plugin's imported module and font registrations after a
        failed load.

        load_module() reuses ``plugin_<id>`` from sys.modules, so a module
        left behind by a load that failed after import (instantiation,
        validate_config, on_enable) would keep serving the old code even
        after the user fixes the plugin and reloads it. Never raises.
        """
        try:
            sys.modules.pop(f"plugin_{plugin_id.replace('-', '_')}", None)
            self.plugin_loader.unregister_plugin_modules(plugin_id)
        except Exception as e:  # pragma: no cover - defensive
            self.logger.debug("Could not drop modules of %s: %s", plugin_id, e)
        try:
            if self.font_manager is not None and hasattr(self.font_manager, 'forget_manager_fonts'):
                self.font_manager.forget_manager_fonts(plugin_id)
        except Exception as e:
            self.logger.debug("Could not forget fonts of %s: %s", plugin_id, e)
    
    #: Config keys the **core** reads out of a plugin's own config block. The
    #: plugin never declares them, so a schema with
    #: ``"additionalProperties": false`` — most published ones do — reports
    #: them as violations and the plugin gets flagged degraded in the web UI for
    #: using a documented core feature.
    #:
    #: Listed explicitly rather than matched on a ``vegas_`` prefix, because
    #: ``vegas_mode`` is the opposite case: plugins *do* declare that one, and a
    #: prefix rule would silently stop validating it.
    #:
    #: Read by: ``vegas_mode/plugin_adapter.py`` (``vegas_width_pct``,
    #: ``vegas_overflow``, ``vegas_live``) and ``base_plugin.py``
    #: (``vegas_max_width_screens``, ``vegas_participation``).
    #:
    #: The list itself lives with the other core-owned per-plugin properties in
    #: ``schema_manager.CORE_PLUGIN_PROPERTIES``, which the web save path also
    #: uses to keep these keys.
    CORE_OWNED_CONFIG_KEYS = CORE_VEGAS_TUNING_KEYS

    def prepare_plugin_config(self, plugin_id: str, config: Any,
                              schema: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """The config a plugin runs with, built from its raw config.json section.

        A plugin that turned an on/off boolean into an ``{enabled, ...}``
        object still finds the boolean in config.json until its settings are
        next saved; it is read as the object, and schema defaults fill in the
        rest (``SchemaManager.prepare_plugin_config``). Used when loading a
        plugin and on hot reload (DisplayController), so ``on_config_change``
        receives the same shape the plugin was constructed with.

        Never raises: on failure the legacy-boolean pass alone is applied, or
        failing that the section is returned as it was.
        """
        if schema is None:
            try:
                schema = self.schema_manager.load_schema(plugin_id)
            except Exception as e:
                self.logger.debug("Could not load schema for %s: %s", plugin_id, e)
                schema = None
        upgraded: List[str] = []
        try:
            prepared = self.schema_manager.prepare_plugin_config(
                plugin_id, config, schema=schema, changed_paths=upgraded)
            self.logger.debug("Merged config with schema defaults for %s", plugin_id)
        except Exception as e:
            self.logger.warning("Could not apply schema defaults for %s: %s", plugin_id, e)
            # Continue without defaults if they can't be applied
            upgraded = []
            prepared = config if isinstance(config, dict) else {}
            if schema:
                try:
                    prepared = normalize_legacy_booleans(prepared, schema, upgraded)
                except Exception as legacy_error:
                    self.logger.warning(
                        "Could not read legacy boolean settings for %s: %s",
                        plugin_id, legacy_error)
        if upgraded:
            self.logger.info(
                "Plugin %s: reading legacy boolean setting %s as "
                "{\"enabled\": ...}; saving the plugin's settings "
                "stores the new shape",
                plugin_id, ", ".join(upgraded),
            )
        return prepared

    def _strip_core_owned_keys(self, config: Dict[str, Any]) -> Dict[str, Any]:
        """A shallow copy of ``config`` without the core's own tuning keys.

        Only the top level is touched, and only when such a key is present, so
        the common case allocates nothing extra.
        """
        if not isinstance(config, dict):
            return config
        if not self.CORE_OWNED_CONFIG_KEYS.intersection(config):
            return config
        return {k: v for k, v in config.items()
                if k not in self.CORE_OWNED_CONFIG_KEYS}

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
                self._strip_core_owned_keys(config), schema, plugin_id
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
        
        # Take the plugin's lock so cleanup()/on_disable() can't run while
        # the update worker is mid-update() on this instance. Bounded: an
        # update() that hangs past PluginExecutor's timeout keeps holding the
        # lock from its lingering thread, and unload must still go through.
        lock = self.get_plugin_lock(plugin_id)
        lock_acquired = lock.acquire(timeout=self.UNLOAD_LOCK_TIMEOUT)
        if not lock_acquired:
            self.logger.warning(
                "Plugin %s still busy after %.1fs; unloading without its lock",
                plugin_id, self.UNLOAD_LOCK_TIMEOUT)
        try:
            return self._unload_plugin_locked(plugin_id)
        finally:
            if lock_acquired:
                lock.release()

    def _unload_plugin_locked(self, plugin_id: str) -> bool:
        """Body of unload_plugin(); caller holds (or gave up on) the plugin lock."""
        if plugin_id not in self.plugins:  # unloaded while we waited
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
            with self._deferred_config_lock:
                self._deferred_config_changes.pop(plugin_id, None)
            with self._plugin_last_update_lock:
                self.plugin_last_update.pop(plugin_id, None)
            self._update_interval_cache.pop(plugin_id, None)
            
            # Remove main module from sys.modules if present
            module_name = f"plugin_{plugin_id.replace('-', '_')}"
            sys.modules.pop(module_name, None)

            # Delegate sub-module and cached-module cleanup to the loader
            self.plugin_loader.unregister_plugin_modules(plugin_id)

            # Its font registrations go with it (the Fonts tab's "Used by").
            try:
                if self.font_manager is not None and hasattr(self.font_manager, 'forget_manager_fonts'):
                    self.font_manager.forget_manager_fonts(plugin_id)
            except Exception as e:
                self.logger.debug("Could not forget fonts of %s: %s", plugin_id, e)

            # Update state
            self.state_manager.set_state(plugin_id, PluginState.UNLOADED)
            self.state_manager.clear_state(plugin_id)
            
            self.logger.info("Unloaded plugin: %s", plugin_id)
            return True
            
        except Exception as e:
            self.logger.error("Error unloading plugin %s: %s", plugin_id, e, exc_info=True)
            self.state_manager.set_state(plugin_id, PluginState.ERROR, error=e)
            if plugin_id not in self.plugins:
                # Failed after the instance was dropped: it is not loaded.
                self.state_manager.record_unloaded(plugin_id)
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
        
        # Re-read the manifest so an edit to it takes effect, from the
        # directory discovery found the plugin in: a directory's name need not
        # be the id its manifest declares.
        with self._discovery_lock:
            directories = dict(self.plugin_directories)
        plugin_dir = self.plugin_loader.find_plugin_directory(
            plugin_id, self.plugins_dir, directories)
        manifest_path = plugin_dir / "manifest.json" if plugin_dir is not None else None
        if manifest_path is not None and manifest_path.exists():
            try:
                with open(manifest_path, 'r', encoding='utf-8') as f:
                    manifest = json.load(f)
                with self._discovery_lock:
                    self.plugin_manifests[plugin_id] = manifest
            except Exception as e:
                self.logger.error("Error reading manifest: %s", e, exc_info=True)
                return False
        
        return self.load_plugin(plugin_id)
    
    def discovered_plugin_ids(self) -> set:
        """Snapshot of the discovered plugin ids, taken under the discovery lock.

        Callers on other threads (the config watcher) must not iterate
        ``plugin_manifests`` directly: discovery rebuilds it entry by entry, so
        an unsynchronised reader can see a half-populated mapping or raise
        "dictionary changed size during iteration".
        """
        with self._discovery_lock:
            return set(self.plugin_manifests)

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
                # One plugin's get_info() raising must not take down the
                # whole installed-plugins listing (/api/v3/plugins/installed).
                try:
                    info['runtime_info'] = plugin.get_info()
                except Exception as e:
                    self.logger.warning("Plugin %s get_info() failed: %s", plugin_id, e)
                    info['runtime_info'] = {}
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

        ``plugin_id`` often comes straight from a request, so anything that is
        not one plain path segment (``..``, ``a/b``, an absolute path) is
        refused instead of being joined onto ``plugins_dir``. The join is not
        resolved further: dev plugins are symlinks into ``plugins_dir``.

        The discovery map is authoritative. For an id discovery has not seen,
        only directory names are tried -- ``<id>`` then ``ledmatrix-<id>``,
        in ``plugins_dir`` only -- so a miss on a web request never reads
        every manifest on disk. Rules: ``src/plugin_system/plugin_dirs.py``.
        """
        with self._discovery_lock:
            if plugin_id in self.plugin_directories:
                return str(self.plugin_directories[plugin_id])

        plugin_dir = resolve_plugin_dir(
            plugin_id, [self.plugins_dir], prefix=True, case_insensitive=False,
            by_manifest=False)
        return str(plugin_dir) if plugin_dir is not None else None
    
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

    def _dynamic_update_interval(self, plugin_id: str, plugin_instance: Any) -> Optional[float]:
        """The interval a plugin asks for right now, or None if it has no view."""
        hook = getattr(plugin_instance, 'get_update_interval', None)
        if not callable(hook):
            return None
        try:
            requested = hook()
        except Exception as exc:  # pylint: disable=broad-except
            self.logger.debug(
                "get_update_interval() failed for %s, using the static interval: %s",
                plugin_id, exc)
            return None
        if requested is None:
            return None
        if isinstance(requested, bool):
            self.logger.debug(
                "get_update_interval() returned a bool for %s, which is not a number",
                plugin_id)
            return None
        try:
            requested = float(requested)
        except (TypeError, ValueError):
            self.logger.debug(
                "get_update_interval() returned %r for %s, which is not a number",
                requested, plugin_id)
            return None
        if not math.isfinite(requested):  # NaN / +inf / -inf
            return None
        return max(requested, self.MIN_DYNAMIC_UPDATE_INTERVAL)

    #: Floor for a plugin-requested interval. A plugin asking for 0 (or a
    #: negative) would otherwise be re-entered on every tick of the render
    #: loop, which is a busy-wait against whatever API it fetches.
    MIN_DYNAMIC_UPDATE_INTERVAL = 5.0

    def _get_plugin_update_interval(self, plugin_id: str, plugin_instance: Any) -> Optional[float]:
        """
        Get the data-fetch interval for a plugin (seconds between update() calls).

        A plugin may implement ``get_update_interval()`` to vary its own cadence
        at runtime, which the static manifest value cannot express. The case
        this exists for: a sports scoreboard needs to poll every 15s while a
        game is in progress and every 15 minutes when nothing is on, and only
        the plugin knows which is true right now. Returning None from the hook
        means "no opinion", and the static resolution below applies.

        The hook is called on every scheduling tick, so implementations must be
        cheap — attribute reads, no config lookups and no I/O. A raising or
        non-numeric hook is ignored rather than allowed to stop the plugin
        updating, since a scheduler that propagates a plugin bug stops every
        other plugin too.

        Precedence, first match wins: the ``get_update_interval()`` hook, then
        ``update_interval`` in the plugin's **manifest**, then
        ``update_interval`` in the plugin's section of config.json, then 60s.
        So a config value only drives the scheduler for a plugin whose
        manifest sets none; when the manifest sets one, the config value is
        ignored here (a plugin may still read it itself, e.g. to skip fetches
        inside update()).

        The static result is cached per plugin_id after the first lookup, so
        the manifest/config resolution is not repeated on every scheduling
        tick of the display loop. A change to ``update_interval`` in
        config.json therefore takes effect when the plugin is next loaded or
        unloaded, which clears the cache. The dynamic hook is deliberately
        *not* cached: caching it would defeat its only purpose.
        """
        dynamic = self._dynamic_update_interval(plugin_id, plugin_instance)
        if dynamic is not None:
            return dynamic

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
        log: bool = True,
        count_failure: bool = True,
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
            log: Log the generic failure line. Callers that already logged
                 something more specific (rate-limited) pass False.
            count_failure: Record the failure in plugin health, where it
                 counts toward the circuit breaker. A busy skip passes False:
                 it records itself as a busy skip, reporting only.
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
        if log:
            self.logger.warning("Plugin %s update() failed; will retry after interval", plugin_id)
        with self._plugin_last_update_lock:
            self.plugin_last_update[plugin_id] = failure_time
        self.state_manager.set_state_with_error(plugin_id, PluginState.ENABLED, error_info)
        if count_failure and self.health_tracker:
            self.health_tracker.record_failure(plugin_id, err)

    def _warn_rate_limited(self, key: str, message: str, *args: Any) -> None:
        """Log a warning at most once per HANG_LOG_INTERVAL for ``key``.

        Repeats in between are counted and the count is appended to the next
        one that is logged, so the journal shows the problem continuing
        without a line per frame or per scheduler tick.
        """
        # setdefault: tests build bare managers with PluginManager.__new__.
        seen = self.__dict__.setdefault('_rate_limited_warnings', {})
        now = time.monotonic()
        last, suppressed = seen.get(key, (None, 0))
        if last is not None and now - last < self.HANG_LOG_INTERVAL:
            seen[key] = (last, suppressed + 1)
            return
        seen[key] = (now, 0)
        if suppressed:
            message += " (%d more since the last warning)"
            args = args + (suppressed,)
        self.logger.warning(message, *args)

    def _record_hang(self, plugin_id: str, operation: str, seconds: float,
                     err: Exception) -> None:
        """Record a hang in plugin health: a failure to the circuit breaker.

        PluginHealthTracker.record_hang also counts the hang separately. Never
        raises: this runs on the update worker and the render thread.
        """
        tracker = self.health_tracker
        if tracker is None:
            return
        try:
            tracker.record_hang(plugin_id, operation, seconds, err)
        except Exception as e:  # pylint: disable=broad-except
            self.logger.debug("Could not record hang for %s: %s", plugin_id, e)

    def note_display_duration(self, plugin_id: str, seconds: float) -> None:
        """Account for one display() call that took ``seconds``.

        Called by the render loop for every frame, so the common case is one
        comparison. At or above PluginExecutor.SLOW_DISPLAY_SECONDS the call
        is logged (rate-limited) and counted as slow in plugin health; at or
        above the executor's timeout -- the limit the first frame of a screen
        is already held to -- it is recorded as a hang, which the circuit
        breaker counts as a failure.
        """
        if seconds < PluginExecutor.SLOW_DISPLAY_SECONDS:
            return
        if seconds >= self.plugin_executor.default_timeout:
            self.record_display_hang(plugin_id, seconds)
            return
        self._warn_rate_limited(
            "slow-display:" + plugin_id,
            "Plugin %s display() took %.2fs; a frame should take milliseconds "
            "(is it fetching or loading files in display()?)", plugin_id, seconds)
        tracker = self.health_tracker
        record_slow = getattr(tracker, 'record_slow_call', None) if tracker is not None else None
        if callable(record_slow):
            try:
                record_slow(plugin_id, 'display', seconds)
            except Exception as e:  # pylint: disable=broad-except
                self.logger.debug("Could not record slow display for %s: %s", plugin_id, e)

    def record_display_hang(self, plugin_id: str, seconds: float) -> None:
        """Record a display() call that ran ``seconds``, past its limit.

        Either it has since returned (note_display_duration) or it is still
        running on the executor's lingering thread, holding the plugin's lock
        (the render loop's first-frame dispatch).
        """
        self._warn_rate_limited(
            "hung-display:" + plugin_id,
            "Plugin %s display() ran for at least %.1fs (limit %.0fs); recorded "
            "as a hang -- repeated hangs open its circuit breaker",
            plugin_id, seconds, self.plugin_executor.default_timeout)
        self._record_hang(plugin_id, 'display', seconds, PluginTimeoutError(
            f"Plugin {plugin_id} display() ran for at least {seconds:.1f}s"))

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

            interval = self._get_plugin_update_interval(plugin_id, plugin_instance)
            if interval is None:
                continue

            # Eligibility check, due check and the RUNNING transition happen
            # together, so a concurrent scheduler cannot claim the same plugin.
            if not self._reserve_for_update(plugin_id, current_time, interval):
                continue

            if self._synchronous_updates:
                # Kill-switch path: the original inline execution
                # (blocks the caller until update() completes/times out)
                self._execute_update_now(plugin_id, plugin_instance, current_time)
                # Up to the executor's 30s each, one after another on the
                # render thread: check in with its watchdog between them.
                display_watchdog.beat()
            else:
                self._enqueue_update(plugin_id, current_time)

    def _reserve_for_update(
        self,
        plugin_id: str,
        current_time: Optional[float] = None,
        interval: Optional[float] = None,
    ) -> bool:
        """Atomically claim a plugin for update, returning True if we won it.

        can_execute() and the RUNNING transition have to happen under one lock.
        As two separate calls, two scheduler threads can both see ENABLED and
        both go on to run the same plugin's update() concurrently — unsafe for
        any plugin that isn't reentrant (shared mutable state, a non-thread-safe
        HTTP session or cache).

        The due-time check is inside the lock too. Leaving it outside would let
        a second thread that had already decided "due" claim the plugin the
        instant the first finished, running update() twice in one interval.

        Args:
            plugin_id: Plugin to claim.
            current_time: Now, for the due check. Omit to skip that check.
            interval: Seconds between updates. Omit to skip the due check.

        Returns:
            True if this caller reserved the plugin and must dispatch it,
            False if it is ineligible, not yet due, or already claimed.
        """
        with self._reservation_lock:
            if not self.state_manager.can_execute(plugin_id):
                return False

            if current_time is not None and interval is not None:
                with self._plugin_last_update_lock:
                    last_update = self.plugin_last_update.get(plugin_id, 0.0)
                if last_update != 0.0 and (current_time - last_update) < interval:
                    return False

            self.state_manager.set_state(plugin_id, PluginState.RUNNING)
            return True

    def _release_reservation(self, plugin_id: str) -> None:
        """Hand a claimed plugin back when it never got dispatched.

        Without this a plugin reserved but not queued would sit in RUNNING
        forever, and can_execute() would refuse it on every later tick.
        """
        self.state_manager.set_state(plugin_id, PluginState.ENABLED)

    def get_plugin_lock(self, plugin_id: str) -> threading.Lock:
        """Per-plugin lock keeping update(), display() and on_config_change()
        mutually exclusive.

        The update worker holds it for the duration of a plugin's update();
        the display side acquires it non-blocking and skips that frame's
        display() call when the plugin is mid-update. Every other waiter uses
        a bounded acquire (see the thread notes in __init__).
        """
        with self._plugin_locks_guard:
            lock = self._plugin_locks.get(plugin_id)
            if lock is None:
                lock = threading.Lock()
                self._plugin_locks[plugin_id] = lock
            return lock

    def _enqueue_update(self, plugin_id: str, scheduled_time: float) -> None:
        """Queue an already-reserved update for the background worker.

        The caller has reserved the plugin (RUNNING), which is what blocks
        re-entry and shows the truthful state in the web UI while the item
        waits its turn. The pending set stays as a second line of defence; if
        it ever fires the reservation has to be handed back, or the plugin
        would sit in RUNNING with nothing queued to release it.
        """
        with self._pending_lock:
            if plugin_id in self._pending_updates:
                self.logger.warning(
                    "Plugin %s reserved for update but already queued; "
                    "releasing the reservation", plugin_id)
                self._release_reservation(plugin_id)
                return
            self._pending_updates.add(plugin_id)
        try:
            self._ensure_update_worker()
            self._update_queue.put((plugin_id, scheduled_time))
        except Exception as exc:  # pylint: disable=broad-except
            # Thread.start() raises RuntimeError when the OS refuses a new
            # thread — a real condition on a Pi under memory pressure. Nothing
            # is queued to release the plugin at that point, so the claim has to
            # be undone here, or it sits in RUNNING with nothing to clear it and
            # can_execute() refuses it for the rest of the process. Swallowed
            # rather than raised so the remaining plugins in this tick still get
            # their turn.
            self.logger.error(
                "Could not queue update for plugin %s (%s: %s); releasing the "
                "reservation so the next tick can retry",
                plugin_id, type(exc).__name__, exc, exc_info=True)
            with self._pending_lock:
                self._pending_updates.discard(plugin_id)
            self._release_reservation(plugin_id)

    def _ensure_update_worker(self) -> None:
        if self._update_worker is not None and self._update_worker.is_alive():
            return
        self._update_worker = threading.Thread(
            target=self._update_worker_loop, name='plugin-update-worker',
            daemon=True)
        self._update_worker.start()

    def _update_worker_loop(self) -> None:
        """Single worker: dispatches queued updates off the render thread
        (matching the old inline behavior — no thundering herd of
        concurrent fetches).

        The plugin's lock is acquired here, before its instance is looked
        up, and the instance is re-fetched under the lock — a concurrent
        unload_plugin() can't leave this loop about to run update() on an
        instance that's already been torn down. The lock — and RUNNING/
        pending lifecycle state — is released by the update itself once the
        real update() call genuinely finishes (see _execute_update_now),
        which can be after this dispatch returns if PluginExecutor's own
        timeout elapses first.

        The lock wait is bounded by PLUGIN_LOCK_TIMEOUT. Whatever holds it
        past that -- a hung display() on the render thread, a lingering
        executor thread, or a long but healthy Vegas content render -- costs
        this worker that long once per attempt, and the plugin's update is
        skipped and reported as a busy skip (_skip_busy_update), which never
        counts toward the circuit breaker; the other plugins' queued updates
        carry on.
        """
        while True:
            item = self._update_queue.get()
            if item is None:  # shutdown sentinel
                return
            if isinstance(item, _DeferredConfigChange):
                self._apply_deferred_config_change(item.plugin_id)
                continue
            plugin_id, scheduled_time = item
            lock = self.get_plugin_lock(plugin_id)
            wait_start = time.monotonic()
            if not lock.acquire(timeout=self.PLUGIN_LOCK_TIMEOUT):
                self._skip_busy_update(plugin_id, time.monotonic() - wait_start)
                continue
            plugin_instance = self.plugins.get(plugin_id)
            if plugin_instance is None:  # unloaded while queued; its
                # lifecycle state was already cleared by unload_plugin —
                # leave it alone rather than resurrecting it to ENABLED
                lock.release()
                with self._pending_lock:
                    self._pending_updates.discard(plugin_id)
                continue
            # A config change that found the lock busy goes in first, so
            # this update() runs against the settings the user saved.
            self._apply_deferred_config_locked(plugin_id, plugin_instance)
            try:
                self._execute_update_now(plugin_id, plugin_instance,
                                         scheduled_time, lock=lock)
            except Exception:  # pylint: disable=broad-except
                # _execute_update_now guarantees the lock/pending bookkeeping
                # is released via its own _finish() before returning or
                # raising; this is a last-resort log only.
                self.logger.exception("update worker: unexpected error for %s",
                                      plugin_id)

    def _skip_busy_update(self, plugin_id: str, waited: float) -> None:
        """Give up on a queued update whose plugin lock stayed held.

        Same bookkeeping as a failed update() -- pending slot dropped before
        the state returns to ENABLED with PluginBusyError error info,
        last-update stamped so the retry waits a full interval -- but
        report-only in health: counted as a busy skip (``busy_skip_count`` /
        ``last_busy_skip``), never as a failure or a hang. The lock holder
        may be perfectly healthy: Vegas prefetch holds a plugin's lock for its
        whole content render, which on a slow Pi can outlast
        PLUGIN_LOCK_TIMEOUT, and counting that would pull a healthy plugin
        from rotation. Real hangs -- display() or update() past the executor
        timeout -- are recorded where they are measured and still open the
        breaker.
        """
        with self._pending_lock:
            self._pending_updates.discard(plugin_id)
        if plugin_id not in self.plugins:
            # Unloaded while we waited: its lifecycle state is already
            # cleared; recording anything would resurrect it as ENABLED.
            return
        self._warn_rate_limited(
            "busy-update:" + plugin_id,
            "Plugin %s update skipped: its lock was still held after %.1fs "
            "(a display(), Vegas render or update() of it is still running); "
            "retrying next interval, not counted as a failure", plugin_id, waited)
        self._record_update_failure(
            plugin_id,
            exc=PluginBusyError(
                f"Plugin {plugin_id} busy: its lock was held for over {waited:.1f}s "
                "by a slow or hung display()/update(); update skipped"),
            log=False,
            count_failure=False)
        tracker = self.health_tracker
        record_busy = getattr(tracker, 'record_busy_skip', None) if tracker is not None else None
        if callable(record_busy):
            try:
                record_busy(plugin_id, 'update lock wait', waited)
            except Exception as e:  # pylint: disable=broad-except
                self.logger.debug("Could not record busy skip for %s: %s", plugin_id, e)

    def apply_config_change(self, plugin_id: str, new_config: Dict[str, Any],
                            plugin_instance: Optional[Any] = None) -> bool:
        """Call ``on_config_change(new_config)`` without racing update()/display().

        Runs on the calling thread -- ConfigService's watcher, for the display
        service -- holding the plugin's lock, waited on for at most
        PLUGIN_LOCK_TIMEOUT. If the lock is still busy (an update() mid-fetch
        can outlast that) the change is parked and handed to the update
        worker, which applies it under the same lock once it is free, and at
        the latest just before the plugin's next update(). A later change for
        the same plugin replaces a parked one.

        Exceptions from on_config_change propagate on the immediate path,
        as they did when the caller invoked it directly.

        Args:
            plugin_id: Plugin identifier.
            new_config: The prepared config to hand the plugin.
            plugin_instance: The instance to notify; defaults to the loaded one.

        Returns:
            True if on_config_change ran now, False if it was deferred or there
            is no loaded plugin to notify.
        """
        if plugin_instance is None:
            plugin_instance = self.plugins.get(plugin_id)
        if plugin_instance is None or not hasattr(plugin_instance, 'on_config_change'):
            return False
        lock = self.get_plugin_lock(plugin_id)
        if lock.acquire(timeout=self.PLUGIN_LOCK_TIMEOUT):
            try:
                with self._deferred_config_lock:
                    # This change supersedes any older one still parked.
                    self._deferred_config_changes.pop(plugin_id, None)
                plugin_instance.on_config_change(new_config)
            finally:
                lock.release()
            return True

        with self._deferred_config_lock:
            self._deferred_config_changes[plugin_id] = (plugin_instance, new_config)
        self._warn_rate_limited(
            "busy-config:" + plugin_id,
            "Plugin %s is busy (lock held for over %.1fs); its config change "
            "will be applied by the update worker once it is free",
            plugin_id, self.PLUGIN_LOCK_TIMEOUT)
        try:
            self._ensure_update_worker()
            self._update_queue.put(_DeferredConfigChange(plugin_id))
        except Exception as exc:  # pylint: disable=broad-except
            # No worker (thread start refused): still parked, so the next
            # update() of this plugin applies it.
            self.logger.error(
                "Could not queue the config change for plugin %s (%s: %s); it "
                "will be applied before its next update()",
                plugin_id, type(exc).__name__, exc)
        return False

    def _apply_deferred_config_change(self, plugin_id: str) -> None:
        """Worker side of a parked config change: take the lock, apply it."""
        with self._deferred_config_lock:
            if plugin_id not in self._deferred_config_changes:
                return  # applied or superseded meanwhile
        lock = self.get_plugin_lock(plugin_id)
        wait_start = time.monotonic()
        if not lock.acquire(timeout=self.PLUGIN_LOCK_TIMEOUT):
            self._warn_rate_limited(
                "busy-config:" + plugin_id,
                "Plugin %s still busy after %.1fs; its config change stays "
                "parked until its next update()",
                plugin_id, time.monotonic() - wait_start)
            return
        try:
            self._apply_deferred_config_locked(plugin_id, self.plugins.get(plugin_id))
        finally:
            lock.release()

    def _apply_deferred_config_locked(self, plugin_id: str,
                                      current_instance: Optional[Any]) -> None:
        """Apply the parked config change for plugin_id; caller holds its lock."""
        with self._deferred_config_lock:
            entry = self._deferred_config_changes.pop(plugin_id, None)
        if entry is None:
            return
        instance, new_config = entry
        if current_instance is None or instance is not current_instance:
            # Unloaded, or reloaded as a new instance built from the current
            # config: nothing left to tell.
            return
        try:
            instance.on_config_change(new_config)
            self.logger.info("Applied deferred config change for plugin %s", plugin_id)
        except Exception:  # pylint: disable=broad-except
            self.logger.exception("Error in plugin %s config change handler", plugin_id)

    def stop_update_worker(self, timeout: float = 5.0) -> None:
        """Signal the worker to exit (used by cleanup; thread is a daemon)."""
        if self._update_worker is not None and self._update_worker.is_alive():
            self._update_queue.put(None)
            self._update_worker.join(timeout=timeout)
            if self._update_worker.is_alive():
                self.logger.warning(
                    "Update worker did not stop within %.1fs; it is a daemon "
                    "thread and will be abandoned on shutdown", timeout)

    def _execute_update_now(self, plugin_id: str, plugin_instance: Any,
                            scheduled_time: float,
                            lock: Optional[threading.Lock] = None) -> None:
        """Execute a plugin's update() via PluginExecutor, then bookkeep.

        Caller is responsible for having set RUNNING state.

        On the synchronous path (``lock=None``) this is the original,
        unchanged inline behavior. On the async worker path, PluginExecutor's
        internal thread.join(timeout) blocks only the calling thread -- on
        timeout the lingering daemon update-thread keeps running the real
        plugin.update() call unkillable in the background. So that the
        plugin's lock (and its RUNNING/pending lifecycle state) stays held
        for that real duration rather than just this bounded wait, ownership
        of both is carried by the wrapped update callable itself, released
        from whichever thread actually finishes it -- see _finish() below.
        """
        finish_guard = threading.Lock()
        finished = {'done': False}

        def _finish(success: bool, exc: Optional[Exception] = None) -> None:
            with finish_guard:
                if finished['done']:
                    return
                finished['done'] = True
            try:
                # The plugin was unloaded (or reloaded as a new instance)
                # while this update() ran: unload_plugin() already cleared its
                # lifecycle state, so recording success/failure here would
                # resurrect a torn-down plugin as ENABLED. Only release.
                if self.plugins.get(plugin_id) is not plugin_instance:
                    if lock is not None:
                        with self._pending_lock:
                            self._pending_updates.discard(plugin_id)
                    return
                # Drop the queue reservation *before* the state goes back to
                # ENABLED. The other order leaves a window where a scheduler
                # sees ENABLED, reserves the plugin, then finds it still in
                # _pending_updates -- the enqueue is dropped and the plugin
                # would sit in RUNNING with nothing left to release it.
                if lock is not None:
                    with self._pending_lock:
                        self._pending_updates.discard(plugin_id)
                if success:
                    with self._plugin_last_update_lock:
                        self.plugin_last_update[plugin_id] = scheduled_time
                    self._note_update_completed(plugin_id)
                    self.state_manager.record_update(plugin_id)
                    self.state_manager.set_state(plugin_id, PluginState.ENABLED)
                    if self.health_tracker:
                        self.health_tracker.record_success(plugin_id)
                else:
                    self._record_update_failure(plugin_id, exc=exc)
            finally:
                if lock is not None:
                    lock.release()

        if lock is None:
            # Synchronous / no-lock path: unchanged behavior.
            try:
                if self.resource_monitor:
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
                _finish(success)
            except Exception as exc:  # pylint: disable=broad-except
                self.logger.exception("Error updating plugin %s: %s", plugin_id, exc)
                _finish(False, exc=exc)
            return

        # Async worker path: the real update() call -- through the resource
        # monitor, if configured -- owns finishing the lock/lifecycle
        # bookkeeping, from whichever thread actually runs it to completion.
        def _target_update() -> None:
            try:
                if self.resource_monitor:
                    self.resource_monitor.monitor_call(plugin_id, plugin_instance.update)
                else:
                    plugin_instance.update()
            except Exception as exc:
                _finish(False, exc=exc)
                raise
            else:
                _finish(True)

        started = time.monotonic()
        try:
            success = self.plugin_executor.execute_update(
                types.SimpleNamespace(update=_target_update), plugin_id)
        except Exception as exc:  # pragma: no cover - defensive; execute_update
            # catches everything internally, but guarantee _finish still
            # runs (releasing the lock) if something unexpected slips through.
            self.logger.exception("Unexpected error dispatching update for %s: %s", plugin_id, exc)
            _finish(False, exc=exc)
            return
        if not success and not finished['done']:
            # The executor stopped waiting but update() is still running: it
            # keeps the lock and the RUNNING state until it returns (then
            # _finish records the outcome). Say so now, rather than leave the
            # plugin silently stuck; record_success on a late return clears it.
            elapsed = time.monotonic() - started
            self._warn_rate_limited(
                "hung-update:" + plugin_id,
                "Plugin %s update() still running after %.1fs; it keeps its "
                "lock until it returns, and is not rescheduled until then",
                plugin_id, elapsed)
            self._record_hang(plugin_id, 'update', elapsed, PluginTimeoutError(
                f"Plugin {plugin_id} update() still running after {elapsed:.1f}s"))

    def run_scheduled_updates_with_changes(self, current_time: Optional[float] = None) -> List[str]:
        """
        Like run_scheduled_updates(), but also reports which plugins have
        fresh data -- the ids whose update() has finished since the last
        call, not necessarily the ones enqueued by this one.

        That distinction is the whole point. This used to snapshot
        plugin_last_update, call run_scheduled_updates(), and diff. But
        run_scheduled_updates() only *enqueues*: the work runs on the
        update worker and the timestamp is stamped there, after this method
        has already returned. The two snapshots were therefore always
        identical and the result was always empty, so Vegas never learned
        that any plugin's data had changed and kept scrolling whatever a
        segment was first built from -- last night's live game still drawn
        as live the next morning. The only path that ever worked was the
        synchronous kill-switch, where update() runs inline.

        Reporting completions instead of enqueues costs a poll's worth of
        latency (the Vegas tick runs every ~4s) and is correct regardless of
        which side of the queue the work lands on.
        """
        self.run_scheduled_updates(current_time)
        return self.drain_completed_updates()

    def _note_update_completed(self, plugin_id: str) -> None:
        """Record that a plugin's update() finished, for the next poll.

        Also tells the update listeners at once, so Vegas live elements are
        redrawn the moment new data lands instead of at the next ~4s poll.
        This runs while the plugin's lock is still held (see _finish), which
        is what makes the listeners' contract strict.
        """
        with self._completed_updates_lock:
            self._completed_updates.add(plugin_id)
        self._fire_update_listeners(plugin_id)

    def add_update_listener(self, listener: Callable[[str], None]) -> None:
        """Call ``listener(plugin_id)`` whenever a plugin's data may have changed.

        That is: its update() completed successfully, or it called
        notify_vegas_data_changed(). The listener runs on the thread that
        noticed -- the update worker, with the plugin's lock still held, or
        the plugin's own thread -- so it must return at once and take no lock
        a plugin could hold: record the id and hand off (a dict store, a
        queue put). An exception from it is logged and does not reach the
        plugin. Adding the same listener twice has no effect.
        """
        # __dict__.get: tests build bare managers with PluginManager.__new__.
        listeners = self.__dict__.get('_update_listeners', ())
        if listener not in listeners:
            self._update_listeners = listeners + (listener,)

    def remove_update_listener(self, listener: Callable[[str], None]) -> None:
        """Stop calling a listener added with add_update_listener()."""
        self._update_listeners = tuple(
            fn for fn in self.__dict__.get('_update_listeners', ()) if fn != listener)

    def notify_data_changed(self, plugin_id: str) -> None:
        """A plugin's data changed outside update(); tell the update listeners.

        BasePlugin.notify_vegas_data_changed() lands here.
        """
        self._fire_update_listeners(plugin_id)

    def _fire_update_listeners(self, plugin_id: str) -> None:
        for listener in self.__dict__.get('_update_listeners', ()):
            try:
                listener(plugin_id)
            except Exception as exc:  # pylint: disable=broad-except
                self._warn_rate_limited(
                    "update-listener",
                    "An update listener failed for plugin %s: %r", plugin_id, exc)

    def drain_completed_updates(self) -> List[str]:
        """Return and clear the plugin ids whose update() has since finished."""
        with self._completed_updates_lock:
            if not self._completed_updates:
                return []
            done = sorted(self._completed_updates)
            self._completed_updates.clear()
            return done
