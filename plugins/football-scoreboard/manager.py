"""
Football Scoreboard Plugin for LEDMatrix - Using Existing Managers

This plugin provides NFL and NCAA FB scoreboard functionality by reusing
the proven, working manager classes from the LEDMatrix core project.
"""

import logging
import time
from typing import Dict, Any

from PIL import ImageFont

try:
    from src.plugin_system.base_plugin import BasePlugin
    from src.background_data_service import get_background_service
    from src.base_odds_manager import BaseOddsManager
except ImportError:
    BasePlugin = None
    get_background_service = None
    BaseOddsManager = None

# Import the copied manager classes
from nfl_managers import NFLLiveManager, NFLRecentManager, NFLUpcomingManager
from ncaa_fb_managers import (
    NCAAFBLiveManager,
    NCAAFBRecentManager,
    NCAAFBUpcomingManager,
)

logger = logging.getLogger(__name__)


class FootballScoreboardPlugin(BasePlugin if BasePlugin else object):
    """
    Football scoreboard plugin using existing manager classes.

    This plugin provides NFL and NCAA FB scoreboard functionality by
    delegating to the proven manager classes from LEDMatrix core.
    """

    def __init__(
        self,
        plugin_id: str,
        config: Dict[str, Any],
        display_manager,
        cache_manager,
        plugin_manager,
    ):
        """Initialize the football scoreboard plugin."""
        if BasePlugin:
            super().__init__(
                plugin_id, config, display_manager, cache_manager, plugin_manager
            )

        self.plugin_id = plugin_id
        self.config = config
        self.display_manager = display_manager
        self.cache_manager = cache_manager
        self.plugin_manager = plugin_manager

        self.logger = logger

        # Basic configuration
        self.is_enabled = config.get("enabled", True)
        self.display_width = getattr(display_manager, "display_width", 128)
        self.display_height = getattr(display_manager, "display_height", 32)

        # League configurations
        self.nfl_enabled = config.get("nfl", {}).get("enabled", False)
        self.ncaa_fb_enabled = config.get("ncaa_fb", {}).get("enabled", False)

        # Global settings
        self.display_duration = float(config.get("display_duration", 30))
        self.game_display_duration = float(config.get("game_display_duration", 15))

        # Additional settings
        self.show_records = config.get("show_records", False)
        self.show_ranking = config.get("show_ranking", False)
        self.show_odds = config.get("show_odds", False)

        # Initialize background service if available
        self.background_service = None
        if get_background_service:
            try:
                self.background_service = get_background_service(
                    self.cache_manager, max_workers=1
                )
                self.logger.info("Background service initialized")
            except Exception as e:
                self.logger.warning(f"Could not initialize background service: {e}")

        # Initialize managers
        self._initialize_managers()

        # Mode cycling
        self.current_mode_index = 0
        self.last_mode_switch = 0
        self.modes = self._get_available_modes()

        self.logger.info(
            f"Football scoreboard plugin initialized - {self.display_width}x{self.display_height}"
        )
        self.logger.info(
            f"NFL enabled: {self.nfl_enabled}, NCAA FB enabled: {self.ncaa_fb_enabled}"
        )

    def _initialize_managers(self):
        """Initialize all manager instances."""
        try:
            # Create adapted configs for managers
            nfl_config = self._adapt_config_for_manager("nfl")
            ncaa_fb_config = self._adapt_config_for_manager("ncaa_fb")

            # Initialize NFL managers if enabled
            if self.nfl_enabled:
                self.nfl_live = NFLLiveManager(
                    nfl_config, self.display_manager, self.cache_manager
                )
                self.nfl_recent = NFLRecentManager(
                    nfl_config, self.display_manager, self.cache_manager
                )
                self.nfl_upcoming = NFLUpcomingManager(
                    nfl_config, self.display_manager, self.cache_manager
                )
                self.logger.info("NFL managers initialized")

            # Initialize NCAA FB managers if enabled
            if self.ncaa_fb_enabled:
                self.ncaa_fb_live = NCAAFBLiveManager(
                    ncaa_fb_config, self.display_manager, self.cache_manager
                )
                self.ncaa_fb_recent = NCAAFBRecentManager(
                    ncaa_fb_config, self.display_manager, self.cache_manager
                )
                self.ncaa_fb_upcoming = NCAAFBUpcomingManager(
                    ncaa_fb_config, self.display_manager, self.cache_manager
                )
                self.logger.info("NCAA FB managers initialized")

        except Exception as e:
            self.logger.error(f"Error initializing managers: {e}")

    def _get_default_logo_dir(self, league: str) -> str:
        """
        Get the default logo directory for a league.
        Matches the directories used in src/logo_downloader.py.
        """
        # Map leagues to their logo directories (matching logo_downloader.py)
        logo_dir_map = {
            'ncaa_fb': 'assets/sports/ncaa_logos',  # NCAA FB uses ncaa_logos, not ncaa_fb_logos
            'nfl': 'assets/sports/nfl_logos',
        }
        # Default to league-specific directory if not in map
        return logo_dir_map.get(league, f"assets/sports/{league}_logos")

    def _adapt_config_for_manager(self, league: str) -> Dict[str, Any]:
        """
        Adapt plugin config format to manager expected format.

        Plugin uses: nfl: {...}, ncaa_fb: {...}
        Managers expect: nfl_scoreboard: {...}, ncaa_fb_scoreboard: {...}
        """
        league_config = self.config.get(league, {})

        # Extract nested configurations
        game_limits = league_config.get("game_limits", {})
        display_options = league_config.get("display_options", {})
        filtering = league_config.get("filtering", {})
        display_modes = league_config.get("display_modes", {})

        # Create manager config with expected structure
        manager_config = {
            f"{league}_scoreboard": {
                "enabled": league_config.get("enabled", False),
                "favorite_teams": league_config.get("favorite_teams", []),
                "display_modes": display_modes,
                "filtering": filtering,
                "recent_games_to_show": game_limits.get("recent_games_to_show", 5),
                "upcoming_games_to_show": game_limits.get("upcoming_games_to_show", 10),
                "logo_dir": league_config.get(
                    "logo_dir", self._get_default_logo_dir(league)
                ),
                "show_records": display_options.get("show_records", self.show_records),
                "show_ranking": display_options.get("show_ranking", self.show_ranking),
                "show_odds": display_options.get("show_odds", self.show_odds),
                "test_mode": league_config.get("test_mode", False),
                "update_interval_seconds": league_config.get(
                    "update_interval_seconds", 300
                ),
                "live_update_interval": league_config.get("live_update_interval", 30),
                "live_game_duration": league_config.get("live_game_duration", 20),
                "background_service": {
                    "request_timeout": 30,
                    "max_retries": 3,
                    "priority": 2,
                },
            }
        }

        # Add global config
        manager_config.update(
            {
                "timezone": self.config.get("timezone", "UTC"),
                "display": self.config.get("display", {}),
            }
        )

        return manager_config

    def _get_available_modes(self) -> list:
        """Get list of available display modes based on enabled leagues."""
        modes = []

        if self.nfl_enabled:
            modes.extend(["nfl_live", "nfl_recent", "nfl_upcoming"])

        if self.ncaa_fb_enabled:
            modes.extend(["ncaa_fb_live", "ncaa_fb_recent", "ncaa_fb_upcoming"])

        # Default to NFL if no leagues enabled
        if not modes:
            modes = ["nfl_live", "nfl_recent", "nfl_upcoming"]

        return modes

    def _get_current_manager(self):
        """Get the current manager based on the current mode."""
        if not self.modes:
            return None

        current_mode = self.modes[self.current_mode_index]

        if current_mode.startswith("nfl_"):
            if not self.nfl_enabled:
                return None
            mode_type = current_mode.split("_", 1)[1]  # "live", "recent", "upcoming"
            if mode_type == "live":
                return self.nfl_live
            elif mode_type == "recent":
                return self.nfl_recent
            elif mode_type == "upcoming":
                return self.nfl_upcoming

        elif current_mode.startswith("ncaa_fb_"):
            if not self.ncaa_fb_enabled:
                return None
            mode_type = current_mode.split("_", 2)[2]  # "live", "recent", "upcoming"
            if mode_type == "live":
                return self.ncaa_fb_live
            elif mode_type == "recent":
                return self.ncaa_fb_recent
            elif mode_type == "upcoming":
                return self.ncaa_fb_upcoming

        return None

    def update(self) -> None:
        """Update football game data."""
        if not self.is_enabled:
            return

        try:
            # Update NFL managers if enabled
            if self.nfl_enabled:
                self.nfl_live.update()
                self.nfl_recent.update()
                self.nfl_upcoming.update()

            # Update NCAA FB managers if enabled
            if self.ncaa_fb_enabled:
                self.ncaa_fb_live.update()
                self.ncaa_fb_recent.update()
                self.ncaa_fb_upcoming.update()

        except Exception as e:
            self.logger.error(f"Error updating managers: {e}")

    def display(self, force_clear: bool = False) -> None:
        """Display football games with mode cycling."""
        if not self.is_enabled:
            return

        try:
            current_time = time.time()

            # Handle mode cycling
            if current_time - self.last_mode_switch >= self.display_duration:
                self.current_mode_index = (self.current_mode_index + 1) % len(
                    self.modes
                )
                self.last_mode_switch = current_time
                force_clear = True

                current_mode = self.modes[self.current_mode_index]
                self.logger.info(f"Switching to display mode: {current_mode}")

            # Get current manager and display
            current_manager = self._get_current_manager()
            if current_manager:
                current_manager.display(force_clear)
            else:
                self.logger.warning("No manager available for current mode")

        except Exception as e:
            self.logger.error(f"Error in display method: {e}")

    def get_info(self) -> Dict[str, Any]:
        """Get plugin information."""
        try:
            current_manager = self._get_current_manager()
            current_mode = self.modes[self.current_mode_index] if self.modes else "none"

            info = {
                "plugin_id": self.plugin_id,
                "name": "Football Scoreboard",
                "version": "2.0.5",
                "enabled": self.is_enabled,
                "display_size": f"{self.display_width}x{self.display_height}",
                "nfl_enabled": self.nfl_enabled,
                "ncaa_fb_enabled": self.ncaa_fb_enabled,
                "current_mode": current_mode,
                "available_modes": self.modes,
                "display_duration": self.display_duration,
                "game_display_duration": self.game_display_duration,
                "show_records": self.show_records,
                "show_ranking": self.show_ranking,
                "show_odds": self.show_odds,
                "managers_initialized": {
                    "nfl_live": hasattr(self, "nfl_live"),
                    "nfl_recent": hasattr(self, "nfl_recent"),
                    "nfl_upcoming": hasattr(self, "nfl_upcoming"),
                    "ncaa_fb_live": hasattr(self, "ncaa_fb_live"),
                    "ncaa_fb_recent": hasattr(self, "ncaa_fb_recent"),
                    "ncaa_fb_upcoming": hasattr(self, "ncaa_fb_upcoming"),
                },
            }

            # Add manager-specific info if available
            if current_manager and hasattr(current_manager, "get_info"):
                try:
                    manager_info = current_manager.get_info()
                    info["current_manager_info"] = manager_info
                except Exception as e:
                    info["current_manager_info"] = f"Error getting manager info: {e}"

            return info

        except Exception as e:
            self.logger.error(f"Error getting plugin info: {e}")
            return {
                "plugin_id": self.plugin_id,
                "name": "Football Scoreboard",
                "error": str(e),
            }

    def cleanup(self) -> None:
        """Clean up resources."""
        try:
            if hasattr(self, "background_service") and self.background_service:
                # Clean up background service if needed
                pass
            self.logger.info("Football scoreboard plugin cleanup completed")
        except Exception as e:
            self.logger.error(f"Error during cleanup: {e}")
