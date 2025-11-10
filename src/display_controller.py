import time
import logging
import sys
import os
from typing import Dict, Any, List, Optional
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed  # pylint: disable=no-name-in-module

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s.%(msecs)03d - %(levelname)s:%(name)s:%(message)s',
    datefmt='%H:%M:%S',
    stream=sys.stdout
)

# Core system imports only - all functionality now handled via plugins
from src.display_manager import DisplayManager
from src.config_manager import ConfigManager
from src.cache_manager import CacheManager
from src.font_manager import FontManager

# Get logger without configuring
logger = logging.getLogger(__name__)

class DisplayController:
    def __init__(self):
        start_time = time.time()
        logger.info("Starting DisplayController initialization")
        
        self.config_manager = ConfigManager()
        self.config = self.config_manager.load_config()
        self.cache_manager = CacheManager()
        logger.info("Config loaded in %.3f seconds", time.time() - start_time)
        
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
            try:
                if hasattr(plugin_instance, 'update'):
                    plugin_instance.update()
            except Exception:  # pylint: disable=broad-except
                logger.exception("Error updating plugin %s", plugin_id)

    def _get_display_duration(self, mode_key):
        """Get display duration for a mode."""
        # Check plugin-specific duration first
        if mode_key in self.plugin_modes:
            plugin_instance = self.plugin_modes[mode_key]
            if hasattr(plugin_instance, 'get_duration'):
                return plugin_instance.get_duration()
        
        # Fall back to config
        display_durations = self.config.get('display', {}).get('display_durations', {})
        return display_durations.get(mode_key, 30)

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
            request = self.cache_manager.get('display_on_demand_request')
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
                    time.sleep(60)
                    continue
                
                # Plugins update on their own schedules - no forced sync updates needed
                # Each plugin has its own update_interval and background services
                
                # Process any deferred updates that may have accumulated
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
                else:
                    active_mode = self.current_display_mode

                manager_to_display = None
                
                # Handle plugin-based display modes
                if active_mode in self.plugin_modes:
                    plugin_instance = self.plugin_modes[active_mode]
                    if hasattr(plugin_instance, 'display'):
                        manager_to_display = plugin_instance
                
                # Display the current mode
                display_result = True  # Default to True for backward compatibility
                if manager_to_display:
                    try:
                        if hasattr(manager_to_display, 'display'):
                            result = manager_to_display.display(force_clear=self.force_change)
                            # Check if display() returned a boolean (new behavior)
                            if isinstance(result, bool):
                                display_result = result
                        self.force_change = False
                    except Exception:  # pylint: disable=broad-except
                        logger.exception("Error displaying %s", self.current_display_mode)
                        self.force_change = True
                        display_result = False
                
                # If display() returned False, skip to next mode immediately (unless on-demand)
                if not display_result:
                    if self.on_demand_active:
                        # Stay on on-demand mode even if no content - show "waiting" message
                        logger.info("No content for on-demand mode %s, staying on mode", active_mode)
                        time.sleep(5)  # Wait 5 seconds before retrying
                        self._publish_on_demand_state()
                        continue
                    else:
                        logger.info("No content to display for %s, skipping to next mode", active_mode)
                else:
                    # Get duration for current mode
                    duration = self._get_display_duration(active_mode)
                    if self.on_demand_active:
                        remaining = self._get_on_demand_remaining()
                        if remaining is not None:
                            duration = min(duration, remaining)
                            if duration <= 0:
                                self._check_on_demand_expiration()
                                continue
                    
                    # For plugins, call display multiple times to allow game rotation
                    if manager_to_display and hasattr(manager_to_display, 'display'):
                        # Check if plugin needs high FPS (like stock ticker)
                        has_enable_scrolling = hasattr(manager_to_display, 'enable_scrolling')
                        enable_scrolling_value = getattr(manager_to_display, 'enable_scrolling', False)
                        needs_high_fps = has_enable_scrolling and enable_scrolling_value
                        logger.debug(
                            "FPS check - has_enable_scrolling: %s, enable_scrolling_value: %s, needs_high_fps: %s",
                            has_enable_scrolling,
                            enable_scrolling_value,
                            needs_high_fps,
                        )
                        
                        if needs_high_fps:
                            # Ultra-smooth FPS for scrolling plugins (8ms = 125 FPS)
                            display_interval = 0.008
                            
                            # Call display continuously for high-FPS plugins
                            elapsed = 0
                            while elapsed < duration:
                                try:
                                    result = manager_to_display.display(force_clear=False)
                                    # If display returns False, break early
                                    if isinstance(result, bool) and not result:
                                        logger.debug("Display returned False, breaking early")
                                        break
                                except Exception:  # pylint: disable=broad-except
                                    logger.exception("Error during display update")
                                
                                time.sleep(display_interval)
                                elapsed += display_interval
                                self._poll_on_demand_requests()
                                self._check_on_demand_expiration()
                                if self.current_display_mode != active_mode:
                                    logger.debug("Mode changed during high-FPS loop, breaking early")
                                    break
                        else:
                            # Normal FPS for other plugins (1 second)
                            display_interval = 1.0
                            
                            elapsed = 0
                            while elapsed < duration:
                                time.sleep(display_interval)
                                elapsed += display_interval
                                
                                # Call display again to allow game rotation
                                if elapsed < duration:  # Don't call on the last iteration
                                    try:
                                        result = manager_to_display.display(force_clear=False)
                                        # If display returns False, break early
                                        if isinstance(result, bool) and not result:
                                            logger.debug("Display returned False, breaking early")
                                            break
                                    except Exception:  # pylint: disable=broad-except
                                        logger.exception("Error during display update")
                                    
                                    self._poll_on_demand_requests()
                                    self._check_on_demand_expiration()
                                    if self.current_display_mode != active_mode:
                                        logger.debug("Mode changed during display loop, breaking early")
                                        break
                    else:
                        # For non-plugin modes, use the original behavior
                        time.sleep(duration)
                
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
                    
                    logger.info("Switching to mode: %s", self.current_display_mode)

        except KeyboardInterrupt:
            logger.info("Received interrupt signal, shutting down...")
        except Exception:  # pylint: disable=broad-except
            logger.exception("Unexpected error in display controller")
        finally:
            self.cleanup()

    def cleanup(self):
        """Clean up resources."""
        logger.info("Cleaning up display controller...")
        if hasattr(self, 'display_manager'):
            self.display_manager.cleanup()
        logger.info("Cleanup complete.")

def main():
    controller = DisplayController()
    controller.run()

if __name__ == "__main__":
    main()
