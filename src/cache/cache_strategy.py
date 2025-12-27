"""
Cache Strategy

Manages cache strategies for different data types with sport-specific configurations.
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
            config_manager: Optional ConfigManager instance for sport-specific configs
            logger: Optional logger instance
        """
        self.config_manager = config_manager
        self.logger = logger or logging.getLogger(__name__)
    
    def get_sport_live_interval(self, sport_key: str) -> int:
        """
        Get the live_update_interval for a specific sport from config.
        Falls back to default values if config is not available.
        
        Args:
            sport_key: Sport identifier (e.g., 'nba', 'nfl')
            
        Returns:
            Live update interval in seconds
        """
        if not self.config_manager:
            # Default intervals - all sports use 60 seconds as default
            default_intervals = {
                'soccer': 60,
                'nfl': 60,
                'nhl': 60,
                'nba': 60,
                'mlb': 60,
                'milb': 60,
                'ncaa_fb': 60,
                'ncaa_baseball': 60,
                'ncaam_basketball': 60,
            }
            return default_intervals.get(sport_key, 60)
        
        try:
            config = self.config_manager.config
            # All sports now use _scoreboard suffix
            sport_config = config.get(f"{sport_key}_scoreboard", {})
            return sport_config.get("live_update_interval", 60)  # Default to 60 seconds
        except (KeyError, AttributeError, TypeError) as e:
            self.logger.warning("Could not get live_update_interval for %s: %s", sport_key, e, exc_info=True)
            return 60  # Default to 60 seconds
    
    def get_cache_strategy(self, data_type: str, sport_key: Optional[str] = None) -> Dict[str, Any]:
        """
        Get cache strategy for different data types.
        Now respects sport-specific live_update_interval configurations.
        
        Args:
            data_type: Type of data (e.g., 'live_scores', 'stocks', 'weather_current')
            sport_key: Optional sport key for sport-specific intervals
            
        Returns:
            Dictionary with cache strategy (max_age, memory_ttl, etc.)
        """
        # Get sport-specific live interval if provided
        live_interval = None
        if sport_key and data_type in ['sports_live', 'live_scores']:
            live_interval = self.get_sport_live_interval(sport_key)
        
        # Try to read sport-specific config for recent/upcoming
        recent_interval = None
        upcoming_interval = None
        if self.config_manager and sport_key:
            try:
                # All sports now use _scoreboard suffix
                sport_cfg = self.config_manager.config.get(f"{sport_key}_scoreboard", {})
                recent_interval = sport_cfg.get('recent_update_interval')
                upcoming_interval = sport_cfg.get('upcoming_update_interval')
            except (KeyError, AttributeError, TypeError) as e:
                self.logger.debug("Could not read sport-specific recent/upcoming intervals for %s: %s", 
                                sport_key, e, exc_info=True)
        
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
                'max_age': recent_interval or 1800,  # 30 minutes default; override by config
                'memory_ttl': (recent_interval or 1800) * 2,
                'force_refresh': False
            },
            'sports_upcoming': {
                'max_age': upcoming_interval or 10800,  # 3 hours default; override by config
                'memory_ttl': (upcoming_interval or 10800) * 2,
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
        
        # Live sports data
        if any(x in key_lower for x in ['live', 'current', 'scoreboard']):
            if 'soccer' in key_lower:
                return 'sports_live'  # Soccer live data is very time-sensitive
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
        
        # Odds data - differentiate between live and upcoming games
        if 'odds' in key_lower:
            # For live games, use shorter cache; for upcoming games, use longer cache
            if any(x in key_lower for x in ['live', 'current']):
                return 'odds_live'  # Live odds change more frequently
            return 'odds'  # Regular odds for upcoming games
        
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

