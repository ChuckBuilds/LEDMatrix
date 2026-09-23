"""
Cache Strategy

Manages cache strategies (TTLs) for different data types.
"""

import logging
from typing import Dict, Any, Optional
from datetime import datetime
import pytz


class CacheStrategy:
    """Manages cache strategies for different data types."""
    
    def __init__(self, config_manager: Optional[Any] = None, logger: Optional[logging.Logger] = None) -> None:
        """
        Initialize cache strategy manager.
        
        Args:
            config_manager: Optional ConfigManager instance. Kept for callers
                that pass one; no strategy currently reads it.
            logger: Optional logger instance
        """
        self.config_manager = config_manager
        self.logger = logger or logging.getLogger(__name__)
    
    def get_sport_live_interval(self, sport_key: str) -> int:
        """
        Live-data cache interval, in seconds, for a sport: 60 for every sport.

        This used to read ``live_update_interval`` from a ``<sport>_scoreboard``
        config section. Those sections belonged to the built-in scoreboards
        that the plugin system replaced; plugin config is keyed by plugin id
        (``football-scoreboard``), so the lookup always fell back to 60.

        Args:
            sport_key: Sport identifier (e.g., 'nba', 'nfl')

        Returns:
            Live update interval in seconds
        """
        return 60

    def get_cache_strategy(self, data_type: str, sport_key: Optional[str] = None) -> Dict[str, Any]:
        """
        Get cache strategy for different data types.

        Args:
            data_type: Type of data (e.g., 'live_scores', 'stocks', 'weather_current')
            sport_key: Optional sport key; for live data it selects the
                per-sport interval from :meth:`get_sport_live_interval`
                instead of the generic live default.

        Returns:
            Dictionary with cache strategy (max_age, memory_ttl, etc.)
        """
        live_interval = None
        if sport_key and data_type in ['sports_live', 'live_scores']:
            live_interval = self.get_sport_live_interval(sport_key)

        strategies = {
            # Ultra time-sensitive data (live scores, current weather)
            'live_scores': {
                'max_age': live_interval or 15,  # Use sport-specific interval
                'memory_ttl': (live_interval or 15) * 2,  # 2x for memory cache
                'force_refresh': True
            },
            'sports_live': {
                'max_age': live_interval or 30,  # Use sport-specific interval
                'memory_ttl': (live_interval or 30) * 2,
                'force_refresh': True
            },
            'weather_current': {
                'max_age': 300,  # 5 minutes
                'memory_ttl': 600,
                'force_refresh': False
            },
            
            # Market data (stocks, crypto)
            'stocks': {
                'max_age': 600,  # 10 minutes
                'memory_ttl': 1200,
                'market_hours_only': True,
                'force_refresh': False
            },
            'crypto': {
                'max_age': 300,  # 5 minutes (crypto trades 24/7)
                'memory_ttl': 600,
                'force_refresh': False
            },
            
            # Sports data
            'sports_recent': {
                'max_age': 1800,  # 30 minutes
                'memory_ttl': 3600,
                'force_refresh': False
            },
            'sports_upcoming': {
                'max_age': 10800,  # 3 hours
                'memory_ttl': 21600,
                'force_refresh': False
            },
            'sports_schedules': {
                'max_age': 86400,  # 24 hours
                'memory_ttl': 172800,
                'force_refresh': False
            },
            'leaderboard': {
                'max_age': 604800,  # 7 days (1 week) - football rankings updated weekly
                'memory_ttl': 1209600,  # 14 days in memory
                'force_refresh': False
            },
            
            # News and odds
            'news': {
                'max_age': 3600,  # 1 hour
                'memory_ttl': 7200,
                'force_refresh': False
            },
            'odds': {
                'max_age': 1800,  # 30 minutes for upcoming games
                'memory_ttl': 3600,
                'force_refresh': False
            },
            'odds_live': {
                'max_age': 120,  # 2 minutes for live games (odds change rapidly)
                'memory_ttl': 240,
                'force_refresh': False
            },
            
            # Static/stable data
            'team_info': {
                'max_age': 604800,  # 1 week
                'memory_ttl': 1209600,
                'force_refresh': False
            },
            'logos': {
                'max_age': 2592000,  # 30 days
                'memory_ttl': 5184000,
                'force_refresh': False
            },
            
            # Default fallback
            'default': {
                'max_age': 300,  # 5 minutes
                'memory_ttl': 600,
                'force_refresh': False
            }
        }
        
        return strategies.get(data_type, strategies['default'])
    
    def get_data_type_from_key(self, key: str) -> str:
        """
        Determine the appropriate cache strategy based on the cache key.
        This helps automatically select the right cache duration.
        
        Args:
            key: Cache key
            
        Returns:
            Data type string for strategy lookup
        """
        key_lower = key.lower()

        # Odds data — checked before the generic 'live' block below because
        # live-odds cache keys (e.g. odds_espn_basketball_nba_<id>_live) contain
        # both 'odds' AND 'live'.  Without this ordering the 'live' check below
        # would match first and return 'sports_live' (30 s TTL) instead of the
        # correct 'odds_live' (120 s TTL).
        if 'odds' in key_lower:
            if any(x in key_lower for x in ['live', 'current']):
                return 'odds_live'  # Live odds change more frequently
            return 'odds'  # Regular odds for upcoming games

        # Live sports data
        if any(x in key_lower for x in ['live', 'current', 'scoreboard']):
            return 'sports_live'

        # Weather data
        if 'weather' in key_lower:
            return 'weather_current'

        # Market data
        if 'stock' in key_lower or 'crypto' in key_lower:
            if 'crypto' in key_lower:
                return 'crypto'
            return 'stocks'

        # News data
        if 'news' in key_lower:
            return 'news'
        
        # Sports schedules and team info
        if any(x in key_lower for x in ['schedule', 'team_map', 'league']):
            return 'sports_schedules'
        
        # Recent games (last few hours)
        if 'recent' in key_lower:
            return 'sports_recent'
        
        # Upcoming games
        if 'upcoming' in key_lower:
            return 'sports_upcoming'
        
        # Static data like logos, team info
        if any(x in key_lower for x in ['logo', 'team_info', 'config']):
            return 'team_info'
        
        # Default fallback
        return 'default'
    
    def get_sport_key_from_cache_key(self, key: str) -> Optional[str]:
        """
        Extract sport key from cache key to determine appropriate live_update_interval.
        
        Args:
            key: Cache key
            
        Returns:
            Sport key or None if not found
        """
        key_lower = key.lower()
        
        # Map cache key patterns to sport keys
        sport_patterns = {
            'nfl': ['nfl'],
            'nba': ['nba', 'basketball'],
            'mlb': ['mlb', 'baseball'],
            'nhl': ['nhl', 'hockey'],
            'soccer': ['soccer'],
            'ncaa_fb': ['ncaa_fb', 'ncaafb', 'college_football'],
            'ncaa_baseball': ['ncaa_baseball', 'college_baseball'],
            'ncaam_basketball': ['ncaam_basketball', 'college_basketball'],
            'milb': ['milb', 'minor_league'],
        }
        
        for sport_key, patterns in sport_patterns.items():
            if any(pattern in key_lower for pattern in patterns):
                return sport_key
        
        return None
    
    def is_market_open(self) -> bool:
        """
        Check if the US stock market is currently open.
        
        Returns:
            True if market is open, False otherwise
        """
        et_tz = pytz.timezone('America/New_York')
        now = datetime.now(et_tz)
        
        # Check if it's a weekday
        if now.weekday() >= 5:  # 5 = Saturday, 6 = Sunday
            return False
        
        # Convert current time to ET
        current_time = now.time()
        market_open = datetime.strptime('09:30', '%H:%M').time()
        market_close = datetime.strptime('16:00', '%H:%M').time()
        
        return market_open <= current_time <= market_close

