import time
import logging
import sys
from typing import Dict, Any, List
from datetime import datetime, time as time_obj

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
from src.odds_ticker_manager import OddsTickerManager
from src.nhl_managers import NHLLiveManager, NHLRecentManager, NHLUpcomingManager
from src.nba_managers import NBALiveManager, NBARecentManager, NBAUpcomingManager
from src.mlb_manager import MLBLiveManager, MLBRecentManager, MLBUpcomingManager
from src.milb_manager import MiLBLiveManager, MiLBRecentManager, MiLBUpcomingManager
from src.soccer_managers import SoccerLiveManager, SoccerRecentManager, SoccerUpcomingManager
from src.nfl_managers import NFLLiveManager, NFLRecentManager, NFLUpcomingManager
from src.ncaa_fb_managers import NCAAFBLiveManager, NCAAFBRecentManager, NCAAFBUpcomingManager
from src.ncaa_baseball_managers import NCAABaseballLiveManager, NCAABaseballRecentManager, NCAABaseballUpcomingManager
from src.ncaam_basketball_managers import NCAAMBasketballLiveManager, NCAAMBasketballRecentManager, NCAAMBasketballUpcomingManager
from src.youtube_display import YouTubeDisplay
from src.calendar_manager import CalendarManager
from src.text_display import TextDisplay
from src.music_manager import MusicManager
from src.of_the_day_manager import OfTheDayManager
from src.news_manager import NewsManager

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
        self.odds_ticker = OddsTickerManager(self.config, self.display_manager) if self.config.get('odds_ticker', {}).get('enabled', False) else None
        self.calendar = CalendarManager(self.display_manager, self.config) if self.config.get('calendar', {}).get('enabled', False) else None
        self.youtube = YouTubeDisplay(self.display_manager, self.config) if self.config.get('youtube', {}).get('enabled', False) else None
        self.text_display = TextDisplay(self.display_manager, self.config) if self.config.get('text_display', {}).get('enabled', False) else None
        self.of_the_day = OfTheDayManager(self.display_manager, self.config) if self.config.get('of_the_day', {}).get('enabled', False) else None
        self.news_manager = NewsManager(self.config, self.display_manager) if self.config.get('news_manager', {}).get('enabled', False) else None
        logger.info(f"Calendar Manager initialized: {'Object' if self.calendar else 'None'}")
        logger.info(f"Text Display initialized: {'Object' if self.text_display else 'None'}")
        logger.info(f"OfTheDay Manager initialized: {'Object' if self.of_the_day else 'None'}")
        logger.info(f"News Manager initialized: {'Object' if self.news_manager else 'None'}")
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
                    self.music_manager = MusicManager(display_manager=self.display_manager, config=self.config, update_callback=self._handle_music_update)
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

        # Initialize MiLB managers if enabled
        milb_time = time.time()
        milb_enabled = self.config.get('milb', {}).get('enabled', False)
        milb_display_modes = self.config.get('milb', {}).get('display_modes', {})
        
        if milb_enabled:
            self.milb_live = MiLBLiveManager(self.config, self.display_manager) if milb_display_modes.get('milb_live', True) else None
            self.milb_recent = MiLBRecentManager(self.config, self.display_manager) if milb_display_modes.get('milb_recent', True) else None
            self.milb_upcoming = MiLBUpcomingManager(self.config, self.display_manager) if milb_display_modes.get('milb_upcoming', True) else None
        else:
            self.milb_live = None
            self.milb_recent = None
            self.milb_upcoming = None
        logger.info("MiLB managers initialized in %.3f seconds", time.time() - milb_time)
            
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
        
        # Initialize NCAA Baseball managers if enabled
        ncaa_baseball_time = time.time()
        ncaa_baseball_enabled = self.config.get('ncaa_baseball_scoreboard', {}).get('enabled', False)
        ncaa_baseball_display_modes = self.config.get('ncaa_baseball_scoreboard', {}).get('display_modes', {})
        
        if ncaa_baseball_enabled:
            self.ncaa_baseball_live = NCAABaseballLiveManager(self.config, self.display_manager) if ncaa_baseball_display_modes.get('ncaa_baseball_live', True) else None
            self.ncaa_baseball_recent = NCAABaseballRecentManager(self.config, self.display_manager) if ncaa_baseball_display_modes.get('ncaa_baseball_recent', True) else None
            self.ncaa_baseball_upcoming = NCAABaseballUpcomingManager(self.config, self.display_manager) if ncaa_baseball_display_modes.get('ncaa_baseball_upcoming', True) else None
        else:
            self.ncaa_baseball_live = None
            self.ncaa_baseball_recent = None
            self.ncaa_baseball_upcoming = None
        logger.info("NCAA Baseball managers initialized in %.3f seconds", time.time() - ncaa_baseball_time)

        # Initialize NCAA Men's Basketball managers if enabled
        ncaam_basketball_time = time.time()
        ncaam_basketball_enabled = self.config.get('ncaam_basketball_scoreboard', {}).get('enabled', False)
        ncaam_basketball_display_modes = self.config.get('ncaam_basketball_scoreboard', {}).get('display_modes', {})
        
        if ncaam_basketball_enabled:
            self.ncaam_basketball_live = NCAAMBasketballLiveManager(self.config, self.display_manager) if ncaam_basketball_display_modes.get('ncaam_basketball_live', True) else None
            self.ncaam_basketball_recent = NCAAMBasketballRecentManager(self.config, self.display_manager) if ncaam_basketball_display_modes.get('ncaam_basketball_recent', True) else None
            self.ncaam_basketball_upcoming = NCAAMBasketballUpcomingManager(self.config, self.display_manager) if ncaam_basketball_display_modes.get('ncaam_basketball_upcoming', True) else None
        else:
            self.ncaam_basketball_live = None
            self.ncaam_basketball_recent = None
            self.ncaam_basketball_upcoming = None
        logger.info("NCAA Men's Basketball managers initialized in %.3f seconds", time.time() - ncaam_basketball_time)
        
        # Track MLB rotation state
        self.mlb_current_team_index = 0
        self.mlb_showing_recent = True
        self.mlb_favorite_teams = self.config.get('mlb', {}).get('favorite_teams', [])
        self.in_mlb_rotation = False
        
        # Read live_priority flags for all sports
        self.nhl_live_priority = self.config.get('nhl_scoreboard', {}).get('live_priority', True)
        self.nba_live_priority = self.config.get('nba_scoreboard', {}).get('live_priority', True)
        self.mlb_live_priority = self.config.get('mlb', {}).get('live_priority', True)
        self.milb_live_priority = self.config.get('milb', {}).get('live_priority', True)
        self.soccer_live_priority = self.config.get('soccer_scoreboard', {}).get('live_priority', True)
        self.nfl_live_priority = self.config.get('nfl_scoreboard', {}).get('live_priority', True)
        self.ncaa_fb_live_priority = self.config.get('ncaa_fb_scoreboard', {}).get('live_priority', True)
        self.ncaa_baseball_live_priority = self.config.get('ncaa_baseball_scoreboard', {}).get('live_priority', True)
        self.ncaam_basketball_live_priority = self.config.get('ncaam_basketball_scoreboard', {}).get('live_priority', True)
        
        # List of available display modes (adjust order as desired)
        self.available_modes = []
        if self.clock: self.available_modes.append('clock')
        if self.weather: self.available_modes.extend(['weather_current', 'weather_hourly', 'weather_daily'])
        if self.stocks: self.available_modes.append('stocks')
        if self.news: self.available_modes.append('stock_news')
        if self.odds_ticker: self.available_modes.append('odds_ticker')
        if self.calendar: self.available_modes.append('calendar')
        if self.youtube: self.available_modes.append('youtube')
        if self.text_display: self.available_modes.append('text_display')
        if self.of_the_day: self.available_modes.append('of_the_day')
        if self.news_manager: self.available_modes.append('news_manager')
        if self.music_manager:
            self.available_modes.append('music')
        # Add NHL display modes if enabled
        if nhl_enabled:
            if self.nhl_recent: self.available_modes.append('nhl_recent')
            if self.nhl_upcoming: self.available_modes.append('nhl_upcoming')
            # nhl_live is handled below for live_priority
        if nba_enabled:
            if self.nba_recent: self.available_modes.append('nba_recent')
            if self.nba_upcoming: self.available_modes.append('nba_upcoming')
        if mlb_enabled:
            if self.mlb_recent: self.available_modes.append('mlb_recent')
            if self.mlb_upcoming: self.available_modes.append('mlb_upcoming')
        if milb_enabled:
            if self.milb_recent: self.available_modes.append('milb_recent')
            if self.milb_upcoming: self.available_modes.append('milb_upcoming')
        if soccer_enabled:
            if self.soccer_recent: self.available_modes.append('soccer_recent')
            if self.soccer_upcoming: self.available_modes.append('soccer_upcoming')
        if nfl_enabled:
            if self.nfl_recent: self.available_modes.append('nfl_recent')
            if self.nfl_upcoming: self.available_modes.append('nfl_upcoming')
        if ncaa_fb_enabled:
            if self.ncaa_fb_recent: self.available_modes.append('ncaa_fb_recent')
            if self.ncaa_fb_upcoming: self.available_modes.append('ncaa_fb_upcoming')
        if ncaa_baseball_enabled:
            if self.ncaa_baseball_recent: self.available_modes.append('ncaa_baseball_recent')
            if self.ncaa_baseball_upcoming: self.available_modes.append('ncaa_baseball_upcoming')
        if ncaam_basketball_enabled:
            if self.ncaam_basketball_recent: self.available_modes.append('ncaam_basketball_recent')
            if self.ncaam_basketball_upcoming: self.available_modes.append('ncaam_basketball_upcoming')
        # Add live modes to rotation if live_priority is False and there are live games
        self._update_live_modes_in_rotation()
        
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
        self.ncaa_fb_showing_recent = True # Start with recent games
        self.ncaa_fb_favorite_teams = self.config.get('ncaa_fb_scoreboard', {}).get('favorite_teams', [])
        self.in_ncaa_fb_rotation = False
        
        # Add NCAA Baseball rotation state
        self.ncaa_baseball_current_team_index = 0
        self.ncaa_baseball_showing_recent = True
        self.ncaa_baseball_favorite_teams = self.config.get('ncaa_baseball_scoreboard', {}).get('favorite_teams', [])
        self.in_ncaa_baseball_rotation = False

        # Add NCAA Men's Basketball rotation state
        self.ncaam_basketball_current_team_index = 0
        self.ncaam_basketball_showing_recent = True
        self.ncaam_basketball_favorite_teams = self.config.get('ncaam_basketball_scoreboard', {}).get('favorite_teams', [])
        self.in_ncaam_basketball_rotation = False
        
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
            'milb_live': 30,
            'milb_recent': 20,
            'milb_upcoming': 20,
            'soccer_live': 30, # Soccer durations
            'soccer_recent': 20,
            'soccer_upcoming': 20,
            'nfl_live': 30, # Added NFL durations
            'nfl_recent': 30,
            'nfl_upcoming': 30,
            'ncaa_fb_live': 30, # Added NCAA FB durations
            'ncaa_fb_recent': 15,
            'ncaa_fb_upcoming': 15,
            'music': 20, # Default duration for music, will be overridden by config if present
            'ncaa_baseball_live': 30, # Added NCAA Baseball durations
            'ncaa_baseball_recent': 15,
            'ncaa_baseball_upcoming': 15,
            'ncaam_basketball_live': 30, # Added NCAA Men's Basketball durations
            'ncaam_basketball_recent': 15,
            'ncaam_basketball_upcoming': 15
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
        if milb_enabled:
            logger.info(f"MiLB Favorite teams: {self.config.get('milb', {}).get('favorite_teams', [])}")
        if soccer_enabled: # Check if soccer is enabled
            logger.info(f"Soccer Favorite teams: {self.soccer_favorite_teams}")
        if nfl_enabled: # Check if NFL is enabled
            logger.info(f"NFL Favorite teams: {self.nfl_favorite_teams}")
        if ncaa_fb_enabled: # Check if NCAA FB is enabled
            logger.info(f"NCAA FB Favorite teams: {self.ncaa_fb_favorite_teams}")
        if ncaa_baseball_enabled: # Check if NCAA Baseball is enabled
            logger.info(f"NCAA Baseball Favorite teams: {self.ncaa_baseball_favorite_teams}")
        if ncaam_basketball_enabled: # Check if NCAA Men's Basketball is enabled
            logger.info(f"NCAA Men's Basketball Favorite teams: {self.ncaam_basketball_favorite_teams}")

        logger.info(f"Available display modes: {self.available_modes}")
        logger.info(f"Initial display mode: {self.current_display_mode}")
        logger.info("DisplayController initialized with display_manager: %s", id(self.display_manager))

        # --- SCHEDULING & CONFIG REFRESH ---
        self.config_check_interval = 30
        self.last_config_check = 0
        self.is_display_active = True
        self._load_config() # Initial load of schedule

    def _handle_music_update(self, track_info: Dict[str, Any], significant_change: bool = False):
        """Callback for when music track info changes."""
        # MusicManager now handles its own display state (album art, etc.)
        # This callback might still be useful if DisplayController needs to react to music changes
        # for reasons other than directly re-drawing the music screen (e.g., logging, global state).
        # For now, we'll keep it simple. If the music screen is active, it will redraw on its own.
        if track_info:
            logger.debug(f"DisplayController received music update (via callback): Title - {track_info.get('title')}, Playing - {track_info.get('is_playing')}")
        else:
            logger.debug("DisplayController received music update (via callback): Track is None or not playing.")

        if self.current_display_mode == 'music' and self.music_manager:
            if significant_change:
                logger.info("Music is current display mode and SIGNIFICANT track updated. Signaling immediate refresh.")
                self.force_clear = True # Tell the display method to clear before drawing
            else:
                logger.debug("Music is current display mode and received a MINOR update (e.g. progress). No force_clear.")
                # self.force_clear = False # Ensure it's false if not significant, or let run loop manage
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
        if self.odds_ticker: self.odds_ticker.update()
        if self.calendar: self.calendar.update(time.time())
        if self.youtube: self.youtube.update()
        if self.text_display: self.text_display.update()
        if self.of_the_day: self.of_the_day.update(time.time())
        if self.news_manager: self.news_manager.fetch_news_data()
        
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
        
        # Update MiLB managers
        if self.milb_live: self.milb_live.update()
        if self.milb_recent: self.milb_recent.update()
        if self.milb_upcoming: self.milb_upcoming.update()
        
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

        # Update NCAA Baseball managers
        if self.ncaa_baseball_live: self.ncaa_baseball_live.update()
        if self.ncaa_baseball_recent: self.ncaa_baseball_recent.update()
        if self.ncaa_baseball_upcoming: self.ncaa_baseball_upcoming.update()

        # Update NCAA Men's Basketball managers
        if self.ncaam_basketball_live: self.ncaam_basketball_live.update()
        if self.ncaam_basketball_recent: self.ncaam_basketball_recent.update()
        if self.ncaam_basketball_upcoming: self.ncaam_basketball_upcoming.update()

    def _check_live_games(self) -> tuple[bool, str]:
        """
        Check if there are any live games available.
        Returns:
            tuple[bool, str]: (has_live_games, sport_type)
            sport_type will be 'nhl', 'nba', 'mlb', 'milb', 'soccer' or None
        """
        # Prioritize sports (e.g., Soccer > NHL > NBA > MLB)
        live_checks = {
            'nhl': self.nhl_live and self.nhl_live.live_games,
            'nba': self.nba_live and self.nba_live.live_games,
            'mlb': self.mlb_live and self.mlb_live.live_games,
            'milb': self.milb_live and self.milb_live.live_games,
            'nfl': self.nfl_live and self.nfl_live.live_games,
            # ... other sports
        }

        for sport, live_games in live_checks.items():
            if live_games:
                logger.debug(f"{sport.upper()} live games available")
                return True, sport

        if 'ncaa_fb_scoreboard' in self.config and self.config['ncaa_fb_scoreboard'].get('enabled', False):
            if self.ncaa_fb_live and self.ncaa_fb_live.live_games:
                logger.debug("NCAA FB live games available")
                return True, 'ncaa_fb'
        
        if 'ncaa_baseball_scoreboard' in self.config and self.config['ncaa_baseball_scoreboard'].get('enabled', False):
            if self.ncaa_baseball_live and self.ncaa_baseball_live.live_games:
                logger.debug("NCAA Baseball live games available")
                return True, 'ncaa_baseball'

        if 'ncaam_basketball_scoreboard' in self.config and self.config['ncaam_basketball_scoreboard'].get('enabled', False):
            if self.ncaam_basketball_live and self.ncaam_basketball_live.live_games:
                logger.debug("NCAA Men's Basketball live games available")
                return True, 'ncaam_basketball'
        # Add more sports checks here (e.g., MLB, Soccer)
        if 'mlb' in self.config and self.config['mlb'].get('enabled', False):
            if self.mlb_live and self.mlb_live.live_games:
                return True, 'mlb'
            
        return False, None

    def _get_team_games(self, team: str, sport: str = 'nhl', is_recent: bool = True) -> bool:
        """
        Get games for a specific team and update the current game.
        Args:
            team: Team abbreviation
            sport: 'nhl', 'nba', 'mlb', 'milb', or 'soccer'
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
        elif sport == 'milb':
            manager_recent = self.milb_recent
            manager_upcoming = self.milb_upcoming
            games_list_attr = 'recent_games' if is_recent else 'upcoming_games'
            abbr_key_home = 'home_team' # MiLB uses different keys
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
        elif sport == 'milb':
            favorite_teams = self.config.get('milb', {}).get('favorite_teams', [])
            manager_recent = self.milb_recent
            manager_upcoming = self.milb_upcoming
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
        elif sport == 'milb':
            if not self.config.get('milb', {}).get('favorite_teams', []): return
            current_team = self.config['milb']['favorite_teams'][self.milb_current_team_index]
            # ... (rest of MiLB rotation logic)
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

    # --- SCHEDULING METHODS ---
    def _load_config(self):
        """Load configuration from the config manager and parse schedule settings."""
        self.config = self.config_manager.load_config()
        schedule_config = self.config.get('schedule', {})
        self.schedule_enabled = schedule_config.get('enabled', False)
        try:
            self.start_time = datetime.strptime(schedule_config.get('start_time', '07:00'), '%H:%M').time()
            self.end_time = datetime.strptime(schedule_config.get('end_time', '22:00'), '%H:%M').time()
        except (ValueError, TypeError):
            logger.warning("Invalid time format in schedule config. Using defaults.")
            self.start_time = time_obj(7, 0)
            self.end_time = time_obj(22, 0)

    def _check_schedule(self):
        """Check if the display should be active based on the schedule."""
        if not self.schedule_enabled:
            if not self.is_display_active:
                logger.info("Schedule is disabled. Activating display.")
                self.is_display_active = True
            return

        now_time = datetime.now().time()
        
        # Handle overnight schedules
        if self.start_time <= self.end_time:
            should_be_active = self.start_time <= now_time < self.end_time
        else: 
            should_be_active = now_time >= self.start_time or now_time < self.end_time

        if should_be_active and not self.is_display_active:
            logger.info("Within scheduled time. Activating display.")
            self.is_display_active = True
            self.force_clear = True # Force a redraw
        elif not should_be_active and self.is_display_active:
            logger.info("Outside of scheduled time. Deactivating display.")
            self.display_manager.clear()
            self.is_display_active = False

    def _update_live_modes_in_rotation(self):
        """Add or remove live modes from available_modes based on live_priority and live games."""
        # Helper to add/remove live modes for all sports
        def update_mode(mode_name, manager, live_priority):
            if not live_priority:
                if manager and getattr(manager, 'live_games', None):
                    if mode_name not in self.available_modes:
                        self.available_modes.append(mode_name)
                else:
                    if mode_name in self.available_modes:
                        self.available_modes.remove(mode_name)
        update_mode('nhl_live', getattr(self, 'nhl_live', None), self.nhl_live_priority)
        update_mode('nba_live', getattr(self, 'nba_live', None), self.nba_live_priority)
        update_mode('mlb_live', getattr(self, 'mlb_live', None), self.mlb_live_priority)
        update_mode('milb_live', getattr(self, 'milb_live', None), self.milb_live_priority)
        update_mode('soccer_live', getattr(self, 'soccer_live', None), self.soccer_live_priority)
        update_mode('nfl_live', getattr(self, 'nfl_live', None), self.nfl_live_priority)
        update_mode('ncaa_fb_live', getattr(self, 'ncaa_fb_live', None), self.ncaa_fb_live_priority)
        update_mode('ncaa_baseball_live', getattr(self, 'ncaa_baseball_live', None), self.ncaa_baseball_live_priority)
        update_mode('ncaam_basketball_live', getattr(self, 'ncaam_basketball_live', None), self.ncaam_basketball_live_priority)

    def run(self):
        """Run the display controller, switching between displays."""
        if not self.available_modes:
            logger.warning("No display modes are enabled. Exiting.")
            self.display_manager.cleanup()
            return
             
        try:
            while True:
                current_time = time.time()

                # Periodically check for config changes
                if current_time - self.last_config_check > self.config_check_interval:
                    self._load_config()
                    self.last_config_check = current_time

                # Enforce the schedule
                self._check_schedule()
                if not self.is_display_active:
                    time.sleep(60)
                    continue
                
                # Update data for all modules first
                self._update_modules()
                
                # Update live modes in rotation if needed
                self._update_live_modes_in_rotation()

                # Check for live games and live_priority
                has_live_games, live_sport_type = self._check_live_games()
                is_currently_live = self.current_display_mode.endswith('_live')
                # Determine if any sport has live_priority True and live games
                live_priority_takeover = False
                live_priority_sport = None
                for sport, attr, priority in [
                    ('nhl', 'nhl_live', self.nhl_live_priority),
                    ('nba', 'nba_live', self.nba_live_priority),
                    ('mlb', 'mlb_live', self.mlb_live_priority),
                    ('milb', 'milb_live', self.milb_live_priority),
                    ('soccer', 'soccer_live', self.soccer_live_priority),
                    ('nfl', 'nfl_live', self.nfl_live_priority),
                    ('ncaa_fb', 'ncaa_fb_live', self.ncaa_fb_live_priority),
                    ('ncaa_baseball', 'ncaa_baseball_live', self.ncaa_baseball_live_priority),
                    ('ncaam_basketball', 'ncaam_basketball_live', self.ncaam_basketball_live_priority)
                ]:
                    manager = getattr(self, attr, None)
                    if priority and manager and getattr(manager, 'live_games', None):
                        live_priority_takeover = True
                        live_priority_sport = sport
                        break
                manager_to_display = None
                # --- State Machine for Display Logic ---
                if is_currently_live:
                    if live_priority_takeover:
                        # Only allow takeover if live_priority is True for the sport
                        if current_time - self.last_switch >= self.get_current_duration():
                            new_mode = f"{live_priority_sport}_live"
                            if self.current_display_mode != new_mode:
                                logger.info(f"Switching to only active live sport: {new_mode} from {self.current_display_mode}")
                                self.current_display_mode = new_mode
                                self.force_clear = True
                            else:
                                self.force_clear = False
                            self.last_switch = current_time
                            manager_to_display = getattr(self, f"{live_priority_sport}_live", None)
                        else:
                            self.force_clear = False
                            current_sport_type = self.current_display_mode.replace('_live', '')
                            manager_to_display = getattr(self, f"{current_sport_type}_live", None)
                    else:
                        # If no sport has live_priority takeover, treat as regular rotation
                        is_currently_live = False
                if not is_currently_live:
                    previous_mode_before_switch = self.current_display_mode
                    if live_priority_takeover:
                        new_mode = f"{live_priority_sport}_live"
                        if self.current_display_mode != new_mode:
                            if previous_mode_before_switch == 'music' and self.music_manager:
                                self.music_manager.deactivate_music_display()
                            self.current_display_mode = new_mode
                            self.force_clear = True
                            self.last_switch = current_time
                            manager_to_display = getattr(self, f"{live_priority_sport}_live", None)
                        else:
                            self.force_clear = False
                            self.last_switch = current_time
                            manager_to_display = getattr(self, f"{live_priority_sport}_live", None)
                    else:
                        # No live_priority takeover, regular rotation
                        needs_switch = False
                        if self.current_display_mode.endswith('_live'):
                            needs_switch = True
                        elif current_time - self.last_switch >= self.get_current_duration():
                            if self.current_display_mode == 'calendar' and self.calendar:
                                self.calendar.advance_event()
                            elif self.current_display_mode == 'of_the_day' and self.of_the_day:
                                self.of_the_day.advance_item()
                            needs_switch = True
                            self.current_mode_index = (self.current_mode_index + 1) % len(self.available_modes)
                            new_mode_after_timer = self.available_modes[self.current_mode_index]
                            if previous_mode_before_switch == 'music' and self.music_manager and new_mode_after_timer != 'music':
                                self.music_manager.deactivate_music_display()
                            self.current_display_mode = new_mode_after_timer
                        if needs_switch:
                            self.force_clear = True
                            self.last_switch = current_time
                        else:
                            self.force_clear = False
                        # Select the manager for the current regular mode
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
                        elif self.current_display_mode == 'odds_ticker' and self.odds_ticker:
                            manager_to_display = self.odds_ticker
                        elif self.current_display_mode == 'calendar' and self.calendar:
                            manager_to_display = self.calendar
                        elif self.current_display_mode == 'youtube' and self.youtube:
                            manager_to_display = self.youtube
                        elif self.current_display_mode == 'text_display' and self.text_display:
                            manager_to_display = self.text_display
                        elif self.current_display_mode == 'of_the_day' and self.of_the_day:
                            manager_to_display = self.of_the_day
                        elif self.current_display_mode == 'news_manager' and self.news_manager:
                            manager_to_display = self.news_manager
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
                        elif self.current_display_mode == 'milb_recent' and self.milb_recent:
                            manager_to_display = self.milb_recent
                        elif self.current_display_mode == 'milb_upcoming' and self.milb_upcoming:
                            manager_to_display = self.milb_upcoming
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
                        elif self.current_display_mode == 'ncaa_baseball_recent' and self.ncaa_baseball_recent:
                            manager_to_display = self.ncaa_baseball_recent
                        elif self.current_display_mode == 'ncaa_baseball_upcoming' and self.ncaa_baseball_upcoming:
                            manager_to_display = self.ncaa_baseball_upcoming
                        elif self.current_display_mode == 'ncaam_basketball_recent' and self.ncaam_basketball_recent:
                            manager_to_display = self.ncaam_basketball_recent
                        elif self.current_display_mode == 'ncaam_basketball_upcoming' and self.ncaam_basketball_upcoming:
                            manager_to_display = self.ncaam_basketball_upcoming


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
                        elif self.current_display_mode == 'odds_ticker':
                             manager_to_display.display(force_clear=self.force_clear)
                        elif self.current_display_mode == 'calendar':
                             manager_to_display.display(force_clear=self.force_clear)
                        elif self.current_display_mode == 'youtube':
                             manager_to_display.display(force_clear=self.force_clear)
                        elif self.current_display_mode == 'text_display':
                             manager_to_display.display() # Assumes internal clearing
                        elif self.current_display_mode == 'of_the_day':
                             manager_to_display.display(force_clear=self.force_clear)
                        elif self.current_display_mode == 'news_manager':
                             manager_to_display.display_news()
                        elif self.current_display_mode == 'nfl_live' and self.nfl_live:
                            self.nfl_live.display(force_clear=self.force_clear)
                        elif self.current_display_mode == 'ncaa_fb_live' and self.ncaa_fb_live:
                            self.ncaa_fb_live.display(force_clear=self.force_clear)
                        elif self.current_display_mode == 'ncaam_basketball_live' and self.ncaam_basketball_live:
                            self.ncaam_basketball_live.display(force_clear=self.force_clear)
                        elif self.current_display_mode == 'ncaa_baseball_live' and self.ncaa_baseball_live:
                            self.ncaa_baseball_live.display(force_clear=self.force_clear)
                        elif self.current_display_mode == 'mlb_live' and self.mlb_live:
                            self.mlb_live.display(force_clear=self.force_clear)
                        elif self.current_display_mode == 'milb_live' and self.milb_live:
                            self.milb_live.display(force_clear=self.force_clear)
                        elif self.current_display_mode == 'ncaa_fb_upcoming' and self.ncaa_fb_upcoming:
                            self.ncaa_fb_upcoming.display(force_clear=self.force_clear)
                        elif self.current_display_mode == 'ncaam_basketball_recent' and self.ncaam_basketball_recent:
                            self.ncaam_basketball_recent.display(force_clear=self.force_clear)
                        elif self.current_display_mode == 'ncaam_basketball_upcoming' and self.ncaam_basketball_upcoming:
                            self.ncaam_basketball_upcoming.display(force_clear=self.force_clear)
                        elif self.current_display_mode == 'ncaa_baseball_recent' and self.ncaa_baseball_recent:
                            self.ncaa_baseball_recent.display(force_clear=self.force_clear)
                        elif self.current_display_mode == 'ncaa_baseball_upcoming' and self.ncaa_baseball_upcoming:
                            self.ncaa_baseball_upcoming.display(force_clear=self.force_clear)
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

                # Add a short sleep to prevent high CPU usage but ruin scrolling text
                # time.sleep(0.1)

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