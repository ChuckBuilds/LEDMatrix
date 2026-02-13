"""
Pluggable Data Source Architecture for UFC Scoreboard Plugin

Based on original LEDMatrix data source architecture.
UFC/MMA adaptation based on work by Alex Resnick (legoguy1000) - PR #137
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
            now = datetime.now()
            formatted_date = now.strftime("%Y%m%d")
            url = f"{self.base_url}/{sport}/{league}/scoreboard"
            response = self.session.get(
                url,
                params={"dates": formatted_date, "limit": 1000},
                headers=self.get_headers(),
                timeout=15
            )
            response.raise_for_status()

            data = response.json()
            events = data.get('events', [])

            # Filter for live games
            live_events = [
                event for event in events
                if event.get('competitions', [{}])[0]
                .get('status', {}).get('type', {}).get('state') == 'in'
            ]

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
                'dates': f"{start_date.strftime('%Y%m%d')}-{end_date.strftime('%Y%m%d')}",
                "limit": 1000
            }

            response = self.session.get(
                url, headers=self.get_headers(), params=params, timeout=15
            )
            response.raise_for_status()

            data = response.json()
            events = data.get('events', [])

            self.logger.debug(f"Fetched {len(events)} scheduled games for {sport}/{league}")
            return events

        except Exception as e:
            self.logger.error(f"Error fetching schedule from ESPN: {e}")
            return []

    def fetch_standings(self, sport: str, league: str) -> Dict:
        """Fetch standings from ESPN API (not applicable for MMA, returns empty)."""
        self.logger.debug(f"Standings not applicable for {sport}/{league}")
        return {}
