import time
import logging
import requests
from typing import Dict, Any, Optional
from pathlib import Path
from datetime import datetime
from PIL import Image, ImageFont
from src.display_manager import DisplayManager
from src.cache_manager import CacheManager
from src.base_classes.basketball import Basketball, BasketballLive
from src.base_classes.sports import SportsRecent, SportsUpcoming
import pytz

# Import the API counter function from web interface
try:
    from web_interface_v2 import increment_api_counter
except ImportError:
    # Fallback if web interface is not available
    def increment_api_counter(kind: str, count: int = 1):
        pass

# Constants
ESPN_NBA_SCOREBOARD_URL = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard"

class BaseNBAManager(Basketball):
    """Base class for NBA managers with common functionality."""
    # Class variables for warning tracking
    _no_data_warning_logged = False
    _last_warning_time = 0
    _warning_cooldown = 60  # Only log warnings once per minute
    _last_log_times = {}
    _shared_data = None
    _last_shared_update = 0

    def __init__(self, config: Dict[str, Any], display_manager: DisplayManager, cache_manager: CacheManager):
        self.logger = logging.getLogger('NBA') # Changed logger name
        super().__init__(config=config, display_manager=display_manager, cache_manager=cache_manager, logger=self.logger, sport_key="nba")

        # Check display modes to determine what data to fetch
        display_modes = self.mode_config.get("display_modes", {})
        self.recent_enabled = display_modes.get("nba_recent", False)
        self.upcoming_enabled = display_modes.get("nba_upcoming", False)
        self.live_enabled = display_modes.get("nba_live", False)

        self.logger.info(f"Initialized NBA manager with display dimensions: {self.display_width}x{self.display_height}")
        self.logger.info(f"Logo directory: {self.logo_dir}")
        self.logger.info(f"Display modes - Recent: {self.recent_enabled}, Upcoming: {self.upcoming_enabled}, Live: {self.live_enabled}")
        self.league = "nba"

    def _fetch_nba_api_data(self, use_cache: bool = True) -> Optional[Dict]:
        """
        Fetches the full season schedule for NBA using background threading.
        Returns cached data immediately if available, otherwise starts background fetch.
        """
        now = datetime.now(pytz.utc)
        season_year = now.year
        if now.month < 7:
            season_year = now.year - 1
        datestring = f"{season_year}1001-{season_year+1}0701"
        cache_key = f"{self.sport_key}_schedule_{season_year}"

        # Check cache first
        if use_cache:
            cached_data = self.cache_manager.get(cache_key)
            if cached_data:
                # Validate cached data structure
                if isinstance(cached_data, dict) and 'events' in cached_data:
                    self.logger.info(f"Using cached schedule for {season_year}")
                    return cached_data
                elif isinstance(cached_data, list):
                    # Handle old cache format (list of events)
                    self.logger.info(f"Using cached schedule for {season_year} (legacy format)")
                    return {'events': cached_data}
                else:
                    self.logger.warning(f"Invalid cached data format for {season_year}: {type(cached_data)}")
                    # Clear invalid cache
                    self.cache_manager.clear_cache(cache_key)
        
        # Start background fetch
        self.logger.info(f"Starting background fetch for {season_year} season schedule...")
        
        def fetch_callback(result):
            """Callback when background fetch completes."""
            if result.success:
                self.logger.info(f"Background fetch completed for {season_year}: {len(result.data.get('events'))} events")
            else:
                self.logger.error(f"Background fetch failed for {season_year}: {result.error}")
            
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
            sport="nba",
            year=season_year,
            url=ESPN_NBA_SCOREBOARD_URL,
            cache_key=cache_key,
            params={"dates": datestring, "limit": 1000},
            headers=self.headers,
            timeout=timeout,
            max_retries=max_retries,
            priority=priority,
            callback=fetch_callback
        )
        
        # Track the request
        self.background_fetch_requests[season_year] = request_id
        
        # For immediate response, try to get partial data
        partial_data = self._get_weeks_data()
        if partial_data:
            return partial_data
        
        return None

    def _fetch_data(self) -> Optional[Dict]:
        """Fetch data using shared data mechanism or direct fetch for live."""
        if isinstance(self, NBALiveManager):
            # Live games should fetch only current games, not entire season
            return self._fetch_todays_games()
        else:
            # Recent and Upcoming managers should use cached season data
            return self._fetch_nba_api_data(use_cache=True)

class NBALiveManager(BaseNBAManager, BasketballLive):
    """Manager for live NBA games."""
    def __init__(self, config: Dict[str, Any], display_manager: DisplayManager, cache_manager: CacheManager):
        super().__init__(config, display_manager, cache_manager)
        self.logger = logging.getLogger('NBALiveManager') # Changed logger name

        if self.test_mode:
            # More detailed test game for NBA
            self.current_game = {
                "id": "test001",
                "home_abbr": "LAL", "home_id": "123", "away_abbr": "GS", "away_id":"asdf",
                "home_score": "21", "away_score": "17",
                "period": 3, "period_text": "Q3", "clock": "5:24",
                "home_logo_path": Path(self.logo_dir, "LAL.png"),
                "away_logo_path": Path(self.logo_dir, "GS.png"),
                "is_live": True, "is_final": False, "is_upcoming": False, "is_halftime": False,
            }
            self.live_games = [self.current_game]
            self.logger.info("Initialized NBALiveManager with test game: BUF vs KC")
        else:
            self.logger.info(" Initialized NBALiveManager in live mode")


class NBARecentManager(BaseNBAManager, SportsRecent):
    """Manager for recently completed NBA games."""
    def __init__(self, config: Dict[str, Any], display_manager: DisplayManager, cache_manager: CacheManager):
        super().__init__(config, display_manager, cache_manager)
        self.logger = logging.getLogger('NBARecentManager') # Changed logger name
        self.logger.info(f"Initialized NBARecentManager with {len(self.favorite_teams)} favorite teams")

class NBAUpcomingManager(BaseNBAManager, SportsUpcoming):
    """Manager for upcoming NBA games."""
    def __init__(self, config: Dict[str, Any], display_manager: DisplayManager, cache_manager: CacheManager):
        super().__init__(config, display_manager, cache_manager)
        self.logger = logging.getLogger('NBAUpcomingManager') # Changed logger name
        self.logger.info(f"Initialized NBAUpcomingManager with {len(self.favorite_teams)} favorite teams")

        """Display upcoming games."""
        if not self.upcoming_games:
            return

        try:
            current_time = time.time()
            
            # Check if it's time to switch games
            if len(self.upcoming_games) > 1 and current_time - self.last_game_switch >= self.game_display_duration:
                # Move to next game
                self.current_game_index = (self.current_game_index + 1) % len(self.upcoming_games)
                self.current_game = self.upcoming_games[self.current_game_index]
                self.last_game_switch = current_time
                force_clear = True
                
                # Log team switching
                if self.current_game:
                    away_abbr = self.current_game.get('away_abbr', 'UNK')
                    home_abbr = self.current_game.get('home_abbr', 'UNK')
                    self.logger.info(f"[NBA Upcoming] Showing {away_abbr} vs {home_abbr}")
            
            # Draw the scorebug layout
            self._draw_scorebug_layout(self.current_game, force_clear)

        except Exception as e:
            self.logger.error(f"[NBA] Error displaying upcoming game: {e}", exc_info=True) 