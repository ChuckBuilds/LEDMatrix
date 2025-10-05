import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

import pytz
import requests
from PIL import Image

from src.base_classes.basketball import Basketball, BasketballLive
from src.base_classes.sports import SportsRecent, SportsUpcoming
from src.cache_manager import CacheManager
from src.display_manager import DisplayManager

# Import the API counter function from web interface
try:
    from web_interface_v2 import increment_api_counter
except ImportError:
    # Fallback if web interface is not available
    def increment_api_counter(kind: str, count: int = 1):
        pass


# Constants
ESPN_NCAAMB_SCOREBOARD_URL = "https://site.api.espn.com/apis/site/v2/sports/basketball/mens-college-basketball/scoreboard"


class BaseNCAAMBasketballManager(Basketball):
    """Base class for NCAA MB managers with common functionality."""

    # Class variables for warning tracking
    _no_data_warning_logged = False
    _last_warning_time = 0
    _warning_cooldown = 60  # Only log warnings once per minute
    _last_log_times = {}
    _shared_data = None
    _last_shared_update = 0

    def __init__(
        self,
        config: Dict[str, Any],
        display_manager: DisplayManager,
        cache_manager: CacheManager,
    ):
        self.logger = logging.getLogger("NCAAMB")  # Changed logger name
        super().__init__(
            config=config,
            display_manager=display_manager,
            cache_manager=cache_manager,
            logger=self.logger,
            sport_key="ncaam_basketball",
        )

        # Check display modes to determine what data to fetch
        display_modes = self.mode_config.get("display_modes", {})
        self.recent_enabled = display_modes.get("ncaam_basketball_recent", False)
        self.upcoming_enabled = display_modes.get("ncaam_basketball_upcoming", False)
        self.live_enabled = display_modes.get("ncaam_basketball_live", False)

        self.logger.info(
            f"Initialized NCAA Mens Basketball manager with display dimensions: {self.display_width}x{self.display_height}"
        )
        self.logger.info(f"Logo directory: {self.logo_dir}")
        self.logger.info(
            f"Display modes - Recent: {self.recent_enabled}, Upcoming: {self.upcoming_enabled}, Live: {self.live_enabled}"
        )
        self.league = "mens-college-basketball"

    def _fetch_odds(self, game: Dict) -> None:
        """Fetch odds for a game and attach it to the game dictionary."""
        # Check if odds should be shown for this sport
        if not self.show_odds:
            return

        # Check if we should only fetch for favorite teams
        is_favorites_only = self.ncaam_basketball_config.get("show_favorite_teams_only", False)
        if is_favorites_only:
            home_abbr = game.get('home_abbr')
            away_abbr = game.get('away_abbr')
            if not (home_abbr in self.favorite_teams or away_abbr in self.favorite_teams):
                self.logger.debug(f"Skipping odds fetch for non-favorite game in favorites-only mode: {away_abbr}@{home_abbr}")
                return

        self.logger.debug(f"Proceeding with odds fetch for game: {game.get('id', 'N/A')}")
        
        try:
            odds_data = self.odds_manager.get_odds(
                sport="basketball",
                league="mens-college-basketball",
                event_id=game["id"]
            )
            if odds_data:
                game['odds'] = odds_data
        except Exception as e:
            self.logger.error(f"Error fetching odds for game {game.get('id', 'N/A')}: {e}")

    def _get_timezone(self):
        try:
            timezone_str = self.config.get('timezone', 'UTC')
            return pytz.timezone(timezone_str)
        except pytz.UnknownTimeZoneError:
            return pytz.utc

    def _should_log(self, message_type: str, cooldown: int = 300) -> bool:
        """Check if a message should be logged based on cooldown period."""
        current_time = time.time()
        last_time = self._last_log_times.get(message_type, 0)
        
        if current_time - last_time >= cooldown:
            self._last_log_times[message_type] = current_time
            return True
        return False

    def _load_test_data(self) -> Dict:
        """Load test data for development and testing."""
        self.logger.info("[NCAAMBasketball] Loading test data")
        
        # Create test data with current time
        now = datetime.now(self._get_timezone())
        
        # Create test events for different scenarios
        events = []
        
        # Live game (2nd Half)
        live_game = {
            "date": now.isoformat(),
            "competitions": [{
                "status": {
                    "type": {
                        "state": "in",
                        "shortDetail": "H2 5:23" # Changed from Q3
                    },
                    "period": 2, # Changed from 3
                    "displayClock": "5:23"
                },
                "competitors": [
                    {
                        "homeAway": "home",
                        "team": {"abbreviation": "UGA"}, 
                        "score": "75" # Adjusted score
                    },
                    {
                        "homeAway": "away",
                        "team": {"abbreviation": "AUB"}, 
                        "score": "72" # Adjusted score
                    }
                ]
            }]
        }
        events.append(live_game)
        
        # Recent game (yesterday)
        recent_game = {
            "date": (now - timedelta(days=1)).isoformat(),
            "competitions": [{
                "status": {
                    "type": {
                        "state": "post",
                        "shortDetail": "Final"
                    },
                    "period": 2, # Changed from 4
                    "displayClock": "0:00"
                },
                "competitors": [
                    {
                        "homeAway": "home",
                        "team": {"abbreviation": "UCLA"}, # Changed from BOS
                        "score": "88" # Adjusted score
                    },
                    {
                        "homeAway": "away",
                        "team": {"abbreviation": "ZAGA"}, # Changed from MIA
                        "score": "85" # Adjusted score
                    }
                ]
            }]
        }
        events.append(recent_game)
        
        # Upcoming game (tomorrow)
        upcoming_game = {
            "date": (now + timedelta(days=1)).isoformat(),
            "competitions": [{
                "status": {
                    "type": {
                        "state": "pre",
                        "shortDetail": "8:00 PM ET" # Adjusted time
                    },
                    "period": 0,
                    "displayClock": "0:00"
                },
                "competitors": [
                    {
                        "homeAway": "home",
                        "team": {"abbreviation": "UGA"}, # Changed from PHX
                        "score": "0"
                    },
                    {
                        "homeAway": "away",
                        "team": {"abbreviation": "AUB"}, # Changed from DEN
                        "score": "0"
                    }
                ]
            }]
        }
        events.append(upcoming_game)
        
        return {"events": events}

    def _load_fonts(self):
        """Load fonts using the unified font system."""
        fonts = {}
        try:
            if hasattr(self.display_manager, 'font_manager'):
                # Use unified font system with element keys
                element_key_mapping = {
                    'score': f"{self.sport_key}.live.score",
                    'time': f"{self.sport_key}.live.time", 
                    'team': f"{self.sport_key}.live.team",
                    'status': f"{self.sport_key}.live.status"
                }
                for font_type, element_key in element_key_mapping.items():
                    fonts[font_type] = self.display_manager.font_manager.resolve(element_key=element_key)
                logging.info(f"Successfully loaded fonts via FontManager for {self.sport_key}")
            else:
                # Fallback to direct font loading
                fonts['score'] = ImageFont.truetype("assets/fonts/PressStart2P-Regular.ttf", 10)
                fonts['time'] = ImageFont.truetype("assets/fonts/PressStart2P-Regular.ttf", 8)
                fonts['team'] = ImageFont.truetype("assets/fonts/PressStart2P-Regular.ttf", 8)
                fonts['status'] = ImageFont.truetype("assets/fonts/4x6-font.ttf", 6)
                logging.info("[NCAAMBasketball] Fallback: Successfully loaded Press Start 2P font for all text elements")
        except Exception as e:
            logging.warning(f"[NCAAMBasketball] Fonts not found, using default PIL font: {e}")
            # Use default PIL font as a last resort
            fonts['score'] = ImageFont.load_default()
            fonts['time'] = ImageFont.load_default()
            fonts['team'] = ImageFont.load_default()
            fonts['status'] = ImageFont.load_default()
        return fonts

    def _load_and_resize_logo(self, team_abbrev: str) -> Optional[Image.Image]:
        """Load and resize a team logo, with caching."""
        self.logger.debug(f"Loading logo for {team_abbrev}")
        
        if team_abbrev in self._logo_cache:
            self.logger.debug(f"Using cached logo for {team_abbrev}")
            return self._logo_cache[team_abbrev]
            
        logo_path = os.path.join(self.logo_dir, f"{team_abbrev}.png")
        self.logger.debug(f"Logo path: {logo_path}")
        
        try:
            # Create test logos if they don't exist (Simple placeholder logic)
            if not os.path.exists(logo_path):
                self.logger.info(f"Creating test logo for {team_abbrev}")
                os.makedirs(os.path.dirname(logo_path), exist_ok=True)
                logo = Image.new('RGBA', (32, 32), (0, 0, 0, 0))
                draw = ImageDraw.Draw(logo)
                # Basic color logic for test logos
                color = (sum(ord(c) for c in team_abbrev) % 200 + 55,  # R
                         sum(ord(c)**2 for c in team_abbrev) % 200 + 55, # G
                         sum(ord(c)**3 for c in team_abbrev) % 200 + 55, # B
                         255) # Alpha
                draw.rectangle([4, 4, 28, 28], fill=color)
                draw.text((8, 8), team_abbrev, fill=(255, 255, 255, 255))
                logo.save(logo_path)
                self.logger.info(f"Created test logo at {logo_path}")
            
            logo = Image.open(logo_path)
            self.logger.debug(f"Opened logo for {team_abbrev}, size: {logo.size}, mode: {logo.mode}")
            
            # Convert to RGBA if not already
            if logo.mode != 'RGBA':
                self.logger.debug(f"Converting {team_abbrev} logo from {logo.mode} to RGBA")
                logo = logo.convert('RGBA')
            
            # Calculate max size based on display dimensions
            max_width = int(self.display_width * 1.5)
            max_height = int(self.display_height * 1.5)
            
            # Resize maintaining aspect ratio
            logo.thumbnail((max_width, max_height), Image.Resampling.LANCZOS)
            self.logger.debug(f"Resized {team_abbrev} logo to {logo.size}")
            
            # Cache the resized logo
            self._logo_cache[team_abbrev] = logo
            return logo
            
        except Exception as e:
            self.logger.error(f"Error loading logo for {team_abbrev}: {e}", exc_info=True)
            return None

    def _draw_text_with_outline(self, draw, text, position, font, fill=(255, 255, 255), outline_color=(0, 0, 0)):
        """Draw text with outline for better visibility."""
        # Draw outline
        for dx in [-1, 0, 1]:
            for dy in [-1, 0, 1]:
                if dx != 0 or dy != 0:
                    draw.text((position[0] + dx, position[1] + dy), text, font=font, fill=outline_color)
        # Draw main text
        draw.text(position, text, font=font, fill=fill)

    def _fetch_ncaam_basketball_api_data(
        self, use_cache: bool = True
    ) -> Optional[Dict]:
        """
        Fetches the full season schedule for NCAA Mens Basketball using background threading.
        Returns cached data immediately if available, otherwise starts background fetch.
        """
        now = datetime.now(pytz.utc)
        season_year = now.year
        cache_key = f"{self.sport_key}_schedule_{season_year}"

        # Check cache first
        if use_cache:
            cached_data = self.cache_manager.get(cache_key)
            if cached_data:
                # Validate cached data structure
                if isinstance(cached_data, dict) and "events" in cached_data:
                    self.logger.info(f"Using cached schedule for {season_year}")
                    return cached_data
                elif isinstance(cached_data, list):
                    # Handle old cache format (list of events)
                    self.logger.info(
                        f"Using cached schedule for {season_year} (legacy format)"
                    )
                    return {"events": cached_data}
                else:
                    self.logger.warning(
                        f"Invalid cached data format for {season_year}: {type(cached_data)}"
                    )
                    # Clear invalid cache
                    self.cache_manager.clear_cache(cache_key)

        # If background service is disabled, fall back to synchronous fetch
        if not self.background_enabled or not self.background_service:
            return self._fetch_ncaam_basketball_api_data_sync(use_cache)

        # Start background fetch
        self.logger.info(
            f"Starting background fetch for {season_year} season schedule..."
        )

        def fetch_callback(result):
            """Callback when background fetch completes."""
            if result.success:
                self.logger.info(
                    f"Background fetch completed for {season_year}: {len(result.data.get('events'))} events"
                )
            else:
                self.logger.error(
                    f"Background fetch failed for {season_year}: {result.error}"
                )

            # Clean up request tracking
            if season_year in self.background_fetch_requests:
                del self.background_fetch_requests[season_year]

        # Get background service configuration
        background_config = self.mode_config.get("background_service", {})
        timeout = background_config.get("request_timeout", 30)
        max_retries = background_config.get("max_retries", 3)
        priority = background_config.get("priority", 2)

        # Submit background fetch request
        request_id = self.background_service.submit_fetch_request(
            sport="ncaa_mens_basketball",
            year=season_year,
            url=ESPN_NCAAMB_SCOREBOARD_URL,
            cache_key=cache_key,
            params={"dates": season_year, "limit": 1000},
            headers=self.headers,
            timeout=timeout,
            max_retries=max_retries,
            priority=priority,
            callback=fetch_callback,
        )

        # Track the request
        self.background_fetch_requests[season_year] = request_id

        # For immediate response, try to get partial data
        partial_data = self._get_weeks_data()
        if partial_data:
            return partial_data

        return None

    def _fetch_ncaam_basketball_api_data_sync(
        self, use_cache: bool = True
    ) -> Optional[Dict]:
        """
        Synchronous fallback for fetching NFL data when background service is disabled.
        """
        now = datetime.now(pytz.utc)
        current_year = now.year
        cache_key = f"ncaa_mens_basketball_schedule_{current_year}"

        self.logger.info(
            f"Fetching full {current_year} season schedule from ESPN API (sync mode)..."
        )
        try:
            response = self.session.get(
                ESPN_NCAAMB_SCOREBOARD_URL,
                params={"dates": current_year, "limit": 1000},
                headers=self.headers,
                timeout=15,
            )
            response.raise_for_status()
            data = response.json()
            events = data.get("events", [])

            if use_cache:
                self.cache_manager.set(cache_key, events)

            self.logger.info(
                f"Successfully fetched {len(events)} events for the {current_year} season."
            )
            return {"events": events}
        except requests.exceptions.RequestException as e:
            self.logger.error(f"API error fetching full schedule: {e}")
            return None

    def _fetch_data(self) -> Optional[Dict]:
        """Fetch data using shared data mechanism or direct fetch for live."""
        if isinstance(self, NCAAMBasketballLiveManager):
            # Live games should fetch only current games, not entire season
            return self._fetch_todays_games()
        else:
            # Recent and Upcoming managers should use cached season data
            return self._fetch_ncaam_basketball_api_data(use_cache=True)


class NCAAMBasketballLiveManager(BaseNCAAMBasketballManager, BasketballLive):
    """Manager for live NCAA MB games."""

    def __init__(
        self,
        config: Dict[str, Any],
        display_manager: DisplayManager,
        cache_manager: CacheManager,
    ):
        super().__init__(config, display_manager, cache_manager)
        self.logger = logging.getLogger(
            "NCAAMBasketballLiveManager"
        )  # Changed logger name

        if self.test_mode:
            # More detailed test game for NCAA MB
            self.current_game = {
                "id": "test001",
                "home_abbr": "AUB",
                "home_id": "123",
                "away_abbr": "GT",
                "away_id": "asdf",
                "home_score": "21",
                "away_score": "17",
                "period": 3,
                "period_text": "Q3",
                "clock": "5:24",
                "home_logo_path": Path(self.logo_dir, "AUB.png"),
                "away_logo_path": Path(self.logo_dir, "GT.png"),
                "is_live": True,
                "is_final": False,
                "is_upcoming": False,
                "is_halftime": False,
            }
            self.live_games = [self.current_game]
            self.logger.info(
                "Initialized NCAAMBasketballLiveManager with test game: GT vs AUB"
            )
        else:
            self.logger.info(" Initialized NCAAMBasketballLiveManager in live mode")


class NCAAMBasketballRecentManager(BaseNCAAMBasketballManager, SportsRecent):
    """Manager for recently completed NCAA MB games."""

    def __init__(
        self,
        config: Dict[str, Any],
        display_manager: DisplayManager,
        cache_manager: CacheManager,
    ):
        super().__init__(config, display_manager, cache_manager)
        self.logger = logging.getLogger(
            "NCAAMBasketballRecentManager"
        )  # Changed logger name
        self.logger.info(
            f"Initialized NCAAMBasketballRecentManager with {len(self.favorite_teams)} favorite teams"
        )


class NCAAMBasketballUpcomingManager(BaseNCAAMBasketballManager, SportsUpcoming):
    """Manager for upcoming NCAA MB games."""

    def __init__(
        self,
        config: Dict[str, Any],
        display_manager: DisplayManager,
        cache_manager: CacheManager,
    ):
        super().__init__(config, display_manager, cache_manager)
        self.logger = logging.getLogger(
            "NCAAMBasketballUpcomingManager"
        )  # Changed logger name
        self.logger.info(
            f"Initialized NCAAMBasketballUpcomingManager with {len(self.favorite_teams)} favorite teams"
        )
