"""
UFC Scoreboard Plugin for LEDMatrix

This plugin provides UFC/MMA scoreboard functionality by reusing
the proven sports manager architecture from LEDMatrix.

Display Modes:
- Switch Mode: Display one fight at a time with timed transitions
- Scroll Mode: High-FPS horizontal scrolling of all fights with UFC separators

Based on original work by Alex Resnick (legoguy1000) - PR #137
"""

import logging
import time
from typing import Dict, Any, Set, Optional, Tuple, List

from PIL import ImageFont

try:
    from src.plugin_system.base_plugin import BasePlugin, VegasDisplayMode
    from src.background_data_service import get_background_service
except ImportError:
    BasePlugin = None
    VegasDisplayMode = None
    get_background_service = None

# Import UFC manager classes
from ufc_managers import UFCLiveManager, UFCRecentManager, UFCUpcomingManager

# Import scroll display components
try:
    from scroll_display import ScrollDisplayManager
    SCROLL_AVAILABLE = True
except ImportError:
    ScrollDisplayManager = None
    SCROLL_AVAILABLE = False

logger = logging.getLogger(__name__)


class UFCScoreboardPlugin(BasePlugin if BasePlugin else object):
    """
    UFC scoreboard plugin using existing MMA manager classes.

    This plugin provides UFC/MMA scoreboard functionality by
    delegating to the MMA manager classes adapted from PR #137.
    """

    def __init__(
        self,
        plugin_id: str,
        config: Dict[str, Any],
        display_manager,
        cache_manager,
        plugin_manager,
    ):
        """Initialize the UFC scoreboard plugin."""
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
        if hasattr(display_manager, "matrix") and display_manager.matrix is not None:
            self.display_width = display_manager.matrix.width
            self.display_height = display_manager.matrix.height
        else:
            self.display_width = getattr(display_manager, "width", 128)
            self.display_height = getattr(display_manager, "height", 32)

        # UFC league configuration
        self.ufc_enabled = config.get("ufc", {}).get("enabled", True)
        self.logger.info(f"UFC enabled: {self.ufc_enabled}")

        # League registry (single league for UFC, but uses same pattern for consistency)
        self._league_registry: Dict[str, Dict[str, Any]] = {}

        # Global settings
        self.display_duration = float(config.get("display_duration", 30))
        self.game_display_duration = float(config.get("game_display_duration", 15))

        # Live priority
        self.ufc_live_priority = config.get("ufc", {}).get("live_priority", False)

        # Display mode settings
        self._display_mode_settings = self._parse_display_mode_settings()

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

        # Initialize league registry
        self._initialize_league_registry()

        # Initialize scroll display manager if available
        self._scroll_manager: Optional[ScrollDisplayManager] = None
        if SCROLL_AVAILABLE and ScrollDisplayManager:
            try:
                self._scroll_manager = ScrollDisplayManager(
                    self.display_manager, self.config, self.logger
                )
                self.logger.info("Scroll display manager initialized")
            except Exception as e:
                self.logger.warning(
                    f"Could not initialize scroll display manager: {e}"
                )
                self._scroll_manager = None

        # Track current scroll state
        self._scroll_active: Dict[str, bool] = {}
        self._scroll_prepared: Dict[str, bool] = {}

        # Enable high-FPS mode for scroll display
        self.enable_scrolling = self._scroll_manager is not None
        if self.enable_scrolling:
            self.logger.info("High-FPS scrolling enabled for UFC scoreboard")

        # Mode cycling
        self.current_mode_index = 0
        self.last_mode_switch = 0
        self.modes = self._get_available_modes()

        self.logger.info(
            f"UFC scoreboard plugin initialized - "
            f"{self.display_width}x{self.display_height}"
        )

        # Dynamic duration tracking
        self._dynamic_cycle_seen_modes: Set[str] = set()
        self._dynamic_mode_to_manager_key: Dict[str, str] = {}
        self._dynamic_manager_progress: Dict[str, Set[str]] = {}
        self._dynamic_managers_completed: Set[str] = set()
        self._dynamic_cycle_complete = False
        self._single_game_manager_start_times: Dict[str, float] = {}
        self._game_id_start_times: Dict[str, Dict[str, float]] = {}
        self._display_mode_to_managers: Dict[str, Set[str]] = {}

        # Track current display context
        self._current_display_league: Optional[str] = None
        self._current_display_mode_type: Optional[str] = None

        # Throttle logging
        self._last_live_content_false_log: float = 0.0
        self._live_content_log_interval: float = 60.0

        # Track display mode state
        self._last_display_mode: Optional[str] = None
        self._current_active_display_mode: Optional[str] = None
        self._current_game_tracking: Dict[str, Dict[str, Any]] = {}
        self._game_transition_log_interval: float = 1.0
        self._mode_start_time: Dict[str, float] = {}

    def _initialize_managers(self):
        """Initialize UFC manager instances."""
        try:
            ufc_config = self._adapt_config_for_manager("ufc")

            if self.ufc_enabled:
                self.ufc_live = UFCLiveManager(
                    ufc_config, self.display_manager, self.cache_manager
                )
                self.ufc_recent = UFCRecentManager(
                    ufc_config, self.display_manager, self.cache_manager
                )
                self.ufc_upcoming = UFCUpcomingManager(
                    ufc_config, self.display_manager, self.cache_manager
                )
                self.logger.info("UFC managers initialized")

        except Exception as e:
            self.logger.error(f"Error initializing managers: {e}", exc_info=True)

    def _initialize_league_registry(self) -> None:
        """Initialize the league registry with the UFC league."""
        self._league_registry["ufc"] = {
            "enabled": self.ufc_enabled,
            "priority": 1,
            "live_priority": self.ufc_live_priority,
            "managers": {
                "live": getattr(self, "ufc_live", None),
                "recent": getattr(self, "ufc_recent", None),
                "upcoming": getattr(self, "ufc_upcoming", None),
            },
        }

        enabled_leagues = [
            lid for lid, data in self._league_registry.items() if data["enabled"]
        ]
        self.logger.info(
            f"League registry initialized: {len(self._league_registry)} league(s), "
            f"{len(enabled_leagues)} enabled: {enabled_leagues}"
        )

    def _get_enabled_leagues_for_mode(self, mode_type: str) -> List[str]:
        """Get enabled leagues for a mode type in priority order."""
        enabled_leagues = []

        for league_id, league_data in self._league_registry.items():
            if not league_data.get("enabled", False):
                continue

            league_config = self.config.get(league_id, {})
            display_modes_config = league_config.get("display_modes", {})

            mode_enabled = True
            if mode_type == "live":
                mode_enabled = display_modes_config.get("show_live", True)
            elif mode_type == "recent":
                mode_enabled = display_modes_config.get("show_recent", True)
            elif mode_type == "upcoming":
                mode_enabled = display_modes_config.get("show_upcoming", True)

            if mode_enabled:
                enabled_leagues.append(league_id)

        enabled_leagues.sort(
            key=lambda lid: self._league_registry[lid].get("priority", 999)
        )
        return enabled_leagues

    def _get_league_manager_for_mode(self, league_id: str, mode_type: str):
        """Get the manager instance for a specific league and mode type."""
        if league_id not in self._league_registry:
            return None
        managers = self._league_registry[league_id].get("managers", {})
        return managers.get(mode_type)

    def _adapt_config_for_manager(self, league: str) -> Dict[str, Any]:
        """
        Adapt plugin config format to manager expected format.

        Plugin uses: ufc: {...}
        Managers expect: ufc_scoreboard: {...}
        """
        league_config = self.config.get(league, {})

        game_limits = league_config.get("game_limits", {})
        display_options = league_config.get("display_options", {})
        filtering = league_config.get("filtering", {})
        display_modes_config = league_config.get("display_modes", {})

        manager_display_modes = {
            f"{league}_live": display_modes_config.get("show_live", True),
            f"{league}_recent": display_modes_config.get("show_recent", True),
            f"{league}_upcoming": display_modes_config.get("show_upcoming", True),
        }

        # Get favorite fighters filtering
        show_favorites_only = filtering.get(
            "show_favorite_fighters_only",
            league_config.get("show_favorite_fighters_only", False),
        )
        show_all_live = filtering.get(
            "show_all_live", league_config.get("show_all_live", False)
        )

        manager_config = {
            f"{league}_scoreboard": {
                "enabled": league_config.get("enabled", True),
                "favorite_fighters": league_config.get("favorite_fighters", []),
                "favorite_weight_class": league_config.get(
                    "favorite_weight_classes", []
                ),
                "display_modes": manager_display_modes,
                "recent_games_to_show": game_limits.get("recent_games_to_show", 5),
                "upcoming_games_to_show": game_limits.get(
                    "upcoming_games_to_show", 10
                ),
                "show_records": display_options.get("show_records", True),
                "show_odds": display_options.get("show_odds", False),
                "show_fighter_names": display_options.get("show_fighter_names", True),
                "show_fight_class": display_options.get("show_fight_class", True),
                "update_interval_seconds": league_config.get(
                    "update_interval_seconds", 300
                ),
                "live_update_interval": league_config.get("live_update_interval", 30),
                "live_game_duration": league_config.get("live_game_duration", 20),
                "recent_game_duration": league_config.get("recent_game_duration", 15),
                "upcoming_game_duration": league_config.get(
                    "upcoming_game_duration", 15
                ),
                "live_priority": league_config.get("live_priority", False),
                "show_favorite_fighters_only": show_favorites_only,
                "show_all_live": show_all_live,
                "filtering": filtering,
                "background_service": {
                    "request_timeout": 30,
                    "max_retries": 3,
                    "priority": 2,
                },
            }
        }

        # Add global config
        timezone_str = self.config.get("timezone")
        if not timezone_str and hasattr(self.cache_manager, "config_manager"):
            timezone_str = self.cache_manager.config_manager.get_timezone()
        if not timezone_str:
            timezone_str = "UTC"

        display_config = self.config.get("display", {})
        if not display_config and hasattr(self.cache_manager, "config_manager"):
            display_config = self.cache_manager.config_manager.get_display_config()

        customization_config = self.config.get("customization", {})

        manager_config.update(
            {
                "timezone": timezone_str,
                "display": display_config,
                "customization": customization_config,
            }
        )

        self.logger.debug(f"Using timezone: {timezone_str} for {league} managers")
        return manager_config

    def _parse_display_mode_settings(self) -> Dict[str, Dict[str, str]]:
        """Parse display mode settings from config."""
        settings = {}
        league_config = self.config.get("ufc", {})
        display_modes_config = league_config.get("display_modes", {})

        settings["ufc"] = {
            "live": display_modes_config.get("live_display_mode", "switch"),
            "recent": display_modes_config.get("recent_display_mode", "switch"),
            "upcoming": display_modes_config.get("upcoming_display_mode", "switch"),
        }

        self.logger.debug(f"Display mode settings for UFC: {settings['ufc']}")
        return settings

    def _get_display_mode(self, league: str, game_type: str) -> str:
        """Get the display mode for a specific league and game type."""
        return self._display_mode_settings.get(league, {}).get(game_type, "switch")

    def _should_use_scroll_mode(self, mode_type: str) -> bool:
        """Check if scroll mode should be used for this game type."""
        if self.ufc_enabled and self._get_display_mode("ufc", mode_type) == "scroll":
            return True
        return False

    def _get_available_modes(self) -> list:
        """Get list of available display modes based on config."""
        modes = []

        if self.ufc_enabled:
            ufc_config = self.config.get("ufc", {})
            display_modes = ufc_config.get("display_modes", {})

            if display_modes.get("show_live", True):
                modes.append("ufc_live")
            if display_modes.get("show_recent", True):
                modes.append("ufc_recent")
            if display_modes.get("show_upcoming", True):
                modes.append("ufc_upcoming")

        if not modes:
            modes = ["ufc_live", "ufc_recent", "ufc_upcoming"]

        return modes

    def _get_current_manager(self):
        """Get the current manager based on the current mode."""
        if not self.modes:
            return None

        current_mode = self.modes[self.current_mode_index]

        if not self.ufc_enabled:
            return None

        if current_mode == "ufc_live":
            return getattr(self, "ufc_live", None)
        elif current_mode == "ufc_recent":
            return getattr(self, "ufc_recent", None)
        elif current_mode == "ufc_upcoming":
            return getattr(self, "ufc_upcoming", None)

        return None

    def _ensure_manager_updated(self, manager) -> None:
        """Trigger an update when the delegated manager is stale."""
        last_update = getattr(manager, "last_update", None)
        update_interval = getattr(manager, "update_interval", None)
        if last_update is None or update_interval is None:
            return

        interval = update_interval
        no_data_interval = getattr(manager, "no_data_interval", None)
        live_games = getattr(manager, "live_games", None)
        if no_data_interval and not live_games:
            interval = no_data_interval

        try:
            if interval and time.time() - last_update >= interval:
                manager.update()
        except Exception as exc:
            self.logger.debug(f"Auto-refresh failed for manager {manager}: {exc}")

    # -------------------------------------------------------------------------
    # Core plugin methods
    # -------------------------------------------------------------------------

    def update(self) -> None:
        """Update UFC fight data."""
        if not self.is_enabled:
            return

        try:
            if self.ufc_enabled:
                if hasattr(self, "ufc_live"):
                    self.ufc_live.update()
                if hasattr(self, "ufc_recent"):
                    self.ufc_recent.update()
                if hasattr(self, "ufc_upcoming"):
                    self.ufc_upcoming.update()
        except Exception as e:
            self.logger.error(f"Error updating managers: {e}")

    def display(self, display_mode: str = None, force_clear: bool = False) -> bool:
        """Display UFC fights for a specific mode.

        Args:
            display_mode: Mode name (e.g., 'ufc_live', 'ufc_recent', 'ufc_upcoming')
            force_clear: If True, clear display before rendering
        """
        if not self.is_enabled:
            return False

        try:
            if display_mode:
                if display_mode not in self.modes:
                    self.logger.debug(
                        f"Skipping disabled mode: {display_mode} "
                        f"(not in available modes: {self.modes})"
                    )
                    return False
                self._current_active_display_mode = display_mode

            if display_mode:
                # Parse mode: ufc_live -> league=ufc, mode_type=live
                mode_type_str = self._extract_mode_type(display_mode)
                if not mode_type_str:
                    self.logger.warning(f"Invalid display_mode: {display_mode}")
                    return False

                league = None
                for league_id in self._league_registry.keys():
                    mode_suffixes = ["_live", "_recent", "_upcoming"]
                    for suffix in mode_suffixes:
                        if display_mode == f"{league_id}{suffix}":
                            league = league_id
                            break
                    if league:
                        break

                if not league:
                    self.logger.warning(f"Unknown league in display_mode: {display_mode}")
                    return False

                if not self._league_registry.get(league, {}).get("enabled", False):
                    self.logger.debug(f"League {league} is disabled")
                    return False

                # Check if mode is enabled
                league_config = self.config.get(league, {})
                display_modes_config = league_config.get("display_modes", {})
                mode_enabled = True
                if mode_type_str == "live":
                    mode_enabled = display_modes_config.get("show_live", True)
                elif mode_type_str == "recent":
                    mode_enabled = display_modes_config.get("show_recent", True)
                elif mode_type_str == "upcoming":
                    mode_enabled = display_modes_config.get("show_upcoming", True)

                if not mode_enabled:
                    self.logger.debug(
                        f"Mode {mode_type_str} disabled for {league}"
                    )
                    return False

                return self._display_league_mode(league, mode_type_str, force_clear)
            else:
                return self._display_internal_cycling(force_clear)

        except Exception as e:
            self.logger.error(f"Error in display method: {e}")
            return False

    def _display_league_mode(
        self, league: str, mode_type: str, force_clear: bool
    ) -> bool:
        """Display a specific league/mode combination."""
        if league not in self._league_registry:
            return False

        if not self._league_registry[league].get("enabled", False):
            return False

        manager = self._get_league_manager_for_mode(league, mode_type)
        if not manager:
            self.logger.debug(f"No manager available for {league} {mode_type}")
            return False

        display_mode = f"{league}_{mode_type}"

        # Set display context for dynamic duration
        self._current_display_league = league
        self._current_display_mode_type = mode_type

        # Try display
        success, _ = self._try_manager_display(
            manager, force_clear, display_mode, mode_type
        )

        if success:
            if display_mode not in self._mode_start_time:
                self._mode_start_time[display_mode] = time.time()

            # Check mode duration
            effective_duration = self._get_effective_mode_duration(
                display_mode, mode_type
            )
            if effective_duration is not None:
                elapsed = time.time() - self._mode_start_time[display_mode]
                if elapsed >= effective_duration:
                    self.logger.info(
                        f"Mode duration expired for {display_mode}: "
                        f"{elapsed:.1f}s >= {effective_duration}s"
                    )
                    self._mode_start_time[display_mode] = time.time()
                    return False
        else:
            if display_mode in self._mode_start_time:
                del self._mode_start_time[display_mode]

        return success

    def _try_manager_display(
        self,
        manager,
        force_clear: bool,
        display_mode: str,
        mode_type: str,
    ) -> Tuple[bool, Optional[str]]:
        """Try to display content from a manager."""
        if not manager:
            return False, None

        self._current_display_mode_type = mode_type
        self._current_display_league = "ufc"

        self._ensure_manager_updated(manager)
        result = manager.display(force_clear)

        actual_mode = f"ufc_{mode_type}" if mode_type else display_mode

        # Track game transitions
        manager_class_name = manager.__class__.__name__
        current_game = getattr(manager, "current_game", None)
        current_game_id = None
        if current_game:
            current_game_id = current_game.get("id") or current_game.get("comp_id")
            if not current_game_id:
                f1 = current_game.get("fighter1_name", "")
                f2 = current_game.get("fighter2_name", "")
                if f1 and f2:
                    current_game_id = f"{f1}_vs_{f2}"

        game_tracking = self._current_game_tracking.get(display_mode, {})
        last_game_id = game_tracking.get("game_id")
        current_time = time.time()
        last_log_time = game_tracking.get("last_log_time", 0.0)

        game_changed = current_game_id and current_game_id != last_game_id
        if game_changed and (
            current_time - last_log_time >= self._game_transition_log_interval
        ):
            if current_game:
                f1 = current_game.get("fighter1_name", "?")
                f2 = current_game.get("fighter2_name", "?")
                self.logger.info(
                    f"Fight transition in {display_mode}: {f1} vs {f2}"
                )
            self._current_game_tracking[display_mode] = {
                "game_id": current_game_id,
                "league": "ufc",
                "last_log_time": current_time,
            }

        if result is True or result is None:
            manager_key = self._build_manager_key(actual_mode, manager)

            try:
                self._record_dynamic_progress(
                    manager, actual_mode=actual_mode, display_mode=display_mode
                )
            except Exception:
                pass

            if display_mode:
                self._display_mode_to_managers.setdefault(display_mode, set()).add(
                    manager_key
                )
            self._evaluate_dynamic_cycle_completion(display_mode=display_mode)
            return True, actual_mode

        return False, None

    def _display_internal_cycling(self, force_clear: bool) -> bool:
        """Handle display for internal mode cycling (legacy support)."""
        if not getattr(self, "_internal_cycling_warned", False):
            self.logger.warning(
                "Using deprecated internal mode cycling. "
                "Use display(display_mode=...) instead."
            )
            self._internal_cycling_warned = True

        current_time = time.time()
        if (
            self.last_mode_switch > 0
            and (current_time - self.last_mode_switch) >= self.display_duration
        ):
            self.current_mode_index = (self.current_mode_index + 1) % len(self.modes)
            self.last_mode_switch = current_time

        if self.last_mode_switch == 0:
            self.last_mode_switch = current_time

        manager = self._get_current_manager()
        if manager:
            self._ensure_manager_updated(manager)
            result = manager.display(force_clear)
            return bool(result)

        return False

    # -------------------------------------------------------------------------
    # Live priority support
    # -------------------------------------------------------------------------

    def has_live_priority(self) -> bool:
        if not self.is_enabled:
            return False
        return self.ufc_enabled and self.ufc_live_priority

    def has_live_content(self) -> bool:
        if not self.is_enabled:
            return False

        ufc_live = False
        if self.ufc_enabled and self.ufc_live_priority and hasattr(self, "ufc_live"):
            raw_live_games = getattr(self.ufc_live, "live_games", [])

            if raw_live_games:
                live_games = [
                    g for g in raw_live_games if not g.get("is_final", False)
                ]

                if live_games:
                    favorite_fighters = getattr(
                        self.ufc_live, "favorite_fighters", []
                    )
                    if favorite_fighters:
                        # Check if any live fight involves a favorite fighter
                        ufc_live = any(
                            g.get("fighter1_name", "").lower() in favorite_fighters
                            or g.get("fighter2_name", "").lower() in favorite_fighters
                            for g in live_games
                        )
                    else:
                        ufc_live = True

                    self.logger.info(
                        f"has_live_content: UFC live_games={len(live_games)}, "
                        f"ufc_live={ufc_live}"
                    )

        current_time = time.time()
        should_log = ufc_live or (
            current_time - self._last_live_content_false_log
            >= self._live_content_log_interval
        )
        if should_log and not ufc_live:
            self.logger.info(f"has_live_content() returning False")
            self._last_live_content_false_log = current_time

        return ufc_live

    def get_live_modes(self) -> list:
        """Return registered mode names that have live content."""
        if not self.is_enabled:
            return []

        live_modes = []
        if self.ufc_enabled and self.ufc_live_priority and hasattr(self, "ufc_live"):
            live_games = getattr(self.ufc_live, "live_games", [])
            if live_games:
                active_games = [
                    g for g in live_games if not g.get("is_final", False)
                ]
                if active_games:
                    live_modes.append("ufc_live")

        return live_modes

    # -------------------------------------------------------------------------
    # Dynamic duration support
    # -------------------------------------------------------------------------

    def supports_dynamic_duration(self) -> bool:
        """Check if dynamic duration is enabled for the current display context."""
        if not self.is_enabled:
            return False

        if not self._current_display_league or not self._current_display_mode_type:
            return False

        league = self._current_display_league
        mode_type = self._current_display_mode_type

        league_config = self.config.get(league, {})
        league_dynamic = league_config.get("dynamic_duration", {})
        league_modes = league_dynamic.get("modes", {})
        mode_config = league_modes.get(mode_type, {})

        if "enabled" in mode_config:
            return bool(mode_config.get("enabled", False))
        if "enabled" in league_dynamic:
            return bool(league_dynamic.get("enabled", False))

        return False

    def get_dynamic_duration_cap(self) -> Optional[float]:
        """Get dynamic duration cap for the current display context."""
        if not self.is_enabled:
            return None
        if not self._current_display_league or not self._current_display_mode_type:
            return None

        league_config = self.config.get(self._current_display_league, {})
        league_dynamic = league_config.get("dynamic_duration", {})
        league_modes = league_dynamic.get("modes", {})
        mode_config = league_modes.get(self._current_display_mode_type, {})

        if "max_duration_seconds" in mode_config:
            try:
                cap = float(mode_config["max_duration_seconds"])
                if cap > 0:
                    return cap
            except (TypeError, ValueError):
                pass

        if "max_duration_seconds" in league_dynamic:
            try:
                cap = float(league_dynamic["max_duration_seconds"])
                if cap > 0:
                    return cap
            except (TypeError, ValueError):
                pass

        return None

    def get_dynamic_duration_floor(self) -> Optional[float]:
        """Get dynamic duration minimum for the current display context."""
        if not self.is_enabled:
            return None
        if not self._current_display_league or not self._current_display_mode_type:
            return None

        league_config = self.config.get(self._current_display_league, {})
        league_dynamic = league_config.get("dynamic_duration", {})
        league_modes = league_dynamic.get("modes", {})
        mode_config = league_modes.get(self._current_display_mode_type, {})

        if "min_duration_seconds" in mode_config:
            try:
                floor = float(mode_config["min_duration_seconds"])
                if floor > 0:
                    return floor
            except (TypeError, ValueError):
                pass

        if "min_duration_seconds" in league_dynamic:
            try:
                floor = float(league_dynamic["min_duration_seconds"])
                if floor > 0:
                    return floor
            except (TypeError, ValueError):
                pass

        return None

    def reset_cycle_state(self) -> None:
        """Reset dynamic cycle tracking."""
        if BasePlugin:
            super().reset_cycle_state()
        self._dynamic_cycle_seen_modes.clear()
        self._dynamic_mode_to_manager_key.clear()
        self._dynamic_cycle_complete = False
        self.logger.debug("Dynamic cycle state reset")

    def is_cycle_complete(self) -> bool:
        """Report whether the plugin has shown a full cycle of content."""
        if not self._dynamic_feature_enabled():
            return True

        # Check scroll mode completion
        if self._current_active_display_mode:
            mode_type = self._extract_mode_type(self._current_active_display_mode)
            if (
                mode_type
                and self._should_use_scroll_mode(mode_type)
                and self._scroll_manager
            ):
                is_complete = self._scroll_manager.is_scroll_complete()
                self.logger.info(
                    f"is_cycle_complete() [scroll]: "
                    f"mode={self._current_active_display_mode}, "
                    f"returning {is_complete}"
                )
                return is_complete

        self._evaluate_dynamic_cycle_completion(
            display_mode=self._current_active_display_mode
        )
        return self._dynamic_cycle_complete

    def _dynamic_feature_enabled(self) -> bool:
        """Return True when dynamic duration should be active."""
        if not self.is_enabled:
            return False
        return self.supports_dynamic_duration()

    def _get_effective_mode_duration(
        self, display_mode: str, mode_type: str
    ) -> Optional[float]:
        """Get effective duration for a display mode."""
        cap = self.get_dynamic_duration_cap()
        if cap:
            return cap

        # Fallback to game_display_duration * number of games
        manager = self._get_league_manager_for_mode("ufc", mode_type)
        if manager:
            total_games = self._get_total_games_for_manager(manager)
            if total_games > 0:
                game_duration = self._get_game_duration("ufc", mode_type, manager)
                return total_games * game_duration

        return None

    def _get_game_duration(
        self, league: str, mode_type: str, manager=None
    ) -> float:
        """Get per-game display duration."""
        league_config = self.config.get(league, {})

        if mode_type == "live":
            return float(league_config.get("live_game_duration", 20))
        elif mode_type == "recent":
            return float(league_config.get("recent_game_duration", 15))
        elif mode_type == "upcoming":
            return float(league_config.get("upcoming_game_duration", 15))

        return float(self.game_display_duration)

    def _record_dynamic_progress(
        self, current_manager, actual_mode: str = None, display_mode: str = None
    ) -> None:
        """Track progress through managers/games for dynamic duration."""
        if not self._dynamic_feature_enabled() or not self.modes:
            self._dynamic_cycle_complete = True
            return

        current_mode = actual_mode or self.modes[self.current_mode_index]
        manager_key = self._build_manager_key(current_mode, current_manager)

        self._dynamic_cycle_seen_modes.add(current_mode)
        self._dynamic_mode_to_manager_key[current_mode] = manager_key

        # Track game progress
        current_game = getattr(current_manager, "current_game", None)
        current_game_id = None
        if current_game:
            current_game_id = str(
                current_game.get("id")
                or current_game.get("comp_id")
                or "unknown"
            )

        if manager_key not in self._dynamic_manager_progress:
            self._dynamic_manager_progress[manager_key] = set()

        if current_game_id:
            self._dynamic_manager_progress[manager_key].add(current_game_id)

        # Check completion
        total_games = self._get_total_games_for_manager(current_manager)
        seen_games = len(self._dynamic_manager_progress.get(manager_key, set()))

        if total_games <= 1:
            # Single game - track by time
            league = self._current_display_league or "ufc"
            mode_type = self._current_display_mode_type or "recent"
            self._track_single_game_progress(
                manager_key, current_manager, league, mode_type
            )
        elif seen_games >= total_games:
            if manager_key not in self._dynamic_managers_completed:
                self._dynamic_managers_completed.add(manager_key)
                self.logger.info(
                    f"Manager {manager_key} completed: "
                    f"{seen_games}/{total_games} games shown"
                )

    def _track_single_game_progress(
        self, manager_key: str, manager, league: str, mode_type: str
    ) -> None:
        """Track progress for a manager with a single game."""
        current_time = time.time()

        if manager_key not in self._single_game_manager_start_times:
            self._single_game_manager_start_times[manager_key] = current_time
            game_duration = self._get_game_duration(league, mode_type, manager)
            self.logger.info(
                f"Single-game manager {manager_key} first seen, "
                f"will complete after {game_duration}s"
            )
        else:
            start_time = self._single_game_manager_start_times[manager_key]
            game_duration = self._get_game_duration(league, mode_type, manager)
            elapsed = current_time - start_time
            if elapsed >= game_duration:
                if manager_key not in self._dynamic_managers_completed:
                    self._dynamic_managers_completed.add(manager_key)
                    self.logger.info(
                        f"Single-game manager {manager_key} completed "
                        f"after {elapsed:.1f}s"
                    )
                    if manager_key in self._single_game_manager_start_times:
                        del self._single_game_manager_start_times[manager_key]

    def _evaluate_dynamic_cycle_completion(
        self, display_mode: str = None
    ) -> None:
        """Check if all managers for the current display mode have completed."""
        if not self._dynamic_feature_enabled():
            self._dynamic_cycle_complete = True
            return

        if display_mode:
            manager_keys = self._display_mode_to_managers.get(display_mode, set())
            if manager_keys:
                all_complete = all(
                    mk in self._dynamic_managers_completed for mk in manager_keys
                )
                if all_complete and not self._dynamic_cycle_complete:
                    self._dynamic_cycle_complete = True
                    self.logger.info(
                        f"Dynamic cycle complete for {display_mode}: "
                        f"all {len(manager_keys)} managers finished"
                    )

    @staticmethod
    def _build_manager_key(mode_name: str, manager) -> str:
        manager_name = manager.__class__.__name__ if manager else "None"
        return f"{mode_name}:{manager_name}"

    @staticmethod
    def _get_total_games_for_manager(manager) -> int:
        if manager is None:
            return 0
        for attr in ("live_games", "games_list", "recent_games", "upcoming_games"):
            value = getattr(manager, attr, None)
            if isinstance(value, list):
                return len(value)
        return 0

    @staticmethod
    def _get_all_game_ids_for_manager(manager) -> set:
        """Get all game IDs from a manager's game list."""
        if manager is None:
            return set()
        game_ids = set()
        for attr in ("live_games", "games_list", "recent_games", "upcoming_games"):
            game_list = getattr(manager, attr, None)
            if isinstance(game_list, list) and game_list:
                for i, game in enumerate(game_list):
                    game_id = game.get("id") or game.get("comp_id")
                    if game_id:
                        game_ids.add(str(game_id))
                    else:
                        f1 = game.get("fighter1_name", "")
                        f2 = game.get("fighter2_name", "")
                        if f1 and f2:
                            game_ids.add(f"{f1}_vs_{f2}-{i}")
                        else:
                            game_ids.add(f"index-{i}")
                break
        return game_ids

    def _extract_mode_type(self, display_mode: str) -> Optional[str]:
        """Extract mode type from display mode string."""
        if display_mode.endswith("_live"):
            return "live"
        elif display_mode.endswith("_recent"):
            return "recent"
        elif display_mode.endswith("_upcoming"):
            return "upcoming"
        return None

    # -------------------------------------------------------------------------
    # Vegas scroll mode support
    # -------------------------------------------------------------------------

    def get_vegas_content(self) -> Optional[Any]:
        """
        Get content for Vegas-style continuous scroll mode.

        Returns None to let PluginAdapter auto-detect scroll_helper.cached_image.
        Triggers scroll content generation if cache is empty.
        """
        if hasattr(self, "_scroll_manager") and self._scroll_manager:
            if not self._scroll_manager.has_cached_content():
                self.logger.info("[UFC Vegas] Triggering scroll content generation")
                self._ensure_scroll_content_for_vegas()

        return None

    def get_vegas_content_type(self) -> str:
        """Indicate the type of content for Vegas scroll."""
        return "multi"

    def get_vegas_display_mode(self) -> "VegasDisplayMode":
        """Get the display mode for Vegas scroll integration."""
        if VegasDisplayMode:
            config_mode = self.config.get("vegas_mode")
            if config_mode:
                try:
                    return VegasDisplayMode(config_mode)
                except ValueError:
                    self.logger.warning(
                        f"Invalid vegas_mode '{config_mode}', using SCROLL"
                    )
            return VegasDisplayMode.SCROLL
        return "scroll"

    def _ensure_scroll_content_for_vegas(self) -> None:
        """Ensure scroll content is generated for Vegas mode."""
        if not self._scroll_manager:
            return

        # Refresh managers
        try:
            self.update()
        except Exception as e:
            self.logger.debug(f"[UFC Vegas] Manager refresh failed: {e}")

        # Collect all fights
        games, leagues = self._collect_fights_for_scroll()

        if not games:
            self.logger.debug("[UFC Vegas] No fights available")
            return

        # Count fight types
        type_counts = {"live": 0, "recent": 0, "upcoming": 0}
        for game in games:
            if game.get("is_live"):
                type_counts["live"] += 1
            elif game.get("is_final"):
                type_counts["recent"] += 1
            else:
                type_counts["upcoming"] += 1

        success = self._scroll_manager.prepare_and_display(
            games, "mixed", leagues
        )

        if success:
            type_summary = ", ".join(
                f"{count} {gtype}"
                for gtype, count in type_counts.items()
                if count > 0
            )
            self.logger.info(
                f"[UFC Vegas] Scroll content generated: "
                f"{len(games)} fights ({type_summary})"
            )
        else:
            self.logger.warning("[UFC Vegas] Failed to generate scroll content")

    def _collect_fights_for_scroll(
        self, mode_type: Optional[str] = None
    ) -> Tuple[List[Dict], List[str]]:
        """
        Collect all fights from UFC managers for scroll mode.

        Args:
            mode_type: Optional filter ('live', 'recent', 'upcoming').
                      If None, collects all types.

        Returns:
            Tuple of (fights list, list of leagues included)
        """
        fights = []
        leagues = []

        if not self.ufc_enabled:
            return fights, leagues

        mode_types = [mode_type] if mode_type else ["live", "recent", "upcoming"]

        for mt in mode_types:
            manager = self._get_league_manager_for_mode("ufc", mt)
            if manager:
                manager_fights = self._get_games_from_manager(manager, mt)
                if manager_fights:
                    for fight in manager_fights:
                        fight["league"] = "ufc"
                        if not isinstance(fight.get("status"), dict):
                            fight["status"] = {}
                        if "state" not in fight["status"]:
                            state_map = {
                                "live": "in",
                                "recent": "post",
                                "upcoming": "pre",
                            }
                            fight["status"]["state"] = state_map.get(mt, "pre")
                    fights.extend(manager_fights)
                    self.logger.debug(
                        f"Collected {len(manager_fights)} UFC {mt} fights for scroll"
                    )

        if fights:
            leagues.append("ufc")

        self.logger.debug(
            f"Total scroll fights collected: {len(fights)} from {leagues}"
        )
        return fights, leagues

    def _get_games_from_manager(self, manager, mode_type: str) -> List[Dict]:
        """Get games list from a manager based on mode type."""
        if mode_type == "live":
            return list(getattr(manager, "live_games", []) or [])
        elif mode_type == "recent":
            games = getattr(manager, "games_list", None)
            if games is None:
                games = getattr(manager, "recent_games", [])
            return list(games or [])
        elif mode_type == "upcoming":
            games = getattr(manager, "games_list", None)
            if games is None:
                games = getattr(manager, "upcoming_games", [])
            return list(games or [])
        return []

    # -------------------------------------------------------------------------
    # Scroll mode display
    # -------------------------------------------------------------------------

    def display_scroll_frame(self) -> bool:
        """Display the next scroll frame (called by display controller)."""
        if not self._scroll_manager:
            return False
        return self._scroll_manager.display_scroll_frame()

    def is_scrolling(self) -> bool:
        """Check if scroll mode is currently active."""
        return (
            self._scroll_manager is not None
            and self._scroll_manager.has_cached_content()
        )

    # -------------------------------------------------------------------------
    # Cleanup
    # -------------------------------------------------------------------------

    def cleanup(self) -> None:
        """Clean up resources."""
        try:
            if hasattr(self, "background_service") and self.background_service:
                pass
            self.logger.info("UFC scoreboard plugin cleanup completed")
        except Exception as e:
            self.logger.error(f"Error during cleanup: {e}")
