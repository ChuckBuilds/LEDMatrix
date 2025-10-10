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
from src.cache_manager import CacheManager
from src.stock_manager import StockManager
from src.stock_news_manager import StockNewsManager
from src.odds_ticker_manager import OddsTickerManager
from src.leaderboard_manager import LeaderboardManager
from src.nhl_managers import NHLLiveManager, NHLRecentManager, NHLUpcomingManager
from src.nba_managers import NBALiveManager, NBARecentManager, NBAUpcomingManager
from src.wnba_managers import WNBALiveManager, WNBARecentManager, WNBAUpcomingManager
from src.mlb_manager import MLBLiveManager, MLBRecentManager, MLBUpcomingManager
from src.milb_manager import MiLBLiveManager, MiLBRecentManager, MiLBUpcomingManager
from src.soccer_managers import SoccerLiveManager, SoccerRecentManager, SoccerUpcomingManager
from src.nfl_managers import NFLLiveManager, NFLRecentManager, NFLUpcomingManager
from src.ncaa_fb_managers import NCAAFBLiveManager, NCAAFBRecentManager, NCAAFBUpcomingManager
from src.ncaa_baseball_managers import NCAABaseballLiveManager, NCAABaseballRecentManager, NCAABaseballUpcomingManager
from src.ncaam_basketball_managers import NCAAMBasketballLiveManager, NCAAMBasketballRecentManager, NCAAMBasketballUpcomingManager
from src.ncaaw_basketball_managers import NCAAWBasketballLiveManager, NCAAWBasketballRecentManager, NCAAWBasketballUpcomingManager
from src.ncaam_hockey_managers import NCAAMHockeyLiveManager, NCAAMHockeyRecentManager, NCAAMHockeyUpcomingManager
from src.ncaaw_hockey_managers import NCAAWHockeyLiveManager, NCAAWHockeyRecentManager, NCAAWHockeyUpcomingManager
from src.youtube_display import YouTubeDisplay
from src.calendar_manager import CalendarManager
from src.text_display import TextDisplay
from src.static_image_manager import StaticImageManager
from src.music_manager import MusicManager, SkipModuleException
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
        self.cache_manager = CacheManager()
        logger.info("Config loaded in %.3f seconds", time.time() - start_time)
        
        config_time = time.time()
        self.display_manager = DisplayManager(self.config)
        logger.info("DisplayManager initialized in %.3f seconds", time.time() - config_time)
        
        # Initialize display modes
        init_time = time.time()
        self.clock = Clock(self.display_manager, self.config) if self.config.get('clock', {}).get('enabled', True) else None
        self.weather = WeatherManager(self.config, self.display_manager) if self.config.get('weather', {}).get('enabled', False) else None
        self.stocks = StockManager(self.config, self.display_manager) if self.config.get('stocks', {}).get('enabled', False) else None
        self.news = StockNewsManager(self.config, self.display_manager) if self.config.get('stock_news', {}).get('enabled', False) else None
        self.odds_ticker = OddsTickerManager(self.config, self.display_manager) if self.config.get('odds_ticker', {}).get('enabled', False) else None
        self.leaderboard = LeaderboardManager(self.config, self.display_manager) if self.config.get('leaderboard', {}).get('enabled', False) else None
        self.calendar = CalendarManager(self.display_manager, self.config) if self.config.get('calendar', {}).get('enabled', False) else None
        self.youtube = YouTubeDisplay(self.display_manager, self.config) if self.config.get('youtube', {}).get('enabled', False) else None
        self.text_display = TextDisplay(self.display_manager, self.config) if self.config.get('text_display', {}).get('enabled', False) else None
        self.static_image = StaticImageManager(self.display_manager, self.config) if self.config.get('static_image', {}).get('enabled', False) else None
        self.of_the_day = OfTheDayManager(self.display_manager, self.config) if self.config.get('of_the_day', {}).get('enabled', False) else None
        self.news_manager = NewsManager(self.config, self.display_manager, self.config_manager) if self.config.get('news_manager', {}).get('enabled', False) else None
        logger.info(f"Calendar Manager initialized: {'Object' if self.calendar else 'None'}")
        logger.info(f"Text Display initialized: {'Object' if self.text_display else 'None'}")
        logger.info(f"Static Image Manager initialized: {'Object' if self.static_image else 'None'}")
        logger.info(f"OfTheDay Manager initialized: {'Object' if self.of_the_day else 'None'}")
        logger.info(f"News Manager initialized: {'Object' if self.news_manager else 'None'}")
        logger.info("Display modes initialized in %.3f seconds", time.time() - init_time)
        
        self.force_change = False
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
            self.nhl_live = NHLLiveManager(self.config, self.display_manager, self.cache_manager) if nhl_display_modes.get('nhl_live', True) else None
            self.nhl_recent = NHLRecentManager(self.config, self.display_manager, self.cache_manager) if nhl_display_modes.get('nhl_recent', True) else None
            self.nhl_upcoming = NHLUpcomingManager(self.config, self.display_manager, self.cache_manager) if nhl_display_modes.get('nhl_upcoming', True) else None
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
            self.nba_live = NBALiveManager(self.config, self.display_manager, self.cache_manager) if nba_display_modes.get('nba_live', True) else None
            self.nba_recent = NBARecentManager(self.config, self.display_manager, self.cache_manager) if nba_display_modes.get('nba_recent', True) else None
            self.nba_upcoming = NBAUpcomingManager(self.config, self.display_manager, self.cache_manager) if nba_display_modes.get('nba_upcoming', True) else None
        else:
            self.nba_live = None
            self.nba_recent = None
            self.nba_upcoming = None
        logger.info("NBA managers initialized in %.3f seconds", time.time() - nba_time)
            
        # Initialize WNBA managers if enabled
        wnba_time = time.time()
        wnba_enabled = self.config.get('wnba_scoreboard', {}).get('enabled', False)
        wnba_display_modes = self.config.get('wnba_scoreboard', {}).get('display_modes', {})
        
        if wnba_enabled:
            self.wnba_live = WNBALiveManager(self.config, self.display_manager, self.cache_manager) if wnba_display_modes.get('wnba_live', True) else None
            self.wnba_recent = WNBARecentManager(self.config, self.display_manager, self.cache_manager) if wnba_display_modes.get('wnba_recent', True) else None
            self.wnba_upcoming = WNBAUpcomingManager(self.config, self.display_manager, self.cache_manager) if wnba_display_modes.get('wnba_upcoming', True) else None
        else:
            self.wnba_live = None
            self.wnba_recent = None
            self.wnba_upcoming = None
        logger.info("WNBA managers initialized in %.3f seconds", time.time() - wnba_time)

        # Initialize MLB managers if enabled
        mlb_time = time.time()
        mlb_enabled = self.config.get('mlb_scoreboard', {}).get('enabled', False)
        mlb_display_modes = self.config.get('mlb_scoreboard', {}).get('display_modes', {})
        
        if mlb_enabled:
            self.mlb_live = MLBLiveManager(self.config, self.display_manager, self.cache_manager) if mlb_display_modes.get('mlb_live', True) else None
            self.mlb_recent = MLBRecentManager(self.config, self.display_manager, self.cache_manager) if mlb_display_modes.get('mlb_recent', True) else None
            self.mlb_upcoming = MLBUpcomingManager(self.config, self.display_manager, self.cache_manager) if mlb_display_modes.get('mlb_upcoming', True) else None
        else:
            self.mlb_live = None
            self.mlb_recent = None
            self.mlb_upcoming = None
        logger.info("MLB managers initialized in %.3f seconds", time.time() - mlb_time)

        # Initialize MiLB managers if enabled
        milb_time = time.time()
        milb_enabled = self.config.get('milb_scoreboard', {}).get('enabled', False)
        milb_display_modes = self.config.get('milb_scoreboard', {}).get('display_modes', {})
        
        if milb_enabled:
            self.milb_live = MiLBLiveManager(self.config, self.display_manager, self.cache_manager) if milb_display_modes.get('milb_live', True) else None
            self.milb_recent = MiLBRecentManager(self.config, self.display_manager, self.cache_manager) if milb_display_modes.get('milb_recent', True) else None
            self.milb_upcoming = MiLBUpcomingManager(self.config, self.display_manager, self.cache_manager) if milb_display_modes.get('milb_upcoming', True) else None
            logger.info(f"MiLB managers initialized - live: {self.milb_live is not None}, recent: {self.milb_recent is not None}, upcoming: {self.milb_upcoming is not None}")
        else:
            self.milb_live = None
            self.milb_recent = None
            self.milb_upcoming = None
            logger.info("MiLB managers disabled")
        logger.info("MiLB managers initialized in %.3f seconds", time.time() - milb_time)
            
        # Initialize Soccer managers if enabled
        soccer_time = time.time()
        soccer_enabled = self.config.get('soccer_scoreboard', {}).get('enabled', False)
        soccer_display_modes = self.config.get('soccer_scoreboard', {}).get('display_modes', {})
        
        if soccer_enabled:
            self.soccer_live = SoccerLiveManager(self.config, self.display_manager, self.cache_manager) if soccer_display_modes.get('soccer_live', True) else None
            self.soccer_recent = SoccerRecentManager(self.config, self.display_manager, self.cache_manager) if soccer_display_modes.get('soccer_recent', True) else None
            self.soccer_upcoming = SoccerUpcomingManager(self.config, self.display_manager, self.cache_manager) if soccer_display_modes.get('soccer_upcoming', True) else None
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
            self.nfl_live = NFLLiveManager(self.config, self.display_manager, self.cache_manager) if nfl_display_modes.get('nfl_live', True) else None
            self.nfl_recent = NFLRecentManager(self.config, self.display_manager, self.cache_manager) if nfl_display_modes.get('nfl_recent', True) else None
            self.nfl_upcoming = NFLUpcomingManager(self.config, self.display_manager, self.cache_manager) if nfl_display_modes.get('nfl_upcoming', True) else None
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
            self.ncaa_fb_live = NCAAFBLiveManager(self.config, self.display_manager, self.cache_manager) if ncaa_fb_display_modes.get('ncaa_fb_live', True) else None
            self.ncaa_fb_recent = NCAAFBRecentManager(self.config, self.display_manager, self.cache_manager) if ncaa_fb_display_modes.get('ncaa_fb_recent', True) else None
            self.ncaa_fb_upcoming = NCAAFBUpcomingManager(self.config, self.display_manager, self.cache_manager) if ncaa_fb_display_modes.get('ncaa_fb_upcoming', True) else None
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
            self.ncaa_baseball_live = NCAABaseballLiveManager(self.config, self.display_manager, self.cache_manager) if ncaa_baseball_display_modes.get('ncaa_baseball_live', True) else None
            self.ncaa_baseball_recent = NCAABaseballRecentManager(self.config, self.display_manager, self.cache_manager) if ncaa_baseball_display_modes.get('ncaa_baseball_recent', True) else None
            self.ncaa_baseball_upcoming = NCAABaseballUpcomingManager(self.config, self.display_manager, self.cache_manager) if ncaa_baseball_display_modes.get('ncaa_baseball_upcoming', True) else None
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
            self.ncaam_basketball_live = NCAAMBasketballLiveManager(self.config, self.display_manager, self.cache_manager) if ncaam_basketball_display_modes.get('ncaam_basketball_live', True) else None
            self.ncaam_basketball_recent = NCAAMBasketballRecentManager(self.config, self.display_manager, self.cache_manager) if ncaam_basketball_display_modes.get('ncaam_basketball_recent', True) else None
            self.ncaam_basketball_upcoming = NCAAMBasketballUpcomingManager(self.config, self.display_manager, self.cache_manager) if ncaam_basketball_display_modes.get('ncaam_basketball_upcoming', True) else None
        else:
            self.ncaam_basketball_live = None
            self.ncaam_basketball_recent = None
            self.ncaam_basketball_upcoming = None
        logger.info("NCAA Men's Basketball managers initialized in %.3f seconds", time.time() - ncaam_basketball_time)

        # Initialize NCAA Womens's Basketball managers if enabled
        ncaaw_basketball_time = time.time()
        ncaaw_basketball_enabled = self.config.get('ncaaw_basketball_scoreboard', {}).get('enabled', False)
        ncaaw_basketball_display_modes = self.config.get('ncaaw_basketball_scoreboard', {}).get('display_modes', {})
        
        if ncaaw_basketball_enabled:
            self.ncaaw_basketball_live = NCAAWBasketballLiveManager(self.config, self.display_manager, self.cache_manager) if ncaaw_basketball_display_modes.get('ncaaw_basketball_live', True) else None
            self.ncaaw_basketball_recent = NCAAWBasketballRecentManager(self.config, self.display_manager, self.cache_manager) if ncaaw_basketball_display_modes.get('ncaaw_basketball_recent', True) else None
            self.ncaaw_basketball_upcoming = NCAAWBasketballUpcomingManager(self.config, self.display_manager, self.cache_manager) if ncaaw_basketball_display_modes.get('ncaaw_basketball_upcoming', True) else None
        else:
            self.ncaaw_basketball_live = None
            self.ncaaw_basketball_recent = None
            self.ncaaw_basketball_upcoming = None
        logger.info("NCAA Womens's Basketball managers initialized in %.3f seconds", time.time() - ncaaw_basketball_time)

        # Initialize NCAA Men's Hockey managers if enabled
        ncaam_hockey_time = time.time()
        ncaam_hockey_enabled = self.config.get('ncaam_hockey_scoreboard', {}).get('enabled', False)
        ncaam_hockey_display_modes = self.config.get('ncaam_hockey_scoreboard', {}).get('display_modes', {})
        
        if ncaam_hockey_enabled:
            self.ncaam_hockey_live = NCAAMHockeyLiveManager(self.config, self.display_manager, self.cache_manager) if ncaam_hockey_display_modes.get('ncaam_hockey_live', True) else None
            self.ncaam_hockey_recent = NCAAMHockeyRecentManager(self.config, self.display_manager, self.cache_manager) if ncaam_hockey_display_modes.get('ncaam_hockey_recent', True) else None
            self.ncaam_hockey_upcoming = NCAAMHockeyUpcomingManager(self.config, self.display_manager, self.cache_manager) if ncaam_hockey_display_modes.get('ncaam_hockey_upcoming', True) else None
        else:
            self.ncaam_hockey_live = None
            self.ncaam_hockey_recent = None
            self.ncaam_hockey_upcoming = None
        logger.info("NCAA Men's Hockey managers initialized in %.3f seconds", time.time() - ncaam_hockey_time)

        # Initialize NCAA Men's Hockey managers if enabled
        ncaaw_hockey_time = time.time()
        ncaaw_hockey_enabled = self.config.get('ncaaw_hockey_scoreboard', {}).get('enabled', False)
        ncaaw_hockey_display_modes = self.config.get('ncaaw_hockey_scoreboard', {}).get('display_modes', {})
        
        if ncaaw_hockey_enabled:
            self.ncaaw_hockey_live = NCAAWHockeyLiveManager(self.config, self.display_manager, self.cache_manager) if ncaaw_hockey_display_modes.get('ncaaw_hockey_live', True) else None
            self.ncaaw_hockey_recent = NCAAWHockeyRecentManager(self.config, self.display_manager, self.cache_manager) if ncaaw_hockey_display_modes.get('ncaaw_hockey_recent', True) else None
            self.ncaaw_hockey_upcoming = NCAAWHockeyUpcomingManager(self.config, self.display_manager, self.cache_manager) if ncaaw_hockey_display_modes.get('ncaaw_hockey_upcoming', True) else None
        else:
            self.ncaaw_hockey_live = None
            self.ncaaw_hockey_recent = None
            self.ncaaw_hockey_upcoming = None
        logger.info("NCAA Men's Hockey managers initialized in %.3f seconds", time.time() - ncaaw_hockey_time)
        
        # Track MLB rotation state
        self.mlb_current_team_index = 0
        self.mlb_showing_recent = True
        self.mlb_favorite_teams = self.config.get('mlb_scoreboard', {}).get('favorite_teams', [])
        self.in_mlb_rotation = False
        
        # Read live_priority flags for all sports
        self.nhl_live_priority = self.config.get('nhl_scoreboard', {}).get('live_priority', True)
        self.nba_live_priority = self.config.get('nba_scoreboard', {}).get('live_priority', True)
        self.wnba_live_priority = self.config.get('wnba_scoreboard', {}).get('live_priority', True)
        self.mlb_live_priority = self.config.get('mlb_scoreboard', {}).get('live_priority', True)
        self.milb_live_priority = self.config.get('milb_scoreboard', {}).get('live_priority', True)
        self.soccer_live_priority = self.config.get('soccer_scoreboard', {}).get('live_priority', True)
        self.nfl_live_priority = self.config.get('nfl_scoreboard', {}).get('live_priority', True)
        self.ncaa_fb_live_priority = self.config.get('ncaa_fb_scoreboard', {}).get('live_priority', True)
        self.ncaa_baseball_live_priority = self.config.get('ncaa_baseball_scoreboard', {}).get('live_priority', True)
        self.ncaam_basketball_live_priority = self.config.get('ncaam_basketball_scoreboard', {}).get('live_priority', True)
        self.ncaaw_basketball_live_priority = self.config.get('ncaaw_basketball_scoreboard', {}).get('live_priority', True)
        self.ncaam_hockey_live_priority = self.config.get('ncaam_hockey_scoreboard', {}).get('live_priority', True)
        self.ncaaw_hockey_live_priority = self.config.get('ncaaw_hockey_scoreboard', {}).get('live_priority', True)
        self.music_live_priority = self.config.get('music', {}).get('live_priority', True)
        
        # List of available display modes (adjust order as desired)
        self.available_modes = []
        if self.clock: self.available_modes.append('clock')
        if self.weather: self.available_modes.extend(['weather_current', 'weather_hourly', 'weather_daily'])
        if self.stocks: self.available_modes.append('stocks')
        if self.news: self.available_modes.append('stock_news')
        if self.odds_ticker: self.available_modes.append('odds_ticker')
        if self.leaderboard: self.available_modes.append('leaderboard')
        if self.calendar: self.available_modes.append('calendar')
        if self.youtube: self.available_modes.append('youtube')
        if self.text_display: self.available_modes.append('text_display')
        if self.static_image: self.available_modes.append('static_image')
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
        if wnba_enabled:
            if self.wnba_recent: self.available_modes.append('wnba_recent')
            if self.wnba_upcoming: self.available_modes.append('wnba_upcoming')
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
        if ncaaw_basketball_enabled:
            if self.ncaaw_basketball_recent: self.available_modes.append('ncaaw_basketball_recent')
            if self.ncaaw_basketball_upcoming: self.available_modes.append('ncaaw_basketball_upcoming')
        if ncaam_hockey_enabled:
            if self.ncaam_hockey_recent: self.available_modes.append('ncaam_hockey_recent')
            if self.ncaam_hockey_upcoming: self.available_modes.append('ncaam_hockey_upcoming')
        if ncaaw_hockey_enabled:
            if self.ncaaw_hockey_recent: self.available_modes.append('ncaaw_hockey_recent')
            if self.ncaaw_hockey_upcoming: self.available_modes.append('ncaaw_hockey_upcoming')
        # Add live modes to rotation if live_priority is False and there are live games
        self._update_live_modes_in_rotation()
        
        # Set initial display to first available mode (clock)
        self.current_mode_index = 0
        self.current_display_mode = "none"
        # Reset logged duration when mode is initialized
        if hasattr(self, '_last_logged_duration'):
            delattr(self, '_last_logged_duration')
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
        
        self.wnba_current_team_index = 0
        self.wnba_showing_recent = True
        self.wnba_favorite_teams = self.config.get('wnba_scoreboard', {}).get('favorite_teams', [])
        self.in_wnba_rotation = False
        
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

        # Add NCAA Womens's Basketball rotation state
        self.ncaaw_basketball_current_team_index = 0
        self.ncaaw_basketball_showing_recent = True
        self.ncaaw_basketball_favorite_teams = self.config.get('ncaaw_basketball_scoreboard', {}).get('favorite_teams', [])
        self.in_ncaaw_basketball_rotation = False
        
        # Update display durations to include all modes
        self.display_durations = self.config['display'].get('display_durations', {})
        # Backward-compatibility: map legacy weather keys to current keys if provided in config
        try:
            if 'weather' in self.display_durations and 'weather_current' not in self.display_durations:
                self.display_durations['weather_current'] = self.display_durations['weather']
            if 'hourly_forecast' in self.display_durations and 'weather_hourly' not in self.display_durations:
                self.display_durations['weather_hourly'] = self.display_durations['hourly_forecast']
            if 'daily_forecast' in self.display_durations and 'weather_daily' not in self.display_durations:
                self.display_durations['weather_daily'] = self.display_durations['daily_forecast']
        except Exception:
            pass
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
            'wnba_live': 30,
            'wnba_recent': 20,
            'wnba_upcoming': 20,
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
            'music_live': 30, # Default duration for music live mode
            'ncaa_baseball_live': 30, # Added NCAA Baseball durations
            'ncaa_baseball_recent': 15,
            'ncaa_baseball_upcoming': 15,
            'ncaam_basketball_live': 30, # Added NCAA Men's Basketball durations
            'ncaam_basketball_recent': 15,
            'ncaam_basketball_upcoming': 15,
            'ncaaw_basketball_live': 30, # Added NCAA Womens's Basketball durations
            'ncaaw_basketball_recent': 15,
            'ncaaw_basketball_upcoming': 15,
            'ncaam_hockey_live': 30, # Added NCAA Men's Hockey durations
            'ncaam_hockey_recent': 15,
            'ncaam_hockey_upcoming': 15,
            'ncaaw_hockey_live': 30, # Added NCAA Men's Hockey durations
            'ncaaw_hockey_recent': 15,
            'ncaaw_hockey_upcoming': 15
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
        if wnba_enabled:
            logger.info(f"WNBA Favorite teams: {self.wnba_favorite_teams}")
        if mlb_enabled:
            logger.info(f"MLB Favorite teams: {self.mlb_favorite_teams}")
        if milb_enabled:
            logger.info(f"MiLB Favorite teams: {self.config.get('milb_scoreboard', {}).get('favorite_teams', [])}")
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
        if ncaaw_basketball_enabled: # Check if NCAA Womens's Basketball is enabled
            logger.info(f"NCAA Womens's Basketball Favorite teams: {self.ncaaw_basketball_favorite_teams}")

        logger.info(f"Available display modes: {self.available_modes}")
        logger.info(f"Initial display mode: {self.current_display_mode}")
        logger.info("DisplayController initialized with display_manager: %s", id(self.display_manager))

        # --- SCHEDULING ---
        self.is_display_active = True
        self._load_schedule_config() # Load schedule config once at startup

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

        # Handle dynamic duration for news manager
        if mode_key == 'news_manager' and self.news_manager:
            try:
                dynamic_duration = self.news_manager.get_dynamic_duration()
                # Only log if duration has changed or we haven't logged this duration yet
                if not hasattr(self, '_last_logged_duration') or self._last_logged_duration != dynamic_duration:
                    logger.info(f"Using dynamic duration for news_manager: {dynamic_duration} seconds")
                    self._last_logged_duration = dynamic_duration
                return dynamic_duration
            except Exception as e:
                logger.error(f"Error getting dynamic duration for news_manager: {e}")
                # Fall back to configured duration
                return self.display_durations.get(mode_key, 60)

        # Handle dynamic duration for stocks
        elif mode_key == 'stocks' and self.stocks:
            try:
                dynamic_duration = self.stocks.get_dynamic_duration()
                # Only log if duration has changed or we haven't logged this duration yet
                if not hasattr(self, '_last_logged_duration') or self._last_logged_duration != dynamic_duration:
                    logger.info(f"Using dynamic duration for stocks: {dynamic_duration} seconds")
                    self._last_logged_duration = dynamic_duration
                return dynamic_duration
            except Exception as e:
                logger.error(f"Error getting dynamic duration for stocks: {e}")
                # Fall back to configured duration
                return self.display_durations.get(mode_key, 60)

        # Handle dynamic duration for stock_news
        elif mode_key == 'stock_news' and self.news:
            try:
                dynamic_duration = self.news.get_dynamic_duration()
                # Only log if duration has changed or we haven't logged this duration yet
                if not hasattr(self, '_last_logged_duration') or self._last_logged_duration != dynamic_duration:
                    logger.info(f"Using dynamic duration for stock_news: {dynamic_duration} seconds")
                    self._last_logged_duration = dynamic_duration
                return dynamic_duration
            except Exception as e:
                logger.error(f"Error getting dynamic duration for stock_news: {e}")
                # Fall back to configured duration
                return self.display_durations.get(mode_key, 60)

        # Handle dynamic duration for odds_ticker
        elif mode_key == 'odds_ticker' and self.odds_ticker:
            try:
                dynamic_duration = self.odds_ticker.get_dynamic_duration()
                # Only log if duration has changed or we haven't logged this duration yet
                if not hasattr(self, '_last_logged_duration') or self._last_logged_duration != dynamic_duration:
                    logger.info(f"Using dynamic duration for odds_ticker: {dynamic_duration} seconds")
                    self._last_logged_duration = dynamic_duration
                return dynamic_duration
            except Exception as e:
                logger.error(f"Error getting dynamic duration for odds_ticker: {e}")
                # Fall back to configured duration
                return self.display_durations.get(mode_key, 60)

        # Handle leaderboard duration (user choice between fixed or dynamic)
        elif mode_key == 'leaderboard' and self.leaderboard:
            try:
                duration = self.leaderboard.get_duration()
                mode_type = "dynamic" if self.leaderboard.dynamic_duration else "fixed"
                # Only log if duration has changed or we haven't logged this duration yet
                if not hasattr(self, '_last_logged_leaderboard_duration') or self._last_logged_leaderboard_duration != duration:
                    logger.info(f"Using leaderboard {mode_type} duration: {duration} seconds")
                    self._last_logged_leaderboard_duration = duration
                return duration
            except Exception as e:
                logger.error(f"Error getting duration for leaderboard: {e}")
                # Fall back to configured duration
                return self.display_durations.get(mode_key, 600)

        # Simplify weather key handling
        elif mode_key.startswith('weather_'):
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
        # Check if we're currently scrolling and defer updates if so
        if self.display_manager.is_currently_scrolling():
            logger.debug("Display is currently scrolling, deferring module updates")
            # Defer updates for modules that might cause lag during scrolling
            if self.odds_ticker: 
                self.display_manager.defer_update(self.odds_ticker.update, priority=1)
            if self.leaderboard:
                self.display_manager.defer_update(self.leaderboard.update, priority=1)
            if self.stocks: 
                self.display_manager.defer_update(self.stocks.update_stock_data, priority=2)
            if self.news: 
                self.display_manager.defer_update(self.news.update_news_data, priority=2)
            # Defer sport manager updates that might do heavy API fetching
            if hasattr(self, 'ncaa_fb_live') and self.ncaa_fb_live:
                self.display_manager.defer_update(self.ncaa_fb_live.update, priority=3)
            if hasattr(self, 'ncaa_fb_recent') and self.ncaa_fb_recent:
                self.display_manager.defer_update(self.ncaa_fb_recent.update, priority=3)
            if hasattr(self, 'ncaa_fb_upcoming') and self.ncaa_fb_upcoming:
                self.display_manager.defer_update(self.ncaa_fb_upcoming.update, priority=3)
            if hasattr(self, 'nfl_live') and self.nfl_live:
                self.display_manager.defer_update(self.nfl_live.update, priority=3)
            if hasattr(self, 'nfl_recent') and self.nfl_recent:
                self.display_manager.defer_update(self.nfl_recent.update, priority=3)
            if hasattr(self, 'nfl_upcoming') and self.nfl_upcoming:
                self.display_manager.defer_update(self.nfl_upcoming.update, priority=3)
            # Continue with non-scrolling-sensitive updates
            if self.weather: self.weather.get_weather()
            if self.calendar: self.calendar.update(time.time())
            if self.youtube: self.youtube.update()
            if self.text_display: self.text_display.update()
            if self.static_image: self.static_image.update()
            if self.of_the_day: self.of_the_day.update(time.time())
        else:
            # Not scrolling, perform all updates normally
            if self.weather: self.weather.get_weather()
            if self.stocks: self.stocks.update_stock_data()
            if self.news: self.news.update_news_data()
            if self.odds_ticker: self.odds_ticker.update()
            if self.calendar: self.calendar.update(time.time())
            if self.youtube: self.youtube.update()
            if self.text_display: self.text_display.update()
            if self.static_image: self.static_image.update()
            if self.of_the_day: self.of_the_day.update(time.time())
            
            # Update sports managers for leaderboard data
            if self.leaderboard: self.leaderboard.update()
            
            # Update key sports managers that feed the leaderboard
            if self.nfl_live: self.nfl_live.update()
            if self.nfl_recent: self.nfl_recent.update()
            if self.nfl_upcoming: self.nfl_upcoming.update()
            if self.ncaa_fb_live: self.ncaa_fb_live.update()
            if self.ncaa_fb_recent: self.ncaa_fb_recent.update()
            if self.ncaa_fb_upcoming: self.ncaa_fb_upcoming.update()
        
        # News manager fetches data when displayed, not during updates
        # if self.news_manager: self.news_manager.fetch_news_data()
        
        # Only update the currently active sport manager to prevent confusing logs
        # and reduce unnecessary API calls
        current_sport = None
        if self.current_display_mode.endswith('_live'):
            current_sport = self.current_display_mode.replace('_live', '')
        elif self.current_display_mode.endswith('_recent'):
            current_sport = self.current_display_mode.replace('_recent', '')
        elif self.current_display_mode.endswith('_upcoming'):
            current_sport = self.current_display_mode.replace('_upcoming', '')
        
        # Update only the currently active sport manager
        if current_sport == 'nhl':
            if self.nhl_live: self.nhl_live.update()
            if self.nhl_recent: self.nhl_recent.update()
            if self.nhl_upcoming: self.nhl_upcoming.update()
        elif current_sport == 'nba':
            if self.nba_live: self.nba_live.update()
            if self.nba_recent: self.nba_recent.update()
            if self.nba_upcoming: self.nba_upcoming.update()
        elif current_sport == 'wnba':
            if self.wnba_live: self.wnba_live.update()
            if self.wnba_recent: self.wnba_recent.update()
            if self.wnba_upcoming: self.wnba_upcoming.update()
        elif current_sport == 'mlb':
            if self.mlb_live: self.mlb_live.update()
            if self.mlb_recent: self.mlb_recent.update()
            if self.mlb_upcoming: self.mlb_upcoming.update()
        elif current_sport == 'milb':
            if self.milb_live: self.milb_live.update()
            if self.milb_recent: self.milb_recent.update()
            if self.milb_upcoming: self.milb_upcoming.update()
        elif current_sport == 'soccer':
            if self.soccer_live: self.soccer_live.update()
            if self.soccer_recent: self.soccer_recent.update()
            if self.soccer_upcoming: self.soccer_upcoming.update()
        elif current_sport == 'nfl':
            if self.nfl_live: self.nfl_live.update()
            if self.nfl_recent: self.nfl_recent.update()
            if self.nfl_upcoming: self.nfl_upcoming.update()
        elif current_sport == 'ncaa_fb':
            if self.ncaa_fb_live: self.ncaa_fb_live.update()
            if self.ncaa_fb_recent: self.ncaa_fb_recent.update()
            if self.ncaa_fb_upcoming: self.ncaa_fb_upcoming.update()
        elif current_sport == 'ncaa_baseball':
            if self.ncaa_baseball_live: self.ncaa_baseball_live.update()
            if self.ncaa_baseball_recent: self.ncaa_baseball_recent.update()
            if self.ncaa_baseball_upcoming: self.ncaa_baseball_upcoming.update()
        elif current_sport == 'ncaam_basketball':
            if self.ncaam_basketball_live: self.ncaam_basketball_live.update()
            if self.ncaam_basketball_recent: self.ncaam_basketball_recent.update()
            if self.ncaam_basketball_upcoming: self.ncaam_basketball_upcoming.update()
        elif current_sport == 'ncaaw_basketball':
            if self.ncaaw_basketball_live: self.ncaaw_basketball_live.update()
            if self.ncaaw_basketball_recent: self.ncaaw_basketball_recent.update()
            if self.ncaaw_basketball_upcoming: self.ncaaw_basketball_upcoming.update()
        elif current_sport == 'ncaam_hockey':
            if self.ncaam_hockey_live: self.ncaam_hockey_live.update()
            if self.ncaam_hockey_recent: self.ncaam_hockey_recent.update()
            if self.ncaam_hockey_upcoming: self.ncaam_hockey_upcoming.update()
        elif current_sport == 'ncaaw_hockey':
            if self.ncaaw_hockey_live: self.ncaaw_hockey_live.update()
            if self.ncaaw_hockey_recent: self.ncaaw_hockey_recent.update()
            if self.ncaaw_hockey_upcoming: self.ncaaw_hockey_upcoming.update()
        else:
            # If no specific sport is active, update all managers (fallback behavior)
            # This ensures data is available when switching to a sport
            if self.nhl_live: self.nhl_live.update()
            if self.nhl_recent: self.nhl_recent.update()
            if self.nhl_upcoming: self.nhl_upcoming.update()
            
            if self.nba_live: self.nba_live.update()
            if self.nba_recent: self.nba_recent.update()
            if self.nba_upcoming: self.nba_upcoming.update()
            
            if self.wnba_live: self.wnba_live.update()
            if self.wnba_recent: self.wnba_recent.update()
            if self.wnba_upcoming: self.wnba_upcoming.update()
            
            if self.mlb_live: self.mlb_live.update()
            if self.mlb_recent: self.mlb_recent.update()
            if self.mlb_upcoming: self.mlb_upcoming.update()
            
            if self.milb_live: self.milb_live.update()
            if self.milb_recent: self.milb_recent.update()
            if self.milb_upcoming: self.milb_upcoming.update()
            
            if self.soccer_live: self.soccer_live.update()
            if self.soccer_recent: self.soccer_recent.update()
            if self.soccer_upcoming: self.soccer_upcoming.update()

            if self.nfl_live: self.nfl_live.update()
            if self.nfl_recent: self.nfl_recent.update()
            if self.nfl_upcoming: self.nfl_upcoming.update()

            if self.ncaa_fb_live: self.ncaa_fb_live.update()
            if self.ncaa_fb_recent: self.ncaa_fb_recent.update()
            if self.ncaa_fb_upcoming: self.ncaa_fb_upcoming.update()

            if self.ncaa_baseball_live: self.ncaa_baseball_live.update()
            if self.ncaa_baseball_recent: self.ncaa_baseball_recent.update()
            if self.ncaa_baseball_upcoming: self.ncaa_baseball_upcoming.update()

            if self.ncaam_basketball_live: self.ncaam_basketball_live.update()
            if self.ncaam_basketball_recent: self.ncaam_basketball_recent.update()
            if self.ncaam_basketball_upcoming: self.ncaam_basketball_upcoming.update()

            if self.ncaaw_basketball_live: self.ncaaw_basketball_live.update()
            if self.ncaaw_basketball_recent: self.ncaaw_basketball_recent.update()
            if self.ncaaw_basketball_upcoming: self.ncaaw_basketball_upcoming.update()

            if self.ncaam_hockey_live: self.ncaam_hockey_live.update()
            if self.ncaam_hockey_recent: self.ncaam_hockey_recent.update()
            if self.ncaam_hockey_upcoming: self.ncaam_hockey_upcoming.update()

            if self.ncaaw_hockey_live: self.ncaaw_hockey_live.update()
            if self.ncaaw_hockey_recent: self.ncaaw_hockey_recent.update()
            if self.ncaaw_hockey_upcoming: self.ncaaw_hockey_upcoming.update()

    def _check_live_games(self) -> tuple:
        """
        Check if there are any live games available.
        Returns:
            tuple: (has_live_games, sport_type)
            sport_type will be 'nhl', 'nba', 'mlb', 'milb', 'soccer' or None
        """
        # Only include sports that are enabled in config
        live_checks = {}
        if 'nhl_scoreboard' in self.config and self.config['nhl_scoreboard'].get('enabled', False):
            live_checks['nhl'] = self.nhl_live and self.nhl_live.live_games
        if 'nba_scoreboard' in self.config and self.config['nba_scoreboard'].get('enabled', False):
            live_checks['nba'] = self.nba_live and self.nba_live.live_games
        if 'wnba_scoreboard' in self.config and self.config['wnba_scoreboard'].get('enabled', False):
            live_checks['wnba'] = self.wnba_live and self.wnba_live.live_games
        if 'mlb' in self.config and self.config['mlb'].get('enabled', False):
            live_checks['mlb'] = self.mlb_live and self.mlb_live.live_games
        if 'milb' in self.config and self.config['milb'].get('enabled', False):
            live_checks['milb'] = self.milb_live and self.milb_live.live_games
        if 'nfl_scoreboard' in self.config and self.config['nfl_scoreboard'].get('enabled', False):
            live_checks['nfl'] = self.nfl_live and self.nfl_live.live_games
        if 'soccer_scoreboard' in self.config and self.config['soccer_scoreboard'].get('enabled', False):
            live_checks['soccer'] = self.soccer_live and self.soccer_live.live_games
        if 'ncaa_fb_scoreboard' in self.config and self.config['ncaa_fb_scoreboard'].get('enabled', False):
            live_checks['ncaa_fb'] = self.ncaa_fb_live and self.ncaa_fb_live.live_games
        if 'ncaa_baseball_scoreboard' in self.config and self.config['ncaa_baseball_scoreboard'].get('enabled', False):
            live_checks['ncaa_baseball'] = self.ncaa_baseball_live and self.ncaa_baseball_live.live_games
        if 'ncaam_basketball_scoreboard' in self.config and self.config['ncaam_basketball_scoreboard'].get('enabled', False):
            live_checks['ncaam_basketball'] = self.ncaam_basketball_live and self.ncaam_basketball_live.live_games
        if 'ncaaw_basketball_scoreboard' in self.config and self.config['ncaaw_basketball_scoreboard'].get('enabled', False):
            live_checks['ncaaw_basketball'] = self.ncaaw_basketball_live and self.ncaaw_basketball_live.live_games
        if 'ncaam_hockey_scoreboard' in self.config and self.config['ncaam_hockey_scoreboard'].get('enabled', False):
            live_checks['ncaam_hockey'] = self.ncaam_hockey_live and self.ncaam_hockey_live.live_games
        if 'ncaaw_hockey_scoreboard' in self.config and self.config['ncaaw_hockey_scoreboard'].get('enabled', False):
            live_checks['ncaaw_hockey'] = self.ncaaw_hockey_live and self.ncaaw_hockey_live.live_games

        for sport, has_live_games in live_checks.items():
            if has_live_games:
                logger.debug(f"{sport.upper()} live games available")
                return True, sport
            
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
        elif sport == 'wnba':
            manager_recent = self.wnba_recent
            manager_upcoming = self.wnba_upcoming
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
        elif sport == 'wnba':
            favorite_teams = self.wnba_favorite_teams
            manager_recent = self.wnba_recent
            manager_upcoming = self.wnba_upcoming
        elif sport == 'mlb':
            favorite_teams = self.mlb_favorite_teams
            manager_recent = self.mlb_recent
            manager_upcoming = self.mlb_upcoming
        elif sport == 'milb':
            favorite_teams = self.config.get('milb_scoreboard', {}).get('favorite_teams', [])
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

    # --- SCHEDULING METHODS ---
    def _load_schedule_config(self):
        """Load schedule configuration once at startup."""
        schedule_config = self.config.get('schedule', {})
        self.schedule_enabled = schedule_config.get('enabled', False)
        try:
            self.start_time = datetime.strptime(schedule_config.get('start_time', '07:00'), '%H:%M').time()
            self.end_time = datetime.strptime(schedule_config.get('end_time', '22:00'), '%H:%M').time()
            logger.info(f"Schedule loaded: enabled={self.schedule_enabled}, start={self.start_time}, end={self.end_time}")
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
        def update_mode(mode_name, manager, live_priority, sport_enabled):
            # Only process if the sport is enabled in config
            if not sport_enabled:
                # If sport is disabled, ensure the mode is removed from rotation
                if mode_name in self.available_modes:
                    self.available_modes.remove(mode_name)
                return

            if not live_priority:
                # Only add to rotation if manager exists and has live games/content
                if manager:
                    # Special handling for music manager - check if music is actively playing
                    if mode_name == 'music_live':
                        has_live_content = self._music_has_live_content()
                        logger.debug(f"Music live mode update: mode_name={mode_name}, has_live_content={has_live_content}, in_available_modes={mode_name in self.available_modes}")
                        if has_live_content and mode_name not in self.available_modes:
                            self.available_modes.append(mode_name)
                            logger.info(f"Added {mode_name} to rotation (music is playing)")
                        elif not has_live_content and mode_name in self.available_modes:
                            self.available_modes.remove(mode_name)
                            logger.info(f"Removed {mode_name} from rotation (music stopped)")
                    elif getattr(manager, 'live_games', None):
                        live_games = getattr(manager, 'live_games', None)
                        if mode_name not in self.available_modes:
                            self.available_modes.append(mode_name)
                            logger.debug(f"Added {mode_name} to rotation (found {len(live_games)} live games)")
                    else:
                        if mode_name in self.available_modes:
                            self.available_modes.remove(mode_name)
                else:
                    if mode_name in self.available_modes:
                        self.available_modes.remove(mode_name)
            else:
                # For live_priority=True, never add to regular rotation
                # These modes are only used for live priority takeover
                if mode_name in self.available_modes:
                    self.available_modes.remove(mode_name)
        
        # Check if each sport is enabled before processing
        nhl_enabled = self.config.get('nhl_scoreboard', {}).get('enabled', False)
        nba_enabled = self.config.get('nba_scoreboard', {}).get('enabled', False)
        wnba_enabled = self.config.get('wnba_scoreboard', {}).get('enabled', False)
        mlb_enabled = self.config.get('mlb_scoreboard', {}).get('enabled', False)
        milb_enabled = self.config.get('milb_scoreboard', {}).get('enabled', False)
        soccer_enabled = self.config.get('soccer_scoreboard', {}).get('enabled', False)
        nfl_enabled = self.config.get('nfl_scoreboard', {}).get('enabled', False)
        ncaa_fb_enabled = self.config.get('ncaa_fb_scoreboard', {}).get('enabled', False)
        ncaa_baseball_enabled = self.config.get('ncaa_baseball_scoreboard', {}).get('enabled', False)
        ncaam_basketball_enabled = self.config.get('ncaam_basketball_scoreboard', {}).get('enabled', False)
        ncaaw_basketball_enabled = self.config.get('ncaaw_basketball_scoreboard', {}).get('enabled', False)
        ncaam_hockey_enabled = self.config.get('ncaam_hockey_scoreboard', {}).get('enabled', False)
        ncaaw_hockey_enabled = self.config.get('ncaaw_hockey_scoreboard', {}).get('enabled', False)
        
        update_mode('nhl_live', getattr(self, 'nhl_live', None), self.nhl_live_priority, nhl_enabled)
        update_mode('nba_live', getattr(self, 'nba_live', None), self.nba_live_priority, nba_enabled)
        update_mode('wnba_live', getattr(self, 'wnba_live', None), self.wnba_live_priority, wnba_enabled)
        update_mode('mlb_live', getattr(self, 'mlb_live', None), self.mlb_live_priority, mlb_enabled)
        update_mode('milb_live', getattr(self, 'milb_live', None), self.milb_live_priority, milb_enabled)
        update_mode('soccer_live', getattr(self, 'soccer_live', None), self.soccer_live_priority, soccer_enabled)
        update_mode('nfl_live', getattr(self, 'nfl_live', None), self.nfl_live_priority, nfl_enabled)
        update_mode('ncaa_fb_live', getattr(self, 'ncaa_fb_live', None), self.ncaa_fb_live_priority, ncaa_fb_enabled)
        update_mode('ncaa_baseball_live', getattr(self, 'ncaa_baseball_live', None), self.ncaa_baseball_live_priority, ncaa_baseball_enabled)
        update_mode('ncaam_basketball_live', getattr(self, 'ncaam_basketball_live', None), self.ncaam_basketball_live_priority, ncaam_basketball_enabled)
        update_mode('ncaaw_basketball_live', getattr(self, 'ncaaw_basketball_live', None), self.ncaaw_basketball_live_priority, ncaaw_basketball_enabled)
        update_mode('ncaam_hockey_live', getattr(self, 'ncaam_hockey_live', None), self.ncaam_hockey_live_priority, ncaam_hockey_enabled)
        update_mode('ncaaw_hockey_live', getattr(self, 'ncaaw_hockey_live', None), self.ncaaw_hockey_live_priority, ncaaw_hockey_enabled)
        # Add music to live priority rotation if enabled and music is playing
        music_enabled = self.config.get('music', {}).get('enabled', False)
        logger.debug(f"Music enabled: {music_enabled}, live_priority: {self.music_live_priority}, manager: {self.music_manager is not None}")
        update_mode('music_live', self.music_manager, self.music_live_priority, music_enabled)

    def _music_has_live_content(self) -> bool:
        """Check if music manager has live content (actively playing music)."""
        if not self.music_manager:
            logger.debug("Music manager not initialized")
            return False

        # Get current track info from music manager
        current_track_info = self.music_manager.get_current_display_info()
        if not current_track_info:
            logger.debug("No current track info from music manager")
            return False

        # Check if music is currently playing
        is_playing = current_track_info.get('is_playing', False)
        title = current_track_info.get('title', '')
        has_live_content = is_playing and title != 'Nothing Playing'

        logger.debug(f"Music live content check: is_playing={is_playing}, title='{title}', has_live_content={has_live_content}, current_track_info={current_track_info}")

        return has_live_content

    def run(self):
        """Run the display controller, switching between displays."""
        if not self.available_modes:
            logger.warning("No display modes are enabled. Exiting.")
            self.display_manager.cleanup()
            return
             
        try:
            logger.info("Clearing cache and refetching data to prevent stale data issues...")
            self.cache_manager.clear_cache()
            self._update_modules()
            logger.info("Cache cleared, waiting 5 seconds for fresh data fetch...")
            time.sleep(5)
            self.current_display_mode = self.available_modes[self.current_mode_index] if self.available_modes else 'none'
            while True:
                current_time = time.time()

                # Check the schedule (no config reload needed)
                self._check_schedule()
                if not self.is_display_active:
                    time.sleep(60)
                    continue
                
                # Update data for all modules first
                self._update_modules()
                
                # Process any deferred updates that may have accumulated
                self.display_manager.process_deferred_updates()
                
                # Update live modes in rotation if needed
                self._update_live_modes_in_rotation()

                # Check for live games and live_priority
                has_live_games, live_sport_type = self._check_live_games()
                is_currently_live = self.current_display_mode.endswith('_live')

                # Special case: if we're in music mode and music is actively playing, treat it as live
                if self.current_display_mode == 'music' and self._music_has_live_content():
                    is_currently_live = True
                    logger.debug("Music mode with active content treated as live priority")
                
                # Collect all sports with live_priority=True that have live games
                live_priority_sports = []
                for sport, attr, priority in [
                    ('nhl', 'nhl_live', self.nhl_live_priority),
                    ('nba', 'nba_live', self.nba_live_priority),
                    ('wnba', 'wnba_live', self.wnba_live_priority),
                    ('mlb', 'mlb_live', self.mlb_live_priority),
                    ('milb', 'milb_live', self.milb_live_priority),
                    ('soccer', 'soccer_live', self.soccer_live_priority),
                    ('nfl', 'nfl_live', self.nfl_live_priority),
                    ('ncaa_fb', 'ncaa_fb_live', self.ncaa_fb_live_priority),
                    ('ncaa_baseball', 'ncaa_baseball_live', self.ncaa_baseball_live_priority),
                    ('ncaam_basketball', 'ncaam_basketball_live', self.ncaam_basketball_live_priority),
                    ('ncaaw_basketball', 'ncaaw_basketball_live', self.ncaaw_basketball_live_priority),
                    ('ncaam_hockey', 'ncaam_hockey_live', self.ncaam_hockey_live_priority),
                    ('ncaaw_hockey', 'ncaaw_hockey_live', self.ncaaw_hockey_live_priority)
                ]:
                    manager = getattr(self, attr, None)

                    # Special handling for music - check if music is actively playing
                    if sport == 'music':
                        has_live_content = self._music_has_live_content()
                        logger.debug(f"Music live priority check: manager={manager is not None}, priority={priority}, has_live_content={has_live_content}")
                        if (manager is not None and
                            priority and
                            has_live_content):
                            live_priority_sports.append(sport)
                            logger.info(f"Live priority music found: music is playing - added to live priority rotation")
                        elif manager is not None and priority:
                            logger.debug("Music has live_priority=True but no music is playing (not taking over)")
                    else:
                        # Standard sports logic
                        live_games = getattr(manager, 'live_games', None) if manager is not None else None
                        # Check that manager exists, has live_priority enabled, has live_games attribute, and has at least one live game
                        if (manager is not None and
                            priority and
                            live_games is not None and
                            len(live_games) > 0):
                            live_priority_sports.append(sport)
                            logger.debug(f"Live priority sport found: {sport} with {len(live_games)} live games")
                        elif manager is not None and priority and live_games is not None:
                            logger.debug(f"{sport} has live_priority=True but {len(live_games)} live games (not taking over)")
                
                # Special case: add music to live priority sports if music is actively playing
                if self._music_has_live_content() and self.music_live_priority and self.music_manager:
                    live_priority_sports.append('music')
                    logger.info("Added music to live priority sports (music is playing)")

                # Determine if we have any live priority sports
                live_priority_takeover = len(live_priority_sports) > 0
                logger.debug(f"Live priority sports: {live_priority_sports}, takeover: {live_priority_takeover}")
                
                manager_to_display = None
                # --- State Machine for Display Logic ---
                if is_currently_live:
                    if live_priority_takeover:
                        # Check if we need to rotate to the next live priority sport
                        current_sport_type = self.current_display_mode.replace('_live', '') if self.current_display_mode.endswith('_live') else self.current_display_mode

                        # If current sport is not in live priority sports, switch to first one
                        if current_sport_type not in live_priority_sports:
                            next_sport = live_priority_sports[0]
                            # Special case for music - use "music" mode instead of "music_live"
                            new_mode = "music" if next_sport == "music" else f"{next_sport}_live"
                            logger.info(f"Current live sport {current_sport_type} no longer has priority, switching to {new_mode}")
                            self.current_display_mode = new_mode
                            if hasattr(self, '_last_logged_duration'):
                                delattr(self, '_last_logged_duration')
                            self.force_clear = True
                            self.last_switch = current_time
                            manager_to_display = self.music_manager if next_sport == "music" else getattr(self, f"{next_sport}_live", None)
                        else:
                            # Check if duration has elapsed for current sport
                            current_duration = self.get_current_duration()
                            if current_time - self.last_switch >= current_duration:
                                # Find next sport in rotation
                                current_index = live_priority_sports.index(current_sport_type)
                                next_index = (current_index + 1) % len(live_priority_sports)
                                next_sport = live_priority_sports[next_index]
                                # Special case for music - use "music" mode instead of "music_live"
                                new_mode = "music" if next_sport == "music" else f"{next_sport}_live"

                                logger.info(f"Rotating live priority sports: {current_sport_type} -> {next_sport} (duration: {current_duration}s)")
                                self.current_display_mode = new_mode
                                if hasattr(self, '_last_logged_duration'):
                                    delattr(self, '_last_logged_duration')
                                self.force_clear = True
                                self.last_switch = current_time
                                manager_to_display = self.music_manager if next_sport == "music" else getattr(self, f"{next_sport}_live", None)
                            else:
                                self.force_clear = False
                                # Special case for music - use "music" mode instead of "music_live"
                                manager_to_display = self.music_manager if current_sport_type == "music" else getattr(self, f"{current_sport_type}_live", None)
                    else:
                        # If no sport has live_priority takeover, treat as regular rotation
                        is_currently_live = False
                if not is_currently_live:
                    previous_mode_before_switch = self.current_display_mode
                    if live_priority_takeover:
                        # Switch to first live priority sport
                        next_sport = live_priority_sports[0]
                        # Special case for music - use "music" mode instead of "music_live"
                        new_mode = "music" if next_sport == "music" else f"{next_sport}_live"

                        # Double-check that the manager actually has live content before switching
                        if next_sport == "music":
                            target_manager = self.music_manager if self._music_has_live_content() else None
                        else:
                            target_manager = getattr(self, f"{next_sport}_live", None)

                        if target_manager and ((next_sport == "music") or (hasattr(target_manager, 'live_games') and len(target_manager.live_games) > 0)):
                            logger.info(f"Live priority takeover: Switching to {new_mode} from {self.current_display_mode}")
                            logger.debug(f"[DisplayController] Live priority takeover details: sport={next_sport}, manager={target_manager}")
                            if previous_mode_before_switch == 'music' and self.music_manager and next_sport != "music":
                                self.music_manager.deactivate_music_display()
                            self.current_display_mode = new_mode
                            # Reset logged duration when mode changes
                            if hasattr(self, '_last_logged_duration'):
                                delattr(self, '_last_logged_duration')
                            self.force_clear = True
                            self.last_switch = current_time
                            manager_to_display = target_manager
                        else:
                            logger.warning(f"[DisplayController] Live priority takeover attempted for {new_mode} but manager has no live content, skipping takeover")
                            live_priority_takeover = False
                    else:
                        # No live_priority takeover, regular rotation
                        needs_switch = False
                        if self.current_display_mode.endswith('_live'):
                            # For live modes without live_priority, check if duration has elapsed
                            if current_time - self.last_switch >= self.get_current_duration():
                                needs_switch = True
                                self.current_mode_index = (self.current_mode_index + 1) % len(self.available_modes)
                                new_mode_after_timer = self.available_modes[self.current_mode_index]
                                if previous_mode_before_switch == 'music' and self.music_manager and new_mode_after_timer != 'music':
                                    self.music_manager.deactivate_music_display()
                                if self.current_display_mode != new_mode_after_timer:
                                    logger.info(f"Switching to {new_mode_after_timer} from {self.current_display_mode}")
                                self.current_display_mode = new_mode_after_timer
                                # Reset logged duration when mode changes
                                if hasattr(self, '_last_logged_duration'):
                                    delattr(self, '_last_logged_duration')
                        elif current_time - self.last_switch >= self.get_current_duration() or self.force_change:
                            self.force_change = False
                            if self.current_display_mode == 'calendar' and self.calendar:
                                self.calendar.advance_event()
                            elif self.current_display_mode == 'of_the_day' and self.of_the_day:
                                self.of_the_day.advance_item()
                            needs_switch = True
                            self.current_mode_index = (self.current_mode_index + 1) % len(self.available_modes)
                            new_mode_after_timer = self.available_modes[self.current_mode_index]
                            if previous_mode_before_switch == 'music' and self.music_manager and new_mode_after_timer != 'music':
                                self.music_manager.deactivate_music_display()
                            if self.current_display_mode != new_mode_after_timer:
                                logger.info(f"Switching to {new_mode_after_timer} from {self.current_display_mode}")
                            self.current_display_mode = new_mode_after_timer
                            # Reset logged duration when mode changes
                            if hasattr(self, '_last_logged_duration'):
                                delattr(self, '_last_logged_duration')
                        else:
                            needs_switch = False
                        if needs_switch:
                            self.force_clear = True
                            self.last_switch = current_time
                        else:
                            self.force_clear = False
                        # Only set manager_to_display if it hasn't been set by live priority logic
                        if manager_to_display is None:
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
                            elif self.current_display_mode == 'leaderboard' and self.leaderboard:
                                manager_to_display = self.leaderboard
                            elif self.current_display_mode == 'calendar' and self.calendar:
                                manager_to_display = self.calendar
                            elif self.current_display_mode == 'youtube' and self.youtube:
                                manager_to_display = self.youtube
                            elif self.current_display_mode == 'text_display' and self.text_display:
                                manager_to_display = self.text_display
                            elif self.current_display_mode == 'static_image' and self.static_image:
                                manager_to_display = self.static_image
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
                            elif self.current_display_mode == 'wnba_recent' and self.wnba_recent:
                                manager_to_display = self.wnba_recent
                            elif self.current_display_mode == 'wnba_upcoming' and self.wnba_upcoming:
                                manager_to_display = self.wnba_upcoming
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
                            elif self.current_display_mode == 'ncaaw_basketball_recent' and self.ncaaw_basketball_recent:
                                manager_to_display = self.ncaaw_basketball_recent
                            elif self.current_display_mode == 'ncaaw_basketball_upcoming' and self.ncaaw_basketball_upcoming:
                                manager_to_display = self.ncaaw_basketball_upcoming
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
                            elif self.current_display_mode == 'music' and self.music_manager:
                                manager_to_display = self.music_manager
                            elif self.current_display_mode == 'music_live' and self.music_manager:
                                manager_to_display = self.music_manager
                            elif self.current_display_mode == 'nhl_live' and self.nhl_live:
                                manager_to_display = self.nhl_live
                            elif self.current_display_mode == 'nba_live' and self.nba_live:
                                manager_to_display = self.nba_live
                            elif self.current_display_mode == 'wnba_live' and self.wnba_live:
                                manager_to_display = self.wnba_live
                            elif self.current_display_mode == 'nfl_live' and self.nfl_live:
                                manager_to_display = self.nfl_live
                            elif self.current_display_mode == 'ncaa_fb_live' and self.ncaa_fb_live:
                                manager_to_display = self.ncaa_fb_live
                            elif self.current_display_mode == 'ncaa_baseball_live' and self.ncaa_baseball_live:
                                manager_to_display = self.ncaa_baseball_live
                            elif self.current_display_mode == 'ncaam_basketball_live' and self.ncaam_basketball_live:
                                manager_to_display = self.ncaam_basketball_live
                            elif self.current_display_mode == 'ncaaw_basketball_live' and self.ncaaw_basketball_live:
                                manager_to_display = self.ncaaw_basketball_live
                            elif self.current_display_mode == 'ncaam_hockey_live' and self.ncaam_hockey_live:
                                manager_to_display = self.ncaam_hockey_live
                            elif self.current_display_mode == 'ncaam_hockey_recent' and self.ncaam_hockey_recent:
                                manager_to_display = self.ncaam_hockey_recent
                            elif self.current_display_mode == 'ncaam_hockey_upcoming' and self.ncaam_hockey_upcoming:
                                manager_to_display = self.ncaam_hockey_upcoming
                            elif self.current_display_mode == 'ncaaw_hockey_live' and self.ncaaw_hockey_live:
                                manager_to_display = self.ncaaw_hockey_live
                            elif self.current_display_mode == 'ncaaw_hockey_recent' and self.ncaaw_hockey_recent:
                                manager_to_display = self.ncaaw_hockey_recent
                            elif self.current_display_mode == 'ncaaw_hockey_upcoming' and self.ncaaw_hockey_upcoming:
                                manager_to_display = self.ncaaw_hockey_upcoming
                            elif self.current_display_mode == 'mlb_live' and self.mlb_live:
                                manager_to_display = self.mlb_live
                            elif self.current_display_mode == 'milb_live' and self.milb_live:
                                manager_to_display = self.milb_live
                            elif self.current_display_mode == 'soccer_live' and self.soccer_live:
                                manager_to_display = self.soccer_live

                # --- Perform Display Update ---
                try:
                    # Log which display is being shown
                    if self.current_display_mode != getattr(self, '_last_logged_mode', None):
                        logger.info(f"Showing {self.current_display_mode}")
                        self._last_logged_mode = self.current_display_mode
                    
                    # Only log manager type when it changes to reduce spam
                    current_manager_type = type(manager_to_display).__name__ if manager_to_display else 'None'
                    if current_manager_type != getattr(self, '_last_logged_manager_type', None):
                        logger.info(f"manager_to_display is {current_manager_type}")
                        self._last_logged_manager_type = current_manager_type
                    
                    if self.current_display_mode in ['music', 'music_live'] and self.music_manager:
                        # Call MusicManager's display method
                        try:
                            self.music_manager.display(force_clear=self.force_clear)
                            # Reset force_clear if it was true for this mode
                            if self.force_clear:
                                self.force_clear = False
                        except SkipModuleException as e:
                            # Music module requested skip (nothing playing and configured to skip)
                            logger.info(f"Music module requested skip: {e}")
                            # Force change to next module
                            self.force_change = True
                        except Exception as e:
                            logger.error(f"Unexpected error in music display: {e}", exc_info=True)
                            # Reset force_clear on error
                            self.force_clear = True
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
                        elif self.current_display_mode in {'odds_ticker', 'leaderboard'}:
                            try:
                                manager_to_display.display(force_clear=self.force_clear)
                            except StopIteration:
                                self.force_change = True
                        elif self.current_display_mode == 'calendar':
                             manager_to_display.display(force_clear=self.force_clear)
                        elif self.current_display_mode == 'youtube':
                             manager_to_display.display(force_clear=self.force_clear)
                        elif self.current_display_mode == 'text_display':
                             manager_to_display.display() # Assumes internal clearing
                        elif self.current_display_mode == 'static_image':
                             manager_to_display.display(force_clear=self.force_clear)
                        elif self.current_display_mode == 'of_the_day':
                             manager_to_display.display(force_clear=self.force_clear)
                        elif self.current_display_mode == 'news_manager':
                             manager_to_display.display_news()
                        elif self.current_display_mode == 'ncaa_fb_upcoming' and self.ncaa_fb_upcoming:
                            self.ncaa_fb_upcoming.display(force_clear=self.force_clear)
                        elif self.current_display_mode == 'ncaam_basketball_recent' and self.ncaam_basketball_recent:
                            self.ncaam_basketball_recent.display(force_clear=self.force_clear)
                        elif self.current_display_mode == 'ncaam_basketball_upcoming' and self.ncaam_basketball_upcoming:
                            self.ncaam_basketball_upcoming.display(force_clear=self.force_clear)
                        elif self.current_display_mode == 'ncaaw_basketball_recent' and self.ncaaw_basketball_recent:
                            self.ncaaw_basketball_recent.display(force_clear=self.force_clear)
                        elif self.current_display_mode == 'ncaaw_basketball_upcoming' and self.ncaaw_basketball_upcoming:
                            self.ncaaw_basketball_upcoming.display(force_clear=self.force_clear)
                        elif self.current_display_mode == 'ncaa_baseball_recent' and self.ncaa_baseball_recent:
                            self.ncaa_baseball_recent.display(force_clear=self.force_clear)
                        elif self.current_display_mode == 'ncaa_baseball_upcoming' and self.ncaa_baseball_upcoming:
                            self.ncaa_baseball_upcoming.display(force_clear=self.force_clear)
                        elif self.current_display_mode == 'ncaam_hockey_recent' and self.ncaam_hockey_recent:
                            self.ncaam_hockey_recent.display(force_clear=self.force_clear)
                        elif self.current_display_mode == 'ncaam_hockey_upcoming' and self.ncaam_hockey_upcoming:
                            self.ncaam_hockey_upcoming.display(force_clear=self.force_clear)
                        elif self.current_display_mode == 'ncaaw_hockey_recent' and self.ncaaw_hockey_recent:
                            self.ncaaw_hockey_recent.display(force_clear=self.force_clear)
                        elif self.current_display_mode == 'ncaaw_hockey_upcoming' and self.ncaaw_hockey_upcoming:
                            self.ncaaw_hockey_upcoming.display(force_clear=self.force_clear)
                        elif self.current_display_mode == 'milb_live' and self.milb_live and len(self.milb_live.live_games) > 0:
                            logger.debug(f"[DisplayController] Calling MiLB live display with {len(self.milb_live.live_games)} live games")
                            # Update data before displaying for live managers
                            self.milb_live.update()
                            self.milb_live.display(force_clear=self.force_clear)
                        elif self.current_display_mode == 'milb_live' and self.milb_live:
                            logger.debug(f"[DisplayController] MiLB live manager exists but has {len(self.milb_live.live_games)} live games, switching to next mode")
                            # Switch to next mode since there are no live games
                            self.current_mode_index = (self.current_mode_index + 1) % len(self.available_modes)
                            self.current_display_mode = self.available_modes[self.current_mode_index]
                            self.force_clear = True
                            self.last_switch = current_time
                            logger.info(f"[DisplayController] Switched from milb_live (no games) to {self.current_display_mode}")
                        elif hasattr(manager_to_display, 'display'): # General case for most managers
                            # Special handling for live managers that need update before display
                            if self.current_display_mode.endswith('_live') and hasattr(manager_to_display, 'update'):
                                manager_to_display.update()
                            # Only log display method calls occasionally to reduce spam
                            current_time = time.time()
                            if not hasattr(self, '_last_display_method_log_time') or current_time - getattr(self, '_last_display_method_log_time', 0) >= 30:
                                logger.info(f"Calling display method for {self.current_display_mode}")
                                self._last_display_method_log_time = current_time
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
                         # If we can't display the current mode, switch to the next available mode
                         if self.available_modes:
                             self.current_mode_index = (self.current_mode_index + 1) % len(self.available_modes)
                             self.current_display_mode = self.available_modes[self.current_mode_index]
                             logger.info(f"Switching to next available mode: {self.current_display_mode}")
                         else:
                             logger.error("No available display modes found!")

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