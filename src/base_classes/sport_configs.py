"""
Sport-Specific Configuration System

This module provides sport-specific configurations including update cadences,
season characteristics, and sport-specific fields for different sports.
"""

from typing import Dict, Any, List
import logging

class SportConfig:
    """Configuration for a specific sport."""
    
    def __init__(self, sport_key: str, config: Dict[str, Any]):
        self.sport_key = sport_key
        self.config = config
        
        # Sport-specific characteristics
        self.update_cadence = config.get('update_cadence', 'daily')
        self.season_length = config.get('season_length', 16)
        self.games_per_week = config.get('games_per_week', 1)
        self.api_endpoints = config.get('api_endpoints', ['scoreboard'])
        self.sport_specific_fields = config.get('sport_specific_fields', [])
        self.update_interval_seconds = config.get('update_interval_seconds', 60)
        self.logo_dir = config.get('logo_dir', 'assets/sports/ncaa_logos')
        
        # Display characteristics
        self.show_records = config.get('show_records', False)
        self.show_ranking = config.get('show_ranking', False)
        self.show_odds = config.get('show_odds', False)
        
        # Data source configuration
        self.data_source_type = config.get('data_source_type', 'espn')
        self.api_base_url = config.get('api_base_url', '')
        self.requires_authentication = config.get('requires_authentication', False)
        
    def get_update_interval(self) -> int:
        """Get the appropriate update interval for this sport."""
        return self.update_interval_seconds
    
    def should_update_now(self, last_update: float, current_time: float) -> bool:
        """Check if this sport should be updated based on its cadence."""
        time_since_update = current_time - last_update
        
        if self.update_cadence == 'daily':
            return time_since_update >= 3600  # 1 hour
        elif self.update_cadence == 'weekly':
            return time_since_update >= 86400  # 24 hours
        elif self.update_cadence == 'hourly':
            return time_since_update >= 3600  # 1 hour
        elif self.update_cadence == 'live_only':
            return time_since_update >= 30  # 30 seconds for live games
        else:
            return time_since_update >= self.update_interval_seconds


def get_sport_configs() -> Dict[str, Dict[str, Any]]:
    """Get all sport-specific configurations."""
    return {
        'nfl': {
            'update_cadence': 'weekly',
            'season_length': 17,
            'games_per_week': 1,
            'api_endpoints': ['scoreboard', 'standings'],
            'sport_specific_fields': ['down', 'distance', 'possession', 'timeouts', 'is_redzone'],
            'update_interval_seconds': 60,
            'logo_dir': 'assets/sports/nfl_logos',
            'show_records': True,
            'show_ranking': True,
            'show_odds': True,
            'data_source_type': 'espn',
            'api_base_url': 'https://site.api.espn.com/apis/site/v2/sports/football/nfl'
        },
        'ncaa_fb': {
            'update_cadence': 'weekly',
            'season_length': 12,
            'games_per_week': 1,
            'api_endpoints': ['scoreboard', 'standings'],
            'sport_specific_fields': ['down', 'distance', 'possession', 'timeouts', 'is_redzone'],
            'update_interval_seconds': 60,
            'logo_dir': 'assets/sports/ncaa_logos',
            'show_records': True,
            'show_ranking': True,
            'show_odds': True,
            'data_source_type': 'espn',
            'api_base_url': 'https://site.api.espn.com/apis/site/v2/sports/football/college-football'
        },
        'mlb': {
            'update_cadence': 'daily',
            'season_length': 162,
            'games_per_week': 6,
            'api_endpoints': ['scoreboard', 'standings', 'stats'],
            'sport_specific_fields': ['inning', 'outs', 'bases', 'strikes', 'balls', 'pitcher', 'batter'],
            'update_interval_seconds': 30,
            'logo_dir': 'assets/sports/mlb_logos',
            'show_records': True,
            'show_ranking': True,
            'show_odds': True,
            'data_source_type': 'mlb_api',
            'api_base_url': 'https://statsapi.mlb.com/api/v1'
        },
        'nhl': {
            'update_cadence': 'daily',
            'season_length': 82,
            'games_per_week': 3,
            'api_endpoints': ['scoreboard', 'standings'],
            'sport_specific_fields': ['period', 'power_play', 'penalties', 'shots_on_goal'],
            'update_interval_seconds': 30,
            'logo_dir': 'assets/sports/nhl_logos',
            'show_records': True,
            'show_ranking': True,
            'show_odds': True,
            'data_source_type': 'espn',
            'api_base_url': 'https://site.api.espn.com/apis/site/v2/sports/hockey/nhl'
        },
        'ncaam_hockey': {
            'update_cadence': 'weekly',
            'season_length': 34,
            'games_per_week': 2,
            'api_endpoints': ['scoreboard', 'standings'],
            'sport_specific_fields': ['period', 'power_play', 'penalties', 'shots_on_goal'],
            'update_interval_seconds': 60,
            'logo_dir': 'assets/sports/ncaa_logos',
            'show_records': True,
            'show_ranking': True,
            'show_odds': False,
            'data_source_type': 'espn',
            'api_base_url': 'https://site.api.espn.com/apis/site/v2/sports/hockey/mens-college-hockey'
        },
        'soccer': {
            'update_cadence': 'weekly',
            'season_length': 34,
            'games_per_week': 1,
            'api_endpoints': ['fixtures', 'standings'],
            'sport_specific_fields': ['half', 'stoppage_time', 'cards', 'possession'],
            'update_interval_seconds': 60,
            'logo_dir': 'assets/sports/soccer_logos',
            'show_records': True,
            'show_ranking': True,
            'show_odds': True,
            'data_source_type': 'soccer_api',
            'api_base_url': 'https://api.football-data.org/v4'
        },
        'nba': {
            'update_cadence': 'daily',
            'season_length': 82,
            'games_per_week': 3,
            'api_endpoints': ['scoreboard', 'standings'],
            'sport_specific_fields': ['quarter', 'time_remaining', 'fouls', 'timeouts'],
            'update_interval_seconds': 30,
            'logo_dir': 'assets/sports/nba_logos',
            'show_records': True,
            'show_ranking': True,
            'show_odds': True,
            'data_source_type': 'espn',
            'api_base_url': 'https://site.api.espn.com/apis/site/v2/sports/basketball/nba'
        }
    }


def get_sport_config(sport_key: str, logger: logging.Logger) -> SportConfig:
    """Get configuration for a specific sport."""
    configs = get_sport_configs()
    sport_config = configs.get(sport_key, {})
    
    if not sport_config:
        logger.warning(f"No configuration found for sport: {sport_key}, using default")
        sport_config = {
            'update_cadence': 'daily',
            'season_length': 16,
            'games_per_week': 1,
            'api_endpoints': ['scoreboard'],
            'sport_specific_fields': [],
            'update_interval_seconds': 60,
            'logo_dir': 'assets/sports/ncaa_logos',
            'show_records': False,
            'show_ranking': False,
            'show_odds': False,
            'data_source_type': 'espn',
            'api_base_url': ''
        }
    
    return SportConfig(sport_key, sport_config)


def get_sports_by_update_cadence(cadence: str) -> List[str]:
    """Get all sports that use a specific update cadence."""
    configs = get_sport_configs()
    return [sport for sport, config in configs.items() if config.get('update_cadence') == cadence]


def get_sports_by_data_source(data_source_type: str) -> List[str]:
    """Get all sports that use a specific data source."""
    configs = get_sport_configs()
    return [sport for sport, config in configs.items() if config.get('data_source_type') == data_source_type]
