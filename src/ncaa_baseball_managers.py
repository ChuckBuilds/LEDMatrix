import logging
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Optional

import pytz
from PIL import Image, ImageDraw

# Import baseball and standard sports classes
from src.base_classes.baseball import Baseball, BaseballLive
from src.base_classes.sports import SportsRecent, SportsUpcoming
from src.cache_manager import CacheManager
from src.display_manager import DisplayManager

# Constants for NCAA Baseball API URL
ESPN_NCAABB_SCOREBOARD_URL = "https://site.api.espn.com/apis/site/v2/sports/baseball/college-baseball/scoreboard"

class BaseNCAABaseballManager(Baseball):
    """Base class for NCAA Baseball managers using new baseball architecture."""
    def __init__(self, config: Dict[str, Any], display_manager: DisplayManager, cache_manager: CacheManager):
        # Initialize with sport_key for NCAABB
        self.logger = logging.getLogger("NCAA Baseball")
        super().__init__(config, display_manager, cache_manager, self.logger, "ncaa_baseball")
        
        # NCAA Baseball-specific configuration
        self.show_odds = self.mode_config.get("show_odds", False)
        self.favorite_teams = self.mode_config.get('favorite_teams', [])
        self.show_records = self.mode_config.get('show_records', False)
        self.league = "college-baseball"

    def _fetch_ncaa_baseball_api_data(self, use_cache: bool = True) -> Optional[Dict]:
        """
        Fetches the full season schedule for NCAA Baseball using week-by-week approach to ensure
        we get all games, then caches the complete dataset.
        
        This method now uses background threading to prevent blocking the display.
        """
        now = datetime.now(pytz.utc)
        start_of_last_month = now.replace(day=1, month=now.month - 1)
        last_day_of_next_month = now.replace(day=1, month=now.month + 2) - timedelta(days=1)
        start_of_last_month_str = start_of_last_month.strftime("%Y%m%d")
        last_day_of_next_month_str = last_day_of_next_month.strftime("%Y%m%d")
        datestring = f"{start_of_last_month_str}-{last_day_of_next_month_str}"
        cache_key = f"ncaa_baseball_schedule_{datestring}"

        if use_cache:
            cached_data = self.cache_manager.get(cache_key)
            if cached_data:
                # Validate cached data structure
                if isinstance(cached_data, dict) and 'events' in cached_data:
                    self.logger.info(f"Using cached schedule for {datestring}")
                    return cached_data
                elif isinstance(cached_data, list):
                    # Handle old cache format (list of events)
                    self.logger.info(f"Using cached schedule for {datestring} (legacy format)")
                    return {'events': cached_data}
                else:
                    self.logger.warning(f"Invalid cached data format for {datestring}: {type(cached_data)}")
                    # Clear invalid cache
                    self.cache_manager.clear_cache(cache_key)
        
        self.logger.info(f"Fetching full {datestring} season schedule from ESPN API...")

        # Start background fetch
        self.logger.info(f"Starting background fetch for {datestring} season schedule...")
        
        def fetch_callback(result):
            """Callback when background fetch completes."""
            if result.success:
                self.logger.info(f"Background fetch completed for {datestring}: {len(result.data.get('events'))} events")
            else:
                self.logger.error(f"Background fetch failed for {datestring}: {result.error}")
            
            # Clean up request tracking
            if datestring in self.background_fetch_requests:
                del self.background_fetch_requests[datestring]
        
        # Get background service configuration
        background_config = self.mode_config.get("background_service", {})
        timeout = background_config.get("request_timeout", 30)
        max_retries = background_config.get("max_retries", 3)
        priority = background_config.get("priority", 2)
        
        # Submit background fetch request
        request_id = self.background_service.submit_fetch_request(
            sport="ncaa_baseball",
            year=now.year,
            url=ESPN_NCAABB_SCOREBOARD_URL,
            cache_key=cache_key,
            params={"dates": datestring, "limit": 1000},
            headers=self.headers,
            timeout=timeout,
            max_retries=max_retries,
            priority=priority,
            callback=fetch_callback
        )
        
        # Track the request
        self.background_fetch_requests[datestring] = request_id
        
        # For immediate response, try to get partial data
        partial_data = self._get_weeks_data()
        if partial_data:
            return partial_data
        return None


    def _fetch_data(self) -> Optional[Dict]:
        """Fetch data using shared data mechanism or direct fetch for live."""
        if isinstance(self, NCAABaseballLiveManager):
            return self._fetch_todays_games()
        else:
            return self._fetch_ncaa_baseball_api_data(use_cache=True)

class NCAABaseballLiveManager(BaseNCAABaseballManager, BaseballLive):
    """Manager for displaying live NCAA Baseball games."""
    def __init__(self, config: Dict[str, Any], display_manager, cache_manager: CacheManager):
        super().__init__(config, display_manager, cache_manager)
        self.logger.info("Initialized NCAA Baseball Live Manager")

        if self.test_mode:
            self.current_game = {
                "home_abbr": "FLA",
                "home_id": "234",
                "away_abbr": "LSU",
                "away_id": "234",
                "home_score": "4",
                "away_score": "5",
                "status": "live",
                "status_state": "in",
                "inning": 8,
                "inning_half": "top",
                "balls": 1,
                "strikes": 2,
                "outs": 2,
                "bases_occupied": [True, True, False],
                "home_logo_path": Path(self.logo_dir, "FLA.png"),
                "away_logo_path": Path(self.logo_dir, "LSU.png"),
                "start_time": datetime.now(timezone.utc).isoformat(),
                "is_live": True, "is_final": False, "is_upcoming": False,
            }
            self.live_games = [self.current_game]
            self.logger.info("Initialized NCAABaseballLiveManager with test game: LSU vs FLA")
        else:
            self.logger.info("Initialized NCAABaseballLiveManager in live mode")


class NCAABaseballRecentManager(BaseNCAABaseballManager, SportsRecent):
    """Manager for displaying recent NCAA Baseball games."""
    def __init__(self, config: Dict[str, Any], display_manager: DisplayManager, cache_manager: CacheManager):
        super().__init__(config, display_manager, cache_manager)
        self.logger = logging.getLogger('NCAABaseballRecentManager') # Changed logger name
        self.logger.info(f"Initialized NCAABaseballRecentManager with {len(self.favorite_teams)} favorite teams") # Changed log prefix

class NCAABaseballUpcomingManager(BaseNCAABaseballManager, SportsUpcoming):
    """Manager for displaying upcoming NCAA Baseball games."""
    def __init__(self, config: Dict[str, Any], display_manager: DisplayManager, cache_manager: CacheManager):
        super().__init__(config, display_manager, cache_manager)
        self.logger = logging.getLogger('NCAABaseballUpcomingManager') # Changed logger name
        self.logger.info(f"Initialized NCAABaseballUpcomingManager with {len(self.favorite_teams)} favorite teams") # Changed log prefix