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

    # What to do when a plugin's content exceeds its width budget.
    #
    #   "rotate"   — advance a window each cycle so everything is seen eventually.
    #                Right for interchangeable items: news headlines, odds, stocks.
    #   "truncate" — always show the start. Right for ordered content, where a
    #                window into the middle is meaningless: a league table that
    #                shows ranks 1-6 then resumes at 7 two rotations later reads
    #                as out of order and out of context.
    #
    # Override per plugin with vegas_overflow.
    overflow_mode: str = "rotate"

    # Cap on one plugin's share of a cycle, as a multiple of display width.
    # 0 (the default) disables the cap, so every plugin contributes all of its
    # content and is always entered at its beginning.
    #
    # Capping was the default until it proved to cost more than it bought.
    # Measured over a 17-plugin fleet on a 512px panel, only four plugins were
    # ever wide enough to hit a 3.0 cap; for those four it produced two visible
    # faults. Content resumed mid-item on each appearance (a news ticker entered
    # at column 6027 of its own strip), and the final window of a rotation was
    # whatever happened to be left — 348px of a 1840px stocks ticker, seven
    # seconds of panel time. Both read as the display being broken rather than
    # as deferral working.
    #
    # A wide plugin does hold the panel for a long time uncapped: set the cap
    # per plugin with vegas_max_width_screens where that matters, rather than
    # globally where it mostly hurts plugins that were never the problem.
    max_plugin_width_ratio: float = 0.0

    # Plugin management
    plugin_order: List[str] = field(default_factory=list)
    excluded_plugins: Set[str] = field(default_factory=set)

    # --- Live content in the ticker -------------------------------------
    #
    # By default a live game preempts Vegas entirely: the display controller
    # refuses to run the ticker while any plugin reports live priority, and you
    # get the full-screen scoreboard instead. Set live_in_ticker to keep the
    # marquee running and let live content take extra turns within it.
    #
    # The rotation is otherwise a strict round robin -- every plugin appears
    # exactly once per cycle -- so with a dozen plugins enabled a live score
    # comes round once a lap and can be minutes old on screen. Weighting lets a
    # plugin claim several slots per cycle instead.
    #
    # Weights are per plugin, not per game: a scoreboard showing four live
    # games still occupies one slot at a time, and rotates its own games within
    # that slot using its own favorite_live_boost.
    live_in_ticker: bool = False

    # Slots per cycle for a plugin reporting live content. 1 disables the boost
    # and restores the plain round robin.
    live_weight: int = 3

    # Slots per cycle for a plugin whose live content involves a favorite team.
    # Only plugins implementing get_vegas_priority_weight() can claim this --
    # the core cannot tell whose game is on, so the plugin reports it.
    favorite_live_weight: int = 5

    # Performance settings
    target_fps: int = 125  # Target frame rate
    buffer_ahead: int = 2  # Number of plugins to buffer ahead

    # Scroll behavior. Neither key steps the scroll or sets a frame rate:
    # motion is always by elapsed time at scroll_speed px/s. With
    # frame_based_scrolling the speed is first converted to px per
    # scroll_delay and clamped to 0.1-5 (ScrollHelper.set_scroll_speed), so
    # the speed actually applied is clamp(scroll_speed * scroll_delay, 0.1, 5)
    # / scroll_delay -- at the 0.02 default, speeds under 5 px/s run at 5.
    frame_based_scrolling: bool = True
    scroll_delay: float = 0.02  # only feeds the clamp above; not a frame period

    # Dynamic duration
    dynamic_duration_enabled: bool = True
    min_cycle_duration: int = 60  # Minimum seconds per full cycle
    max_cycle_duration: int = 240  # Maximum seconds per full cycle

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
        # Missing keys fall back to the field defaults above, so each default
        # is written once and the two cannot drift apart.
        d = cls()
        get = vegas_config.get

        return cls(
            enabled=get('enabled', d.enabled),
            scroll_speed=float(get('scroll_speed', d.scroll_speed)),
            separator_width=int(get('separator_width', d.separator_width)),
            intra_plugin_gap=int(get('intra_plugin_gap', d.intra_plugin_gap)),
            render_width_pct=int(get('render_width_pct', d.render_width_pct)),
            min_content_separation=int(
                get('min_content_separation', d.min_content_separation)),
            min_cut_gap=int(get('min_cut_gap', d.min_cut_gap)),
            smooth_scroll=get('smooth_scroll', d.smooth_scroll),
            continuous_scroll=get('continuous_scroll', d.continuous_scroll),
            extend_threshold_screens=float(
                get('extend_threshold_screens', d.extend_threshold_screens)),
            auto_trim=get('auto_trim', d.auto_trim),
            trim_threshold=int(get('trim_threshold', d.trim_threshold)),
            content_padding=int(get('content_padding', d.content_padding)),
            min_plugin_width=int(get('min_plugin_width', d.min_plugin_width)),
            lead_in_width=int(get('lead_in_width', d.lead_in_width)),
            plugins_per_cycle=int(get('plugins_per_cycle', d.plugins_per_cycle)),
            max_plugin_width_ratio=float(
                get('max_plugin_width_ratio', d.max_plugin_width_ratio)),
            overflow_mode=str(get('overflow_mode', d.overflow_mode)),
            plugin_order=list(get('plugin_order', d.plugin_order)),
            excluded_plugins=set(get('excluded_plugins', d.excluded_plugins)),
            live_in_ticker=bool(get('live_in_ticker', d.live_in_ticker)),
            # Clamped: a weight below 1 would drop the plugin from the rotation
            # entirely, and a very large one starves everything else.
            live_weight=max(1, min(10, int(get('live_weight', d.live_weight)))),
            favorite_live_weight=max(1, min(10, int(
                get('favorite_live_weight', d.favorite_live_weight)))),
            target_fps=int(get('target_fps', d.target_fps)),
            buffer_ahead=int(get('buffer_ahead', d.buffer_ahead)),
            frame_based_scrolling=get(
                'frame_based_scrolling', d.frame_based_scrolling),
            scroll_delay=float(get('scroll_delay', d.scroll_delay)),
            dynamic_duration_enabled=get(
                'dynamic_duration_enabled', d.dynamic_duration_enabled),
            min_cycle_duration=int(get('min_cycle_duration', d.min_cycle_duration)),
            max_cycle_duration=int(get('max_cycle_duration', d.max_cycle_duration)),
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
            'live_in_ticker': self.live_in_ticker,
            'live_weight': self.live_weight,
            'favorite_live_weight': self.favorite_live_weight,
            'overflow_mode': self.overflow_mode,
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

        if self.overflow_mode not in ('rotate', 'truncate'):
            errors.append(
                "overflow_mode must be 'rotate' or 'truncate', "
                f"got {self.overflow_mode!r}")

        if self.max_plugin_width_ratio < 0:
            errors.append(
                "max_plugin_width_ratio must be >= 0 "
                f"(0 disables the cap), got {self.max_plugin_width_ratio}")

        return errors
