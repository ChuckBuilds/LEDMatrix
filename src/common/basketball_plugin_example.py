"""
Example: Basketball Plugin using LEDMatrix Common Helpers

This example shows how to refactor the basketball plugin to use the
ledmatrix-common package for cleaner, more maintainable code.
"""

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from PIL import Image, ImageDraw

# Import common helpers
from src.common import (
    LogoHelper, TextHelper, APIHelper, DisplayHelper, 
    GameHelper, ConfigHelper
)
from src.plugin_system.base_plugin import BasePlugin


class BasketballPluginManager(BasePlugin):
    """
    Basketball scoreboard plugin using LEDMatrix Common helpers.
    
    This version is much cleaner and more maintainable than the original
    because it delegates common functionality to the shared helpers.
    """
    
    def __init__(
        self,
        plugin_id: str,
        config: Dict[str, Any],
        display_manager,
        cache_manager,
        plugin_manager
    ):
        """Initialize the basketball plugin with common helpers."""
        super().__init__(plugin_id, config, display_manager, cache_manager, plugin_manager)
        
        # Get display dimensions
        self.display_width = display_manager.matrix.width
        self.display_height = display_manager.matrix.height
        
        # Initialize common helpers
        self._init_helpers()
        
        # Load configuration
        self._load_config()
        
        # State tracking
        self.current_games = []
        self.current_game = None
        
        # Log initialization
        enabled_leagues = [k for k, v in self.league_configs.items() if v['enabled']]
        self.logger.info(f"Basketball plugin initialized with leagues: {enabled_leagues}")
    
    def _init_helpers(self):
        """Initialize all common helpers."""
        # Logo helper for team logos
        self.logo_helper = LogoHelper(
            display_width=self.display_width,
            display_height=self.display_height,
            logger=self.logger
        )
        
        # Text helper for rendering
        self.text_helper = TextHelper(logger=self.logger)
        self.fonts = self.text_helper.load_fonts()
        
        # API helper for ESPN data
        self.api_helper = APIHelper(
            cache_manager=self.cache_manager,
            logger=self.logger
        )
        
        # Display helper for layouts
        self.display_helper = DisplayHelper(
            display_width=self.display_width,
            display_height=self.display_height,
            logger=self.logger
        )
        
        # Game helper for data processing
        self.game_helper = GameHelper(
            timezone_str=self.config.get('timezone', 'UTC'),
            logger=self.logger
        )
        
        # Config helper for configuration management
        self.config_helper = ConfigHelper(logger=self.logger)
    
    def _load_config(self):
        """Load and validate configuration."""
        # Get basketball-specific config
        basketball_config = self.config_helper.get_sports_config(self.config, 'basketball')
        
        # Build league configurations
        self.league_configs = {
            'nba': {
                'enabled': basketball_config.get('nba_enabled', True),
                'url': 'https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard',
                'logo_dir': Path('assets/sports/nba_logos'),
                'favorite_teams': basketball_config.get('nba_favorite_teams', []),
                'display_modes': {
                    'nba_live': basketball_config.get('nba_display_modes_live', True),
                    'nba_recent': basketball_config.get('nba_display_modes_recent', True),
                    'nba_upcoming': basketball_config.get('nba_display_modes_upcoming', True),
                },
            },
            'wnba': {
                'enabled': basketball_config.get('wnba_enabled', False),
                'url': 'https://site.api.espn.com/apis/site/v2/sports/basketball/wnba/scoreboard',
                'logo_dir': Path('assets/sports/wnba_logos'),
                'favorite_teams': basketball_config.get('wnba_favorite_teams', []),
                'display_modes': {
                    'wnba_live': basketball_config.get('wnba_display_modes_live', True),
                    'wnba_recent': basketball_config.get('wnba_display_modes_recent', True),
                    'wnba_upcoming': basketball_config.get('wnba_display_modes_upcoming', True),
                },
            },
            'ncaam': {
                'enabled': basketball_config.get('ncaam_basketball_enabled', False),
                'url': 'https://site.api.espn.com/apis/site/v2/sports/basketball/mens-college-basketball/scoreboard',
                'logo_dir': Path('assets/sports/ncaa_logos'),
                'favorite_teams': basketball_config.get('ncaam_basketball_favorite_teams', []),
                'display_modes': {
                    'ncaam_basketball_live': basketball_config.get('ncaam_basketball_display_modes_live', True),
                    'ncaam_basketball_recent': basketball_config.get('ncaam_basketball_display_modes_recent', True),
                    'ncaam_basketball_upcoming': basketball_config.get('ncaam_basketball_display_modes_upcoming', True),
                },
            },
            'ncaaw': {
                'enabled': basketball_config.get('ncaaw_basketball_enabled', False),
                'url': 'https://site.api.espn.com/apis/site/v2/sports/basketball/womens-college-basketball/scoreboard',
                'logo_dir': Path('assets/sports/ncaa_logos'),
                'favorite_teams': basketball_config.get('ncaaw_basketball_favorite_teams', []),
                'display_modes': {
                    'ncaaw_basketball_live': basketball_config.get('ncaaw_basketball_display_modes_live', True),
                    'ncaaw_basketball_recent': basketball_config.get('ncaaw_basketball_display_modes_recent', True),
                    'ncaaw_basketball_upcoming': basketball_config.get('ncaaw_basketball_display_modes_upcoming', True),
                },
            },
        }
    
    def update(self) -> None:
        """Update game data for all enabled leagues."""
        try:
            all_games = []
            
            for league_key, league_config in self.league_configs.items():
                if not league_config['enabled']:
                    continue
                
                games = self._fetch_league_games(league_key, league_config)
                for game in games:
                    game['league_key'] = league_key
                    game['league_config'] = league_config
                all_games.extend(games)
            
            self.current_games = all_games
            self.logger.debug(f"Updated basketball data: {len(all_games)} total games")
            
        except Exception as e:
            self.logger.error(f"Error updating basketball data: {e}", exc_info=True)
    
    def _fetch_league_games(self, league_key: str, league_config: Dict) -> List[Dict]:
        """Fetch games for a specific league using API helper."""
        try:
            # Use API helper to fetch ESPN data with caching
            data = self.api_helper.fetch_espn_scoreboard(
                sport='basketball',
                league=league_key,
                cache_key=f"basketball_{league_key}",
                cache_ttl=300  # 5 minutes cache
            )
            
            if not data:
                return []
            
            # Use game helper to process events
            events = data.get('events', [])
            games = self.game_helper.process_games(events, sport='basketball')
            
            # Add logo paths to games
            for game in games:
                logo_dir = league_config['logo_dir']
                game['home_logo_path'] = logo_dir / f"{game['home_abbr']}.png"
                game['away_logo_path'] = logo_dir / f"{game['away_abbr']}.png"
            
            return games
            
        except Exception as e:
            self.logger.error(f"Error fetching {league_key} games: {e}", exc_info=True)
            return []
    
    def display(self, force_clear: bool = False, display_mode: str = None) -> None:
        """Display basketball games using display helper."""
        try:
            mode = display_mode or self._determine_display_mode()
            
            if not mode:
                self._display_no_games()
                return
            
            # Filter games for mode
            filtered_games = self._filter_games_for_mode(mode)
            
            if not filtered_games:
                self._display_no_games()
                return
            
            # Display first game
            self.current_game = filtered_games[0]
            self._draw_scorebug_layout(self.current_game, force_clear)
            
        except Exception as e:
            self.logger.error(f"Error displaying game: {e}", exc_info=True)
    
    def _determine_display_mode(self) -> Optional[str]:
        """Determine display mode based on available games."""
        # Priority: live > recent > upcoming
        for game in self.current_games:
            if game.get('is_live'):
                return f"{game['league_key']}_live"
        for game in self.current_games:
            if game.get('is_final'):
                return f"{game['league_key']}_recent"
        for game in self.current_games:
            if game.get('is_upcoming'):
                return f"{game['league_key']}_upcoming"
        return None
    
    def _filter_games_for_mode(self, mode: str) -> List[Dict]:
        """Filter games based on display mode."""
        filtered = []
        
        for game in self.current_games:
            league_config = game.get('league_config', {})
            display_modes = league_config.get('display_modes', {})
            
            if mode in display_modes and display_modes[mode]:
                if 'live' in mode and game.get('is_live'):
                    filtered.append(game)
                elif 'recent' in mode and game.get('is_final'):
                    filtered.append(game)
                elif 'upcoming' in mode and game.get('is_upcoming'):
                    filtered.append(game)
        
        return filtered[:5]
    
    def _draw_scorebug_layout(self, game: Dict, force_clear: bool = False) -> None:
        """Draw the basketball scorebug layout using display helper."""
        try:
            # Load logos using logo helper
            home_logo = self.logo_helper.load_logo(
                game['home_abbr'], 
                game['home_logo_path']
            )
            away_logo = self.logo_helper.load_logo(
                game['away_abbr'], 
                game['away_logo_path']
            )
            
            if not home_logo or not away_logo:
                self.logger.error("Failed to load logos")
                self._display_error("Logo Error")
                return
            
            # Use display helper to create scorebug layout
            final_img = self.display_helper.draw_scorebug_layout(
                game_data=game,
                fonts=self.fonts,
                home_logo=home_logo,
                away_logo=away_logo
            )
            
            # Display the image
            self.display_manager.image.paste(final_img, (0, 0))
            self.display_manager.update_display()
            
        except Exception as e:
            self.logger.error(f"Error drawing scorebug: {e}", exc_info=True)
    
    def _display_no_games(self) -> None:
        """Display 'no games' message using display helper."""
        try:
            img = self.display_helper.draw_no_data_message("No Games")
            self.display_manager.image = img.copy()
            self.display_manager.update_display()
        except Exception as e:
            self.logger.error(f"Error displaying no games: {e}", exc_info=True)
    
    def _display_error(self, message: str) -> None:
        """Display error message using display helper."""
        try:
            img = self.display_helper.draw_error_message(message)
            self.display_manager.image = img.copy()
            self.display_manager.update_display()
        except Exception as e:
            self.logger.error(f"Error displaying error message: {e}", exc_info=True)
    
    def get_display_duration(self) -> float:
        """Get display duration."""
        return self.config.get('display_duration', 15)
    
    def cleanup(self) -> None:
        """Cleanup resources."""
        self.current_games = []
        self.logger.info("Basketball plugin cleaned up")


# Example usage and benefits:
"""
Benefits of using LEDMatrix Common helpers:

1. **Cleaner Code**: The plugin is much shorter and more readable
2. **Reusable Components**: Common functionality is shared across plugins
3. **Better Testing**: Each helper can be tested independently
4. **Easier Maintenance**: Bug fixes in helpers benefit all plugins
5. **Consistent Behavior**: All plugins use the same underlying logic
6. **Reduced Dependencies**: Plugins don't need to import LEDMatrix core
7. **Better Error Handling**: Centralized error handling in helpers
8. **Configuration Management**: Consistent config handling across plugins

The original basketball plugin was 326 lines. This version is much cleaner
and delegates most functionality to the common helpers, making it easier to
maintain and extend.
"""
