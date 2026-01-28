"""
Vegas Mode Configuration

Handles configuration for Vegas-style continuous scroll mode including
plugin ordering, exclusions, scroll speed, and display settings.
"""

import logging
from typing import Dict, Any, List, Set, Optional
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class VegasModeConfig:
    """Configuration for Vegas scroll mode."""

    # Core settings
    enabled: bool = False
    scroll_speed: float = 50.0  # Pixels per second
    separator_width: int = 32  # Gap between plugins (pixels)

    # Plugin management
    plugin_order: List[str] = field(default_factory=list)
    excluded_plugins: Set[str] = field(default_factory=set)

    # Performance settings
    target_fps: int = 125  # Target frame rate
    buffer_ahead: int = 2  # Number of plugins to buffer ahead

    # Scroll behavior
    frame_based_scrolling: bool = True
    scroll_delay: float = 0.02  # 50 FPS effective scroll updates

    # Dynamic duration
    dynamic_duration_enabled: bool = True
    min_cycle_duration: int = 60  # Minimum seconds per full cycle
    max_cycle_duration: int = 600  # Maximum seconds per full cycle

    @classmethod
    def from_config(cls, config: Dict[str, Any]) -> 'VegasModeConfig':
        """
        Create VegasModeConfig from main configuration dictionary.

        Args:
            config: Main config dict (expects config['display']['vegas_scroll'])

        Returns:
            VegasModeConfig instance
        """
        vegas_config = config.get('display', {}).get('vegas_scroll', {})

        return cls(
            enabled=vegas_config.get('enabled', False),
            scroll_speed=float(vegas_config.get('scroll_speed', 50.0)),
            separator_width=int(vegas_config.get('separator_width', 32)),
            plugin_order=list(vegas_config.get('plugin_order', [])),
            excluded_plugins=set(vegas_config.get('excluded_plugins', [])),
            target_fps=int(vegas_config.get('target_fps', 125)),
            buffer_ahead=int(vegas_config.get('buffer_ahead', 2)),
            frame_based_scrolling=vegas_config.get('frame_based_scrolling', True),
            scroll_delay=float(vegas_config.get('scroll_delay', 0.02)),
            dynamic_duration_enabled=vegas_config.get('dynamic_duration_enabled', True),
            min_cycle_duration=int(vegas_config.get('min_cycle_duration', 60)),
            max_cycle_duration=int(vegas_config.get('max_cycle_duration', 600)),
        )

    def to_dict(self) -> Dict[str, Any]:
        """Convert config to dictionary for serialization."""
        return {
            'enabled': self.enabled,
            'scroll_speed': self.scroll_speed,
            'separator_width': self.separator_width,
            'plugin_order': self.plugin_order,
            'excluded_plugins': list(self.excluded_plugins),
            'target_fps': self.target_fps,
            'buffer_ahead': self.buffer_ahead,
            'frame_based_scrolling': self.frame_based_scrolling,
            'scroll_delay': self.scroll_delay,
            'dynamic_duration_enabled': self.dynamic_duration_enabled,
            'min_cycle_duration': self.min_cycle_duration,
            'max_cycle_duration': self.max_cycle_duration,
        }

    def get_frame_interval(self) -> float:
        """Get the frame interval in seconds for target FPS."""
        return 1.0 / max(1, self.target_fps)

    def is_plugin_included(self, plugin_id: str) -> bool:
        """
        Check if a plugin should be included in Vegas scroll.

        Args:
            plugin_id: Plugin identifier to check

        Returns:
            True if plugin should be included
        """
        # If explicit order is set, only include those plugins
        if self.plugin_order:
            return plugin_id in self.plugin_order and plugin_id not in self.excluded_plugins
        # Otherwise include all non-excluded plugins
        return plugin_id not in self.excluded_plugins

    def get_ordered_plugins(self, available_plugins: List[str]) -> List[str]:
        """
        Get plugins in configured order, filtering excluded ones.

        Args:
            available_plugins: List of all available plugin IDs

        Returns:
            Ordered list of plugin IDs to include in Vegas scroll
        """
        if self.plugin_order:
            # Use explicit order, filter to only available and non-excluded
            ordered = [
                p for p in self.plugin_order
                if p in available_plugins and p not in self.excluded_plugins
            ]
            # Add any available plugins not in the order list (at the end)
            for p in available_plugins:
                if p not in ordered and p not in self.excluded_plugins:
                    ordered.append(p)
            return ordered
        else:
            # Use natural order, filter excluded
            return [p for p in available_plugins if p not in self.excluded_plugins]

    def validate(self) -> List[str]:
        """
        Validate configuration values.

        Returns:
            List of validation error messages (empty if valid)
        """
        errors = []

        if self.scroll_speed < 1.0:
            errors.append(f"scroll_speed must be >= 1.0, got {self.scroll_speed}")
        if self.scroll_speed > 500.0:
            errors.append(f"scroll_speed must be <= 500.0, got {self.scroll_speed}")

        if self.separator_width < 0:
            errors.append(f"separator_width must be >= 0, got {self.separator_width}")
        if self.separator_width > 256:
            errors.append(f"separator_width must be <= 256, got {self.separator_width}")

        if self.target_fps < 30:
            errors.append(f"target_fps must be >= 30, got {self.target_fps}")
        if self.target_fps > 200:
            errors.append(f"target_fps must be <= 200, got {self.target_fps}")

        if self.buffer_ahead < 1:
            errors.append(f"buffer_ahead must be >= 1, got {self.buffer_ahead}")
        if self.buffer_ahead > 5:
            errors.append(f"buffer_ahead must be <= 5, got {self.buffer_ahead}")

        return errors

    def update(self, new_config: Dict[str, Any]) -> None:
        """
        Update configuration from new values.

        Args:
            new_config: New configuration values to apply
        """
        vegas_config = new_config.get('display', {}).get('vegas_scroll', {})

        if 'enabled' in vegas_config:
            self.enabled = vegas_config['enabled']
        if 'scroll_speed' in vegas_config:
            self.scroll_speed = float(vegas_config['scroll_speed'])
        if 'separator_width' in vegas_config:
            self.separator_width = int(vegas_config['separator_width'])
        if 'plugin_order' in vegas_config:
            self.plugin_order = list(vegas_config['plugin_order'])
        if 'excluded_plugins' in vegas_config:
            self.excluded_plugins = set(vegas_config['excluded_plugins'])
        if 'target_fps' in vegas_config:
            self.target_fps = int(vegas_config['target_fps'])
        if 'buffer_ahead' in vegas_config:
            self.buffer_ahead = int(vegas_config['buffer_ahead'])
        if 'frame_based_scrolling' in vegas_config:
            self.frame_based_scrolling = vegas_config['frame_based_scrolling']
        if 'scroll_delay' in vegas_config:
            self.scroll_delay = float(vegas_config['scroll_delay'])

        # Log config update
        logger.info(
            "Vegas mode config updated: enabled=%s, speed=%.1f, fps=%d, buffer=%d",
            self.enabled, self.scroll_speed, self.target_fps, self.buffer_ahead
        )
