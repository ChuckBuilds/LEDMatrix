"""
Vegas Mode Configuration

Handles configuration for Vegas-style continuous scroll mode including
plugin ordering, exclusions, scroll speed, and display settings.
"""

import logging
from typing import Dict, Any, List, Set
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class VegasModeConfig:
    """Configuration for Vegas scroll mode."""

    # Core settings
    enabled: bool = False
    scroll_speed: float = 50.0  # Pixels per second
    separator_width: int = 32  # Gap between plugins (pixels)

    # Fraction of the panel width a plugin is told it has while rendering for
    # the ticker, as a percentage. Trimming can only remove blank margins; it
    # cannot compact a layout that genuinely spans the display — a five-column
    # forecast, a full-width progress bar, a centred stat block with the panel's
    # whole width between its elements. Rendering at a narrower size makes the
    # plugin choose a tighter layout instead. 100 disables it.
    render_width_pct: int = 100

    # Minimum blank columns guaranteed between adjacent content, measured from
    # actual ink rather than added blindly. A flat additive gap leaves
    # card-style content nearly touching when the cards are drawn flush to their
    # own edges, while padding out content that already has wide margins.
    min_content_separation: int = 24

    # Gap between rows contributed by the *same* plugin. separator_width marks
    # the handoff from one plugin to the next; applying it between every image
    # forced a 32px chasm between each row of a per-row ticker (the F1
    # scoreboard renders its own rows 4px apart), which both looked wrong and
    # silently inflated the width that plugin occupied.
    intra_plugin_gap: int = 8

    # Content density
    #
    # Plugins that render onto a full-display canvas contribute that whole
    # canvas to the ticker, blank margins included. On a wide panel that is the
    # dominant source of dead air: a plugin drawing 35px of text on a 512px
    # canvas otherwise buys 9.5s of black at 50px/s. Trimming reclaims it.
    auto_trim: bool = True
    trim_threshold: int = 10  # Per-channel value a pixel must exceed to be "ink"
    content_padding: int = 8  # Blank columns kept either side of trimmed content
    min_plugin_width: int = 8  # Segments narrower than this after trim are dropped

    # Columns of blank lead-in before the first item of a cycle. ScrollHelper
    # defaults this to a full display width, which reads as the display being
    # switched off at the start of every cycle.
    lead_in_width: int = 0

    # Blend between neighbouring pixel positions so motion happens at the frame
    # rate rather than the scroll speed. With integer positioning the number of
    # distinct frames per second equals scroll_speed, so at 50px/s the motion is
    # 50 discrete 1px steps however fast the loop runs. The trade is a slight
    # horizontal softening of text, since each frame is a blend of two positions.
    smooth_scroll: bool = True

    # Keep one continuous strip, extending it with the next group of plugins as
    # the scroll approaches the end, instead of composing a fresh strip and
    # swapping it in. A swap stops the motion, substitutes every pixel at once
    # and restarts with the viewport already full — read as a freeze, a flash
    # and a jump. Extending means the next group simply scrolls in from the
    # right. Set false to restore the swap behaviour.
    continuous_scroll: bool = True

    # Extend once the unscrolled remainder falls below this many screen widths.
    # Needs to be more than one so the join is prepared before it is on screen.
    extend_threshold_screens: float = 2.0

    # How many plugins are composed into one scroll cycle. Kept separate from
    # buffer_ahead (which is only a prefetch low-water mark) because the two
    # were previously the same number: a buffer_ahead of 2 meant just 3 plugins
    # per cycle, so a 20-plugin install took seven cycles to come around.
    plugins_per_cycle: int = 6

    # Minimum run of blank columns that counts as a boundary between items when
    # an oversized segment has to be narrowed. Measured on rendered text, the
    # gaps between characters are a single column while gaps between items are
    # 8px and up, so anything above 1 stops a cut landing inside a word. Cutting
    # mid-word orphaned the tail into the next cycle, which showed up as a lone
    # letter floating between two unrelated plugins.
    min_cut_gap: int = 6

    # Cap on one plugin's share of a cycle, as a multiple of display width.
    # A single ticker returning 7,000px would otherwise hold the panel for over
    # two minutes. Overflow is deferred to later cycles rather than discarded.
    # 0 disables the cap.
    max_plugin_width_ratio: float = 3.0

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
            intra_plugin_gap=int(vegas_config.get('intra_plugin_gap', 8)),
            render_width_pct=int(vegas_config.get('render_width_pct', 100)),
            min_content_separation=int(
                vegas_config.get('min_content_separation', 24)),
            min_cut_gap=int(vegas_config.get('min_cut_gap', 6)),
            smooth_scroll=vegas_config.get('smooth_scroll', True),
            continuous_scroll=vegas_config.get('continuous_scroll', True),
            extend_threshold_screens=float(
                vegas_config.get('extend_threshold_screens', 2.0)),
            auto_trim=vegas_config.get('auto_trim', True),
            trim_threshold=int(vegas_config.get('trim_threshold', 10)),
            content_padding=int(vegas_config.get('content_padding', 8)),
            min_plugin_width=int(vegas_config.get('min_plugin_width', 8)),
            lead_in_width=int(vegas_config.get('lead_in_width', 0)),
            plugins_per_cycle=int(vegas_config.get('plugins_per_cycle', 6)),
            max_plugin_width_ratio=float(
                vegas_config.get('max_plugin_width_ratio', 3.0)),
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
            'intra_plugin_gap': self.intra_plugin_gap,
            'render_width_pct': self.render_width_pct,
            'min_content_separation': self.min_content_separation,
            'min_cut_gap': self.min_cut_gap,
            'smooth_scroll': self.smooth_scroll,
            'continuous_scroll': self.continuous_scroll,
            'extend_threshold_screens': self.extend_threshold_screens,
            'auto_trim': self.auto_trim,
            'trim_threshold': self.trim_threshold,
            'content_padding': self.content_padding,
            'min_plugin_width': self.min_plugin_width,
            'lead_in_width': self.lead_in_width,
            'plugins_per_cycle': self.plugins_per_cycle,
            'max_plugin_width_ratio': self.max_plugin_width_ratio,
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

        This is consistent with get_ordered_plugins - plugins not explicitly
        in plugin_order are still included (appended at the end) unless excluded.

        Args:
            plugin_id: Plugin identifier to check

        Returns:
            True if plugin should be included
        """
        # Plugins are included unless explicitly excluded
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
        if self.scroll_speed > 200.0:
            errors.append(f"scroll_speed must be <= 200.0, got {self.scroll_speed}")

        if self.separator_width < 0:
            errors.append(f"separator_width must be >= 0, got {self.separator_width}")
        if self.separator_width > 128:
            errors.append(f"separator_width must be <= 128, got {self.separator_width}")

        if self.target_fps < 30:
            errors.append(f"target_fps must be >= 30, got {self.target_fps}")
        if self.target_fps > 200:
            errors.append(f"target_fps must be <= 200, got {self.target_fps}")

        if self.buffer_ahead < 1:
            errors.append(f"buffer_ahead must be >= 1, got {self.buffer_ahead}")
        if self.buffer_ahead > 5:
            errors.append(f"buffer_ahead must be <= 5, got {self.buffer_ahead}")

        if not 10 <= self.render_width_pct <= 100:
            errors.append(
                "render_width_pct must be between 10 and 100, "
                f"got {self.render_width_pct}")

        if not 0 <= self.min_content_separation <= 256:
            errors.append(
                "min_content_separation must be between 0 and 256, "
                f"got {self.min_content_separation}")

        if not 1.0 <= self.extend_threshold_screens <= 10.0:
            errors.append(
                "extend_threshold_screens must be between 1.0 and 10.0, "
                f"got {self.extend_threshold_screens}")

        if not 1 <= self.min_cut_gap <= 128:
            errors.append(
                "min_cut_gap must be between 1 and 128, "
                f"got {self.min_cut_gap}")

        if self.intra_plugin_gap < 0:
            errors.append(
                f"intra_plugin_gap must be >= 0, got {self.intra_plugin_gap}")
        if self.intra_plugin_gap > 128:
            errors.append(
                f"intra_plugin_gap must be <= 128, got {self.intra_plugin_gap}")

        if not 0 <= self.trim_threshold <= 254:
            errors.append(
                f"trim_threshold must be between 0 and 254, got {self.trim_threshold}")

        if self.content_padding < 0:
            errors.append(
                f"content_padding must be >= 0, got {self.content_padding}")
        if self.content_padding > 128:
            errors.append(
                f"content_padding must be <= 128, got {self.content_padding}")

        if self.min_plugin_width < 0:
            errors.append(
                f"min_plugin_width must be >= 0, got {self.min_plugin_width}")
        # Bounded because every segment narrower than this is dropped — an
        # unbounded value would discard every plugin and leave a blank ticker.
        if self.min_plugin_width > 512:
            errors.append(
                f"min_plugin_width must be <= 512, got {self.min_plugin_width}")

        if self.lead_in_width < 0:
            errors.append(
                f"lead_in_width must be >= 0, got {self.lead_in_width}")

        if self.plugins_per_cycle < 1:
            errors.append(
                f"plugins_per_cycle must be >= 1, got {self.plugins_per_cycle}")
        if self.plugins_per_cycle > 50:
            errors.append(
                f"plugins_per_cycle must be <= 50, got {self.plugins_per_cycle}")

        if self.max_plugin_width_ratio < 0:
            errors.append(
                "max_plugin_width_ratio must be >= 0 "
                f"(0 disables the cap), got {self.max_plugin_width_ratio}")

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
        if 'intra_plugin_gap' in vegas_config:
            self.intra_plugin_gap = int(vegas_config['intra_plugin_gap'])
        if 'render_width_pct' in vegas_config:
            self.render_width_pct = int(vegas_config['render_width_pct'])
        if 'min_content_separation' in vegas_config:
            self.min_content_separation = int(
                vegas_config['min_content_separation'])
        if 'min_cut_gap' in vegas_config:
            self.min_cut_gap = int(vegas_config['min_cut_gap'])
        if 'smooth_scroll' in vegas_config:
            self.smooth_scroll = vegas_config['smooth_scroll']
        if 'continuous_scroll' in vegas_config:
            self.continuous_scroll = vegas_config['continuous_scroll']
        if 'extend_threshold_screens' in vegas_config:
            self.extend_threshold_screens = float(
                vegas_config['extend_threshold_screens'])
        if 'auto_trim' in vegas_config:
            self.auto_trim = vegas_config['auto_trim']
        if 'trim_threshold' in vegas_config:
            self.trim_threshold = int(vegas_config['trim_threshold'])
        if 'content_padding' in vegas_config:
            self.content_padding = int(vegas_config['content_padding'])
        if 'min_plugin_width' in vegas_config:
            self.min_plugin_width = int(vegas_config['min_plugin_width'])
        if 'lead_in_width' in vegas_config:
            self.lead_in_width = int(vegas_config['lead_in_width'])
        if 'plugins_per_cycle' in vegas_config:
            self.plugins_per_cycle = int(vegas_config['plugins_per_cycle'])
        if 'max_plugin_width_ratio' in vegas_config:
            self.max_plugin_width_ratio = float(
                vegas_config['max_plugin_width_ratio'])
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
        if 'dynamic_duration_enabled' in vegas_config:
            self.dynamic_duration_enabled = vegas_config['dynamic_duration_enabled']
        if 'min_cycle_duration' in vegas_config:
            self.min_cycle_duration = int(vegas_config['min_cycle_duration'])
        if 'max_cycle_duration' in vegas_config:
            self.max_cycle_duration = int(vegas_config['max_cycle_duration'])

        # Log config update
        logger.info(
            "Vegas mode config updated: enabled=%s, speed=%.1f, fps=%d, buffer=%d",
            self.enabled, self.scroll_speed, self.target_fps, self.buffer_ahead
        )
