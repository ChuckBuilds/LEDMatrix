"""UFC Manager Classes - Adapted from original work by Alex Resnick (legoguy1000) - PR #137"""

import logging
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Optional

import requests

from mma import MMA, MMALive, MMARecent, MMAUpcoming

# Constants
ESPN_UFC_SCOREBOARD_URL = (
    "https://site.api.espn.com/apis/site/v2/sports/mma/ufc/scoreboard"
)


class BaseUFCManager(MMA):
    """Base class for UFC managers with common functionality."""

    # Class variables for warning tracking
    _no_data_warning_logged = False
    _last_warning_time = 0
    _warning_cooldown = 60  # Only log warnings once per minute
    _shared_data = None
    _last_shared_update = 0

    def __init__(
        self,
        config: Dict[str, Any],
        display_manager: Any,
        cache_manager: Any,
    ):
        self.logger = logging.getLogger("UFC")
        super().__init__(
            config=config,
            display_manager=display_manager,
            cache_manager=cache_manager,
            logger=self.logger,
            sport_key="ufc_scoreboard",
        )

        self.league = "ufc"
        self.sport = "mma"

        # Check display modes to determine what data to fetch
        display_modes = self.mode_config.get("display_modes", {})
        self.recent_enabled = display_modes.get("ufc_recent", False)
        self.upcoming_enabled = display_modes.get("ufc_upcoming", False)
        self.live_enabled = display_modes.get("ufc_live", False)

        self.logger.info(
            f"Initialized UFC manager with display dimensions: "
            f"{self.display_width}x{self.display_height}"
        )
        self.logger.info(f"Logo directory: {self.logo_dir}")
        self.logger.info(
            f"Display modes - Recent: {self.recent_enabled}, "
            f"Upcoming: {self.upcoming_enabled}, Live: {self.live_enabled}"
        )

    def _fetch_ufc_api_data(self, use_cache: bool = True) -> Optional[Dict]:
        """
        Fetches the full season schedule for UFC using background threading.
        Returns cached data immediately if available, otherwise starts background fetch.
        """
        now = datetime.now(timezone.utc)
        season_year = now.year
        datestring = f"{season_year}0101-{season_year}1231"
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
                        f"Invalid cached data format for {season_year}: "
                        f"{type(cached_data)}"
                    )
                    # Clear invalid cache
                    self.cache_manager.clear_cache(cache_key)

        # Synchronous fallback when background service is not available
        if not self.background_enabled:
            try:
                response = self.session.get(
                    ESPN_UFC_SCOREBOARD_URL,
                    params={"dates": datestring, "limit": 1000},
                    headers=self.headers,
                    timeout=30,
                )
                response.raise_for_status()
                data = response.json()
                self.cache_manager.set(cache_key, data)
                return data
            except Exception as e:
                self.logger.error(f"Sync fetch failed: {e}")
                return None

        # Start background fetch
        self.logger.info(
            f"Starting background fetch for {season_year} season schedule..."
        )

        def fetch_callback(result):
            """Callback when background fetch completes."""
            if result.success:
                self.logger.info(
                    f"Background fetch completed for {season_year}: "
                    f"{len(result.data.get('events'))} events"
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
            sport="mma",
            year=season_year,
            url=ESPN_UFC_SCOREBOARD_URL,
            cache_key=cache_key,
            params={"dates": datestring, "limit": 1000},
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
        if isinstance(self, UFCLiveManager):
            # Live games should fetch only current games, not entire season
            return self._fetch_todays_games()
        else:
            # Recent and Upcoming managers should use cached season data
            return self._fetch_ufc_api_data(use_cache=True)


class UFCLiveManager(BaseUFCManager, MMALive):
    """Manager for live UFC fights."""

    def __init__(
        self,
        config: Dict[str, Any],
        display_manager: Any,
        cache_manager: Any,
    ):
        super().__init__(config, display_manager, cache_manager)
        self.logger = logging.getLogger("UFCLiveManager")

        if self.test_mode:
            self.current_game = {
                "id": "test001",
                "event_id": "test_event001",
                "comp_id": "test001",
                "fighter1_id": "12345",
                "fighter1_name": "Israel Adesanya",
                "fighter1_name_short": "I. Adesanya",
                "fighter1_image_path": Path(self.logo_dir, "12345.png"),
                "fighter1_image_url": "https://a.espncdn.com/combiner/i?img=/i/headshots/mma/players/full/12345.png",
                "fighter1_record": "24-3-0",
                "fighter2_id": "67890",
                "fighter2_name": "Dricus Du Plessis",
                "fighter2_name_short": "D. Du Plessis",
                "fighter2_image_path": Path(self.logo_dir, "67890.png"),
                "fighter2_image_url": "https://a.espncdn.com/combiner/i?img=/i/headshots/mma/players/full/67890.png",
                "fighter2_record": "21-2-0",
                "fight_class": "MW",
                "status_text": "R2 3:45",
                "is_live": True,
                "is_final": False,
                "is_upcoming": False,
                "is_period_break": False,
                "start_time_utc": None,
            }
            self.live_games = [self.current_game]
            self.logger.info(
                "Initialized UFCLiveManager with test game: "
                "Adesanya vs Du Plessis"
            )
        else:
            self.logger.info("Initialized UFCLiveManager in live mode")


class UFCRecentManager(BaseUFCManager, MMARecent):
    """Manager for recently completed UFC fights."""

    def __init__(
        self,
        config: Dict[str, Any],
        display_manager: Any,
        cache_manager: Any,
    ):
        super().__init__(config, display_manager, cache_manager)
        self.logger = logging.getLogger("UFCRecentManager")
        self.logger.info(
            f"Initialized UFCRecentManager with "
            f"{len(self.favorite_fighters)} favorite fighters"
        )


class UFCUpcomingManager(BaseUFCManager, MMAUpcoming):
    """Manager for upcoming UFC fights."""

    def __init__(
        self,
        config: Dict[str, Any],
        display_manager: Any,
        cache_manager: Any,
    ):
        super().__init__(config, display_manager, cache_manager)
        self.logger = logging.getLogger("UFCUpcomingManager")
        self.logger.info(
            f"Initialized UFCUpcomingManager with "
            f"{len(self.favorite_fighters)} favorite fighters"
        )
