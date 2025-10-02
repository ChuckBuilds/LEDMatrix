import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

import pytz
import requests

from src.base_classes.hockey import Hockey, HockeyLive
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
ESPN_NHL_SCOREBOARD_URL = "https://site.api.espn.com/apis/site/v2/sports/hockey/nhl/scoreboard"

class BaseNHLManager(Hockey):
    """Base class for NHL managers with common functionality."""
    # Class variables for warning tracking
    _no_data_warning_logged = False
    _last_warning_time = 0
    _warning_cooldown = 60  # Only log warnings once per minute
    _shared_data = None
    _last_shared_update = 0
    _processed_games_cache = {}  # Cache for processed game data
    _processed_games_timestamp = 0
    
    def __init__(self, config: Dict[str, Any], display_manager: DisplayManager, cache_manager: CacheManager):
        self.logger = logging.getLogger('NHL') # Changed logger name
        super().__init__(config=config, display_manager=display_manager, cache_manager=cache_manager, logger=self.logger, sport_key="nhl")
       
        # Check display modes to determine what data to fetch
        display_modes = self.mode_config.get("display_modes", {})
        self.recent_enabled = display_modes.get("ncaam_hockey_recent", False)
        self.upcoming_enabled = display_modes.get("ncaam_hockey_upcoming", False)
        self.live_enabled = display_modes.get("ncaam_hockey_live", False)
        self.league = "nhl"

        self.logger.info(f"Initialized NHL manager with display dimensions: {self.display_width}x{self.display_height}")
        self.logger.info(f"Logo directory: {self.logo_dir}")
        self.logger.info(f"Display modes - Recent: {self.recent_enabled}, Upcoming: {self.upcoming_enabled}, Live: {self.live_enabled}")
 
    def _fetch_nhl_api_data(self, use_cache: bool = True) -> Optional[Dict]:
        """
        Fetches NHL data using background threading.
        Returns cached data immediately if available, otherwise starts background fetch.
        """
        now = datetime.now(pytz.utc)
        season_year = now.year
        if now.month < 8:
            season_year = now.year - 1
        datestring = f"{season_year}0901-{season_year+1}0801"
        cache_key = f"nhl_schedule_{season_year}"

        # Check cache first
        if use_cache:
            cached_data = self.cache_manager.get(cache_key)
            if cached_data:
                # Validate cached data structure
                if isinstance(cached_data, dict) and 'events' in cached_data:
                    self.logger.info(f"Using cached data for {season_year}")
                    return cached_data
                elif isinstance(cached_data, list):
                    # Handle old cache format (list of events)
                    self.logger.info(f"Using cached data for {season_year} (legacy format)")
                    return {'events': cached_data}
                else:
                    self.logger.warning(f"Invalid cached data format for {season_year}: {type(cached_data)}")
                    # Clear invalid cache
                    self.cache_manager.clear_cache(cache_key)
        
        # If background service is disabled, fall back to synchronous fetch
        if not self.background_enabled or not self.background_service:
            return self._fetch_nhl_api_data_sync(use_cache)
        
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
            sport="nhl",
            year=season_year,
            url=ESPN_NHL_SCOREBOARD_URL,
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
        
        # For immediate response, try to get partial data from cache
        partial_data = self._get_weeks_data()
        if partial_data:
            return partial_data
        
        return None
    
    def _fetch_nhl_api_data_sync(self, use_cache: bool = True) -> Optional[Dict]:
        """
        Synchronous fallback for fetching NFL data when background service is disabled.
        """
        now = datetime.now(pytz.utc)
        current_year = now.year
        cache_key = f"nhl_schedule_{current_year}"

        self.logger.info(f"Fetching full {current_year} season schedule from ESPN API (sync mode)...")
        try:
            response = self.session.get(ESPN_NHL_SCOREBOARD_URL, params={"dates": current_year, "limit":1000}, headers=self.headers, timeout=15)
            response.raise_for_status()
            data = response.json()
            events = data.get('events', [])
            
            if use_cache:
                self.cache_manager.set(cache_key, events)
            
            self.logger.info(f"Successfully fetched {len(events)} events for the {current_year} season.")
            return {'events': events}
        except requests.exceptions.RequestException as e:
            self.logger.error(f"API error fetching full schedule: {e}")
            return None

    def _fetch_data(self, date_str: str = None) -> Optional[Dict]:
        """Fetch data using shared data mechanism or direct fetch for live."""
        if isinstance(self, NHLLiveManager):
            # Live games should fetch only current games, not entire season
            return self._fetch_todays_games()
        else:
            # Recent and Upcoming managers should use cached season data
            return self._fetch_nhl_api_data(use_cache=True)

class NHLLiveManager(BaseNHLManager, HockeyLive):
    """Manager for live NHL games."""
    def __init__(self, config: Dict[str, Any], display_manager: DisplayManager, cache_manager: CacheManager):
        super().__init__(config, display_manager, cache_manager)
        self.logger = logging.getLogger('NHLLiveManager') # Changed logger name
        
        # Initialize with test game only if test mode is enabled
        if self.test_mode:
            self.current_game = {
                "id": "401596361",
                "home_abbr": "TB",
                "away_abbr": "DAL",
                "home_id": "178",
                "away_id": "2137",
                "home_score": "3",
                "away_score": "2",
                "period": 2,
                "clock": "12:34",
                "home_logo_path": Path(self.logo_dir, "TB.png"),
                "away_logo_path": Path(self.logo_dir, "DAL .png"),
                "game_time": "7:30 PM",
                "game_date": "Apr 17",
                "is_live": True, "is_final": False, "is_upcoming": False,
            }
            self.live_games = [self.current_game]
            logging.info("Initialized NHLLiveManager with test game: TB vs DAL")
        else:
            logging.info("Initialized NHLLiveManager in live mode")

class NHLRecentManager(BaseNHLManager, SportsRecent):
    """Manager for recently completed NHL games."""
    def __init__(self, config: Dict[str, Any], display_manager: DisplayManager, cache_manager: CacheManager):
        super().__init__(config, display_manager, cache_manager)
        self.logger = logging.getLogger('NHLRecentManager') # Changed logger name
        self.logger.info(f"Initialized NHLRecentManager with {len(self.favorite_teams)} favorite teams")


class NHLUpcomingManager(BaseNHLManager, SportsUpcoming):
    """Manager for upcoming NHL games."""
    def __init__(self, config: Dict[str, Any], display_manager: DisplayManager, cache_manager: CacheManager):
        super().__init__(config, display_manager, cache_manager)
        self.logger = logging.getLogger('NHLUpcomingManager') # Changed logger name
        self.logger.info(f"Initialized NHLUpcomingManager with {len(self.favorite_teams)} favorite teams")
