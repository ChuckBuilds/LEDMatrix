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

        self.logger.info(f"Initialized NCAAMHockey manager with display dimensions: {self.display_width}x{self.display_height}")
        self.logger.info(f"Logo directory: {self.logo_dir}")
        self.logger.info(f"Display modes - Recent: {self.recent_enabled}, Upcoming: {self.upcoming_enabled}, Live: {self.live_enabled}")
    
    def _fetch_team_rankings(self) -> Dict[str, int]:
        """Fetch current team rankings from ESPN API."""
        current_time = time.time()
        
        # Check if we have cached rankings that are still valid
        if (self._team_rankings_cache and 
            current_time - self._rankings_cache_timestamp < self._rankings_cache_duration):
            return self._team_rankings_cache
        
        try:
            rankings_url = "https://site.api.espn.com/apis/site/v2/sports/hockey/mens-college-hockey/rankings"
            response = self.session.get(rankings_url, headers=self.headers, timeout=30)
            response.raise_for_status()
            data = response.json()
            
            rankings = {}
            rankings_data = data.get('rankings', [])
            
            if rankings_data:
                # Use the first ranking (usually AP Top 25)
                first_ranking = rankings_data[0]
                teams = first_ranking.get('ranks', [])
                
                for team_data in teams:
                    team_info = team_data.get('team', {})
                    team_abbr = team_info.get('abbreviation', '')
                    current_rank = team_data.get('current', 0)
                    
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

    def _get_timezone(self):
        try:
            timezone_str = self.config.get('timezone', 'UTC')
            return pytz.timezone(timezone_str)
        except pytz.UnknownTimeZoneError:
            return pytz.utc

    def _should_log(self, warning_type: str, cooldown: int = 60) -> bool:
        """Check if we should log a warning based on cooldown period."""
        current_time = time.time()
        if current_time - self._last_warning_time > cooldown:
            self._last_warning_time = current_time
            return True
        return False

    def _fetch_odds(self, game: Dict) -> None:
        super()._fetch_odds(game, "mens-college-hockey")
    
    def _fetch_ncaa_fb_api_data(self, use_cache: bool = True) -> Optional[Dict]:
        """
        Fetches the full season schedule for NCAAMH, caches it, and then filters
        for relevant games based on the current configuration.
        """
        now = datetime.now(pytz.utc)
        current_year = now.year
        years_to_check = [current_year]
        if now.month < 8:
            years_to_check.append(current_year - 1)

        all_events = []
        for year in years_to_check:
            cache_key = f"ncaamh_schedule_{year}"
            if use_cache:
                cached_data = self.cache_manager.get(cache_key)
                if cached_data:
                    self.logger.info(f"[NCAAMH] Using cached schedule for {year}")
                    all_events.extend(cached_data)
                    continue
            
            self.logger.info(f"[NCAAMH] Fetching full {year} season schedule from ESPN API...")
            try:
                response = self.session.get(ESPN_NCAAMH_SCOREBOARD_URL, params={"dates": year,"limit":1000},headers=self.headers, timeout=15)
                response.raise_for_status()
                data = response.json()
                events = data.get('events', [])
                if use_cache:
                    self.cache_manager.set(cache_key, events)
                self.logger.info(f"[NCAAMH] Successfully fetched and cached {len(events)} events for {year} season.")
                all_events.extend(events)
            except requests.exceptions.RequestException as e:
                self.logger.error(f"[NCAAMH] API error fetching full schedule for {year}: {e}")
                continue
        
        if not all_events:
            self.logger.warning("[NCAAMH] No events found in schedule data.")
            return None

        return {'events': all_events}

    def _fetch_data(self) -> Optional[Dict]:
        """Fetch data using shared data mechanism or direct fetch for live."""
        if isinstance(self, NCAAMHockeyLiveManager):
            return self._fetch_ncaa_fb_api_data(use_cache=False)
        else:
            return self._fetch_ncaa_fb_api_data(use_cache=True)

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
                "game_date": "Apr 17"
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
        