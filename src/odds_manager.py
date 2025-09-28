import time
import logging
import requests
import json
from datetime import datetime, timedelta, timezone
from src.cache_manager import CacheManager
import pytz
from typing import Dict, Any, Optional, List

# Import the API counter function from web interface
try:
    from web_interface_v2 import increment_api_counter
except ImportError:
    # Fallback if web interface is not available
    def increment_api_counter(kind: str, count: int = 1):
        pass

class OddsManager:
    def __init__(self, cache_manager: CacheManager, config_manager=None):
        self.cache_manager = cache_manager
        self.config_manager = config_manager
        self.logger = logging.getLogger(__name__)
        self.base_url = "https://sports.core.api.espn.com/v2/sports"

    def get_odds(self, sport: str | None, league: str | None, event_id: str, update_interval_seconds=3600):
        if sport is None or league is None:
            raise ValueError("Sport and League cannot be None")
        cache_key = f"odds_espn_{sport}_{league}_{event_id}"

        # Check cache first
        cached_data = self.cache_manager.get_with_auto_strategy(cache_key)

        if cached_data:
            self.logger.info(f"Using cached odds from ESPN for {cache_key}")
            return cached_data

        self.logger.info(f"Cache miss - fetching fresh odds from ESPN for {cache_key}")
        
        try:
            # Map league names to ESPN API format
            league_mapping = {
                'ncaa_fb': 'college-football',
                'nfl': 'nfl',
                'nba': 'nba',
                'mlb': 'mlb',
                'nhl': 'nhl'
            }
            
            espn_league = league_mapping.get(league, league)
            url = f"{self.base_url}/{sport}/leagues/{espn_league}/events/{event_id}/competitions/{event_id}/odds"
            self.logger.info(f"Requesting odds from URL: {url}")
            response = requests.get(url, timeout=10)
            response.raise_for_status()
            raw_data = response.json()
            
            # Increment API counter for odds data
            increment_api_counter('odds', 1)
            self.logger.debug(f"Received raw odds data from ESPN: {json.dumps(raw_data, indent=2)}")
            
            odds_data = self._extract_espn_data(raw_data)
            if odds_data:
                self.logger.info(f"Successfully extracted odds data: {odds_data}")
            else:
                self.logger.debug("No odds data available for this game")
            
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
            self.logger.error(f"Error decoding JSON response from ESPN API for {cache_key}.")
        
        return self.cache_manager.get_with_auto_strategy(cache_key)

    def _extract_espn_data(self, data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        self.logger.debug(f"Extracting ESPN odds data. Data keys: {list(data.keys())}")
        
        if "items" in data and data["items"]:
            self.logger.debug(f"Found {len(data['items'])} items in odds data")
            item = data["items"][0]
            self.logger.debug(f"First item keys: {list(item.keys())}")
            
            # The ESPN API returns odds data directly in the item, not in a providers array
            # Extract the odds data directly from the item
            extracted_data = {
                "details": item.get("details"),
                "over_under": item.get("overUnder"),
                "spread": item.get("spread"),
                "home_team_odds": {
                    "money_line": item.get("homeTeamOdds", {}).get("moneyLine"),
                    "spread_odds": item.get("homeTeamOdds", {}).get("current", {}).get("pointSpread", {}).get("value")
                },
                "away_team_odds": {
                    "money_line": item.get("awayTeamOdds", {}).get("moneyLine"),
                    "spread_odds": item.get("awayTeamOdds", {}).get("current", {}).get("pointSpread", {}).get("value")
                }
            }
            self.logger.debug(f"Returning extracted odds data: {json.dumps(extracted_data, indent=2)}")
            return extracted_data
        
        # Check if this is a valid empty response or an unexpected structure
        if "count" in data and data["count"] == 0 and "items" in data and data["items"] == []:
            # This is a valid empty response - no odds available for this game
            self.logger.debug(f"No odds available for this game. Response: {json.dumps(data, indent=2)}")
            return None
        else:
            # This is an unexpected response structure
            self.logger.warning("No 'items' found in ESPN odds data.")
            self.logger.warning(f"Unexpected response structure: {json.dumps(data, indent=2)}")
            return None 