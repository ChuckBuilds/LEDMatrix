import os
import time
import logging
import requests
import json
from typing import Dict, Any, Optional, List
from datetime import datetime, timedelta
from src.display_manager import DisplayManager
from src.cache_manager import CacheManager # Keep CacheManager import
import pytz
from src.base_classes.sports import SportsRecent, SportsUpcoming
from src.base_classes.football import Football, FootballLive
# Constants
ESPN_NCAAFB_SCOREBOARD_URL = "https://site.api.espn.com/apis/site/v2/sports/football/college-football/scoreboard" # Changed URL for NCAA FB

class BaseNCAAFBManager(Football): # Renamed class
    """Base class for NCAA FB managers with common functionality.""" # Updated docstring
    # Class variables for warning tracking
    _no_data_warning_logged = False
    _last_warning_time = 0
    _warning_cooldown = 60  # Only log warnings once per minute
    _shared_data = None
    _last_shared_update = 0
    _processed_games_cache = {}  # Cache for processed game data
    _processed_games_timestamp = 0

    def __init__(self, config: Dict[str, Any], display_manager: DisplayManager, cache_manager: CacheManager):
        self.logger = logging.getLogger('NCAAFB') # Changed logger name
        super().__init__(config=config, display_manager=display_manager, cache_manager=cache_manager, logger=self.logger, sport_key="ncaa_fb")

        # Check display modes to determine what data to fetch
        display_modes = self.mode_config.get("display_modes", {})
        self.recent_enabled = display_modes.get("ncaa_fb_recent", False)
        self.upcoming_enabled = display_modes.get("ncaa_fb_upcoming", False)
        self.live_enabled = display_modes.get("ncaa_fb_live", False)


        self.logger.info(f"Initialized NCAAFB manager with display dimensions: {self.display_width}x{self.display_height}")
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
            rankings_url = "https://site.api.espn.com/apis/site/v2/sports/football/college-football/rankings"
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

    def _fetch_ncaa_fb_api_data(self, use_cache: bool = True) -> Optional[Dict]:
        """
        Fetches the full season schedule for NCAAFB using week-by-week approach to ensure
        we get all games, then caches the complete dataset.
        
        This method now uses background threading to prevent blocking the display.
        """
        now = datetime.now(pytz.utc)
        season_year = now.year
        if now.month < 8:
            season_year = now.year - 1
        

        cache_key = f"ncaafb_schedule_{season_year}"
        if use_cache:
            cached_data = self.cache_manager.get(cache_key, max_age=self.season_cache_duration)
            if cached_data:
                self.logger.info(f"[NCAAFB] Using cached schedule for {season_year}")
                return {'events': cached_data}
        
        self.logger.info(f"[NCAAFB] Fetching full {season_year} season schedule from ESPN API...")
        
        # Start background fetch for complete data
        self._start_background_schedule_fetch(season_year)
        
        # For immediate response, fetch current/recent games only
        
        if not (year_events := self._fetch_immediate_games()):
            self.logger.warning("[NCAAFB] No events found in schedule data.")
            return None
        
        return {'events': year_events}
    
    def _fetch_immediate_games(self) -> Optional[List[Dict]]:
        """Fetch immediate games (current week + next few days) for quick display."""
        immediate_events = []
        
        try:
            # Fetch current week and next few days for immediate display
            now = datetime.now(pytz.utc)
            start_date = now + timedelta(days=-1)
            end_date = now + timedelta(days=7)
            date_str = f"{start_date.strftime('%Y%m%d')}-{end_date.strftime('%Y%m%d')}"
                
            url = f"https://site.api.espn.com/apis/site/v2/sports/football/college-football/scoreboard?dates={date_str}"
            response = self.session.get(url, headers=self.headers, timeout=10)
            response.raise_for_status()
            data = response.json()
            date_events = data.get('events', [])
            immediate_events.extend(date_events)
                
            if immediate_events:
                self.logger.info(f"[NCAAFB] Using {len(immediate_events)} immediate events while background fetch completes")
                return immediate_events
            
        except requests.exceptions.RequestException as e:
            self.logger.warning(f"[NCAAFB] Error fetching immediate games for {now}: {e}")
        
        return None
    
    def _start_background_schedule_fetch(self, season_year: int):
        """Start background thread to fetch complete season schedule."""
        import threading
        
        if not hasattr(self, '_background_fetching'):
            self._background_fetching = set()
        
        datestring = f"{season_year}0801-{season_year+1}0201"

        if season_year in self._background_fetching:
            return  # Already fetching
        
        self._background_fetching.add(season_year)
        
        def background_fetch():
            start_time = time.time()
            self.logger.info(f"[NCAAFB] Starting background fetch for {season_year} season...")
            year_events = []
                    
            try:
                url = f"https://site.api.espn.com/apis/site/v2/sports/football/college-football/scoreboard"
                response = self.session.get(url, params={"dates":datestring, "limit": 1000}, headers=self.headers, timeout=15)
                response.raise_for_status()
                data = response.json()
                year_events = data.get('events', [])
               
                # Cache the complete data
                cache_key = f"ncaafb_schedule_{season_year}"
                self.cache_manager.set(cache_key, year_events)
                elapsed_time = time.time() - start_time                
                self.logger.info(f"[NCAAFB] Background fetch completed for {season_year}: {len(year_events)} events cached in {elapsed_time:.1f}s")
            except requests.exceptions.RequestException as e:
                self.logger.error(f"[NCAAFB] Background fetch failed for {season_year}: {e}")
            finally:
                self._background_fetching.discard(season_year)
        
        # Start background thread
        fetch_thread = threading.Thread(target=background_fetch, daemon=True)
        fetch_thread.start()
    
    def _get_partial_schedule_data(self, year: int) -> List[Dict]:
        """Get partial schedule data if available from cache or previous fetch."""
        cache_key = f"ncaafb_schedule_{year}"
        cached_data = self.cache_manager.get(cache_key, max_age=self.season_cache_duration * 2)  # Allow older data
        if cached_data:
            self.logger.debug(f"[NCAAFB] Using partial cached data for {year}: {len(cached_data)} events")
            return cached_data
        return []

    def _fetch_data(self) -> Optional[Dict]:
        """Fetch data using shared data mechanism or direct fetch for live."""
        if isinstance(self, NCAAFBLiveManager):
            return self._fetch_ncaa_fb_api_data(use_cache=False)
        else:
            return self._fetch_ncaa_fb_api_data(use_cache=True)


    def _fetch_odds(self, game: Dict) -> None:
        super()._fetch_odds(game, "college-football")

class NCAAFBLiveManager(BaseNCAAFBManager, FootballLive): # Renamed class
    """Manager for live NCAA FB games.""" # Updated docstring
    def __init__(self, config: Dict[str, Any], display_manager: DisplayManager, cache_manager: CacheManager):
        super().__init__(config=config, display_manager=display_manager, cache_manager=cache_manager)
        self.logger = logging.getLogger('NCAAFBLiveManager') # Changed logger name

        if self.test_mode:
            # More detailed test game for NCAA FB
            self.current_game = {
                "id": "testNCAAFB001",
                "home_abbr": "UGA", "away_abbr": "AUB", # NCAA Examples
                "home_score": "28", "away_score": "21",
                "period": 4, "period_text": "Q4", "clock": "01:15",
                "down_distance_text": "2nd & 5", 
                "possession": "UGA", # Placeholder ID for home team
                "possession_indicator": "home", # Explicitly set for test
                "home_timeouts": 1, "away_timeouts": 2,
                "home_logo_path": os.path.join(self.logo_dir, "UGA.png"),
                "away_logo_path": os.path.join(self.logo_dir, "AUB.png"),
                "is_live": True, "is_final": False, "is_upcoming": False, "is_halftime": False,
                "status_text": "Q4 01:15"
            }
            self.live_games = [self.current_game]
            logging.info("Initialized NCAAFBLiveManager with test game: AUB vs UGA") # Updated log message
        else:
            logging.info("Initialized NCAAFBLiveManager in live mode") # Updated log message

class NCAAFBRecentManager(BaseNCAAFBManager, SportsRecent): # Renamed class
    """Manager for recently completed NCAA FB games.""" # Updated docstring
    def __init__(self, config: Dict[str, Any], display_manager: DisplayManager, cache_manager: CacheManager):
        super().__init__(config, display_manager, cache_manager)
        self.logger = logging.getLogger('NCAAFBRecentManager') # Changed logger name
        self.logger.info(f"Initialized NCAAFBRecentManager with {len(self.favorite_teams)} favorite teams") # Changed log prefix

class NCAAFBUpcomingManager(BaseNCAAFBManager, SportsUpcoming): # Renamed class
    """Manager for upcoming NCAA FB games.""" # Updated docstring
    def __init__(self, config: Dict[str, Any], display_manager: DisplayManager, cache_manager: CacheManager):
        super().__init__(config, display_manager, cache_manager)
        self.logger = logging.getLogger('NCAAFBRecentManager') # Changed logger name
        self.logger.info(f"Initialized NCAAFBUpcomingManager with {len(self.favorite_teams)} favorite teams") # Changed log prefix
