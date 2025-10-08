import logging
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Optional

import pytz
from PIL import ImageDraw

# Import baseball and standard sports classes
from src.base_classes.baseball import Baseball, BaseballLive, BaseballRecent
from src.base_classes.sports import SportsUpcoming
from src.cache_manager import CacheManager
from src.display_manager import DisplayManager

# Import the API counter function from web interface
try:
    from web_interface_v2 import increment_api_counter
except ImportError:
    # Fallback if web interface is not available
    def increment_api_counter(kind: str, count: int = 1):
        pass


ESPN_MLB_SCOREBOARD_URL = "https://site.api.espn.com/apis/site/v2/sports/baseball/mlb/scoreboard"  # Changed URL for NCAA FB


class BaseMLBManager(Baseball):
    """Base class for MLB managers using new baseball architecture."""

    def __init__(
        self,
        config: Dict[str, Any],
        display_manager: DisplayManager,
        cache_manager: CacheManager,
    ):
        # Initialize with sport_key for MLB
        self.logger = logging.getLogger("MLB")
        super().__init__(config, display_manager, cache_manager, self.logger, "mlb")

        # MLB-specific configuration
        self.show_odds = self.mode_config.get("show_odds", False)
        self.favorite_teams = self.mode_config.get("favorite_teams", [])
        self.show_records = self.mode_config.get("show_records", False)
        self.league = "mlb"

    def _fetch_mlb_api_data(self, use_cache: bool = True) -> Optional[Dict]:
        """
        Fetches the full season schedule for NCAAFB using week-by-week approach to ensure
        we get all games, then caches the complete dataset.

        This method now uses background threading to prevent blocking the display.
        """
        now = datetime.now(pytz.utc)
        start_of_last_month = now.replace(day=1, month=now.month - 1)
        last_day_of_next_month = now.replace(day=1, month=now.month + 2) - timedelta(
            days=1
        )
        start_of_last_month_str = start_of_last_month.strftime("%Y%m%d")
        last_day_of_next_month_str = last_day_of_next_month.strftime("%Y%m%d")
        datestring = f"{start_of_last_month_str}-{last_day_of_next_month_str}"
        cache_key = f"mlb_schedule_{datestring}"

        if use_cache:
            cached_data = self.cache_manager.get(cache_key)
            if cached_data:
                # Validate cached data structure
                if isinstance(cached_data, dict) and "events" in cached_data:
                    self.logger.info(f"Using cached schedule for {datestring}")
                    return cached_data
                elif isinstance(cached_data, list):
                    # Handle old cache format (list of events)
                    self.logger.info(
                        f"Using cached schedule for {datestring} (legacy format)"
                    )
                    return {"events": cached_data}
                else:
                    self.logger.warning(
                        f"Invalid cached data format for {datestring}: {type(cached_data)}"
                    )
                    # Clear invalid cache
                    self.cache_manager.clear_cache(cache_key)

        self.logger.info(f"Fetching full {datestring} season schedule from ESPN API...")

        # Start background fetch
        self.logger.info(
            f"Starting background fetch for {datestring} season schedule..."
        )

        def fetch_callback(result):
            """Callback when background fetch completes."""
            if result.success:
                self.logger.info(
                    f"Background fetch completed for {datestring}: {len(result.data.get('events'))} events"
                )
            else:
                self.logger.error(
                    f"Background fetch failed for {datestring}: {result.error}"
                )

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
            sport="mlb",
            year=now.year,
            url=ESPN_MLB_SCOREBOARD_URL,
            cache_key=cache_key,
            params={"dates": datestring, "limit": 1000},
            headers=self.headers,
            timeout=timeout,
            max_retries=max_retries,
            priority=priority,
            callback=fetch_callback,
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
        if isinstance(self, MLBLiveManager):
            return self._fetch_todays_games()
        else:
            return self._fetch_mlb_api_data(use_cache=True)


class MLBLiveManager(BaseMLBManager, BaseballLive):
    """Manager for displaying live MLB games."""

    def __init__(
        self, config: Dict[str, Any], display_manager, cache_manager: CacheManager
    ):
        super().__init__(config, display_manager, cache_manager)
        self.logger.info("Initialized MLB Live Manager")

        # Initialize with test game only if test mode is enabled
        if self.test_mode:
            self.current_game = {
                "home_abbr": "TB",
                "home_id": "234",
                "away_abbr": "TEX",
                "away_id": "234",
                "home_score": "3",
                "away_score": "2",
                "inning": 5,
                "inning_half": "top",
                "balls": 2,
                "strikes": 1,
                "outs": 1,
                "bases_occupied": [True, False, True],
                "home_logo_path": Path(self.logo_dir, "TB.png"),
                "away_logo_path": Path(self.logo_dir, "TEX.png"),
                "start_time": datetime.now(timezone.utc).isoformat(),
                "is_live": True, "is_final": False, "is_upcoming": False,
            }
            self.live_games = [self.current_game]
            self.logger.info("Initialized MLBLiveManager with test game: TB vs TEX")
        else:
            self.logger.info("Initialized MLBLiveManager in live mode")


class MLBRecentManager(BaseMLBManager, BaseballRecent):
    """Manager for displaying recent MLB games."""

    def __init__(
        self,
        config: Dict[str, Any],
        display_manager: DisplayManager,
        cache_manager: CacheManager,
    ):
        super().__init__(config, display_manager, cache_manager)
        self.logger = logging.getLogger("MLBRecentManager")  # Changed logger name
        self.logger.info(
            f"Initialized MLBRecentManager with {len(self.favorite_teams)} favorite teams"
        )  # Changed log prefix

class MLBUpcomingManager(BaseMLBManager, SportsUpcoming):
    """Manager for displaying upcoming MLB games."""

    def __init__(
        self,
        config: Dict[str, Any],
        display_manager: DisplayManager,
        cache_manager: CacheManager,
    ):
        super().__init__(config, display_manager, cache_manager)
        self.logger = logging.getLogger("MLBUpcomingManager")  # Changed logger name
        self.logger.info(
            f"Initialized MLBUpcomingManager with {len(self.favorite_teams)} favorite teams"
        )  # Changed log prefix
