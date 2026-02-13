"""
Scroll Display Handler for UFC Scoreboard Plugin

Implements high-FPS horizontal scrolling of all matching fights with
UFC separator icons. Uses ScrollHelper for efficient numpy-based scrolling.

Based on scroll_display.py from football-scoreboard plugin.
UFC/MMA adaptation based on work by Alex Resnick (legoguy1000) - PR #137
"""

import logging
import time
import os
from pathlib import Path
from typing import Dict, Any, List, Optional
from PIL import Image

try:
    from src.common.scroll_helper import ScrollHelper
except ImportError:
    ScrollHelper = None

from fight_renderer import FightRenderer

logger = logging.getLogger(__name__)


class ScrollDisplayManager:
    """
    Handles scroll display mode for the UFC scoreboard plugin.

    This class:
    - Collects all fights matching criteria
    - Pre-renders each fight using FightRenderer
    - Adds UFC separator icons between fight card groups
    - Composes a single wide image using ScrollHelper
    - Implements dynamic duration based on total content width
    """

    # Path to UFC separator icon
    UFC_SEPARATOR_ICON = "assets/sports/ufc_logos/UFC.png"

    def __init__(
        self,
        display_manager,
        config: Dict[str, Any],
        custom_logger: Optional[logging.Logger] = None,
    ):
        self.display_manager = display_manager
        self.config = config
        self.logger = custom_logger or logger

        # Get display dimensions
        if hasattr(display_manager, "matrix") and display_manager.matrix is not None:
            self.display_width = display_manager.matrix.width
            self.display_height = display_manager.matrix.height
        else:
            self.display_width = getattr(display_manager, "width", 128)
            self.display_height = getattr(display_manager, "height", 32)

        # Initialize ScrollHelper
        if ScrollHelper:
            self.scroll_helper = ScrollHelper(
                self.display_width, self.display_height, self.logger
            )
            self._configure_scroll_helper()
        else:
            self.scroll_helper = None
            self.logger.error("ScrollHelper not available - scroll mode will not work")

        # Shared headshot cache for fight renderer
        self._headshot_cache: Dict[str, Image.Image] = {}

        # Separator icon cache
        self._separator_icons: Dict[str, Image.Image] = {}
        self._load_separator_icons()

        # Tracking state
        self._current_fights: List[Dict] = []
        self._current_fight_type: str = ""
        self._current_leagues: List[str] = []
        self._is_scrolling = False
        self._scroll_start_time: Optional[float] = None
        self._last_log_time: float = 0
        self._log_interval: float = 5.0

        # Performance tracking
        self._frame_count: int = 0
        self._fps_sample_start: float = time.time()

    def _configure_scroll_helper(self) -> None:
        """Configure scroll helper with settings from config."""
        if not self.scroll_helper:
            return

        scroll_settings = self._get_scroll_settings()

        # Set scroll speed
        scroll_speed = scroll_settings.get("scroll_speed", 50.0)
        scroll_delay = scroll_settings.get("scroll_delay", 0.01)

        self.scroll_helper.set_scroll_speed(scroll_speed)
        self.scroll_helper.set_scroll_delay(scroll_delay)

        # Enable dynamic duration
        dynamic_duration = scroll_settings.get("dynamic_duration", True)
        self.scroll_helper.set_dynamic_duration_settings(
            enabled=dynamic_duration,
            min_duration=30,
            max_duration=600,
            buffer=0.2,
        )

        # Use frame-based scrolling
        self.scroll_helper.set_frame_based_scrolling(True)

        # Convert scroll_speed to pixels per frame
        if scroll_speed < 10.0:
            pixels_per_frame = scroll_speed
        else:
            pixels_per_frame = scroll_speed * scroll_delay

        pixels_per_frame = max(0.1, min(5.0, pixels_per_frame))
        self.scroll_helper.set_scroll_speed(pixels_per_frame)

        effective_pps = pixels_per_frame / scroll_delay if scroll_delay > 0 else pixels_per_frame * 100

        self.logger.info(
            f"ScrollHelper configured: {pixels_per_frame:.2f} px/frame, delay={scroll_delay}s "
            f"(effective {effective_pps:.1f} px/s), dynamic_duration={dynamic_duration}"
        )

    def _get_scroll_settings(self) -> Dict[str, Any]:
        """Get scroll settings from config."""
        defaults = {
            "scroll_speed": 50.0,
            "scroll_delay": 0.01,
            "gap_between_games": 48,
            "show_league_separators": True,
            "dynamic_duration": True,
        }

        ufc_config = self.config.get("ufc", {})
        ufc_scroll = ufc_config.get("scroll_settings", {})
        if ufc_scroll:
            return {**defaults, **ufc_scroll}

        return defaults

    def _load_separator_icons(self) -> None:
        """Load and resize UFC separator icon."""
        separator_height = self.display_height - 4

        if os.path.exists(self.UFC_SEPARATOR_ICON):
            try:
                ufc_icon = Image.open(self.UFC_SEPARATOR_ICON)
                if ufc_icon.mode != "RGBA":
                    ufc_icon = ufc_icon.convert("RGBA")
                aspect = ufc_icon.width / ufc_icon.height
                new_width = int(separator_height * aspect)
                ufc_icon = ufc_icon.resize(
                    (new_width, separator_height), Image.Resampling.LANCZOS
                )
                self._separator_icons["ufc"] = ufc_icon
                self.logger.debug(f"Loaded UFC separator icon: {new_width}x{separator_height}")
            except Exception as e:
                self.logger.error(f"Error loading UFC separator icon: {e}")
        else:
            self.logger.warning(f"UFC separator icon not found at {self.UFC_SEPARATOR_ICON}")

    def _determine_fight_type(self, fight: Dict) -> str:
        """Determine fight type from its data."""
        if fight.get("is_live"):
            return "live"
        elif fight.get("is_final"):
            return "recent"
        elif fight.get("is_upcoming"):
            return "upcoming"

        # Fallback: check status dict
        status = fight.get("status")
        if isinstance(status, dict):
            state = status.get("state", "")
            if state == "in":
                return "live"
            elif state == "post":
                return "recent"
        return "upcoming"

    def has_cached_content(self) -> bool:
        """Check if scroll content is cached and ready."""
        return (
            self.scroll_helper is not None
            and hasattr(self.scroll_helper, "cached_image")
            and self.scroll_helper.cached_image is not None
        )

    def prepare_and_display(
        self,
        fights: List[Dict],
        fight_type: str,
        leagues: List[str],
        rankings_cache: Dict[str, int] = None,
    ) -> bool:
        """
        Prepare scrolling content from a list of fights.

        Args:
            fights: List of fight dictionaries
            fight_type: 'live', 'recent', 'upcoming', or 'mixed'
            leagues: List of leagues (usually just ['ufc'])
            rankings_cache: Not used for MMA, kept for API compatibility

        Returns:
            True if content was prepared successfully
        """
        if not self.scroll_helper:
            self.logger.error("ScrollHelper not available")
            return False

        if not fights:
            self.logger.debug("No fights to prepare for scroll")
            return False

        scroll_settings = self._get_scroll_settings()
        gap_between_fights = scroll_settings.get("gap_between_games", 48)
        show_separators = scroll_settings.get("show_league_separators", True)

        # Get display options from UFC config
        ufc_config = self.config.get("ufc", {})
        display_options = ufc_config.get("display_options", {})

        # Create fight renderer
        renderer = FightRenderer(
            self.display_width,
            self.display_height,
            self.config,
            headshot_cache=self._headshot_cache,
            custom_logger=self.logger,
        )

        # Pre-render all fight cards
        content_items: List[Image.Image] = []
        current_league = None

        for fight in fights:
            fight_league = fight.get("league", "ufc")

            # Add league separator if switching leagues or at start
            if show_separators:
                if current_league is None or fight_league != current_league:
                    separator = self._separator_icons.get(fight_league)
                    if separator:
                        sep_img = Image.new(
                            "RGB",
                            (separator.width + 8, self.display_height),
                            (0, 0, 0),
                        )
                        y_offset = (self.display_height - separator.height) // 2
                        sep_img.paste(separator, (4, y_offset), separator)
                        content_items.append(sep_img)

            current_league = fight_league

            # Determine fight type from data
            individual_type = self._determine_fight_type(fight)

            # Render fight card
            fight_img = renderer.render_fight_card(
                fight, fight_type=individual_type, display_options=display_options
            )

            if fight_img:
                # Add horizontal padding
                padding = 12
                padded_width = fight_img.width + (padding * 2)
                padded_img = Image.new(
                    "RGB", (padded_width, fight_img.height), (0, 0, 0)
                )
                padded_img.paste(fight_img, (padding, 0))
                content_items.append(padded_img)
            else:
                self.logger.warning(
                    f"Failed to render fight card for {fight.get('fighter2_name', '?')} vs {fight.get('fighter1_name', '?')}"
                )

        if not content_items:
            self.logger.warning("No fight cards were rendered")
            return False

        # Create scrolling image
        try:
            self.scroll_helper.create_scrolling_image(
                content_items,
                item_gap=gap_between_fights,
                element_gap=0,
            )

            self._current_fights = fights
            self._current_fight_type = fight_type
            self._current_leagues = leagues
            self._is_scrolling = True
            self._scroll_start_time = time.time()
            self._frame_count = 0

            self.logger.info(
                f"Prepared scroll content: {len(fights)} fights, "
                f"{len(content_items)} items (with separators)"
            )
            return True

        except Exception as e:
            self.logger.error(f"Error creating scrolling image: {e}", exc_info=True)
            return False

    def display_scroll_frame(self) -> bool:
        """
        Display the next frame of scrolling content.

        Returns:
            True if a frame was displayed, False if scroll is complete or no content
        """
        if not self.scroll_helper or not self.scroll_helper.cached_image:
            return False

        # Update scroll position
        self.scroll_helper.update_scroll_position()

        # Get visible portion
        visible = self.scroll_helper.get_visible_portion()
        if not visible:
            return False

        try:
            self.display_manager.image = visible
            self.display_manager.update_display()

            self._frame_count += 1
            self.scroll_helper.log_frame_rate()
            self._log_scroll_progress()

            return True
        except Exception as e:
            self.logger.error(f"Error displaying scroll frame: {e}")
            return False

    def _log_scroll_progress(self) -> None:
        """Log scroll progress periodically."""
        current_time = time.time()
        if current_time - self._last_log_time >= self._log_interval:
            elapsed = current_time - (self._scroll_start_time or current_time)
            fps = self._frame_count / elapsed if elapsed > 0 else 0
            self.logger.debug(
                f"Scroll progress: {self._frame_count} frames, "
                f"{fps:.1f} FPS, {elapsed:.1f}s elapsed"
            )
            self._last_log_time = current_time

    def is_scroll_complete(self) -> bool:
        """Check if the scroll has completed one full cycle."""
        if not self.scroll_helper:
            return True
        return self.scroll_helper.is_scroll_complete()

    def reset(self) -> None:
        """Reset scroll state."""
        self._is_scrolling = False
        self._current_fights = []
        self._frame_count = 0
        if self.scroll_helper:
            self.scroll_helper.reset()
