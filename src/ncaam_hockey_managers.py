import os
import time
import logging
import requests
import json
from typing import Dict, Any, Optional
from PIL import Image, ImageDraw, ImageFont
from datetime import datetime, timezone
from src.display_manager import DisplayManager
from src.cache_manager import CacheManager # Keep CacheManager import
from src.odds_manager import OddsManager
from src.logo_downloader import download_missing_logo
import pytz
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from src.base_classes.sports import SportsRecent, SportsUpcoming
from src.base_classes.hockey import Hockey, HockeyLive
from pathlib import Path
# Constants
ESPN_NCAAMH_SCOREBOARD_URL = "https://site.api.espn.com/apis/site/v2/sports/hockey/mens-college-hockey/scoreboard"

class BaseNCAAMHockeyManager(Hockey): # Renamed class
    """Base class for NCAA Mens Hockey managers with common functionality.""" # Updated docstring
    # Class variables for warning tracking
    _no_data_warning_logged = False
    _last_warning_time = 0
    _warning_cooldown = 60  # Only log warnings once per minute
    _shared_data = None
    _last_shared_update = 0
    _processed_games_cache = {}  # Cache for processed game data
    _processed_games_timestamp = 0

    def __init__(self, config: Dict[str, Any], display_manager: DisplayManager, cache_manager: CacheManager):
        self.logger = logging.getLogger('NCAAMH') # Changed logger name
        super().__init__(config=config, display_manager=display_manager, cache_manager=cache_manager, logger=self.logger, sport_key="ncaam_hockey")
        
        # Configuration is already set in base class
        # self.logo_dir and self.update_interval are already configured

        # Check display modes to determine what data to fetch
        display_modes = self.mode_config.get("display_modes", {})
        self.recent_enabled = display_modes.get("ncaam_hockey_recent", False)
        self.upcoming_enabled = display_modes.get("ncaam_hockey_upcoming", False)
        self.live_enabled = display_modes.get("ncaam_hockey_live", False)
        self.league = "mens-college-hockey"

        self.logger.info(f"Initialized NCAAMHockey manager with display dimensions: {self.display_width}x{self.display_height}")
        self.logger.info(f"Logo directory: {self.logo_dir}")
        self.logger.info(f"Display modes - Recent: {self.recent_enabled}, Upcoming: {self.upcoming_enabled}, Live: {self.live_enabled}")

    
    def _fetch_ncaa_hockey_api_data(self, use_cache: bool = True) -> Optional[Dict]:
        """
        Fetches the full season schedule for NCAAMH, caches it, and then filters
        for relevant games based on the current configuration.
        """
        now = datetime.now(pytz.utc)
        season_year = now.year
        if now.month < 8:
            season_year = now.year - 1
        datestring = f"{season_year}0901-{season_year+1}0501"
        cache_key = f"ncaa_mens_hockey_schedule_{season_year}"

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
        
        self.logger.info(f"Fetching full {season_year} season schedule from ESPN API...")

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
            sport="ncaa_mens_hockey",
            year=season_year,
            url=ESPN_NCAAMH_SCOREBOARD_URL,
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
        if isinstance(self, NCAAMHockeyLiveManager):
            return self._fetch_todays_games()
        else:
            return self._fetch_ncaa_hockey_api_data(use_cache=True)

class NCAAMHockeyLiveManager(BaseNCAAMHockeyManager, HockeyLive): # Renamed class
    """Manager for live NCAA Mens Hockey games."""
    def __init__(self, config: Dict[str, Any], display_manager: DisplayManager, cache_manager: CacheManager):
        super().__init__(config, display_manager, cache_manager)
        self.logger = logging.getLogger('NCAAMHockeyLiveManager') # Changed logger name

        # Initialize with test game only if test mode is enabled
        if self.test_mode:
            self.current_game = {
                "id": "401596361",
                "home_abbr": "RIT",
                "away_abbr": "CLAR ",
                "home_score": "3",
                "away_score": "2",
                "period": 2,
                "period_text": "1st",
                "home_id": "178",
                "away_id": "2137",
                "clock": "12:34",
                "home_logo_path": Path(self.logo_dir, "RIT.png"),
                "away_logo_path": Path(self.logo_dir, "CLAR .png"),
                "game_time": "7:30 PM",
                "game_date": "Apr 17",
                "is_live": True, "is_final": False, "is_upcoming": False,
            }
            self.live_games = [self.current_game]
            self.logger.info("Initialized NCAAMHockeyLiveManager with test game: RIT vs CLAR ")
        else:
            self.logger.info("Initialized NCAAMHockeyLiveManager in live mode")

class NCAAMHockeyRecentManager(BaseNCAAMHockeyManager, SportsRecent):
    """Manager for recently completed NCAAMH games."""
    def __init__(self, config: Dict[str, Any], display_manager: DisplayManager, cache_manager: CacheManager):
        super().__init__(config, display_manager, cache_manager)
        self.logger = logging.getLogger('NCAAMHockeyRecentManager') # Changed logger name
        self.logger.info(f"Initialized NCAAMHRecentManager with {len(self.favorite_teams)} favorite teams")

class NCAAMHockeyUpcomingManager(BaseNCAAMHockeyManager, SportsUpcoming):
    """Manager for upcoming NCAA Mens Hockey games."""
    def __init__(self, config: Dict[str, Any], display_manager: DisplayManager, cache_manager: CacheManager):
        super().__init__(config, display_manager, cache_manager)
        self.logger = logging.getLogger('NCAAMHockeyUpcomingManager') # Changed logger name
        self.logger.info(f"Initialized NCAAMHUpcomingManager with {len(self.favorite_teams)} favorite teams")
        