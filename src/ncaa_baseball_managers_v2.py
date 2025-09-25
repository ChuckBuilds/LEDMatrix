"""
NCAA Baseball Managers - Updated to use new baseball base class

This module demonstrates how to update existing baseball managers to use the new
baseball base class architecture while maintaining all existing functionality.
"""

import time
import logging
import requests
import json
from typing import Dict, Any, List, Optional
from datetime import datetime, timedelta, timezone
import os
from PIL import Image, ImageDraw, ImageFont
import numpy as np
from .cache_manager import CacheManager
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from src.odds_manager import OddsManager
import pytz

# Import new baseball base classes
from .base_classes.baseball import Baseball, BaseballLive, BaseballRecent, BaseballUpcoming

# Get logger
logger = logging.getLogger(__name__)

# Constants for NCAA Baseball API URL
ESPN_NCAABB_SCOREBOARD_URL = "https://site.api.espn.com/apis/site/v2/sports/baseball/college-baseball/scoreboard"

class BaseNCAABaseballManager(Baseball):
    """Base class for NCAA Baseball managers using new baseball architecture."""
    
    def __init__(self, config: Dict[str, Any], display_manager, cache_manager: CacheManager):
        # Initialize with sport_key for NCAA Baseball
        super().__init__(config, display_manager, cache_manager, logger, "ncaa_baseball")
        
        # NCAA Baseball-specific configuration
        self.ncaa_baseball_config = config.get('ncaa_baseball_scoreboard', {})
        self.show_odds = self.ncaa_baseball_config.get('show_odds', False)
        self.show_records = self.ncaa_baseball_config.get('show_records', False)
        self.favorite_teams = self.ncaa_baseball_config.get('favorite_teams', [])
        
        # Logo handling
        self.logo_dir = self.ncaa_baseball_config.get('logo_dir', os.path.join('assets', 'sports', 'ncaa_logos'))
        if not os.path.exists(self.logo_dir):
            self.logger.warning(f"NCAA Baseball logos directory not found: {self.logo_dir}")
            try:
                os.makedirs(self.logo_dir, exist_ok=True)
                self.logger.info(f"Created NCAA Baseball logos directory: {self.logo_dir}")
            except Exception as e:
                self.logger.error(f"Failed to create NCAA Baseball logos directory: {e}")
        
        # Set up session with retry logic
        self.session = requests.Session()
        retry_strategy = Retry(
            total=5,
            backoff_factor=1,
            status_forcelist=[429, 500, 502, 503, 504],
        )
        adapter = HTTPAdapter(max_retries=retry_strategy)
        self.session.mount("http://", adapter)
        self.session.mount("https://", adapter)
        
        # Load fonts
        self.fonts = self._load_fonts()
        
        # Initialize game tracking
        self.live_games = []
        self.current_game = None
        self.current_game_index = 0
        self.last_update = 0
        self.update_interval = self.ncaa_baseball_config.get('live_update_interval', 20)
        self.no_data_interval = 300
        self.last_game_switch = 0
        self.game_display_duration = self.ncaa_baseball_config.get('live_game_duration', 30)
        self.last_display_update = 0
        self.last_log_time = 0
        self.log_interval = 300
        self.last_count_log_time = 0
        self.count_log_interval = 5
        self.test_mode = self.ncaa_baseball_config.get('test_mode', False)
    
    def _load_fonts(self) -> Dict[str, ImageFont.FreeTypeFont]:
        """Load fonts for display."""
        fonts = {}
        try:
            # Load main font
            font_path = os.path.join('assets', 'fonts', '5by7.regular.ttf')
            if os.path.exists(font_path):
                fonts['main'] = ImageFont.truetype(font_path, 8)
            else:
                fonts['main'] = ImageFont.load_default()
            
            # Load small font
            fonts['small'] = ImageFont.load_default()
            
            return fonts
        except Exception as e:
            self.logger.error(f"Error loading fonts: {e}")
            return {'main': ImageFont.load_default(), 'small': ImageFont.load_default()}
    
    def _get_team_logo(self, team_abbr: str) -> Optional[Image.Image]:
        """Get team logo for display."""
        try:
            logo_path = os.path.join(self.logo_dir, f"{team_abbr}.png")
            if os.path.exists(logo_path):
                return Image.open(logo_path)
            return None
        except Exception as e:
            self.logger.error(f"Error loading logo for {team_abbr}: {e}")
            return None
    
    def _draw_text_with_outline(self, draw, text, position, font, fill=(255, 255, 255), outline_color=(0, 0, 0)):
        """Draw text with outline for better visibility."""
        x, y = position
        for dx, dy in [(-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1)]:
            draw.text((x + dx, y + dy), text, font=font, fill=outline_color)
        draw.text((x, y), text, font=font, fill=fill)
    
    def _draw_base_indicators(self, draw: ImageDraw.Draw, bases_occupied: List[bool], center_x: int, y: int) -> None:
        """Draw base indicators for baseball."""
        base_size = 3
        base_spacing = 8
        
        # Draw bases in diamond formation
        base_positions = [
            (center_x, y - base_spacing),  # 1st base
            (center_x + base_spacing, y),  # 2nd base
            (center_x, y + base_spacing),  # 3rd base
            (center_x - base_spacing, y)   # Home plate
        ]
        
        for i, (pos, occupied) in enumerate(zip(base_positions, bases_occupied)):
            color = (255, 255, 0) if occupied else (128, 128, 128)
            draw.ellipse([pos[0] - base_size, pos[1] - base_size, 
                         pos[0] + base_size, pos[1] + base_size], fill=color)
    
    def _create_game_display(self, game_data: Dict[str, Any]) -> Image.Image:
        """Create display image for a game."""
        try:
            # Create image
            width = self.display_manager.matrix.width
            height = self.display_manager.matrix.height
            image = Image.new('RGB', (width, height), (0, 0, 0))
            draw = ImageDraw.Draw(image)
            
            # Get game details
            home_team = game_data.get('home_team', {})
            away_team = game_data.get('away_team', {})
            home_score = game_data.get('home_score', '0')
            away_score = game_data.get('away_score', '0')
            
            # Get baseball-specific details
            inning = game_data.get('inning', '')
            outs = game_data.get('outs', 0)
            bases = game_data.get('bases', '')
            strikes = game_data.get('strikes', 0)
            balls = game_data.get('balls', 0)
            
            # Draw team names and scores
            font = self.fonts['main']
            y_offset = 10
            
            # Away team
            away_text = f"{away_team.get('abbreviation', 'AWAY')} {away_score}"
            draw.text((5, y_offset), away_text, font=font, fill=(255, 255, 255))
            
            # Home team
            home_text = f"{home_team.get('abbreviation', 'HOME')} {home_score}"
            draw.text((5, y_offset + 15), home_text, font=font, fill=(255, 255, 255))
            
            # Baseball-specific details
            if inning:
                inning_text = f"Inning: {inning}"
                draw.text((5, y_offset + 30), inning_text, font=font, fill=(255, 255, 255))
            
            if outs is not None:
                outs_text = f"Outs: {outs}"
                draw.text((5, y_offset + 45), outs_text, font=font, fill=(255, 255, 255))
            
            if strikes is not None and balls is not None:
                count_text = f"Count: {balls}-{strikes}"
                draw.text((5, y_offset + 60), count_text, font=font, fill=(255, 255, 255))
            
            return image
            
        except Exception as e:
            self.logger.error(f"Error creating game display: {e}")
            return Image.new('RGB', (self.display_manager.matrix.width, self.display_manager.matrix.height), (0, 0, 0))
    
    def _fetch_ncaa_baseball_api_data(self, use_cache: bool = True) -> Dict[str, Any]:
        """Fetch NCAA Baseball data from ESPN API."""
        try:
            # This would implement the actual NCAA Baseball API fetching
            # For now, return empty data
            return {}
        except Exception as e:
            self.logger.error(f"Error fetching NCAA Baseball data: {e}")
            return {}
    
    def _is_baseball_game_live(self, game: Dict) -> bool:
        """Check if a baseball game is currently live."""
        return super()._is_baseball_game_live(game)
    
    def _get_baseball_game_status(self, game: Dict) -> str:
        """Get baseball-specific game status."""
        return super()._get_baseball_game_status(game)


class NCAABaseballLiveManager(BaseNCAABaseballManager, BaseballLive):
    """Manager for live NCAA Baseball games using new baseball architecture."""
    
    def __init__(self, config: Dict[str, Any], display_manager, cache_manager: CacheManager):
        super().__init__(config, display_manager, cache_manager)
        self.logger.info("NCAA Baseball Live Manager initialized with new baseball architecture")
    
    def get_duration(self) -> int:
        """Get display duration for live NCAA Baseball games."""
        return self.ncaa_baseball_config.get('live_game_duration', 30)
    
    def display(self, force_clear: bool = False) -> bool:
        """Display live NCAA Baseball games."""
        try:
            # Fetch live games using the new architecture
            live_games = self._fetch_immediate_games()
            
            if not live_games:
                self.logger.warning("No live NCAA Baseball games found")
                return False
            
            # Filter games based on criteria
            games_to_show = [game for game in live_games if self._should_show_baseball_game(game)]
            
            if not games_to_show:
                self.logger.debug("No NCAA Baseball games meet display criteria")
                return False
            
            # Display each game
            for game in games_to_show:
                self._display_single_game(game)
                time.sleep(2)
            
            return True
            
        except Exception as e:
            self.logger.error(f"Error displaying live NCAA Baseball games: {e}")
            return False
    
    def _display_single_game(self, game: Dict) -> None:
        """Display a single NCAA Baseball game."""
        try:
            # Get game details
            home_team = game.get('home_team_name', '')
            away_team = game.get('away_team_name', '')
            home_score = game.get('home_score', '0')
            away_score = game.get('away_score', '0')
            status = game.get('status_text', '')
            
            # Get baseball-specific display text
            baseball_text = self._get_baseball_display_text(game)
            
            # Create display text
            display_text = f"{away_team} {away_score} @ {home_team} {home_score}"
            if status:
                display_text += f" - {status}"
            if baseball_text:
                display_text += f" ({baseball_text})"
            
            # Display the text
            self.display_manager.display_text(display_text)
            
        except Exception as e:
            self.logger.error(f"Error displaying single NCAA Baseball game: {e}")


class NCAABaseballRecentManager(BaseNCAABaseballManager, BaseballRecent):
    """Manager for recent NCAA Baseball games using new baseball architecture."""
    
    def __init__(self, config: Dict[str, Any], display_manager, cache_manager: CacheManager):
        super().__init__(config, display_manager, cache_manager)
        self.logger.info("NCAA Baseball Recent Manager initialized with new baseball architecture")
    
    def get_duration(self) -> int:
        """Get display duration for recent NCAA Baseball games."""
        return self.ncaa_baseball_config.get('recent_game_duration', 20)
    
    def display(self, force_clear: bool = False) -> bool:
        """Display recent NCAA Baseball games."""
        try:
            # Fetch recent games using the new architecture
            recent_games = self._get_partial_schedule_data(datetime.now().year)
            
            if not recent_games:
                self.logger.warning("No recent NCAA Baseball games found")
                return False
            
            # Filter for recent games (last 24 hours)
            now = datetime.now(pytz.UTC)
            recent_games = [game for game in recent_games 
                           if game.get('is_final', False) and 
                           game.get('start_time_utc') and 
                           (now - game['start_time_utc']).total_seconds() < 86400]
            
            if not recent_games:
                self.logger.debug("No recent NCAA Baseball games in last 24 hours")
                return False
            
            # Display each game
            for game in recent_games:
                self._display_single_game(game)
                time.sleep(2)
            
            return True
            
        except Exception as e:
            self.logger.error(f"Error displaying recent NCAA Baseball games: {e}")
            return False


class NCAABaseballUpcomingManager(BaseNCAABaseballManager, BaseballUpcoming):
    """Manager for upcoming NCAA Baseball games using new baseball architecture."""
    
    def __init__(self, config: Dict[str, Any], display_manager, cache_manager: CacheManager):
        super().__init__(config, display_manager, cache_manager)
        self.logger.info("NCAA Baseball Upcoming Manager initialized with new baseball architecture")
    
    def get_duration(self) -> int:
        """Get display duration for upcoming NCAA Baseball games."""
        return self.ncaa_baseball_config.get('upcoming_game_duration', 15)
    
    def display(self, force_clear: bool = False) -> bool:
        """Display upcoming NCAA Baseball games."""
        try:
            # Fetch upcoming games using the new architecture
            upcoming_games = self._get_partial_schedule_data(datetime.now().year)
            
            if not upcoming_games:
                self.logger.warning("No upcoming NCAA Baseball games found")
                return False
            
            # Filter for upcoming games (next 7 days)
            now = datetime.now(pytz.UTC)
            upcoming_games = [game for game in upcoming_games 
                             if game.get('is_upcoming', False) and 
                             game.get('start_time_utc') and 
                             (game['start_time_utc'] - now).total_seconds() < 604800]
            
            if not upcoming_games:
                self.logger.debug("No upcoming NCAA Baseball games in next 7 days")
                return False
            
            # Display each game
            for game in upcoming_games:
                self._display_single_game(game)
                time.sleep(2)
            
            return True
            
        except Exception as e:
            self.logger.error(f"Error displaying upcoming NCAA Baseball games: {e}")
            return False
