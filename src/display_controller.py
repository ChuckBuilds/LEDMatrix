import time
import logging
import sys
import os
from typing import Dict, Any, List
from datetime import datetime, time as time_obj

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
from src.old_managers.calendar_manager import CalendarManager

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
        
        # Core system components only
        self.calendar = CalendarManager(self.display_manager, self.config) if self.config.get('calendar', {}).get('enabled', True) else None
        
        # All other functionality handled via plugins
        logger.info(f"Calendar Manager initialized: {'Object' if self.calendar else 'None'}")
        logger.info("Display modes initialized in %.3f seconds", time.time() - init_time)
        
        self.force_change = False
        
        # All sports and content managers now handled via plugins
        logger.info("All sports and content managers now handled via plugin system")
        
        # List of available display modes - now handled entirely by plugins
        self.available_modes = []
        
        # Initialize Plugin System
        import traceback
        plugin_time = time.time()
        self.plugin_manager = None
        self.plugin_modes = {}  # mode -> plugin_instance mapping for plugin-first dispatch
        
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
            
            logger.info(f"Plugin Manager initialized with plugins directory: {plugins_dir}")
            
            self.plugin_manager = PluginManager(
                plugins_dir=plugins_dir,
                config_manager=self.config_manager,
                display_manager=self.display_manager,
                cache_manager=self.cache_manager,
                font_manager=self.font_manager
            )

            # Discover plugins
            discovered_plugins = self.plugin_manager.discover_plugins()
            logger.info(f"Discovered {len(discovered_plugins)} plugin(s)")

            # Load enabled plugins
            for plugin_id in discovered_plugins:
                plugin_config = self.config.get(plugin_id, {})
                if plugin_config.get('enabled', False):
                    if self.plugin_manager.load_plugin(plugin_id):
                        logger.info(f"Loaded plugin: {plugin_id}")
                        
                        # Get plugin instance and manifest
                        plugin_instance = self.plugin_manager.get_plugin(plugin_id)
                        manifest = self.plugin_manager.plugin_manifests.get(plugin_id, {})
                        display_modes = manifest.get('display_modes', [plugin_id])
                        
                        # Add plugin modes to available modes
                        for mode in display_modes:
                            self.available_modes.append(mode)
                            self.plugin_modes[mode] = plugin_instance
                            logger.info(f"Added plugin mode: {mode}")
                    else:
                        logger.warning(f"Failed to load plugin: {plugin_id}")
                else:
                    logger.info(f"Plugin {plugin_id} is disabled")

            logger.info(f"Plugin system initialized in %.3f seconds", time.time() - plugin_time)
            logger.info(f"Total available modes: {len(self.available_modes)}")
            logger.info(f"Available modes: {self.available_modes}")

        except Exception as e:
            logger.error(f"Plugin system initialization failed: {e}")
            logger.error(traceback.format_exc())
            self.plugin_manager = None

        # Display rotation state
        self.current_mode_index = 0
        self.current_display_mode = None
        self.last_mode_change = time.time()
        self.mode_duration = 30  # Default duration
        
        # Schedule management
        self.is_display_active = True
        
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
                logger.debug(f"Display inactive - {current_day} is disabled in schedule")
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
            logger.warning(f"Invalid schedule format: {e}")
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
            except Exception as e:
                logger.error(f"Error updating plugin {plugin_id}: {e}")

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

    def run(self):
        """Run the display controller, switching between displays."""
        if not self.available_modes:
            logger.warning("No display modes are enabled. Exiting.")
            self.display_manager.cleanup()
            return
             
        try:
            logger.info("Clearing cache and refetching data to prevent stale data issues...")
            self.cache_manager.clear_cache()
            self._update_modules()
            logger.info("Cache cleared, waiting 5 seconds for fresh data fetch...")
            time.sleep(5)
            self.current_display_mode = self.available_modes[self.current_mode_index] if self.available_modes else 'none'
            
            while True:
                current_time = time.time()

                # Check the schedule
                self._check_schedule()
                if not self.is_display_active:
                    time.sleep(60)
                    continue
                
                # Update data for all modules first
                self._update_modules()
                
                # Process any deferred updates that may have accumulated
                self.display_manager.process_deferred_updates()

                manager_to_display = None
                
                # Handle plugin-based display modes
                if self.current_display_mode in self.plugin_modes:
                    plugin_instance = self.plugin_modes[self.current_display_mode]
                    if hasattr(plugin_instance, 'display'):
                        manager_to_display = plugin_instance
                
                # Handle calendar (core system component)
                elif self.current_display_mode == 'calendar' and self.calendar:
                    manager_to_display = self.calendar
                
                # Display the current mode
                if manager_to_display:
                    try:
                        if hasattr(manager_to_display, 'display'):
                            manager_to_display.display(force_clear=self.force_change)
                        self.force_change = False
                    except Exception as e:
                        logger.error(f"Error displaying {self.current_display_mode}: {e}")
                        self.force_change = True
                
                # Get duration for current mode
                duration = self._get_display_duration(self.current_display_mode)
                
                # For plugins, call display multiple times to allow game rotation
                if manager_to_display and hasattr(manager_to_display, 'display'):
                    # Check if plugin needs high FPS (like stock ticker)
                    has_enable_scrolling = hasattr(manager_to_display, 'enable_scrolling')
                    enable_scrolling_value = getattr(manager_to_display, 'enable_scrolling', False)
                    needs_high_fps = has_enable_scrolling and enable_scrolling_value
                    logger.debug(f"FPS check - has_enable_scrolling: {has_enable_scrolling}, enable_scrolling_value: {enable_scrolling_value}, needs_high_fps: {needs_high_fps}")
                    
                    if needs_high_fps:
                        # Ultra-smooth FPS for scrolling plugins (8ms = 125 FPS)
                        display_interval = 0.008
                        
                        # Call display continuously for high-FPS plugins
                        elapsed = 0
                        while elapsed < duration:
                            try:
                                manager_to_display.display(force_clear=False)
                            except Exception as e:
                                logger.error(f"Error during display update: {e}")
                            
                            time.sleep(display_interval)
                            elapsed += display_interval
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
                                    manager_to_display.display(force_clear=False)
                                except Exception as e:
                                    logger.error(f"Error during display update: {e}")
                else:
                    # For non-plugin modes, use the original behavior
                    time.sleep(duration)
                
                # Move to next mode
                self.current_mode_index = (self.current_mode_index + 1) % len(self.available_modes)
                self.current_display_mode = self.available_modes[self.current_mode_index]
                self.last_mode_change = time.time()
                
                logger.info(f"Switching to mode: {self.current_display_mode}")

        except KeyboardInterrupt:
            logger.info("Received interrupt signal, shutting down...")
        except Exception as e:
            logger.error(f"Unexpected error in display controller: {e}")
            import traceback
            logger.error(traceback.format_exc())
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
