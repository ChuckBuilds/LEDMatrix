# Based on original LEDMatrix sports architecture. UFC/MMA adaptation based on work by Alex Resnick (legoguy1000) - PR #137
import logging
import os
import threading
import time
from abc import ABC, abstractmethod
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import pytz
import requests
from PIL import Image, ImageDraw, ImageFont
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# Import simplified dependencies for plugin use
try:
    from dynamic_team_resolver import DynamicTeamResolver
except ImportError:
    DynamicTeamResolver = None
from logo_downloader import LogoDownloader, download_missing_logo
from base_odds_manager import BaseOddsManager
from data_sources import ESPNDataSource


class SportsCore(ABC):
    def __init__(
        self,
        config: Dict[str, Any],
        display_manager,
        cache_manager,
        logger: logging.Logger,
        sport_key: str,
    ):
        self.logger = logger
        self.config = config
        self.cache_manager = cache_manager
        self.config_manager = getattr(cache_manager, "config_manager", None)
        # Initialize odds manager
        self.odds_manager = BaseOddsManager(self.cache_manager, self.config_manager)
        self.display_manager = display_manager
        # Get display dimensions from matrix (same as base SportsCore class)
        # This ensures proper scaling for different display sizes
        if hasattr(display_manager, 'matrix') and display_manager.matrix is not None:
            self.display_width = display_manager.matrix.width
            self.display_height = display_manager.matrix.height
        else:
            # Fallback to width/height properties (which also check matrix)
            self.display_width = getattr(display_manager, "width", 128)
            self.display_height = getattr(display_manager, "height", 32)

        self.sport_key = sport_key
        self.sport = None
        self.league = None

        # Initialize new architecture components (will be overridden by sport-specific classes)
        self.sport_config = None
        # Initialize data source
        self.data_source = ESPNDataSource(logger)
        self.mode_config = config.get(
            f"{sport_key}_scoreboard", {}
        )  # Changed config key
        self.is_enabled: bool = self.mode_config.get("enabled", False)
        self.show_odds: bool = self.mode_config.get("show_odds", False)
        # Use LogoDownloader to get the correct default logo directory for this sport
        from src.logo_downloader import LogoDownloader
        default_logo_dir = Path(LogoDownloader().get_logo_directory(sport_key))
        self.logo_dir = default_logo_dir
        self.update_interval: int = self.mode_config.get("update_interval_seconds", 60)
        self.show_records: bool = self.mode_config.get("show_records", False)
        self.show_ranking: bool = self.mode_config.get("show_ranking", False)
        # Number of games to show (instead of time-based windows)
        self.recent_games_to_show: int = self.mode_config.get(
            "recent_games_to_show", 5
        )  # Show last 5 games
        self.upcoming_games_to_show: int = self.mode_config.get(
            "upcoming_games_to_show", 10
        )  # Show next 10 games
        filtering_config = self.mode_config.get("filtering", {})
        self.show_favorite_teams_only: bool = self.mode_config.get(
            "show_favorite_teams_only",
            filtering_config.get("show_favorite_teams_only", False),
        )
        self.show_all_live: bool = self.mode_config.get(
            "show_all_live",
            filtering_config.get("show_all_live", False),
        )

        self.session = requests.Session()
        retry_strategy = Retry(
            total=5,  # increased number of retries
            backoff_factor=1,  # increased backoff factor
            # added 429 to retry list
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["GET", "HEAD", "OPTIONS"],
        )
        adapter = HTTPAdapter(max_retries=retry_strategy)
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)

        self._logo_cache = {}

        # Set up headers
        self.headers = {
            "User-Agent": "LEDMatrix/1.0 (https://github.com/yourusername/LEDMatrix; contact@example.com)",
            "Accept": "application/json",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip, deflate, br",
            "Connection": "keep-alive",
        }
        self.last_update = 0
        self.current_game = None
        # Thread safety lock for shared game state
        self._games_lock = threading.RLock()
        self.fonts = self._load_fonts()

        # Initialize dynamic team resolver and resolve favorite teams
        # UFC doesn't use team resolution, so make it optional
        raw_favorite_teams = self.mode_config.get("favorite_teams", [])
        if DynamicTeamResolver is not None:
            self.dynamic_resolver = DynamicTeamResolver(cache_manager=cache_manager)
            self.favorite_teams = self.dynamic_resolver.resolve_teams(
                raw_favorite_teams, sport_key
            )
        else:
            self.dynamic_resolver = None
            self.favorite_teams = raw_favorite_teams

        # Log dynamic team resolution
        if raw_favorite_teams != self.favorite_teams:
            self.logger.info(
                f"Resolved dynamic teams: {raw_favorite_teams} -> {self.favorite_teams}"
            )
        else:
            self.logger.info(f"Favorite teams: {self.favorite_teams}")

        self.logger.setLevel(logging.INFO)

        # Initialize team rankings cache
        self._team_rankings_cache = {}
        self._rankings_cache_timestamp = 0
        self._rankings_cache_duration = 3600  # Cache rankings for 1 hour

        # Initialize background data service with optimized settings
        # Hardcoded for memory optimization: 1 worker, 30s timeout, 3 retries
        try:
            from src.background_data_service import get_background_service

            self.background_service = get_background_service(
                self.cache_manager, max_workers=1
            )
            self.background_fetch_requests = {}  # Track background fetch requests
            self.background_enabled = True
            self.logger.info(
                "Background service enabled with 1 worker (memory optimized)"
            )
        except ImportError:
            # Fallback if background service is not available
            self.background_service = None
            self.background_fetch_requests = {}
            self.background_enabled = False
            self.logger.warning(
                "Background service not available - using synchronous fetching"
            )

    def _get_season_schedule_dates(self) -> tuple[str, str]:
        return "", ""

    def _draw_scorebug_layout(self, game: Dict, force_clear: bool = False) -> None:
        """Placeholder draw method - subclasses should override."""
        # This base method will be simple, subclasses provide specifics
        try:
            img = Image.new("RGB", (self.display_width, self.display_height), (0, 0, 0))
            draw = ImageDraw.Draw(img)
            status = game.get("status_text", "N/A")
            self._draw_text_with_outline(draw, status, (2, 2), self.fonts["status"])
            self.display_manager.image.paste(img, (0, 0))
            # Don't call update_display here, let subclasses handle it after drawing
        except Exception as e:
            self.logger.error(
                f"Error in base _draw_scorebug_layout: {e}", exc_info=True
            )

    def display(self, force_clear: bool = False) -> bool:
        """Render the current game. Returns False when nothing can be shown."""
        if not self.is_enabled:  # Check if module is enabled
            return False

        if not self.current_game:
            # Clear the display so old content doesn't persist
            if force_clear:
                self.display_manager.clear()
                self.display_manager.update_display()
            current_time = time.time()
            if not hasattr(self, "_last_warning_time"):
                self._last_warning_time = 0
            if current_time - getattr(self, "_last_warning_time", 0) > 300:
                self.logger.warning(
                    f"No game data available to display in {self.__class__.__name__}"
                )
                setattr(self, "_last_warning_time", current_time)
            return False

        try:
            self._draw_scorebug_layout(self.current_game, force_clear)
            # display_manager.update_display() should be called within subclass draw methods
            # or after calling display() in the main loop. Let's keep it out of the base display.
            return True
        except Exception as e:
            self.logger.error(
                f"Error during display call in {self.__class__.__name__}: {e}",
                exc_info=True,
            )
            return False

    def _load_custom_font_from_element_config(self, element_config: Dict[str, Any], default_size: int = 8) -> ImageFont.FreeTypeFont:
        """
        Load a custom font from an element configuration dictionary.

        Args:
            element_config: Configuration dict for a single element containing 'font' and 'font_size' keys
            default_size: Default font size if not specified in config

        Returns:
            PIL ImageFont object
        """
        # Get font name and size, with defaults
        font_name = element_config.get('font', 'PressStart2P-Regular.ttf')
        font_size = int(element_config.get('font_size', default_size))  # Ensure integer for PIL

        # Build font path
        font_path = os.path.join('assets', 'fonts', font_name)

        # Try to load the font
        try:
            if os.path.exists(font_path):
                # Try loading as TTF first (works for both TTF and some BDF files with PIL)
                if font_path.lower().endswith('.ttf'):
                    font = ImageFont.truetype(font_path, font_size)
                    self.logger.debug(f"Loaded font: {font_name} at size {font_size}")
                    return font
                elif font_path.lower().endswith('.bdf'):
                    # PIL's ImageFont.truetype() can sometimes handle BDF files
                    # If it fails, we'll fall through to the default font
                    try:
                        font = ImageFont.truetype(font_path, font_size)
                        self.logger.debug(f"Loaded BDF font: {font_name} at size {font_size}")
                        return font
                    except Exception:
                        self.logger.warning(f"Could not load BDF font {font_name} with PIL, using default")
                        # Fall through to default
                else:
                    self.logger.warning(f"Unknown font file type: {font_name}, using default")
            else:
                self.logger.warning(f"Font file not found: {font_path}, using default")
        except Exception as e:
            self.logger.error(f"Error loading font {font_name}: {e}, using default")

        # Fall back to default font
        default_font_path = os.path.join('assets', 'fonts', 'PressStart2P-Regular.ttf')
        try:
            if os.path.exists(default_font_path):
                return ImageFont.truetype(default_font_path, font_size)
            else:
                self.logger.warning("Default font not found, using PIL default")
                return ImageFont.load_default()
        except Exception as e:
            self.logger.error(f"Error loading default font: {e}")
            return ImageFont.load_default()

    def _get_layout_offset(self, element: str, axis: str, default: int = 0) -> int:
        """
        Get layout offset for a specific element and axis.

        Args:
            element: Element name (e.g., 'home_logo', 'score', 'status_text')
            axis: 'x_offset' or 'y_offset' (or 'away_x_offset', 'home_x_offset' for records)
            default: Default value if not configured (default: 0)

        Returns:
            Offset value from config or default (always returns int)
        """
        try:
            layout_config = self.config.get('customization', {}).get('layout', {})
            element_config = layout_config.get(element, {})
            offset_value = element_config.get(axis, default)

            # Ensure we return an integer (handle float/string from config)
            if isinstance(offset_value, (int, float)):
                return int(offset_value)
            elif isinstance(offset_value, str):
                # Try to convert string to int
                try:
                    return int(float(offset_value))
                except (ValueError, TypeError):
                    self.logger.warning(
                        f"Invalid layout offset value for {element}.{axis}: '{offset_value}', using default {default}"
                    )
                    return default
            else:
                return default
        except Exception as e:
            # Gracefully handle any config access errors
            self.logger.debug(f"Error reading layout offset for {element}.{axis}: {e}, using default {default}")
            return default

    def _load_fonts(self):
        """Load fonts used by the scoreboard from config or use defaults."""
        fonts = {}

        # Get customization config, with backward compatibility
        customization = self.config.get('customization', {})

        # Load fonts from config with defaults for backward compatibility
        score_config = customization.get('score_text', {})
        period_config = customization.get('period_text', {})
        team_config = customization.get('team_name', {})
        status_config = customization.get('status_text', {})
        detail_config = customization.get('detail_text', {})
        rank_config = customization.get('rank_text', {})

        try:
            fonts["score"] = self._load_custom_font_from_element_config(score_config, default_size=10)
            fonts["time"] = self._load_custom_font_from_element_config(period_config, default_size=8)
            fonts["team"] = self._load_custom_font_from_element_config(team_config, default_size=8)
            fonts["status"] = self._load_custom_font_from_element_config(status_config, default_size=6)
            fonts["detail"] = self._load_custom_font_from_element_config(detail_config, default_size=6)
            fonts["rank"] = self._load_custom_font_from_element_config(rank_config, default_size=10)
            self.logger.info("Successfully loaded fonts from config")
        except Exception as e:
            self.logger.error(f"Error loading fonts: {e}, using defaults")
            # Fallback to hardcoded defaults
            try:
                fonts["score"] = ImageFont.truetype("assets/fonts/PressStart2P-Regular.ttf", 10)
                fonts["time"] = ImageFont.truetype("assets/fonts/PressStart2P-Regular.ttf", 8)
                fonts["team"] = ImageFont.truetype("assets/fonts/PressStart2P-Regular.ttf", 8)
                fonts["status"] = ImageFont.truetype("assets/fonts/4x6-font.ttf", 6)
                fonts["detail"] = ImageFont.truetype("assets/fonts/4x6-font.ttf", 6)
                fonts["rank"] = ImageFont.truetype("assets/fonts/PressStart2P-Regular.ttf", 10)
            except IOError:
                self.logger.warning("Fonts not found, using default PIL font.")
                fonts["score"] = ImageFont.load_default()
                fonts["time"] = ImageFont.load_default()
                fonts["team"] = ImageFont.load_default()
                fonts["status"] = ImageFont.load_default()
                fonts["detail"] = ImageFont.load_default()
                fonts["rank"] = ImageFont.load_default()
        return fonts

    def _draw_dynamic_odds(
        self, draw: ImageDraw.Draw, odds: Dict[str, Any], width: int, height: int
    ) -> None:
        """Draw odds with dynamic positioning - only show negative spread and position O/U based on favored team."""
        try:
            # Skip odds rendering in test mode or if odds data is invalid
            if (
                not odds
                or isinstance(odds, dict)
                and any(
                    isinstance(v, type) and hasattr(v, "__call__")
                    for v in odds.values()
                )
            ):
                self.logger.debug("Skipping odds rendering - test mode or invalid data")
                return

            self.logger.debug(f"Drawing odds with data: {odds}")

            home_team_odds = odds.get("home_team_odds", {})
            away_team_odds = odds.get("away_team_odds", {})
            home_spread = home_team_odds.get("spread_odds")
            away_spread = away_team_odds.get("spread_odds")

            # Get top-level spread as fallback
            top_level_spread = odds.get("spread")

            # If we have a top-level spread and the individual spreads are None or 0, use the top-level
            if top_level_spread is not None:
                if home_spread is None or home_spread == 0.0:
                    home_spread = top_level_spread
                if away_spread is None:
                    away_spread = -top_level_spread

            # Determine which team is favored (has negative spread)
            # Add type checking to handle Mock objects in test environment
            home_favored = False
            away_favored = False

            if home_spread is not None and isinstance(home_spread, (int, float)):
                home_favored = home_spread < 0
            if away_spread is not None and isinstance(away_spread, (int, float)):
                away_favored = away_spread < 0

            # Only show the negative spread (favored team)
            favored_spread = None
            favored_side = None

            if home_favored:
                favored_spread = home_spread
                favored_side = "home"
                self.logger.debug(f"Home team favored with spread: {favored_spread}")
            elif away_favored:
                favored_spread = away_spread
                favored_side = "away"
                self.logger.debug(f"Away team favored with spread: {favored_spread}")
            else:
                self.logger.debug(
                    "No clear favorite - spreads: home={home_spread}, away={away_spread}"
                )

            # Show the negative spread on the appropriate side
            if favored_spread is not None:
                spread_text = str(favored_spread)
                font = self.fonts["detail"]  # Use detail font for odds

                if favored_side == "home":
                    # Home team is favored, show spread on right side
                    spread_width = draw.textlength(spread_text, font=font)
                    spread_x = width - spread_width  # Top right
                    spread_y = 0
                    self._draw_text_with_outline(
                        draw, spread_text, (spread_x, spread_y), font, fill=(0, 255, 0)
                    )
                    self.logger.debug(
                        f"Showing home spread '{spread_text}' on right side"
                    )
                else:
                    # Away team is favored, show spread on left side
                    spread_x = 0  # Top left
                    spread_y = 0
                    self._draw_text_with_outline(
                        draw, spread_text, (spread_x, spread_y), font, fill=(0, 255, 0)
                    )
                    self.logger.debug(
                        f"Showing away spread '{spread_text}' on left side"
                    )

            # Show over/under on the opposite side of the favored team
            over_under = odds.get("over_under")
            if over_under is not None and isinstance(over_under, (int, float)):
                ou_text = f"O/U: {over_under}"
                font = self.fonts["detail"]  # Use detail font for odds
                ou_width = draw.textlength(ou_text, font=font)

                if favored_side == "home":
                    # Home favored, show O/U on left side (opposite of spread)
                    ou_x = 0  # Top left
                    ou_y = 0
                    self.logger.debug(
                        f"Showing O/U '{ou_text}' on left side (home favored)"
                    )
                elif favored_side == "away":
                    # Away favored, show O/U on right side (opposite of spread)
                    ou_x = width - ou_width  # Top right
                    ou_y = 0
                    self.logger.debug(
                        f"Showing O/U '{ou_text}' on right side (away favored)"
                    )
                else:
                    # No clear favorite, show O/U in center
                    ou_x = (width - ou_width) // 2
                    ou_y = 0
                    self.logger.debug(
                        f"Showing O/U '{ou_text}' in center (no clear favorite)"
                    )

                self._draw_text_with_outline(
                    draw, ou_text, (ou_x, ou_y), font, fill=(0, 255, 0)
                )

        except Exception as e:
            self.logger.error(f"Error drawing odds: {e}", exc_info=True)

    def _draw_text_with_outline(
        self, draw, text, position, font, fill=(255, 255, 255), outline_color=(0, 0, 0)
    ):
        """Draw text with a black outline for better readability."""
        x, y = position
        for dx, dy in [
            (-1, -1),
            (-1, 0),
            (-1, 1),
            (0, -1),
            (0, 1),
            (1, -1),
            (1, 0),
            (1, 1),
        ]:
            draw.text((x + dx, y + dy), text, font=font, fill=outline_color)
        draw.text((x, y), text, font=font, fill=fill)

    def _load_and_resize_logo(
        self, team_id: str, team_abbrev: str, logo_path: Path, logo_url: str | None
    ) -> Optional[Image.Image]:
        """Load and resize a team logo, with caching and automatic download if missing."""
        self.logger.debug(f"Logo path: {logo_path}")
        if team_abbrev in self._logo_cache:
            self.logger.debug(f"Using cached logo for {team_abbrev}")
            return self._logo_cache[team_abbrev]

        try:
            # Try different filename variations first (for cases like TA&M vs TAANDM)
            actual_logo_path = None
            filename_variations = LogoDownloader.get_logo_filename_variations(
                team_abbrev
            )

            for filename in filename_variations:
                test_path = logo_path.parent / filename
                if test_path.exists():
                    actual_logo_path = test_path
                    self.logger.debug(
                        f"Found logo at alternative path: {actual_logo_path}"
                    )
                    break

            # If no variation found, try to download missing logo
            if not actual_logo_path and not logo_path.exists():
                self.logger.info(
                    f"Logo not found for {team_abbrev} at {logo_path}. Attempting to download."
                )

                # Try to download the logo from ESPN API (this will create placeholder if download fails)
                download_missing_logo(
                    self.sport_key, team_id, team_abbrev, logo_path, logo_url
                )
                actual_logo_path = logo_path

            # Use the original path if no alternative was found
            if not actual_logo_path:
                actual_logo_path = logo_path

            # Only try to open the logo if the file exists
            if os.path.exists(actual_logo_path):
                logo = Image.open(actual_logo_path)
            else:
                self.logger.error(
                    f"Logo file still doesn't exist at {actual_logo_path} after download attempt"
                )
                return None
            if logo.mode != "RGBA":
                logo = logo.convert("RGBA")

            max_width = int(self.display_width * 1.5)
            max_height = int(self.display_height * 1.5)
            logo.thumbnail((max_width, max_height), Image.Resampling.LANCZOS)
            self._logo_cache[team_abbrev] = logo
            return logo

        except Exception as e:
            self.logger.error(
                f"Error loading logo for {team_abbrev}: {e}", exc_info=True
            )
            return None

    def _fetch_odds(self, game: Dict) -> None:
        """Fetch odds for a specific game using async threading to prevent blocking."""
        try:
            if not self.show_odds:
                return

            # Determine update interval based on game state
            is_live = game.get("is_live", False)
            update_interval = (
                self.mode_config.get("live_odds_update_interval", 60)
                if is_live
                else self.mode_config.get("odds_update_interval", 3600)
            )

            # For upcoming games, use async fetch with short timeout to avoid blocking
            # For live games, we want odds more urgently, but still use async to prevent blocking
            import threading
            import queue

            result_queue = queue.Queue()

            def fetch_odds():
                try:
                    odds_result = self.odds_manager.get_odds(
                        sport=self.sport,
                        league=self.league,
                        event_id=game["id"],
                        update_interval_seconds=update_interval,
                    )
                    result_queue.put(('success', odds_result))
                except Exception as e:
                    result_queue.put(('error', e))

            # Start odds fetch in a separate thread
            odds_thread = threading.Thread(target=fetch_odds)
            odds_thread.daemon = True
            odds_thread.start()

            # Wait for result with timeout (shorter for upcoming games)
            timeout = 2.0 if is_live else 1.5  # Live games get slightly longer timeout
            try:
                result_type, result_data = result_queue.get(timeout=timeout)
                if result_type == 'success':
                    odds_data = result_data
                    if odds_data:
                        game["odds"] = odds_data
                        self.logger.debug(
                            f"Successfully fetched and attached odds for game {game['id']}"
                        )
                    else:
                        self.logger.debug(f"No odds data returned for game {game['id']}")
                else:
                    self.logger.debug(f"Odds fetch failed for game {game['id']}: {result_data}")
            except queue.Empty:
                # Timeout - odds will be fetched on next update if needed
                # This prevents blocking the entire update() method
                self.logger.debug(f"Odds fetch timed out for game {game['id']} (non-blocking)")

        except Exception as e:
            self.logger.error(
                f"Error fetching odds for game {game.get('id', 'N/A')}: {e}"
            )

    def _get_timezone(self):
        """Get timezone from config, with fallback to cache_manager's config_manager."""
        try:
            # First try plugin config
            timezone_str = self.config.get("timezone")
            # If not in plugin config, try to get from cache_manager's config_manager
            if not timezone_str and hasattr(self, 'cache_manager') and hasattr(self.cache_manager, 'config_manager'):
                timezone_str = self.cache_manager.config_manager.get_timezone()
            # Final fallback to UTC
            if not timezone_str:
                timezone_str = "UTC"

            self.logger.debug(f"Using timezone: {timezone_str}")
            return pytz.timezone(timezone_str)
        except pytz.UnknownTimeZoneError:
            self.logger.warning(f"Unknown timezone: {timezone_str}, falling back to UTC")
            return pytz.utc

    def _should_log(self, warning_type: str, cooldown: int = 60) -> bool:
        """Check if we should log a warning based on cooldown period."""
        current_time = time.time()
        if current_time - self._last_warning_time > cooldown:
            self._last_warning_time = current_time
            return True
        return False

    def _fetch_team_rankings(self) -> Dict[str, int]:
        """Fetch team rankings using the new architecture components."""
        current_time = time.time()

        # Check if we have cached rankings that are still valid
        if (
            self._team_rankings_cache
            and current_time - self._rankings_cache_timestamp
            < self._rankings_cache_duration
        ):
            return self._team_rankings_cache

        try:
            data = self.data_source.fetch_standings(self.sport, self.league)

            rankings = {}
            rankings_data = data.get("rankings", [])

            if rankings_data:
                # Use the first ranking (usually AP Top 25)
                first_ranking = rankings_data[0]
                teams = first_ranking.get("ranks", [])

                for team_data in teams:
                    team_info = team_data.get("team", {})
                    team_abbr = team_info.get("abbreviation", "")
                    current_rank = team_data.get("current", 0)

                    if team_abbr and current_rank > 0:
                        rankings[team_abbr] = current_rank

            # Cache the results
            self._team_rankings_cache = rankings
            self._rankings_cache_timestamp = current_time

            self.logger.debug(f"Fetched rankings for {len(rankings)} teams")
            return rankings

        except Exception as e:
            self.logger.error(f"Error fetching team rankings: {e}")
            return {}

    @staticmethod
    def _extract_team_record(team_data: Dict) -> str:
        """Extract the overall record string from a competitor/team object.

        The ESPN scoreboard API uses ``records`` (plural) with a ``summary``
        field, while the team-schedule API uses ``record`` (singular) with a
        ``displayValue`` field.  This helper handles both formats so that
        records display correctly regardless of which API provided the data.
        """
        # Scoreboard API format: records[0].summary  (e.g. "10-2")
        records = team_data.get("records")
        if records and isinstance(records, list) and len(records) > 0:
            return records[0].get("summary", "")

        # Team-schedule API format: record[0].displayValue  (e.g. "7-0")
        record = team_data.get("record")
        if record and isinstance(record, list) and len(record) > 0:
            return record[0].get("displayValue", record[0].get("summary", ""))

        return ""

    def _extract_game_details_common(
        self, game_event: Dict
    ) -> tuple[Dict | None, Dict | None, Dict | None, Dict | None, Dict | None]:
        if not game_event:
            return None, None, None, None, None
        try:
            # Safe access to competitions array
            competitions = game_event.get("competitions", [])
            if not competitions:
                self.logger.warning(f"No competitions data for game {game_event.get('id', 'unknown')}")
                return None, None, None, None, None
            competition = competitions[0]
            status = competition.get("status")
            if not status:
                self.logger.warning(f"No status data for game {game_event.get('id', 'unknown')}")
                return None, None, None, None, None
            competitors = competition.get("competitors", [])
            game_date_str = game_event["date"]
            situation = competition.get("situation")
            start_time_utc = None
            try:
                # Parse the datetime string
                if game_date_str.endswith('Z'):
                    game_date_str = game_date_str.replace('Z', '+00:00')
                dt = datetime.fromisoformat(game_date_str)
                # Ensure the datetime is UTC-aware (fromisoformat may create timezone-aware but not pytz.UTC)
                if dt.tzinfo is None:
                    # If naive, assume it's UTC
                    start_time_utc = dt.replace(tzinfo=pytz.UTC)
                else:
                    # Convert to pytz.UTC for consistency
                    start_time_utc = dt.astimezone(pytz.UTC)
            except ValueError:
                self.logger.warning(f"Could not parse game date: {game_date_str}")

            home_team = next(
                (c for c in competitors if c.get("homeAway") == "home"), None
            )
            away_team = next(
                (c for c in competitors if c.get("homeAway") == "away"), None
            )

            if not home_team or not away_team:
                self.logger.warning(
                    f"Could not find home or away team in event: {game_event.get('id')}"
                )
                return None, None, None, None, None

            try:
                home_abbr = home_team["team"]["abbreviation"]
            except KeyError:
                home_abbr = home_team["team"]["name"][:3]
            try:
                away_abbr = away_team["team"]["abbreviation"]
            except KeyError:
                away_abbr = away_team["team"]["name"][:3]

            # Check if this is a favorite team game BEFORE doing expensive logging
            is_favorite_game = self.favorite_teams and (
                home_abbr in self.favorite_teams or away_abbr in self.favorite_teams
            )

            # Only log debug info for favorite team games
            if is_favorite_game:
                self.logger.debug(
                    f"Processing favorite team game: {game_event.get('id')}"
                )
                self.logger.debug(
                    f"Found teams: {away_abbr}@{home_abbr}, Status: {status['type']['name']}, State: {status['type']['state']}"
                )

            game_time, game_date = "", ""
            if start_time_utc:
                local_time = start_time_utc.astimezone(self._get_timezone())
                game_time = local_time.strftime("%I:%M%p").lstrip("0")

                # Check date format from config
                use_short_date_format = self.config.get("display", {}).get(
                    "use_short_date_format", False
                )
                if use_short_date_format:
                    game_date = local_time.strftime("%-m/%-d")
                else:
                    # Note: display_manager.format_date_with_ordinal will be handled by plugin wrapper
                    game_date = local_time.strftime("%m/%d")  # Simplified for plugin

            home_record = self._extract_team_record(home_team)
            away_record = self._extract_team_record(away_team)

            # Don't show "0-0" records - set to blank instead
            if home_record in {"0-0", "0-0-0"}:
                home_record = ""
            if away_record in {"0-0", "0-0-0"}:
                away_record = ""

            details = {
                "id": game_event.get("id"),
                "game_time": game_time,
                "game_date": game_date,
                "start_time_utc": start_time_utc,
                "status_text": status["type"][
                    "shortDetail"
                ],  # e.g., "Final", "7:30 PM", "Q1 12:34"
                "is_live": status["type"]["state"] == "in",
                "is_final": status["type"]["state"] == "post",
                "is_upcoming": (
                    status["type"]["state"] == "pre"
                    or status["type"]["name"].lower()
                    in ["scheduled", "pre-game", "status_scheduled"]
                ),
                "is_halftime": status["type"]["state"] == "halftime"
                or status["type"]["name"] == "STATUS_HALFTIME",  # Added halftime check
                "is_period_break": status["type"]["name"]
                == "STATUS_END_PERIOD",  # Added Period Break check
                "home_abbr": home_abbr,
                "home_id": home_team["id"],
                "home_score": home_team.get("score", "0"),
                "home_logo_path": self.logo_dir
                / Path(f"{LogoDownloader.normalize_abbreviation(home_abbr)}.png"),
                "home_logo_url": home_team["team"].get("logo"),
                "home_record": home_record,
                "away_record": away_record,
                "away_abbr": away_abbr,
                "away_id": away_team["id"],
                "away_score": away_team.get("score", "0"),
                "away_logo_path": self.logo_dir
                / Path(f"{LogoDownloader.normalize_abbreviation(away_abbr)}.png"),
                "away_logo_url": away_team["team"].get("logo"),
                "is_within_window": True,  # Whether game is within display window
            }
            return details, home_team, away_team, status, situation
        except Exception as e:
            # Log the problematic event structure if possible
            self.logger.error(
                f"Error extracting game details: {e} from event: {game_event.get('id')}",
                exc_info=True,
            )
            return None, None, None, None, None

    @abstractmethod
    def _extract_game_details(self, game_event: dict) -> dict | None:
        details, _, _, _, _ = self._extract_game_details_common(game_event)
        return details

    @abstractmethod
    def _fetch_data(self) -> Optional[Dict]:
        pass

    def _fetch_todays_games(self) -> Optional[Dict]:
        """Fetch only today's games for live updates (not entire season)."""
        try:
            now = datetime.now()
            formatted_date = now.strftime("%Y%m%d")
            # Fetch todays games only
            url = f"https://site.api.espn.com/apis/site/v2/sports/{self.sport}/{self.league}/scoreboard"
            self.logger.debug(f"Fetching today's games for {self.sport}/{self.league} on date {formatted_date}")
            response = self.session.get(
                url,
                params={"dates": formatted_date, "limit": 1000},
                headers=self.headers,
                timeout=10,
            )
            response.raise_for_status()
            data = response.json()
            events = data.get("events", [])

            self.logger.info(
                f"Fetched {len(events)} todays games for {self.sport} - {self.league}"
            )

            # Log status of each game for debugging
            if events:
                for event in events:
                    status = event.get("competitions", [{}])[0].get("status", {})
                    status_type = status.get("type", {})
                    state = status_type.get("state", "unknown")
                    name = status_type.get("name", "unknown")
                    self.logger.debug(
                        f"Event {event.get('id', 'unknown')}: state={state}, name={name}, "
                        f"shortDetail={status_type.get('shortDetail', 'N/A')}"
                    )

            return {"events": events}
        except requests.exceptions.RequestException as e:
            self.logger.error(
                f"API error fetching todays games for {self.sport} - {self.league}: {e}"
            )
            return None

    def _get_weeks_data(self) -> Optional[Dict]:
        """
        Get partial data for immediate display while background fetch is in progress.
        This fetches current/recent games only for quick response.
        """
        try:
            # Fetch current week and next few days for immediate display
            now = datetime.now(pytz.utc)
            immediate_events = []

            start_date = now + timedelta(weeks=-2)
            end_date = now + timedelta(weeks=1)
            date_str = f"{start_date.strftime('%Y%m%d')}-{end_date.strftime('%Y%m%d')}"
            url = f"https://site.api.espn.com/apis/site/v2/sports/{self.sport}/{self.league}/scoreboard"
            response = self.session.get(
                url,
                params={"dates": date_str, "limit": 1000},
                headers=self.headers,
                timeout=10,
            )
            response.raise_for_status()
            data = response.json()
            immediate_events = data.get("events", [])

            if immediate_events:
                self.logger.info(f"Fetched {len(immediate_events)} events {date_str}")
                return {"events": immediate_events}

        except requests.exceptions.RequestException as e:
            self.logger.warning(
                f"Error fetching this weeks games for {self.sport} - {self.league} - {date_str}: {e}"
            )
        return None

    def _custom_scorebug_layout(self, game: dict, draw_overlay: ImageDraw.ImageDraw):
        pass

    def cleanup(self):
        """Clean up resources when plugin is unloaded."""
        # Close HTTP session
        if hasattr(self, 'session') and self.session:
            try:
                self.session.close()
            except Exception as e:
                self.logger.warning(f"Error closing session: {e}")

        # Clear caches
        if hasattr(self, '_logo_cache'):
            self._logo_cache.clear()

        self.logger.info(f"{self.__class__.__name__} cleanup completed")


class SportsUpcoming(SportsCore):
    def __init__(
        self,
        config: Dict[str, Any],
        display_manager,
        cache_manager,
        logger: logging.Logger,
        sport_key: str,
    ):
        super().__init__(config, display_manager, cache_manager, logger, sport_key)
        self.upcoming_games = []  # Store all fetched upcoming games initially
        self.games_list = []  # Filtered list for display (favorite teams)
        self.current_game_index = 0
        self.last_update = 0
        self.update_interval = self.mode_config.get(
            "upcoming_update_interval", 3600
        )  # Check for recent games every hour
        self.last_log_time = 0
        self.log_interval = 300
        self.last_warning_time = 0
        self.warning_cooldown = 300
        self.last_game_switch = 0
        self.game_display_duration = self.mode_config.get("upcoming_game_duration", 15)

    def _select_games_for_display(
        self, processed_games: List[Dict], favorite_teams: List[str]
    ) -> List[Dict]:
        """
        Single-pass game selection with proper deduplication and counting.

        When a game involves two favorite teams, it counts toward BOTH teams' limits.
        This prevents unexpected game counts from the multi-pass algorithm.
        """
        # Sort by start time for consistent priority
        sorted_games = sorted(
            processed_games,
            key=lambda g: g.get("start_time_utc")
            or datetime.max.replace(tzinfo=timezone.utc),
        )

        if not favorite_teams:
            # No favorites: return all games (caller will apply limits)
            return sorted_games

        selected_games = []
        selected_ids = set()
        team_counts = {team: 0 for team in favorite_teams}

        for game in sorted_games:
            game_id = game.get("id")
            if game_id in selected_ids:
                continue

            home = game.get("home_abbr")
            away = game.get("away_abbr")

            home_fav = home in favorite_teams
            away_fav = away in favorite_teams

            if not home_fav and not away_fav:
                continue

            # Check if at least one favorite team still needs games
            home_needs = home_fav and team_counts[home] < self.upcoming_games_to_show
            away_needs = away_fav and team_counts[away] < self.upcoming_games_to_show

            if home_needs or away_needs:
                selected_games.append(game)
                selected_ids.add(game_id)
                # Count game for ALL favorite teams involved
                # This is key: one game counts toward limits of BOTH teams if both are favorites
                if home_fav:
                    team_counts[home] += 1
                if away_fav:
                    team_counts[away] += 1

                self.logger.debug(
                    f"Selected game {away}@{home}: team_counts={team_counts}"
                )

            # Check if all favorites are satisfied
            if all(c >= self.upcoming_games_to_show for c in team_counts.values()):
                self.logger.debug("All favorite teams satisfied, stopping selection")
                break

        self.logger.info(
            f"Selected {len(selected_games)} games for {len(favorite_teams)} "
            f"favorite teams: {team_counts}"
        )
        return selected_games

    def update(self):
        """Update upcoming games data."""
        if not self.is_enabled:
            return
        current_time = time.time()
        if current_time - self.last_update < self.update_interval:
            return

        self.last_update = current_time

        # Fetch rankings if enabled
        if self.show_ranking:
            self._fetch_team_rankings()

        try:
            data = self._fetch_data()  # Uses shared cache
            if not data or "events" not in data:
                self.logger.warning(
                    "No events found in shared data."
                )  # Changed log prefix
                if not self.games_list:
                    self.current_game = None
                return

            events = data["events"]
            # self.logger.info(f"Processing {len(events)} events from shared data.") # Changed log prefix

            processed_games = []
            favorite_games_found = 0
            all_upcoming_games = 0  # Count all upcoming games regardless of favorites

            for event in events:
                game = self._extract_game_details(event)
                # Count all upcoming games for debugging
                if game and game["is_upcoming"]:
                    all_upcoming_games += 1

                # Filter criteria: must be upcoming ('pre' state)
                if game and game["is_upcoming"]:
                    # Only fetch odds for games that will be displayed
                    # If show_favorite_teams_only is True, filter by favorite teams
                    # But if no favorite teams are configured, show all games (fallback)
                    if self.show_favorite_teams_only and self.favorite_teams:
                        if (
                            game["home_abbr"] not in self.favorite_teams
                            and game["away_abbr"] not in self.favorite_teams
                        ):
                            continue
                    processed_games.append(game)
                    # Count favorite team games for logging
                    if self.favorite_teams and (
                        game["home_abbr"] in self.favorite_teams
                        or game["away_abbr"] in self.favorite_teams
                    ):
                        favorite_games_found += 1
                    if self.show_odds:
                        self._fetch_odds(game)

            # Enhanced logging for debugging
            self.logger.info(f"Found {all_upcoming_games} total upcoming games in data")
            self.logger.info(
                f"Found {len(processed_games)} upcoming games after filtering"
            )

            if processed_games:
                for game in processed_games[:3]:  # Show first 3
                    self.logger.info(
                        f"  {game['away_abbr']}@{game['home_abbr']} - {game['start_time_utc']}"
                    )

            if self.favorite_teams and all_upcoming_games > 0:
                self.logger.info(f"Favorite teams: {self.favorite_teams}")
                self.logger.info(
                    f"Found {favorite_games_found} favorite team upcoming games"
                )

            # Use single-pass algorithm for game selection
            # This properly handles games between two favorite teams (counts for both)
            if self.show_favorite_teams_only and self.favorite_teams:
                team_games = self._select_games_for_display(
                    processed_games, self.favorite_teams
                )
            else:
                # No favorite teams: show N total games sorted by time (schedule view)
                team_games = sorted(
                    processed_games,
                    key=lambda g: g.get("start_time_utc")
                    or datetime.max.replace(tzinfo=timezone.utc),
                )[:self.upcoming_games_to_show]
                self.logger.info(
                    f"No favorites configured: showing {len(team_games)} total upcoming games"
                )

            # Log changes or periodically
            should_log = (
                current_time - self.last_log_time >= self.log_interval
                or len(team_games) != len(self.games_list)
                or any(
                    g1["id"] != g2.get("id")
                    for g1, g2 in zip(self.games_list, team_games)
                )
                or (not self.games_list and team_games)
            )

            # Check if the list of games to display has changed (thread-safe)
            with self._games_lock:
                new_game_ids = {g["id"] for g in team_games}
                current_game_ids = {g["id"] for g in self.games_list}

                if new_game_ids != current_game_ids:
                    self.logger.info(
                        f"Found {len(team_games)} upcoming games within window for display."
                    )  # Changed log prefix
                    self.games_list = team_games
                    if (
                        not self.current_game
                        or not self.games_list
                        or self.current_game["id"] not in new_game_ids
                    ):
                        self.current_game_index = 0
                        self.current_game = self.games_list[0] if self.games_list else None
                        self.last_game_switch = current_time
                    else:
                        try:
                            self.current_game_index = next(
                                i
                                for i, g in enumerate(self.games_list)
                                if g["id"] == self.current_game["id"]
                            )
                            self.current_game = self.games_list[self.current_game_index]
                        except StopIteration:
                            self.current_game_index = 0
                            self.current_game = self.games_list[0]
                            self.last_game_switch = current_time

                elif self.games_list:
                    self.current_game = self.games_list[
                        self.current_game_index
                    ]  # Update data

                if not self.games_list:
                    self.logger.info(
                        "No relevant upcoming games found to display."
                    )  # Changed log prefix
                    self.current_game = None

            if should_log and not self.games_list:
                # Log favorite teams only if no games are found and logging is needed
                self.logger.debug(
                    f"Favorite teams: {self.favorite_teams}"
                )  # Changed log prefix
                self.logger.debug(
                    f"Total upcoming games before filtering: {len(processed_games)}"
                )  # Changed log prefix
                self.last_log_time = current_time
            elif should_log:
                self.last_log_time = current_time

        except Exception as e:
            self.logger.error(
                f"Error updating upcoming games: {e}", exc_info=True
            )  # Changed log prefix
            # self.current_game = None # Decide if clear on error

    def _draw_scorebug_layout(self, game: Dict, force_clear: bool = False) -> None:
        """Draw the layout for an upcoming game."""
        try:
            # Clear the display first to ensure full coverage (like weather plugin does)
            if force_clear:
                self.display_manager.clear()

            # Use display_manager.matrix dimensions directly to ensure full display coverage
            display_width = self.display_manager.matrix.width if hasattr(self.display_manager, 'matrix') and self.display_manager.matrix else self.display_width
            display_height = self.display_manager.matrix.height if hasattr(self.display_manager, 'matrix') and self.display_manager.matrix else self.display_height

            main_img = Image.new(
                "RGBA", (display_width, display_height), (0, 0, 0, 255)
            )
            overlay = Image.new(
                "RGBA", (display_width, display_height), (0, 0, 0, 0)
            )
            draw_overlay = ImageDraw.Draw(overlay)

            home_logo = self._load_and_resize_logo(
                game["home_id"],
                game["home_abbr"],
                game["home_logo_path"],
                game.get("home_logo_url"),
            )
            away_logo = self._load_and_resize_logo(
                game["away_id"],
                game["away_abbr"],
                game["away_logo_path"],
                game.get("away_logo_url"),
            )

            if not home_logo or not away_logo:
                self.logger.error(
                    f"Failed to load logos for game: {game.get('id')}"
                )  # Changed log prefix
                draw_final = ImageDraw.Draw(main_img.convert("RGB"))
                self._draw_text_with_outline(
                    draw_final, "Logo Error", (5, 5), self.fonts["status"]
                )
                self.display_manager.image = main_img.convert("RGB")
                self.display_manager.update_display()
                return

            center_y = display_height // 2

            # MLB-style logo positions with layout offsets
            home_x = display_width - home_logo.width + 2 + self._get_layout_offset('home_logo', 'x_offset')
            home_y = center_y - (home_logo.height // 2) + self._get_layout_offset('home_logo', 'y_offset')
            main_img.paste(home_logo, (home_x, home_y), home_logo)

            away_x = -2 + self._get_layout_offset('away_logo', 'x_offset')
            away_y = center_y - (away_logo.height // 2) + self._get_layout_offset('away_logo', 'y_offset')
            main_img.paste(away_logo, (away_x, away_y), away_logo)

            # Draw Text Elements on Overlay
            game_date = game.get("game_date", "")
            game_time = game.get("game_time", "")

            # Note: Rankings are now handled in the records/rankings section below

            # "Next Game" at the top (use smaller status font) with layout offsets
            status_font = self.fonts["status"]
            if display_width > 128:
                status_font = self.fonts["time"]
            status_text = "Next Game"
            status_width = draw_overlay.textlength(status_text, font=status_font)
            status_x = (display_width - status_width) // 2 + self._get_layout_offset('status_text', 'x_offset')
            status_y = 1 + self._get_layout_offset('status_text', 'y_offset')  # Changed from 2
            self._draw_text_with_outline(
                draw_overlay, status_text, (status_x, status_y), status_font
            )

            # Date text (centered, below "Next Game") with layout offsets
            date_width = draw_overlay.textlength(game_date, font=self.fonts["time"])
            date_x = (display_width - date_width) // 2 + self._get_layout_offset('date', 'x_offset')
            # Adjust Y position to stack date and time nicely
            date_y = center_y - 7 + self._get_layout_offset('date', 'y_offset')  # Raise date slightly
            self._draw_text_with_outline(
                draw_overlay, game_date, (date_x, date_y), self.fonts["time"]
            )

            # Time text (centered, below Date) with layout offsets
            time_width = draw_overlay.textlength(game_time, font=self.fonts["time"])
            time_x = (display_width - time_width) // 2 + self._get_layout_offset('time', 'x_offset')
            time_y = date_y + 9 + self._get_layout_offset('time', 'y_offset')  # Place time below date
            self._draw_text_with_outline(
                draw_overlay, game_time, (time_x, time_y), self.fonts["time"]
            )

            # Draw odds if available
            if "odds" in game and game["odds"]:
                self._draw_dynamic_odds(
                    draw_overlay, game["odds"], display_width, display_height
                )

            # Draw records or rankings if enabled
            if self.show_records or self.show_ranking:
                try:
                    record_font = ImageFont.truetype("assets/fonts/4x6-font.ttf", 6)
                    self.logger.debug(f"Loaded 6px record font successfully")
                except IOError:
                    record_font = ImageFont.load_default()
                    self.logger.warning(
                        f"Failed to load 6px font, using default font (size: {record_font.size})"
                    )

                # Get team abbreviations
                away_abbr = game.get("away_abbr", "")
                home_abbr = game.get("home_abbr", "")

                record_bbox = draw_overlay.textbbox((0, 0), "0-0", font=record_font)
                record_height = record_bbox[3] - record_bbox[1]
                record_y = self.display_height - record_height + self._get_layout_offset('records', 'y_offset')
                self.logger.debug(
                    f"Record positioning: height={record_height}, record_y={record_y}, display_height={self.display_height}"
                )

                # Display away team info
                if away_abbr:
                    if self.show_ranking and self.show_records:
                        # When both rankings and records are enabled, rankings replace records completely
                        away_rank = self._team_rankings_cache.get(away_abbr, 0)
                        if away_rank > 0:
                            away_text = f"#{away_rank}"
                        else:
                            # Show nothing for unranked teams when rankings are prioritized
                            away_text = ""
                    elif self.show_ranking:
                        # Show ranking only if available
                        away_rank = self._team_rankings_cache.get(away_abbr, 0)
                        if away_rank > 0:
                            away_text = f"#{away_rank}"
                        else:
                            away_text = ""
                    elif self.show_records:
                        # Show record only when rankings are disabled
                        away_text = game.get("away_record", "")
                    else:
                        away_text = ""

                    if away_text:
                        away_record_x = 0 + self._get_layout_offset('records', 'away_x_offset')
                        self.logger.debug(
                            f"Drawing away ranking '{away_text}' at ({away_record_x}, {record_y}) with font size {record_font.size if hasattr(record_font, 'size') else 'unknown'}"
                        )
                        self._draw_text_with_outline(
                            draw_overlay,
                            away_text,
                            (away_record_x, record_y),
                            record_font,
                        )

                # Display home team info
                if home_abbr:
                    if self.show_ranking and self.show_records:
                        # When both rankings and records are enabled, rankings replace records completely
                        home_rank = self._team_rankings_cache.get(home_abbr, 0)
                        if home_rank > 0:
                            home_text = f"#{home_rank}"
                        else:
                            # Show nothing for unranked teams when rankings are prioritized
                            home_text = ""
                    elif self.show_ranking:
                        # Show ranking only if available
                        home_rank = self._team_rankings_cache.get(home_abbr, 0)
                        if home_rank > 0:
                            home_text = f"#{home_rank}"
                        else:
                            home_text = ""
                    elif self.show_records:
                        # Show record only when rankings are disabled
                        home_text = game.get("home_record", "")
                    else:
                        home_text = ""

                    if home_text:
                        home_record_bbox = draw_overlay.textbbox(
                            (0, 0), home_text, font=record_font
                        )
                        home_record_width = home_record_bbox[2] - home_record_bbox[0]
                        home_record_x = self.display_width - home_record_width + self._get_layout_offset('records', 'home_x_offset')
                        self.logger.debug(
                            f"Drawing home ranking '{home_text}' at ({home_record_x}, {record_y}) with font size {record_font.size if hasattr(record_font, 'size') else 'unknown'}"
                        )
                        self._draw_text_with_outline(
                            draw_overlay,
                            home_text,
                            (home_record_x, record_y),
                            record_font,
                        )

            # Composite and display
            main_img = Image.alpha_composite(main_img, overlay)
            main_img = main_img.convert("RGB")
            self.display_manager.image.paste(main_img, (0, 0))
            self.display_manager.update_display()  # Update display here

        except Exception as e:
            self.logger.error(
                f"Error displaying upcoming game: {e}", exc_info=True
            )  # Changed log prefix

    def display(self, force_clear=False) -> bool:
        """Display upcoming games, handling switching."""
        if not self.is_enabled:
            return False

        if not self.games_list:
            # Clear the display so old content doesn't persist
            if force_clear:
                self.display_manager.clear()
                self.display_manager.update_display()
            if self.current_game:
                self.current_game = None  # Clear state if list empty
            current_time = time.time()
            # Log warning periodically if no games found
            if current_time - self.last_warning_time > self.warning_cooldown:
                self.logger.info(
                    "No upcoming games found for favorite teams to display."
                )  # Changed log prefix
                self.last_warning_time = current_time
            return False  # Skip display update

        try:
            current_time = time.time()

            # Check if it's time to switch games (protected by lock for thread safety)
            with self._games_lock:
                if (
                    len(self.games_list) > 1
                    and current_time - self.last_game_switch >= self.game_display_duration
                ):
                    self.current_game_index = (self.current_game_index + 1) % len(
                        self.games_list
                    )
                    self.current_game = self.games_list[self.current_game_index]
                    self.last_game_switch = current_time
                    force_clear = True  # Force redraw on switch

                    # Log team switching with sport prefix
                    if self.current_game:
                        away_abbr = self.current_game.get("away_abbr", "UNK")
                        home_abbr = self.current_game.get("home_abbr", "UNK")
                        sport_prefix = (
                            self.sport_key.upper()
                            if hasattr(self, "sport_key")
                            else "SPORT"
                        )
                        self.logger.info(
                            f"[{sport_prefix} Upcoming] Showing {away_abbr} vs {home_abbr}"
                        )
                    else:
                        self.logger.debug(
                            f"Switched to game index {self.current_game_index}"
                        )

            if self.current_game:
                self._draw_scorebug_layout(self.current_game, force_clear)
            # update_display() is called within _draw_scorebug_layout for upcoming

        except Exception as e:
            self.logger.error(
                f"Error in display loop: {e}", exc_info=True
            )  # Changed log prefix
            return False

        return True


class SportsRecent(SportsCore):

    def __init__(
        self,
        config: Dict[str, Any],
        display_manager,
        cache_manager,
        logger: logging.Logger,
        sport_key: str,
    ):
        super().__init__(config, display_manager, cache_manager, logger, sport_key)
        self.recent_games = []  # Store all fetched recent games initially
        self.games_list = []  # Filtered list for display (favorite teams)
        self.current_game_index = 0
        self.last_update = 0
        self.update_interval = self.mode_config.get(
            "recent_update_interval", 3600
        )  # Check for recent games every hour
        self.last_game_switch = 0
        self.game_display_duration = self.mode_config.get("recent_game_duration", 15)
        self._zero_clock_timestamps: Dict[str, float] = {}  # Track games at 0:00

    def _get_zero_clock_duration(self, game_id: str) -> float:
        """Track how long a game has been at 0:00 clock."""
        current_time = time.time()
        if game_id not in self._zero_clock_timestamps:
            self._zero_clock_timestamps[game_id] = current_time
            return 0.0
        return current_time - self._zero_clock_timestamps[game_id]

    def _clear_zero_clock_tracking(self, game_id: str) -> None:
        """Clear tracking when game clock moves away from 0:00 or game ends."""
        if game_id in self._zero_clock_timestamps:
            del self._zero_clock_timestamps[game_id]

    def _select_recent_games_for_display(
        self, processed_games: List[Dict], favorite_teams: List[str]
    ) -> List[Dict]:
        """
        Single-pass game selection for recent games with proper deduplication.

        When a game involves two favorite teams, it counts toward BOTH teams' limits.
        Games are sorted by most recent first.
        """
        # Sort by start time, most recent first
        sorted_games = sorted(
            processed_games,
            key=lambda g: g.get("start_time_utc")
            or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        )

        if not favorite_teams:
            # No favorites: return all games (caller will apply limits)
            return sorted_games

        selected_games = []
        selected_ids = set()
        team_counts = {team: 0 for team in favorite_teams}

        for game in sorted_games:
            game_id = game.get("id")
            if game_id in selected_ids:
                continue

            home = game.get("home_abbr")
            away = game.get("away_abbr")

            home_fav = home in favorite_teams
            away_fav = away in favorite_teams

            if not home_fav and not away_fav:
                continue

            # Check if at least one favorite team still needs games
            home_needs = home_fav and team_counts[home] < self.recent_games_to_show
            away_needs = away_fav and team_counts[away] < self.recent_games_to_show

            if home_needs or away_needs:
                selected_games.append(game)
                selected_ids.add(game_id)
                # Count game for ALL favorite teams involved
                if home_fav:
                    team_counts[home] += 1
                if away_fav:
                    team_counts[away] += 1

                self.logger.debug(
                    f"Selected recent game {away}@{home}: team_counts={team_counts}"
                )

            # Check if all favorites are satisfied
            if all(c >= self.recent_games_to_show for c in team_counts.values()):
                self.logger.debug("All favorite teams satisfied, stopping selection")
                break

        return selected_games

    def update(self):
        """Update recent games data."""
        if not self.is_enabled:
            return
        current_time = time.time()
        if current_time - self.last_update < self.update_interval:
            return

        self.last_update = current_time  # Update time even if fetch fails

        # Fetch rankings if enabled
        if self.show_ranking:
            self._fetch_team_rankings()

        try:
            data = self._fetch_data()  # Uses shared cache
            if not data or "events" not in data:
                self.logger.warning(
                    "No events found in shared data."
                )  # Changed log prefix
                if not self.games_list:
                    self.current_game = None  # Clear display if no games were showing
                return

            events = data["events"]
            self.logger.info(
                f"Processing {len(events)} events from shared data."
            )  # Changed log prefix

            # Define date range for "recent" games (last 21 days to capture games from 3 weeks ago)
            now = datetime.now(timezone.utc)
            recent_cutoff = now - timedelta(days=21)
            self.logger.info(
                f"Current time: {now}, Recent cutoff: {recent_cutoff} (21 days ago)"
            )

            # Process games and filter for final games, date range & favorite teams
            processed_games = []
            for event in events:
                game = self._extract_game_details(event)
                if not game:
                    continue

                # Check if game appears finished even if not marked as "post" yet
                # This handles cases where API hasn't updated status yet
                appears_finished = False
                game_id = game.get("id")
                if not game.get("is_final", False):
                    # Check if game appears to be over based on clock/period
                    clock = game.get("clock", "")
                    period = game.get("period", 0)
                    period_text = game.get("status_text", "").lower()

                    if period >= 4:
                        clock_normalized = clock.replace(":", "").strip()

                        # Explicit "final" in status text is definitive
                        if "final" in period_text:
                            appears_finished = True
                            self._clear_zero_clock_tracking(game_id)
                            self.logger.debug(
                                f"Game {game.get('away_abbr')}@{game.get('home_abbr')} "
                                f"appears finished (period_text contains 'final')"
                            )
                        elif clock_normalized in ["000", "00", ""] or clock == "0:00" or clock == ":00":
                            # Clock at 0:00 but no explicit final - use grace period
                            # This prevents premature transitions during potential OT or reviews
                            zero_clock_duration = self._get_zero_clock_duration(game_id)

                            # Only mark finished after 2 minute grace period (allows OT decisions)
                            if zero_clock_duration >= 120:
                                appears_finished = True
                                self.logger.debug(
                                    f"Game {game.get('away_abbr')}@{game.get('home_abbr')} "
                                    f"appears finished after {zero_clock_duration:.0f}s at 0:00 "
                                    f"(period={period}, clock={clock})"
                                )
                            else:
                                self.logger.debug(
                                    f"Game {game.get('away_abbr')}@{game.get('home_abbr')} "
                                    f"at 0:00 but only for {zero_clock_duration:.0f}s - waiting for confirmation"
                                )
                        else:
                            # Clock is not at 0:00, clear any tracking
                            self._clear_zero_clock_tracking(game_id)
                else:
                    # Game is marked final, clear tracking
                    self._clear_zero_clock_tracking(game_id)

                # Filter criteria: must be final OR appear finished, AND within recent date range
                is_eligible = game.get("is_final", False) or appears_finished
                if is_eligible:
                    game_time = game.get("start_time_utc")
                    if game_time and game_time >= recent_cutoff:
                        processed_games.append(game)
                        # Log when adding games, especially if they appear finished but aren't marked final
                        final_status = "final" if game.get("is_final") else "appears finished"
                        self.logger.info(
                            f"Added {final_status} game to recent list: "
                            f"{game.get('away_abbr')}@{game.get('home_abbr')} "
                            f"({game.get('away_score')}-{game.get('home_score')}) "
                            f"at {game_time.strftime('%Y-%m-%d %H:%M:%S UTC') if game_time else 'unknown time'}"
                        )
                    elif game_time:
                        self.logger.debug(
                            f"Game {game.get('away_abbr')}@{game.get('home_abbr')} "
                            f"is final but outside date range (game_time={game_time}, cutoff={recent_cutoff})"
                        )
                else:
                    # Log why game was filtered out (only for favorite teams to reduce noise)
                    if self.favorite_teams and (game.get("home_abbr") in self.favorite_teams or game.get("away_abbr") in self.favorite_teams):
                        self.logger.debug(
                            f"Game {game.get('away_abbr')}@{game.get('home_abbr')} "
                            f"not included: is_final={game.get('is_final')}, "
                            f"period={game.get('period')}, clock={game.get('clock')}, "
                            f"status={game.get('status_text')}"
                        )
            # Use single-pass algorithm for game selection
            # This properly handles games between two favorite teams (counts for both)
            if self.show_favorite_teams_only and self.favorite_teams:
                team_games = self._select_recent_games_for_display(
                    processed_games, self.favorite_teams
                )
                # Debug: Show which games are selected for display
                for i, game in enumerate(team_games):
                    self.logger.info(
                        f"Game {i+1} for display: {game['away_abbr']} @ {game['home_abbr']} - {game.get('start_time_utc')} - Score: {game['away_score']}-{game['home_score']}"
                    )
            else:
                # No favorites or show_favorite_teams_only disabled: show N total games sorted by time
                team_games = sorted(
                    processed_games,
                    key=lambda g: g.get("start_time_utc")
                    or datetime.min.replace(tzinfo=timezone.utc),
                    reverse=True,
                )[:self.recent_games_to_show]
                self.logger.info(
                    f"No favorites configured: showing {len(team_games)} total recent games"
                )

            # Check if the list of games to display has changed (thread-safe)
            with self._games_lock:
                new_game_ids = {g["id"] for g in team_games}
                current_game_ids = {g["id"] for g in self.games_list}

                if new_game_ids != current_game_ids:
                    self.logger.info(
                        f"Found {len(team_games)} final games within window for display."
                    )  # Changed log prefix
                    self.games_list = team_games
                    # Reset index if list changed or current game removed
                    if (
                        not self.current_game
                        or not self.games_list
                        or self.current_game["id"] not in new_game_ids
                    ):
                        self.current_game_index = 0
                        self.current_game = self.games_list[0] if self.games_list else None
                        self.last_game_switch = current_time  # Reset switch timer
                    else:
                        # Try to maintain position if possible
                        try:
                            self.current_game_index = next(
                                i
                                for i, g in enumerate(self.games_list)
                                if g["id"] == self.current_game["id"]
                            )
                            self.current_game = self.games_list[
                                self.current_game_index
                            ]  # Update data just in case
                        except StopIteration:
                            self.current_game_index = 0
                            self.current_game = self.games_list[0]
                            self.last_game_switch = current_time

                elif self.games_list:
                    # List content is same, just update data for current game
                    self.current_game = self.games_list[self.current_game_index]

                if not self.games_list:
                    self.logger.info(
                        "No relevant recent games found to display."
                    )  # Changed log prefix
                    self.current_game = None  # Ensure display clears if no games

        except Exception as e:
            self.logger.error(
                f"Error updating recent games: {e}", exc_info=True
            )  # Changed log prefix
            # Don't clear current game on error, keep showing last known state
            # self.current_game = None # Decide if we want to clear display on error

    def _draw_scorebug_layout(self, game: Dict, force_clear: bool = False) -> None:
        """Draw the layout for a recently completed game."""
        try:
            # Clear the display first to ensure full coverage (like weather plugin does)
            if force_clear:
                self.display_manager.clear()

            # Use display_manager.matrix dimensions directly to ensure full display coverage
            display_width = self.display_manager.matrix.width if hasattr(self.display_manager, 'matrix') and self.display_manager.matrix else self.display_width
            display_height = self.display_manager.matrix.height if hasattr(self.display_manager, 'matrix') and self.display_manager.matrix else self.display_height

            main_img = Image.new(
                "RGBA", (display_width, display_height), (0, 0, 0, 255)
            )
            overlay = Image.new(
                "RGBA", (display_width, display_height), (0, 0, 0, 0)
            )
            draw_overlay = ImageDraw.Draw(overlay)

            home_logo = self._load_and_resize_logo(
                game["home_id"],
                game["home_abbr"],
                game["home_logo_path"],
                game.get("home_logo_url"),
            )
            away_logo = self._load_and_resize_logo(
                game["away_id"],
                game["away_abbr"],
                game["away_logo_path"],
                game.get("away_logo_url"),
            )

            if not home_logo or not away_logo:
                self.logger.error(
                    f"Failed to load logos for game: {game.get('id')}"
                )  # Changed log prefix
                # Draw placeholder text if logos fail (similar to live)
                draw_final = ImageDraw.Draw(main_img.convert("RGB"))
                self._draw_text_with_outline(
                    draw_final, "Logo Error", (5, 5), self.fonts["status"]
                )
                self.display_manager.image = main_img.convert("RGB")
                self.display_manager.update_display()
                return

            center_y = display_height // 2

            # MLB-style logo positioning (closer to edges) with layout offsets
            home_x = display_width - home_logo.width + 2 + self._get_layout_offset('home_logo', 'x_offset')
            home_y = center_y - (home_logo.height // 2) + self._get_layout_offset('home_logo', 'y_offset')
            main_img.paste(home_logo, (home_x, home_y), home_logo)

            away_x = -2 + self._get_layout_offset('away_logo', 'x_offset')
            away_y = center_y - (away_logo.height // 2) + self._get_layout_offset('away_logo', 'y_offset')
            main_img.paste(away_logo, (away_x, away_y), away_logo)

            # Draw Text Elements on Overlay
            # Note: Rankings are now handled in the records/rankings section below

            # Final Scores (Centered vertically, same position as live) with layout offsets
            home_score = str(game.get("home_score", "0"))
            away_score = str(game.get("away_score", "0"))
            score_text = f"{away_score}-{home_score}"
            score_width = draw_overlay.textlength(score_text, font=self.fonts["score"])
            score_x = (display_width - score_width) // 2 + self._get_layout_offset('score', 'x_offset')
            score_y = (display_height // 2) - 3 + self._get_layout_offset('score', 'y_offset')  # Centered vertically, same as live games
            self._draw_text_with_outline(
                draw_overlay, score_text, (score_x, score_y), self.fonts["score"]
            )

            # Game date (Bottom of display, one line above bottom edge, centered) with layout offsets
            # Use same font as upcoming games (time font) for consistency
            game_date = game.get("game_date", "")
            if game_date:
                date_width = draw_overlay.textlength(game_date, font=self.fonts["time"])
                date_x = (display_width - date_width) // 2 + self._get_layout_offset('date', 'x_offset')
                # Position date at bottom of display, one line above the bottom edge
                date_y = display_height - 7 + self._get_layout_offset('date', 'y_offset')  # One line above bottom edge
                self._draw_text_with_outline(
                    draw_overlay, game_date, (date_x, date_y), self.fonts["time"]
                )

            # "Final" text (Top center) with layout offsets
            status_text = game.get(
                "period_text", "Final"
            )  # Use formatted period text (e.g., "Final/OT") or default "Final"
            status_width = draw_overlay.textlength(status_text, font=self.fonts["time"])
            status_x = (display_width - status_width) // 2 + self._get_layout_offset('status_text', 'x_offset')
            status_y = 1 + self._get_layout_offset('status_text', 'y_offset')
            self._draw_text_with_outline(
                draw_overlay, status_text, (status_x, status_y), self.fonts["time"]
            )

            # Draw odds if available
            if "odds" in game and game["odds"]:
                self._draw_dynamic_odds(
                    draw_overlay, game["odds"], display_width, display_height
                )

            # Draw records or rankings if enabled
            if self.show_records or self.show_ranking:
                try:
                    record_font = ImageFont.truetype("assets/fonts/4x6-font.ttf", 6)
                    self.logger.debug(f"Loaded 6px record font successfully")
                except IOError:
                    record_font = ImageFont.load_default()
                    self.logger.warning(
                        f"Failed to load 6px font, using default font (size: {record_font.size})"
                    )

                # Get team abbreviations
                away_abbr = game.get("away_abbr", "")
                home_abbr = game.get("home_abbr", "")

                record_bbox = draw_overlay.textbbox((0, 0), "0-0", font=record_font)
                record_height = record_bbox[3] - record_bbox[1]
                record_y = self.display_height - record_height
                self.logger.debug(
                    f"Record positioning: height={record_height}, record_y={record_y}, display_height={self.display_height}"
                )

                # Display away team info
                if away_abbr:
                    if self.show_ranking and self.show_records:
                        # When both rankings and records are enabled, rankings replace records completely
                        away_rank = self._team_rankings_cache.get(away_abbr, 0)
                        if away_rank > 0:
                            away_text = f"#{away_rank}"
                        else:
                            # Show nothing for unranked teams when rankings are prioritized
                            away_text = ""
                    elif self.show_ranking:
                        # Show ranking only if available
                        away_rank = self._team_rankings_cache.get(away_abbr, 0)
                        if away_rank > 0:
                            away_text = f"#{away_rank}"
                        else:
                            away_text = ""
                    elif self.show_records:
                        # Show record only when rankings are disabled
                        away_text = game.get("away_record", "")
                    else:
                        away_text = ""

                    if away_text:
                        away_record_x = 0
                        self.logger.debug(
                            f"Drawing away ranking '{away_text}' at ({away_record_x}, {record_y}) with font size {record_font.size if hasattr(record_font, 'size') else 'unknown'}"
                        )
                        self._draw_text_with_outline(
                            draw_overlay,
                            away_text,
                            (away_record_x, record_y),
                            record_font,
                        )

                # Display home team info
                if home_abbr:
                    if self.show_ranking and self.show_records:
                        # When both rankings and records are enabled, rankings replace records completely
                        home_rank = self._team_rankings_cache.get(home_abbr, 0)
                        if home_rank > 0:
                            home_text = f"#{home_rank}"
                        else:
                            # Show nothing for unranked teams when rankings are prioritized
                            home_text = ""
                    elif self.show_ranking:
                        # Show ranking only if available
                        home_rank = self._team_rankings_cache.get(home_abbr, 0)
                        if home_rank > 0:
                            home_text = f"#{home_rank}"
                        else:
                            home_text = ""
                    elif self.show_records:
                        # Show record only when rankings are disabled
                        home_text = game.get("home_record", "")
                    else:
                        home_text = ""

                    if home_text:
                        home_record_bbox = draw_overlay.textbbox(
                            (0, 0), home_text, font=record_font
                        )
                        home_record_width = home_record_bbox[2] - home_record_bbox[0]
                        home_record_x = display_width - home_record_width + self._get_layout_offset('records', 'home_x_offset')
                        self.logger.debug(
                            f"Drawing home ranking '{home_text}' at ({home_record_x}, {record_y}) with font size {record_font.size if hasattr(record_font, 'size') else 'unknown'}"
                        )
                        self._draw_text_with_outline(
                            draw_overlay,
                            home_text,
                            (home_record_x, record_y),
                            record_font,
                        )

            self._custom_scorebug_layout(game, draw_overlay)
            # Composite and display
            main_img = Image.alpha_composite(main_img, overlay)
            main_img = main_img.convert("RGB")
            # Assign directly like weather plugin does for full display coverage
            self.display_manager.image = main_img
            self.display_manager.update_display()  # Update display here

        except Exception as e:
            self.logger.error(
                f"Error displaying recent game: {e}", exc_info=True
            )  # Changed log prefix

    def display(self, force_clear=False) -> bool:
        """Display recent games, handling switching."""
        if not self.is_enabled or not self.games_list:
            # If disabled or no games, clear the display so old content doesn't persist
            if force_clear or not self.games_list:
                self.display_manager.clear()
                self.display_manager.update_display()
            if not self.games_list and self.current_game:
                self.current_game = None  # Clear internal state if list becomes empty
            return False

        try:
            current_time = time.time()

            # Check if it's time to switch games (protected by lock for thread safety)
            with self._games_lock:
                if (
                    len(self.games_list) > 1
                    and current_time - self.last_game_switch >= self.game_display_duration
                ):
                    self.current_game_index = (self.current_game_index + 1) % len(
                        self.games_list
                    )
                    self.current_game = self.games_list[self.current_game_index]
                    self.last_game_switch = current_time
                    force_clear = True  # Force redraw on switch

                    # Log team switching with sport prefix
                    if self.current_game:
                        away_abbr = self.current_game.get("away_abbr", "UNK")
                        home_abbr = self.current_game.get("home_abbr", "UNK")
                        sport_prefix = (
                            self.sport_key.upper()
                            if hasattr(self, "sport_key")
                            else "SPORT"
                        )
                        self.logger.info(
                            f"[{sport_prefix} Recent] Showing {away_abbr} vs {home_abbr}"
                        )
                    else:
                        self.logger.debug(
                            f"Switched to game index {self.current_game_index}"
                        )

            if self.current_game:
                self._draw_scorebug_layout(self.current_game, force_clear)
            # update_display() is called within _draw_scorebug_layout for recent

        except Exception as e:
            self.logger.error(
                f"Error in display loop: {e}", exc_info=True
            )  # Changed log prefix
            return False

        return True


class SportsLive(SportsCore):

    def __init__(
        self,
        config: Dict[str, Any],
        display_manager,
        cache_manager,
        logger: logging.Logger,
        sport_key: str,
    ):
        super().__init__(config, display_manager, cache_manager, logger, sport_key)
        self.update_interval = self.mode_config.get("live_update_interval", 15)
        self.no_data_interval = 300
        # Log the configured interval for debugging
        self.logger.info(
            f"SportsLive initialized: live_update_interval={self.update_interval}s, "
            f"no_data_interval={self.no_data_interval}s, "
            f"mode_config keys={list(self.mode_config.keys())}"
        )
        self.last_update = 0
        self.live_games = []
        self.current_game_index = 0
        self.last_game_switch = 0  # Will be set to current_time when games are first loaded
        self.game_display_duration = self.mode_config.get("live_game_duration", 20)
        self.last_display_update = 0
        self.last_log_time = 0
        self.log_interval = 300
        self.last_count_log_time = 0  # Track when we last logged count data
        self.count_log_interval = 5  # Only log count data every 5 seconds
        # Initialize test_mode - defaults to False (live mode)
        self.test_mode = self.mode_config.get("test_mode", False)
        # Track game update timestamps for stale data detection
        self.game_update_timestamps = {}  # {game_id: {"clock": timestamp, "score": timestamp, "last_seen": timestamp}}
        self.stale_game_timeout = self.mode_config.get("stale_game_timeout", 300)  # 5 minutes default

    def _is_game_really_over(self, game: Dict) -> bool:
        """Check if a game appears to be over even if API says it's live."""
        game_str = f"{game.get('away_abbr')}@{game.get('home_abbr')}"

        # Check if period_text indicates final
        period_text = game.get("period_text", "").lower()
        if "final" in period_text:
            self.logger.debug(
                f"[LIVE_PRIORITY_DEBUG] _is_game_really_over({game_str}): "
                f"returning True - 'final' in period_text='{period_text}'"
            )
            return True

        # Check if clock is 0:00 in Q4 or OT
        # Safely coerce clock to string to handle None or non-string values
        raw_clock = game.get("clock")
        if raw_clock is None or not isinstance(raw_clock, str):
            clock = "0:00"
        else:
            clock = raw_clock
        period = game.get("period", 0)
        # Handle various clock formats: "0:00", ":00", "0", ":40" (stuck at :40)
        clock_normalized = clock.replace(":", "").strip()

        self.logger.debug(
            f"[LIVE_PRIORITY_DEBUG] _is_game_really_over({game_str}): "
            f"raw_clock={raw_clock!r}, clock='{clock}', clock_normalized='{clock_normalized}', period={period}, period_text='{period_text}'"
        )

        if period >= 4:
            # In Q4 or OT, if clock is 0:00 or appears stuck (like :40), consider it over
            # Check for clock at 0:00 - various formats: "0:00", ":00", normalized "000"/"00"
            # Note: Clocks like ":40", ":50" are legitimate (under 1 minute remaining)
            if clock_normalized == "000" or clock_normalized == "00" or clock == "0:00" or clock == ":00":
                self.logger.debug(
                    f"[LIVE_PRIORITY_DEBUG] _is_game_really_over({game_str}): "
                    f"returning True - clock appears to be 0:00 (clock='{clock}', normalized='{clock_normalized}', period={period})"
                )
                return True

        self.logger.debug(
            f"[LIVE_PRIORITY_DEBUG] _is_game_really_over({game_str}): returning False"
        )
        return False

    def _detect_stale_games(self, games: List[Dict]) -> None:
        """Remove games that appear stale or haven't updated."""
        current_time = time.time()

        for game in games[:]:  # Copy list to iterate safely
            game_id = game.get("id")
            if not game_id:
                continue

            # Check if game data is stale
            timestamps = self.game_update_timestamps.get(game_id, {})
            last_seen = timestamps.get("last_seen", 0)

            if last_seen > 0 and current_time - last_seen > self.stale_game_timeout:
                self.logger.warning(
                    f"Removing stale game {game.get('away_abbr')}@{game.get('home_abbr')} "
                    f"(last seen {int(current_time - last_seen)}s ago)"
                )
                games.remove(game)
                if game_id in self.game_update_timestamps:
                    del self.game_update_timestamps[game_id]
                continue

            # Also check if game appears to be over
            if self._is_game_really_over(game):
                self.logger.debug(
                    f"Removing game that appears over: {game.get('away_abbr')}@{game.get('home_abbr')} "
                    f"(clock={game.get('clock')}, period={game.get('period')}, period_text={game.get('period_text')})"
                )
                games.remove(game)
                if game_id in self.game_update_timestamps:
                    del self.game_update_timestamps[game_id]

    def update(self):
        """Update live game data and handle game switching."""
        if not self.is_enabled:
            return

        # Define current_time and interval before the problematic line (originally line 455)
        # Ensure 'import time' is present at the top of the file.
        current_time = time.time()

        # Define interval using a pattern similar to NFLLiveManager's update method.
        # Uses getattr for robustness, assuming attributes for live_games, test_mode,
        # no_data_interval, and update_interval are available on self.
        _live_games_attr = self.live_games
        _test_mode_attr = getattr(
            self, 'test_mode', False
        )  # test_mode is often from a base class or config - use getattr for safety
        _no_data_interval_attr = (
            self.no_data_interval
        )  # Default similar to NFLLiveManager
        _update_interval_attr = (
            self.update_interval
        )  # Default similar to NFLLiveManager

        # For live managers, always use the configured live_update_interval when checking for updates.
        # Only use no_data_interval if we've recently checked and confirmed there are no live games.
        # This ensures we check for live games frequently even if the list is temporarily empty.
        # Only use no_data_interval if we have no live games AND we've checked recently (within last 5 minutes)
        time_since_last_update = current_time - self.last_update
        has_recently_checked = self.last_update > 0 and time_since_last_update < 300

        if _live_games_attr or _test_mode_attr:
            # We have live games or are in test mode, use the configured update interval
            interval = _update_interval_attr
        elif has_recently_checked:
            # We've checked recently and found no live games, use longer interval
            interval = _no_data_interval_attr
        else:
            # First check or haven't checked in a while, use update interval to check for live games
            interval = _update_interval_attr

        # Original line from traceback (line 455), now with variables defined:
        if current_time - self.last_update >= interval:
            self.last_update = current_time

            # Fetch rankings if enabled
            if self.show_ranking:
                self._fetch_team_rankings()

            # Fetch live game data
            data = self._fetch_data()
            new_live_games = []
            if not data:
                self.logger.debug(f"No data returned from _fetch_data() for {self.sport_key}")
            elif "events" not in data:
                self.logger.debug(f"Data returned but no 'events' key for {self.sport_key}: {list(data.keys()) if isinstance(data, dict) else type(data)}")
            elif data and "events" in data:
                total_events = len(data["events"])
                self.logger.debug(f"Fetched {total_events} total events from API for {self.sport_key}")

                live_or_halftime_count = 0
                filtered_out_count = 0

                for game in data["events"]:
                    details = self._extract_game_details(game)
                    if details:
                        # Log game status for debugging - use INFO level to see what's happening
                        status_state = game.get("competitions", [{}])[0].get("status", {}).get("type", {}).get("state", "unknown")
                        status_name = game.get("competitions", [{}])[0].get("status", {}).get("type", {}).get("name", "unknown")
                        self.logger.info(
                            f"[{self.sport_key.upper()} Live] Game {details.get('away_abbr', '?')}@{details.get('home_abbr', '?')}: "
                            f"state={status_state}, name={status_name}, is_live={details.get('is_live')}, "
                            f"is_halftime={details.get('is_halftime')}, is_final={details.get('is_final')}, "
                            f"clock={details.get('clock', 'N/A')}, period={details.get('period', 'N/A')}, "
                            f"status_text={details.get('status_text', 'N/A')}"
                        )

                        # Filter out final games and games that appear to be over
                        if details.get("is_final", False):
                            self.logger.info(
                                f"[{self.sport_key.upper()} Live] Filtered out final game: {details.get('away_abbr')}@{details.get('home_abbr')} "
                                f"(is_final={details.get('is_final')}, clock={details.get('clock')}, period={details.get('period')})"
                            )
                            continue

                        # Additional validation: check if game appears to be over
                        if self._is_game_really_over(details):
                            self.logger.info(
                                f"[{self.sport_key.upper()} Live] Skipping game that appears final: {details.get('away_abbr')}@{details.get('home_abbr')} "
                                f"(clock={details.get('clock')}, period={details.get('period')}, period_text={details.get('period_text')})"
                            )
                            continue

                        # Check if game should be considered live
                        # First check explicit flags
                        is_explicitly_live = details["is_live"] or details["is_halftime"]

                        # Also check if game appears to be live based on status even if not explicitly marked
                        # Some APIs may mark games differently (e.g., "in progress" vs "in")
                        status_text = details.get("status_text", "").upper()
                        appears_live_by_status = (
                            (status_state == "in" and not details.get("is_final", False))
                            or (status_name and "in" in status_name.lower() and "progress" in status_name.lower())
                            or (status_text and ("Q1" in status_text or "Q2" in status_text or "Q3" in status_text or "Q4" in status_text or "OT" in status_text))
                            or (details.get("clock") and details.get("clock") != "" and details.get("clock") != "0:00" and details.get("clock") != ":00")
                        )

                        is_actually_live = is_explicitly_live or appears_live_by_status

                        if is_actually_live:
                            if appears_live_by_status and not is_explicitly_live:
                                # Game appears to be live but wasn't explicitly marked as such - log this
                                self.logger.warning(
                                    f"[{self.sport_key.upper()} Live] Game {details.get('away_abbr')}@{details.get('home_abbr')} "
                                    f"appears live (state={status_state}, name={status_name}, clock={details.get('clock')}) "
                                    f"but is_live={details.get('is_live')}, is_halftime={details.get('is_halftime')} - treating as live"
                                )
                            live_or_halftime_count += 1
                            self.logger.info(
                                f"[{self.sport_key.upper()} Live] Found live/halftime game: {details.get('away_abbr')}@{details.get('home_abbr')} "
                                f"(is_live={details.get('is_live')}, is_halftime={details.get('is_halftime')}, "
                                f"state={status_state}, appears_live_by_status={appears_live_by_status})"
                            )

                            # Track game timestamps for stale detection
                            game_id = details.get("id")
                            if game_id:
                                current_clock = details.get("clock", "")
                                current_score = f"{details.get('away_score', '0')}-{details.get('home_score', '0')}"

                                if game_id not in self.game_update_timestamps:
                                    self.game_update_timestamps[game_id] = {}

                                timestamps = self.game_update_timestamps[game_id]
                                timestamps["last_seen"] = time.time()

                                # Track if clock/score changed
                                if timestamps.get("last_clock") != current_clock:
                                    timestamps["last_clock"] = current_clock
                                    timestamps["clock_changed_at"] = time.time()
                                if timestamps.get("last_score") != current_score:
                                    timestamps["last_score"] = current_score
                                    timestamps["score_changed_at"] = time.time()

                            # Determine if this game should be included based on filtering settings
                            # Priority: show_all_live > favorite_teams_only (if favorites exist) > show all
                            game_str = f"{details.get('away_abbr')}@{details.get('home_abbr')}"
                            home_abbr = details.get("home_abbr")
                            away_abbr = details.get("away_abbr")

                            if self.show_all_live:
                                # Always show all live games if show_all_live is enabled
                                should_include = True
                                include_reason = "show_all_live=True"
                            elif not self.show_favorite_teams_only:
                                # If favorite teams filtering is disabled, show all games
                                should_include = True
                                include_reason = "show_favorite_teams_only=False"
                            elif not self.favorite_teams:
                                # If favorite teams filtering is enabled but no favorites are configured,
                                # show all games (same behavior as SportsUpcoming)
                                should_include = True
                                include_reason = "favorite_teams is empty"
                            else:
                                # Favorite teams filtering is enabled AND favorites are configured
                                # Only show games involving favorite teams
                                home_match = home_abbr in self.favorite_teams
                                away_match = away_abbr in self.favorite_teams
                                should_include = home_match or away_match
                                include_reason = (
                                    f"favorite_teams={self.favorite_teams}, "
                                    f"home_abbr='{home_abbr}' in_favorites={home_match}, "
                                    f"away_abbr='{away_abbr}' in_favorites={away_match}"
                                )

                            self.logger.debug(
                                f"[LIVE_PRIORITY_DEBUG] {self.sport_key.upper()} filter decision for {game_str}: "
                                f"should_include={should_include}, reason: {include_reason}"
                            )

                            if not should_include:
                                filtered_out_count += 1
                                self.logger.info(
                                    f"[{self.sport_key.upper()} Live] Filtered out live game {details.get('away_abbr')}@{details.get('home_abbr')}: "
                                    f"show_all_live={self.show_all_live}, "
                                    f"show_favorite_teams_only={self.show_favorite_teams_only}, "
                                    f"favorite_teams={self.favorite_teams}"
                                )

                            if should_include:
                                if self.show_odds:
                                    self._fetch_odds(details)
                                new_live_games.append(details)

                self.logger.info(
                    f"[{self.sport_key.upper()} Live] Live game filtering: {total_events} total events, "
                    f"{live_or_halftime_count} live/halftime, "
                    f"{filtered_out_count} filtered out, "
                    f"{len(new_live_games)} included | "
                    f"show_all_live={self.show_all_live}, "
                    f"show_favorite_teams_only={self.show_favorite_teams_only}, "
                    f"favorite_teams={self.favorite_teams if self.favorite_teams else '[] (showing all)'}"
                )

                # Detect and remove stale games
                self._detect_stale_games(new_live_games)

                # Log changes or periodically
                current_time_for_log = (
                    time.time()
                )  # Use a consistent time for logging comparison
                should_log = (
                    current_time_for_log - self.last_log_time >= self.log_interval
                    or len(new_live_games) != len(self.live_games)
                    or any(
                        g1["id"] != g2.get("id")
                        for g1, g2 in zip(self.live_games, new_live_games)
                    )  # Check if game IDs changed
                    or (
                        not self.live_games and new_live_games
                    )  # Log if games appeared
                )

                if should_log:
                    if new_live_games:
                        filter_text = (
                            "favorite teams"
                            if self.show_favorite_teams_only or self.show_all_live
                            else "all teams"
                        )
                        self.logger.info(
                            f"Found {len(new_live_games)} live/halftime games for {filter_text}."
                        )
                        for (
                            game_info
                        ) in new_live_games:  # Renamed game to game_info
                            self.logger.info(
                                f"  - {game_info['away_abbr']}@{game_info['home_abbr']} ({game_info.get('status_text', 'N/A')})"
                            )
                    else:
                        filter_text = (
                            "favorite teams"
                            if self.show_favorite_teams_only or self.show_all_live
                            else "criteria"
                        )
                        self.logger.info(
                            f"No live/halftime games found for {filter_text}."
                        )
                    self.last_log_time = current_time_for_log

                # Update game list and current game (thread-safe)
                with self._games_lock:
                    if new_live_games:
                        # Check if the games themselves changed, not just scores/time
                        new_game_ids = {g["id"] for g in new_live_games}
                        current_game_ids = {g["id"] for g in self.live_games}

                        if new_game_ids != current_game_ids:
                            self.live_games = sorted(
                                new_live_games,
                                key=lambda g: g.get("start_time_utc")
                                or datetime.now(timezone.utc),
                            )  # Sort by start time
                            # Reset index if current game is gone or list is new
                            if (
                                not self.current_game
                                or self.current_game["id"] not in new_game_ids
                            ):
                                self.current_game_index = 0
                                self.current_game = (
                                    self.live_games[0] if self.live_games else None
                                )
                                self.last_game_switch = current_time
                            else:
                                # Find current game's new index if it still exists
                                try:
                                    self.current_game_index = next(
                                        i
                                        for i, g in enumerate(self.live_games)
                                        if g["id"] == self.current_game["id"]
                                    )
                                    self.current_game = self.live_games[
                                        self.current_game_index
                                    ]  # Update current_game with fresh data
                                    # Fix: Set last_game_switch if it's still 0 (initialized) to prevent immediate switching
                                    if self.last_game_switch == 0:
                                        self.last_game_switch = current_time
                                except (
                                    StopIteration
                                ):  # Should not happen if check above passed, but safety first
                                    self.current_game_index = 0
                                    self.current_game = self.live_games[0]
                                    self.last_game_switch = current_time

                        else:
                            # Just update the data for the existing games
                            temp_game_dict = {g["id"]: g for g in new_live_games}
                            self.live_games = [
                                temp_game_dict.get(g["id"], g) for g in self.live_games
                            ]  # Update in place
                            if self.current_game:
                                self.current_game = temp_game_dict.get(
                                    self.current_game["id"], self.current_game
                                )
                            # Fix: Set last_game_switch if it's still 0 (initialized) to prevent immediate switching
                            # This handles the case where games were loaded previously but last_game_switch was never set
                            if self.last_game_switch == 0:
                                self.last_game_switch = current_time

                        # Display update handled by main loop based on interval

                    else:
                        # No live games found
                        if self.live_games:  # Were there games before?
                            self.logger.info(
                                "Live games previously showing have ended or are no longer live."
                            )  # Changed log prefix
                        self.live_games = []
                        self.current_game = None
                        self.current_game_index = 0

            else:
                # Error fetching data or no events
                if self.live_games:  # Were there games before?
                    self.logger.warning(
                        "Could not fetch update; keeping existing live game data for now."
                    )  # Changed log prefix
                else:
                    self.logger.warning(
                        "Could not fetch data and no existing live games."
                    )  # Changed log prefix
                    self.current_game = None  # Clear current game if fetch fails and no games were active

            # Handle game switching (outside test mode check, thread-safe)
            # Fix: Don't check for switching if last_game_switch is still 0 (games haven't been loaded yet)
            # This prevents immediate switching when the system has been running for a while before games load
            with self._games_lock:
                if (
                    not self.test_mode
                    and len(self.live_games) > 1
                    and self.last_game_switch > 0
                    and (current_time - self.last_game_switch) >= self.game_display_duration
                ):
                    self.current_game_index = (self.current_game_index + 1) % len(
                        self.live_games
                    )
                    self.current_game = self.live_games[self.current_game_index]
                    self.last_game_switch = current_time
                    self.logger.info(
                        f"Switched live view to: {self.current_game['away_abbr']}@{self.current_game['home_abbr']}"
                    )  # Changed log prefix
                # Force display update via flag or direct call if needed, but usually let main loop handle
