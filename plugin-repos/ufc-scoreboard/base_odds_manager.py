"""
BaseOddsManager - Odds data fetching adapted for MMA/UFC.

Based on LEDMatrix BaseOddsManager with MMA-specific adaptations for
homeAthleteOdds/awayAthleteOdds and separate event_id/comp_id support.

UFC/MMA odds adaptation based on work by Alex Resnick (legoguy1000) - PR #137
"""

import time
import logging
import requests
import json
from datetime import datetime, timedelta, timezone
from typing import Dict, Any, Optional, List

# Import the API counter function from web interface
try:
    from web_interface_v2 import increment_api_counter
except ImportError:
    # Fallback if web interface is not available
    def increment_api_counter(kind: str, count: int = 1):
        pass


class BaseOddsManager:
    """
    Base class for odds data fetching and management.

    Provides core functionality for:
    - ESPN API odds fetching
    - Caching and data processing
    - Error handling and timeouts
    - League mapping and data extraction
    - MMA athlete odds support (homeAthleteOdds/awayAthleteOdds)
    """

    def __init__(self, cache_manager, config_manager=None):
        self.cache_manager = cache_manager
        self.config_manager = config_manager
        self.logger = logging.getLogger(__name__)
        self.base_url = "https://sports.core.api.espn.com/v2/sports"

        # Configuration with defaults
        self.update_interval = 3600  # 1 hour default
        self.request_timeout = 30  # 30 seconds default
        self.cache_ttl = 1800  # 30 minutes default

        # Load configuration if available
        if config_manager:
            self._load_configuration()

    def _load_configuration(self):
        """Load configuration from config manager."""
        if not self.config_manager:
            return

        try:
            config = self.config_manager.get_config()
            odds_config = config.get("base_odds_manager", {})

            self.update_interval = odds_config.get(
                "update_interval", self.update_interval
            )
            self.request_timeout = odds_config.get("timeout", self.request_timeout)
            self.cache_ttl = odds_config.get("cache_ttl", self.cache_ttl)

            self.logger.debug(
                f"BaseOddsManager configuration loaded: "
                f"update_interval={self.update_interval}s, "
                f"timeout={self.request_timeout}s, "
                f"cache_ttl={self.cache_ttl}s"
            )

        except Exception as e:
            self.logger.warning(f"Failed to load BaseOddsManager configuration: {e}")

    def get_odds(
        self,
        sport: str,
        league: str,
        event_id: str,
        comp_id: str = None,
        update_interval_seconds: int = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Fetch odds data for a specific fight/game.

        Args:
            sport: Sport name (e.g., 'mma', 'football')
            league: League name (e.g., 'ufc', 'nfl')
            event_id: ESPN event ID
            comp_id: ESPN competition ID (for MMA where events have multiple fights).
                     If None, defaults to event_id.
            update_interval_seconds: Override default update interval

        Returns:
            Dictionary containing odds data or None if unavailable
        """
        if sport is None or league is None or event_id is None:
            raise ValueError("Sport, League, and event_id cannot be None")

        if comp_id is None:
            comp_id = event_id

        # Use provided interval or default
        interval = update_interval_seconds or self.update_interval
        cache_key = f"odds_espn_{sport}_{league}_{event_id}_{comp_id}"

        # Check cache first
        cached_data = self.cache_manager.get(cache_key)

        if cached_data:
            if isinstance(cached_data, dict) and cached_data.get("no_odds"):
                self.logger.debug(f"Cached no-odds marker for {cache_key}, skipping")
            else:
                self.logger.info(f"Using cached odds from ESPN for {cache_key}")
                return cached_data

        self.logger.info(f"Cache miss - fetching fresh odds from ESPN for {cache_key}")

        try:
            # Map league names to ESPN API format
            league_mapping = {
                "ufc": "ufc",
                "ncaa_fb": "college-football",
                "nfl": "nfl",
                "nba": "nba",
                "mlb": "mlb",
                "nhl": "nhl",
            }

            espn_league = league_mapping.get(league, league)
            url = (
                f"{self.base_url}/{sport}/leagues/{espn_league}"
                f"/events/{event_id}/competitions/{comp_id}/odds"
            )
            self.logger.info(f"Requesting odds from URL: {url}")

            response = requests.get(url, timeout=self.request_timeout)
            response.raise_for_status()
            raw_data = response.json()

            # Increment API counter for odds data
            increment_api_counter("odds", 1)
            self.logger.debug(
                f"Received raw odds data from ESPN: {json.dumps(raw_data, indent=2)}"
            )

            odds_data = self._extract_espn_data(raw_data)
            if odds_data:
                self.logger.info(f"Successfully extracted odds data: {odds_data}")
            else:
                self.logger.debug("No odds data available for this fight")

            if odds_data:
                self.cache_manager.set(cache_key, odds_data)
                self.logger.info(f"Saved odds data to cache for {cache_key}")
            else:
                self.logger.debug(f"No odds data available for {cache_key}")
                # Cache the fact that no odds are available to avoid repeated API calls
                self.cache_manager.set(cache_key, {"no_odds": True})

            return odds_data

        except requests.exceptions.RequestException as e:
            self.logger.error(f"Error fetching odds from ESPN API for {cache_key}: {e}")
        except json.JSONDecodeError:
            self.logger.error(
                f"Error decoding JSON response from ESPN API for {cache_key}."
            )

        return self.cache_manager.get(cache_key)

    def _extract_espn_data(self, data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Extract and format odds data from ESPN API response.

        Supports both team-based odds (homeTeamOdds/awayTeamOdds) and
        MMA athlete-based odds (homeAthleteOdds/awayAthleteOdds).

        Args:
            data: Raw ESPN API response data

        Returns:
            Formatted odds data dictionary or None
        """
        self.logger.debug(f"Extracting ESPN odds data. Data keys: {list(data.keys())}")

        if "items" in data and data["items"]:
            self.logger.debug(f"Found {len(data['items'])} items in odds data")
            item = data["items"][0]
            self.logger.debug(f"First item keys: {list(item.keys())}")

            # MMA uses homeAthleteOdds/awayAthleteOdds instead of homeTeamOdds/awayTeamOdds
            home_odds = item.get("homeTeamOdds", item.get("homeAthleteOdds", {}))
            away_odds = item.get("awayTeamOdds", item.get("awayAthleteOdds", {}))

            extracted_data = {
                "details": item.get("details"),
                "over_under": item.get("overUnder"),
                "spread": item.get("spread"),
                "home_team_odds": {
                    "money_line": home_odds.get("moneyLine"),
                    "spread_odds": home_odds.get("current", {})
                    .get("pointSpread", {})
                    .get("value"),
                },
                "away_team_odds": {
                    "money_line": away_odds.get("moneyLine"),
                    "spread_odds": away_odds.get("current", {})
                    .get("pointSpread", {})
                    .get("value"),
                },
            }
            self.logger.debug(
                f"Returning extracted odds data: {json.dumps(extracted_data, indent=2)}"
            )
            return extracted_data

        # Check if this is a valid empty response
        if (
            "count" in data
            and data["count"] == 0
            and "items" in data
            and data["items"] == []
        ):
            self.logger.debug("Valid empty response - no odds available for this fight")
            return None

        # Unexpected structure
        self.logger.warning(
            f"Unexpected odds data structure: {json.dumps(data, indent=2)}"
        )
        return None

    def get_multiple_odds(
        self,
        sport: str,
        league: str,
        event_ids: List[str],
        comp_ids: List[str] = None,
        update_interval_seconds: int = None,
    ) -> Dict[str, Dict[str, Any]]:
        """
        Fetch odds data for multiple fights.

        Args:
            sport: Sport name
            league: League name
            event_ids: List of ESPN event IDs
            comp_ids: List of competition IDs (parallel to event_ids). If None, uses event_ids.
            update_interval_seconds: Override default update interval

        Returns:
            Dictionary mapping comp_id to odds data
        """
        results = {}

        if comp_ids is None:
            comp_ids = event_ids

        for event_id, comp_id in zip(event_ids, comp_ids):
            try:
                odds_data = self.get_odds(
                    sport, league, event_id, comp_id, update_interval_seconds
                )
                if odds_data:
                    results[comp_id] = odds_data
            except Exception as e:
                self.logger.error(f"Error fetching odds for event {event_id}/{comp_id}: {e}")
                continue

        return results

    def clear_cache(self, sport: str = None, league: str = None, event_id: str = None):
        """Clear odds cache for specific criteria."""
        if sport and league and event_id:
            cache_key = f"odds_espn_{sport}_{league}_{event_id}"
            self.cache_manager.delete(cache_key)
            self.logger.info(f"Cleared cache for {cache_key}")
        else:
            self.cache_manager.clear()
            self.logger.info("Cleared all cache")
