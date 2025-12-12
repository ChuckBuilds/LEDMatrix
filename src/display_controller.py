import time
import logging
import sys
import os
from typing import Dict, Any, List, Optional
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed  # pylint: disable=no-name-in-module

# Core system imports only - all functionality now handled via plugins
from src.display_manager import DisplayManager
from src.config_manager import ConfigManager
from src.config_service import ConfigService
from src.cache_manager import CacheManager
from src.font_manager import FontManager
from src.logging_config import get_logger

# Get logger with consistent configuration
logger = get_logger(__name__)
DEFAULT_DYNAMIC_DURATION_CAP = 180.0

class DisplayController:
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
        self.config_manager = config_manager  # Keep for backward compatibility
        self.config = self.config_service.get_config()
        self.cache_manager = CacheManager()
        logger.info("Config loaded in %.3f seconds (hot-reload: %s)", time.time() - start_time, enable_hot_reload)
        
        # Validate startup configuration
        try:
            from src.startup_validator import StartupValidator
            validator = StartupValidator(self.config_manager)
            is_valid, errors, warnings = validator.validate_all()
            
            if warnings:
                for warning in warnings:
                    logger.warning(f"Startup validation warning: {warning}")
            
            if not is_valid:
                error_msg = "Startup validation failed:\n" + "\n".join(f"  - {e}" for e in errors)
                logger.error(error_msg)
                # For now, log errors but continue - can be made stricter later
                # validator.raise_on_errors()  # Uncomment to fail fast on errors
        except Exception as e:
            logger.warning(f"Startup validation could not be completed: {e}")
        
        config_time = time.time()
        self.display_manager = DisplayManager(self.config)
        logger.info("DisplayManager initialized in %.3f seconds", time.time() - config_time)
        
        # Initialize Font Manager
        font_time = time.time()
        self.font_manager = FontManager(self.config)
        logger.info("FontManager initialized in %.3f seconds", time.time() - font_time)
        
        # Initialize display modes - all functionality now handled via plugins
        init_time = time.time()
        
        # All other functionality handled via plugins
        logger.info("Display modes initialized in %.3f seconds", time.time() - init_time)
        
        self.force_change = False
        
        # All sports and content managers now handled via plugins
        logger.info("All sports and content managers now handled via plugin system")
        
        # List of available display modes - now handled entirely by plugins
        self.available_modes = []
        
        # Initialize Plugin System
        plugin_time = time.time()
        self.plugin_manager = None
        self.plugin_modes = {}  # mode -> plugin_instance mapping for plugin-first dispatch
        self.mode_to_plugin_id: Dict[str, str] = {}
        self.plugin_display_modes: Dict[str, List[str]] = {}
        self.on_demand_active = False
        self.on_demand_mode: Optional[str] = None
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
        self.rotation_resume_index: Optional[int] = None
        
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
                # If relative, resolve relative to the project root (LEDMatrix directory)
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
            
            # Validate plugins after plugin manager is created
            try:
                from src.startup_validator import StartupValidator
                validator = StartupValidator(self.config_manager, self.plugin_manager)
                is_valid, errors, warnings = validator.validate_all()
                
                if warnings:
                    for warning in warnings:
                        logger.warning(f"Plugin validation warning: {warning}")
                
                if not is_valid:
                    error_msg = "Plugin validation failed:\n" + "\n".join(f"  - {e}" for e in errors)
                    logger.error(error_msg)
            except Exception as e:
                logger.warning(f"Plugin validation could not be completed: {e}")

            # Discover plugins
            discovered_plugins = self.plugin_manager.discover_plugins()
            logger.info("Discovered %d plugin(s)", len(discovered_plugins))

            # Count enabled plugins for progress tracking
            enabled_plugins = [p for p in discovered_plugins if self.config.get(p, {}).get('enabled', False)]
            enabled_count = len(enabled_plugins)
            logger.info("Loading %d enabled plugin(s) in parallel (max 4 concurrent)...", enabled_count)
            
            # Helper function for parallel loading
            def load_single_plugin(plugin_id):
                """Load a single plugin and return result."""
                plugin_load_start = time.time()
                try:
                    if self.plugin_manager.load_plugin(plugin_id):
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
                    for plugin_id in discovered_plugins
                    if self.config.get(plugin_id, {}).get('enabled', False)
                }
                
                # Process results as they complete
                for future in as_completed(future_to_plugin):
                    result = future.result()
                    loaded_count += 1
                    
                    if result['success']:
                        plugin_id = result['plugin_id']
                        logger.info("✓ Loaded plugin %s in %.3f seconds (%d/%d)", 
                                  plugin_id, result['load_time'], loaded_count, enabled_count)
                        
                        # Get plugin instance and manifest
                        plugin_instance = self.plugin_manager.get_plugin(plugin_id)
                        manifest = self.plugin_manager.plugin_manifests.get(plugin_id, {})
                        display_modes = manifest.get('display_modes', [plugin_id])
                        if isinstance(display_modes, list) and display_modes:
                            self.plugin_display_modes[plugin_id] = list(display_modes)
                        else:
                            display_modes = [plugin_id]
                            self.plugin_display_modes[plugin_id] = list(display_modes)
                        
                        # Subscribe plugin to config changes for hot-reload
                        if hasattr(self, 'config_service') and hasattr(plugin_instance, 'on_config_change'):
                            def config_change_callback(old_config: Dict[str, Any], new_config: Dict[str, Any]) -> None:
                                """Callback for plugin config changes."""
                                try:
                                    plugin_instance.on_config_change(new_config)
                                    logger.debug("Plugin %s notified of config change", plugin_id)
                                except Exception as e:
                                    logger.error("Error in plugin %s config change handler: %s", plugin_id, e, exc_info=True)
                            
                            self.config_service.subscribe(config_change_callback, plugin_id=plugin_id)
                            logger.debug("Subscribed plugin %s to config changes", plugin_id)
                        
                        # Add plugin modes to available modes
                        for mode in display_modes:
                            self.available_modes.append(mode)
                            self.plugin_modes[mode] = plugin_instance
                            self.mode_to_plugin_id[mode] = plugin_id
                            logger.debug("  Added mode: %s", mode)
                        
                        # Show progress
                        progress_pct = int((loaded_count / enabled_count) * 100)
                        elapsed = time.time() - plugin_time
                        logger.info("Progress: %d%% (%d/%d plugins, %.1fs elapsed)", 
                                  progress_pct, loaded_count, enabled_count, elapsed)
                    else:
                        logger.warning("✗ Failed to load plugin %s: %s", 
                                     result['plugin_id'], result['error'])
            
            # Log disabled plugins
            disabled_count = len(discovered_plugins) - enabled_count
            if disabled_count > 0:
                logger.debug("%d plugin(s) disabled in config", disabled_count)

            logger.info("Plugin system initialized in %.3f seconds", time.time() - plugin_time)
            logger.info("Total available modes: %d", len(self.available_modes))
            logger.info("Available modes: %s", self.available_modes)

        except Exception:  # pylint: disable=broad-except
            logger.exception("Plugin system initialization failed")
            self.plugin_manager = None

        # Display rotation state
        self.current_mode_index = 0
        self.current_display_mode = None
        self.last_mode_change = time.time()
        self.mode_duration = 30  # Default duration
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
        
        # Publish initial on-demand state
        try:
            self._publish_on_demand_state()
        except (OSError, ValueError, RuntimeError) as err:
            logger.debug("Initial on-demand state publish failed: %s", err, exc_info=True)

        # Initial data update for plugins (ensures data available on first display)
        logger.info("Performing initial plugin data update...")
        update_start = time.time()
        self._update_modules()
        logger.info("Initial plugin update completed in %.3f seconds", time.time() - update_start)

        logger.info("DisplayController initialization completed in %.3f seconds", time.time() - start_time)

    def _check_schedule(self):
        """Check if display should be active based on schedule."""
        schedule_config = self.config.get('schedule', {})
        if not schedule_config.get('enabled', True):
            self.is_display_active = True
            return
            
        current_time = datetime.now()
        current_day = current_time.strftime('%A').lower()  # Get day name (monday, tuesday, etc.)
        current_time_only = current_time.time()
        
        # Check if per-day schedule is configured
        days_config = schedule_config.get('days')
        
        if days_config and current_day in days_config:
            # Use per-day schedule
            day_config = days_config[current_day]
            
            # Check if this day is enabled
            if not day_config.get('enabled', True):
                self.is_display_active = False
                logger.debug("Display inactive - %s is disabled in schedule", current_day)
                return
            
            start_time_str = day_config.get('start_time', '07:00')
            end_time_str = day_config.get('end_time', '23:00')
        else:
            # Use global schedule
            start_time_str = schedule_config.get('start_time', '07:00')
            end_time_str = schedule_config.get('end_time', '23:00')
        
        try:
            start_time = datetime.strptime(start_time_str, '%H:%M').time()
            end_time = datetime.strptime(end_time_str, '%H:%M').time()
            
            if start_time <= end_time:
                # Normal case: start and end on same day
                self.is_display_active = start_time <= current_time_only <= end_time
            else:
                # Overnight case: start and end on different days
                self.is_display_active = current_time_only >= start_time or current_time_only <= end_time
                
        except ValueError as e:
            logger.warning("Invalid schedule format: %s", e)
            self.is_display_active = True

    def _update_modules(self):
        """Update all plugin modules."""
        if not self.plugin_manager:
            return
            
        # Update all loaded plugins
        plugins_dict = getattr(self.plugin_manager, 'loaded_plugins', None) or getattr(self.plugin_manager, 'plugins', {})
        for plugin_id, plugin_instance in plugins_dict.items():
            # Check circuit breaker before attempting update
            if hasattr(self.plugin_manager, 'health_tracker') and self.plugin_manager.health_tracker:
                if self.plugin_manager.health_tracker.should_skip_plugin(plugin_id):
                    logger.debug(f"Skipping update for plugin {plugin_id} due to circuit breaker")
                    continue
            
            # Use PluginExecutor if available for safe execution
            if hasattr(self.plugin_manager, 'plugin_executor'):
                success = self.plugin_manager.plugin_executor.execute_update(plugin_instance, plugin_id)
                if success and hasattr(self.plugin_manager, 'plugin_last_update'):
                    self.plugin_manager.plugin_last_update[plugin_id] = time.time()
            else:
                # Fallback to direct call
                try:
                    if hasattr(plugin_instance, 'update'):
                        plugin_instance.update()
                        if hasattr(self.plugin_manager, 'plugin_last_update'):
                            self.plugin_manager.plugin_last_update[plugin_id] = time.time()
                        # Record success
                        if hasattr(self.plugin_manager, 'health_tracker') and self.plugin_manager.health_tracker:
                            self.plugin_manager.health_tracker.record_success(plugin_id)
                except Exception as exc:  # pylint: disable=broad-except
                    logger.exception("Error updating plugin %s", plugin_id)
                    # Record failure
                    if hasattr(self.plugin_manager, 'health_tracker') and self.plugin_manager.health_tracker:
                        self.plugin_manager.health_tracker.record_failure(plugin_id, exc)

    def _tick_plugin_updates(self):
        """Run scheduled plugin updates if the plugin manager supports them."""
        if not self.plugin_manager:
            return

        if hasattr(self.plugin_manager, "run_scheduled_updates"):
            try:
                self.plugin_manager.run_scheduled_updates()
            except Exception:  # pylint: disable=broad-except
                logger.exception("Error running scheduled plugin updates")

    def _sleep_with_plugin_updates(self, duration: float, tick_interval: float = 1.0):
        """Sleep while continuing to service plugin update schedules."""
        if duration <= 0:
            return

        end_time = time.time() + duration
        tick_interval = max(0.001, tick_interval)

        while True:
            remaining = end_time - time.time()
            if remaining <= 0:
                break

            sleep_time = min(tick_interval, remaining)
            time.sleep(sleep_time)
            self._tick_plugin_updates()

    def _get_display_duration(self, mode_key):
        """Get display duration for a mode."""
        # Check plugin-specific duration first
        if mode_key in self.plugin_modes:
            plugin_instance = self.plugin_modes[mode_key]
            if hasattr(plugin_instance, 'get_display_duration'):
                return plugin_instance.get_display_duration()
        
        # Fall back to config
        display_durations = self.config.get('display', {}).get('display_durations', {})
        return display_durations.get(mode_key, 30)

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
        if not self.on_demand_active or self.on_demand_expires_at is None:
            return None
        remaining = self.on_demand_expires_at - time.time()
        return max(0.0, remaining)

    def _publish_on_demand_state(self) -> None:
        """Publish current on-demand state to cache for external consumers."""
        try:
            state = {
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
            self.cache_manager.set('display_on_demand_state', state)
        except (OSError, RuntimeError, ValueError, TypeError) as err:
            logger.error("Failed to publish on-demand state: %s", err, exc_info=True)

    def _set_on_demand_error(self, message: str) -> None:
        """Set on-demand state to error and publish."""
        self.on_demand_status = 'error'
        self.on_demand_last_error = message
        self.on_demand_last_event = None
        self.on_demand_active = False
        self.on_demand_mode = None
        self.on_demand_plugin_id = None
        self.on_demand_duration = None
        self.on_demand_requested_at = None
        self.on_demand_expires_at = None
        self.on_demand_pinned = False
        self.rotation_resume_index = None
        self.on_demand_schedule_override = False
        self._publish_on_demand_state()

    def _poll_on_demand_requests(self) -> None:
        """Poll cache for new on-demand requests from external controllers."""
        try:
            # Use a long max_age (1 hour) to ensure requests aren't expired before processing
            # The request_id check prevents duplicate processing
            request = self.cache_manager.get('display_on_demand_request', max_age=3600)
        except (OSError, RuntimeError, ValueError, TypeError) as err:
            logger.error("Failed to read on-demand request: %s", err, exc_info=True)
            return

        if not request:
            return

        request_id = request.get('request_id')
        if not request_id or request_id == self.on_demand_request_id:
            return

        action = request.get('action')
        logger.info("Received on-demand request %s: %s", request_id, action)
        if action == 'start':
            self._activate_on_demand(request)
        elif action == 'stop':
            self._clear_on_demand(reason='requested-stop')
        else:
            logger.warning("Unknown on-demand action: %s", action)
        self.on_demand_request_id = request_id

    def _resolve_mode_for_plugin(self, plugin_id: Optional[str], mode: Optional[str]) -> Optional[str]:
        """Resolve the display mode to use for on-demand activation."""
        if mode:
            return mode

        if plugin_id and plugin_id in self.plugin_display_modes:
            modes = self.plugin_display_modes.get(plugin_id, [])
            if modes:
                return modes[0]
        return plugin_id

    def _activate_on_demand(self, request: Dict[str, Any]) -> None:
        """Activate on-demand mode for a specific plugin display."""
        plugin_id = request.get('plugin_id')
        mode = request.get('mode')
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

        if self.available_modes:
            self.rotation_resume_index = self.current_mode_index
        else:
            self.rotation_resume_index = None

        if resolved_mode in self.available_modes:
            self.current_mode_index = self.available_modes.index(resolved_mode)

        self.on_demand_active = True
        self.on_demand_mode = resolved_mode
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
        self.current_display_mode = resolved_mode
        logger.info("Activated on-demand mode '%s' for plugin '%s'", resolved_mode, resolved_plugin_id)
        self._publish_on_demand_state()

    def _clear_on_demand(self, reason: Optional[str] = None) -> None:
        """Clear on-demand mode and resume normal rotation."""
        if not self.on_demand_active and self.on_demand_status == 'idle':
            if reason == 'requested-stop':
                self.on_demand_last_event = 'stop-request-ignored'  # Already idle
                self._publish_on_demand_state()
            return

        self.on_demand_active = False
        self.on_demand_mode = None
        self.on_demand_plugin_id = None
        self.on_demand_duration = None
        self.on_demand_requested_at = None
        self.on_demand_expires_at = None
        self.on_demand_pinned = False
        self.on_demand_status = 'idle'
        self.on_demand_last_error = None
        self.on_demand_last_event = reason or 'cleared'
        self.on_demand_schedule_override = False

        if self.rotation_resume_index is not None and self.available_modes:
            self.current_mode_index = self.rotation_resume_index % len(self.available_modes)
            self.current_display_mode = self.available_modes[self.current_mode_index]
        elif self.available_modes:
            # Default to first mode
            self.current_mode_index = self.current_mode_index % len(self.available_modes)
            self.current_display_mode = self.available_modes[self.current_mode_index]

        self.rotation_resume_index = None
        self.force_change = True
        logger.info("Cleared on-demand mode (reason=%s), resuming rotation", reason)
        self._publish_on_demand_state()

    def _check_on_demand_expiration(self) -> None:
        """Expire on-demand mode if duration has elapsed."""
        if not self.on_demand_active or self.on_demand_expires_at is None:
            return

        if time.time() >= self.on_demand_expires_at:
            logger.info("On-demand mode '%s' expired", self.on_demand_mode)
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
                pass  # Background service may not be initialized
            
            # Log deferred updates stats
            if hasattr(self.display_manager, '_scrolling_state'):
                deferred_count = len(self.display_manager._scrolling_state.get('deferred_updates', []))
                if deferred_count > 0:
                    logger.info(f"Deferred Updates Queue: {deferred_count} pending updates")
            
        except Exception as e:
            logger.debug(f"Error logging memory stats: {e}")

    def _check_live_priority(self):
        """
        Check all plugins for live priority content.
        Returns the mode that should be displayed if live content is found, None otherwise.
        """
        for mode_name, plugin_instance in self.plugin_modes.items():
            if hasattr(plugin_instance, 'has_live_priority') and hasattr(plugin_instance, 'has_live_content'):
                try:
                    if plugin_instance.has_live_priority() and plugin_instance.has_live_content():
                        # Get the specific live mode from the plugin if available
                        if hasattr(plugin_instance, 'get_live_modes'):
                            live_modes = plugin_instance.get_live_modes()
                            if live_modes and len(live_modes) > 0:
                                # Verify the mode actually exists before returning it
                                for suggested_mode in live_modes:
                                    if suggested_mode in self.plugin_modes:
                                        return suggested_mode
                                # If suggested modes don't exist, fall through to check current mode
                        # Fallback: if this mode ends with _live, return it
                        if mode_name.endswith('_live'):
                            return mode_name
                except Exception as e:
                    logger.warning("Error checking live priority for %s: %s", mode_name, e)
        return None

    def run(self):
        """Run the display controller, switching between displays."""
        if not self.available_modes:
            logger.warning("No display modes are enabled. Exiting.")
            self.display_manager.cleanup()
            return
             
        try:
            # Initialize with cached data for fast startup - let background updates refresh naturally
            logger.info("Starting display with cached data (fast startup mode)")
            self.current_display_mode = self.available_modes[self.current_mode_index] if self.available_modes else 'none'
            
            while True:
                # Handle on-demand commands before rendering
                self._poll_on_demand_requests()
                self._check_on_demand_expiration()
                self._tick_plugin_updates()
                
                # Periodic memory monitoring (if enabled)
                if self._enable_memory_logging:
                    self._log_memory_stats_if_due()

                # Check the schedule
                self._check_schedule()
                if self.on_demand_active and not self.is_display_active:
                    if not self.on_demand_schedule_override:
                        logger.info("On-demand override keeping display active during scheduled downtime")
                    self.on_demand_schedule_override = True
                    self.is_display_active = True
                elif not self.on_demand_active and self.on_demand_schedule_override:
                    self.on_demand_schedule_override = False

                if not self.is_display_active:
                    self._sleep_with_plugin_updates(60)
                    continue
                
                # Plugins update on their own schedules - no forced sync updates needed
                # Each plugin has its own update_interval and background services
                
                # Process any deferred updates that may have accumulated
                # This also cleans up expired updates to prevent memory leaks
                self.display_manager.process_deferred_updates()

                # Check for live priority content and switch to it immediately
                if not self.on_demand_active:
                    live_priority_mode = self._check_live_priority()
                    if live_priority_mode and self.current_display_mode != live_priority_mode:
                        logger.info("Live content detected - switching immediately to %s", live_priority_mode)
                        self.current_display_mode = live_priority_mode
                        self.force_change = True
                        # Update mode index to match the new mode
                        try:
                            self.current_mode_index = self.available_modes.index(live_priority_mode)
                        except ValueError:
                            pass

                if self.on_demand_active and self.on_demand_mode:
                    active_mode = self.on_demand_mode
                    if self.current_display_mode != active_mode:
                        self.current_display_mode = active_mode
                        self.force_change = True
                else:
                    active_mode = self.current_display_mode

                if self._active_dynamic_mode and self._active_dynamic_mode != active_mode:
                    self._active_dynamic_mode = None

                manager_to_display = None
                
                # Handle plugin-based display modes
                if active_mode in self.plugin_modes:
                    plugin_instance = self.plugin_modes[active_mode]
                    if hasattr(plugin_instance, 'display'):
                        # Check plugin health before attempting to display
                        plugin_id = getattr(plugin_instance, 'plugin_id', active_mode)
                        should_skip = False
                        if self.plugin_manager and hasattr(self.plugin_manager, 'health_tracker') and self.plugin_manager.health_tracker:
                            should_skip = self.plugin_manager.health_tracker.should_skip_plugin(plugin_id)
                            if should_skip:
                                logger.debug(f"Skipping plugin {plugin_id} due to circuit breaker (mode: {active_mode})")
                                display_result = False
                                # Skip to next mode - let existing logic handle it
                                manager_to_display = None
                        
                        manager_to_display = plugin_instance
                        logger.debug(f"Found plugin manager for mode {active_mode}: {type(plugin_instance).__name__}")
                    else:
                        logger.warning(f"Plugin {active_mode} found but has no display() method")
                else:
                    logger.debug(f"Mode {active_mode} not found in plugin_modes (available: {list(self.plugin_modes.keys())})")
                
                # Display the current mode
                display_result = True  # Default to True for backward compatibility
                display_failed_due_to_exception = False  # Track if False was due to exception vs no content
                if manager_to_display:
                    plugin_id = getattr(manager_to_display, 'plugin_id', active_mode)
                    try:
                        logger.debug(f"Calling display() for {active_mode} with force_clear={self.force_change}")
                        if hasattr(manager_to_display, 'display'):
                            # Check if plugin accepts display_mode parameter
                            import inspect
                            sig = inspect.signature(manager_to_display.display)
                            
                            # Use PluginExecutor for safe execution with timeout
                            if self.plugin_manager and hasattr(self.plugin_manager, 'plugin_executor'):
                                result = self.plugin_manager.plugin_executor.execute_display(
                                    manager_to_display,
                                    plugin_id,
                                    force_clear=self.force_change,
                                    display_mode=active_mode if 'display_mode' in sig.parameters else None
                                )
                                # execute_display returns bool, convert to expected format
                                if result:
                                    result = True  # Success
                                else:
                                    result = False  # Failed
                            else:
                                # Fallback to direct call if executor not available
                                if 'display_mode' in sig.parameters:
                                    result = manager_to_display.display(display_mode=active_mode, force_clear=self.force_change)
                                else:
                                    result = manager_to_display.display(force_clear=self.force_change)
                            
                            logger.debug(f"display() returned: {result} (type: {type(result)})")
                            # Check if display() returned a boolean (new behavior)
                            if isinstance(result, bool):
                                display_result = result
                                if not display_result:
                                    logger.info(f"Plugin {plugin_id} display() returned False for mode {active_mode}")
                        
                        # Record success if display completed without exception
                        if self.plugin_manager and hasattr(self.plugin_manager, 'health_tracker') and self.plugin_manager.health_tracker:
                            self.plugin_manager.health_tracker.record_success(plugin_id)
                        
                        self.force_change = False
                    except Exception as exc:  # pylint: disable=broad-except
                        logger.exception("Error displaying %s", self.current_display_mode)
                        # Record failure
                        if self.plugin_manager and hasattr(self.plugin_manager, 'health_tracker') and self.plugin_manager.health_tracker:
                            self.plugin_manager.health_tracker.record_failure(plugin_id, exc)
                        self.force_change = True
                        display_result = False
                        display_failed_due_to_exception = True  # Mark that this was an exception, not just no content
                
                # If display() returned False, skip to next mode immediately (unless on-demand)
                if not display_result:
                    if self.on_demand_active:
                        # Stay on on-demand mode even if no content - show "waiting" message
                        logger.info("No content for on-demand mode %s, staying on mode", active_mode)
                        self._sleep_with_plugin_updates(5)
                        self._publish_on_demand_state()
                        continue
                    else:
                        logger.info("No content to display for %s, skipping to next mode", active_mode)
                        # Don't clear display when immediately moving to next mode - this causes black flashes
                        # The next mode will render immediately with force_clear=True, which is sufficient
                        
                        # Only skip all modes for this plugin if there was an exception (broken plugin)
                        # If it's just "no content", we should still try other modes (recent, upcoming)
                        if display_failed_due_to_exception:
                            current_plugin_id = self.mode_to_plugin_id.get(active_mode)
                            if current_plugin_id and current_plugin_id in self.plugin_display_modes:
                                plugin_modes = self.plugin_display_modes[current_plugin_id]
                                logger.warning("Skipping all %d mode(s) for plugin %s due to exception: %s", 
                                              len(plugin_modes), current_plugin_id, plugin_modes)
                                # Find the next mode that's not from this plugin
                                next_index = self.current_mode_index
                                attempts = 0
                                max_attempts = len(self.available_modes)
                                found_next = False
                                while attempts < max_attempts:
                                    next_index = (next_index + 1) % len(self.available_modes)
                                    next_mode = self.available_modes[next_index]
                                    next_plugin_id = self.mode_to_plugin_id.get(next_mode)
                                    if next_plugin_id != current_plugin_id:
                                        self.current_mode_index = next_index
                                        self.current_display_mode = next_mode
                                        self.last_mode_change = time.time()
                                        self.force_change = True
                                        logger.info("Switching to mode: %s (skipped plugin %s due to exception)", 
                                                  self.current_display_mode, current_plugin_id)
                                        found_next = True
                                        break
                                    attempts += 1
                                # If we couldn't find a different plugin, just advance normally
                                if not found_next:
                                    logger.warning("All remaining modes are from plugin %s, advancing normally", current_plugin_id)
                                    # Will fall through to normal rotation logic below
                                else:
                                    # Already set next mode, skip to next iteration
                                    continue
                        # If no exception (just no content), fall through to normal rotation logic
                        # This allows trying other modes (recent, upcoming) from the same plugin
                else:
                    # Get base duration for current mode
                    base_duration = self._get_display_duration(active_mode)
                    dynamic_enabled = (
                        manager_to_display and self._plugin_supports_dynamic(manager_to_display)
                    )
                    
                    # Log dynamic duration status
                    if dynamic_enabled:
                        logger.debug(
                            "Dynamic duration enabled for mode %s (plugin: %s)",
                            active_mode,
                            getattr(manager_to_display, "plugin_id", "unknown"),
                        )

                    # Only reset cycle when actually switching to a different dynamic mode.
                    # This prevents resetting the cycle when staying on the same live priority mode
                    # with force_change=True (which is used for display clearing, not cycle resets).
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
                        self._plugin_reset_cycle(manager_to_display)
                        self._active_dynamic_mode = active_mode
                    elif not dynamic_enabled and self._active_dynamic_mode == active_mode:
                        logger.debug(
                            "Dynamic duration disabled for mode %s - clearing active dynamic mode",
                            active_mode,
                        )
                        self._active_dynamic_mode = None

                    min_duration = base_duration
                    if dynamic_enabled:
                        # Try to get plugin-calculated cycle duration first
                        logger.debug("Attempting to get cycle duration for mode %s", active_mode)
                        plugin_cycle_duration = self._plugin_cycle_duration(manager_to_display, active_mode)
                        logger.debug("Got cycle duration: %s", plugin_cycle_duration)
                        
                        # Get caps for validation
                        plugin_cap = self._plugin_dynamic_cap(manager_to_display)
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
                        
                        if chosen_cap <= 0:
                            logger.warning(
                                "Invalid dynamic duration cap %s for mode %s, using default %ds",
                                chosen_cap,
                                active_mode,
                                DEFAULT_DYNAMIC_DURATION_CAP,
                            )
                            chosen_cap = DEFAULT_DYNAMIC_DURATION_CAP
                        
                        # Use plugin-calculated duration if available, capped by max
                        if plugin_cycle_duration is not None and plugin_cycle_duration > 0:
                            # Plugin provided a calculated duration - use it but respect cap
                            target_duration = min(plugin_cycle_duration, chosen_cap)
                            max_duration = target_duration
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
                        
                        if max_duration < min_duration:
                            logger.warning(
                                "max_duration (%s) < min_duration (%s) for mode %s, adjusting max to min",
                                max_duration,
                                min_duration,
                                active_mode,
                            )
                            max_duration = min_duration
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

                    if self.on_demand_active:
                        remaining = self._get_on_demand_remaining()
                        if remaining is not None:
                            min_duration = min(min_duration, remaining)
                            max_duration = min(max_duration, remaining)
                            if max_duration <= 0:
                                self._check_on_demand_expiration()
                                continue

                    # For plugins, call display multiple times to allow game rotation
                    if manager_to_display and hasattr(manager_to_display, 'display'):
                        # Check if plugin needs high FPS (like stock ticker)
                        # Always enable high-FPS for static-image plugin (for GIF animation support)
                        plugin_id = getattr(manager_to_display, 'plugin_id', None)
                        if plugin_id == 'static-image':
                            needs_high_fps = True
                            logger.debug("FPS check - static-image plugin: forcing high-FPS mode for GIF support")
                        else:
                            has_enable_scrolling = hasattr(manager_to_display, 'enable_scrolling')
                            enable_scrolling_value = getattr(manager_to_display, 'enable_scrolling', False)
                            needs_high_fps = has_enable_scrolling and enable_scrolling_value
                            logger.debug(
                                "FPS check - has_enable_scrolling: %s, enable_scrolling_value: %s, needs_high_fps: %s",
                                has_enable_scrolling,
                                enable_scrolling_value,
                                needs_high_fps,
                            )

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
                            logger.info(
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

                            while True:
                                try:
                                    result = manager_to_display.display(force_clear=False)
                                    if isinstance(result, bool) and not result:
                                        logger.debug("Display returned False, breaking early")
                                        break
                                except Exception:  # pylint: disable=broad-except
                                    logger.exception("Error during display update")

                                time.sleep(display_interval)
                                self._tick_plugin_updates()
                                self._poll_on_demand_requests()
                                self._check_on_demand_expiration()

                                if self.current_display_mode != active_mode:
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

                            while True:
                                time.sleep(display_interval)
                                self._tick_plugin_updates()

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
                                    result = manager_to_display.display(force_clear=False)
                                    if isinstance(result, bool) and not result:
                                        logger.info("Display returned False for %s, breaking early", active_mode)
                                        break
                                except Exception:  # pylint: disable=broad-except
                                    logger.exception("Error during display update")

                                self._poll_on_demand_requests()
                                self._check_on_demand_expiration()
                                if self.current_display_mode != active_mode:
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
                    else:
                        # For non-plugin modes, use the original behavior
                        self._sleep_with_plugin_updates(max_duration)
                
                # Move to next mode
                if self.on_demand_active:
                    # Stay on the same mode while on-demand is active
                    self._publish_on_demand_state()
                    continue

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
                
                if should_rotate:
                    self.current_mode_index = (self.current_mode_index + 1) % len(self.available_modes)
                    self.current_display_mode = self.available_modes[self.current_mode_index]
                    self.last_mode_change = time.time()
                    self.force_change = True
                    
                    logger.info("Switching to mode: %s", self.current_display_mode)

        except KeyboardInterrupt:
            logger.info("Received interrupt signal, shutting down...")
        except Exception:  # pylint: disable=broad-except
            logger.exception("Unexpected error in display controller")
        finally:
            self.cleanup()

    def cleanup(self):
        """Clean up resources."""
        # Shutdown config service if it exists
        if hasattr(self, 'config_service'):
            try:
                self.config_service.shutdown()
            except Exception as e:
                logger.warning("Error shutting down config service: %s", e)
        logger.info("Cleaning up display controller...")
        if hasattr(self, 'display_manager'):
            self.display_manager.cleanup()
        logger.info("Cleanup complete.")

def main():
    controller = DisplayController()
    controller.run()

if __name__ == "__main__":
    main()
