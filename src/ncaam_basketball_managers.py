import itertools
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Optional

import pytz
import requests

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

    def _get_events(self, start_date: datetime, end_date: datetime) -> Optional[Dict]:
        """
        Get partial data for immediate display while background fetch is in progress.
        This fetches current/recent games only for quick response.
        """
        events = []
        for group, date in list(
            itertools.product(
                [50, 51], self.get_days_between_dates(start_date, end_date)
            )
        ):
            try:
                # Fetch todays games only
                url = f"https://site.api.espn.com/apis/site/v2/sports/{self.sport}/{self.league}/scoreboard"
                print(date)
                print(group)
                response = self.session.get(
                    url,
                    params={
                        "date": date.strftime("%Y%m%d"),
                        "limit": 1000,
                        "groups": group,
                    },
                    headers=self.headers,
                    timeout=10,
                )
                response.raise_for_status()
                data = response.json()
                events.extend(data.get("events", []))
                print(len(events))
            except requests.exceptions.RequestException as e:
                self.logger.error(
                    f"API error fetching todays games for {self.sport} - {self.league}: {e}"
                )
                return None
        self.logger.info(
            f"Fetched {len(events)} todays games for {self.sport} - {self.league}"
        )
        print(len(events))
        return {"events": events}

    def _get_weeks_data(self) -> Optional[Dict]:
        """
        Get partial data for immediate display while background fetch is in progress.
        This fetches current/recent games only for quick response.
        """
        tz = pytz.timezone("EST")
        now = datetime.now(tz)
        start_date = now + timedelta(weeks=-2)
        end_date = now + timedelta(weeks=1)
        return self._get_events(start_date, end_date)

    def _fetch_todays_games(self) -> Dict | None:
        """Fetch only today's games for live updates (not entire season)."""
        tz = pytz.timezone("EST")
        now = datetime.now(tz)
        yesterday = now - timedelta(days=1)
        data = self._get_events(yesterday, now)
        # print(data)
        return data

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
