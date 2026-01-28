"""
Vegas Mode Coordinator

Main orchestrator for Vegas-style continuous scroll mode. Coordinates between
StreamManager, RenderPipeline, and the display system to provide smooth
continuous scrolling of all enabled plugin content.

Supports three display modes per plugin:
- SCROLL: Content scrolls continuously within the stream
- FIXED_SEGMENT: Fixed block that scrolls by with other content
- STATIC: Scroll pauses, plugin displays for its duration, then resumes
"""

import logging
import time
import threading
from typing import Optional, Dict, Any, List, Callable, TYPE_CHECKING

from src.vegas_mode.config import VegasModeConfig
from src.vegas_mode.plugin_adapter import PluginAdapter
from src.vegas_mode.stream_manager import StreamManager
from src.vegas_mode.render_pipeline import RenderPipeline
from src.plugin_system.base_plugin import VegasDisplayMode

if TYPE_CHECKING:
    from src.plugin_system.plugin_manager import PluginManager
    from src.plugin_system.base_plugin import BasePlugin
    from src.display_manager import DisplayManager

logger = logging.getLogger(__name__)


class VegasModeCoordinator:
    """
    Orchestrates Vegas scroll mode operation.

    Responsibilities:
    - Initialize and coordinate all Vegas mode components
    - Manage the high-FPS render loop
    - Handle live priority interruptions
    - Process config updates
    - Provide status and control interface
    """

    def __init__(
        self,
        config: Dict[str, Any],
        display_manager: 'DisplayManager',
        plugin_manager: 'PluginManager'
    ):
        """
        Initialize the Vegas mode coordinator.

        Args:
            config: Main configuration dictionary
            display_manager: DisplayManager instance
            plugin_manager: PluginManager instance
        """
        # Parse configuration
        self.vegas_config = VegasModeConfig.from_config(config)

        # Store references
        self.display_manager = display_manager
        self.plugin_manager = plugin_manager

        # Initialize components
        self.plugin_adapter = PluginAdapter(display_manager)
        self.stream_manager = StreamManager(
            self.vegas_config,
            plugin_manager,
            self.plugin_adapter
        )
        self.render_pipeline = RenderPipeline(
            self.vegas_config,
            display_manager,
            self.stream_manager
        )

        # State management
        self._is_active = False
        self._is_paused = False
        self._should_stop = False
        self._state_lock = threading.Lock()

        # Live priority tracking
        self._live_priority_active = False
        self._live_priority_check: Optional[Callable[[], Optional[str]]] = None

        # Interrupt checker for yielding control back to display controller
        self._interrupt_check: Optional[Callable[[], bool]] = None
        self._interrupt_check_interval: int = 10  # Check every N frames

        # Config update tracking
        self._config_version = 0
        self._pending_config_update = False
        self._pending_config: Optional[Dict[str, Any]] = None

        # Static pause handling
        self._static_pause_active = False
        self._static_pause_plugin: Optional['BasePlugin'] = None
        self._static_pause_start: Optional[float] = None
        self._saved_scroll_position: Optional[int] = None

        # Track which plugins should use STATIC mode (pause scroll)
        self._static_mode_plugins: set = set()

        # Statistics
        self.stats = {
            'total_runtime_seconds': 0.0,
            'cycles_completed': 0,
            'interruptions': 0,
            'config_updates': 0,
            'static_pauses': 0,
        }
        self._start_time: Optional[float] = None

        logger.info(
            "VegasModeCoordinator initialized: enabled=%s, fps=%d, buffer_ahead=%d",
            self.vegas_config.enabled,
            self.vegas_config.target_fps,
            self.vegas_config.buffer_ahead
        )

    @property
    def is_enabled(self) -> bool:
        """Check if Vegas mode is enabled in configuration."""
        return self.vegas_config.enabled

    @property
    def is_active(self) -> bool:
        """Check if Vegas mode is currently running."""
        return self._is_active

    def set_live_priority_checker(self, checker: Callable[[], Optional[str]]) -> None:
        """
        Set the callback for checking live priority content.

        Args:
            checker: Callable that returns live priority mode name or None
        """
        self._live_priority_check = checker

    def set_interrupt_checker(
        self,
        checker: Callable[[], bool],
        check_interval: int = 10
    ) -> None:
        """
        Set the callback for checking if Vegas should yield control.

        This allows the display controller to interrupt Vegas mode
        when on-demand, wifi status, or other priority events occur.

        Args:
            checker: Callable that returns True if Vegas should yield
            check_interval: Check every N frames (default 10)
        """
        self._interrupt_check = checker
        self._interrupt_check_interval = max(1, check_interval)

    def start(self) -> bool:
        """
        Start Vegas mode operation.

        Returns:
            True if started successfully
        """
        if not self.vegas_config.enabled:
            logger.warning("Cannot start Vegas mode - not enabled in config")
            return False

        with self._state_lock:
            if self._is_active:
                logger.warning("Vegas mode already active")
                return True

            # Validate configuration
            errors = self.vegas_config.validate()
            if errors:
                logger.error("Vegas config validation failed: %s", errors)
                return False

            # Initialize stream manager
            if not self.stream_manager.initialize():
                logger.error("Failed to initialize stream manager")
                return False

            # Compose initial content
            if not self.render_pipeline.compose_scroll_content():
                logger.error("Failed to compose initial scroll content")
                return False

            self._is_active = True
            self._should_stop = False
            self._start_time = time.time()

        logger.info("Vegas mode started")
        return True

    def stop(self) -> None:
        """Stop Vegas mode operation."""
        with self._state_lock:
            if not self._is_active:
                return

            self._should_stop = True
            self._is_active = False

            if self._start_time:
                self.stats['total_runtime_seconds'] += time.time() - self._start_time
                self._start_time = None

        # Cleanup components
        self.render_pipeline.reset()
        self.stream_manager.reset()
        self.display_manager.set_scrolling_state(False)

        logger.info("Vegas mode stopped")

    def pause(self) -> None:
        """Pause Vegas mode (for live priority interruption)."""
        with self._state_lock:
            if not self._is_active:
                return
            self._is_paused = True
            self.stats['interruptions'] += 1

        self.display_manager.set_scrolling_state(False)
        logger.info("Vegas mode paused")

    def resume(self) -> None:
        """Resume Vegas mode after pause."""
        with self._state_lock:
            if not self._is_active:
                return
            self._is_paused = False

        logger.info("Vegas mode resumed")

    def run_frame(self) -> bool:
        """
        Run a single frame of Vegas mode.

        Should be called at target FPS (e.g., 125 FPS = every 8ms).

        Returns:
            True if frame was rendered, False if Vegas mode is not active
        """
        # Check if we should be running
        with self._state_lock:
            if not self._is_active or self._is_paused or self._should_stop:
                return False
            # Check for config updates (synchronized access)
            has_pending_update = self._pending_config_update

        # Check for live priority
        if self._check_live_priority():
            return False

        # Apply pending config update outside lock
        if has_pending_update:
            self._apply_pending_config()

        # Check if we need to start a new cycle
        if self.render_pipeline.is_cycle_complete():
            if not self.render_pipeline.start_new_cycle():
                logger.warning("Failed to start new Vegas cycle")
                return False
            self.stats['cycles_completed'] += 1

        # Check for hot-swap opportunities
        if self.render_pipeline.should_recompose():
            self.render_pipeline.hot_swap_content()

        # Render frame
        return self.render_pipeline.render_frame()

    def run_iteration(self) -> bool:
        """
        Run a complete Vegas mode iteration (display duration).

        This is called by DisplayController to run Vegas mode for one
        "display duration" period before checking for mode changes.

        Handles three display modes:
        - SCROLL/FIXED_SEGMENT: Continue normal scroll rendering
        - STATIC: Pause scroll, display plugin, resume on completion

        Returns:
            True if iteration completed normally, False if interrupted
        """
        if not self.is_active:
            if not self.start():
                return False

        # Update static mode plugin list on iteration start
        self._update_static_mode_plugins()

        frame_interval = self.vegas_config.get_frame_interval()
        duration = self.render_pipeline.get_dynamic_duration()
        start_time = time.time()
        frame_count = 0

        logger.info("Starting Vegas iteration for %.1fs", duration)

        while True:
            # Check for STATIC mode plugin that should pause scroll
            static_plugin = self._check_static_plugin_trigger()
            if static_plugin:
                if not self._handle_static_pause(static_plugin):
                    # Static pause was interrupted
                    return False
                # After static pause, skip this segment and continue
                self.stream_manager.get_next_segment()  # Consume the segment
                continue

            # Run frame
            if not self.run_frame():
                # Check why we stopped
                with self._state_lock:
                    if self._should_stop:
                        return False
                    if self._is_paused:
                        # Paused for live priority - let caller handle
                        return False

            # Sleep for frame interval
            time.sleep(frame_interval)

            # Increment frame count and check for interrupt periodically
            frame_count += 1
            if (self._interrupt_check and
                    frame_count % self._interrupt_check_interval == 0):
                try:
                    if self._interrupt_check():
                        logger.debug(
                            "Vegas interrupted by callback after %d frames",
                            frame_count
                        )
                        return False
                except Exception:
                    # Don't let interrupt check errors stop Vegas
                    pass

            # Check elapsed time
            elapsed = time.time() - start_time
            if elapsed >= duration:
                break

            # Check for cycle completion
            if self.render_pipeline.is_cycle_complete():
                break

        logger.info("Vegas iteration completed after %.1fs", time.time() - start_time)
        return True

    def _check_live_priority(self) -> bool:
        """
        Check if live priority content should interrupt Vegas mode.

        Returns:
            True if Vegas mode should be paused for live priority
        """
        if not self._live_priority_check:
            return False

        try:
            live_mode = self._live_priority_check()
            if live_mode:
                if not self._live_priority_active:
                    self._live_priority_active = True
                    self.pause()
                    logger.info("Live priority detected: %s - pausing Vegas", live_mode)
                return True
            else:
                if self._live_priority_active:
                    self._live_priority_active = False
                    self.resume()
                    logger.info("Live priority ended - resuming Vegas")
                return False
        except Exception:
            logger.exception("Error checking live priority")
            return False

    def update_config(self, new_config: Dict[str, Any]) -> None:
        """
        Update Vegas mode configuration.

        Config changes are applied at next safe point to avoid disruption.

        Args:
            new_config: New configuration dictionary
        """
        with self._state_lock:
            self._pending_config_update = True
            self._pending_config = new_config
            self._config_version += 1
            self.stats['config_updates'] += 1

        logger.debug("Config update queued (version %d)", self._config_version)

    def _apply_pending_config(self) -> None:
        """Apply pending configuration update."""
        with self._state_lock:
            if self._pending_config is None:
                self._pending_config_update = False
                return
            pending_config = self._pending_config

        try:
            new_vegas_config = VegasModeConfig.from_config(pending_config)

            # Check if enabled state changed
            was_enabled = self.vegas_config.enabled
            self.vegas_config = new_vegas_config

            # Update components
            self.render_pipeline.update_config(new_vegas_config)
            self.stream_manager.config = new_vegas_config

            # Handle enable/disable
            if was_enabled and not new_vegas_config.enabled:
                self.stop()
            elif not was_enabled and new_vegas_config.enabled:
                self.start()

            logger.info("Config update applied (version %d)", self._config_version)

        except Exception as e:
            logger.error("Error applying config update: %s", e)

        finally:
            with self._state_lock:
                self._pending_config_update = False
                self._pending_config = None

    def mark_plugin_updated(self, plugin_id: str) -> None:
        """
        Notify that a plugin's data has been updated.

        Args:
            plugin_id: ID of plugin that was updated
        """
        if self._is_active:
            self.stream_manager.mark_plugin_updated(plugin_id)
            self.plugin_adapter.invalidate_cache(plugin_id)

    def get_status(self) -> Dict[str, Any]:
        """Get comprehensive Vegas mode status."""
        status = {
            'enabled': self.vegas_config.enabled,
            'active': self._is_active,
            'paused': self._is_paused,
            'live_priority_active': self._live_priority_active,
            'config': self.vegas_config.to_dict(),
            'stats': self.stats.copy(),
        }

        if self._is_active:
            status['render_info'] = self.render_pipeline.get_current_scroll_info()
            status['stream_status'] = self.stream_manager.get_buffer_status()

        return status

    def get_ordered_plugins(self) -> List[str]:
        """Get the current ordered list of plugins in Vegas scroll."""
        if hasattr(self.plugin_manager, 'loaded_plugins'):
            available = list(self.plugin_manager.loaded_plugins.keys())
            return self.vegas_config.get_ordered_plugins(available)
        return []

    # -------------------------------------------------------------------------
    # Static pause handling (for STATIC display mode)
    # -------------------------------------------------------------------------

    def _check_static_plugin_trigger(self) -> Optional['BasePlugin']:
        """
        Check if a STATIC mode plugin should take over display.

        Called during iteration to detect when scroll should pause
        for a static plugin display.

        Returns:
            Plugin instance if static pause should begin, None otherwise
        """
        # Get the next plugin that would be displayed
        next_segment = self.stream_manager.peek_next_segment()
        if not next_segment:
            return None

        plugin_id = next_segment.plugin_id
        plugin = self.plugin_manager.get_plugin(plugin_id)

        if not plugin:
            return None

        # Check if this plugin is configured for STATIC mode
        try:
            display_mode = plugin.get_vegas_display_mode()
            if display_mode == VegasDisplayMode.STATIC:
                return plugin
        except Exception as e:
            logger.debug("Error checking vegas mode for %s: %s", plugin_id, e)

        return None

    def _handle_static_pause(self, plugin: 'BasePlugin') -> bool:
        """
        Handle a static pause - scroll pauses while plugin displays.

        Args:
            plugin: The STATIC mode plugin to display

        Returns:
            True if completed normally, False if interrupted
        """
        plugin_id = plugin.plugin_id

        with self._state_lock:
            if self._static_pause_active:
                logger.warning("Static pause already active")
                return True

            # Save current scroll position for smooth resume
            self._saved_scroll_position = self.render_pipeline.get_scroll_position()
            self._static_pause_active = True
            self._static_pause_plugin = plugin
            self._static_pause_start = time.time()
            self.stats['static_pauses'] += 1

        logger.info("Static pause started for plugin: %s", plugin_id)

        # Stop scrolling indicator
        self.display_manager.set_scrolling_state(False)

        try:
            # Display the plugin using its standard display() method
            plugin.display(force_clear=True)
            self.display_manager.update_display()

            # Wait for the plugin's display duration
            duration = plugin.get_display_duration()
            start = time.time()

            while time.time() - start < duration:
                # Check for interruptions
                if self._should_stop:
                    logger.info("Static pause interrupted by stop request")
                    return False

                if self._check_live_priority():
                    logger.info("Static pause interrupted by live priority")
                    return False

                # Sleep in small increments to remain responsive
                time.sleep(0.1)

            logger.info(
                "Static pause completed for %s after %.1fs",
                plugin_id, time.time() - start
            )

        except Exception as e:
            logger.error("Error during static pause for %s: %s", plugin_id, e)
            return False

        finally:
            self._end_static_pause()

        return True

    def _end_static_pause(self) -> None:
        """End static pause and restore scroll state."""
        should_resume_scrolling = False

        with self._state_lock:
            # Only resume scrolling if we weren't interrupted
            was_active = self._static_pause_active
            should_resume_scrolling = (
                was_active and
                not self._should_stop and
                not self._live_priority_active
            )

            # Clear pause state
            self._static_pause_active = False
            self._static_pause_plugin = None
            self._static_pause_start = None

            # Restore scroll position if we're resuming
            if should_resume_scrolling and self._saved_scroll_position is not None:
                self.render_pipeline.set_scroll_position(self._saved_scroll_position)
            self._saved_scroll_position = None

        # Only resume scrolling state if not interrupted
        if should_resume_scrolling:
            self.display_manager.set_scrolling_state(True)
            logger.debug("Static pause ended, scroll resumed")
        else:
            logger.debug("Static pause ended (interrupted, not resuming scroll)")

    def _update_static_mode_plugins(self) -> None:
        """Update the set of plugins using STATIC display mode."""
        self._static_mode_plugins.clear()

        for plugin_id in self.get_ordered_plugins():
            plugin = self.plugin_manager.get_plugin(plugin_id)
            if plugin:
                try:
                    mode = plugin.get_vegas_display_mode()
                    if mode == VegasDisplayMode.STATIC:
                        self._static_mode_plugins.add(plugin_id)
                except Exception as e:
                    logger.error(
                        "Error getting vegas display mode for plugin %s: %s",
                        plugin_id, e
                    )

        if self._static_mode_plugins:
            logger.info(
                "Static mode plugins: %s",
                ', '.join(self._static_mode_plugins)
            )

    def cleanup(self) -> None:
        """Clean up all resources."""
        self.stop()
        self.render_pipeline.cleanup()
        self.stream_manager.cleanup()
        self.plugin_adapter.cleanup()
        logger.info("VegasModeCoordinator cleanup complete")
