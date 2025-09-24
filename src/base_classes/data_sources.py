"""
Pluggable Data Source Architecture

This module provides abstract data sources that can be plugged into the sports system
to support different APIs and data providers.
"""

from abc import ABC, abstractmethod
from typing import Dict, Any, Optional, List
import requests
import logging
from datetime import datetime, timedelta
import time

class DataSource(ABC):
    """Abstract base class for data sources."""
    
    def __init__(self, logger: logging.Logger):
        self.logger = logger
        self.session = requests.Session()
        
        # Configure retry strategy
        from requests.adapters import HTTPAdapter
        from urllib3.util.retry import Retry
        
        retry_strategy = Retry(
            total=5,
            backoff_factor=1,
            status_forcelist=[429, 500, 502, 503, 504],
        )
        adapter = HTTPAdapter(max_retries=retry_strategy)
        self.session.mount("http://", adapter)
        self.session.mount("https://", adapter)
    
    @abstractmethod
    def fetch_live_games(self, sport: str, league: str) -> List[Dict]:
        """Fetch live games for a sport/league."""
        pass
    
    @abstractmethod
    def fetch_schedule(self, sport: str, league: str, date_range: tuple) -> List[Dict]:
        """Fetch schedule for a sport/league within date range."""
        pass
    
    @abstractmethod
    def fetch_standings(self, sport: str, league: str) -> Dict:
        """Fetch standings for a sport/league."""
        pass
    
    def get_headers(self) -> Dict[str, str]:
        """Get headers for API requests."""
        return {
            'User-Agent': 'LEDMatrix/1.0',
            'Accept': 'application/json'
        }


class ESPNDataSource(DataSource):
    """ESPN API data source."""
    
    def __init__(self, logger: logging.Logger):
        super().__init__(logger)
        self.base_url = "https://site.api.espn.com/apis/site/v2/sports"
    
    def fetch_live_games(self, sport: str, league: str) -> List[Dict]:
        """Fetch live games from ESPN API."""
        try:
            url = f"{self.base_url}/{sport}/{league}/scoreboard"
            response = self.session.get(url, headers=self.get_headers(), timeout=15)
            response.raise_for_status()
            
            data = response.json()
            events = data.get('events', [])
            
            # Filter for live games
            live_events = [event for event in events 
                          if event.get('competitions', [{}])[0].get('status', {}).get('type', {}).get('state') == 'in']
            
            self.logger.debug(f"Fetched {len(live_events)} live games for {sport}/{league}")
            return live_events
            
        except Exception as e:
            self.logger.error(f"Error fetching live games from ESPN: {e}")
            return []
    
    def fetch_schedule(self, sport: str, league: str, date_range: tuple) -> List[Dict]:
        """Fetch schedule from ESPN API."""
        try:
            start_date, end_date = date_range
            url = f"{self.base_url}/{sport}/{league}/scoreboard"
            
            params = {
                'dates': f"{start_date.strftime('%Y%m%d')}-{end_date.strftime('%Y%m%d')}"
            }
            
            response = self.session.get(url, headers=self.get_headers(), params=params, timeout=15)
            response.raise_for_status()
            
            data = response.json()
            events = data.get('events', [])
            
            self.logger.debug(f"Fetched {len(events)} scheduled games for {sport}/{league}")
            return events
            
        except Exception as e:
            self.logger.error(f"Error fetching schedule from ESPN: {e}")
            return []
    
    def fetch_standings(self, sport: str, league: str) -> Dict:
        """Fetch standings from ESPN API."""
        try:
            url = f"{self.base_url}/{sport}/{league}/standings"
            response = self.session.get(url, headers=self.get_headers(), timeout=15)
            response.raise_for_status()
            
            data = response.json()
            self.logger.debug(f"Fetched standings for {sport}/{league}")
            return data
            
        except Exception as e:
            self.logger.error(f"Error fetching standings from ESPN: {e}")
            return {}


class MLBAPIDataSource(DataSource):
    """MLB API data source."""
    
    def __init__(self, logger: logging.Logger):
        super().__init__(logger)
        self.base_url = "https://statsapi.mlb.com/api/v1"
    
    def fetch_live_games(self, sport: str, league: str) -> List[Dict]:
        """Fetch live games from MLB API."""
        try:
            url = f"{self.base_url}/schedule"
            params = {
                'sportId': 1,  # MLB
                'date': datetime.now().strftime('%Y-%m-%d'),
                'hydrate': 'game,team,venue,weather'
            }
            
            response = self.session.get(url, headers=self.get_headers(), params=params, timeout=15)
            response.raise_for_status()
            
            data = response.json()
            games = data.get('dates', [{}])[0].get('games', [])
            
            # Filter for live games
            live_games = [game for game in games 
                         if game.get('status', {}).get('abstractGameState') == 'Live']
            
            self.logger.debug(f"Fetched {len(live_games)} live games from MLB API")
            return live_games
            
        except Exception as e:
            self.logger.error(f"Error fetching live games from MLB API: {e}")
            return []
    
    def fetch_schedule(self, sport: str, league: str, date_range: tuple) -> List[Dict]:
        """Fetch schedule from MLB API."""
        try:
            start_date, end_date = date_range
            url = f"{self.base_url}/schedule"
            
            params = {
                'sportId': 1,  # MLB
                'startDate': start_date.strftime('%Y-%m-%d'),
                'endDate': end_date.strftime('%Y-%m-%d'),
                'hydrate': 'game,team,venue'
            }
            
            response = self.session.get(url, headers=self.get_headers(), params=params, timeout=15)
            response.raise_for_status()
            
            data = response.json()
            all_games = []
            for date_data in data.get('dates', []):
                all_games.extend(date_data.get('games', []))
            
            self.logger.debug(f"Fetched {len(all_games)} scheduled games from MLB API")
            return all_games
            
        except Exception as e:
            self.logger.error(f"Error fetching schedule from MLB API: {e}")
            return []
    
    def fetch_standings(self, sport: str, league: str) -> Dict:
        """Fetch standings from MLB API."""
        try:
            url = f"{self.base_url}/standings"
            params = {
                'leagueId': 103,  # American League
                'season': datetime.now().year,
                'standingsType': 'regularSeason'
            }
            
            response = self.session.get(url, headers=self.get_headers(), params=params, timeout=15)
            response.raise_for_status()
            
            data = response.json()
            self.logger.debug(f"Fetched standings from MLB API")
            return data
            
        except Exception as e:
            self.logger.error(f"Error fetching standings from MLB API: {e}")
            return {}


class SoccerAPIDataSource(DataSource):
    """Soccer API data source (generic structure)."""
    
    def __init__(self, logger: logging.Logger, api_key: str = None):
        super().__init__(logger)
        self.api_key = api_key
        self.base_url = "https://api.football-data.org/v4"  # Example API
    
    def get_headers(self) -> Dict[str, str]:
        """Get headers with API key for soccer API."""
        headers = super().get_headers()
        if self.api_key:
            headers['X-Auth-Token'] = self.api_key
        return headers
    
    def fetch_live_games(self, sport: str, league: str) -> List[Dict]:
        """Fetch live games from soccer API."""
        try:
            # This would need to be adapted based on the specific soccer API
            url = f"{self.base_url}/matches"
            params = {
                'status': 'LIVE',
                'competition': league
            }
            
            response = self.session.get(url, headers=self.get_headers(), params=params, timeout=15)
            response.raise_for_status()
            
            data = response.json()
            matches = data.get('matches', [])
            
            self.logger.debug(f"Fetched {len(matches)} live games from soccer API")
            return matches
            
        except Exception as e:
            self.logger.error(f"Error fetching live games from soccer API: {e}")
            return []
    
    def fetch_schedule(self, sport: str, league: str, date_range: tuple) -> List[Dict]:
        """Fetch schedule from soccer API."""
        try:
            start_date, end_date = date_range
            url = f"{self.base_url}/matches"
            
            params = {
                'competition': league,
                'dateFrom': start_date.strftime('%Y-%m-%d'),
                'dateTo': end_date.strftime('%Y-%m-%d')
            }
            
            response = self.session.get(url, headers=self.get_headers(), params=params, timeout=15)
            response.raise_for_status()
            
            data = response.json()
            matches = data.get('matches', [])
            
            self.logger.debug(f"Fetched {len(matches)} scheduled games from soccer API")
            return matches
            
        except Exception as e:
            self.logger.error(f"Error fetching schedule from soccer API: {e}")
            return []
    
    def fetch_standings(self, sport: str, league: str) -> Dict:
        """Fetch standings from soccer API."""
        try:
            url = f"{self.base_url}/competitions/{league}/standings"
            response = self.session.get(url, headers=self.get_headers(), timeout=15)
            response.raise_for_status()
            
            data = response.json()
            self.logger.debug(f"Fetched standings from soccer API")
            return data
            
        except Exception as e:
            self.logger.error(f"Error fetching standings from soccer API: {e}")
            return {}


def get_data_source_for_sport(sport_key: str, data_source_type: str, logger: logging.Logger, **kwargs) -> DataSource:
    """Factory function to get the appropriate data source for a sport."""
    data_sources = {
        'espn': ESPNDataSource,
        'mlb_api': MLBAPIDataSource,
        'soccer_api': SoccerAPIDataSource
    }
    
    data_source_class = data_sources.get(data_source_type)
    if not data_source_class:
        logger.warning(f"No data source found for type: {data_source_type}, using ESPN")
        return ESPNDataSource(logger)
    
    return data_source_class(logger, **kwargs)
