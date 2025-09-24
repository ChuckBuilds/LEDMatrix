"""
MLB (Major League Baseball) Managers

This module demonstrates how to add a new sport using the new architecture.
Baseball has different characteristics than football/hockey:
- Daily games during season
- Different sport-specific fields (innings, outs, bases, etc.)
- Different data source (MLB API instead of ESPN)
"""

import os
import time
import logging
import requests
from typing import Dict, Any, Optional, List
from datetime import datetime, timedelta
from src.display_manager import DisplayManager
from src.cache_manager import CacheManager
import pytz
from src.base_classes.sports import SportsRecent, SportsUpcoming, SportsCore
from pathlib import Path

class BaseMLBManager(SportsCore):
    """Base class for MLB managers with common functionality."""
    # Class variables for warning tracking
    _no_data_warning_logged = False
    _last_warning_time = 0
    _warning_cooldown = 60  # Only log warnings once per minute
    _shared_data = None
    _last_shared_update = 0
    _processed_games_cache = {}  # Cache for processed game data
    _processed_games_timestamp = 0

    def __init__(self, config: Dict[str, Any], display_manager: DisplayManager, cache_manager: CacheManager):
        self.logger = logging.getLogger('MLB')
        super().__init__(config=config, display_manager=display_manager, cache_manager=cache_manager, logger=self.logger, sport_key="mlb")
        
        # Override configuration with sport-specific settings
        self.logo_dir = Path(self.sport_config.logo_dir)
        self.update_interval = self.sport_config.get_update_interval()

        # Check display modes to determine what data to fetch
        display_modes = self.mode_config.get("display_modes", {})
        self.recent_enabled = display_modes.get("mlb_recent", False)
        self.upcoming_enabled = display_modes.get("mlb_upcoming", False)
        self.live_enabled = display_modes.get("mlb_live", False)

        # MLB-specific configuration
        self.favorite_teams = self.mode_config.get("favorite_teams", [])
        self.show_records = self.sport_config.show_records
        self.show_ranking = self.sport_config.show_ranking
        self.show_odds = self.sport_config.show_odds

    def _get_sport_specific_display_text(self, game: Dict) -> str:
        """Get sport-specific display text for baseball."""
        try:
            # Extract baseball-specific fields
            inning = game.get('inning', '')
            outs = game.get('outs', 0)
            bases = game.get('bases', '')
            strikes = game.get('strikes', 0)
            balls = game.get('balls', 0)
            
            # Build display text
            display_parts = []
            
            if inning:
                display_parts.append(f"Inning: {inning}")
            
            if outs is not None:
                display_parts.append(f"Outs: {outs}")
            
            if bases:
                display_parts.append(f"Bases: {bases}")
            
            if strikes is not None and balls is not None:
                display_parts.append(f"Count: {balls}-{strikes}")
            
            return " | ".join(display_parts) if display_parts else ""
            
        except Exception as e:
            self.logger.error(f"Error getting sport-specific display text: {e}")
            return ""

    def _should_show_game(self, game: Dict) -> bool:
        """Determine if a game should be shown based on MLB-specific criteria."""
        try:
            # Check if game is live or recent
            is_live = game.get('is_live', False)
            is_final = game.get('is_final', False)
            is_upcoming = game.get('is_upcoming', False)
            
            # Show live games
            if is_live and self.live_enabled:
                return True
            
            # Show recent games (within last 24 hours)
            if is_final and self.recent_enabled:
                # Check if game ended within last 24 hours
                game_time = game.get('start_time_utc')
                if game_time:
                    time_diff = datetime.now(pytz.UTC) - game_time
                    if time_diff.total_seconds() < 86400:  # 24 hours
                        return True
            
            # Show upcoming games (within next 7 days)
            if is_upcoming and self.upcoming_enabled:
                game_time = game.get('start_time_utc')
                if game_time:
                    time_diff = game_time - datetime.now(pytz.UTC)
                    if time_diff.total_seconds() < 604800:  # 7 days
                        return True
            
            return False
            
        except Exception as e:
            self.logger.error(f"Error checking if game should be shown: {e}")
            return False


class MLBLiveManager(BaseMLBManager):
    """Manager for live MLB games."""
    
    def __init__(self, config: Dict[str, Any], display_manager: DisplayManager, cache_manager: CacheManager):
        super().__init__(config, display_manager, cache_manager)
        self.logger.info("MLB Live Manager initialized")

    def get_duration(self) -> int:
        """Get display duration for live MLB games."""
        return self.mode_config.get("duration", 10)

    def display(self) -> bool:
        """Display live MLB games."""
        try:
            # Fetch live games using the new architecture
            live_games = self._fetch_immediate_games()
            
            if not live_games:
                if not self._no_data_warning_logged:
                    self.logger.warning("No live MLB games found")
                    self._no_data_warning_logged = True
                return False
            
            # Filter games based on criteria
            games_to_show = [game for game in live_games if self._should_show_game(game)]
            
            if not games_to_show:
                self.logger.debug("No MLB games meet display criteria")
                return False
            
            # Display each game
            for game in games_to_show:
                self._display_single_game(game)
                time.sleep(2)  # Brief pause between games
            
            return True
            
        except Exception as e:
            self.logger.error(f"Error displaying live MLB games: {e}")
            return False

    def _display_single_game(self, game: Dict) -> None:
        """Display a single MLB game."""
        try:
            # Get game details
            home_team = game.get('home_team_name', '')
            away_team = game.get('away_team_name', '')
            home_score = game.get('home_score', '0')
            away_score = game.get('away_score', '0')
            status = game.get('status_text', '')
            
            # Get sport-specific display text
            sport_text = self._get_sport_specific_display_text(game)
            
            # Create display text
            display_text = f"{away_team} {away_score} @ {home_team} {home_score}"
            if status:
                display_text += f" - {status}"
            if sport_text:
                display_text += f" ({sport_text})"
            
            # Display the text
            self.display_manager.display_text(display_text)
            
        except Exception as e:
            self.logger.error(f"Error displaying single MLB game: {e}")


class MLBRecentManager(BaseMLBManager):
    """Manager for recent MLB games."""
    
    def __init__(self, config: Dict[str, Any], display_manager: DisplayManager, cache_manager: CacheManager):
        super().__init__(config, display_manager, cache_manager)
        self.logger.info("MLB Recent Manager initialized")

    def get_duration(self) -> int:
        """Get display duration for recent MLB games."""
        return self.mode_config.get("duration", 8)

    def display(self) -> bool:
        """Display recent MLB games."""
        try:
            # Fetch recent games using the new architecture
            recent_games = self._get_partial_schedule_data(datetime.now().year)
            
            if not recent_games:
                if not self._no_data_warning_logged:
                    self.logger.warning("No recent MLB games found")
                    self._no_data_warning_logged = True
                return False
            
            # Filter for recent games (last 24 hours)
            now = datetime.now(pytz.UTC)
            recent_games = [game for game in recent_games 
                           if game.get('is_final', False) and 
                           game.get('start_time_utc') and 
                           (now - game['start_time_utc']).total_seconds() < 86400]
            
            if not recent_games:
                self.logger.debug("No recent MLB games in last 24 hours")
                return False
            
            # Display each game
            for game in recent_games:
                self._display_single_game(game)
                time.sleep(2)
            
            return True
            
        except Exception as e:
            self.logger.error(f"Error displaying recent MLB games: {e}")
            return False


class MLBUpcomingManager(BaseMLBManager):
    """Manager for upcoming MLB games."""
    
    def __init__(self, config: Dict[str, Any], display_manager: DisplayManager, cache_manager: CacheManager):
        super().__init__(config, display_manager, cache_manager)
        self.logger.info("MLB Upcoming Manager initialized")

    def get_duration(self) -> int:
        """Get display duration for upcoming MLB games."""
        return self.mode_config.get("duration", 6)

    def display(self) -> bool:
        """Display upcoming MLB games."""
        try:
            # Fetch upcoming games using the new architecture
            upcoming_games = self._get_partial_schedule_data(datetime.now().year)
            
            if not upcoming_games:
                if not self._no_data_warning_logged:
                    self.logger.warning("No upcoming MLB games found")
                    self._no_data_warning_logged = True
                return False
            
            # Filter for upcoming games (next 7 days)
            now = datetime.now(pytz.UTC)
            upcoming_games = [game for game in upcoming_games 
                             if game.get('is_upcoming', False) and 
                             game.get('start_time_utc') and 
                             (game['start_time_utc'] - now).total_seconds() < 604800]
            
            if not upcoming_games:
                self.logger.debug("No upcoming MLB games in next 7 days")
                return False
            
            # Display each game
            for game in upcoming_games:
                self._display_single_game(game)
                time.sleep(2)
            
            return True
            
        except Exception as e:
            self.logger.error(f"Error displaying upcoming MLB games: {e}")
            return False
