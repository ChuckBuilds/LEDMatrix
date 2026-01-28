"""
Vegas Mode Coordinator

Main orchestrator for Vegas-style continuous scroll mode. Coordinates between
StreamManager, RenderPipeline, and the display system to provide smooth
continuous scrolling of all enabled plugin content.
"""

import logging
import time
import threading
from typing import Optional, Dict, Any, List, Callable, TYPE_CHECKING

from src.vegas_mode.config import VegasModeConfig
from src.vegas_mode.plugin_adapter import PluginAdapter
from src.vegas_mode.stream_manager import StreamManager
from src.vegas_mode.render_pipeline import RenderPipeline

if TYPE_CHECKING:
    from src.plugin_system.plugin_manager import PluginManager
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

        # Config update tracking
        self._config_version = 0
        self._pending_config_update = False

        # Statistics
        self.stats = {
            'total_runtime_seconds': 0.0,
            'cycles_completed': 0,
            'interruptions': 0,
            'config_updates': 0,
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

        Returns:
            True if iteration completed normally, False if interrupted
        """
        if not self.is_active:
            if not self.start():
                return False

        frame_interval = self.vegas_config.get_frame_interval()
        duration = self.render_pipeline.get_dynamic_duration()
        start_time = time.time()

        logger.info("Starting Vegas iteration for %.1fs", duration)

        while True:
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
        except Exception as e:
            logger.error("Error checking live priority: %s", e)
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
            if not hasattr(self, '_pending_config'):
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
                if hasattr(self, '_pending_config'):
                    delattr(self, '_pending_config')

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

    def cleanup(self) -> None:
        """Clean up all resources."""
        self.stop()
        self.render_pipeline.cleanup()
        self.stream_manager.cleanup()
        self.plugin_adapter.cleanup()
        logger.info("VegasModeCoordinator cleanup complete")
