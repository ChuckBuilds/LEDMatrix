"""
BaseOddsManager - Base class for odds data fetching and management.

This base class provides core odds fetching functionality that can be inherited
by plugins that need odds data (odds ticker, scoreboards, etc.).

Follows LEDMatrix configuration management patterns:
- Single responsibility: Data fetching only
- Reusable: Other plugins can inherit from it
- Clean configuration: Separate config sections
- Maintainable: Changes to odds logic affect all plugins
"""

import logging
import time

import requests
import json
from typing import Dict, Any, Optional, List


class BaseOddsManager:
    """
    Base class for odds data fetching and management.
    
    Provides core functionality for:
    - ESPN API odds fetching
    - Caching and data processing
    - Error handling and timeouts
    - League mapping and data extraction
    
    Plugins can inherit from this class to get odds functionality.
    """
    
    def __init__(self, cache_manager, config_manager=None):
        """
        Initialize the base odds manager.
        
        Args:
            cache_manager: Cache manager instance for data persistence
            config_manager: Configuration manager (optional)
        """
        self.cache_manager = cache_manager
        self.config_manager = config_manager
        self.logger = logging.getLogger(__name__)
        self.base_url = "https://sports.core.api.espn.com/v2/sports"

        # This path used a bare requests.get, so it identified itself as
        # python-requests/x.y -- the one thing ESPN is known to reject. Around
        # 2026-08-04 it began 403ing browser strings and bare custom tokens
        # alike; what it accepts is a token with a URL that says who is
        # calling. Every other ESPN caller in the tree already sends this
        # (src/common/api_helper.py, src/base_classes/data_sources.py); the
        # odds path was simply missed, and it is the one whose failures cost
        # the caller its whole update budget.
        #
        # Deliberately no retry adapter, unlike api_helper: retries multiply
        # request_timeout, which is set to 5s precisely to stay inside that
        # budget. One try, then the cooldown below.
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'LEDMatrix/1.0 (+https://github.com/ChuckBuilds/LEDMatrix)',
            'Accept': 'application/json',
        })
        
        # Configuration with defaults
        self.update_interval = 3600  # 1 hour default
        # Well under the plugin executor's 30s operation budget. At 30s a
        # single stalled ESPN request consumed the entire budget and the whole
        # update() was killed -- and odds are fetched per live game, inside the
        # live update loop, with show_odds defaulting on. Losing one game's
        # odds beats losing the update that carries every game's score.
        self.request_timeout = 5
        # Set when a request fails; until then, skip the network entirely.
        self._skip_network_until = 0.0
        self.cache_ttl = 1800       # 30 minutes default
        
        # Load configuration if available
        if config_manager:
            self._load_configuration()
    
    def _load_configuration(self):
        """Load configuration from config manager."""
        if not self.config_manager:
            return
            
        try:
            config = self.config_manager.get_config()
            odds_config = config.get('base_odds_manager', {})
            
            self.update_interval = odds_config.get('update_interval', self.update_interval)
            self.request_timeout = odds_config.get('timeout', self.request_timeout)
            self.cache_ttl = odds_config.get('cache_ttl', self.cache_ttl)
            
            self.logger.debug(f"BaseOddsManager configuration loaded: "
                            f"update_interval={self.update_interval}s, "
                            f"timeout={self.request_timeout}s, "
                            f"cache_ttl={self.cache_ttl}s")
                            
        except Exception as e:
            self.logger.warning(f"Failed to load BaseOddsManager configuration: {e}")
    
    # After a network failure, stop trying for this long and serve cache only.
    # A short per-request timeout bounds one stall, but a full Sunday slate is
    # ~16 games fetched in a loop, so 16 consecutive timeouts still blow the
    # budget. When ESPN is unreachable it is unreachable for all of them, so
    # the first failure is enough to know: skip the rest of this pass and try
    # again shortly.
    _FAILURE_COOLDOWN = 60.0

    def get_odds(self, sport: str | None, league: str | None, event_id: str,
                 update_interval_seconds: int = None) -> Optional[Dict[str, Any]]:
        """
        Fetch odds data for a specific game.
        
        Args:
            sport: Sport name (e.g., 'football', 'basketball')
            league: League name (e.g., 'nfl', 'nba')
            event_id: ESPN event ID
            update_interval_seconds: Override default update interval

        Returns:
            Dictionary containing odds data or None if unavailable
        """
        if sport is None or league is None:
            raise ValueError("Sport and League cannot be None")

        # Use provided interval or default
        interval = update_interval_seconds or self.update_interval
        cache_key = f"odds_espn_{sport}_{league}_{event_id}"

        # Check cache first
        cached_data = self.cache_manager.get_with_auto_strategy(cache_key)

        if cached_data:
            self.logger.info(f"Using cached odds from ESPN for {cache_key}")
            return cached_data

        if time.monotonic() < self._skip_network_until:
            # A recent request failed, so ESPN is very likely still unreachable.
            # Returning now keeps the caller's update inside its time budget
            # instead of paying the timeout again for every remaining game.
            self.logger.debug(
                "Skipping odds fetch for %s: a recent request failed, holding off "
                "for another %.0fs", cache_key,
                self._skip_network_until - time.monotonic())
            return None

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
            
            response = self.session.get(url, timeout=self.request_timeout)
            response.raise_for_status()
            raw_data = response.json()

            self._skip_network_until = 0.0   # reachable again

            self.logger.debug(f"Received raw odds data from ESPN: {json.dumps(raw_data, indent=2)}")
            
            odds_data = self._extract_espn_data(raw_data)
            if odds_data:
                self.logger.info(f"Successfully extracted odds data: {odds_data}")
            else:
                self.logger.debug("No odds data available for this game")
            
            if odds_data:
                self.cache_manager.set(cache_key, odds_data, ttl=interval)
                self.logger.info(f"Saved odds data to cache for {cache_key} with TTL {interval}s")
            else:
                self.logger.debug(f"No odds data available for {cache_key}")
                # Cache the fact that no odds are available to avoid repeated API calls
                self.cache_manager.set(cache_key, {"no_odds": True}, ttl=interval)
            
            return odds_data

        except requests.exceptions.RequestException as e:
            self._skip_network_until = time.monotonic() + self._FAILURE_COOLDOWN
            self.logger.error(
                "Error fetching odds from ESPN API for %s: %s. Holding off on odds "
                "for %.0fs so a slate of games does not pay this timeout each.",
                cache_key, e, self._FAILURE_COOLDOWN)
        except json.JSONDecodeError:
            self.logger.error(f"Error decoding JSON response from ESPN API for {cache_key}.")
        
        return self.cache_manager.get_with_auto_strategy(cache_key)

    def _extract_espn_data(self, data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Extract and format odds data from ESPN API response.
        
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
            
            # The ESPN API returns odds data directly in the item, not in a
            # providers array. ESPN sends explicit JSON nulls for absent
            # sides ("homeTeamOdds": null), so every level uses `or {}` —
            # .get's default only applies when the key is missing entirely.
            home = item.get("homeTeamOdds") or {}
            away = item.get("awayTeamOdds") or {}
            extracted_data = {
                "details": item.get("details"),
                "over_under": item.get("overUnder"),
                "spread": item.get("spread"),
                "home_team_odds": {
                    "money_line": home.get("moneyLine"),
                    "spread_odds": ((home.get("current") or {})
                                    .get("pointSpread") or {}).get("value")
                },
                "away_team_odds": {
                    "money_line": away.get("moneyLine"),
                    "spread_odds": ((away.get("current") or {})
                                    .get("pointSpread") or {}).get("value")
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
    
    def get_odds_for_games(self, games: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Fetch odds for multiple games efficiently.

        Args:
            games: List of game dictionaries with sport, league, and id

        Returns:
            List of games with odds data added
        """
        games_with_odds = []

        for game in games:
            try:
                sport = game.get('sport')
                league = game.get('league')
                event_id = game.get('id')

                if sport and league and event_id:
                    odds_data = self.get_odds(sport, league, event_id)
                    game['odds'] = odds_data
                else:
                    game['odds'] = None

                games_with_odds.append(game)

            except Exception as e:
                self.logger.error(f"Error fetching odds for game {game.get('id', 'unknown')}: {e}")
                game['odds'] = None
                games_with_odds.append(game)

        return games_with_odds
    
    def is_odds_available(self, odds_data: Optional[Dict[str, Any]]) -> bool:
        """
        Check if odds data contains valid odds information.
        
        Args:
            odds_data: Odds data dictionary
            
        Returns:
            True if valid odds are available, False otherwise
        """
        if not odds_data or odds_data.get('no_odds'):
            return False
            
        # Check for any valid odds data
        if odds_data.get('spread') is not None:
            return True
        if odds_data.get('home_team_odds', {}).get('spread_odds') is not None:
            return True
        if odds_data.get('away_team_odds', {}).get('spread_odds') is not None:
            return True
        if odds_data.get('over_under') is not None:
            return True
            
        return False
    
    def format_odds_summary(self, odds_data: Optional[Dict[str, Any]]) -> str:
        """
        Format odds data into a human-readable summary.
        
        Args:
            odds_data: Odds data dictionary
            
        Returns:
            Formatted odds summary string
        """
        # Gate only on truly-empty / negative-cached data. is_odds_available
        # deliberately ignores money lines (its callers decide whether to
        # RENDER an odds widget), but a summary of money-line-only odds is
        # still meaningful — the parts loop below handles them.
        if not odds_data or odds_data.get('no_odds'):
            return "No odds available"

        parts = []
        
        # Add spread information
        spread = odds_data.get('spread')
        if spread is not None:
            parts.append(f"Spread: {spread}")
        
        # Add over/under
        over_under = odds_data.get('over_under')
        if over_under is not None:
            parts.append(f"O/U: {over_under}")
        
        # Add money lines
        home_ml = odds_data.get('home_team_odds', {}).get('money_line')
        away_ml = odds_data.get('away_team_odds', {}).get('money_line')
        
        if home_ml is not None:
            parts.append(f"Home ML: {home_ml}")
        if away_ml is not None:
            parts.append(f"Away ML: {away_ml}")
        
        return " | ".join(parts) if parts else "No odds available"
