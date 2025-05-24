import time
import logging
import sys
from typing import Dict, Any, List

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s.%(msecs)03d - %(levelname)s:%(name)s:%(message)s',
    datefmt='%H:%M:%S',
    stream=sys.stdout
)

from src.clock import Clock
from src.weather_manager import WeatherManager
from src.display_manager import DisplayManager
from src.config_manager import ConfigManager
from src.stock_manager import StockManager
from src.stock_news_manager import StockNewsManager
from src.nhl_managers import NHLLiveManager, NHLRecentManager, NHLUpcomingManager
from src.nba_managers import NBALiveManager, NBARecentManager, NBAUpcomingManager
from src.mlb_manager import MLBLiveManager, MLBRecentManager, MLBUpcomingManager
from src.soccer_managers import SoccerLiveManager, SoccerRecentManager, SoccerUpcomingManager
from src.nfl_managers import NFLLiveManager, NFLRecentManager, NFLUpcomingManager
from src.ncaa_fb_managers import NCAAFBLiveManager, NCAAFBRecentManager, NCAAFBUpcomingManager
from src.ncaa_mens_basketball_managers import NCAAMensBasketballLiveManager, NCAAMensBasketballRecentManager, NCAAMensBasketballUpcomingManager
from src.ncaa_mens_baseball_managers import NCAAMensBaseballLiveManager, NCAAMensBaseballRecentManager, NCAAMensBaseballUpcomingManager
from src.youtube_display import YouTubeDisplay
from src.calendar_manager import CalendarManager
from src.text_display import TextDisplay
from src.music_manager import MusicManager

# Get logger without configuring
logger = logging.getLogger(__name__)

class DisplayController:
    def __init__(self):
        start_time = time.time()
        logger.info("Starting DisplayController initialization")
        
        self.config_manager = ConfigManager()
        self.config = self.config_manager.load_config()
        logger.info("Config loaded in %.3f seconds", time.time() - start_time)
        
        config_time = time.time()
        self.display_manager = DisplayManager(self.config)
        logger.info("DisplayManager initialized in %.3f seconds", time.time() - config_time)
        
        # Initialize display modes
        init_time = time.time()
        self.clock = Clock(self.display_manager) if self.config.get('clock', {}).get('enabled', True) else None
        self.weather = WeatherManager(self.config, self.display_manager) if self.config.get('weather', {}).get('enabled', False) else None
        self.stocks = StockManager(self.config, self.display_manager) if self.config.get('stocks', {}).get('enabled', False) else None
        self.news = StockNewsManager(self.config, self.display_manager) if self.config.get('stock_news', {}).get('enabled', False) else None
        self.calendar = CalendarManager(self.display_manager, self.config) if self.config.get('calendar', {}).get('enabled', False) else None
        self.youtube = YouTubeDisplay(self.display_manager, self.config) if self.config.get('youtube', {}).get('enabled', False) else None
        self.text_display = TextDisplay(self.display_manager, self.config) if self.config.get('text_display', {}).get('enabled', False) else None
        logger.info(f"Calendar Manager initialized: {'Object' if self.calendar else 'None'}")
        logger.info(f"Text Display initialized: {'Object' if self.text_display else 'None'}")
        logger.info("Display modes initialized in %.3f seconds", time.time() - init_time)
        
        # Initialize Music Manager
        music_init_time = time.time()
        self.music_manager = None
        
        if hasattr(self, 'config'):
            music_config_main = self.config.get('music', {})
            if music_config_main.get('enabled', False):
                try:
                    # Pass display_manager and config. The callback is now optional for MusicManager.
                    # DisplayController might not need a specific music update callback anymore if MusicManager handles all display.
                    self.music_manager = MusicManager(display_manager=self.display_manager, 
                                                      config=self.config, 
                                                      update_callback=self._handle_music_update,
                                                      display_controller_obj=self)
                    if self.music_manager.enabled: 
                        logger.info("MusicManager initialized successfully.")
                        self.music_manager.start_polling()
                    else:
                        logger.info("MusicManager initialized but is internally disabled or failed to load its own config.")
                        self.music_manager = None 
                except Exception as e:
                    logger.error(f"Failed to initialize MusicManager: {e}", exc_info=True)
                    self.music_manager = None
            else:
                logger.info("Music module is disabled in main configuration (config.json).")
        else:
            logger.error("Config not loaded before MusicManager initialization attempt.")
        logger.info("MusicManager initialized in %.3f seconds", time.time() - music_init_time)
        
        # Initialize NHL managers if enabled
        nhl_time = time.time()
        nhl_enabled = self.config.get('nhl_scoreboard', {}).get('enabled', False)
        nhl_display_modes = self.config.get('nhl_scoreboard', {}).get('display_modes', {})
        
        if nhl_enabled:
            self.nhl_live = NHLLiveManager(self.config, self.display_manager) if nhl_display_modes.get('nhl_live', True) else None
            self.nhl_recent = NHLRecentManager(self.config, self.display_manager) if nhl_display_modes.get('nhl_recent', True) else None
            self.nhl_upcoming = NHLUpcomingManager(self.config, self.display_manager) if nhl_display_modes.get('nhl_upcoming', True) else None
        else:
            self.nhl_live = None
            self.nhl_recent = None
            self.nhl_upcoming = None
        logger.info("NHL managers initialized in %.3f seconds", time.time() - nhl_time)
            
        # Initialize NBA managers if enabled
        nba_time = time.time()
        nba_enabled = self.config.get('nba_scoreboard', {}).get('enabled', False)
        nba_display_modes = self.config.get('nba_scoreboard', {}).get('display_modes', {})
        
        if nba_enabled:
            self.nba_live = NBALiveManager(self.config, self.display_manager) if nba_display_modes.get('nba_live', True) else None
            self.nba_recent = NBARecentManager(self.config, self.display_manager) if nba_display_modes.get('nba_recent', True) else None
            self.nba_upcoming = NBAUpcomingManager(self.config, self.display_manager) if nba_display_modes.get('nba_upcoming', True) else None
        else:
            self.nba_live = None
            self.nba_recent = None
            self.nba_upcoming = None
        logger.info("NBA managers initialized in %.3f seconds", time.time() - nba_time)

        # Initialize MLB managers if enabled
        mlb_time = time.time()
        mlb_enabled = self.config.get('mlb', {}).get('enabled', False)
        mlb_display_modes = self.config.get('mlb', {}).get('display_modes', {})
        
        if mlb_enabled:
            self.mlb_live = MLBLiveManager(self.config, self.display_manager) if mlb_display_modes.get('mlb_live', True) else None
            self.mlb_recent = MLBRecentManager(self.config, self.display_manager) if mlb_display_modes.get('mlb_recent', True) else None
            self.mlb_upcoming = MLBUpcomingManager(self.config, self.display_manager) if mlb_display_modes.get('mlb_upcoming', True) else None
        else:
            self.mlb_live = None
            self.mlb_recent = None
            self.mlb_upcoming = None
        logger.info("MLB managers initialized in %.3f seconds", time.time() - mlb_time)
            
        # Initialize Soccer managers if enabled
        soccer_time = time.time()
        soccer_enabled = self.config.get('soccer_scoreboard', {}).get('enabled', False)
        soccer_display_modes = self.config.get('soccer_scoreboard', {}).get('display_modes', {})
        
        if soccer_enabled:
            self.soccer_live = SoccerLiveManager(self.config, self.display_manager) if soccer_display_modes.get('soccer_live', True) else None
            self.soccer_recent = SoccerRecentManager(self.config, self.display_manager) if soccer_display_modes.get('soccer_recent', True) else None
            self.soccer_upcoming = SoccerUpcomingManager(self.config, self.display_manager) if soccer_display_modes.get('soccer_upcoming', True) else None
        else:
            self.soccer_live = None
            self.soccer_recent = None
            self.soccer_upcoming = None
        logger.info("Soccer managers initialized in %.3f seconds", time.time() - soccer_time)
            
        # Initialize NFL managers if enabled
        nfl_time = time.time()
        nfl_enabled = self.config.get('nfl_scoreboard', {}).get('enabled', False)
        nfl_display_modes = self.config.get('nfl_scoreboard', {}).get('display_modes', {})
        
        if nfl_enabled:
            self.nfl_live = NFLLiveManager(self.config, self.display_manager) if nfl_display_modes.get('nfl_live', True) else None
            self.nfl_recent = NFLRecentManager(self.config, self.display_manager) if nfl_display_modes.get('nfl_recent', True) else None
            self.nfl_upcoming = NFLUpcomingManager(self.config, self.display_manager) if nfl_display_modes.get('nfl_upcoming', True) else None
        else:
            self.nfl_live = None
            self.nfl_recent = None
            self.nfl_upcoming = None
        logger.info("NFL managers initialized in %.3f seconds", time.time() - nfl_time)
        
        # Initialize NCAA FB managers if enabled
        ncaa_fb_time = time.time()
        ncaa_fb_enabled = self.config.get('ncaa_fb_scoreboard', {}).get('enabled', False)
        ncaa_fb_display_modes = self.config.get('ncaa_fb_scoreboard', {}).get('display_modes', {})
        
        if ncaa_fb_enabled:
            self.ncaa_fb_live = NCAAFBLiveManager(self.config, self.display_manager) if ncaa_fb_display_modes.get('ncaa_fb_live', True) else None
            self.ncaa_fb_recent = NCAAFBRecentManager(self.config, self.display_manager) if ncaa_fb_display_modes.get('ncaa_fb_recent', True) else None
            self.ncaa_fb_upcoming = NCAAFBUpcomingManager(self.config, self.display_manager) if ncaa_fb_display_modes.get('ncaa_fb_upcoming', True) else None
        else:
            self.ncaa_fb_live = None
            self.ncaa_fb_recent = None
            self.ncaa_fb_upcoming = None
        logger.info("NCAA FB managers initialized in %.3f seconds", time.time() - ncaa_fb_time)
        
        # Initialize NCAA Mens Basketball managers if enabled
        ncaa_mens_basketball_time = time.time()
        ncaa_mens_basketball_enabled = self.config.get('ncaa_mens_basketball_scoreboard', {}).get('enabled', False)
        ncaa_mens_basketball_display_modes = self.config.get('ncaa_mens_basketball_scoreboard', {}).get('display_modes', {})
        
        if ncaa_mens_basketball_enabled:
            self.ncaa_mens_basketball_live = NCAAMensBasketballLiveManager(self.config, self.display_manager) if ncaa_mens_basketball_display_modes.get('ncaa_mens_basketball_live', True) else None
            self.ncaa_mens_basketball_recent = NCAAMensBasketballRecentManager(self.config, self.display_manager) if ncaa_mens_basketball_display_modes.get('ncaa_mens_basketball_recent', True) else None
            self.ncaa_mens_basketball_upcoming = NCAAMensBasketballUpcomingManager(self.config, self.display_manager) if ncaa_mens_basketball_display_modes.get('ncaa_mens_basketball_upcoming', True) else None
        else:
            self.ncaa_mens_basketball_live = None
            self.ncaa_mens_basketball_recent = None
            self.ncaa_mens_basketball_upcoming = None
        logger.info("NCAA Mens Basketball managers initialized in %.3f seconds", time.time() - ncaa_mens_basketball_time)

        # Initialize NCAA Mens Baseball managers if enabled
        ncaa_mens_baseball_time = time.time()
        ncaa_mens_baseball_enabled = self.config.get('ncaa_mens_baseball_scoreboard', {}).get('enabled', False)
        ncaa_mens_baseball_display_modes = self.config.get('ncaa_mens_baseball_scoreboard', {}).get('display_modes', {})

        if ncaa_mens_baseball_enabled:
            self.ncaa_mens_baseball_live = NCAAMensBaseballLiveManager(self.config, self.display_manager) if ncaa_mens_baseball_display_modes.get('ncaa_mens_baseball_live', True) else None
            self.ncaa_mens_baseball_recent = NCAAMensBaseballRecentManager(self.config, self.display_manager) if ncaa_mens_baseball_display_modes.get('ncaa_mens_baseball_recent', True) else None
            self.ncaa_mens_baseball_upcoming = NCAAMensBaseballUpcomingManager(self.config, self.display_manager) if ncaa_mens_baseball_display_modes.get('ncaa_mens_baseball_upcoming', True) else None
        else:
            self.ncaa_mens_baseball_live = None
            self.ncaa_mens_baseball_recent = None
            self.ncaa_mens_baseball_upcoming = None
        logger.info("NCAA Mens Baseball managers initialized in %.3f seconds", time.time() - ncaa_mens_baseball_time)
        
        # Track MLB rotation state
        self.mlb_current_team_index = 0
        self.mlb_showing_recent = True
        self.mlb_favorite_teams = self.config.get('mlb', {}).get('favorite_teams', [])
        self.in_mlb_rotation = False
        
        # List of available display modes (adjust order as desired)
        self.available_modes = []
        if self.clock: self.available_modes.append('clock')
        if self.weather: self.available_modes.extend(['weather_current', 'weather_hourly', 'weather_daily'])
        if self.stocks: self.available_modes.append('stocks')
        if self.news: self.available_modes.append('stock_news')
        if self.calendar: self.available_modes.append('calendar')
        if self.youtube: self.available_modes.append('youtube')
        if self.text_display: self.available_modes.append('text_display')
        
        # Add Music display mode if enabled
        if self.music_manager: # Will be non-None only if successfully initialized and enabled
            self.available_modes.append('music')
        
        # Add NHL display modes if enabled
        if nhl_enabled:
            if self.nhl_recent: self.available_modes.append('nhl_recent')
            if self.nhl_upcoming: self.available_modes.append('nhl_upcoming')
            # nhl_live is handled separately when live games are available
        
        # Add NBA display modes if enabled
        if nba_enabled:
            if self.nba_recent: self.available_modes.append('nba_recent')
            if self.nba_upcoming: self.available_modes.append('nba_upcoming')
            # nba_live is handled separately when live games are available
            
        # Add MLB display modes if enabled
        if mlb_enabled:
            if self.mlb_recent: self.available_modes.append('mlb_recent') # Use recent if mode enabled
            if self.mlb_upcoming: self.available_modes.append('mlb_upcoming') # Use upcoming if mode enabled
            # mlb_live is handled separately when live games are available

        # Add Soccer display modes if enabled
        if soccer_enabled:
            if self.soccer_recent: self.available_modes.append('soccer_recent')
            if self.soccer_upcoming: self.available_modes.append('soccer_upcoming')
            # soccer_live is handled separately when live games are available
        
        # Add NFL display modes if enabled
        if nfl_enabled:
            if self.nfl_recent: self.available_modes.append('nfl_recent')
            if self.nfl_upcoming: self.available_modes.append('nfl_upcoming')
            # nfl_live is handled separately
        
        # Add NCAA FB display modes if enabled
        if ncaa_fb_enabled:
            if self.ncaa_fb_recent: self.available_modes.append('ncaa_fb_recent')
            if self.ncaa_fb_upcoming: self.available_modes.append('ncaa_fb_upcoming')
            # ncaa_fb_live is handled separately
        
        # Add NCAA Mens Basketball display modes if enabled
        if ncaa_mens_basketball_enabled:
            if self.ncaa_mens_basketball_recent: self.available_modes.append('ncaa_mens_basketball_recent')
            if self.ncaa_mens_basketball_upcoming: self.available_modes.append('ncaa_mens_basketball_upcoming')
            # ncaa_mens_basketball_live is handled separately

        # Add NCAA Mens Baseball display modes if enabled
        if ncaa_mens_baseball_enabled:
            if self.ncaa_mens_baseball_recent: self.available_modes.append('ncaa_mens_baseball_recent')
            if self.ncaa_mens_baseball_upcoming: self.available_modes.append('ncaa_mens_baseball_upcoming')
            # ncaa_mens_baseball_live is handled separately
        
        # Set initial display to first available mode (clock)
        self.current_mode_index = 0
        self.current_display_mode = self.available_modes[0] if self.available_modes else 'none'
        self.last_switch = time.time()
        self.force_clear = True
        self.update_interval = 0.01  # Reduced from 0.1 to 0.01 for smoother scrolling
        
        # Track team-based rotation states (Add Soccer)
        self.nhl_current_team_index = 0
        self.nhl_showing_recent = True
        self.nhl_favorite_teams = self.config.get('nhl_scoreboard', {}).get('favorite_teams', [])
        self.in_nhl_rotation = False
        
        self.nba_current_team_index = 0
        self.nba_showing_recent = True
        self.nba_favorite_teams = self.config.get('nba_scoreboard', {}).get('favorite_teams', [])
        self.in_nba_rotation = False
        
        self.soccer_current_team_index = 0 # Soccer rotation state
        self.soccer_showing_recent = True
        self.soccer_favorite_teams = self.config.get('soccer_scoreboard', {}).get('favorite_teams', [])
        self.in_soccer_rotation = False
        
        # Add NFL rotation state
        self.nfl_current_team_index = 0 
        self.nfl_showing_recent = True
        self.nfl_favorite_teams = self.config.get('nfl_scoreboard', {}).get('favorite_teams', [])
        self.in_nfl_rotation = False
        
        # Add NCAA FB rotation state
        self.ncaa_fb_current_team_index = 0 
        self.ncaa_fb_showing_recent = True
        self.ncaa_fb_favorite_teams = self.config.get('ncaa_fb_scoreboard', {}).get('favorite_teams', [])
        self.in_ncaa_fb_rotation = False
        
        # Add NCAA Mens Basketball rotation state
        self.ncaa_mens_basketball_current_team_index = 0
        self.ncaa_mens_basketball_showing_recent = True # Start with recent games
        self.ncaa_mens_basketball_favorite_teams = self.config.get('ncaa_mens_basketball_scoreboard', {}).get('favorite_teams', [])
        self.in_ncaa_mens_basketball_rotation = False

        # Add NCAA Mens Baseball rotation state
        self.ncaa_mens_baseball_current_team_index = 0
        self.ncaa_mens_baseball_showing_recent = True # Start with recent games
        self.ncaa_mens_baseball_favorite_teams = self.config.get('ncaa_mens_baseball_scoreboard', {}).get('favorite_teams', [])
        self.in_ncaa_mens_baseball_rotation = False
        
        # Update display durations to include all modes
        self.display_durations = self.config['display'].get('display_durations', {})
        # Add defaults for soccer if missing
        default_durations = {
            'clock': 15,
            'weather_current': 15,
            'weather_hourly': 15,
            'weather_daily': 15,
            'stocks': 45,
            'stock_news': 30,
            'calendar': 30,
            'youtube': 30,
            'nhl_live': 30,
            'nhl_recent': 20,
            'nhl_upcoming': 20,
            'nba_live': 30,
            'nba_recent': 20,
            'nba_upcoming': 20,
            'mlb_live': 30,
            'mlb_recent': 20,
            'mlb_upcoming': 20,
            'soccer_live': 30, # Soccer durations
            'soccer_recent': 20,
            'soccer_upcoming': 20,
            'nfl_live': 30, # Added NFL durations
            'nfl_recent': 30,
            'nfl_upcoming': 30,
            'ncaa_fb_live': 30, # Added NCAA FB durations
            'ncaa_fb_recent': 15,
            'ncaa_fb_upcoming': 15,
            'music': 20 # Default duration for music, will be overridden by config if present
        }
        # Merge loaded durations with defaults
        for key, value in default_durations.items():
            if key not in self.display_durations:
                 self.display_durations[key] = value
        
        # Log favorite teams only if the respective sport is enabled
        if nhl_enabled:
            logger.info(f"NHL Favorite teams: {self.nhl_favorite_teams}")
        if nba_enabled:
            logger.info(f"NBA Favorite teams: {self.nba_favorite_teams}")
        if mlb_enabled:
            logger.info(f"MLB Favorite teams: {self.mlb_favorite_teams}")
        if soccer_enabled: # Check if soccer is enabled
            logger.info(f"Soccer Favorite teams: {self.soccer_favorite_teams}")
        if nfl_enabled: # Check if NFL is enabled
            logger.info(f"NFL Favorite teams: {self.nfl_favorite_teams}")
        if ncaa_fb_enabled: # Check if NCAA FB is enabled
            logger.info(f"NCAA FB Favorite teams: {self.ncaa_fb_favorite_teams}")

        logger.info(f"Available display modes: {self.available_modes}")
        logger.info(f"Initial display mode: {self.current_display_mode}")
        logger.info("DisplayController initialized with display_manager: %s", id(self.display_manager))

    def is_display_active(self, display_name: str) -> bool:
        """Checks if the given display_name is the currently active display mode."""
        return self.current_display_mode == display_name

    def _handle_music_update(self, track_info: Dict[str, Any]):
        """Callback for when music track info changes. (Simplified)"""
        # MusicManager now handles its own display state (album art, etc.)
        # This callback might still be useful if DisplayController needs to react to music changes
        # for reasons other than directly re-drawing the music screen (e.g., logging, global state).
        # For now, we'll keep it simple. If the music screen is active, it will redraw on its own.
        if track_info:
            logger.debug(f"DisplayController received music update (via callback): Title - {track_info.get('title')}, Playing - {track_info.get('is_playing')}")
        else:
            logger.debug("DisplayController received music update (via callback): Track is None or not playing.")

        # If the current display mode is music, the MusicManager's display method will be called
        # in the main loop and will use its own updated internal state. No explicit action needed here
        # to force a redraw of the music screen itself, unless DisplayController wants to switch TO music mode.
        # Example: if self.current_display_mode == 'music': self.force_clear = True (but MusicManager.display handles this)

    def get_current_duration(self) -> int:
        """Get the duration for the current display mode."""
        mode_key = self.current_display_mode
        # Simplify weather key handling
        if mode_key.startswith('weather_'):
            return self.display_durations.get(mode_key, 15)
            # duration_key = mode_key.split('_', 1)[1]
            # if duration_key == 'current': duration_key = 'weather_current' # Keep specific keys
            # elif duration_key == 'hourly': duration_key = 'weather_hourly'
            # elif duration_key == 'daily': duration_key = 'weather_daily'
            # else: duration_key = 'weather_current' # Default to current
            # return self.display_durations.get(duration_key, 15)
        
        return self.display_durations.get(mode_key, 15)

    def _update_modules(self):
        """Call update methods on active managers."""
        if self.weather: self.weather.get_weather()
        if self.stocks: self.stocks.update_stock_data()
        if self.news: self.news.update_news_data()
        if self.calendar: self.calendar.update(time.time())
        if self.youtube: self.youtube.update()
        if self.text_display: self.text_display.update()
        
        # Update NHL managers
        if self.nhl_live: self.nhl_live.update()
        if self.nhl_recent: self.nhl_recent.update()
        if self.nhl_upcoming: self.nhl_upcoming.update()
        
        # Update NBA managers
        if self.nba_live: self.nba_live.update()
        if self.nba_recent: self.nba_recent.update()
        if self.nba_upcoming: self.nba_upcoming.update()
        
        # Update MLB managers
        if self.mlb_live: self.mlb_live.update()
        if self.mlb_recent: self.mlb_recent.update()
        if self.mlb_upcoming: self.mlb_upcoming.update()
        
        # Update Soccer managers
        if self.soccer_live: self.soccer_live.update()
        if self.soccer_recent: self.soccer_recent.update()
        if self.soccer_upcoming: self.soccer_upcoming.update()

        # Update NFL managers
        if self.nfl_live: self.nfl_live.update()
        if self.nfl_recent: self.nfl_recent.update()
        if self.nfl_upcoming: self.nfl_upcoming.update()

        # Update NCAA FB managers
        if self.ncaa_fb_live: self.ncaa_fb_live.update()
        if self.ncaa_fb_recent: self.ncaa_fb_recent.update()
        if self.ncaa_fb_upcoming: self.ncaa_fb_upcoming.update()

        # Update NCAA Mens Basketball managers
        if self.ncaa_mens_basketball_live: self.ncaa_mens_basketball_live.update()
        if self.ncaa_mens_basketball_recent: self.ncaa_mens_basketball_recent.update()
        if self.ncaa_mens_basketball_upcoming: self.ncaa_mens_basketball_upcoming.update()

        # Update NCAA Mens Baseball managers
        if self.ncaa_mens_baseball_live: self.ncaa_mens_baseball_live.update()
        if self.ncaa_mens_baseball_recent: self.ncaa_mens_baseball_recent.update()
        if self.ncaa_mens_baseball_upcoming: self.ncaa_mens_baseball_upcoming.update()

        # Update YouTube manager
        if self.youtube: self.youtube.update()

    def _check_live_games(self) -> tuple[bool, str]:
        """
        Check if there are any live games available.
        Returns:
            tuple[bool, str]: (has_live_games, sport_type)
            sport_type will be 'nhl', 'nba', 'mlb', 'soccer' or None
        """
        # Prioritize sports (e.g., Soccer > NHL > NBA > MLB)
        if self.soccer_live and self.soccer_live.live_games:
            return True, 'soccer'
        
        if self.nfl_live and self.nfl_live.live_games:
            logger.debug("NFL live games available")
            return True, 'nfl'
            
        if self.nhl_live and self.nhl_live.live_games:
            return True, 'nhl'
            
        if self.nba_live and self.nba_live.live_games:
            return True, 'nba'
            
        if self.mlb_live and self.mlb_live.live_games:
            return True, 'mlb'
            
        if 'ncaa_fb_scoreboard' in self.config and self.config['ncaa_fb_scoreboard'].get('enabled', False):
            if self.ncaa_fb_live and self.ncaa_fb_live.live_games:
                logger.debug("NCAA FB live games available")
                return True, 'ncaa_fb'

        if 'ncaa_mens_basketball_scoreboard' in self.config and self.config['ncaa_mens_basketball_scoreboard'].get('enabled', False):
            if self.ncaa_mens_basketball_live and self.ncaa_mens_basketball_live.live_games:
                logger.debug("NCAA Mens Basketball live games available")
                return True, 'ncaa_mens_basketball'

        if 'ncaa_mens_baseball_scoreboard' in self.config and self.config['ncaa_mens_baseball_scoreboard'].get('enabled', False):
            if self.ncaa_mens_baseball_live and self.ncaa_mens_baseball_live.live_games:
                logger.debug("NCAA Mens Baseball live games available")
                return True, 'ncaa_mens_baseball'
            
        return False, None

    def _get_team_games(self, team: str, sport: str = 'nhl', is_recent: bool = True) -> bool:
        """
        Get games for a specific team and update the current game.
        Args:
            team: Team abbreviation
            sport: 'nhl', 'nba', 'mlb', or 'soccer'
            is_recent: Whether to look for recent or upcoming games
        Returns:
            bool: True if games were found and set
        """
        manager_recent = None
        manager_upcoming = None
        games_list_attr = 'games_list' # Default for NHL/NBA
        abbr_key_home = 'home_abbr'
        abbr_key_away = 'away_abbr'

        if sport == 'nhl':
            manager_recent = self.nhl_recent
            manager_upcoming = self.nhl_upcoming
        elif sport == 'nba':
            manager_recent = self.nba_recent
            manager_upcoming = self.nba_upcoming
        elif sport == 'mlb':
            manager_recent = self.mlb_recent
            manager_upcoming = self.mlb_upcoming
            games_list_attr = 'recent_games' if is_recent else 'upcoming_games'
            abbr_key_home = 'home_team' # MLB uses different keys
            abbr_key_away = 'away_team'
        elif sport == 'soccer':
            manager_recent = self.soccer_recent
            manager_upcoming = self.soccer_upcoming
            games_list_attr = 'games_list' if is_recent else 'upcoming_games' # Soccer uses games_list/upcoming_games
        elif sport == 'nfl':
            manager_recent = self.nfl_recent
            manager_upcoming = self.nfl_upcoming
        elif sport == 'ncaa_fb': # Add NCAA FB case
            manager_recent = self.ncaa_fb_recent
            manager_upcoming = self.ncaa_fb_upcoming
        else:
            logger.warning(f"Unsupported sport '{sport}' for team game check")
            return False

        manager = manager_recent if is_recent else manager_upcoming

        if manager and hasattr(manager, games_list_attr):
            game_list = getattr(manager, games_list_attr, [])
            for game in game_list:
                # Need to handle potential missing keys gracefully
                home_team_abbr = game.get(abbr_key_home)
                away_team_abbr = game.get(abbr_key_away)
                if home_team_abbr == team or away_team_abbr == team:
                    manager.current_game = game
                    return True
        return False


    def _has_team_games(self, sport: str = 'nhl') -> bool:
        """Check if there are any games for favorite teams."""
        favorite_teams = []
        manager_recent = None
        manager_upcoming = None
        
        if sport == 'nhl':
            favorite_teams = self.nhl_favorite_teams
            manager_recent = self.nhl_recent
            manager_upcoming = self.nhl_upcoming
        elif sport == 'nba':
            favorite_teams = self.nba_favorite_teams
            manager_recent = self.nba_recent
            manager_upcoming = self.nba_upcoming
        elif sport == 'mlb':
            favorite_teams = self.mlb_favorite_teams
            manager_recent = self.mlb_recent
            manager_upcoming = self.mlb_upcoming
        elif sport == 'soccer':
            favorite_teams = self.soccer_favorite_teams
            manager_recent = self.soccer_recent
            manager_upcoming = self.soccer_upcoming
        elif sport == 'nfl':
            favorite_teams = self.nfl_favorite_teams
            manager_recent = self.nfl_recent
            manager_upcoming = self.nfl_upcoming
        elif sport == 'ncaa_fb': # Add NCAA FB case
            favorite_teams = self.ncaa_fb_favorite_teams
            manager_recent = self.ncaa_fb_recent
            manager_upcoming = self.ncaa_fb_upcoming
            
        return bool(favorite_teams and (manager_recent or manager_upcoming))

    def _rotate_team_games(self, sport: str = 'nhl') -> None:
        """Rotate through games for favorite teams. (No longer used directly in loop)"""
        # This logic is now mostly handled within each manager's display/update
        # Keeping the structure in case direct rotation is needed later.
        if not self._has_team_games(sport):
            return

        if sport == 'nhl':
            if not self.nhl_favorite_teams: return
            current_team = self.nhl_favorite_teams[self.nhl_current_team_index]
            # ... (rest of NHL rotation logic - now less relevant)
        elif sport == 'nba':
             if not self.nba_favorite_teams: return
             current_team = self.nba_favorite_teams[self.nba_current_team_index]
             # ... (rest of NBA rotation logic)
        elif sport == 'mlb':
            if not self.mlb_favorite_teams: return
            current_team = self.mlb_favorite_teams[self.mlb_current_team_index]
            # ... (rest of MLB rotation logic)
        elif sport == 'soccer':
            if not self.soccer_favorite_teams: return
            current_team = self.soccer_favorite_teams[self.soccer_current_team_index]
            # Try to find games for current team (recent first)
            found_games = self._get_team_games(current_team, 'soccer', self.soccer_showing_recent)
            if not found_games:
                # Try opposite type (upcoming/recent)
                self.soccer_showing_recent = not self.soccer_showing_recent
                found_games = self._get_team_games(current_team, 'soccer', self.soccer_showing_recent)
            
            if not found_games:
                # Move to next team if no games found for current one
                self.soccer_current_team_index = (self.soccer_current_team_index + 1) % len(self.soccer_favorite_teams)
                self.soccer_showing_recent = True # Reset to recent for the new team
                # Maybe try finding game for the *new* team immediately? Optional.
        elif sport == 'nfl':
            if not self.nfl_favorite_teams: return
            current_team = self.nfl_favorite_teams[self.nfl_current_team_index]
            # Try to find games for current team (recent first)
            found_games = self._get_team_games(current_team, 'nfl', self.nfl_showing_recent)
            if not found_games:
                # Try opposite type (upcoming/recent)
                self.nfl_showing_recent = not self.nfl_showing_recent
                found_games = self._get_team_games(current_team, 'nfl', self.nfl_showing_recent)
            
            if not found_games:
                # Move to next team if no games found for current one
                self.nfl_current_team_index = (self.nfl_current_team_index + 1) % len(self.nfl_favorite_teams)
                self.nfl_showing_recent = True # Reset to recent for the new team
        elif sport == 'ncaa_fb': # Add NCAA FB case
            if not self.ncaa_fb_favorite_teams: return
            current_team = self.ncaa_fb_favorite_teams[self.ncaa_fb_current_team_index]
            # Try to find games for current team (recent first)
            found_games = self._get_team_games(current_team, 'ncaa_fb', self.ncaa_fb_showing_recent)
            if not found_games:
                # Try opposite type (upcoming/recent)
                self.ncaa_fb_showing_recent = not self.ncaa_fb_showing_recent
                found_games = self._get_team_games(current_team, 'ncaa_fb', self.ncaa_fb_showing_recent)
            
            if not found_games:
                # Move to next team if no games found for current one
                self.ncaa_fb_current_team_index = (self.ncaa_fb_current_team_index + 1) % len(self.ncaa_fb_favorite_teams)
                self.ncaa_fb_showing_recent = True # Reset to recent for the new team

    def run(self):
        """Run the display controller, switching between displays."""
        if not self.available_modes:
            logger.warning("No display modes are enabled. Exiting.")
            self.display_manager.cleanup()
            return
             
        try:
            while True:
                current_time = time.time()
                
                # Update data for all modules first
                self._update_modules()
                
                # Check for live games
                has_live_games, live_sport_type = self._check_live_games()
                is_currently_live = self.current_display_mode.endswith('_live')

                manager_to_display = None # Manager instance for the current frame

                # --- State Machine for Display Logic ---

                if is_currently_live:
                    # --- Currently in a Live Game Mode ---
                    if has_live_games:
                        # Still live games available, check timer for rotation or continuation
                        if current_time - self.last_switch >= self.get_current_duration():
                            # Timer expired for the current live game, decide next step
                            logger.debug(f"Timer expired for live mode {self.current_display_mode}. Checking for rotation.")
                            
                            active_live_sports = []
                            # Use the same priority order as _check_live_games
                            priority_order = ['soccer', 'nfl', 'nhl', 'nba', 'mlb', 'ncaa_fb'] 
                            for sport in priority_order:
                                live_attr = f"{sport}_live"
                                if hasattr(self, live_attr) and getattr(self, live_attr) and getattr(self, live_attr).live_games:
                                    active_live_sports.append(sport)
                            
                            logger.debug(f"Active live sports found: {active_live_sports}")

                            if not active_live_sports:
                                # Should not happen if has_live_games is True, but handle defensively
                                logger.warning("In live mode, timer expired, but no active sports found. Falling back.")
                                # Fallback: Exit live mode logic will handle this below implicitly
                                is_currently_live = False # Force exit from live block
                                has_live_games = False  # Ensure we enter regular rotation block

                            elif len(active_live_sports) == 1:
                                # Only one sport is live, stay on it (or switch if mode mismatch)
                                new_mode = f"{active_live_sports[0]}_live"
                                if self.current_display_mode != new_mode:
                                    logger.info(f"Switching to only active live sport: {new_mode} from {self.current_display_mode}")
                                    self.current_display_mode = new_mode
                                    self.force_clear = True
                                else:
                                    logger.debug(f"Staying on single active live sport: {self.current_display_mode}")
                                    self.force_clear = False # Just resetting timer, no need to clear
                                self.last_switch = current_time
                                manager_to_display = getattr(self, f"{active_live_sports[0]}_live", None)
                            
                            else:
                                # Multiple sports live, rotate
                                try:
                                    current_sport = self.current_display_mode.replace('_live', '')
                                    current_index = active_live_sports.index(current_sport)
                                    next_index = (current_index + 1) % len(active_live_sports)
                                    next_sport = active_live_sports[next_index]
                                    next_mode = f"{next_sport}_live"
                                    logger.info(f"Rotating live sports: {self.current_display_mode} -> {next_mode}")
                                    self.current_display_mode = next_mode
                                    self.force_clear = True
                                    self.last_switch = current_time
                                    manager_to_display = getattr(self, f"{next_sport}_live", None)
                                except ValueError:
                                    # Current sport is no longer active, switch to highest priority active one
                                    logger.warning(f"Current live sport {current_sport} not found in active list {active_live_sports}. Switching to highest priority.")
                                    next_sport = active_live_sports[0]
                                    next_mode = f"{next_sport}_live"
                                    self.current_display_mode = next_mode
                                    self.force_clear = True
                                    self.last_switch = current_time
                                    manager_to_display = getattr(self, f"{next_sport}_live", None)
                        else:
                            # Timer not expired, continue showing current live game
                            logger.debug(f"Continuing live mode {self.current_display_mode}. Timer not expired.")
                            self.force_clear = False 
                            current_sport_type = self.current_display_mode.replace('_live', '')
                            manager_to_display = getattr(self, f"{current_sport_type}_live", None)
                    
                    else:
                        # Was in live mode, but no live games detected *now*. Switch OUT.
                        logger.info(f"Exiting live mode {self.current_display_mode} as no live games detected.")
                        # Fall through to the 'not is_currently_live' block below
                        is_currently_live = False # Update state for the next block
                        # Let the regular mode selection logic handle the transition


                if not is_currently_live:
                    # --- Currently in a Regular Mode (or just exited Live) ---
                    if has_live_games:
                        # Not currently live, but live games ARE available. Switch IN.
                        # (This check ensures we only switch *in* if we weren't already live)
                        new_mode = f"{live_sport_type}_live" # live_sport_type has the highest priority
                        if self.current_display_mode != new_mode: # Avoid unnecessary resets if somehow already correct
                             logger.info(f"Switching into LIVE mode: {new_mode} from {self.current_display_mode}")
                             self.current_display_mode = new_mode
                             self.force_clear = True
                             self.last_switch = current_time
                             manager_to_display = getattr(self, f"{live_sport_type}_live", None)
                        else:
                             # Should technically not happen based on is_currently_live flag, but safety first
                             self.force_clear = False
                             self.last_switch = current_time # Reset timer anyway
                             manager_to_display = getattr(self, f"{live_sport_type}_live", None)

                    else:
                        # No live games detected, and not in live mode. Regular rotation.
                        needs_switch = False
                        if self.current_display_mode.endswith('_live'):
                             # This case handles the explicit transition OUT of live mode 
                             # initiated in the block above.
                             logger.info(f"Transitioning from live mode to regular rotation.")
                             needs_switch = True 
                             # Find the next *regular* mode index cleanly
                             try:
                                 # Find where we *would* be if we weren't live
                                 # This assumes self.current_mode_index tracks the regular rotation position
                                 self.current_mode_index = (self.current_mode_index + 1) % len(self.available_modes)
                             except Exception: # Catch potential issues if available_modes changed etc.
                                 logger.warning("Error advancing regular mode index after live mode exit. Resetting.")
                                 self.current_mode_index = 0 
                             
                             if not self.available_modes: # Safety check
                                 logger.error("No available regular modes to switch to!")
                                 self.current_display_mode = 'none' # Or handle error appropriately
                                 # Consider exiting or sleeping
                             else:
                                 self.current_display_mode = self.available_modes[self.current_mode_index]

                        elif current_time - self.last_switch >= self.get_current_duration():
                             # Regular timer expired, advance to next mode
                             logger.debug(f"Timer expired for regular mode {self.current_display_mode}. Switching.")
                             # Advance calendar event *before* potentially switching away
                             if self.current_display_mode == 'calendar' and self.calendar:
                                 self.calendar.advance_event()
                             needs_switch = True
                             self.current_mode_index = (self.current_mode_index + 1) % len(self.available_modes)
                             self.current_display_mode = self.available_modes[self.current_mode_index]
                        
                        if needs_switch:
                             logger.info(f"Switching to regular mode: {self.current_display_mode}")
                             self.force_clear = True
                             self.last_switch = current_time
                        else:
                             # Timer not expired for current regular mode
                             self.force_clear = False

                        # Select the manager for the current regular mode
                        # (This code block is largely the same as before)
                        if self.current_display_mode == 'clock' and self.clock:
                             manager_to_display = self.clock
                        elif self.current_display_mode == 'weather_current' and self.weather:
                             manager_to_display = self.weather
                        elif self.current_display_mode == 'weather_hourly' and self.weather:
                             manager_to_display = self.weather
                        elif self.current_display_mode == 'weather_daily' and self.weather:
                             manager_to_display = self.weather
                        elif self.current_display_mode == 'stocks' and self.stocks:
                             manager_to_display = self.stocks
                        elif self.current_display_mode == 'stock_news' and self.news:
                              manager_to_display = self.news
                        elif self.current_display_mode == 'calendar' and self.calendar:
                              manager_to_display = self.calendar
                        elif self.current_display_mode == 'youtube' and self.youtube:
                              manager_to_display = self.youtube
                        elif self.current_display_mode == 'text_display' and self.text_display:
                              manager_to_display = self.text_display
                        # Add other regular managers (NHL recent/upcoming, NBA, MLB, Soccer, NFL, NCAA FB)
                        elif self.current_display_mode == 'nhl_recent' and self.nhl_recent:
                            manager_to_display = self.nhl_recent
                        elif self.current_display_mode == 'nhl_upcoming' and self.nhl_upcoming:
                            manager_to_display = self.nhl_upcoming
                        elif self.current_display_mode == 'nba_recent' and self.nba_recent:
                            manager_to_display = self.nba_recent
                        elif self.current_display_mode == 'nba_upcoming' and self.nba_upcoming:
                            manager_to_display = self.nba_upcoming
                        elif self.current_display_mode == 'mlb_recent' and self.mlb_recent:
                            manager_to_display = self.mlb_recent
                        elif self.current_display_mode == 'mlb_upcoming' and self.mlb_upcoming:
                            manager_to_display = self.mlb_upcoming
                        elif self.current_display_mode == 'soccer_recent' and self.soccer_recent:
                            manager_to_display = self.soccer_recent
                        elif self.current_display_mode == 'soccer_upcoming' and self.soccer_upcoming:
                            manager_to_display = self.soccer_upcoming
                        elif self.current_display_mode == 'nfl_recent' and self.nfl_recent:
                            manager_to_display = self.nfl_recent
                        elif self.current_display_mode == 'nfl_upcoming' and self.nfl_upcoming:
                            manager_to_display = self.nfl_upcoming
                        elif self.current_display_mode == 'ncaa_fb_recent' and self.ncaa_fb_recent:
                            manager_to_display = self.ncaa_fb_recent
                        elif self.current_display_mode == 'ncaa_fb_upcoming' and self.ncaa_fb_upcoming:
                            manager_to_display = self.ncaa_fb_upcoming

                # NCAA Mens Basketball rotation logic
                elif self.current_display_mode == 'ncaa_mens_basketball_recent' or self.current_display_mode == 'ncaa_mens_basketball_upcoming':
                    if self.ncaa_mens_basketball_favorite_teams and len(self.ncaa_mens_basketball_favorite_teams) > 0: # Only rotate if there are favorite teams
                        if not self.in_ncaa_mens_basketball_rotation:
                            logger.info("Entering NCAA Mens Basketball rotation")
                            self.in_ncaa_mens_basketball_rotation = True
                            self.ncaa_mens_basketball_current_team_index = 0
                            self.ncaa_mens_basketball_showing_recent = True # Start with recent for the first team

                        # Switch between recent and upcoming for the current team
                        if self.ncaa_mens_basketball_showing_recent and self.ncaa_mens_basketball_upcoming:
                            self.current_display_mode = 'ncaa_mens_basketball_upcoming'
                            self.ncaa_mens_basketball_showing_recent = False
                            logger.info(f"Switching to NCAA Mens Basketball upcoming for team {self.ncaa_mens_basketball_favorite_teams[self.ncaa_mens_basketball_current_team_index % len(self.ncaa_mens_basketball_favorite_teams)]}")
                        elif not self.ncaa_mens_basketball_showing_recent and self.ncaa_mens_basketball_recent:
                            self.current_display_mode = 'ncaa_mens_basketball_recent'
                            self.ncaa_mens_basketball_showing_recent = True
                            self.ncaa_mens_basketball_current_team_index = (self.ncaa_mens_basketball_current_team_index + 1) % len(self.ncaa_mens_basketball_favorite_teams)
                            logger.info(f"Switching to NCAA Mens Basketball recent for team {self.ncaa_mens_basketball_favorite_teams[self.ncaa_mens_basketball_current_team_index % len(self.ncaa_mens_basketball_favorite_teams)]}")
                        # If only one mode is available, stick to it and just cycle teams
                        elif self.ncaa_mens_basketball_recent and not self.ncaa_mens_basketball_upcoming: # Only recent enabled
                            self.current_display_mode = 'ncaa_mens_basketball_recent'
                            self.ncaa_mens_basketball_current_team_index = (self.ncaa_mens_basketball_current_team_index + 1) % len(self.ncaa_mens_basketball_favorite_teams)
                        elif self.ncaa_mens_basketball_upcoming and not self.ncaa_mens_basketball_recent: # Only upcoming enabled
                            self.current_display_mode = 'ncaa_mens_basketball_upcoming'
                            self.ncaa_mens_basketball_current_team_index = (self.ncaa_mens_basketball_current_team_index + 1) % len(self.ncaa_mens_basketball_favorite_teams)
                        else: # No recent/upcoming enabled
                            self._go_to_next_mode()
                    else: # No favorite teams, just go to next mode
                        self._go_to_next_mode()

                # NCAA Mens Baseball rotation logic
                elif self.current_display_mode == 'ncaa_mens_baseball_recent' or self.current_display_mode == 'ncaa_mens_baseball_upcoming':
                    if self.ncaa_mens_baseball_favorite_teams and len(self.ncaa_mens_baseball_favorite_teams) > 0: # Only rotate if there are favorite teams
                        if not self.in_ncaa_mens_baseball_rotation:
                            logger.info("Entering NCAA Mens Baseball rotation")
                            self.in_ncaa_mens_baseball_rotation = True
                            self.ncaa_mens_baseball_current_team_index = 0
                            self.ncaa_mens_baseball_showing_recent = True # Start with recent for the first team

                        # Switch between recent and upcoming for the current team
                        if self.ncaa_mens_baseball_showing_recent and self.ncaa_mens_baseball_upcoming:
                            self.current_display_mode = 'ncaa_mens_baseball_upcoming'
                            self.ncaa_mens_baseball_showing_recent = False
                            logger.info(f"Switching to NCAA Mens Baseball upcoming for team {self.ncaa_mens_baseball_favorite_teams[self.ncaa_mens_baseball_current_team_index % len(self.ncaa_mens_baseball_favorite_teams)]}")
                        elif not self.ncaa_mens_baseball_showing_recent and self.ncaa_mens_baseball_recent:
                            self.current_display_mode = 'ncaa_mens_baseball_recent'
                            self.ncaa_mens_baseball_showing_recent = True
                            self.ncaa_mens_baseball_current_team_index = (self.ncaa_mens_baseball_current_team_index + 1) % len(self.ncaa_mens_baseball_favorite_teams)
                            logger.info(f"Switching to NCAA Mens Baseball recent for team {self.ncaa_mens_baseball_favorite_teams[self.ncaa_mens_baseball_current_team_index % len(self.ncaa_mens_baseball_favorite_teams)]}")
                        # If only one mode is available, stick to it and just cycle teams
                        elif self.ncaa_mens_baseball_recent and not self.ncaa_mens_baseball_upcoming: # Only recent enabled
                            self.current_display_mode = 'ncaa_mens_baseball_recent'
                            self.ncaa_mens_baseball_current_team_index = (self.ncaa_mens_baseball_current_team_index + 1) % len(self.ncaa_mens_baseball_favorite_teams)
                        elif self.ncaa_mens_baseball_upcoming and not self.ncaa_mens_baseball_recent: # Only upcoming enabled
                            self.current_display_mode = 'ncaa_mens_baseball_upcoming'
                            self.ncaa_mens_baseball_current_team_index = (self.ncaa_mens_baseball_current_team_index + 1) % len(self.ncaa_mens_baseball_favorite_teams)
                        else: # No recent/upcoming enabled
                            self._go_to_next_mode()
                    else: # No favorite teams, just go to next mode
                        self._go_to_next_mode()

                # --- Perform Display Update ---
                try:
                    if self.current_display_mode == 'music' and self.music_manager:
                        # Call MusicManager's display method
                        self.music_manager.display(force_clear=self.force_clear)
                        # Reset force_clear if it was true for this mode
                        if self.force_clear:
                            self.force_clear = False
                    elif manager_to_display:
                        logger.debug(f"Attempting to display mode: {self.current_display_mode} using manager {type(manager_to_display).__name__} with force_clear={self.force_clear}")
                        # Call the appropriate display method based on mode/manager type
                        # Note: Some managers have different display methods or handle clearing internally
                        if self.current_display_mode == 'clock':
                            manager_to_display.display_time(force_clear=self.force_clear)
                        elif self.current_display_mode == 'weather_current':
                            manager_to_display.display_weather(force_clear=self.force_clear)
                        elif self.current_display_mode == 'weather_hourly':
                            manager_to_display.display_hourly_forecast(force_clear=self.force_clear)
                        elif self.current_display_mode == 'weather_daily':
                            manager_to_display.display_daily_forecast(force_clear=self.force_clear)
                        elif self.current_display_mode == 'stocks':
                            manager_to_display.display_stocks(force_clear=self.force_clear)
                        elif self.current_display_mode == 'stock_news':
                             manager_to_display.display_news() # Assumes internal clearing
                        elif self.current_display_mode == 'calendar':
                             manager_to_display.display(force_clear=self.force_clear)
                        elif self.current_display_mode == 'youtube' and self.youtube:
                             self.youtube.display(force_clear=self.force_clear)
                        elif self.current_display_mode == 'text_display':
                             manager_to_display.display() # Assumes internal clearing
                        elif self.current_display_mode == 'nfl_live' and self.nfl_live:
                            self.nfl_live.display(force_clear=self.force_clear)
                        elif self.current_display_mode == 'ncaa_fb_live' and self.ncaa_fb_live:
                            self.ncaa_fb_live.display(force_clear=self.force_clear)
                        elif self.current_display_mode == 'ncaa_mens_basketball_live' and self.ncaa_mens_basketball_live:
                            self.ncaa_mens_basketball_live.display(force_clear=self.force_clear)
                        elif self.current_display_mode == 'ncaa_mens_baseball_live' and self.ncaa_mens_baseball_live:
                            self.ncaa_mens_baseball_live.display(force_clear=self.force_clear)
                        elif self.current_display_mode == 'mlb_live' and self.mlb_live:
                            self.mlb_live.display(force_clear=self.force_clear)
                        elif self.current_display_mode == 'ncaa_fb_upcoming' and self.ncaa_fb_upcoming:
                            self.ncaa_fb_upcoming.display(force_clear=self.force_clear)
                        elif self.current_display_mode == 'ncaa_mens_basketball_recent' and self.ncaa_mens_basketball_recent:
                            self.ncaa_mens_basketball_recent.display(force_clear=self.force_clear)
                        elif self.current_display_mode == 'ncaa_mens_basketball_upcoming' and self.ncaa_mens_basketball_upcoming:
                            self.ncaa_mens_basketball_upcoming.display(force_clear=self.force_clear)
                        elif self.current_display_mode == 'ncaa_mens_baseball_recent' and self.ncaa_mens_baseball_recent:
                            self.ncaa_mens_baseball_recent.display(force_clear=self.force_clear)
                        elif self.current_display_mode == 'ncaa_mens_baseball_upcoming' and self.ncaa_mens_baseball_upcoming:
                            self.ncaa_mens_baseball_upcoming.display(force_clear=self.force_clear)
                        elif hasattr(manager_to_display, 'display'): # General case for most managers
                            manager_to_display.display(force_clear=self.force_clear)
                        else:
                            logger.warning(f"Manager {type(manager_to_display).__name__} for mode {self.current_display_mode} does not have a standard 'display' method.")
                        
                        # Reset force_clear *after* a successful display call that used it
                        # Important: Only reset if the display method *might* have used it.
                        # Internal clearing methods (news, text) don't necessitate resetting it here.
                        if self.force_clear and self.current_display_mode not in ['stock_news', 'text_display']:
                            self.force_clear = False 
                    elif self.current_display_mode != 'none':
                         logger.warning(f"No manager found or selected for display mode: {self.current_display_mode}")

                except Exception as e:
                    logger.error(f"Error during display update for mode {self.current_display_mode}: {e}", exc_info=True)
                    # Force clear on the next iteration after an error to be safe
                    self.force_clear = True 


                # Small sleep removed - updates/drawing should manage timing
                # time.sleep(self.update_interval) 

        except KeyboardInterrupt:
            logger.info("Display controller stopped by user")
        except Exception as e:
            logger.error(f"Critical error in display controller run loop: {e}", exc_info=True)
        finally:
            logger.info("Cleaning up display manager...")
            self.display_manager.cleanup()
            if self.music_manager: # Check if music_manager object exists
                logger.info("Stopping music polling...")
                self.music_manager.stop_polling()
            logger.info("Cleanup complete.")

def main():
    controller = DisplayController()
    controller.run()

if __name__ == "__main__":
    main() 