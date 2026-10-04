"""
Display Controller — top-level orchestration for the LEDMatrix application.

This module owns the main run loop that drives the LED display.  It ties
together every major subsystem:

  - ConfigManager / ConfigService  — loads config.json, hot-reloads on change
  - DisplayManager                 — hardware (or emulator) output interface
  - FontManager                    — TTF/BDF font loading and caching
  - CacheManager                   — multi-tier API response cache
  - PluginManager                  — plugin lifecycle (load, update, display)
  - DisplaySyncManager             — optional leader/follower multi-Pi sync
  - VegasModeCoordinator           — optional continuous Vegas scroll mode

The main loop inside :meth:`DisplayController.run` rotates through enabled
plugin display modes, respecting schedule windows, brightness dim schedules,
on-demand overrides, and live-priority interrupts.

Entry point: :func:`main` — instantiates :class:`DisplayController` and calls
:meth:`~DisplayController.run`.
"""

import time
import os
import inspect
import signal
import json
import threading
import types
from collections import deque
from contextlib import contextmanager
from typing import Dict, Any, List, Optional, Callable, Set, Tuple
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed  # pylint: disable=no-name-in-module
import pytz

from src import display_watchdog
from src.display_arbiter import (
    Arbiter, ArbiterInputs, ArbiterState, Source, WifiNotice, wifi_notice_preempts,
)
from src.display_manager import DisplayManager
from src.config_manager import ConfigManager
from src.config_service import ConfigService
from src.cache_manager import CacheManager
from src.font_manager import FontManager
from src.logging_config import get_logger
from src.exceptions import PluginError
from src.common.frame_timing import HANDOVER_OP
from src.common.sync_manager import DisplaySyncManager, SyncRole
from src.ipc.contract import (
    BrightnessResult,
    BrightnessSetArgs,
    Command as ControlCommand,
    ErrorCode as ControlErrorCode,
    PluginReloadArgs,
    PluginReloadResult,
)
from src.ipc.server import ControlServer, QueuedCommand, StateHub, start_control_server
from src.vegas_mode.render_pipeline import SYNC_SEND_INTERVAL

# Get logger with consistent configuration
logger = get_logger(__name__)

# How often the unchanged current mode is republished for the web UI, which
# treats display_current_state older than 120 s as unknown.
CURRENT_STATE_REFRESH_SECONDS = 30

# While the control socket serves the web interface's state readers
# (StateHub.readers_active), display_current_state is only their fallback:
# it is then rewritten at this interval and on a change of the flags, not on
# every mode change. Below the readers' 120 s max_age, so the fallback copy
# never reads as unknown.
CURRENT_STATE_RELAXED_REFRESH_SECONDS = 60

# How long startup will wait for plugins to fetch their first data before
# showing anything. Each plugin's update blocks for up to the executor's 30s
# timeout and they run one after another, so the uncapped total is the sum of
# every slow plugin: 82 seconds on the worst boot measured, with a blank panel
# throughout. Whatever does not finish in time is picked up by the scheduled
# update tick moments later, with the display already running.
_INITIAL_UPDATE_BUDGET_SECONDS = 20.0

# The least budget worth starting a plugin with. Below this the plugin is
# deferred instead: granting it a floor would let the pass run past its
# deadline, and granting it the true remainder would record a timeout for a
# slot it never had a chance to use.
_MIN_INITIAL_UPDATE_TIMEOUT_SECONDS = 2.0

DEFAULT_DYNAMIC_DURATION_CAP = 180.0


class _PluginReloadJob:
    """A ``plugin.reload`` whose slow half runs off the render thread.

    The render thread takes the plugin out of the rotation and out of the
    plugin manager (DisplayController._start_plugin_reload); then this job,
    on its own thread, tears the old instance down and loads the new one
    (run()). Tearing down waits for the plugin's lock, which a Vegas content
    render of the old instance can hold for seconds. Done on the render
    thread, that wait froze the panel (3.0 s on ledpi). The render thread
    puts the new instance in the rotation once ``done`` is set
    (DisplayController._finish_plugin_reloads).
    """

    def __init__(self, command: QueuedCommand, plugin_id: str,
                 previous_order: Dict[str, int], old_instance: Any) -> None:
        self.command = command
        self.plugin_id = plugin_id
        #: Each mode's place in available_modes before the reload.
        self.previous_order = previous_order
        self.old_instance = old_instance
        self.done = threading.Event()
        self.loaded = False
        #: The old instance's teardown failed, so its modules may still be in
        #: sys.modules and a load now could quietly reuse the old code.
        self.unload_failed = False
        self.error: Optional[Exception] = None
        #: Reloads of the same plugin asked for while this one ran. They
        #: start once it is done, so they load the files as they are then.
        self.followers: List[QueuedCommand] = []

    def run(self, plugin_manager: Any) -> None:
        """Tear down the old instance, then load the plugin again. Never raises."""
        try:
            if self.old_instance is not None:
                if not plugin_manager.unload_detached_plugin(self.plugin_id, self.old_instance):
                    # Loading now could reuse the old plugin_<id> module and
                    # report a reload that never happened (the synchronous
                    # path refused this too: reload_plugin stops when
                    # unload_plugin fails).
                    self.old_instance = None
                    self.unload_failed = True
                    return
            self.old_instance = None
            self.loaded = bool(plugin_manager.reload_plugin(self.plugin_id))
        except Exception as exc:  # pylint: disable=broad-except
            self.error = exc
        finally:
            self.done.set()


# Follower dead reckoning (the follower branch of DisplayController.run()).
# A leader position further off than this fraction of the strip is a cycle
# reset, and is snapped to rather than corrected toward.
_FOLLOWER_SNAP_FRACTION = 0.5
# Strip width assumed, in screens, before the follower has the leader's image.
_FOLLOWER_FALLBACK_STRIP_SCREENS = 4
# Drift beyond this many pixels is corrected by _FOLLOWER_DRIFT_GAIN of the
# error per tick; smaller drift by _FOLLOWER_NEAR_GAIN, so UDP jitter does not
# show as the scroll twitching.
_FOLLOWER_DRIFT_PX = 10
_FOLLOWER_DRIFT_GAIN = 0.20
_FOLLOWER_NEAR_GAIN = 0.05
# Follower render period, and how late a frame may run before the pacing grid
# is restarted rather than caught up.
_FOLLOWER_FRAME_INTERVAL = 1.0 / 60
_FOLLOWER_DEADLINE_SLIP = 0.1

class DisplayController:
    """
    Top-level controller that owns the LED display run loop.

    Responsibilities
    ----------------
    * Initialise and wire together all subsystems at startup.
    * Rotate through plugin display modes in :meth:`run`.
    * Honour schedule windows (active/inactive hours) and dim schedules.
    * Handle on-demand override requests (external callers can pin a
      specific plugin/mode for a fixed duration via the cache bus).
    * Coordinate with a follower Pi when multi-display sync is configured.
    * Delegate all actual content to the plugin system — this class contains
      no display logic of its own.

    There is exactly one instance per process; call :func:`main` to create
    it and start the run loop.
    """

    #: How long the run loop pauses per pass once a whole rotation has had
    #: nothing to show. See _note_empty_pass.
    EMPTY_ROTATION_PAUSE = 1.0

    #: Consecutive passes whose mode had nothing to show, and the rotation
    #: (on-demand or not, and its modes) they were counted in. Class-level so
    #: controllers built without __init__ (tests) have them too.
    _empty_pass_streak = 0
    _empty_pass_rotation: Optional[Tuple[bool, Tuple[str, ...]]] = None

    def __init__(self):
        start_time = time.time()
        logger.info("Starting DisplayController initialization")
        
        # Initialize ConfigManager and wrap with ConfigService for hot-reload
        config_manager = ConfigManager()
        enable_hot_reload = os.environ.get('LEDMATRIX_HOT_RELOAD', 'true').lower() == 'true'
        self.config_service = ConfigService(
            config_manager=config_manager,
            enable_hot_reload=enable_hot_reload
        )
        self.config_manager = config_manager
        self.config = self.config_service.get_config()
        self.cache_manager = CacheManager()
        # The web interface's /api/v3/errors/* read what this publishes.
        from src.error_aggregator import start_error_snapshot_publisher
        start_error_snapshot_publisher(self.cache_manager)
        # Host budgets and the other fetch_service settings, before any plugin
        # fetches; the web UI's fetch statistics read what the publisher
        # writes (src/common/fetch_service.py).
        from src.common.fetch_service import (
            configure_fetch_service, start_fetch_stats_publisher)
        configure_fetch_service(self.config.get('fetch_service'))
        self._fetch_stats_publisher = start_fetch_stats_publisher(self.cache_manager)
        logger.info("Config loaded in %.3f seconds (hot-reload: %s)", time.time() - start_time, enable_hot_reload)
        
        # Validate startup configuration. Errors are logged, not fatal. The
        # plugin checks need the plugin manager and run once it exists.
        try:
            from src.startup_validator import StartupValidator
            validator = StartupValidator(self.config_manager,
                                         cache_manager=self.cache_manager)
            is_valid, errors, warnings = validator.validate_all()
            for warning in warnings:
                logger.warning("Startup validation warning: %s", warning)
            if not is_valid:
                logger.error("Startup validation failed:\n%s",
                             "\n".join(f"  - {e}" for e in errors))
        except Exception as e:
            logger.warning("Startup validation could not be completed: %s", e)

        # Automatic updates need their health-check units, and this is the
        # one root process running project code, so it installs them while
        # automatic updates are on. See src/auto_update_setup.py.
        try:
            from src.auto_update_setup import ensure_update_helper
            ensure_update_helper(self.config)
        except Exception as e:
            logger.warning("Automatic update setup could not be completed: %s", e)
        
        config_time = time.time()
        self.display_manager = DisplayManager(self.config)
        logger.info("DisplayManager initialized in %.3f seconds", time.time() - config_time)

        # Initialize multi-display sync (standalone by default — no-op unless configured)
        sync_cfg = self.config.get("sync", {})
        hw_cfg = self.config.get("display", {}).get("hardware", {})
        self.sync_manager = DisplaySyncManager(
            role_str=sync_cfg.get("role", "standalone"),
            cfg=sync_cfg,
            hw_config=hw_cfg,
            logger=logger,
        )
        # Tell the leader its own physical display width so it can include it in hello_ack
        if self.sync_manager.role == SyncRole.LEADER:
            self.sync_manager.set_leader_width(self.display_manager.width)

        # Follower mode setup
        if self.sync_manager.role == SyncRole.FOLLOWER:
            # Gate update_display() so background plugin threads cannot write to
            # hardware — only our render loop is permitted.
            _real_update = self.display_manager.update_display
            _dm = self.display_manager
            def _follower_gated_update():
                # Allow through when the sync render loop has the token, or when
                # the leader has gone offline and we've fallen back to standalone.
                if getattr(_dm, '_sync_render_allowed', False) or not self.sync_manager.is_follower_active():
                    _real_update()
            self.display_manager.update_display = _follower_gated_update

            # No new_cycle handler is registered: the leader sends its scroll
            # image over TCP at each new cycle and the follower adopts it (see
            # set_on_scroll_image in _initialize_vegas_mode). A local rebuild
            # would overwrite that image with a different, locally built one.

        # Follower render-loop state (see the follower branch of run()).
        self._follower_dr_last_t: Optional[float] = None  # perf_counter of last tick
        self._follower_local_x: Optional[float] = None    # dead-reckoned scroll_x
        # Set on a cycle reset; holds the last frame until the leader's new
        # scroll image arrives.
        self._follower_pending_new_image = False
        self._follower_last_frame = None
        # (image, array) from the leader, handed from the sync TCP thread to
        # the render thread, which adopts it at the start of a follower frame
        # (_adopt_follower_scroll_image). One append / one popleft, each
        # atomic, so the render thread never draws from a half-swapped
        # cached_image / cached_array / total_scroll_width.
        self._follower_incoming_image: deque = deque(maxlen=1)
        self._follower_deadline: Optional[float] = None
        # Leader: time.time() of the last follower frame sent.
        self._last_follower_send = 0.0

        # Initialize Font Manager
        font_time = time.time()
        self.font_manager = FontManager(self.config)
        logger.info("FontManager initialized in %.3f seconds", time.time() - font_time)

        self.force_change = False
        self.available_modes = []
        
        # Initialize Plugin System
        plugin_time = time.time()
        self.plugin_manager = None
        self._plugin_runtime_publisher = None
        self.plugin_modes = {}  # mode -> plugin_instance mapping for plugin-first dispatch
        self.mode_to_plugin_id: Dict[str, str] = {}
        self.plugin_display_modes: Dict[str, List[str]] = {}
        # plugin_display_modes is mutated only by _register_loaded_plugin /
        # _unregister_plugin on the render thread, but the config-watcher
        # thread reads it in _enabled_plugin_not_running. Both mutation sites
        # run during reconcile (rare), so this lock never touches the per-frame
        # path -- the hot-path reads are same-thread as the writes.
        self._plugin_modes_lock = threading.Lock()
        # Guards the consume-and-clear of _pending_plugin_reconcile. Only taken
        # when a reconcile is actually pending or a config change arrives, both
        # rare -- the per-frame path just reads the bool.
        self._reconcile_flag_lock = threading.Lock()
        # Per-plugin config-change callbacks, kept so we can unsubscribe a
        # plugin when it is disabled live.
        self._plugin_config_callbacks: Dict[str, Callable] = {}
        # Set by the config-watcher thread when the enabled-plugin set changes;
        # the main run loop reconciles (loads/unloads) on its own thread so
        # mutating available_modes never races with rendering.
        self._pending_plugin_reconcile = False
        # Set by the config-watcher thread when Vegas is switched on but no
        # coordinator exists (Vegas was off at startup). The render thread
        # creates it in _is_vegas_mode_active(), never the watcher thread.
        self._pending_vegas_init = False
        # Monotonic stamp of the last mailbox disk read; see
        # _poll_on_demand_requests. None means "never polled", so the first
        # call always goes through.
        self._last_on_demand_poll: Optional[float] = None
        # Monotonic stamp of the last _service_pending_changes pass; same
        # "None means never" convention as _last_on_demand_poll.
        self._last_pending_service: Optional[float] = None
        # Monotonic stamp of the last scheduled-update pass; see
        # _tick_plugin_updates_if_due. Same "None means never" convention.
        self._last_plugin_update_tick: Optional[float] = None
        # The control socket (src/ipc), started by run(). None when it is not
        # served (Windows, LEDMATRIX_CONTROL_SOCKET=off, a bind failure);
        # the file mailbox works either way.
        self._control_server = None
        # A brightness set_brightness() refused, so the periodic service pass
        # doesn't retry (and log) the same failure several times a second.
        self._failed_brightness_target: Optional[int] = None
        self.on_demand_active = False
        self.on_demand_mode: Optional[str] = None
        self.on_demand_modes: List[str] = []  # All modes for the on-demand plugin
        self.on_demand_mode_index: int = 0  # Current index in on-demand modes rotation
        self.on_demand_plugin_id: Optional[str] = None
        self.on_demand_duration: Optional[float] = None
        self.on_demand_requested_at: Optional[float] = None
        self.on_demand_expires_at: Optional[float] = None
        self.on_demand_pinned = False
        self.on_demand_request_id: Optional[str] = None
        self.on_demand_status: str = 'idle'
        self.on_demand_last_error: Optional[str] = None
        self.on_demand_last_event: Optional[str] = None
        self.on_demand_schedule_override = False
        # Plugins that are disabled in config and loaded only because an
        # on-demand request named them. The main loop unloads each one once
        # on-demand has moved off it (_release_on_demand_plugins).
        self._on_demand_loaded_plugins: Set[str] = set()
        self.rotation_resume_index: Optional[int] = None
        # Saved rotation position when a live-priority plugin preempts the
        # rotation, so it resumes where it left off (not after the live plugin)
        # once live priority ends.
        self._live_resume_index: Optional[int] = None

        # WiFi status message tracking. The path comes from wifi_manager so
        # reader and writer can't drift: this used to resolve three levels up
        # from src/, one above the repo, and never saw a message.
        from src.wifi_manager import get_wifi_status_path
        self.wifi_status_file = get_wifi_status_path()
        self.wifi_status_active = False
        self.wifi_status_expires_at: Optional[float] = None
        # _check_wifi_status_message throttle state (checked at frame rate,
        # stat'd at most once per second)
        self._wifi_status_check_ts = 0.0
        self._wifi_status_last_result: Optional[Dict[str, Any]] = None

        # Plugin display() signature cache — must be initialised before the plugin
        # loading loop below so the .pop() invalidation at load time is always safe.
        self._plugin_accepts_display_mode: Dict[str, bool] = {}

        try:
            logger.info("Attempting to import plugin system...")
            from src.plugin_system import PluginManager
            logger.info("Plugin system imported successfully")
            
            # Get plugin directory from config, default to plugin-repos for production
            plugin_system_config = self.config.get('plugin_system', {})
            plugins_dir_name = plugin_system_config.get('plugins_directory', 'plugin-repos')
            
            # Resolve plugin directory - handle both absolute and relative paths
            if os.path.isabs(plugins_dir_name):
                plugins_dir = plugins_dir_name
            else:
                # If relative, resolve against the current working directory.
                # That is the project root only because ledmatrix.service
                # sets WorkingDirectory to it; run from anywhere else, a
                # relative path resolves against wherever that is.
                project_root = os.getcwd()
                plugins_dir = os.path.join(project_root, plugins_dir_name)
            
            logger.info("Plugin Manager initialized with plugins directory: %s", plugins_dir)
            
            self.plugin_manager = PluginManager(
                plugins_dir=plugins_dir,
                config_manager=self.config_manager,
                display_manager=self.display_manager,
                cache_manager=self.cache_manager,
                font_manager=self.font_manager
            )

            # The web UI's loaded / state / error_info for each plugin read
            # what this publishes. Started before loading, so the loads that
            # follow are published as they land.
            from src.plugin_system.plugin_runtime import start_plugin_runtime_publisher
            self._plugin_runtime_publisher = start_plugin_runtime_publisher(
                self.cache_manager, self.plugin_manager.state_manager)

            # Activate the plugin health/metrics subsystem. PluginManager leaves
            # health_tracker/resource_monitor as None by default; wiring real
            # instances here turns on the circuit breaker (a repeatedly-failing
            # plugin's update() is skipped after consecutive failures, then
            # retried after a cooldown) and per-plugin execution-time metrics.
            # Both persist to the shared cache so the web UI can surface them.
            # Done before discovery/loading so load-time schema warnings have a
            # tracker to record against.
            try:
                from src.plugin_system.plugin_health import PluginHealthTracker
                from src.plugin_system.resource_monitor import PluginResourceMonitor
                self.plugin_manager.health_tracker = PluginHealthTracker(self.cache_manager)
                self.plugin_manager.resource_monitor = PluginResourceMonitor(self.cache_manager)
                logger.info("Plugin health tracking and resource monitoring enabled")
            except Exception as e:
                logger.warning("Could not enable plugin health/resource monitoring: %s", e)

            # Discover plugins. Before the plugin checks so they can reuse the
            # list: each discover_plugins() call rescans the plugins directory
            # and logs every plugin again.
            discovered_plugins = self.plugin_manager.discover_plugins()
            logger.info("Discovered %d plugin(s)", len(discovered_plugins))

            # Only the plugin checks: validate_all() above has run the rest,
            # and running it again logged every config warning twice.
            try:
                from src.startup_validator import StartupValidator
                validator = StartupValidator(self.config_manager, self.plugin_manager,
                                             cache_manager=self.cache_manager)
                validator._validate_plugins(discovered_plugins=discovered_plugins)
                for warning in validator.warnings:
                    logger.warning("Plugin validation warning: %s", warning)
                if validator.errors:
                    logger.error("Plugin validation failed:\n%s",
                                 "\n".join(f"  - {e}" for e in validator.errors))
            except Exception as e:
                logger.warning("Plugin validation could not be completed: %s", e)

            # Check for on-demand plugin filter from cache
            on_demand_config = self.cache_manager.get('display_on_demand_config', max_age=3600)
            enabled_plugins = self._select_startup_plugins(discovered_plugins, on_demand_config)

            # Count enabled plugins for progress tracking
            enabled_count = len(enabled_plugins)
            logger.info("Loading %d enabled plugin(s) in parallel (max 4 concurrent)...", enabled_count)
            
            # Helper function for parallel loading
            def load_single_plugin(plugin_id):
                """Load a single plugin and return result."""
                plugin_load_start = time.time()
                try:
                    if plugin_id in self._on_demand_loaded_plugins:
                        loaded = self.plugin_manager.load_plugin(plugin_id, force_enabled=True)
                    else:
                        loaded = self.plugin_manager.load_plugin(plugin_id)
                    if loaded:
                        plugin_load_time = time.time() - plugin_load_start
                        return {
                            'success': True,
                            'plugin_id': plugin_id,
                            'load_time': plugin_load_time,
                            'error': None
                        }
                    else:
                        return {
                            'success': False,
                            'plugin_id': plugin_id,
                            'load_time': time.time() - plugin_load_start,
                            'error': 'Load returned False'
                        }
                except Exception as e:
                    return {
                        'success': False,
                        'plugin_id': plugin_id,
                        'load_time': time.time() - plugin_load_start,
                        'error': str(e)
                    }
            
            # Load enabled plugins in parallel with up to 4 concurrent workers
            loaded_count = 0
            with ThreadPoolExecutor(max_workers=4) as executor:
                # Submit all enabled plugins for loading
                future_to_plugin = {
                    executor.submit(load_single_plugin, plugin_id): plugin_id
                    for plugin_id in enabled_plugins
                }
                
                # Process results as they complete
                for future in as_completed(future_to_plugin):
                    result = future.result()
                    loaded_count += 1
                    
                    if result['success']:
                        plugin_id = result['plugin_id']
                        logger.info("Loaded plugin %s in %.3f seconds (%d/%d)", 
                                  plugin_id, result['load_time'], loaded_count, enabled_count)
                        
                        # Register the loaded plugin's modes, config subscription
                        # and dispatch maps (shared with live enable hot-reload).
                        self._register_loaded_plugin(plugin_id)

                        # Show progress
                        progress_pct = int((loaded_count / enabled_count) * 100)
                        elapsed = time.time() - plugin_time
                        logger.info("Progress: %d%% (%d/%d plugins, %.1fs elapsed)", 
                                  progress_pct, loaded_count, enabled_count, elapsed)
                    else:
                        logger.warning("Failed to load plugin %s: %s", 
                                     result['plugin_id'], result['error'])
            
            # Log disabled plugins
            disabled_count = len(discovered_plugins) - enabled_count
            if disabled_count > 0:
                logger.debug("%d plugin(s) disabled in config", disabled_count)

            logger.info("Plugin system initialized in %.3f seconds", time.time() - plugin_time)
            # Parallel loading appends modes in load-completion order, which
            # varies between restarts; apply the user's configured rotation
            # order (no-op when not configured).
            self._apply_plugin_rotation_order()
            logger.info("Total available modes: %d", len(self.available_modes))
            logger.info("Available modes: %s", self.available_modes)
            
            # If on-demand mode was restored from cache, populate on_demand_modes now that plugins are loaded
            if self.on_demand_active and self.on_demand_plugin_id:
                self._populate_on_demand_modes_from_plugin()

        except Exception:  # pylint: disable=broad-except
            logger.exception("Plugin system initialization failed")
            self.plugin_manager = None
            # Its state machine no longer describes what runs; let the last
            # snapshot go stale (readers then say unknown) rather than keep
            # refreshing it.
            if self._plugin_runtime_publisher is not None:
                self._plugin_runtime_publisher.stop(publish_stopped=False)
                self._plugin_runtime_publisher = None

        # The web UI's Fonts tab ("Used by") reads what this publishes.
        from src.font_usage import start_font_usage_publisher
        self._font_usage_publisher = start_font_usage_publisher(
            self.cache_manager, self.font_manager, self.plugin_manager)

        # Display rotation state
        self.current_mode_index = 0
        self.current_display_mode = None
        # Last mode written to the display_current_state cache key, and when.
        self._last_published_mode: Optional[str] = None
        self._last_published_at = 0.0
        # (is_display_active, on_demand_active) as last published: a change
        # to either is republished at once, like a mode change.
        self._last_published_flags: Optional[Tuple[bool, bool]] = None
        self.global_dynamic_config = (
            self.config.get("display", {}).get("dynamic_duration", {}) or {}
        )
        self._active_dynamic_mode: Optional[str] = None
        
        # Memory monitoring
        self._memory_log_interval = 3600.0  # Log memory stats every hour
        self._last_memory_log = time.time()
        self._enable_memory_logging = self.config.get("display", {}).get("memory_logging", False)
        
        # Schedule management
        self.is_display_active = True
        # The previous schedule result, so only transitions log at INFO.
        self._was_display_active = True

        # Config values read on hot paths, cached so they are not re-read
        # from the config dict every frame. _refresh_config_cache() updates
        # them on every hot reload.
        self._normal_brightness: int = (
            self.config.get('display', {}).get('hardware', {}).get('brightness', 90)
        )
        self._scroll_speed: float = self._vegas_scroll_speed(self.config)

        # Brightness state tracking for dim schedule
        self.current_brightness = self._normal_brightness
        self.is_dimmed = False
        self._was_dimmed = False

        # _check_schedule and _check_dim_schedule re-evaluate at most once per
        # clock minute: each stores the (hour, minute) it last evaluated and
        # skips the strptime and comparison work until the minute changes.
        # Reset to None on config change so the next call re-evaluates.
        self._tz = None  # pytz timezone, built lazily by _timezone()
        self._schedule_checked_minute: Optional[tuple] = None
        self._dim_checked_minute: Optional[tuple] = None
        self._cached_target_brightness: int = self._normal_brightness

        # Register controller-level hot-reload callback so cached config values
        # (_normal_brightness, _scroll_speed, _tz, minute-gates) stay in sync
        # when the user saves settings via the web UI.
        self.config_service.subscribe(self._controller_config_change)

        # Publish initial on-demand state
        try:
            self._publish_on_demand_state()
        except (OSError, ValueError, RuntimeError) as err:
            logger.debug("Initial on-demand state publish failed: %s", err, exc_info=True)

        # Initial data update for plugins (ensures data available on first display)
        logger.info("Performing initial plugin data update...")
        update_start = time.time()
        self._update_modules(deadline=update_start + _INITIAL_UPDATE_BUDGET_SECONDS)
        logger.info("Initial plugin update completed in %.3f seconds", time.time() - update_start)

        # Initialize Vegas mode coordinator
        self.vegas_coordinator = None
        self._initialize_vegas_mode()

        logger.info("DisplayController initialization completed in %.3f seconds", time.time() - start_time)

    def _initialize_vegas_mode(self):
        """Initialize Vegas mode coordinator if enabled."""
        vegas_config = self.config.get('display', {}).get('vegas_scroll', {})
        if not vegas_config.get('enabled', False):
            logger.debug("Vegas mode disabled in config")
            return

        if self.plugin_manager is None:
            logger.warning("Vegas mode skipped: plugin_manager is None")
            return

        try:
            from src.vegas_mode import VegasModeCoordinator

            self.vegas_coordinator = VegasModeCoordinator(
                config=self.config,
                display_manager=self.display_manager,
                plugin_manager=self.plugin_manager
            )

            # Set up live priority checker
            self.vegas_coordinator.set_live_priority_checker(self._check_live_priority)

            # Set up interrupt checker for on-demand/wifi status and follower mode
            def _vegas_interrupt():
                return self._check_vegas_interrupt() or self.sync_manager.is_follower_active()
            # Every 10 frames (~80ms at 125 FPS, ~0.4 s at the 24 a Pi 4
            # often manages), or at the next frame when a control socket
            # command is queued: that check is one Event read per frame.
            self.vegas_coordinator.set_interrupt_checker(
                _vegas_interrupt,
                check_interval=10,
                urgent=self._control_command_pending,
            )

            # Run plugin updates inside the Vegas loop so the inter-iteration
            # gap is <1 ms (nothing left for _tick_plugin_updates() to do).
            # Use the Vegas-aware variant so plugins that got fresh data are
            # hot-swapped into the scroll promptly instead of waiting for the
            # next full cycle.
            self.vegas_coordinator.set_update_callback(self._tick_plugin_updates_for_vegas)

            # Wire multi-display sync into Vegas render pipeline
            follower_pos = self.config.get("sync", {}).get("follower_position", "left")
            self.vegas_coordinator.set_sync_manager(self.sync_manager, follower_pos)

            logger.info("Vegas mode coordinator initialized")

            # Follower does NOT build its own initial scroll image — the leader
            # pushes its image via TCP as soon as set_on_follower_connected fires.
            # A local build would create a different (wrong) image that could
            # temporarily replace the leader's correct one.

            # When the leader sends its scroll image (TCP), update our
            # cached_array so both Pis have pixel-identical images. This runs
            # on the sync TCP thread, so it only converts and queues the
            # image; the render thread swaps it in between frames.
            import numpy as _np
            def _on_leader_scroll_image(image):
                vc = self.vegas_coordinator
                if vc and vc.render_pipeline:
                    arr = _np.asarray(image.convert("RGB"), dtype=_np.uint8)
                    self._follower_incoming_image.append((image, arr))
            self.sync_manager.set_on_scroll_image(_on_leader_scroll_image)

            if self.sync_manager.role == SyncRole.LEADER:
                # When a follower first connects, push the current scroll image so
                # the follower doesn't have to wait for the next new_cycle event.
                # Polls until the image is ready (Vegas may still be composing on startup).
                def _on_follower_connected():
                    for _ in range(300):  # up to 30s
                        vc = self.vegas_coordinator
                        if vc and vc.render_pipeline:
                            img = vc.render_pipeline.scroll_helper.cached_image
                            if img is not None:
                                self.sync_manager.send_scroll_image(img)
                                return
                        time.sleep(0.1)
                    logger.warning("Sync: no scroll image available to push to new follower")
                self.sync_manager.set_on_follower_connected(_on_follower_connected)

        except Exception as e:
            logger.error("Failed to initialize Vegas mode: %s", e, exc_info=True)
            self.vegas_coordinator = None

    def _adopt_follower_scroll_image(self, rp) -> None:
        """Swap in the leader's latest scroll image, on the render thread.

        The sync TCP thread used to set cached_image, cached_array and
        total_scroll_width one after another while this thread read them, so
        a frame could slice the new array with the old width. It now queues
        the image and this applies it between frames.
        """
        try:
            image, arr = self._follower_incoming_image.popleft()
        except IndexError:
            return
        if rp is None:
            return
        rp.scroll_helper.cached_image = image
        rp.scroll_helper.cached_array = arr
        rp.scroll_helper.total_scroll_width = image.width
        self._follower_pending_new_image = False
        logger.info(
            "Sync: follower adopted leader scroll image %dx%d",
            image.width, image.height,
        )

    def _is_vegas_mode_active(self) -> bool:
        """Check if Vegas mode should be running."""
        self._apply_pending_vegas_init()
        if not self.vegas_coordinator:
            return False
        # A stopped coordinator never reaches run_frame(), where queued config
        # is applied, so re-enabling Vegas from the web UI would never land.
        self.vegas_coordinator.apply_pending_config_if_idle()
        if not self.vegas_coordinator.is_enabled:
            return False
        if self.on_demand_active:
            return False  # On-demand takes priority
        return True

    def _apply_pending_vegas_init(self) -> None:
        """Create the Vegas coordinator if Vegas was switched on after startup.

        Render thread only: the config watcher just sets _pending_vegas_init.
        Called from _is_vegas_mode_active() and from the main loop before the
        sync-follower branch, which skips _is_vegas_mode_active() while a
        follower is connected but still needs the coordinator to show the
        leader's scroll image.
        """
        if not self.vegas_coordinator and self._pending_vegas_init:
            self._pending_vegas_init = False
            self._initialize_vegas_mode()

    def _check_vegas_interrupt(self) -> bool:
        """
        Check if Vegas should yield control for higher priority events.

        Called periodically by Vegas coordinator to allow responsive
        handling of on-demand requests, wifi status, etc.

        Returns:
            True if Vegas should yield control, False to continue
        """
        # A Vegas iteration runs for up to max_cycle_duration (240s by
        # default) without returning to the main loop, and this is the only
        # code of ours it calls while it does. Without servicing here, an
        # on-demand request was never even read until the iteration ended --
        # on_demand_active below is only set by that read -- and a saved
        # brightness or a schedule boundary waited just as long.
        self._service_pending_changes()

        # Check for pending on-demand request
        if self.on_demand_active:
            return True

        # Scheduled off mid-iteration: hand back so the main loop blanks it.
        if not self.is_display_active:
            return True

        # Check for wifi status that needs display
        if self._check_wifi_status_message():
            return True

        # A plugin reload starts at the top of the loop, outside the
        # iteration; the iteration then resumes while it loads.
        if self._plugin_reload_pending:
            return True

        return False

    def _timezone(self):
        """The configured timezone, built once and cached until config changes."""
        if self._tz is None:
            timezone_str = self.config.get('timezone', 'UTC')
            try:
                self._tz = pytz.timezone(timezone_str)
            except pytz.UnknownTimeZoneError:
                logger.warning("Unknown timezone '%s', using UTC", timezone_str)
                self._tz = pytz.UTC
        return self._tz

    @staticmethod
    def _in_window(start, end, now) -> bool:
        """Whether ``now`` is within [start, end), a window that may span midnight.

        Half-open: on from the start minute, off at exactly the end minute.
        A closed end made the end minute count as inside, and because ``now``
        carries seconds, only a check at hh:mm:00.000 saw it that way -- so
        whether the panel went off at the start or the end of that minute
        depended on when the minute's one check ran. ``start == end`` is an
        empty window, as it effectively was before.
        """
        if start <= end:
            return start <= now < end
        return now >= start or now < end

    def _check_schedule(self):
        """Check if display should be active based on schedule."""
        schedule_config = self.config.get('schedule', {})

        # No schedule configured: always active.
        if not schedule_config:
            self.is_display_active = True
            self._was_display_active = True
            return

        # A schedule without an 'enabled' key counts as enabled.
        if 'enabled' in schedule_config and not schedule_config.get('enabled', True):
            self.is_display_active = True
            self._was_display_active = True
            logger.debug("Schedule is disabled - display always active")
            return

        current_time = datetime.now(self._timezone())
        # Gate: schedule state can only change on a minute boundary, so skip
        # all the strptime / comparison work if we already evaluated this minute.
        current_minute_key = (current_time.hour, current_time.minute)
        if current_minute_key == self._schedule_checked_minute:
            return
        self._schedule_checked_minute = current_minute_key

        current_day = current_time.strftime('%A').lower()  # e.g. 'monday'
        current_time_only = current_time.time()

        # Check if per-day schedule is configured
        days_config = schedule_config.get('days')

        # Determine which schedule to use. Respect an explicit 'mode' field
        # (like the dim schedule does) so a stray/legacy 'days' dict left over
        # from config migration or a prior per-day setup can't silently
        # override a user's Global schedule selection.
        mode = schedule_config.get('mode')
        mode_normalized = mode.replace('_', '-') if mode else None

        use_per_day = False
        if mode_normalized == 'global':
            use_per_day = False
        elif mode_normalized == 'per-day':
            use_per_day = bool(days_config and current_day in days_config)
        elif days_config:
            # No explicit mode recorded (legacy config) - fall back to
            # inferring from presence of a 'days' dict for the current day.
            if current_day in days_config:
                use_per_day = True
            else:
                logger.debug("Per-day schedule exists but %s not configured, using global schedule", current_day)

        if use_per_day:
            day_config = days_config[current_day]

            if not day_config.get('enabled', True):
                was_active = self._was_display_active
                self.is_display_active = False
                if was_active:
                    logger.info("Schedule activated: Display is now INACTIVE (%s is disabled in schedule). Display will be blanked.", current_day)
                else:
                    logger.debug("Display inactive - %s is disabled in schedule", current_day)
                self._was_display_active = self.is_display_active
                return

            start_time_str = day_config.get('start_time', '07:00')
            end_time_str = day_config.get('end_time', '23:00')
            schedule_type = f"per-day ({current_day})"
        else:
            start_time_str = schedule_config.get('start_time', '07:00')
            end_time_str = schedule_config.get('end_time', '23:00')
            schedule_type = "global"

        try:
            start_time = datetime.strptime(start_time_str, '%H:%M').time()
            end_time = datetime.strptime(end_time_str, '%H:%M').time()
            self.is_display_active = self._in_window(start_time, end_time, current_time_only)

            was_active = self._was_display_active
            if not self.is_display_active:
                if was_active:
                    logger.info("Schedule activated: Display is now INACTIVE (outside %s schedule window %s - %s). Display will be blanked.",
                               schedule_type, start_time_str, end_time_str)
                else:
                    logger.debug("Display inactive - outside %s schedule window (%s - %s)",
                               schedule_type, start_time_str, end_time_str)
            else:
                if not was_active:
                    logger.info("Schedule activated: Display is now ACTIVE (within %s schedule window %s - %s)",
                               schedule_type, start_time_str, end_time_str)
                else:
                    logger.debug("Display active - within %s schedule window (%s - %s)",
                               schedule_type, start_time_str, end_time_str)

            self._was_display_active = self.is_display_active

        except ValueError as e:
            logger.warning("Invalid schedule format for %s schedule: %s (start: %s, end: %s). Defaulting to active.",
                         schedule_type, e, start_time_str, end_time_str)
            self.is_display_active = True
            self._was_display_active = True

    def _check_dim_schedule(self) -> int:
        """
        Check if display should be dimmed based on dim schedule.

        Returns:
            Target brightness level (dim_brightness if in dim period,
            normal brightness otherwise)
        """
        normal_brightness = self._normal_brightness

        # If display is OFF via schedule, don't process dim schedule
        if not self.is_display_active:
            self.is_dimmed = False
            return normal_brightness

        dim_config = self.config.get('dim_schedule', {})

        # If dim schedule doesn't exist or is disabled, use normal brightness
        if not dim_config or not dim_config.get('enabled', False):
            self.is_dimmed = False
            return normal_brightness

        # Re-evaluated at most once per clock minute, like _check_schedule.
        current_time = datetime.now(self._timezone())
        current_minute_key = (current_time.hour, current_time.minute)
        if current_minute_key == self._dim_checked_minute:
            return self._cached_target_brightness
        self._dim_checked_minute = current_minute_key

        current_day = current_time.strftime('%A').lower()
        current_time_only = current_time.time()

        # Determine if using per-day or global dim schedule
        # Normalize mode to handle both "per-day" and "per_day" variants
        mode = dim_config.get('mode', 'global')
        mode_normalized = mode.replace('_', '-') if mode else 'global'
        days_config = dim_config.get('days')
        use_per_day = mode_normalized == 'per-day' and days_config and current_day in days_config

        if use_per_day:
            day_config = days_config[current_day]
            if not day_config.get('enabled', True):
                # Past the minute gate, so the cache must say the same thing:
                # returning here without it left the previous minute's dim
                # value to be served for the rest of this one, and the
                # brightness flipped between dim and normal every minute.
                if self._was_dimmed:
                    logger.info(f"Dim schedule deactivated: brightness restored to {normal_brightness}%")
                self.is_dimmed = False
                self._was_dimmed = False
                self._cached_target_brightness = normal_brightness  # persist for minute-gate
                return normal_brightness
            start_time_str = day_config.get('start_time', '20:00')
            end_time_str = day_config.get('end_time', '07:00')
        else:
            start_time_str = dim_config.get('start_time', '20:00')
            end_time_str = dim_config.get('end_time', '07:00')

        try:
            start_time = datetime.strptime(start_time_str, '%H:%M').time()
            end_time = datetime.strptime(end_time_str, '%H:%M').time()

            if self._in_window(start_time, end_time, current_time_only):
                self.is_dimmed = True
                target_brightness = dim_config.get('dim_brightness', 30)
            else:
                self.is_dimmed = False
                target_brightness = normal_brightness

            # Log state changes
            if self.is_dimmed and not self._was_dimmed:
                logger.info(f"Dim schedule activated: brightness set to {target_brightness}%")
            elif not self.is_dimmed and self._was_dimmed:
                logger.info(f"Dim schedule deactivated: brightness restored to {target_brightness}%")

            self._was_dimmed = self.is_dimmed
            self._cached_target_brightness = target_brightness  # persist for minute-gate
            return target_brightness

        except ValueError as e:
            logger.warning("Invalid dim schedule time format: %s", e)
            self._cached_target_brightness = normal_brightness  # persist for minute-gate
            return normal_brightness

    def _update_modules(self, deadline: Optional[float] = None):
        """Update all plugin modules.

        Args:
            deadline: Wall-clock time after which remaining plugins are left
                for the scheduled update tick instead of being waited on. Each
                update blocks this thread for up to the executor's timeout, and
                they run one after another, so without a bound the total is the
                sum of every slow plugin on the system. Measured at startup on
                a live rig: 82 seconds, 55 and 26 on the two boots before -- all
                of it with nothing on the panel.
        """
        if not self.plugin_manager:
            return

        # Update all loaded plugins
        plugins_dict = self.plugin_manager.plugins
        deferred = []
        for plugin_id, plugin_instance in plugins_dict.items():
            update_timeout = None
            if deadline is not None:
                update_timeout = deadline - time.time()
                if update_timeout < _MIN_INITIAL_UPDATE_TIMEOUT_SECONDS:
                    # Too little left to be worth starting. Deferring rather
                    # than granting a floor keeps the budget a real ceiling --
                    # clamping up to a minimum let a plugin that began with a
                    # sliver left run on past the deadline -- and a plugin
                    # handed a slot it cannot use would just be recorded as
                    # having timed out.
                    #
                    # Nothing is lost either way: a plugin that has never
                    # updated is immediately due, so run_scheduled_updates()
                    # picks it up within seconds, with the display already
                    # running.
                    deferred.append(plugin_id)
                    continue
            health_tracker = self.plugin_manager.health_tracker
            if health_tracker is not None and health_tracker.should_skip_plugin(plugin_id):
                logger.debug("Skipping update for plugin %s due to circuit breaker", plugin_id)
                continue

            # The remaining budget is the timeout, so the pass cannot run past
            # its deadline. Bounding the loop alone did not do it: the last
            # plugin to start could still block for the executor's full 30s,
            # which turned a 20s budget into a 31.8s pass on the rig.
            success = self.plugin_manager.plugin_executor.execute_update(
                plugin_instance, plugin_id, timeout=update_timeout)
            if success:
                self.plugin_manager.plugin_last_update[plugin_id] = time.time()

        if deferred:
            logger.info(
                "Initial update budget spent; %d plugin(s) left to the update "
                "tick so the display can start: %s",
                len(deferred), ", ".join(deferred))

    def _tick_plugin_updates_for_vegas(self) -> None:
        """Run scheduled plugin updates and tell Vegas mode which plugins
        actually got fresh data, so it can refresh them in the scroll
        without waiting for a full cycle to complete.

        This is the Vegas coordinator's update callback, used instead of the
        plain _tick_plugin_updates() so that a live score change reaches the
        ticker within seconds rather than at the next cycle boundary (which,
        depending on min/max_cycle_duration, can be minutes away).

        PluginManager.run_scheduled_updates_with_changes() takes the
        before/after plugin_last_update snapshot, so the snapshot, update
        pass and diff are locked against the main render loop's own update
        tick running concurrently on the other thread.
        """
        if not self.plugin_manager:
            return

        updated = self.plugin_manager.run_scheduled_updates_with_changes()

        vc = self.vegas_coordinator
        if vc is None:
            return

        if updated:
            logger.info("Vegas update tick: %d plugin(s) updated: %s", len(updated), updated)
            for plugin_id in updated:
                try:
                    vc.mark_plugin_updated(plugin_id)
                except Exception:  # pylint: disable=broad-except
                    logger.exception("Error marking plugin %s updated for Vegas", plugin_id)

    #: Shortest gap between scheduled-update passes from the frame loops and
    #: the dwell sleep. The pass (PluginManager.run_scheduled_updates) copies
    #: the plugin dict and takes several locks per plugin to find, almost
    #: always, that nothing is due: about 95 us with 20 plugins on a Pi, or
    #: 1.2% of the render thread at 125 frames a second. No interval is
    #: shorter than PluginManager.MIN_DYNAMIC_UPDATE_INTERVAL (5 s), and the
    #: 1 Hz frame loop already ticks once a second, so a quarter second late
    #: is not noticed.
    PLUGIN_UPDATE_TICK_INTERVAL = 0.25

    #: Class-level default for controllers built without __init__ (tests).
    _last_plugin_update_tick: Optional[float] = None

    def _tick_plugin_updates_if_due(self) -> None:
        """_tick_plugin_updates, at most once per PLUGIN_UPDATE_TICK_INTERVAL.

        For the per-frame callers. The top of each loop pass calls
        _tick_plugin_updates itself, unthrottled, because that is where a
        plugin just loaded, reloaded or enabled for on-demand gets its first
        update, and it must not wait out the floor.
        """
        last = self._last_plugin_update_tick
        if last is not None and time.monotonic() - last < self.PLUGIN_UPDATE_TICK_INTERVAL:
            return
        self._tick_plugin_updates()

    def _tick_plugin_updates(self):
        """Run any plugin updates that are due."""
        if not self.plugin_manager:
            return
        self._last_plugin_update_tick = time.monotonic()
        try:
            self.plugin_manager.run_scheduled_updates()
        except Exception:  # pylint: disable=broad-except
            logger.exception("Error running scheduled plugin updates")

    @contextmanager
    def _display_lock_or_skip(self, plugin_id):
        """Try-lock guard keeping a plugin's display() off its in-flight update().

        Yields True when display may run (lock held, released on exit) or
        when there is no plugin manager or plugin id to lock on. Yields False
        when the plugin's update() is currently executing on the background
        worker — the caller should treat the frame as displayed (the panel
        holds the last pushed frame) rather than as a plugin failure, so a
        mid-update skip never advances the rotation.
        """
        pm = self.plugin_manager
        if not pm or not plugin_id:
            yield True
            return
        lock = pm.get_plugin_lock(plugin_id)
        if not lock.acquire(blocking=False):
            yield False
            return
        try:
            yield True
        finally:
            lock.release()

    def _display_once(self, plugin, mode: str, accepts_display_mode: bool,
                      force_clear: bool = False):
        """Call ``plugin.display()`` directly for one frame of a render loop.

        Frames after a screen's first dispatch come through here rather than
        PluginExecutor: the executor spawns a thread per call, which at the
        high-FPS loop's frame rate would cost more than the advisory timeout
        it buys -- and that timeout cannot cancel a hung plugin anyway (see
        execute_with_timeout). The first dispatch in run() still goes through
        the executor, so failures there are caught and recorded.

        Args:
            plugin: The plugin to draw.
            mode: The display mode, passed on when the plugin accepts
                ``display_mode`` so plugins with several modes stay on it.
            accepts_display_mode: Whether display() takes ``display_mode``.
            force_clear: Passed through to display().

        Each call is timed (two monotonic reads) and handed to
        PluginManager.note_display_duration, which logs and records slow
        calls and counts one that ran past the executor's timeout as a hang.

        Returns:
            display()'s result, or True when the frame was skipped because
            the plugin's update() holds its lock (the panel keeps the last
            frame; that is not a failure).
        """
        # Every frame of both per-screen render loops comes through here.
        display_watchdog.watchdog.beat()
        plugin_id = getattr(plugin, 'plugin_id', None)
        with self._display_lock_or_skip(plugin_id) as can_display:
            if not can_display:
                return True
            started = time.monotonic()
            try:
                if accepts_display_mode:
                    return plugin.display(display_mode=mode, force_clear=force_clear)
                return plugin.display(force_clear=force_clear)
            finally:
                note = getattr(self.plugin_manager, 'note_display_duration', None)
                if note is not None and plugin_id:
                    note(plugin_id, time.monotonic() - started)
                # A screen that drew once and holds makes no more
                # update_display() calls, so a frame the preview throttle
                # skipped would otherwise never reach the snapshot.
                write_owed = getattr(getattr(self, 'display_manager', None),
                                     'write_owed_snapshot', None)
                if write_owed is not None:
                    write_owed()

    def _health_tracker(self):
        """The plugin circuit breaker, or None when it is not enabled."""
        if self.plugin_manager is None:
            return None
        return self.plugin_manager.health_tracker

    def _follower_sign(self) -> int:
        """-1 when the follower panel sits left of the leader, +1 when right.

        The follower shows the strip at the leader's position plus
        ``sign * width``: content that has already scrolled off the leader's
        left edge when it is on the left (the default).
        """
        position = self.config.get("sync", {}).get("follower_position", "left")
        return -1 if position == "left" else 1

    def _send_follower_frame(self, plugin_instance) -> None:
        """Leader: generate and send the follower's portion of the current frame.

        The follower is physically to the LEFT of the leader in a right-to-left
        scrolling ticker, so it shows content at scroll_position - display_width
        (content that already scrolled off the leader's left edge).
        Set sync.follower_position = "right" in config to invert this.
        """
        if not (self.sync_manager and self.sync_manager.role == SyncRole.LEADER):
            return
        now = time.time()
        if now - self._last_follower_send < SYNC_SEND_INTERVAL:
            return
        self._last_follower_send = now

        follower_frame = None
        offset = self._follower_sign() * self.display_manager.width

        # 1. Explicit hook — plugin opted in with get_offset_frame()
        try:
            follower_frame = plugin_instance.get_offset_frame(offset)
        except AttributeError:
            pass  # Most plugins don't implement get_offset_frame; that's expected

        # 2. Auto-detect — plugin has a scroll_helper (standard pattern for all
        #    scroll plugins). Works with zero plugin code changes.
        if follower_frame is None:
            try:
                scroll_h = getattr(plugin_instance, 'scroll_helper', None)
                if scroll_h is not None:
                    follower_frame = scroll_h.get_portion_at(scroll_h.scroll_position + offset)
            except Exception:  # nosec B110 - scroll_helper.get_portion_at is optional; skip on error
                pass

        # 3. Mirror fallback — static plugins (clock, weather) show same frame
        if follower_frame is None:
            follower_frame = self.display_manager.image

        if follower_frame is not None:
            self.sync_manager.send_frame(follower_frame)

    def _sleep_with_plugin_updates(self, duration: float, tick_interval: float = 1.0):
        """Sleep while continuing to service plugin update schedules.

        Also services pending changes (see _service_pending_changes), and
        returns early when one of them changes what the panel should show --
        an on-demand start or stop, the display schedule turning the panel
        on or off, a WiFi notice arriving, a live game taking over
        (_check_live_takeover), or a plugin reload waiting for the top of
        the loop -- so the caller can act on it instead of finishing a dwell
        that could be a minute long (sixty seconds while scheduled off).

        The waits between checks wake for a control socket command, so one
        is applied within milliseconds rather than at the next 0.25 s tick.
        """
        if duration <= 0 or self._plugin_reload_pending:
            return

        end_time = time.time() + duration
        tick_interval = max(0.001, min(tick_interval, self.PENDING_CHANGES_INTERVAL))
        mode = self.current_display_mode
        display_active = self.is_display_active
        on_demand = self.on_demand_active
        # Edge-triggered: the notice's own dwell starts with it pending and
        # must not cut itself short; any other dwell ends when one arrives.
        wifi_pending = self._wifi_notice_pending()

        while True:
            remaining = end_time - time.time()
            if remaining <= 0:
                break

            sleep_time = min(tick_interval, remaining)
            # Woken early by a control socket command, which
            # _service_pending_changes then applies without its floor.
            self._wait_for_control(sleep_time)
            # A dwell can be a minute long (sixty seconds while scheduled
            # off); the watchdog must hear from this thread throughout.
            display_watchdog.watchdog.beat()
            self._tick_plugin_updates_if_due()
            self._service_pending_changes()
            self._check_live_takeover()
            if (self.current_display_mode != mode
                    or self.is_display_active != display_active
                    or self.on_demand_active != on_demand
                    or (not wifi_pending and self.is_display_active
                        and self._wifi_notice_pending())
                    or self._plugin_reload_pending):
                break

    def _note_empty_pass(self) -> None:
        """Record a pass whose mode had nothing to show; pause once a whole
        rotation has been empty.

        A mode with no content rotates to the next one at once, with no dwell.
        When every mode is empty -- say, only a sports plugin enabled in its
        off-season -- the loop went round with no sleep at all: 100% of a core,
        a plugin-executor thread per pass and several log lines each time,
        indefinitely. After one full rotation of empty passes, each further
        one pauses EMPTY_ROTATION_PAUSE seconds. Live content is still picked
        up within that second (the live-priority check runs at the top of
        every pass), the pause services plugin updates, and it returns early
        on an on-demand request or a schedule change. The streak resets as
        soon as any mode shows something, and when the rotation itself
        changes (on-demand starting or stopping, a plugin enabled or
        disabled): a streak counted in one rotation says nothing about the
        modes of another, which haven't been tried yet.
        """
        modes = self.on_demand_modes if self.on_demand_active else self.available_modes
        rotation_key = (bool(self.on_demand_active), tuple(modes))
        if rotation_key != self._empty_pass_rotation:
            self._empty_pass_rotation = rotation_key
            self._empty_pass_streak = 0
        self._empty_pass_streak += 1
        rotation = max(1, len(modes))
        if self._empty_pass_streak < rotation:
            return
        if self._empty_pass_streak == rotation:
            logger.info("No mode has anything to show; checking one mode every %.0fs "
                        "until one does", self.EMPTY_ROTATION_PAUSE)
        self._sleep_with_plugin_updates(self.EMPTY_ROTATION_PAUSE)

    def _get_display_duration(self, mode_key):
        """Seconds to show a mode: the Rotation & Durations page's value for it
        (display.display_durations), else the plugin's own duration.

        The saved value has to win. Every plugin inherits
        get_display_duration(), so checking the plugin first meant the page's
        values were never read.
        """
        display_durations = self.config.get('display', {}).get('display_durations', {}) or {}
        override = display_durations.get(mode_key)
        if isinstance(override, (int, float)) and not isinstance(override, bool) and override > 0:
            return float(override)

        plugin_instance = self.plugin_modes.get(mode_key)
        if plugin_instance is not None and hasattr(plugin_instance, 'get_display_duration'):
            return plugin_instance.get_display_duration()
        return 30

    def _get_global_dynamic_cap(self) -> Optional[float]:
        """Return global fallback dynamic duration cap."""
        cap_value = self.global_dynamic_config.get("max_duration_seconds")
        if cap_value is None:
            return DEFAULT_DYNAMIC_DURATION_CAP
        try:
            cap = float(cap_value)
            if cap <= 0:
                return None
            return cap
        except (TypeError, ValueError):
            logger.warning("Invalid global dynamic duration cap: %s", cap_value)
            return None

    def _plugin_supports_dynamic(self, plugin_instance) -> bool:
        """Safely determine whether plugin supports dynamic duration."""
        supports_fn = getattr(plugin_instance, "supports_dynamic_duration", None)
        if not callable(supports_fn):
            return False
        try:
            return bool(supports_fn())
        except Exception as exc:  # pylint: disable=broad-except
            plugin_id = getattr(plugin_instance, "plugin_id", "unknown")
            logger.warning(
                "Failed to query dynamic duration support for %s: %s", plugin_id, exc
            )
            return False

    def _plugin_dynamic_cap(self, plugin_instance) -> Optional[float]:
        """Fetch plugin-specific dynamic duration cap."""
        cap_fn = getattr(plugin_instance, "get_dynamic_duration_cap", None)
        if not callable(cap_fn):
            return None
        try:
            return cap_fn()
        except Exception as exc:  # pylint: disable=broad-except
            plugin_id = getattr(plugin_instance, "plugin_id", "unknown")
            logger.warning(
                "Failed to read dynamic duration cap for %s: %s", plugin_id, exc
            )
            return None

    def _plugin_cycle_duration(self, plugin_instance, display_mode: str = None) -> Optional[float]:
        """Fetch plugin-calculated cycle duration for a specific mode.
        
        This allows plugins to calculate the total time needed to show all content
        for a mode (e.g., number_of_games × per_game_duration).
        
        Args:
            plugin_instance: The plugin to query
            display_mode: The mode to get duration for (e.g., 'football_recent')
        
        Returns:
            Calculated duration in seconds, or None if not available
        """
        duration_fn = getattr(plugin_instance, "get_cycle_duration", None)
        if not callable(duration_fn):
            return None
        try:
            return duration_fn(display_mode=display_mode)
        except Exception as exc:  # pylint: disable=broad-except
            plugin_id = getattr(plugin_instance, "plugin_id", "unknown")
            logger.debug(
                "Failed to read cycle duration for %s mode %s: %s", 
                plugin_id, 
                display_mode,
                exc
            )
            return None

    def _plugin_reset_cycle(self, plugin_instance) -> None:
        """Reset plugin cycle tracking if supported."""
        reset_fn = getattr(plugin_instance, "reset_cycle_state", None)
        if not callable(reset_fn):
            return
        try:
            reset_fn()
        except Exception as exc:  # pylint: disable=broad-except
            plugin_id = getattr(plugin_instance, "plugin_id", "unknown")
            logger.warning("Failed to reset cycle state for %s: %s", plugin_id, exc)

    def _plugin_cycle_complete(self, plugin_instance) -> bool:
        """Determine if plugin reports cycle completion."""
        complete_fn = getattr(plugin_instance, "is_cycle_complete", None)
        if not callable(complete_fn):
            return True
        try:
            return bool(complete_fn())
        except Exception as exc:  # pylint: disable=broad-except
            plugin_id = getattr(plugin_instance, "plugin_id", "unknown")
            logger.warning(
                "Failed to read cycle completion for %s: %s (keeping display active)",
                plugin_id,
                exc,
                exc_info=True,
            )
            # Return False on error to keep displaying rather than cutting short
            # This is safer - better to show content longer than to exit prematurely
            return False

    def _get_on_demand_remaining(self) -> Optional[float]:
        """Calculate remaining time for an active on-demand session."""
        # Read once: the control socket's status command calls this from
        # its own thread, while the render thread may be clearing the field.
        expires_at = self.on_demand_expires_at
        if not self.on_demand_active or expires_at is None:
            return None
        return max(0.0, expires_at - time.time())

    #: The control socket's state stream (src/ipc/server.StateHub), while the
    #: socket is served. Class-level default for controllers built without
    #: __init__ (tests) and for a display with no socket.
    _state_hub: Optional[StateHub] = None

    def _current_mode_state(self) -> Dict[str, Any]:
        """What display_current_state and the socket's ``display`` section hold."""
        return {
            'mode': self.current_display_mode,
            'plugin_id': self.mode_to_plugin_id.get(self.current_display_mode),
            'mode_index': self.current_mode_index,
            'total_modes': len(self.available_modes),
            'on_demand_active': self.on_demand_active,
            'is_display_active': self.is_display_active,
            'last_updated': time.time(),
        }

    def _push_live_state(self, display_state: Optional[Dict[str, Any]] = None) -> None:
        """Hand the current mode and the brightness to the control socket's
        state stream. In memory, no disk: the hub only bumps its version (and
        wakes subscribers) when something other than ``last_updated`` changed.

        Called on every pass of the publish points below, so
        ``display.last_updated`` doubles as the render thread's proof of life
        for the socket's readers, as the cache key's max_age does today.
        """
        hub = self._state_hub
        if hub is None:
            return
        try:
            hub.publish('display', display_state or self._current_mode_state(),
                        volatile=('last_updated',))
            hub.publish('brightness', {
                'brightness': getattr(self, '_normal_brightness', None),
                'panel_brightness': getattr(self, 'current_brightness', None),
                'dimmed': bool(getattr(self, 'is_dimmed', False)),
            })
        except Exception as err:  # pylint: disable=broad-except
            logger.debug("Could not publish the display state to the control socket: %s",
                         err, exc_info=True)

    def _state_readers_on_socket(self) -> bool:
        """Is the control socket serving the web interface's state readers?"""
        hub = self._state_hub
        try:
            return bool(hub is not None and hub.readers_active())
        except Exception:  # pylint: disable=broad-except
            return False

    def _publish_current_mode_state(self) -> None:
        """Publish the currently active display mode/plugin to cache for the web UI."""
        try:
            state = self._current_mode_state()
            self._push_live_state(state)
            self.cache_manager.set('display_current_state', state)
            self._last_published_mode = self.current_display_mode
            self._last_published_flags = self._current_state_flags()
            self._last_published_at = time.monotonic()
        except (OSError, RuntimeError, ValueError, TypeError) as err:
            logger.error("Failed to publish current display state: %s", err, exc_info=True)

    def _current_state_flags(self) -> Tuple[bool, bool]:
        """The published flags besides the mode that a reader acts on."""
        return (bool(self.is_display_active), bool(self.on_demand_active))

    def _publish_current_mode_state_if_changed(self) -> None:
        """Publish the current mode state when it changed, or when the last
        publish is older than CURRENT_STATE_REFRESH_SECONDS.

        A change is the mode, or ``is_display_active`` / ``on_demand_active``:
        waking from scheduled-off, or an on-demand session starting or ending,
        often leaves the mode name as it was, and the web UI would otherwise
        show the old flag until the next refresh.

        The web UI reads this key with a max_age (api_v3/display.py), so a mode
        that stays on screen longer than that -- a live game under live
        priority, a single enabled plugin -- has to be republished or the UI
        reports it as unknown. Otherwise this writes only on a change, not on
        every render tick.

        While the control socket serves the web interface's state readers,
        the socket's in-memory copy is updated on every call and the cache
        key is only their fallback: a mode change alone is then written at
        the relaxed refresh (CURRENT_STATE_RELAXED_REFRESH_SECONDS), still
        inside the readers' max_age. The flags are still written at once.
        When the socket stops serving them, the next call writes a changed
        mode again.
        """
        relaxed = self._state_readers_on_socket()
        refresh = CURRENT_STATE_RELAXED_REFRESH_SECONDS if relaxed else CURRENT_STATE_REFRESH_SECONDS
        if ((not relaxed and self.current_display_mode != self._last_published_mode)
                or self._current_state_flags() != getattr(self, '_last_published_flags', None)
                or time.monotonic() - self._last_published_at >= refresh):
            self._publish_current_mode_state()   # pushes to the socket as well
        else:
            self._push_live_state()

    def _on_demand_state(self) -> Dict[str, Any]:
        """The on-demand state as published to the cache and the control socket."""
        return {
            'active': self.on_demand_active,
            'mode': self.on_demand_mode,
            'plugin_id': self.on_demand_plugin_id,
            'requested_at': self.on_demand_requested_at,
            'expires_at': self.on_demand_expires_at,
            'duration': self.on_demand_duration,
            'pinned': self.on_demand_pinned,
            'status': self.on_demand_status,
            'error': self.on_demand_last_error,
            'last_event': self.on_demand_last_event,
            'remaining': self._get_on_demand_remaining(),
            'last_updated': time.time()
        }

    def _publish_on_demand_state(self) -> None:
        """Publish current on-demand state to cache for external consumers."""
        try:
            state = self._on_demand_state()
            hub = self._state_hub
            if hub is not None:
                # In memory, first: a subscriber hears the outcome of an
                # on-demand command even if the cache write below fails.
                hub.publish('on_demand', state, volatile=('last_updated', 'remaining'))
            self.cache_manager.set('display_on_demand_state', state)
        except (OSError, RuntimeError, ValueError, TypeError) as err:
            logger.error("Failed to publish on-demand state: %s", err, exc_info=True)

    def _set_on_demand_error(self, message: str) -> None:
        """Set on-demand state to error and publish."""
        self._reset_on_demand_fields()
        self.on_demand_status = 'error'
        self.on_demand_last_error = message
        self.on_demand_last_event = None
        self.rotation_resume_index = None
        self._publish_on_demand_state()

    def _reset_on_demand_fields(self) -> None:
        """Clear the on-demand session: no plugin, no modes, no expiry.

        Leaves status, last error/event and rotation_resume_index to the
        caller, which is what differs between ending a session and failing
        to start one.
        """
        self.on_demand_active = False
        self.on_demand_mode = None
        self.on_demand_modes = []
        self.on_demand_mode_index = 0
        self.on_demand_plugin_id = None
        self.on_demand_duration = None
        self.on_demand_requested_at = None
        self.on_demand_expires_at = None
        self.on_demand_pinned = False
        self.on_demand_schedule_override = False
        # While the session ran, _evaluate_schedule may have forced
        # is_display_active on over a scheduled-off answer. Drop the minute
        # gate so the next _check_schedule recomputes it; otherwise the panel
        # stayed on until the next clock minute.
        self._schedule_checked_minute = None

    def _advance_on_demand(self) -> None:
        """Move an active on-demand session to its next mode and publish it.

        The caller checks that on_demand_modes is non-empty.
        """
        self.on_demand_mode_index = (self.on_demand_mode_index + 1) % len(self.on_demand_modes)
        next_mode = self.on_demand_modes[self.on_demand_mode_index]
        logger.info("Rotating to next on-demand mode: %s (index %d/%d)",
                    next_mode, self.on_demand_mode_index, len(self.on_demand_modes))
        self.current_display_mode = next_mode
        self.force_change = True
        self._publish_on_demand_state()

    #: Shortest gap between mailbox disk reads. This is called after every
    #: frame -- about 125 times a second on a scrolling mode -- and the read
    #: below is deliberately uncached, so without a floor it was 125 disk reads
    #: per second to find nothing. An on-demand request comes from a person
    #: clicking in the web UI, so a quarter second of latency is not
    #: perceptible, and it cuts the read rate by 30x.
    ON_DEMAND_POLL_INTERVAL = 0.25

    #: Shortest gap between _service_pending_changes passes. The same floor as
    #: the mailbox poll, since that read is the only real cost in the pass:
    #: the schedule checks are gated to once per clock minute and the rest is
    #: attribute compares. Callers run at frame rate, so between passes the
    #: whole cost is one monotonic-clock compare.
    PENDING_CHANGES_INTERVAL = ON_DEMAND_POLL_INTERVAL

    #: Class-level default for controllers built without __init__ (tests).
    _control_server: Optional[ControlServer] = None

    def _service_pending_changes(self) -> None:
        """Apply changes made elsewhere while the display thread is busy.

        The main loop applies on-demand requests, the display on/off schedule
        and brightness (a saved display.hardware.brightness, or a dim-schedule
        transition) once per pass -- that is, once per screen. A screen can
        dwell a minute and a Vegas iteration four, so those waited just as
        long: an on-demand request sat unread for up to 240s, and a brightness
        saved and reverted within one screen never reached the panel at all.

        So the places the display thread spends long stretches -- the dwell
        sleep, the per-screen render loops and Vegas's interrupt check -- call
        this. It is throttled to PENDING_CHANGES_INTERVAL, runs on the display
        thread (set_brightness must not be called from the config watcher),
        and does what the main loop does, in the same order. Callers decide
        what to do about the result from the state it leaves behind
        (on_demand_active, current_display_mode, is_display_active).
        """
        now = time.monotonic()
        last = self._last_pending_service
        # A command queued on the control socket skips the floor: it is in
        # memory, so applying it now costs no disk read.
        if (last is not None and now - last < self.PENDING_CHANGES_INTERVAL
                and not (self._control_server and self._control_server.has_pending)):
            return
        self._last_pending_service = now

        try:
            # A reloaded plugin rejoins the rotation as soon as it has
            # loaded, between two frames of the screen or Vegas strip that
            # carried on while it loaded.
            self._finish_plugin_reloads()
            self._poll_on_demand_requests()
            self._check_on_demand_expiration()
            self._evaluate_schedule()
            self._apply_brightness_target(repaint=True)
            # A Vegas iteration or a long screen keeps the main loop away for
            # minutes; keep the web UI's "Now showing" from going stale.
            self._publish_current_mode_state_if_changed()
        except Exception:  # pylint: disable=broad-except
            # Called from inside Vegas and the render loops; a failure here
            # must not take the display loop down with it.
            logger.exception("Error servicing pending display changes")

    def _evaluate_schedule(self) -> None:
        """Re-check the on/off schedule, letting an on-demand session override it."""
        self._check_schedule()
        if self.on_demand_active and not self.is_display_active:
            if not self.on_demand_schedule_override:
                logger.info("On-demand override keeping display active during scheduled downtime")
            self.on_demand_schedule_override = True
            self.is_display_active = True
        elif not self.on_demand_active and self.on_demand_schedule_override:
            self.on_demand_schedule_override = False

    def _apply_brightness_target(self, repaint: bool = False) -> None:
        """Push the brightness the config and dim schedule call for, if it changed.

        Args:
            repaint: Re-push the current frame afterwards. The panel only shows
                a new brightness from the next frame pushed; the main loop is
                about to render one, but a dwell or a static screen may not
                push again for a minute.
        """
        if not self.is_display_active:
            return
        target = self._check_dim_schedule()
        if target == self.current_brightness:
            self._failed_brightness_target = None
            return
        if target == self._failed_brightness_target:
            return
        if self.display_manager.set_brightness(target):
            self.current_brightness = target
            self._failed_brightness_target = None
            if repaint:
                self.display_manager.update_display()
        else:
            self._failed_brightness_target = target

    def _select_startup_plugins(self, discovered_plugins: List[str],
                                on_demand_config: Optional[Dict[str, Any]]) -> List[str]:
        """Which plugins to load at startup, restoring on-demand state if any.

        Every normally-enabled plugin loads, on-demand or not. Loading only the
        on-demand plugin left every other plugin unavailable for the rest of
        the process's life whenever the service was restarted while on-demand
        was still active -- and a restart during an on-demand session is
        routine, since that is how updates and config changes are applied. The
        panel came back cycling that one plugin's modes and nothing else, with
        no way out but clearing the on-demand cache by hand.

        On-demand still resumes on its saved mode; this only widens what gets
        loaded, so normal rotation has somewhere to return to when it ends.
        A plugin that is disabled in config but named by the on-demand request
        is still loaded, since otherwise the mode being resumed would have
        nothing behind it. It is tracked as loaded for on-demand only, the
        same as one loaded live by _activate_on_demand, so it is unloaded
        when the session ends instead of staying loaded until the next
        restart. Its config section is not touched: setting ``enabled`` in
        self.config wrote into the dict config_manager caches and returns to
        every later load_config() in this process.
        """
        enabled_plugins = [p for p in discovered_plugins
                           if self.config.get(p, {}).get('enabled', False)]

        on_demand_plugin_id = on_demand_config.get('plugin_id') if on_demand_config else None
        if not on_demand_plugin_id:
            return enabled_plugins

        if on_demand_plugin_id not in discovered_plugins:
            logger.error("On-demand plugin '%s' not found in discovered plugins",
                         on_demand_plugin_id)
            logger.warning("Falling back to normal mode (all enabled plugins)")
            return enabled_plugins

        if on_demand_plugin_id not in enabled_plugins:
            logger.info("Loading disabled plugin '%s' for on-demand mode only", on_demand_plugin_id)
            self._on_demand_loaded_plugins.add(on_demand_plugin_id)
            enabled_plugins.append(on_demand_plugin_id)

        # Restore on-demand state from the cached request so it resumes.
        self.on_demand_active = True
        self.on_demand_plugin_id = on_demand_plugin_id
        self.on_demand_mode = on_demand_config.get('mode')
        self.on_demand_duration = on_demand_config.get('duration')
        self.on_demand_pinned = on_demand_config.get('pinned', False)
        self.on_demand_requested_at = on_demand_config.get('requested_at')
        self.on_demand_expires_at = on_demand_config.get('expires_at')
        self.on_demand_status = 'active'
        self.on_demand_schedule_override = True
        logger.info("On-demand mode detected during initialization: resuming on plugin '%s'; "
                    "all %d enabled plugin(s) still load normally",
                    on_demand_plugin_id, len(enabled_plugins))
        return enabled_plugins

    def _consume_on_demand_request(self, request_id: str) -> None:
        """Remove the request we just handled from the mailbox.

        Leaving it on disk meant a restart replayed the previous request: the
        fresh controller read it, activated it and cached it, so the request
        the caller had just made was ignored and the panel silently showed the
        earlier plugin.

        Compare before deleting. The web process can post a newer request
        between the read and this delete; an unconditional delete threw that
        one away and it was never processed -- the user's second click did
        nothing. Re-reading uncached and only deleting our own request_id
        leaves a newer request in the mailbox for the next poll instead.

        This narrows the window rather than closing it: a request landing
        between the re-read and the delete is still lost. Closing it properly
        needs an atomic claim (a rename, or a compare-and-delete primitive)
        that the cache layer does not currently offer, so the honest fix is a
        smaller window plus this note, not a bigger lock. For start requests
        processed_id still guards against reprocessing if the delete fails.
        """
        try:
            current = self.cache_manager.get('display_on_demand_request',
                                             max_age=3600, memory_ttl=0)
            if not current or current.get('request_id') == request_id:
                self.cache_manager.delete('display_on_demand_request')
            else:
                logger.debug("Newer on-demand request %s arrived while processing "
                             "%s; leaving it in the mailbox",
                             current.get('request_id'), request_id)
        except (OSError, AttributeError, KeyError) as err:
            logger.debug("Could not clear the on-demand request mailbox: %s", err)

    def _start_control_server(self) -> None:
        """Serve the control socket (src/ipc/server.py). Never raises.

        Its handlers only queue commands; _drain_control_commands applies
        them on the render thread, where the mailbox is read.
        """
        if self._control_server is not None:
            return
        hub = StateHub(loop_probe=display_watchdog.watchdog.liveness)
        try:
            self._control_server = start_control_server(
                status_provider=self._control_status,
                cache_dir=getattr(self.cache_manager, 'cache_dir', None),
                state_hub=hub)
        except Exception:  # pylint: disable=broad-except
            logger.exception("Control socket not started; using the file mailbox only")
        if self._control_server is not None:
            self._start_state_stream(hub)

    def _start_state_stream(self, hub: StateHub) -> None:
        """Start publishing to the socket's state stream (``state.get`` and
        ``state.subscribe``): everything a reader would see, now, then on
        every publish. Never raises; without it readers use the cache keys."""
        try:
            self._state_hub = hub
            self._push_live_state()
            hub.publish('on_demand', self._on_demand_state(),
                        volatile=('last_updated', 'remaining'))
            publisher = getattr(self, '_plugin_runtime_publisher', None)
            if publisher is not None:
                publisher.attach_hub(hub)
        except Exception:  # pylint: disable=broad-except
            logger.exception("Control socket state stream not started; readers use the cache")

    def _control_status(self) -> Dict[str, Any]:
        """The socket's on_demand.status answer. Runs on the socket's thread: reads only."""
        return {'on_demand': self._on_demand_state(),
                'current_mode': self.current_display_mode,
                'display_active': self.is_display_active}

    def _drain_control_commands(self) -> None:
        """Apply the commands that arrived over the control socket.

        On-demand commands go through _handle_on_demand_request, the
        mailbox's own handler, so both ways in behave the same, and a
        request that came both ways (a client that timed out and fell back)
        has one request id and is processed once. A brightness is applied
        here. A plugin reload waits for the top of the next loop pass, where
        no plugin is on the stack (_apply_pending_plugin_reloads); until
        then the current screen ends early (_plugin_reload_pending).
        """
        server = self._control_server
        if server is None or not server.has_pending:
            return
        for command in server.drain():
            try:
                if command.cmd == ControlCommand.BRIGHTNESS_SET:
                    self._apply_control_brightness(command)
                elif command.cmd == ControlCommand.PLUGIN_RELOAD:
                    self._pending_plugin_reloads = self._pending_plugin_reloads + (command,)
                else:
                    self._handle_on_demand_request(command.as_on_demand_request())
            except Exception:  # pylint: disable=broad-except
                logger.exception("Failed to apply control socket command %s",
                                 command.request_id)
                command.fail(ControlErrorCode.INTERNAL, 'the display failed to apply it')

    def _wait_for_control(self, timeout: float) -> bool:
        """Sleep up to ``timeout``, waking early for a control socket command.

        True when a command is waiting. Without a socket (Windows, switched
        off, tests) this is the plain sleep it replaces.
        """
        server = self._control_server
        wait = getattr(server, 'wait_for_command', None) if server is not None else None
        if wait is None:
            time.sleep(timeout)
            return False
        return bool(wait(timeout))

    def _control_command_pending(self) -> bool:
        """A socket command is queued: Vegas checks this every frame."""
        server = self._control_server
        return bool(server is not None and server.has_pending)

    def _screen_preempted(self, active_mode: Optional[str]) -> bool:
        """What ends a screen mid-way: the checks the frame loops make."""
        return (self.current_display_mode != active_mode
                or not self.is_display_active
                or self._wifi_notice_pending()
                or self._plugin_reload_pending)

    def _wait_frame_interval(self, interval: float, active_mode: Optional[str]) -> bool:
        """The static screen's sleep between frames, woken by socket commands.

        Each command that arrives is applied at once (_service_pending_changes
        skips its floor while one is queued). True when that ended the
        screen; otherwise the wait carries on to the end of the interval, so
        the frame cadence is unchanged by a command that does not change the
        screen (a brightness, say).
        """
        if self._control_server is None:
            time.sleep(interval)
            return False
        deadline = time.monotonic() + interval
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            if not self._wait_for_control(remaining):
                return False
            self._service_pending_changes()
            if self._screen_preempted(active_mode):
                return True

    def _apply_control_brightness(self, command: QueuedCommand) -> None:
        """``brightness.set``: the new normal brightness, on the panel now.

        It replaces the configured value in memory only, the way a saved
        config would once the config watcher saw it, so the dim schedule and
        the scheduled-off rules treat it exactly like the setting; the next
        config the watcher loads replaces it again.
        """
        args = command.args
        if not isinstance(args, BrightnessSetArgs):
            command.fail(ControlErrorCode.INTERNAL, 'not a brightness command')
            return
        self._normal_brightness = args.brightness
        # The dim schedule's per-minute cache holds the old normal level.
        self._dim_checked_minute = None
        # An explicit request retries a level the panel refused before.
        self._failed_brightness_target = None
        self._apply_brightness_target(repaint=True)
        if self.is_display_active:
            target = self._check_dim_schedule()   # cached for the minute: no new work
            if self.current_brightness != target:
                command.fail(ControlErrorCode.FAILED,
                             f'the panel did not take brightness {target}')
                return
        logger.info("Brightness set to %d%% over the control socket (panel %s%%)",
                    args.brightness, self.current_brightness)
        result: BrightnessResult = {
            'brightness': args.brightness,
            'panel_brightness': int(self.current_brightness),
            'dimmed': bool(self.is_dimmed),
            'display_active': bool(self.is_display_active),
        }
        command.succeed(dict(result))

    #: Plugin reloads from the control socket, waiting for the top of the
    #: next loop pass. A tuple, replaced rather than mutated.
    _pending_plugin_reloads: Tuple[QueuedCommand, ...] = ()
    #: Reloads started and not finished yet. Their plugin is out of the
    #: rotation while a plugin-reload thread loads it again. A tuple,
    #: replaced rather than mutated, so the config watcher can read it.
    _plugin_reload_jobs: Tuple[_PluginReloadJob, ...] = ()
    #: A reconcile skipped a plugin that was reloading: run one again once
    #: the reloads are done.
    _reconcile_after_reload = False

    @property
    def _plugin_reload_pending(self) -> bool:
        """A reload is waiting for the top of the loop, so the screen ends early.

        Only the start of a reload waits there. The load runs on another
        thread while the screens carry on, and the new instance joins the
        rotation between two frames (_finish_plugin_reloads).
        """
        return bool(self._pending_plugin_reloads)

    def _plugin_reloading(self, plugin_id: Optional[str]) -> bool:
        """``plugin_id`` is out of the rotation while it is loaded again."""
        return any(job.plugin_id == plugin_id for job in self._plugin_reload_jobs)

    def _apply_pending_plugin_reloads(self) -> None:
        """Start the plugin reloads the control socket asked for. Render
        thread, top of the loop pass: no display() and no Vegas iteration on
        the stack.

        The same steps as disabling and re-enabling the plugin live, with the
        manifest re-read from disk, so the running set ends up as a restart
        would build it. Only taking the plugin out happens here. The old
        instance is torn down and the new one loaded on a plugin-reload
        thread, so the screens carry on meanwhile (_start_plugin_reload).
        Reloads that have finished are put back in the rotation first.
        """
        self._finish_plugin_reloads()
        commands, self._pending_plugin_reloads = self._pending_plugin_reloads, ()
        for command in commands:
            try:
                self._start_plugin_reload(command)
            except Exception:  # pylint: disable=broad-except
                logger.exception("Plugin reload over the control socket failed")
                command.fail(ControlErrorCode.INTERNAL, 'the display failed to reload it')

    def _start_plugin_reload(self, command: QueuedCommand) -> None:
        """Take the plugin out of the rotation and start loading it again.

        Render thread. Everything here is quick: the controller's maps, the
        config subscription, and PluginManager.detach_plugin, after which
        nothing new calls the old instance (no update(), no Vegas fetch).
        The slow part runs on a plugin-reload thread: waiting for a Vegas
        render or an update() of the old instance to let go of its lock, the
        teardown, and the import and constructor of the new one. Meanwhile
        Vegas scrolls what its strip already holds of the plugin.
        """
        args = command.args
        if not isinstance(args, PluginReloadArgs):
            command.fail(ControlErrorCode.INTERNAL, 'not a reload command')
            return
        plugin_id = args.plugin_id
        for job in self._plugin_reload_jobs:
            if job.plugin_id == plugin_id:
                # Updated again while loading: reload once more when this
                # one is done, so the newest files are the ones that run.
                job.followers.append(command)
                return
        if self.plugin_manager is None or plugin_id not in self.plugin_display_modes:
            command.fail(ControlErrorCode.NOT_LOADED, f'{plugin_id} is not running')
            return
        if plugin_id in self._on_demand_loaded_plugins:
            # Loaded only for an on-demand session (config says disabled);
            # reloading would have to repeat that special load.
            command.fail(ControlErrorCode.BUSY,
                         f'{plugin_id} is loaded only for on-demand; restart to reload it')
            return

        previous_mode = self.current_display_mode
        previous_order = {mode: i for i, mode in enumerate(self.available_modes)}
        logger.info("Reloading plugin %s over the control socket", plugin_id)
        self._unregister_plugin(plugin_id, action='Reloading', unload=False)
        old_instance = self.plugin_manager.detach_plugin(plugin_id)
        job = _PluginReloadJob(command, plugin_id, previous_order, old_instance)
        self._plugin_reload_jobs = self._plugin_reload_jobs + (job,)
        self._resync_mode_index_after_change(previous_mode)
        self._spawn_plugin_reload(job)

    def _spawn_plugin_reload(self, job: _PluginReloadJob) -> None:
        """Run ``job`` on its own thread. The run-loop harness replaces this
        to run it on its fake clock."""
        threading.Thread(target=job.run, args=(self.plugin_manager,), daemon=True,
                         name=f'plugin-reload-{job.plugin_id}').start()

    def _finish_plugin_reloads(self) -> None:
        """Put the plugins whose reload has finished back in the rotation.

        Render thread: the top of the loop and _service_pending_changes, so
        a new instance joins between two frames of whatever is showing. That
        is never the plugin itself, which left the rotation when its reload
        started. Its modes go back to their old places in the rotation, the
        current screen stays current, Vegas fetches its content again, and
        the command is answered with the outcome.
        """
        jobs = self._plugin_reload_jobs
        if not jobs:
            return
        finished = tuple(job for job in jobs if job.done.is_set())
        if not finished:
            return
        self._plugin_reload_jobs = tuple(job for job in jobs if job not in finished)
        previous_mode = self.current_display_mode
        for job in finished:
            try:
                self._finish_plugin_reload(job)
            except Exception:  # pylint: disable=broad-except
                logger.exception("Plugin reload over the control socket failed")
                job.command.fail(ControlErrorCode.INTERNAL, 'the display failed to reload it')
            if job.followers:
                self._pending_plugin_reloads = self._pending_plugin_reloads + tuple(job.followers)
        self._apply_plugin_rotation_order()
        self._resync_mode_index_after_change(previous_mode)
        if self._reconcile_after_reload and not self._plugin_reload_jobs:
            self._reconcile_after_reload = False
            with self._reconcile_flag_lock:
                self._pending_plugin_reconcile = True

    def _finish_plugin_reload(self, job: _PluginReloadJob) -> None:
        plugin_id = job.plugin_id
        command = job.command
        if job.error is not None:
            logger.error("Plugin reload over the control socket failed",
                         exc_info=(type(job.error), job.error, job.error.__traceback__))
            command.fail(ControlErrorCode.INTERNAL, 'the display failed to reload it')
            return
        if job.unload_failed:
            logger.error("Plugin %s: the old instance could not be unloaded, so the "
                         "update was not loaded; it is out of the rotation until the "
                         "display restarts", plugin_id)
            command.fail(ControlErrorCode.FAILED,
                         f'{plugin_id}: the old version could not be unloaded; '
                         f'restart the display to load the update')
            return
        if not job.loaded:
            logger.error("Plugin %s did not load after its update; it is out of the "
                         "rotation until it loads", plugin_id)
            command.fail(ControlErrorCode.FAILED,
                         f'{plugin_id} did not load; see the display log')
            return
        modes = list(self._register_loaded_plugin(plugin_id))
        # Registering appends; put its modes back where they were in the
        # rotation (a mode the new version added goes last).
        previous_order = job.previous_order
        self.available_modes.sort(
            key=lambda mode: previous_order.get(mode, len(previous_order)))
        vegas = getattr(self, 'vegas_coordinator', None)
        if vegas is not None:
            try:
                # Fetch its content again rather than scroll the old copy.
                vegas.mark_plugin_updated(plugin_id)
            except Exception:  # pylint: disable=broad-except
                logger.debug("Vegas did not take the reload of %s", plugin_id, exc_info=True)
        manifest = (getattr(self.plugin_manager, 'plugin_manifests', None) or {}).get(plugin_id)
        version = manifest.get('version') if isinstance(manifest, dict) else None
        logger.info("Reloaded plugin %s (version %s, modes %s)", plugin_id, version, modes)
        result: PluginReloadResult = {
            'plugin_id': plugin_id, 'reloaded': True,
            'version': version if isinstance(version, str) else None,
            'modes': modes,
        }
        command.succeed(dict(result))

    def _poll_on_demand_requests(self) -> None:
        """Poll cache for new on-demand requests from external controllers."""
        # Socket commands are already in memory: no disk read, so no floor.
        self._drain_control_commands()
        now = time.monotonic()
        if (self._last_on_demand_poll is not None
                and now - self._last_on_demand_poll < self.ON_DEMAND_POLL_INTERVAL):
            return
        self._last_on_demand_poll = now

        try:
            # Use a long max_age (1 hour) to ensure requests aren't expired before processing
            # The request_id check prevents duplicate processing.
            #
            # memory_ttl=0 is required, not optional: this key is a mailbox the
            # web process writes and this process reads. get() defaults the
            # in-memory TTL to max_age, so without it the first request read was
            # pinned in memory for the full hour and every later poll returned
            # that stale copy -- meaning no second on-demand request was honoured
            # for an hour, while the API still reported success.
            request = self.cache_manager.get('display_on_demand_request',
                                             max_age=3600, memory_ttl=0)
        except (OSError, RuntimeError, ValueError, TypeError) as err:
            logger.error("Failed to read on-demand request: %s", err, exc_info=True)
            return

        if not request:
            return
        self._handle_on_demand_request(request)

    def _handle_on_demand_request(self, request: Dict[str, Any]) -> None:
        """Process one on-demand request, from the mailbox or the control socket."""
        request_id = request.get('request_id')
        if not request_id:
            return

        action = request.get('action')
        
        # For stop requests, always process them (don't check processed_id)
        # This allows stopping even if the same stop request was sent before
        if action == 'stop':
            logger.info("Received on-demand stop request %s", request_id)
            # Always process stop requests, even if same request_id (user might click multiple times)
            if self.on_demand_active:
                self.on_demand_request_id = request_id
                self._clear_on_demand(reason='requested-stop')
                logger.info("On-demand mode cleared, resuming normal rotation")
            else:
                logger.debug("Stop request %s received but on-demand is not active", request_id)
                # Still update request_id to acknowledge the request
                self.on_demand_request_id = request_id
                if self.on_demand_status == 'error':
                    # A failed request left status 'error' published, and
                    # without this the status route kept reporting it until
                    # the state aged out (120s) or another request came in.
                    self._clear_on_demand(reason='requested-stop')
            # Stop requests are deliberately exempt from the request_id/
            # processed_id guards above, so that a second click stops a mode
            # that a race left running. Consuming the mailbox is therefore the
            # only thing that ends the request: without it the same stop was
            # re-read and re-processed on every poll, forever, logging at
            # ON_DEMAND_POLL_INTERVAL for the life of the process.
            self._consume_on_demand_request(request_id)
            return

        # For start requests, check if already processed
        if request_id == self.on_demand_request_id:
            logger.debug("On-demand start request %s already processed (instance check)", request_id)
            return
        
        # Also check persistent processed_id (for restart scenarios)
        processed_request_id = self.cache_manager.get('display_on_demand_processed_id', max_age=3600)
        if request_id == processed_request_id:
            logger.debug("On-demand start request %s already processed (persisted check)", request_id)
            return
        
        logger.info("Received on-demand request %s: %s (plugin_id=%s, mode=%s)", 
                   request_id, action, request.get('plugin_id'), request.get('mode'))
        
        # Mark as processed BEFORE processing (to prevent duplicate processing)
        self.cache_manager.set('display_on_demand_processed_id', request_id, ttl=3600)
        self.on_demand_request_id = request_id
        self._consume_on_demand_request(request_id)

        if action == 'start':
            logger.info("Processing on-demand start request for plugin: %s", request.get('plugin_id'))
            self._activate_on_demand(request)
        else:
            logger.warning("Unknown on-demand action: %s", action)

    def _resolve_mode_for_plugin(self, plugin_id: Optional[str], mode: Optional[str]) -> Optional[str]:
        """Resolve the display mode to use for on-demand activation."""
        # If mode is provided, check if it's actually a valid mode or just the plugin_id
        if mode:
            # If mode matches plugin_id, it's likely the plugin_id was sent as mode
            # Try to resolve it to an actual display mode
            if plugin_id and mode == plugin_id:
                # Mode is the plugin_id, resolve to first available display mode
                if plugin_id in self.plugin_display_modes:
                    modes = self.plugin_display_modes.get(plugin_id, [])
                    if modes:
                        logger.debug("Resolving mode '%s' (plugin_id) to first display mode: %s", mode, modes[0])
                        return modes[0]
            # Check if mode is a valid display mode
            elif mode in self.plugin_modes:
                return mode
            # Mode provided but not valid - might be plugin_id, try to resolve
            elif plugin_id and plugin_id in self.plugin_display_modes:
                modes = self.plugin_display_modes.get(plugin_id, [])
                if modes and mode in modes:
                    return mode
                elif modes:
                    logger.warning("Mode '%s' not found for plugin '%s', using first available: %s", 
                                 mode, plugin_id, modes[0])
                    return modes[0]
            # Mode doesn't match anything, return as-is (will fail validation later)
            return mode

        # No mode provided, resolve from plugin_id
        if plugin_id and plugin_id in self.plugin_display_modes:
            modes = self.plugin_display_modes.get(plugin_id, [])
            if modes:
                return modes[0]
        return plugin_id

    def _on_demand_modes_for_plugin(self, plugin_id: str) -> List[str]:
        """Every loaded display mode belonging to `plugin_id`, in rotation order.

        Live modes that actually have content lead, then the rest, then live
        modes with nothing to show -- so an on-demand request for a sports
        plugin opens on a game in progress rather than an empty live screen.
        Returns an empty list when the plugin has no loaded modes.
        """
        plugin_modes = self.plugin_display_modes.get(plugin_id, [])
        if not plugin_modes:
            # Fallback: find all modes that belong to this plugin
            plugin_modes = [mode for mode, pid in self.mode_to_plugin_id.items() if pid == plugin_id]

        # Filter to only include modes that exist in plugin_modes
        available_plugin_modes = [m for m in plugin_modes if m in self.plugin_modes]
        if not available_plugin_modes:
            return []

        # Prioritize live modes if they exist and have content
        live_modes = [m for m in available_plugin_modes if m.endswith('_live')]
        other_modes = [m for m in available_plugin_modes if not m.endswith('_live')]

        # Check if live modes have content
        live_with_content = []
        for live_mode in live_modes:
            plugin_instance = self.plugin_modes.get(live_mode)
            if plugin_instance and hasattr(plugin_instance, 'has_live_content'):
                try:
                    if plugin_instance.has_live_content():
                        live_with_content.append(live_mode)
                except Exception:
                    # Treated as no live content; logged so a plugin whose
                    # check always raises is findable.
                    logger.debug("has_live_content() failed for %s", live_mode,
                                 exc_info=True)

        # Build mode list: live modes with content first, then other modes, then live modes without content
        if live_with_content:
            ordered_modes = live_with_content + other_modes + [m for m in live_modes if m not in live_with_content]
        else:
            # No live content, skip live modes
            ordered_modes = other_modes

        if not ordered_modes:
            # Only live modes available but no content - use them anyway
            ordered_modes = live_modes

        return ordered_modes

    def _apply_on_demand_pin(self, ordered_modes: List[str], resolved_mode: Optional[str],
                             pinned: bool) -> List[str]:
        """Narrow an on-demand rotation to the single requested mode when pinned.

        `pinned` reaches the controller from the API and was stored and
        republished but never acted on, so a pinned request still rotated
        through every mode the resolved plugin owns. That is the right default
        for a sports plugin, whose modes are views of one subject
        (nfl_live/nfl_recent/nfl_upcoming), and the wrong one for a plugin
        whose modes are unrelated -- each Starlark app is its own widget, so
        asking for one and getting all of them is not what was requested.
        """
        if not pinned or not resolved_mode or resolved_mode not in ordered_modes:
            return ordered_modes
        return [resolved_mode]

    def _populate_on_demand_modes_from_plugin(self) -> None:
        """
        Populate on_demand_modes from the on-demand plugin's display modes.
        Called after plugin loading completes when on-demand state is restored from cache.
        """
        if not self.on_demand_active or not self.on_demand_plugin_id:
            return

        plugin_id = self.on_demand_plugin_id

        ordered_modes = self._on_demand_modes_for_plugin(plugin_id)
        if not ordered_modes:
            logger.warning("No valid display modes found for on-demand plugin '%s' after restoration", plugin_id)
            self.on_demand_modes = []
            return

        # A restart must not silently un-pin: the pin is part of the request
        # being resumed, and it is restored from the same cached config above.
        ordered_modes = self._apply_on_demand_pin(
            ordered_modes, self.on_demand_mode, self.on_demand_pinned)

        self.on_demand_modes = ordered_modes
        # Set index to match the restored mode if available, otherwise start at 0
        if self.on_demand_mode and self.on_demand_mode in ordered_modes:
            self.on_demand_mode_index = ordered_modes.index(self.on_demand_mode)
        else:
            self.on_demand_mode_index = 0
        
        logger.info("Populated on-demand modes for plugin '%s': %s (starting at index %d: %s)", 
                   plugin_id, ordered_modes, self.on_demand_mode_index, 
                   ordered_modes[self.on_demand_mode_index] if ordered_modes else 'N/A')

    def _load_plugin_for_on_demand(self, plugin_id: str) -> bool:
        """Load an installed plugin that isn't running so on-demand can show it.

        This process only loads the plugins enabled in config, so a request
        for a disabled one -- the config page's "Preview on display" button
        offers it on every plugin -- failed with "invalid-mode" while the UI
        said the plugin would be enabled for the session. Nothing did that
        short of a restart, and restarts no longer happen on a request.

        Loads through the same path as a live enable (load_plugin, then
        _register_loaded_plugin), with force_enabled so the instance runs
        enabled while config.json keeps saying disabled. The plugin is
        recorded in _on_demand_loaded_plugins, and the main loop unloads it
        once on-demand moves off it (_release_on_demand_plugins).

        Returns False after publishing an error when the load fails. A
        plugin that isn't installed returns True without loading anything:
        the mode checks that follow report it as they always have.
        """
        if self.plugin_manager is None:
            return True
        try:
            known = self.plugin_manager.discovered_plugin_ids()
        except AttributeError:
            known = set(getattr(self.plugin_manager, 'plugin_manifests', ()) or ())
        if plugin_id not in known:
            # Installed after this process scanned: the web process checked
            # its own, fresher list before posting the request.
            try:
                known = set(self.plugin_manager.discover_plugins())
            except Exception:  # pylint: disable=broad-except
                logger.exception("On-demand: plugin discovery failed")
                known = set()
            if plugin_id not in known:
                return True

        logger.info("On-demand: loading disabled plugin '%s' for this session only", plugin_id)
        self._on_demand_loaded_plugins.add(plugin_id)
        try:
            loaded = self.plugin_manager.load_plugin(plugin_id, force_enabled=True)
            if loaded:
                modes = self._register_loaded_plugin(plugin_id)
                logger.info("On-demand: loaded plugin '%s' (modes: %s)", plugin_id, modes)
        except Exception:  # pylint: disable=broad-except
            logger.exception("On-demand: error loading plugin '%s'", plugin_id)
            loaded = False
        if not loaded:
            # Stays in _on_demand_loaded_plugins so the main loop removes
            # whatever part of it did get registered.
            logger.error("On-demand: could not load plugin '%s'", plugin_id)
            self._set_on_demand_error("load-failed")
            return False
        return True

    def _release_on_demand_plugins(self) -> None:
        """Unload plugins loaded only for on-demand that it has moved off.

        Runs from the main loop, right after its own on-demand poll, not
        where on-demand ends: a stop, an expiry or the next request is often
        read from inside a render loop or a dwell sleep, where the plugin
        being released may still be on the stack mid-display(). Unloading
        goes through _unregister_plugin, as a live disable does, and nothing
        is written to config.json.

        A plugin the user enabled in the meantime stays loaded and takes its
        place in the rotation, which is what the reconcile that the enable
        queued would have done.
        """
        if self.plugin_manager is None:  # plugin system failed after startup restore
            self._on_demand_loaded_plugins.clear()
            return
        keep = self.on_demand_plugin_id if self.on_demand_active else None
        releasable = [p for p in self._on_demand_loaded_plugins if p != keep]
        if not releasable:
            return
        try:
            config = self.config_service.get_config()
        except Exception as e:  # pylint: disable=broad-except
            logger.warning("On-demand release: falling back to cached config: %s", e)
            config = self.config
        previous_mode = self.current_display_mode
        for plugin_id in releasable:
            self._on_demand_loaded_plugins.discard(plugin_id)
            section = config.get(plugin_id)
            if isinstance(section, dict) and section.get('enabled', False):
                logger.info("On-demand: keeping plugin '%s' loaded; it was enabled "
                            "while on-demand showed it", plugin_id)
                continue
            if (plugin_id in self.plugin_display_modes
                    or self.plugin_manager.get_plugin(plugin_id) is not None):
                logger.info("On-demand: unloading plugin '%s'; it is disabled in config",
                            plugin_id)
                self._unregister_plugin(plugin_id)
        if not self.on_demand_active:
            # Only outside a session: rotation_resume_index points into
            # available_modes until the session ends.
            self._apply_plugin_rotation_order()
        self._resync_mode_index_after_change(previous_mode)
        if self.current_display_mode != previous_mode:
            self.force_change = True

    def _rotation_index_outside_on_demand(self, start: int) -> Optional[int]:
        """First index from `start` (wrapping) whose mode is not owned by a
        plugin loaded only for on-demand, or None if every mode is.

        Ending a session must not resume the rotation onto the plugin that
        is about to be unloaded. A live load appends that plugin's modes
        after the saved resume index, but a session restored after a
        restart has no saved index and its plugin was ordered in with the
        rest -- the rotation resumed onto it, and a stop read during its own
        screen changed nothing on the panel until that screen ended.
        """
        if not self._on_demand_loaded_plugins:
            return start
        on_demand_only = {mode for plugin_id in self._on_demand_loaded_plugins
                          for mode in self.plugin_display_modes.get(plugin_id, [])}
        count = len(self.available_modes)
        for step in range(count):
            index = (start + step) % count
            if self.available_modes[index] not in on_demand_only:
                return index
        return None

    def _activate_on_demand(self, request: Dict[str, Any]) -> None:
        """Activate on-demand mode for a specific plugin display."""
        plugin_id = request.get('plugin_id')
        mode = request.get('mode')
        if plugin_id and self._plugin_reloading(plugin_id):
            # Out of the rotation for a moment while a store update loads it
            # again; loading it for on-demand now would load it twice.
            logger.warning("On-demand: plugin '%s' is reloading; try again in a moment",
                           plugin_id)
            self._set_on_demand_error("plugin-reloading")
            return
        if (plugin_id and plugin_id not in self.plugin_display_modes
                and not self._load_plugin_for_on_demand(plugin_id)):
            return
        resolved_mode = self._resolve_mode_for_plugin(plugin_id, mode)

        if not resolved_mode:
            logger.error("On-demand request missing mode and plugin_id")
            self._set_on_demand_error("missing-mode")
            return

        if resolved_mode not in self.plugin_modes:
            logger.error("Requested on-demand mode '%s' is not available", resolved_mode)
            self._set_on_demand_error("invalid-mode")
            return

        resolved_plugin_id = self.mode_to_plugin_id.get(resolved_mode)
        if not resolved_plugin_id:
            logger.error("Could not resolve plugin for mode '%s'", resolved_mode)
            self._set_on_demand_error("unknown-plugin")
            return

        duration = request.get('duration')
        if duration is not None:
            try:
                duration = float(duration)
                if duration <= 0:
                    duration = None
            except (TypeError, ValueError):
                logger.warning("Invalid duration '%s' in on-demand request", duration)
                duration = None

        pinned = bool(request.get('pinned', False))
        now = time.time()

        # Only a request that starts a session records where rotation was.
        # A request made while on-demand is already showing would otherwise
        # save the previous request's mode (current_mode_index points at it
        # by now), and clearing would resume there instead of where the
        # normal rotation was interrupted.
        if not self.on_demand_active:
            if self.available_modes:
                self.rotation_resume_index = self.current_mode_index
            else:
                self.rotation_resume_index = None

        if resolved_mode in self.available_modes:
            self.current_mode_index = self.available_modes.index(resolved_mode)

        ordered_modes = self._on_demand_modes_for_plugin(resolved_plugin_id)
        if not ordered_modes:
            logger.error("No valid display modes found for plugin '%s'", resolved_plugin_id)
            self._set_on_demand_error("no-modes")
            return

        ordered_modes = self._apply_on_demand_pin(ordered_modes, resolved_mode, pinned)

        self.on_demand_active = True
        self.on_demand_mode = resolved_mode
        self.on_demand_modes = ordered_modes
        self.on_demand_mode_index = 0
        self.on_demand_plugin_id = resolved_plugin_id
        self.on_demand_duration = duration
        self.on_demand_requested_at = now
        self.on_demand_expires_at = (now + duration) if duration else None
        self.on_demand_pinned = pinned
        self.on_demand_status = 'active'
        self.on_demand_last_error = None
        self.on_demand_last_event = 'started'
        self.on_demand_schedule_override = True
        self.force_change = True
        
        # Clear display before switching to on-demand mode
        try:
            self.display_manager.clear()
            self.display_manager.update_display()
        except Exception as e:
            logger.warning("Failed to clear display during on-demand activation: %s", e)
        
        # Start with first mode (or resolved_mode if it's in the list)
        if resolved_mode in ordered_modes:
            self.on_demand_mode_index = ordered_modes.index(resolved_mode)
        self.current_display_mode = ordered_modes[self.on_demand_mode_index]
        logger.info("Activated on-demand for plugin '%s' with %d modes: %s (starting at index %d: %s)", 
                   resolved_plugin_id, len(ordered_modes), ordered_modes, 
                   self.on_demand_mode_index, self.current_display_mode)
        self._publish_on_demand_state()
        
        # Store config for initialization filtering (allows plugin filtering on restart)
        config_data = {
            'plugin_id': resolved_plugin_id,
            'mode': resolved_mode,
            'duration': duration,
            'pinned': pinned,
            'requested_at': now,
            'expires_at': self.on_demand_expires_at
        }
        # Use expiration time as TTL, but cap at 1 hour
        ttl = min(3600, int(duration)) if duration else 3600
        self.cache_manager.set('display_on_demand_config', config_data, ttl=ttl)
        logger.debug("Stored on-demand config for plugin filtering: %s", resolved_plugin_id)

    def _clear_on_demand(self, reason: Optional[str] = None) -> None:
        """Clear on-demand mode and resume normal rotation."""
        if not self.on_demand_active and self.on_demand_status == 'idle':
            if reason == 'requested-stop':
                self.on_demand_last_event = 'stop-request-ignored'  # Already idle
                self._publish_on_demand_state()
            return
        if not self.on_demand_active and self.on_demand_status == 'error':
            # _set_on_demand_error already ended any session and dropped
            # rotation_resume_index; the full clear below would only move
            # the rotation and force a redraw. Just drop the error.
            self.on_demand_status = 'idle'
            self.on_demand_last_error = None
            self.on_demand_last_event = reason or 'cleared'
            self._publish_on_demand_state()
            return

        self._reset_on_demand_fields()
        self.on_demand_status = 'idle'
        self.on_demand_last_error = None
        self.on_demand_last_event = reason or 'cleared'

        # Clear on-demand configuration from cache
        self.cache_manager.clear_cache('display_on_demand_config')

        if self.available_modes:
            saved = self.rotation_resume_index
            # Default to the current index if no resume index
            start = saved if saved is not None else self.current_mode_index
            index = self._rotation_index_outside_on_demand(start % len(self.available_modes))
            if index is None:
                # Every mode belongs to a plugin loaded only for on-demand,
                # which the main loop is about to unload; it then idles.
                self.current_mode_index = 0
                self.current_display_mode = None
                logger.info("No enabled mode to resume rotation to")
            elif saved is not None:
                self.current_mode_index = index
                self.current_display_mode = self.available_modes[index]
                logger.info("Resuming rotation from saved index %d: mode '%s'",
                            saved, self.current_display_mode)
            else:
                self.current_mode_index = index
                self.current_display_mode = self.available_modes[index]
                logger.info("Resuming rotation to mode '%s' (index %d)",
                            self.current_display_mode, self.current_mode_index)
        else:
            logger.warning("No available modes to resume rotation to")

        self.rotation_resume_index = None
        self.force_change = True
        logger.info("On-demand mode cleared (reason=%s), resuming normal rotation to mode: %s", 
                   reason, self.current_display_mode)
        self._publish_on_demand_state()

    def _check_on_demand_expiration(self) -> None:
        """Expire on-demand mode if duration has elapsed."""
        if not self.on_demand_active:
            return
        
        if self.on_demand_expires_at is None:
            return

        if time.time() >= self.on_demand_expires_at:
            logger.info("On-demand mode '%s' expired (duration: %s seconds)", 
                       self.on_demand_mode, self.on_demand_duration)
            self._clear_on_demand(reason='expired')
    
    def _log_memory_stats_if_due(self) -> None:
        """Log memory statistics if logging is enabled and interval has elapsed."""
        if not self._enable_memory_logging:
            return
        
        current_time = time.time()
        if (current_time - self._last_memory_log) < self._memory_log_interval:
            return
        
        self._last_memory_log = current_time
        
        try:
            # Log cache manager memory stats
            if hasattr(self.cache_manager, 'log_memory_cache_stats'):
                self.cache_manager.log_memory_cache_stats()
            
            # Log background service memory stats if available
            try:
                from src.background_data_service import get_background_service
                bg_service = get_background_service()
                if bg_service and hasattr(bg_service, 'log_memory_stats'):
                    bg_service.log_memory_stats()
            except Exception:
                # Background service may not be initialized
                logger.debug("Background service memory stats unavailable",
                             exc_info=True)
            
            # Log deferred updates stats
            if hasattr(self.display_manager, '_scrolling_state'):
                deferred_count = len(self.display_manager._scrolling_state.get('deferred_updates', []))
                if deferred_count > 0:
                    logger.info(f"Deferred Updates Queue: {deferred_count} pending updates")
            
        except Exception as e:
            logger.debug(f"Error logging memory stats: {e}")

    def _apply_live_priority(self, live_priority_mode):
        """Switch to a live-priority mode, or resume rotation when it ends.

        When a live-priority plugin preempts the rotation, the position the
        rotation had reached is saved so that, once live priority ends, the
        rotation resumes from there instead of continuing after the live
        plugin's mode (which would skip every mode between the two). The save
        happens only on the initial switch, not on each re-check while the
        live hold continues.
        """
        if live_priority_mode:
            if self.current_display_mode != live_priority_mode:
                logger.info("Live content detected - switching immediately to %s", live_priority_mode)
                if self._live_resume_index is None:
                    self._live_resume_index = self.current_mode_index
                self.current_display_mode = live_priority_mode
                self.force_change = True
                # Update mode index to match the new mode
                try:
                    self.current_mode_index = self.available_modes.index(live_priority_mode)
                except ValueError:
                    pass
        elif self._live_resume_index is not None and self.available_modes:
            # Live priority ended — resume rotation where it was interrupted.
            self.current_mode_index = self._live_resume_index % len(self.available_modes)
            self.current_display_mode = self.available_modes[self.current_mode_index]
            self.force_change = True
            logger.info("Live priority ended - resuming rotation at %s", self.current_display_mode)
            self._live_resume_index = None

    def _collect_live_modes(self):
        """Return every currently live-priority mode, in registration order.

        Scans all registered plugin modes; for each plugin that has live
        priority *and* live content, collects the specific live mode(s) it
        reports via get_live_modes() (only those actually registered), falling
        back to the scanned mode name when it ends in '_live'. Deduplicated,
        preserving order. A plugin registered under several mode keys (the
        sports plugins register one per league) contributes each live mode once.
        """
        self._last_live_scan = time.monotonic()
        live = []
        seen = set()
        # Asked once per plugin per scan, not once per mode key: a scoreboard
        # registered under several modes computes the same answer each time.
        is_live: Dict[int, bool] = {}
        for mode_name, plugin_instance in self.plugin_modes.items():
            if not (hasattr(plugin_instance, 'has_live_priority')
                    and hasattr(plugin_instance, 'has_live_content')):
                continue
            try:
                key = id(plugin_instance)
                if key not in is_live:
                    is_live[key] = bool(plugin_instance.has_live_priority()
                                        and plugin_instance.has_live_content())
                if not is_live[key]:
                    continue
                resolved = []
                if hasattr(plugin_instance, 'get_live_modes'):
                    for suggested_mode in (plugin_instance.get_live_modes() or []):
                        if suggested_mode in self.plugin_modes:
                            resolved.append(suggested_mode)
                if not resolved and mode_name.endswith('_live'):
                    resolved.append(mode_name)
                for m in resolved:
                    if m not in seen:
                        seen.add(m)
                        live.append(m)
            except Exception as e:
                logger.warning("Error checking live priority for %s: %s", mode_name, e)
        self._last_live_modes = tuple(live)
        return live

    def _vegas_keeps_live_in_ticker(self) -> bool:
        """Whether live content should stay in the ticker instead of preempting it."""
        coordinator = self.vegas_coordinator
        config = getattr(coordinator, 'vegas_config', None)
        return bool(getattr(config, 'live_in_ticker', True))

    def _check_live_priority(self, advance=False):
        """Return the live-priority mode to display, or None if nothing is live.

        When several plugins report live content at once (e.g. a baseball game
        and a soccer match), this round-robins between them so the display
        alternates each dwell instead of pinning to whichever plugin is first in
        registration order.

        advance=False (default): a non-advancing peek — returns the live mode
        already on screen if it is still live, otherwise the first live mode.
        Used by the Vegas coordinator and the vegas-active check, which only
        need to know whether *any* game is live (and must not spin the cursor).

        advance=True: the rotation pick — returns the live mode *after* the one
        currently shown, so each dwell advances to the next live game. The
        currently-displayed mode is the cursor, so this stays correct as games
        start and end (no separate index to keep in sync).
        """
        live_modes = self._collect_live_modes()
        if not live_modes:
            return None
        if self.current_display_mode in live_modes:
            if advance:
                idx = live_modes.index(self.current_display_mode)
                return live_modes[(idx + 1) % len(live_modes)]
            return self.current_display_mode
        return live_modes[0]

    #: Shortest gap between live-priority scans made mid-screen. A scan asks
    #: every live-priority plugin has_live_content(), which the scoreboards
    #: compute by filtering their game lists. Once a second matches the 1 Hz
    #: frame loop and is a quarter of the rate Vegas already polls at.
    LIVE_TAKEOVER_INTERVAL = 1.0

    #: Class-level defaults for controllers built without __init__ (tests).
    #: When the last live-priority scan of any kind ran (_collect_live_modes).
    _last_live_scan: Optional[float] = None
    #: What that scan found.
    _last_live_modes: Tuple[str, ...] = ()
    #: A mid-screen takeover chose current_display_mode and it has not been
    #: shown yet, so the next pass must not advance the live round-robin past it.
    _live_takeover_unshown: bool = False

    def _check_live_takeover(self) -> None:
        """Hand the panel to a live game that started while a screen runs.

        Called from the frame loops and the dwell sleep. Live priority used
        to be checked only between screens, so a game that went live during
        a 30 s screen waited for it to end. This switches current_display_mode
        to the live mode, which ends the screen the way any other mode change
        does. Throttled to LIVE_TAKEOVER_INTERVAL since the last scan of any
        kind. Nothing happens while an on-demand session is active, while
        the panel is scheduled off, while Vegas keeps live content in its
        ticker, or when the screen showing is already a live mode.

        A live screen is not rescanned at all: live priority put it there,
        and live games take turns between screens, not mid-screen.
        """
        if self.current_display_mode in self._last_live_modes:
            return
        last = self._last_live_scan
        if last is not None and time.monotonic() - last < self.LIVE_TAKEOVER_INTERVAL:
            return
        if self.on_demand_active or not self.is_display_active:
            return
        try:
            coordinator = getattr(self, 'vegas_coordinator', None)
            if (coordinator is not None and coordinator.is_enabled
                    and self._vegas_keeps_live_in_ticker()):
                return
            live_modes = self._collect_live_modes()
            if not live_modes or self.current_display_mode in live_modes:
                return
            self._apply_live_priority(live_modes[0])
            self._live_takeover_unshown = True
        except Exception:  # pylint: disable=broad-except
            # Called from inside the frame loops; a failure here must not
            # take the display loop down with it.
            logger.exception("Error checking for a live-priority takeover")

    # -- Pieces of run() --------------------------------------------------
    # Extracted from run() unchanged, as the first step of restructuring it
    # into an Arbiter / ScreenRunner / Sources (docs/RUN_LOOP_REDESIGN.md).
    # test/test_run_loop_golden.py pins down what the loop does with them.

    def _blank_while_scheduled_off(self, dwell: float) -> None:
        """One pass while the schedule has the panel off: blank it and dwell.

        The Arbiter's SCHEDULED_OFF plan; ``dwell`` is its max_duration. The
        dwell returns early when on-demand starts or the schedule turns the
        panel back on (see _sleep_with_plugin_updates).
        """
        # Clear display when schedule makes it inactive to ensure blank screen
        # (not showing initialization screen)
        self._end_scroll_before_core_screen()
        try:
            self.display_manager.clear()
            self.display_manager.update_display()
        except Exception as e:
            logger.debug(f"Error clearing display when inactive: {e}")

        logger.info(f"Display not active (is_display_active={self.is_display_active}), sleeping...")
        self._publish_current_mode_state()
        self._sleep_with_plugin_updates(dwell)

    def _run_follower_frame(self) -> None:
        """One frame while a sync leader drives this panel (follower mode).

        Dead-reckoning follower render: advance the local position at the
        configured speed each tick, then snap or nudge it toward the
        leader's scroll_x to absorb UDP jitter. Paced to a fixed deadline
        grid of _FOLLOWER_FRAME_INTERVAL.
        """
        now_dr = time.perf_counter()
        last_t = self._follower_dr_last_t
        dt = now_dr - last_t if last_t is not None else 0.0
        self._follower_dr_last_t = now_dr

        vc = self.vegas_coordinator
        rp = vc.render_pipeline if (vc and vc.render_pipeline) else None
        width = self.display_manager.width
        self._adopt_follower_scroll_image(rp)

        local_x = self._follower_local_x
        if local_x is None:
            local_x = float(width)  # safe start (past pre-roll guard)
        local_x += self._scroll_speed * dt

        # Latest position from the leader (None until a packet arrives)
        scroll_x = self.sync_manager.get_latest_scroll_x()
        if scroll_x is not None:
            diff = scroll_x - local_x
            total_w = (
                rp.scroll_helper.total_scroll_width
                if rp and rp.scroll_helper.total_scroll_width
                else width * _FOLLOWER_FALLBACK_STRIP_SCREENS
            )
            if abs(diff) > total_w * _FOLLOWER_SNAP_FRACTION:
                # A jump that large is a cycle reset: snap.
                local_x = float(scroll_x)
                self._follower_pending_new_image = True
            elif abs(diff) > _FOLLOWER_DRIFT_PX:
                local_x += diff * _FOLLOWER_DRIFT_GAIN
            else:
                local_x += diff * _FOLLOWER_NEAR_GAIN

        self._follower_local_x = local_x

        # has_strip(), not cached_image: every follower frame asks, and
        # reading a strip the follower's own rebuild deferred would build
        # and keep a second copy of it as a PIL image.
        if rp and rp.scroll_helper.has_strip():
            # Hold last frame until TCP image arrives after cycle reset
            if not self._follower_pending_new_image and local_x >= width:
                rp.scroll_helper.scroll_position = (
                    local_x + self._follower_sign() * width)
                frame = rp.scroll_helper.get_visible_portion()
                if frame is not None:
                    self._follower_last_frame = frame
        elif scroll_x is None:
            # Fallback: pixel frame before first scroll_x arrives
            frame = self.sync_manager.get_latest_frame()
            if frame is not None:
                self._follower_last_frame = frame

        if self._follower_last_frame is not None:
            self.display_manager.image = self._follower_last_frame
            self.display_manager._sync_render_allowed = True
            self.display_manager.update_display()
            self.display_manager._sync_render_allowed = False

        # Pace to a fixed deadline grid rather than sleeping a
        # flat interval, so render time doesn't lower the rate.
        # After a stall longer than _FOLLOWER_DEADLINE_SLIP the
        # grid restarts instead of rendering a burst to catch up.
        now = time.perf_counter()
        deadline = self._follower_deadline
        if deadline is None or now > deadline + _FOLLOWER_DEADLINE_SLIP:
            deadline = now
        deadline += _FOLLOWER_FRAME_INTERVAL
        self._follower_deadline = deadline
        remaining = deadline - time.perf_counter()
        if remaining > 0:
            time.sleep(remaining)

    def _arbiter_inputs(self) -> ArbiterInputs:
        """This pass's snapshot for Arbiter.decide, taken after _evaluate_schedule.

        _evaluate_schedule forces is_display_active on while an on-demand
        session overrides a scheduled-off window, and flags that with
        on_demand_schedule_override, so the schedule's own answer is "on and
        not overridden". The WiFi notice is read only when it could win --
        the panel is on and neither a follower nor on-demand outranks it --
        because _check_wifi_status_message has side effects (its 1 Hz
        throttle, deleting an expired or corrupt file) that such a pass
        never had.
        """
        schedule_on = self.is_display_active and not self.on_demand_schedule_override
        on_demand = self.on_demand_active
        follower = self.sync_manager.is_follower_active()
        notice = None
        if self.is_display_active and not follower and not on_demand:
            notice = self._read_wifi_notice()
        return ArbiterInputs(schedule_on=schedule_on, on_demand_active=on_demand,
                             follower_active=follower, wifi_notice=notice)

    def _read_wifi_notice(self) -> Optional[WifiNotice]:
        """The pending WiFi notice (see _check_wifi_status_message), or None."""
        status = self._check_wifi_status_message()
        if not status:
            return None
        return WifiNotice(message=status['message'],
                          expires_at=float(status['expires_at']))

    def _show_wifi_notice(self, notice: WifiNotice, dwell: float) -> bool:
        """Draw the Arbiter's WIFI plan and hold it for ``dwell`` seconds.

        Returns True when the message was drawn, and the pass ends there
        (no rotation). A message that fails to draw is treated as no
        message: the pass carries on as a LEGACY plan.
        """
        self._end_scroll_before_core_screen()
        if not self._display_wifi_status_message(
                {'message': notice.message, 'expires_at': notice.expires_at}):
            # Display failed, clear the status and continue normally
            return False
        # The plugin that resumes afterwards must redraw
        # the whole panel, not paint over the message.
        self.force_change = True
        self._sleep_with_plugin_updates(dwell)
        return True

    def _wifi_notice_pending(self) -> bool:
        """True when a WiFi notice should end the current screen early.

        Polled from the frame loops, the dwell sleep and after a Vegas
        iteration yields, so a notice preempts whatever is on the panel
        within about a second instead of waiting for the screen to end --
        by which time a short notice has usually expired unseen. Cheap at
        frame rate: _check_wifi_status_message stats the file at most once
        a second. The rule is display_arbiter.wifi_notice_preempts; the
        file is not read at all while on-demand, which outranks the notice,
        is active.
        """
        if self.on_demand_active:
            return False
        return wifi_notice_preempts(self._read_wifi_notice(), self.on_demand_active,
                                    time.time())

    def _resolve_active_mode(self):
        """The mode this pass shows: the on-demand session's current mode
        while one is active, else the rotation's.

        Moves current_display_mode onto the on-demand mode (forcing a clear)
        when they differ, and ends an on-demand session that has no modes.
        None when the rotation has no current mode.
        """
        if not self.on_demand_active:
            return self.current_display_mode
        # Guard against empty on_demand_modes
        if not self.on_demand_modes:
            logger.warning("On-demand active but no modes available, clearing on-demand mode")
            self._clear_on_demand(reason='no-modes-available')
            return self.current_display_mode
        # Rotate through on-demand plugin modes
        if self.on_demand_mode_index >= len(self.on_demand_modes):
            # Reset to first mode if index is out of bounds
            self.on_demand_mode_index = 0
        active_mode = self.on_demand_modes[self.on_demand_mode_index]
        if self.current_display_mode != active_mode:
            self.current_display_mode = active_mode
            self.force_change = True
        return active_mode

    def _plugin_for_mode(self, mode: Optional[str]):
        """The plugin to draw ``mode``, or None to skip it this pass.

        None when no plugin owns the mode, it has no display(), or the
        circuit breaker has it switched off.
        """
        if mode not in self.plugin_modes:
            logger.warning(f"Mode {mode} not found in plugin_modes (available: {list(self.plugin_modes.keys())})")
            return None
        plugin_instance = self.plugin_modes[mode]
        if not hasattr(plugin_instance, 'display'):
            logger.warning(f"Plugin {mode} found but has no display() method")
            return None
        # Check plugin health before attempting to display
        plugin_id = getattr(plugin_instance, 'plugin_id', mode)
        health_tracker = self._health_tracker()
        if health_tracker is not None and health_tracker.should_skip_plugin(plugin_id):
            logger.info("Skipping plugin %s due to circuit breaker (mode: %s)", plugin_id, mode)
            return None
        logger.debug(f"Found plugin manager for mode {mode}: {type(plugin_instance).__name__}")
        return plugin_instance

    def _start_screen_handover(self, plugin, active_mode: str) -> bool:
        """Before a screen's first dispatch: if the screen is static, keep
        the last scroll's leftovers off its first frame.

        Nothing else ends a scroll when the rotation moves on: the state
        expires 2 s after the scroller's last frame. Left to that, a static
        screen's first frame -- up for a whole second -- went out, on a panel
        with scan-order compensation, with rows taken from the scroller's
        last frame (after a held scroll, for its first refresh); and its
        second frame, 1 s later, was still "mid-scroll", so the frame-timing
        soak counted a 1-2 s freeze and the stall watchdog logged a "Render
        stall" at every scroller-to-static handover. See
        DisplayManager.end_scroll_for_static_screen.

        Returns whether the screen is static, for _finish_screen_handover.
        False when that cannot be told, which leaves the scroll state as it
        was before this existed.
        """
        try:
            static_screen = not self._needs_high_fps(plugin, active_mode, log=False)
        except Exception:  # pylint: disable=broad-except
            # A plugin property raising. The FPS check after the dispatch is
            # where that is reported; here it only means "leave it alone".
            logger.debug("Could not tell whether %s is static before its first frame",
                         active_mode, exc_info=True)
            return False
        if static_screen:
            end_scroll = getattr(self.display_manager, 'end_scroll_for_static_screen', None)
            if end_scroll is not None:
                end_scroll()
        return static_screen

    def _end_scroll_before_core_screen(self) -> None:
        """End any scroll before the controller draws a screen of its own.

        The schedule-off blank and the WiFi notice are drawn by the
        controller, not by a plugin, so they never pass through
        _dispatch_first_frame and its handover. Drawn while the last scroll's state is still set, the blank
        went out with the scroller's lagging rows (on a scan-compensated
        panel) for its 60 s dwell, and the notice's redraws were timed as
        freezes of the old scroll. Ending the state first sends them out as
        drawn, at hold 1, as static frames. A scroller that resumes sets the
        state again on its next frame.
        """
        set_scrolling_state = getattr(self.display_manager, 'set_scrolling_state', None)
        if set_scrolling_state is not None:
            set_scrolling_state(False)

    def _note_screen_handover(self) -> None:
        """Tag the frame the first dispatch is about to present.

        The gap from the last screen's final frame to it is the next screen
        drawing, not a scroll freezing: frame_timing counts it apart from
        the freezes, and the stall watchdog labels it a handover gap.
        """
        recorder = getattr(self.display_manager, 'frame_timing', None)
        note = getattr(recorder, 'note_op', None)
        if note is not None:
            note(HANDOVER_OP)

    def _finish_screen_handover(self, static_screen: bool) -> None:
        """After a screen's first dispatch, whatever it returned.

        Drops the handover tag if no frame took it (a screen with nothing to
        show), so it cannot land on an unrelated frame later. For a static
        screen, also ends the previous scroll now, whether or not it showed
        anything: its first frame has gone out, and with the state left set
        its next one -- a second later in the 1 Hz loop -- would be timed as
        a frame of the old scroll.
        """
        dm = self.display_manager
        recorder = getattr(dm, 'frame_timing', None)
        drop = getattr(recorder, 'drop_op', None)
        if drop is not None:
            drop(HANDOVER_OP)
        if static_screen:
            set_scrolling_state = getattr(dm, 'set_scrolling_state', None)
            if set_scrolling_state is not None:
                set_scrolling_state(False)

    def _dispatch_first_frame(self, plugin, active_mode: str) -> Tuple[bool, bool, bool]:
        """Draw the first frame of a screen through the PluginExecutor.

        Later frames call display() directly (_display_once). This one goes
        through the executor so a hang or an exception here is bounded and
        recorded.

        Returns:
            (shown, raised, accepts_display_mode). ``shown`` is False when
            display() returned False (nothing to show) or anything raised;
            ``raised`` says it was an exception rather than no content.
            ``accepts_display_mode`` is the cached signature check the
            screen's later frames need; it is only meaningful when shown.
        """
        # A plugin that returns nothing (None) counts as having displayed.
        display_result = True
        # Whether a False result came from an exception rather than
        # the plugin having no content.
        display_failed_due_to_exception = False
        _accepts_display_mode = False
        plugin_id = getattr(plugin, 'plugin_id', active_mode)
        # Decided before the first frame rather than at run()'s FPS check
        # after it, by when that frame has gone out with the last scroll's
        # rows. See _start_screen_handover.
        static_screen = self._start_screen_handover(plugin, active_mode)
        try:
            logger.debug(f"Calling display() for {active_mode} with force_clear={self.force_change}")
            if plugin_id not in self._plugin_accepts_display_mode:
                self._plugin_accepts_display_mode[plugin_id] = (
                    'display_mode' in inspect.signature(plugin.display).parameters
                )
            _accepts_display_mode = self._plugin_accepts_display_mode[plugin_id]

            pm = self.plugin_manager
            display_lock = pm.get_plugin_lock(plugin_id) if pm else None
            can_display = display_lock is None or display_lock.acquire(blocking=False)
            display_hung = False
            # Set when display() raised inside the executor.
            display_error: Optional[Exception] = None
            if can_display:
                # Only when display() will run: a busy plugin
                # presents nothing for the tag to land on.
                self._note_screen_handover()

            if display_lock is None:
                # Only when plugin loading failed part-way.
                result = self._display_once(
                    plugin, active_mode, _accepts_display_mode,
                    force_clear=self.force_change)
            elif not can_display:
                # update() in flight on the worker — hold
                # the last frame; not a plugin failure
                result = True
            else:
                # PluginExecutor's own thread.join(timeout) can
                # return before the real display() call
                # finishes (a lingering daemon thread keeps
                # running it) -- so the lock is released from
                # inside the wrapped call itself, whichever
                # thread actually finishes it, rather than
                # here when this dispatch merely returns.
                release_guard = threading.Lock()
                released = {'done': False, 'started': False}

                def _release_display_lock():
                    with release_guard:
                        if released['done']:
                            return
                        released['done'] = True
                    display_lock.release()

                if _accepts_display_mode:
                    def _display_target(display_mode=None, force_clear=False):
                        released['started'] = True
                        try:
                            return plugin.display(
                                display_mode=display_mode, force_clear=force_clear)
                        finally:
                            _release_display_lock()
                else:
                    def _display_target(force_clear=False):
                        released['started'] = True
                        try:
                            return plugin.display(force_clear=force_clear)
                        finally:
                            _release_display_lock()

                dispatch_start = time.monotonic()
                try:
                    result = pm.plugin_executor.execute_display(
                        types.SimpleNamespace(display=_display_target),
                        plugin_id,
                        force_clear=self.force_change,
                        display_mode=active_mode if _accepts_display_mode else None,
                        # Already resolved and cached above.
                        # Without this the executor re-derives
                        # it with inspect.signature() against
                        # the SimpleNamespace built above -- a
                        # fresh callable every call, so nothing
                        # there can ever cache.
                        accepts_display_mode=_accepts_display_mode,
                        # A raise must reach the breaker as a
                        # failure. As a bare False it read as
                        # "no content" and was recorded as a
                        # success, so the breaker never tripped
                        # on a plugin that raises every time.
                        raise_errors=True
                    )
                except PluginError as exc:
                    # display() raised. The executor has logged
                    # and recorded it, and _display_target's
                    # finally released the lock. The screen is
                    # an empty pass, as before; only the health
                    # record changes. Keep what display() raised
                    # as last_error, not the executor's wrapper.
                    display_error = exc.__cause__ or exc
                    result = False
                except Exception:  # pragma: no cover - defensive;
                    # execute_display catches everything
                    # internally, but guarantee the lock is
                    # never leaked if something unexpected
                    # slips through.
                    _release_display_lock()
                    raise

                dispatch_seconds = time.monotonic() - dispatch_start
                if released['started'] and not released['done']:
                    # The executor gave up waiting and display()
                    # is still running on its thread, holding
                    # the lock. A hang, not a success: recorded
                    # so repeats open the circuit breaker, and
                    # the update worker's bounded wait skips it.
                    display_hung = True
                    pm.record_display_hang(plugin_id, dispatch_seconds)
                else:
                    pm.note_display_duration(plugin_id, dispatch_seconds)

            logger.debug(f"display() returned: {result} (type: {type(result)})")
            if isinstance(result, bool):
                display_result = result
                if not display_result:
                    logger.debug("Plugin %s display() returned False for mode %s", plugin_id, active_mode)

            # Record success only when display() actually ran this
            # frame -- a skipped frame (lock busy) held the last
            # frame, not a real success, and must not clear
            # force_change or the pending mode-switch clear will
            # be lost when display() finally does run.
            # A hang was already recorded as a failure by
            # record_display_hang, so it records nothing here.
            if can_display:
                health_tracker = self._health_tracker()
                if health_tracker is not None and not display_hung:
                    if display_error is not None:
                        health_tracker.record_failure(plugin_id, display_error)
                    else:
                        health_tracker.record_success(plugin_id)
                self.force_change = False
        except Exception as exc:  # pylint: disable=broad-except
            logger.exception("Error displaying %s", self.current_display_mode)
            health_tracker = self._health_tracker()
            if health_tracker is not None:
                health_tracker.record_failure(plugin_id, exc)
            self.force_change = True
            display_result = False
            display_failed_due_to_exception = True
        # Whatever the dispatch did -- drew, had nothing to show, raised
        # inside the executor or out here -- and after the health record,
        # before the 1 Hz loop or the next mode.
        self._finish_screen_handover(static_screen)
        return display_result, display_failed_due_to_exception, _accepts_display_mode

    def _skip_failed_plugin_modes(self, active_mode: str) -> bool:
        """After a plugin's dispatch raised, move the rotation past every
        mode that plugin owns.

        Returns True when it moved to another plugin's mode. False when
        every remaining mode is the failed plugin's (or the mode has no
        known plugin): the caller then rotates normally.
        """
        current_plugin_id = self.mode_to_plugin_id.get(active_mode)
        if not (current_plugin_id and current_plugin_id in self.plugin_display_modes):
            return False
        plugin_modes = self.plugin_display_modes[current_plugin_id]
        logger.warning("Skipping all %d mode(s) for plugin %s due to exception: %s",
                       len(plugin_modes), current_plugin_id, plugin_modes)
        # Find the next mode that's not from this plugin
        next_index = self.current_mode_index
        attempts = 0
        max_attempts = len(self.available_modes)
        while attempts < max_attempts:
            next_index = (next_index + 1) % len(self.available_modes)
            next_mode = self.available_modes[next_index]
            next_plugin_id = self.mode_to_plugin_id.get(next_mode)
            if next_plugin_id != current_plugin_id:
                self.current_mode_index = next_index
                self.current_display_mode = next_mode
                self.force_change = True
                logger.info("Switching to mode: %s (skipped plugin %s due to exception)",
                            self.current_display_mode, current_plugin_id)
                return True
            attempts += 1
        # If we couldn't find a different plugin, just advance normally
        logger.warning("All remaining modes are from plugin %s, advancing normally", current_plugin_id)
        return False

    def _track_dynamic_cycle(self, plugin, active_mode: str, dynamic_enabled: bool) -> None:
        """Reset the plugin's cycle when a different dynamic-duration mode
        comes on screen; forget the active one when dynamic duration is off.

        Only switching to a different dynamic mode resets the cycle. Staying
        on the same live-priority mode with force_change=True (which is used
        for display clearing, not cycle resets) must not.
        """
        if dynamic_enabled and self._active_dynamic_mode != active_mode:
            if self._active_dynamic_mode is not None:
                logger.debug(
                    "Switching dynamic duration mode from %s to %s - resetting cycle",
                    self._active_dynamic_mode,
                    active_mode,
                )
            else:
                logger.debug(
                    "Starting dynamic duration mode %s - resetting cycle",
                    active_mode,
                )
            self._plugin_reset_cycle(plugin)
            self._active_dynamic_mode = active_mode
        elif not dynamic_enabled and self._active_dynamic_mode == active_mode:
            logger.debug(
                "Dynamic duration disabled for mode %s - clearing active dynamic mode",
                active_mode,
            )
            self._active_dynamic_mode = None

    def _resolve_durations(self, plugin, active_mode: str, base_duration: float,
                           dynamic_enabled: bool) -> Tuple[float, float]:
        """The (min, max) seconds a screen of ``active_mode`` runs for.

        Without dynamic duration both are the mode's display duration. With
        it, min is the display duration and max is the plugin's own cycle
        duration if it reports one, capped by the smaller of the plugin's
        and the global cap (DEFAULT_DYNAMIC_DURATION_CAP when neither is
        set), and never below min. A non-positive duration becomes 15 s.

        Changes no controller state; it only asks the plugin and config.
        """
        min_duration = base_duration
        if dynamic_enabled:
            # Try to get plugin-calculated cycle duration first
            logger.debug("Attempting to get cycle duration for mode %s", active_mode)
            plugin_cycle_duration = self._plugin_cycle_duration(plugin, active_mode)
            logger.debug("Got cycle duration: %s", plugin_cycle_duration)

            # Get caps for validation
            plugin_cap = self._plugin_dynamic_cap(plugin)
            global_cap = self._get_global_dynamic_cap()
            cap_candidates = [
                cap
                for cap in (plugin_cap, global_cap)
                if cap is not None and cap > 0
            ]
            if cap_candidates:
                chosen_cap = min(cap_candidates)
            else:
                chosen_cap = DEFAULT_DYNAMIC_DURATION_CAP

            # Validate and sanitize durations
            if min_duration <= 0:
                logger.warning(
                    "Invalid min_duration %s for mode %s, using default 15s",
                    min_duration,
                    active_mode,
                )
                min_duration = 15.0

            # Use plugin-calculated duration if available, capped by max
            if plugin_cycle_duration is not None and plugin_cycle_duration > 0:
                # Plugin provided a calculated duration - use it but respect cap
                max_duration = min(plugin_cycle_duration, chosen_cap)
                logger.info(
                    "Using plugin-calculated cycle duration for %s: %.1fs (capped at %.1fs)",
                    active_mode,
                    plugin_cycle_duration,
                    chosen_cap,
                )
            else:
                # No calculated duration - use cap as max
                max_duration = chosen_cap

            # Ensure max_duration >= min_duration
            max_duration = max(min_duration, max_duration)
        else:
            max_duration = base_duration

            # Validate base duration even when not dynamic
            if max_duration <= 0:
                logger.warning(
                    "Invalid base_duration %s for mode %s, using default 15s",
                    max_duration,
                    active_mode,
                )
                max_duration = 15.0
        return min_duration, max_duration

    def _clamp_to_on_demand(self, min_duration: float,
                            max_duration: float) -> Optional[Tuple[float, float]]:
        """Shorten a screen's (min, max) to what is left of a timed on-demand
        session. None when the session has no time left (nothing to show).
        """
        if self.on_demand_active:
            remaining = self._get_on_demand_remaining()
            if remaining is not None:
                min_duration = min(min_duration, remaining)
                max_duration = min(max_duration, remaining)
                if max_duration <= 0:
                    return None
        return min_duration, max_duration

    def _needs_high_fps(self, plugin, active_mode: str, log: bool = True) -> bool:
        """Whether a screen runs the high-FPS (8 ms) loop or the 1 s one.

        In precedence order:
        1. A plugin that declares needs_high_fps knows best
           (e.g. static-image sets it False for still PNGs,
           True for animated GIFs).
        2. Back-compat: older static-image versions without
           the attribute keep the historical forced high-FPS
           (GIF support).
        3. Otherwise scrolling plugins get high FPS.

        ``log=False`` for the look taken before a screen's first dispatch
        (see _start_screen_handover): the FPS check after it logs the
        decision, and once per screen is enough.
        """
        plugin_id = getattr(plugin, 'plugin_id', None)
        declared = getattr(plugin, 'needs_high_fps', None)
        if declared is not None:
            needs_high_fps = bool(declared)
            if log:
                logger.debug(
                    "[DisplayController] FPS check for %s (plugin=%s) - "
                    "plugin declares needs_high_fps=%s",
                    active_mode, plugin_id, needs_high_fps)
        elif plugin_id == 'static-image':
            needs_high_fps = True
            if log:
                logger.debug("FPS check - static-image plugin: forcing high-FPS mode for GIF support")
        else:
            has_enable_scrolling = hasattr(plugin, 'enable_scrolling')
            enable_scrolling_value = getattr(plugin, 'enable_scrolling', False)
            needs_high_fps = has_enable_scrolling and enable_scrolling_value
            if log:
                logger.info(
                    "FPS check for %s - has_enable_scrolling: %s, enable_scrolling_value: %s, needs_high_fps: %s",
                    active_mode,
                    has_enable_scrolling,
                    enable_scrolling_value,
                    needs_high_fps,
                )
        return needs_high_fps

    def _advance_after_screen(self, active_mode: Optional[str]) -> None:
        """Pick the next mode once a screen has run its course.

        An on-demand session moves to its next mode (one with no modes left
        is ended, and the rotation advances instead). Otherwise the rotation
        advances -- unless the mode just shown is a live-priority mode that
        is still live, which holds the panel.
        """
        if self.on_demand_active:
            # Guard against empty on_demand_modes to prevent ZeroDivisionError
            if not self.on_demand_modes:
                logger.warning("On-demand active but no modes available, clearing on-demand mode")
                self._clear_on_demand(reason='no-modes-available')
                # Fall through to normal rotation
            else:
                self._advance_on_demand()
                return

        # Check for live priority - don't rotate if current plugin has live content
        should_rotate = True
        if active_mode in self.plugin_modes:
            plugin_instance = self.plugin_modes[active_mode]
            if hasattr(plugin_instance, 'has_live_priority') and hasattr(plugin_instance, 'has_live_content'):
                try:
                    if plugin_instance.has_live_priority() and plugin_instance.has_live_content():
                        logger.info("Live priority active for %s - staying on current mode", active_mode)
                        should_rotate = False
                except Exception as e:
                    logger.warning("Error checking live priority for %s: %s", active_mode, e)

        if should_rotate and self.available_modes:
            self.current_mode_index = (self.current_mode_index + 1) % len(self.available_modes)
            self.current_display_mode = self.available_modes[self.current_mode_index]
            self.force_change = True

            logger.info("Switching to mode: %s", self.current_display_mode)

    def run(self):
        """Run the display controller, switching between displays."""
        if not self.available_modes:
            logger.warning(
                "No display modes are enabled at startup; idling until a "
                "plugin is enabled via the web UI."
            )

        # This thread is the one the systemd watchdog and the heartbeat
        # vouch for: beats from any other thread are ignored, so a render
        # thread stuck inside a plugin stops them.
        display_watchdog.watchdog.bind_render_thread()
        self._start_control_server()

        try:
            # Initialize with cached data for fast startup - let background updates refresh naturally
            logger.info("Starting display with cached data (fast startup mode)")
            self.current_display_mode = self.available_modes[self.current_mode_index] if self.available_modes else 'none'
            logger.info(f"Initial mode set to: {self.current_display_mode} (index: {self.current_mode_index}, total modes: {len(self.available_modes)})")
            self._publish_current_mode_state()
            
            while True:
                # Arms the watchdog after the first frame -- or after the
                # first full pass, when there is nothing to draw -- and pings
                # it from then on.
                display_watchdog.watchdog.loop_pass()

                # Apply plugin enable/disable edits saved via the web UI. The
                # config-watcher thread only sets the flag; loading/unloading and
                # rebuilding available_modes happens here on the render thread so
                # it can't race with rendering. Deferred while on-demand is active
                # (the flag stays set) so we don't fight its temporary-enable.
                # The lock-free read is a fast path only; it can be a false
                # negative (the watcher setting the flag just after it is read
                # is seen next iteration), never a false positive that loses a
                # request.
                if self._pending_plugin_reconcile and not self.on_demand_active:
                    self._service_pending_reconcile()

                # Plugin reloads from the control socket (a store update),
                # started here for the same reason: nothing of the plugin's
                # is on the stack. The screen that was showing ended early
                # for them. Reloads that have finished loading join the
                # rotation here too, if no frame got to them first.
                if self._pending_plugin_reloads or self._plugin_reload_jobs:
                    self._apply_pending_plugin_reloads()

                if not self.available_modes:
                    # Nothing to render yet. Re-check _pending_plugin_reconcile
                    # every ~1s (rather than a long sleep) so enabling a plugin
                    # via the web UI is picked up about as promptly as it would
                    # be once modes exist and the loop is iterating per-frame.
                    self._sleep_with_plugin_updates(1)
                    continue

                # Handle on-demand commands before rendering
                self._poll_on_demand_requests()
                self._check_on_demand_expiration()
                # Unload plugins loaded only to show them on-demand once it
                # has moved off them. Here, where no display() is on the
                # stack; one ended from inside a screen is caught here on
                # the next pass.
                if self._on_demand_loaded_plugins:
                    self._release_on_demand_plugins()
                    if not self.available_modes:
                        continue  # it was all there was; idle as above
                # Unthrottled, unlike the frame loops: a plugin loaded,
                # reloaded or enabled for on-demand above is due at once.
                self._tick_plugin_updates()
                
                # Clean up expired WiFi status messages
                self._cleanup_expired_wifi_status()
                
                # Periodic memory monitoring (if enabled)
                if self._enable_memory_logging:
                    self._log_memory_stats_if_due()

                # Check the schedule
                self._evaluate_schedule()

                # Check dim schedule and apply brightness (only when display
                # is active). No repaint: this screen's first frame pushes it.
                self._apply_brightness_target()

                # Who gets the panel this pass (src/display_arbiter.py). The
                # Arbiter decides the scheduled-off gate, Follower and Wifi;
                # a LEGACY plan carries on to the code below.
                plan = Arbiter.decide(ArbiterState(), self._arbiter_inputs(), time.time())

                if plan.source is Source.SCHEDULED_OFF:
                    self._blank_while_scheduled_off(plan.max_duration)
                    continue
                
                self._publish_current_mode_state_if_changed()
                self._apply_pending_vegas_init()
                logger.debug("Display active, processing mode: %s", self.current_display_mode)
                
                # Plugins update on their own schedules - no forced sync updates needed
                # Each plugin has its own update_interval and background services
                
                # Multi-display sync: follower mode — render frames received from leader.
                # Plugin update() threads still run (via _tick_plugin_updates above) so
                # data is fresh when we return to standalone if the leader goes offline.
                if plan.source is Source.FOLLOWER:
                    self._run_follower_frame()
                    continue

                # Process any deferred updates that may have accumulated
                # This also cleans up expired updates to prevent memory leaks
                self.display_manager.process_deferred_updates()

                # WiFi status message: interrupts the rotation, but on-demand
                # outranks it (the Arbiter's order). Past this point no WiFi
                # message is showing this pass: one that failed to draw
                # carries on as a LEGACY plan.
                if (plan.source is Source.WIFI and plan.notice is not None
                        and self._show_wifi_notice(plan.notice, plan.max_duration)):
                    continue  # Skip to next iteration, don't rotate

                # Check for live priority content and switch to it immediately.
                # advance=True so multiple simultaneously-live games take turns
                # (round-robin) instead of pinning to the first plugin.
                # Skipped when the ticker is keeping live content: switching
                # the rotation underneath Vegas would move current_mode_index
                # and stash a resume point for a takeover that never happens.
                # After a mid-screen takeover (_check_live_takeover) the live
                # mode is already chosen but not shown yet: don't advance past it.
                if (not self.on_demand_active
                        and not (self._is_vegas_mode_active()
                                 and self._vegas_keeps_live_in_ticker())):
                    live_priority_mode = self._check_live_priority(
                        advance=not self._live_takeover_unshown)
                    self._apply_live_priority(live_priority_mode)
                self._live_takeover_unshown = False

                # Vegas scroll mode - continuous ticker across all plugins
                # Priority: on-demand > wifi-status > live-priority > vegas > normal rotation
                if self._is_vegas_mode_active():
                    # Live content normally preempts the ticker entirely. With
                    # vegas_scroll.live_in_ticker the marquee keeps running and
                    # the live plugin takes extra turns inside it instead --
                    # see StreamManager._apply_priority_weights.
                    live_mode = (None if self._vegas_keeps_live_in_ticker()
                                 else self._check_live_priority())
                    if not live_mode:
                        try:
                            # Run Vegas mode iteration
                            if self.vegas_coordinator.run_iteration():
                                # Vegas completed an iteration, continue to next loop
                                continue
                            else:
                                # Vegas was interrupted (live priority), fall through to normal handling
                                logger.debug("Vegas mode interrupted, falling back to normal rotation")
                                if not self.is_display_active:
                                    # Scheduled off mid-iteration: blank the
                                    # panel now rather than render a screen.
                                    continue
                                if self._plugin_reload_pending:
                                    # Reload first (top of the loop), then
                                    # the ticker carries on.
                                    continue
                                if self._wifi_notice_pending():
                                    # It yielded for a WiFi notice: the next
                                    # pass shows it, not a rotation screen
                                    # that would outlast a short notice.
                                    # Checked before live content: WiFi
                                    # outranks live, and step 7 of a later
                                    # pass switches to the game.
                                    continue
                                # Live content stopped the ticker: switch to
                                # the game now. Step 7 ran before the game
                                # went live, so without this a rotation screen
                                # showed first and the game a screen later.
                                if (not self.on_demand_active
                                        and not self._vegas_keeps_live_in_ticker()):
                                    live_mode = self._check_live_priority(advance=True)
                                    if live_mode:
                                        self._apply_live_priority(live_mode)
                        except Exception:
                            logger.exception("Vegas mode error")
                            # Fall through to normal rotation on error

                active_mode = self._resolve_active_mode()

                if self._active_dynamic_mode and self._active_dynamic_mode != active_mode:
                    self._active_dynamic_mode = None

                # DEBUG, not INFO: "Switching to mode" already logged this mode. Every
                # routine rotation line lands in the persistent journal, and on an SD
                # card each one costs far more than its bytes: measured on ledpi, about
                # 9 KB of card writes per stored line.
                logger.debug("Processing mode: %s (%d available)", active_mode, len(self.available_modes))
                logger.debug("Loaded plugin modes: %s", list(self.plugin_modes.keys()))

                # Handle plugin-based display modes
                manager_to_display = self._plugin_for_mode(active_mode)

                # Display the current mode.
                if not manager_to_display:
                    logger.warning(f"No plugin manager found for mode {active_mode} - skipping display and rotating to next mode")
                    display_result = False
                    display_failed_due_to_exception = False
                else:
                    (display_result, display_failed_due_to_exception,
                     _accepts_display_mode) = self._dispatch_first_frame(
                        manager_to_display, active_mode)

                # If display() returned False, skip to next mode immediately
                if not display_result:
                    was_on_demand = self.on_demand_active
                    self._note_empty_pass()
                    # The pause returns early when an on-demand request, its
                    # end, or the schedule decides what comes next. Rotating
                    # past this empty mode now would skip that: an on-demand
                    # start would advance past the mode just requested.
                    if (self.current_display_mode != active_mode
                            or self.on_demand_active != was_on_demand
                            or not self.is_display_active):
                        continue
                    if self.on_demand_active:
                        logger.info("No content for on-demand mode %s, skipping to next mode", active_mode)
                        if not self.on_demand_modes:
                            logger.warning("On-demand active but no modes configured, skipping rotation")
                            continue
                        self._advance_on_demand()
                        continue
                    else:
                        # Routine (no live game right now): DEBUG, see "Processing mode" above.
                        logger.debug("No content to display for %s, skipping to next mode", active_mode)
                        # Don't clear display when immediately moving to next mode - this causes black flashes
                        # The next mode will render immediately with force_clear=True, which is sufficient

                        # Only skip all modes for this plugin if there was an exception (broken plugin)
                        # If it's just "no content", we should still try other modes (recent, upcoming)
                        if (display_failed_due_to_exception
                                and self._skip_failed_plugin_modes(active_mode)):
                            # Already set next mode, skip to next iteration
                            continue
                        # If no exception (just no content), fall through to normal rotation logic
                        # This allows trying other modes (recent, upcoming) from the same plugin
                else:
                    self._empty_pass_streak = 0
                    # Get base duration for current mode
                    base_duration = self._get_display_duration(active_mode)
                    dynamic_enabled = self._plugin_supports_dynamic(manager_to_display)

                    # Log dynamic duration status
                    if dynamic_enabled:
                        logger.debug(
                            "Dynamic duration enabled for mode %s (plugin: %s)",
                            active_mode,
                            getattr(manager_to_display, "plugin_id", "unknown"),
                        )

                    self._track_dynamic_cycle(manager_to_display, active_mode, dynamic_enabled)
                    min_duration, max_duration = self._resolve_durations(
                        manager_to_display, active_mode, base_duration, dynamic_enabled)

                    bounds = self._clamp_to_on_demand(min_duration, max_duration)
                    if bounds is None:
                        self._check_on_demand_expiration()
                        continue
                    min_duration, max_duration = bounds

                    # High-FPS decision; see _needs_high_fps for the order.
                    # Read again here, after the first dispatch, as it always
                    # was: a plugin may settle it in that display() call.
                    needs_high_fps = self._needs_high_fps(manager_to_display, active_mode)

                    target_duration = max_duration
                    start_time = time.time()

                    def _should_exit_dynamic(elapsed_time: float) -> bool:
                        if not dynamic_enabled:
                            return False
                        # Add small grace period (0.5s) after min_duration to prevent
                        # premature exits due to timing issues
                        grace_period = 0.5
                        if elapsed_time < min_duration + grace_period:
                            logger.debug(
                                "_should_exit_dynamic: elapsed %.2fs < min_duration %.2fs + grace %.2fs, returning False",
                                elapsed_time,
                                min_duration,
                                grace_period,
                            )
                            return False
                        cycle_complete = self._plugin_cycle_complete(manager_to_display)
                        logger.debug(
                            "_should_exit_dynamic: elapsed %.2fs >= min %.2fs, cycle_complete=%s, returning %s",
                            elapsed_time,
                            min_duration + grace_period,
                            cycle_complete,
                            cycle_complete,
                        )
                        if cycle_complete:
                            logger.debug(
                                "Cycle complete detected for %s after %.2fs (min: %.2fs, grace: %.2fs)",
                                active_mode,
                                elapsed_time,
                                min_duration,
                                grace_period,
                            )
                        return cycle_complete

                    loop_completed = False

                    if needs_high_fps:
                        # Ultra-smooth FPS for scrolling plugins (8ms = 125 FPS)
                        display_interval = 0.008
                        logger.debug(
                            "Entering high-FPS loop for %s with display_interval=%.3fs (%.1f FPS)",
                            active_mode,
                            display_interval,
                            1.0 / display_interval
                        )

                        while True:
                            _frame_start = time.perf_counter()
                            try:
                                result = self._display_once(
                                    manager_to_display, active_mode, _accepts_display_mode)
                                if isinstance(result, bool) and not result:
                                    logger.debug("Display returned False, breaking early")
                                    break
                            except Exception:  # pylint: disable=broad-except
                                logger.exception("Error during display update")

                            # Multi-display sync: send follower frame after each render
                            self._send_follower_frame(manager_to_display)

                            self._tick_plugin_updates_if_due()
                            # Throttled: one clock compare between passes.
                            self._service_pending_changes()
                            self._check_live_takeover()

                            # Pace to the frame deadline rather than sleeping a flat
                            # interval on top of the work. display() has already
                            # blocked on the panel's vsync by this point, so an
                            # unconditional sleep is added to a wait that already
                            # happened. Measured on a 2x128x64 chain at
                            # limit_refresh_rate_hz=100: ~4ms of render plus a flat
                            # 8ms put each iteration at ~12ms against a 10ms refresh
                            # grid, so every swap missed a refresh and the loop
                            # settled at 50fps where display_interval asks for 125 --
                            # and with zero headroom, ~14% of frames slipped a
                            # further refresh, which is what reads as scroll stutter.
                            _remaining = display_interval - (time.perf_counter() - _frame_start)
                            # Yield even when the frame overran its budget, so plugin
                            # update threads and the web UI are not starved of the GIL.
                            time.sleep(_remaining if _remaining > 0 else 0.001)

                            if self._screen_preempted(active_mode):
                                logger.debug("Mode changed during high-FPS loop, breaking early")
                                break

                            elapsed = time.time() - start_time
                            if elapsed >= target_duration:
                                logger.debug(
                                    "Reached high-FPS target duration %.2fs for mode %s",
                                    target_duration,
                                    active_mode,
                                )
                                loop_completed = True
                                break
                            if _should_exit_dynamic(elapsed):
                                logger.debug(
                                    "Dynamic duration cycle complete for %s after %.2fs",
                                    active_mode,
                                    elapsed,
                                )
                                loop_completed = True
                                break
                    else:
                        # Normal FPS for other plugins (1 second)
                        display_interval = 1.0
                        logger.debug(
                            "Entering normal FPS loop for %s with display_interval=%.3fs",
                            active_mode,
                            display_interval
                        )

                        while True:
                            # Wakes for a control socket command and applies
                            # it at once, instead of up to a second later.
                            if self._wait_frame_interval(display_interval, active_mode):
                                logger.info("Mode changed during display loop from %s to %s, "
                                            "breaking early", active_mode,
                                            self.current_display_mode)
                                break
                            self._tick_plugin_updates_if_due()

                            elapsed = time.time() - start_time
                            if elapsed >= target_duration:
                                logger.debug(
                                    "Reached standard target duration %.2fs for mode %s",
                                    target_duration,
                                    active_mode,
                                )
                                loop_completed = True
                                break

                            try:
                                result = self._display_once(
                                    manager_to_display, active_mode, _accepts_display_mode)
                                if isinstance(result, bool) and not result:
                                    # For dynamic duration plugins, don't exit on False - keep looping
                                    # until cycle is complete or max duration is reached
                                    if not dynamic_enabled:
                                        logger.info("Display returned False for %s (no dynamic duration), breaking early", active_mode)
                                        break
                                    else:
                                        logger.debug("Display returned False for %s (dynamic duration enabled), continuing loop", active_mode)
                            except Exception:  # pylint: disable=broad-except
                                logger.exception("Error during display update")

                            # Multi-display sync: send follower frame after each render
                            self._send_follower_frame(manager_to_display)

                            self._service_pending_changes()
                            self._check_live_takeover()
                            if self._screen_preempted(active_mode):
                                logger.info("Mode changed during display loop from %s to %s, breaking early", active_mode, self.current_display_mode)
                                break

                            if _should_exit_dynamic(elapsed):
                                logger.info(
                                    "Dynamic duration cycle complete for %s after %.2fs",
                                    active_mode,
                                    elapsed,
                                )
                                loop_completed = True
                                break

                    # LOAD-BEARING: if current_display_mode changed mid-loop (on-demand
                    # activation, live priority, etc.), restart the main loop now instead
                    # of falling into the "honour minimum duration" sleep below. That sleep
                    # can run for up to the *previous* mode's full display_duration (default
                    # 30s) and doesn't poll on-demand requests or re-check the mode, so a
                    # freshly-requested mode switch would sit invisible for up to 30s — or
                    # get clobbered by a queued stop request — before ever rendering.
                    # _activate_on_demand already sets force_change=True and clears the
                    # display, so the next loop iteration renders the new mode immediately.
                    # Likewise if the schedule turned the display off
                    # mid-screen (the next iteration blanks it), or a WiFi
                    # notice arrived (the next iteration shows it, then this
                    # mode resumes rather than rotating past it).
                    if (self.current_display_mode != active_mode
                            or not self.is_display_active
                            or (not loop_completed and self._wifi_notice_pending())):
                        continue
                    # A screen cut short for a plugin reload is over: the
                    # make-up dwell below returns at once, the rotation
                    # advances, and the next pass reloads before it draws.

                    # Ensure we honour minimum duration when not dynamic and loop ended early
                    if (
                        not dynamic_enabled
                        and not loop_completed
                        and not needs_high_fps
                    ):
                        elapsed = time.time() - start_time
                        remaining_sleep = max(0.0, max_duration - elapsed)
                        if remaining_sleep > 0:
                            self._sleep_with_plugin_updates(remaining_sleep)
                            # Cut short by a WiFi notice: show it, then
                            # resume this mode rather than rotating past it.
                            if (self._wifi_notice_pending()
                                    and time.time() - start_time < max_duration):
                                continue

                    if dynamic_enabled:
                        elapsed_total = time.time() - start_time
                        cycle_done = self._plugin_cycle_complete(manager_to_display)
                        
                        # Log cycle completion status and metrics
                        if cycle_done:
                            logger.info(
                                "Dynamic duration cycle completed for %s after %.2fs (target: %.2fs, min: %.2fs, max: %.2fs)",
                                active_mode,
                                elapsed_total,
                                target_duration,
                                min_duration,
                                max_duration,
                            )
                        elif elapsed_total >= max_duration:
                            logger.info(
                                "Dynamic duration cap reached before cycle completion for %s (%.2fs/%ds, min: %.2fs)",
                                active_mode,
                                elapsed_total,
                                int(max_duration),
                                min_duration,
                            )
                        else:
                            logger.debug(
                                "Dynamic duration cycle in progress for %s: %.2fs elapsed (target: %.2fs, min: %.2fs, max: %.2fs)",
                                active_mode,
                                elapsed_total,
                                target_duration,
                                min_duration,
                                max_duration,
                            )

                # The dwell sleeps above return early when a pending change
                # (on-demand started or stopped, display scheduled off) has
                # already decided what comes next; rotating now would skip it
                # -- an on-demand start would advance past the requested mode.
                if (self.current_display_mode != active_mode
                        or not self.is_display_active):
                    continue

                # Move to next mode
                self._advance_after_screen(active_mode)

        except KeyboardInterrupt:
            logger.info("Received interrupt signal, shutting down...")
        except Exception:  # pylint: disable=broad-except
            logger.exception("Unexpected error in display controller")
        finally:
            self.cleanup()

    def _check_wifi_status_message(self) -> Optional[Dict[str, Any]]:
        """
        Safely check for WiFi status message file.
        
        Returns:
            Dict with 'message', 'timestamp', 'duration' if valid message exists, None otherwise.
            Returns None on any error or if message is expired/invalid.
        """
        try:
            # Throttle the existence stat to ~1 Hz: this runs on every render
            # iteration (60+ fps), and the file usually doesn't exist — the
            # status message's lifetime is measured in seconds anyway.
            # Both attributes are initialised in __init__.
            now = time.time()
            if (now - self._wifi_status_check_ts) < 1.0:
                return self._wifi_status_last_result
            self._wifi_status_check_ts = now
            self._wifi_status_last_result = None

            # Check if file exists
            if not self.wifi_status_file or not self.wifi_status_file.exists():
                return None
            
            # Read and parse JSON file
            try:
                with open(self.wifi_status_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
            except (json.JSONDecodeError, IOError, OSError) as e:
                logger.debug(f"Error reading WiFi status file (will be cleaned up): {e}")
                # Clean up corrupted file
                try:
                    self.wifi_status_file.unlink()
                except Exception:
                    logger.debug("Could not remove WiFi status file %s",
                                 self.wifi_status_file, exc_info=True)
                return None
            
            # Validate required fields
            if not isinstance(data, dict):
                logger.debug("WiFi status file contains invalid data (not a dict)")
                return None
            
            message = data.get('message')
            timestamp = data.get('timestamp')
            duration = data.get('duration', 5)
            
            if not message or not isinstance(message, str):
                logger.debug("WiFi status file missing or invalid message field")
                return None
            
            if not isinstance(timestamp, (int, float)) or timestamp <= 0:
                logger.debug("WiFi status file missing or invalid timestamp field")
                return None
            
            if not isinstance(duration, (int, float)) or duration < 0:
                duration = 5  # Default to 5 seconds if invalid
            
            # Check if message has expired
            current_time = time.time()
            expires_at = timestamp + duration
            
            if current_time >= expires_at:
                logger.debug(f"WiFi status message expired (age: {current_time - timestamp:.1f}s, duration: {duration}s)")
                # Clean up expired file
                try:
                    self.wifi_status_file.unlink()
                except Exception:
                    logger.debug("Could not remove WiFi status file %s",
                                 self.wifi_status_file, exc_info=True)
                return None
            
            # Message is valid and not expired — cache for the throttle window
            self._wifi_status_last_result = {
                'message': message,
                'timestamp': timestamp,
                'duration': duration,
                'expires_at': expires_at
            }
            return self._wifi_status_last_result
            
        except Exception as e:
            # Catch-all for any unexpected errors - log but don't break the display
            logger.debug(f"Unexpected error checking WiFi status message: {e}")
            return None
    
    def _display_wifi_status_message(self, status_data: Dict[str, Any]) -> bool:
        """
        Safely display a WiFi status message on the LED matrix.
        
        Args:
            status_data: Dict with 'message', 'expires_at' from _check_wifi_status_message()
        
        Returns:
            True if message was displayed successfully, False otherwise.
        """
        try:
            message = status_data.get('message', '')
            if not message:
                return False
            
            # Clear display
            self.display_manager.clear()
            
            # Get display dimensions for centering
            width = self.display_manager.width
            height = self.display_manager.height
            
            # Split long messages into multiple lines if needed
            # Simple word wrapping for messages longer than ~20 characters
            max_chars_per_line = min(20, width // 6)  # Rough estimate based on font width
            words = message.split()
            lines = []
            current_line = []
            current_length = 0
            
            for word in words:
                word_length = len(word) + 1  # +1 for space
                if current_length + word_length > max_chars_per_line and current_line:
                    lines.append(' '.join(current_line))
                    current_line = [word]
                    current_length = len(word)
                else:
                    current_line.append(word)
                    current_length += word_length
            
            if current_line:
                lines.append(' '.join(current_line))
            
            # Limit to 2 lines max (for small displays)
            lines = lines[:2]
            
            # Calculate vertical spacing
            font_height = self.display_manager.get_font_height(self.display_manager.small_font)
            total_height = len(lines) * font_height
            start_y = max(0, (height - total_height) // 2)
            
            # Draw each line
            for i, line in enumerate(lines):
                y_pos = start_y + (i * font_height)
                # Use small font and center horizontally
                self.display_manager.draw_text(
                    line,
                    y=y_pos,
                    color=(255, 255, 255),  # White text
                    small_font=True
                )
            
            # Update display
            self.display_manager.update_display()
            
            # Track that WiFi status is active
            self.wifi_status_active = True
            self.wifi_status_expires_at = status_data.get('expires_at')
            
            logger.debug(f"Displayed WiFi status message: {message[:50]}")
            return True
            
        except Exception as e:
            # Catch-all for any display errors - log but don't break
            logger.warning(f"Error displaying WiFi status message: {e}")
            self.wifi_status_active = False
            self.wifi_status_expires_at = None
            return False
    
    def _cleanup_expired_wifi_status(self):
        """Safely clean up expired WiFi status message file."""
        try:
            if self.wifi_status_active and self.wifi_status_expires_at:
                current_time = time.time()
                if current_time >= self.wifi_status_expires_at:
                    # Message has expired, clean up
                    if self.wifi_status_file and self.wifi_status_file.exists():
                        try:
                            self.wifi_status_file.unlink()
                            logger.debug("Cleaned up expired WiFi status message file")
                        except Exception as e:
                            logger.debug(f"Could not delete WiFi status file: {e}")
                    
                    self.wifi_status_active = False
                    self.wifi_status_expires_at = None
        except Exception as e:
            logger.debug(f"Error cleaning up WiFi status: {e}")
            # Reset state on any error
            self.wifi_status_active = False
            self.wifi_status_expires_at = None

    def _register_loaded_plugin(self, plugin_id: str) -> List[str]:
        """Register an already-loaded plugin's display modes, config-change
        subscription and dispatch maps with the controller.

        Shared by startup loading and live enable hot-reload so both paths
        build identical controller state. Returns the registered modes.
        """
        plugin_instance = self.plugin_manager.get_plugin(plugin_id)
        manifest = self.plugin_manager.plugin_manifests.get(plugin_id, {})

        # Prefer the plugin's dynamic modes attribute (e.g. based on enabled
        # leagues), else fall back to manifest display_modes, else the id.
        if plugin_instance is not None and getattr(plugin_instance, 'modes', None):
            display_modes = list(plugin_instance.modes)
            logger.debug("Using plugin.modes for %s: %s", plugin_id, display_modes)
        else:
            display_modes = manifest.get('display_modes', [plugin_id])
            logger.debug("Using manifest display_modes for %s: %s", plugin_id, display_modes)
        if not (isinstance(display_modes, list) and display_modes):
            display_modes = [plugin_id]
        with self._plugin_modes_lock:
            self.plugin_display_modes[plugin_id] = list(display_modes)

        # Subscribe to config changes for per-plugin hot-reload. Bind plugin_id
        # and instance as defaults so each plugin's callback targets its own
        # instance (avoids late-binding when registering many plugins), and
        # remember the callback so we can unsubscribe on disable.
        if hasattr(self, 'config_service') and hasattr(plugin_instance, 'on_config_change'):
            def config_change_callback(old_config: Dict[str, Any], new_config: Dict[str, Any],
                                       _pid: str = plugin_id, _plugin: Any = plugin_instance) -> None:
                """Callback for plugin config changes."""
                try:
                    # ConfigService hands over the raw config.json section.
                    # Prepare it as loading did (legacy booleans read as
                    # objects, schema defaults filled in), so the plugin gets
                    # the same shape it was constructed with.
                    prepare = getattr(self.plugin_manager, 'prepare_plugin_config', None)
                    prepared = prepare(_pid, new_config) if callable(prepare) else None
                    if isinstance(prepared, dict):
                        new_config = prepared
                    if _pid in self._on_demand_loaded_plugins:
                        # Saved while on-demand shows it: config.json still
                        # says disabled, and on_config_change would switch
                        # the instance off mid-session.
                        new_config = {**new_config, 'enabled': True}
                    # Runs on ConfigService's watcher thread. Under the
                    # plugin's lock, so it cannot interleave with update()
                    # on the worker or display() on the render thread; a
                    # lock held past the bound defers it to the worker.
                    apply = getattr(self.plugin_manager, 'apply_config_change', None)
                    if callable(apply):
                        applied = apply(_pid, new_config, plugin_instance=_plugin)
                    else:
                        _plugin.on_config_change(new_config)
                        applied = True
                    logger.debug("Plugin %s notified of config change%s", _pid,
                                 "" if applied else " (deferred: plugin busy)")
                except Exception as e:
                    logger.error("Error in plugin %s config change handler: %s", _pid, e, exc_info=True)

            self.config_service.subscribe(config_change_callback, plugin_id=plugin_id)
            self._plugin_config_callbacks[plugin_id] = config_change_callback
            logger.debug("Subscribed plugin %s to config changes", plugin_id)

        # Add modes to the dispatch maps.
        for mode in display_modes:
            if mode not in self.available_modes:
                self.available_modes.append(mode)
            self.plugin_modes[mode] = plugin_instance
            self.mode_to_plugin_id[mode] = plugin_id
            logger.debug("  Added mode: %s", mode)
        # Invalidate signature cache so the new instance is re-inspected.
        self._plugin_accepts_display_mode.pop(plugin_id, None)
        return display_modes

    def _unregister_plugin(self, plugin_id: str, action: str = 'Disabled',
                           unload: bool = True) -> None:
        """Remove a plugin's modes, config subscription and instance, then
        unload it. Used by live disable hot-reload, and by a reload
        (``action`` names which in the log line), which passes
        ``unload=False``: it hands the instance to its plugin-reload thread
        to unload instead (_start_plugin_reload)."""
        with self._plugin_modes_lock:
            modes = self.plugin_display_modes.pop(plugin_id, [])
        for mode in modes:
            if mode in self.available_modes:
                self.available_modes.remove(mode)
            self.plugin_modes.pop(mode, None)
            self.mode_to_plugin_id.pop(mode, None)

        # Unsubscribe the plugin's config-change callback. Pop only on a
        # successful unsubscribe -- if it raises, keep our reference so a
        # later retry (or at least cleanup) still has the real callback
        # instead of a lost one.
        callback = self._plugin_config_callbacks.get(plugin_id)
        if callback is not None and hasattr(self, 'config_service'):
            try:
                self.config_service.unsubscribe(callback, plugin_id=plugin_id)
            except Exception as e:
                logger.debug("Error unsubscribing plugin %s from config changes: %s", plugin_id, e)
            else:
                self._plugin_config_callbacks.pop(plugin_id, None)
        else:
            self._plugin_config_callbacks.pop(plugin_id, None)

        self._plugin_accepts_display_mode.pop(plugin_id, None)

        # Tear down the instance (cleanup + on_disable + module unload).
        if unload:
            try:
                self.plugin_manager.unload_plugin(plugin_id)
            except Exception as e:
                logger.error("Error unloading plugin %s: %s", plugin_id, e, exc_info=True)

        logger.info("%s plugin %s live (removed modes: %s)", action, plugin_id, modes)

    def _enabled_set_changed(self, old_config: Dict[str, Any], new_config: Dict[str, Any]) -> bool:
        """True if any top-level section's ``enabled`` flag differs between two
        configs. A cheap watcher-thread check that gates the full reconcile.
        Non-plugin sections (e.g. schedule) may match too; the reconcile
        no-ops for anything that isn't a discovered plugin."""
        def enabled_map(cfg: Dict[str, Any]) -> Dict[str, bool]:
            return {
                key: bool(value.get('enabled', False))
                for key, value in cfg.items()
                if isinstance(value, dict)
            }
        return enabled_map(old_config) != enabled_map(new_config)

    def _service_pending_reconcile(self) -> None:
        """Consume a pending reconcile request and run it.

        The request is consumed BEFORE reconciling, not cleared after. Clearing
        after would drop any config change that lands while reconcile is
        running: reconcile has already read its config by then, so the clear
        erases a request it never served and the newest config never
        reconciles -- the same "your save did nothing" failure this whole path
        exists to prevent. Consuming first means such a request stays set and
        is picked up on the next pass.

        A retryable failure (e.g. discovery) re-arms the flag.
        """
        with self._reconcile_flag_lock:
            pending = self._pending_plugin_reconcile
            self._pending_plugin_reconcile = False
        if pending and not self._reconcile_enabled_plugins():
            with self._reconcile_flag_lock:
                self._pending_plugin_reconcile = True

    def _enabled_plugin_not_running(self, new_config: Dict[str, Any]) -> bool:
        """True when a discovered plugin is enabled in config but not running.

        ``_enabled_set_changed`` compares only top-level ``enabled`` flags, which
        misses the case that strands a plugin: one whose ``validate_config()``
        returned False is absent from the running set, and the edit that fixes it
        (enabling a league, filling in an API key) lives *nested* inside that
        plugin's own section. No top-level flag changes, so no reconcile is
        queued, and the save that should have fixed it appears to do nothing --
        only toggling some unrelated plugin recovers it. hockey-scoreboard sat
        enabled-but-absent on a live rig for four days this way.

        Deliberately narrow: it fires only for ids the plugin manager has
        actually discovered, so non-plugin sections that carry their own
        ``enabled`` flag (``schedule``, ``display``, ...) don't queue a reconcile
        on every save. In the steady state -- everything enabled is loaded --
        this is False and costs nothing. That matters because reconcile runs
        ``discover_plugins()`` on the render thread, where a needless
        filesystem scan per config save would show up as a frame hitch.

        Runs on the config-watcher thread, so both mappings it reads are
        snapshotted under the lock that guards their writes.
        """
        if self.plugin_manager is None:
            return False
        # Two snapshots, each taken under its own lock and never nested, so a
        # half-written mapping is never observed and this can't deadlock
        # against discovery (which holds the discovery lock while rebuilding).
        try:
            known = self.plugin_manager.discovered_plugin_ids()
        except AttributeError:
            # Older manager without the accessor: fall back to a plain read.
            known = set(getattr(self.plugin_manager, 'plugin_manifests', ()) or ())
        with self._plugin_modes_lock:
            running = set(self.plugin_display_modes)
        # Out of plugin_display_modes only while a reload loads it again.
        running.update(job.plugin_id for job in self._plugin_reload_jobs)
        for key, value in new_config.items():
            if (key in known and isinstance(value, dict)
                    and value.get('enabled', False) and key not in running):
                return True
        return False

    def _reconcile_enabled_plugins(self) -> bool:
        """Load/unload plugins so the running set matches the enabled set in
        config. Runs on the main display thread (never the config-watcher
        thread) so mutating available_modes is race-free against rendering.

        Returns True if reconciliation completed (including a no-op), or
        False on a retryable failure -- the caller keeps the pending-reconcile
        flag set in that case so the request isn't silently dropped."""
        if self.plugin_manager is None:
            return True
        try:
            config = self.config_service.get_config()
        except Exception as e:
            logger.warning("Plugin reconcile: falling back to cached config: %s", e)
            config = self.config
        try:
            discovered = set(self.plugin_manager.discover_plugins())
        except Exception as e:
            logger.error("Plugin reconcile: discovery failed: %s", e, exc_info=True)
            return False

        for p in discovered:
            if p in config and not isinstance(config.get(p), dict):
                logger.warning(
                    "Plugin reconcile: config for %s is a %s, not a dict; treating as disabled",
                    p, type(config.get(p)).__name__
                )

        desired = {
            p for p in discovered
            if isinstance(config.get(p), dict) and config.get(p, {}).get('enabled', False)
        }
        # A plugin being reloaded counts as running, so it is not loaded a
        # second time beside its plugin-reload thread. One disabled during
        # its reload is unloaded by a reconcile run after the reload is done.
        reloading = {job.plugin_id for job in self._plugin_reload_jobs}
        current = set(self.plugin_display_modes.keys()) | reloading
        to_add = desired - current
        to_remove = current - desired
        if to_remove & reloading:
            logger.info("Plugin reconcile: %s will be unloaded after its reload",
                        sorted(to_remove & reloading))
            self._reconcile_after_reload = True
            to_remove -= reloading
        if not to_add and not to_remove:
            return True

        previous_mode = self.current_display_mode

        for plugin_id in to_remove:
            self._unregister_plugin(plugin_id)

        for plugin_id in to_add:
            try:
                if self.plugin_manager.load_plugin(plugin_id):
                    modes = self._register_loaded_plugin(plugin_id)
                    logger.info("Enabled plugin %s live (modes: %s)", plugin_id, modes)
                else:
                    logger.warning("Plugin reconcile: failed to load %s", plugin_id)
            except Exception as e:
                logger.error("Plugin reconcile: error enabling %s: %s", plugin_id, e, exc_info=True)

        # Newly enabled plugins were appended at the end; put them in the
        # configured rotation slot before resyncing the index.
        self._apply_plugin_rotation_order()
        self._resync_mode_index_after_change(previous_mode)
        logger.info("[DisplayController] Plugin reconcile complete: +%s -%s (%d modes)",
                    sorted(to_add), sorted(to_remove), len(self.available_modes))
        return True

    def _apply_plugin_rotation_order(self) -> None:
        """Reorder available_modes to follow display.plugin_rotation_order.

        The configured value is a list of plugin ids; their modes rotate in
        that order (each plugin's own modes keep their declared order), with
        any enabled-but-unlisted plugins appended afterwards in their current
        relative order. An empty/missing list leaves available_modes exactly
        as built (today's behavior). Mirrors vegas_mode/config.py's
        get_ordered_plugins() semantics for the primary rotation.
        """
        configured = (self.config.get("display", {}) or {}).get("plugin_rotation_order", []) or []
        # Defensive: hand-edited or migrated configs may hold a non-list or
        # non-string entries; keep the existing rotation rather than applying
        # a garbage order.
        if not isinstance(configured, list):
            logger.warning("[DisplayController] Ignoring invalid plugin_rotation_order (not a list): %r",
                           type(configured).__name__)
            return
        configured = [p for p in configured if isinstance(p, str)]
        if not configured or not self.available_modes:
            return

        ordered_ids = [p for p in configured if p in self.plugin_display_modes]
        new_modes: List[str] = []
        for plugin_id in ordered_ids:
            for mode in self.plugin_display_modes[plugin_id]:
                if mode in self.available_modes and mode not in new_modes:
                    new_modes.append(mode)
        # Unlisted plugins' modes (and any mode not attributable to a plugin)
        # follow in their existing relative order.
        for mode in self.available_modes:
            if mode not in new_modes:
                new_modes.append(mode)
        if new_modes != self.available_modes:
            self.available_modes = new_modes
            logger.info("[DisplayController] Applied plugin rotation order %s -> modes: %s",
                        configured, self.available_modes)

    def _resync_mode_index_after_change(self, previous_mode: Optional[str]) -> None:
        """Clamp rotation state after available_modes changed. Stays on the
        previous mode if it survived, otherwise restarts cleanly within range."""
        if not self.available_modes:
            self.current_mode_index = 0
            self.current_display_mode = None
            return
        if previous_mode in self.available_modes:
            self.current_mode_index = self.available_modes.index(previous_mode)
        else:
            self.current_mode_index %= len(self.available_modes)
            self.current_display_mode = self.available_modes[self.current_mode_index]

    def _controller_config_change(self, old_config: Dict[str, Any], new_config: Dict[str, Any]) -> None:
        """ConfigService subscriber: runs on the config-watcher thread."""
        self._refresh_config_cache(new_config)
        # Vegas keeps its own parsed copy of display.vegas_scroll. Queue the
        # new one only when it changed: applying it rebuilds the strip.
        # (getattr: this can fire before __init__ creates the coordinator.)
        vegas = getattr(self, 'vegas_coordinator', None)
        new_vegas = (new_config.get('display', {}) or {}).get('vegas_scroll')
        if vegas is not None and (
                (old_config.get('display', {}) or {}).get('vegas_scroll') != new_vegas):
            vegas.update_config(new_config)
        elif vegas is None and (new_vegas or {}).get('enabled', False):
            # No coordinator yet because Vegas was off at startup. Creating
            # one here would race the render thread; flag it instead.
            self._pending_vegas_init = True
        # If a plugin was enabled/disabled, flag a reconcile for the main
        # loop to apply (loading/unloading off the watcher thread is unsafe).
        if (self._enabled_set_changed(old_config, new_config)
                or self._enabled_plugin_not_running(new_config)):
            with self._reconcile_flag_lock:
                self._pending_plugin_reconcile = True

    def _refresh_config_cache(self, new_config: Dict[str, Any]) -> None:
        """Refresh all config-derived caches when a hot-reload fires.

        Called by the controller-level ConfigService subscriber.  Keeps
        ``_normal_brightness``, ``_scroll_speed``, the cached timezone, and the
        schedule minute-gates consistent with the live config so callers never
        read stale values after the user saves settings via the web UI.
        """
        self.config = new_config
        # A no-op unless the fetch_service section itself changed.
        from src.common.fetch_service import configure_fetch_service
        configure_fetch_service(new_config.get('fetch_service'))
        self._normal_brightness = (
            self.config.get('display', {}).get('hardware', {}).get('brightness', 90)
        )
        self._scroll_speed = self._vegas_scroll_speed(self.config)
        # Force the timezone to be re-derived from the new config on next schedule check
        self._tz = None
        # Invalidate minute-gates so the new schedule/dim times take effect immediately
        self._schedule_checked_minute = None
        self._dim_checked_minute = None
        self._cached_target_brightness = self._normal_brightness
        logger.debug("Config cache refreshed (brightness=%s, scroll_speed=%s)",
                     self._normal_brightness, self._scroll_speed)

    @staticmethod
    def _vegas_scroll_speed(config: Dict[str, Any]) -> float:
        """Vegas scroll speed in px/s, with VegasModeConfig's default.

        The default has to be the one Vegas itself uses: a follower
        dead-reckons with this value between the leader's position packets,
        so a different default makes it run at the wrong speed.
        """
        from src.vegas_mode.config import VegasModeConfig
        vegas_cfg = (config.get('display', {}) or {}).get('vegas_scroll', {}) or {}
        return float(vegas_cfg.get('scroll_speed', VegasModeConfig.scroll_speed))

    def cleanup(self):
        """Clean up resources."""
        # First: a clean stop is not a hang, and a heartbeat left behind
        # would read as a frozen panel to the web interface.
        display_watchdog.watchdog.stopping()
        # Stop taking commands; the socket file goes with it.
        if self._control_server is not None:
            try:
                self._control_server.close()
            except Exception as e:
                logger.warning("Error closing the control socket: %s", e)
            self._control_server = None
            self._state_hub = None
        # Stop the async update worker first so no in-flight update() call
        # is still touching display/cache-backed resources while they're
        # torn down below.
        if self.plugin_manager:
            try:
                self.plugin_manager.stop_update_worker()
            except Exception as e:
                logger.warning("Error stopping plugin update worker: %s", e)
        # Vegas is torn down before the display manager: stopping it resets
        # the display's scrolling state.
        if self.vegas_coordinator is not None:
            try:
                self.vegas_coordinator.cleanup()
            except Exception as e:
                logger.warning("Error cleaning up Vegas mode: %s", e)
        # After Vegas, which sends through it. Stopping also withdraws the
        # sync status file, which the web UI otherwise kept showing as live.
        if getattr(self, 'sync_manager', None) is not None:
            try:
                self.sync_manager.stop()
            except Exception as e:
                logger.warning("Error stopping display sync: %s", e)
        # Shutdown config service if it exists
        if hasattr(self, 'config_service'):
            try:
                self.config_service.shutdown()
            except Exception as e:
                logger.warning("Error shutting down config service: %s", e)
        if getattr(self, '_font_usage_publisher', None) is not None:
            self._font_usage_publisher.stop()
        if getattr(self, '_fetch_stats_publisher', None) is not None:
            try:
                self._fetch_stats_publisher.stop()
            except Exception as e:
                logger.warning("Error stopping the fetch statistics publisher: %s", e)
        # Publishes "stopped", so the web UI stops reporting what was loaded.
        if getattr(self, '_plugin_runtime_publisher', None) is not None:
            try:
                self._plugin_runtime_publisher.stop()
            except Exception as e:
                logger.warning("Error stopping the plugin runtime publisher: %s", e)
        logger.info("Cleaning up display controller...")
        if hasattr(self, 'display_manager'):
            self.display_manager.cleanup()
        logger.info("Cleanup complete.")

def _raise_keyboard_interrupt(signum, frame):
    """SIGTERM handler: stop the way Ctrl-C does.

    systemd stops ledmatrix.service with SIGTERM. Python's default action for
    it ends the process at once, so run()'s ``finally: self.cleanup()`` --
    which stops the update worker, tears down Vegas and clears the panel --
    never ran. Raising KeyboardInterrupt sends SIGTERM down that same path.
    """
    raise KeyboardInterrupt


def main():
    """Application entry point — create a DisplayController and run until interrupted."""
    controller = DisplayController()
    # Installed after construction: a SIGTERM while plugins are still loading
    # keeps the default immediate exit rather than waiting for the loads.
    signal.signal(signal.SIGTERM, _raise_keyboard_interrupt)
    controller.run()

if __name__ == "__main__":
    main()
