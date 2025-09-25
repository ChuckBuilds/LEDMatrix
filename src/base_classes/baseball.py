"""
Baseball Base Classes

This module provides baseball-specific base classes that extend the core sports functionality
with baseball-specific logic for innings, outs, bases, strikes, balls, etc.
"""

from typing import Dict, Any, Optional, List
from src.base_classes.sports import SportsCore
from src.base_classes.api_extractors import ESPNBaseballExtractor
from src.base_classes.data_sources import ESPNDataSource, MLBAPIDataSource
import logging

class Baseball(SportsCore):
    """Base class for baseball sports with common functionality."""
    
    # Baseball sport configuration (moved from sport_configs.py)
    SPORT_CONFIG = {
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
        'data_source_type': 'espn',  # Can be overridden for MLB API
        'api_base_url': 'https://site.api.espn.com/apis/site/v2/sports/baseball'
    }
    
    def __init__(self, config: Dict[str, Any], display_manager, cache_manager, logger: logging.Logger, sport_key: str):
        super().__init__(config, display_manager, cache_manager, logger, sport_key)
        
        # Initialize baseball-specific architecture components
        self.sport_config = self.get_sport_config()
        self.api_extractor = ESPNBaseballExtractor(logger)
        
        # Choose data source based on sport (MLB uses MLB API, others use ESPN)
        if sport_key == 'mlb':
            self.data_source = MLBAPIDataSource(logger)
        else:
            self.data_source = ESPNDataSource(logger)
        
        # Baseball-specific configuration
        self.show_innings = self.mode_config.get("show_innings", True)
        self.show_outs = self.mode_config.get("show_outs", True)
        self.show_bases = self.mode_config.get("show_bases", True)
        self.show_count = self.mode_config.get("show_count", True)
        self.show_pitcher_batter = self.mode_config.get("show_pitcher_batter", False)
    
    def get_sport_config(self) -> Dict[str, Any]:
        """Get baseball sport configuration."""
        return self.SPORT_CONFIG.copy()
    
    def _get_baseball_display_text(self, game: Dict) -> str:
        """Get baseball-specific display text."""
        try:
            display_parts = []
            
            # Inning information
            if self.show_innings:
                inning = game.get('inning', '')
                if inning:
                    display_parts.append(f"Inning: {inning}")
            
            # Outs information
            if self.show_outs:
                outs = game.get('outs', 0)
                if outs is not None:
                    display_parts.append(f"Outs: {outs}")
            
            # Bases information
            if self.show_bases:
                bases = game.get('bases', '')
                if bases:
                    display_parts.append(f"Bases: {bases}")
            
            # Count information
            if self.show_count:
                strikes = game.get('strikes', 0)
                balls = game.get('balls', 0)
                if strikes is not None and balls is not None:
                    display_parts.append(f"Count: {balls}-{strikes}")
            
            # Pitcher/Batter information
            if self.show_pitcher_batter:
                pitcher = game.get('pitcher', '')
                batter = game.get('batter', '')
                if pitcher:
                    display_parts.append(f"Pitcher: {pitcher}")
                if batter:
                    display_parts.append(f"Batter: {batter}")
            
            return " | ".join(display_parts) if display_parts else ""
            
        except Exception as e:
            self.logger.error(f"Error getting baseball display text: {e}")
            return ""
    
    def _is_baseball_game_live(self, game: Dict) -> bool:
        """Check if a baseball game is currently live."""
        try:
            # Check if game is marked as live
            is_live = game.get('is_live', False)
            if is_live:
                return True
            
            # Check inning to determine if game is active
            inning = game.get('inning', '')
            if inning and inning != 'Final':
                return True
            
            return False
            
        except Exception as e:
            self.logger.error(f"Error checking if baseball game is live: {e}")
            return False
    
    def _get_baseball_game_status(self, game: Dict) -> str:
        """Get baseball-specific game status."""
        try:
            status = game.get('status_text', '')
            inning = game.get('inning', '')
            
            if self._is_baseball_game_live(game):
                if inning:
                    return f"Live - {inning}"
                else:
                    return "Live"
            elif game.get('is_final', False):
                return "Final"
            elif game.get('is_upcoming', False):
                return "Upcoming"
            else:
                return status
                
        except Exception as e:
            self.logger.error(f"Error getting baseball game status: {e}")
            return ""


class BaseballLive(Baseball):
    """Base class for live baseball games."""
    
    def __init__(self, config: Dict[str, Any], display_manager, cache_manager, logger: logging.Logger, sport_key: str):
        super().__init__(config, display_manager, cache_manager, logger, sport_key)
        self.logger.info(f"{sport_key.upper()} Live Manager initialized")
    
    def _should_show_baseball_game(self, game: Dict) -> bool:
        """Determine if a baseball game should be shown."""
        try:
            # Only show live games
            if not self._is_baseball_game_live(game):
                return False
            
            # Check if game meets display criteria
            return self._should_show_game(game)
            
        except Exception as e:
            self.logger.error(f"Error checking if baseball game should be shown: {e}")
            return False


