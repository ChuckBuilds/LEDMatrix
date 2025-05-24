import os
import time
import logging
import requests
import json
from typing import Dict, Any, Optional, List
from PIL import Image, ImageDraw, ImageFont
from pathlib import Path
from datetime import datetime, timedelta, timezone
from src.display_manager import DisplayManager
from src.cache_manager import CacheManager

# Constants
ESPN_NCAAMB_SCOREBOARD_URL = "https://site.api.espn.com/apis/site/v2/sports/basketball/mens-college-basketball/scoreboard"

# Configure logging to match main configuration
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s.%(msecs)03d - %(levelname)s:%(name)s:%(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

class BaseNCAAMensBasketballManager:
    """Base class for NCAA Mens Basketball managers with common functionality."""
    # Class variables for warning tracking
    _no_data_warning_logged = False
    _last_warning_time = 0
    _warning_cooldown = 60  # Only log warnings once per minute
    _last_log_times = {}
    _shared_data = None
    _last_shared_update = 0
    cache_manager = CacheManager()  # Make cache_manager a class attribute
    logger = logging.getLogger('NCAAMensBasketball')  # Make logger a class attribute
    
    def __init__(self, config: Dict[str, Any], display_manager: DisplayManager):
        self.display_manager = display_manager
        self.config = config
        self.ncaa_mens_basketball_config = config.get("ncaa_mens_basketball_scoreboard", {})
        self.is_enabled = self.ncaa_mens_basketball_config.get("enabled", False)
        self.test_mode = self.ncaa_mens_basketball_config.get("test_mode", False)
        self.logo_dir = self.ncaa_mens_basketball_config.get("logo_dir", "assets/sports/ncaam_logos")
        self.update_interval = self.ncaa_mens_basketball_config.get("update_interval_seconds", 300)
        self.last_update = 0
        self.current_game = None
        self.fonts = self._load_fonts()
        self.favorite_teams = self.ncaa_mens_basketball_config.get("favorite_teams", [])
        self.recent_hours = self.ncaa_mens_basketball_config.get("recent_game_hours", 72)  # Default 72 hours
        
        # Set logging level to INFO to reduce noise
        self.logger.setLevel(logging.INFO)
        
        # Get display dimensions from config
        display_config = config.get("display", {})
        hardware_config = display_config.get("hardware", {})
        cols = hardware_config.get("cols", 64)
        chain = hardware_config.get("chain_length", 1)
        self.display_width = int(cols * chain)
        self.display_height = hardware_config.get("rows", 32)
        
        # Cache for loaded logos
        self._logo_cache = {}
        
        self.logger.info(f"Initialized NCAAMensBasketball manager with display dimensions: {self.display_width}x{self.display_height}")
        self.logger.info(f"Logo directory: {self.logo_dir}")

    def _should_log(self, message_type: str, cooldown: int = 300) -> bool:
        """Check if a message should be logged based on cooldown period."""
        current_time = time.time()
        last_time = self._last_log_times.get(message_type, 0)
        
        if current_time - last_time >= cooldown:
            self._last_log_times[message_type] = current_time
            return True
        return False

    def _load_test_data(self) -> Dict:
        """Load test data for development and testing."""
        self.logger.info("[NCAAMensBasketball] Loading test data")
        
        # Create test data with current time
        now = datetime.now(timezone.utc)
        
        # Create test events for different scenarios
        events = []
        
        # Live game (2nd Half)
        live_game = {
            "date": now.isoformat(),
            "competitions": [{
                "status": {
                    "type": {
                        "state": "in",
                        "shortDetail": "H2 5:23" # Changed from Q3
                    },
                    "period": 2, # Changed from 3
                    "displayClock": "5:23"
                },
                "competitors": [
                    {
                        "homeAway": "home",
                        "team": {"abbreviation": "UGA"}, 
                        "score": "75" # Adjusted score
                    },
                    {
                        "homeAway": "away",
                        "team": {"abbreviation": "AUB"}, 
                        "score": "72" # Adjusted score
                    }
                ]
            }]
        }
        events.append(live_game)
        
        # Recent game (yesterday)
        recent_game = {
            "date": (now - timedelta(days=1)).isoformat(),
            "competitions": [{
                "status": {
                    "type": {
                        "state": "post",
                        "shortDetail": "Final"
                    },
                    "period": 2, # Changed from 4
                    "displayClock": "0:00"
                },
                "competitors": [
                    {
                        "homeAway": "home",
                        "team": {"abbreviation": "UCLA"}, # Changed from BOS
                        "score": "88" # Adjusted score
                    },
                    {
                        "homeAway": "away",
                        "team": {"abbreviation": "ZAGA"}, # Changed from MIA
                        "score": "85" # Adjusted score
                    }
                ]
            }]
        }
        events.append(recent_game)
        
        # Upcoming game (tomorrow)
        upcoming_game = {
            "date": (now + timedelta(days=1)).isoformat(),
            "competitions": [{
                "status": {
                    "type": {
                        "state": "pre",
                        "shortDetail": "8:00 PM ET" # Adjusted time
                    },
                    "period": 0,
                    "displayClock": "0:00"
                },
                "competitors": [
                    {
                        "homeAway": "home",
                        "team": {"abbreviation": "UGA"}, # Changed from PHX
                        "score": "0"
                    },
                    {
                        "homeAway": "away",
                        "team": {"abbreviation": "AUB"}, # Changed from DEN
                        "score": "0"
                    }
                ]
            }]
        }
        events.append(upcoming_game)
        
        return {"events": events}

    def _load_fonts(self):
        """Load fonts used by the scoreboard."""
        fonts = {}
        try:
            # Try to load the Press Start 2P font first
            fonts['score'] = ImageFont.truetype("assets/fonts/PressStart2P-Regular.ttf", 10)
            fonts['time'] = ImageFont.truetype("assets/fonts/PressStart2P-Regular.ttf", 8)
            fonts['team'] = ImageFont.truetype("assets/fonts/PressStart2P-Regular.ttf", 8)
            fonts['status'] = ImageFont.truetype("assets/fonts/4x6-font.ttf", 6)
            logging.info("[NCAAMensBasketball] Successfully loaded Press Start 2P font for all text elements")
        except IOError:
            logging.warning("[NCAAMensBasketball] Press Start 2P font not found, trying 4x6 font.")
            try:
                # Try to load the 4x6 font as a fallback
                fonts['score'] = ImageFont.truetype("assets/fonts/4x6-font.ttf", 12)
                fonts['time'] = ImageFont.truetype("assets/fonts/4x6-font.ttf", 8)
                fonts['team'] = ImageFont.truetype("assets/fonts/4x6-font.ttf", 8)
                fonts['status'] = ImageFont.truetype("assets/fonts/4x6-font.ttf", 9)
                logging.info("[NCAAMensBasketball] Successfully loaded 4x6 font for all text elements")
            except IOError:
                logging.warning("[NCAAMensBasketball] 4x6 font not found, using default PIL font.")
                # Use default PIL font as a last resort
                fonts['score'] = ImageFont.load_default()
                fonts['time'] = ImageFont.load_default()
                fonts['team'] = ImageFont.load_default()
                fonts['status'] = ImageFont.load_default()
        return fonts

    def _load_and_resize_logo(self, team_abbrev: str) -> Optional[Image.Image]:
        """Load and resize a team logo, with caching."""
        self.logger.debug(f"Loading logo for {team_abbrev}")
        
        if team_abbrev in self._logo_cache:
            self.logger.debug(f"Using cached logo for {team_abbrev}")
            return self._logo_cache[team_abbrev]
            
        logo_path = os.path.join(self.logo_dir, f"{team_abbrev}.png")
        self.logger.debug(f"Logo path: {logo_path}")
        
        try:
            # Create test logos if they don't exist (Simple placeholder logic)
            if not os.path.exists(logo_path):
                self.logger.info(f"Creating test logo for {team_abbrev}")
                os.makedirs(os.path.dirname(logo_path), exist_ok=True)
                logo = Image.new('RGBA', (32, 32), (0, 0, 0, 0))
                draw = ImageDraw.Draw(logo)
                # Basic color logic for test logos
                color = (sum(ord(c) for c in team_abbrev) % 200 + 55,  # R
                         sum(ord(c)**2 for c in team_abbrev) % 200 + 55, # G
                         sum(ord(c)**3 for c in team_abbrev) % 200 + 55, # B
                         255) # Alpha
                draw.rectangle([4, 4, 28, 28], fill=color)
                draw.text((8, 8), team_abbrev, fill=(255, 255, 255, 255))
                logo.save(logo_path)
                self.logger.info(f"Created test logo at {logo_path}")
            
            logo = Image.open(logo_path)
            self.logger.debug(f"Opened logo for {team_abbrev}, size: {logo.size}, mode: {logo.mode}")
            
            # Convert to RGBA if not already
            if logo.mode != 'RGBA':
                self.logger.debug(f"Converting {team_abbrev} logo from {logo.mode} to RGBA")
                logo = logo.convert('RGBA')
            
            # Calculate max size based on display dimensions
            max_width = int(self.display_width * 1.5)
            max_height = int(self.display_height * 1.5)
            
            # Resize maintaining aspect ratio
            logo.thumbnail((max_width, max_height), Image.Resampling.LANCZOS)
            self.logger.debug(f"Resized {team_abbrev} logo to {logo.size}")
            
            # Cache the resized logo
            self._logo_cache[team_abbrev] = logo
            return logo
            
        except Exception as e:
            self.logger.error(f"Error loading logo for {team_abbrev}: {e}", exc_info=True)
            return None

    @classmethod
    def _fetch_shared_data(cls, date_str: str = None) -> Optional[Dict]:
        """Fetch and cache data for all managers to share."""
        current_time = time.time()
        
        # If we have recent data, use it
        if cls._shared_data and (current_time - cls._last_shared_update) < 300:  # 5 minutes
            return cls._shared_data
            
        try:
            # Check cache first
            cache_key = f"ncaam_basketball_{date_str}" if date_str else 'ncaam_basketball_today' # Prefix cache key
            cached_data = cls.cache_manager.get_cached_data(cache_key, max_age=300)  # 5 minutes cache
            if cached_data:
                cls.logger.info(f"[NCAAMensBasketball] Using cached data for {cache_key}")
                cls._shared_data = cached_data
                cls._last_shared_update = current_time
                return cached_data
                
            # If not in cache or stale, fetch from API
            url = ESPN_NCAAMB_SCOREBOARD_URL
            params = {}
            if date_str:
                params['dates'] = date_str
                
            response = requests.get(url, params=params)
            response.raise_for_status()
            data = response.json()
            cls.logger.info(f"[NCAAMensBasketball] Successfully fetched data from ESPN API")
            
            # Cache the response
            cls.cache_manager.save_cache(cache_key, data)
            cls._shared_data = data
            cls._last_shared_update = current_time
            
            # If no date specified, fetch data from multiple days
            if not date_str:
                # Get today's date in YYYYMMDD format
                today = datetime.now(timezone.utc).date()
                dates_to_fetch = [
                    (today - timedelta(days=2)).strftime('%Y%m%d'),
                    (today - timedelta(days=1)).strftime('%Y%m%d'),
                    today.strftime('%Y%m%d')
                ]
                
                # Fetch data for each date
                all_events = []
                for fetch_date in dates_to_fetch:
                    if fetch_date != today.strftime('%Y%m%d'):  # Skip today as we already have it
                        date_cache_key = f"ncaam_basketball_{fetch_date}" # Prefix cache key
                        # Check cache for this date
                        cached_date_data = cls.cache_manager.get_cached_data(date_cache_key, max_age=300)
                        if cached_date_data:
                            cls.logger.info(f"[NCAAMensBasketball] Using cached data for date {fetch_date}")
                            if "events" in cached_date_data:
                                all_events.extend(cached_date_data["events"])
                            continue
                            
                        params['dates'] = fetch_date
                        response = requests.get(url, params=params)
                        response.raise_for_status()
                        date_data = response.json()
                        if date_data and "events" in date_data:
                            all_events.extend(date_data["events"])
                            cls.logger.info(f"[NCAAMensBasketball] Fetched {len(date_data['events'])} events for date {fetch_date}")
                            # Cache the response
                            cls.cache_manager.save_cache(date_cache_key, date_data)
                
                # Combine events from all dates
                if all_events:
                    data["events"].extend(all_events)
                    cls.logger.info(f"[NCAAMensBasketball] Combined {len(data['events'])} total events from all dates")
                    cls._shared_data = data
                    cls._last_shared_update = current_time
            
            return data
        except requests.exceptions.RequestException as e:
            cls.logger.error(f"[NCAAMensBasketball] Error fetching data from ESPN: {e}")
            return None

    def _fetch_data(self, date_str: str = None) -> Optional[Dict]:
        """Fetch data using shared data mechanism."""
        # For live games, bypass the shared cache to ensure fresh data
        if isinstance(self, NCAAMensBasketballLiveManager):
            try:
                url = ESPN_NCAAMB_SCOREBOARD_URL
                params = {}
                if date_str:
                    params['dates'] = date_str
                    
                response = requests.get(url, params=params)
                response.raise_for_status()
                data = response.json()
                self.logger.info(f"[NCAAMensBasketball] Successfully fetched live game data from ESPN API")
                return data
            except requests.exceptions.RequestException as e:
                self.logger.error(f"[NCAAMensBasketball] Error fetching live game data from ESPN: {e}")
                return None
        else:
            # For non-live games, use the shared cache
            return self._fetch_shared_data(date_str)

    def _extract_game_details(self, game_event: Dict) -> Optional[Dict]:
        """Extract relevant game details from ESPN API response."""
        if not game_event:
            return None

        try:
            competition = game_event["competitions"][0]
            status = competition["status"]
            competitors = competition["competitors"]
            game_date_str = game_event["date"]

            # Parse game date/time
            try:
                start_time_utc = datetime.fromisoformat(game_date_str.replace("Z", "+00:00"))
                self.logger.debug(f"[NCAAMensBasketball] Parsed game time: {start_time_utc}")
            except ValueError:
                logging.warning(f"[NCAAMensBasketball] Could not parse game date: {game_date_str}")
                start_time_utc = None

            home_team = next(c for c in competitors if c.get("homeAway") == "home")
            away_team = next(c for c in competitors if c.get("homeAway") == "away")

            # Format game time and date for display
            game_time = ""
            game_date = ""
            if start_time_utc:
                # Convert to local time
                local_time = start_time_utc.astimezone()
                game_time = local_time.strftime("%-I:%M %p")
                game_date = local_time.strftime("%-m/%-d")

            # Calculate if game is within recent window
            is_within_window = False
            if start_time_utc:
                cutoff_time = datetime.now(timezone.utc) - timedelta(hours=self.recent_hours)
                is_within_window = start_time_utc > cutoff_time
                self.logger.debug(f"[NCAAMensBasketball] Game time: {start_time_utc}, Cutoff time: {cutoff_time}, Within window: {is_within_window}")

            details = {
                "start_time_utc": start_time_utc,
                "status_text": status["type"]["shortDetail"],
                "period": status.get("period", 0),
                "clock": status.get("displayClock", "0:00"),
                "is_live": status["type"]["state"] in ("in", "halftime"), # Include halftime as live
                "is_halftime": status["type"]["state"] == "halftime",
                "is_final": status["type"]["state"] == "post",
                "is_upcoming": status["type"]["state"] == "pre",
                "is_within_window": is_within_window,
                "home_abbr": home_team["team"]["abbreviation"],
                "home_score": home_team.get("score", "0"),
                "home_logo_path": os.path.join(self.logo_dir, f"{home_team['team']['abbreviation']}.png"),
                "away_abbr": away_team["team"]["abbreviation"],
                "away_score": away_team.get("score", "0"),
                "away_logo_path": os.path.join(self.logo_dir, f"{away_team['team']['abbreviation']}.png"),
                "game_time": game_time,
                "game_date": game_date
            }

            # Log game details for debugging
            self.logger.debug(f"[NCAAMensBasketball] Extracted game details: {details['away_abbr']} vs {details['home_abbr']}")
            self.logger.debug(f"[NCAAMensBasketball] Game status: is_final={details['is_final']}, is_within_window={details['is_within_window']}")

            return details
        except Exception as e:
            logging.error(f"[NCAAMensBasketball] Error extracting game details: {e}")
            return None

    def _draw_scorebug_layout(self, game: Dict, force_clear: bool = False) -> None:
        """Draw the scorebug layout for the current game."""
        try:
            # Create a new black image for the main display
            main_img = Image.new('RGBA', (self.display_width, self.display_height), (0, 0, 0, 255))
            
            # Load logos once
            home_logo = self._load_and_resize_logo(game["home_abbr"])
            away_logo = self._load_and_resize_logo(game["away_abbr"])
            
            if not home_logo or not away_logo:
                self.logger.error("Failed to load one or both team logos")
                return

            # Create a single overlay for both logos
            overlay = Image.new('RGBA', (self.display_width, self.display_height), (0, 0, 0, 0))

            # Calculate vertical center line for alignment
            center_y = self.display_height // 2

            # Draw home team logo (far right, extending beyond screen)
            home_x = self.display_width - home_logo.width + 12
            home_y = center_y - (home_logo.height // 2)
            
            # Paste the home logo onto the overlay
            overlay.paste(home_logo, (home_x, home_y), home_logo)

            # Draw away team logo (far left, extending beyond screen)
            away_x = -12
            away_y = center_y - (away_logo.height // 2)
            
            # Paste the away logo onto the overlay
            overlay.paste(away_logo, (away_x, away_y), away_logo)

            # Composite the overlay with the main image
            main_img = Image.alpha_composite(main_img, overlay)

            # Convert to RGB for final display
            main_img = main_img.convert('RGB')
            draw = ImageDraw.Draw(main_img)

            # Check if this is an upcoming game
            is_upcoming = game.get("is_upcoming", False)
            
            if is_upcoming:
                # For upcoming games, show date and time stacked in the center
                game_date = game.get("game_date", "")
                game_time = game.get("game_time", "")
                
                # Show "Next Game" at the top
                status_text = "Next Game"
                status_width = draw.textlength(status_text, font=self.fonts['status'])
                status_x = (self.display_width - status_width) // 2
                status_y = 2
                draw.text((status_x, status_y), status_text, font=self.fonts['status'], fill=(255, 255, 255))
                
                # Calculate position for the date text (centered horizontally, below "Next Game")
                date_width = draw.textlength(game_date, font=self.fonts['time'])
                date_x = (self.display_width - date_width) // 2
                date_y = center_y - 5  # Position in center
                draw.text((date_x, date_y), game_date, font=self.fonts['time'], fill=(255, 255, 255))
                
                # Calculate position for the time text (centered horizontally, in center)
                time_width = draw.textlength(game_time, font=self.fonts['time'])
                time_x = (self.display_width - time_width) // 2
                time_y = date_y + 10  # Position below date
                draw.text((time_x, time_y), game_time, font=self.fonts['time'], fill=(255, 255, 255))
            else:
                # For live/final games, show scores and period/time
                home_score = str(game.get("home_score", "0"))
                away_score = str(game.get("away_score", "0"))
                score_text = f"{away_score}-{home_score}"
                
                # Calculate position for the score text (centered at the bottom)
                score_width = draw.textlength(score_text, font=self.fonts['score'])
                score_x = (self.display_width - score_width) // 2
                score_y = self.display_height - 10
                draw.text((score_x, score_y), score_text, font=self.fonts['score'], fill=(255, 255, 255))

                # Draw period and time or Final
                if game.get("is_final", False):
                    status_text = "Final"
                    status_width = draw.textlength(status_text, font=self.fonts['time'])
                    status_x = (self.display_width - status_width) // 2
                    status_y = 5
                    draw.text((status_x, status_y), status_text, font=self.fonts['time'], fill=(255, 255, 255))
                elif game.get("is_halftime", False):
                    status_text = "Halftime"
                    status_width = draw.textlength(status_text, font=self.fonts['time'])
                    status_x = (self.display_width - status_width) // 2
                    status_y = 5
                    draw.text((status_x, status_y), status_text, font=self.fonts['time'], fill=(255, 255, 255))
                else:
                    period = game.get("period", 0)
                    clock = game.get("clock", "0:00")
                    
                    # Format period text for NCAA MB (Halves/OT)
                    if period == 1:
                        period_text = "1st H"
                    elif period == 2:
                        period_text = "2nd H"
                    elif period == 3:
                        period_text = "OT"
                    elif period > 3:
                        period_text = f"{period - 2}OT" # 2OT, 3OT etc.
                    else:
                         period_text = "" # Should not happen in live game normally

                    # Draw period text at the top
                    period_width = draw.textlength(period_text, font=self.fonts['time'])
                    period_x = (self.display_width - period_width) // 2
                    period_y = 1
                    draw.text((period_x, period_y), period_text, font=self.fonts['time'], fill=(255, 255, 255))
                    
                    # Draw clock below period
                    clock_width = draw.textlength(clock, font=self.fonts['time'])
                    clock_x = (self.display_width - clock_width) // 2
                    clock_y = period_y + 10  # Position below period
                    draw.text((clock_x, clock_y), clock, font=self.fonts['time'], fill=(255, 255, 255))

            # Display the image
            self.display_manager.image.paste(main_img, (0, 0))
            self.display_manager.update_display()

        except Exception as e:
            self.logger.error(f"Error displaying game: {e}", exc_info=True)

    def display(self, force_clear: bool = False) -> None:
        """Common display method for all NCAAMensBasketball managers"""
        if not self.current_game:
            current_time = time.time()
            if not hasattr(self, '_last_warning_time'):
                self._last_warning_time = 0
            if current_time - self._last_warning_time > 300:  # 5 minutes cooldown
                self.logger.warning("[NCAAMensBasketball] No game data available to display")
                self._last_warning_time = current_time
            return
            
        self._draw_scorebug_layout(self.current_game, force_clear)

class NCAAMensBasketballLiveManager(BaseNCAAMensBasketballManager):
    """Manager for live NCAA Mens Basketball games."""
    def __init__(self, config: Dict[str, Any], display_manager: DisplayManager):
        super().__init__(config, display_manager)
        self.update_interval = self.ncaa_mens_basketball_config.get("live_update_interval", 15)  # 15 seconds for live games
        self.no_data_interval = 300  # 5 minutes when no live games
        self.last_update = 0
        self.logger.info("Initialized NCAAMensBasketball Live Manager")
        self.live_games = []  # List to store all live games
        self.current_game_index = 0  # Index to track which game to show
        self.last_game_switch = 0  # Track when we last switched games
        self.game_display_duration = self.ncaa_mens_basketball_config.get("live_game_duration", 20)  # Display each live game for 20 seconds
        self.last_display_update = 0  # Track when we last updated the display
        self.last_log_time = 0
        self.log_interval = 300  # Only log status every 5 minutes
        self.has_favorite_team_game = False  # Track if we have any favorite team games
        
        # Initialize with test game only if test mode is enabled
        if self.test_mode:
            # Use the live game from _load_test_data
            test_data = self._load_test_data()
            live_test_event = next((e for e in test_data.get("events", []) if e["competitions"][0]["status"]["type"]["state"] == "in"), None)
            if live_test_event:
                 self.current_game = self._extract_game_details(live_test_event)
                 if self.current_game:
                    self.live_games = [self.current_game]
                    self.logger.info(f"[NCAAMensBasketball] Initialized NCAAMensBasketballLiveManager with test game: {self.current_game['away_abbr']} vs {self.current_game['home_abbr']}")
            else:
                 self.logger.warning("[NCAAMensBasketball] Could not find live test game data to initialize.")
        else:
            self.logger.info("[NCAAMensBasketball] Initialized NCAAMensBasketballLiveManager in live mode")

    def update(self):
        """Update live game data."""
        current_time = time.time()
        
        # Determine update interval based on whether we have favorite team games
        if self.has_favorite_team_game:
            interval = self.update_interval  # Short interval for live favorite team games
        else:
            interval = self.no_data_interval  # Longer interval when no favorite team games live
        
        if current_time - self.last_update >= interval:
            self.last_update = current_time
            
            if self.test_mode:
                # For testing, update the clock and maybe period
                if self.current_game:
                    minutes = int(self.current_game["clock"].split(":")[0])
                    seconds = int(self.current_game["clock"].split(":")[1])
                    seconds -= 1
                    if seconds < 0:
                        seconds = 59
                        minutes -= 1
                        if minutes < 0:
                             # Simulate moving from H1 to H2 or H2 to OT
                            if self.current_game["period"] == 1:
                                self.current_game["period"] = 2
                                minutes = 19 # Reset clock for H2
                                seconds = 59
                            elif self.current_game["period"] == 2:
                                self.current_game["period"] = 3 # Go to OT
                                minutes = 4 # Reset clock for OT
                                seconds = 59
                            elif self.current_game["period"] >= 3: # OT+
                                self.current_game["period"] += 1
                                minutes = 4
                                seconds = 59
                    self.current_game["clock"] = f"{minutes:02d}:{seconds:02d}"
                    # Always update display in test mode
                    self.display(force_clear=True)
            else:
                # Fetch live game data from ESPN API
                data = self._fetch_data()
                if data and "events" in data:
                    # Find all live games involving favorite teams
                    new_live_games = []
                    has_favorite_team = False
                    for event in data["events"]:
                        details = self._extract_game_details(event)
                        if details and details["is_live"]: # is_live includes 'in' and 'halftime'
                            if not self.favorite_teams or (
                                details["home_abbr"] in self.favorite_teams or
                                details["away_abbr"] in self.favorite_teams
                            ):
                                new_live_games.append(details)
                                if self.favorite_teams and (
                                    details["home_abbr"] in self.favorite_teams or
                                    details["away_abbr"] in self.favorite_teams
                                ):
                                    has_favorite_team = True
                    
                    # Update favorite team game status
                    self.has_favorite_team_game = has_favorite_team
                    
                    # Only log if there's a change in games or enough time has passed
                    should_log = (
                        current_time - self.last_log_time >= self.log_interval or
                        len(new_live_games) != len(self.live_games) or
                        not self.live_games or  # Log if we had no games before
                        has_favorite_team != self.has_favorite_team_game  # Log if favorite team status changed
                    )
                    
                    if should_log:
                        if new_live_games:
                            self.logger.info(f"[NCAAMensBasketball] Found {len(new_live_games)} live games")
                            for game in new_live_games:
                                status_str = f"H{game['period']}" if game['period'] <=2 else f"OT{game['period']-2 if game['period'] > 3 else ''}"
                                self.logger.info(f"[NCAAMensBasketball] Live game: {game['away_abbr']} vs {game['home_abbr']} - {status_str}, {game['clock']}")
                            if has_favorite_team:
                                self.logger.info("[NCAAMensBasketball] Found live game(s) for favorite team(s)")
                        else:
                            self.logger.info("[NCAAMensBasketball] No live games found")
                        self.last_log_time = current_time
                    
                    if new_live_games:
                        # Update the current game with the latest data if it matches
                        current_game_updated = False
                        for new_game in new_live_games:
                             if self.current_game and (
                                 (new_game["home_abbr"] == self.current_game["home_abbr"] and
                                  new_game["away_abbr"] == self.current_game["away_abbr"]) or
                                 (new_game["home_abbr"] == self.current_game["away_abbr"] and
                                  new_game["away_abbr"] == self.current_game["home_abbr"])
                             ):
                                self.current_game = new_game
                                current_game_updated = True
                                break

                        # Only update the games list if there's a structural change
                        if not self.live_games or set(game["away_abbr"] + game["home_abbr"] for game in new_live_games) != set(game["away_abbr"] + game["home_abbr"] for game in self.live_games):
                            self.live_games = new_live_games
                            # If we don't have a current game, it's not in the new list, or the list was empty, reset
                            if not self.current_game or not current_game_updated or not self.live_games:
                                self.current_game_index = 0
                                self.current_game = self.live_games[0] if self.live_games else None
                                self.last_game_switch = current_time


                        # Only update display if we have new data and enough time has passed
                        if current_time - self.last_display_update >= 1.0:
                            # self.display(force_clear=True) # REMOVED: DisplayController handles this
                            self.last_display_update = current_time
                    else:
                        # No live games found
                        self.live_games = []
                        self.current_game = None
                        self.has_favorite_team_game = False

    def display(self, force_clear: bool = False):
        """Display live game information."""
        if not self.current_game:
             # Explicitly clear display if there's nothing to show
             img = Image.new('RGB', (self.display_width, self.display_height), (0, 0, 0))
             self.display_manager.image.paste(img, (0, 0))
             self.display_manager.update_display()
             return
        super().display(force_clear)  # Call parent class's display method

class NCAAMensBasketballRecentManager(BaseNCAAMensBasketballManager):
    """Manager for recent NCAA Mens Basketball games."""
    def __init__(self, config: Dict[str, Any], display_manager: DisplayManager):
        super().__init__(config, display_manager)
        self.recent_games = []
        self.current_game_index = 0
        self.last_update = 0
        self.update_interval = 3600  # 1 hour for recent games
        self.recent_hours = self.ncaa_mens_basketball_config.get("recent_game_hours", 48)
        self.last_game_switch = 0
        self.game_display_duration = self.ncaa_mens_basketball_config.get("recent_game_duration", 15) # Configurable duration
        self.last_log_time = 0
        self.log_interval = 300  # Only log status every 5 minutes
        self.last_warning_time = 0
        self.warning_cooldown = 300  # Only show warning every 5 minutes
        self.logger.info(f"Initialized NCAAMensBasketballRecentManager with {len(self.favorite_teams)} favorite teams")
        
    def update(self):
        """Update recent games data."""
        current_time = time.time()
        if current_time - self.last_update < self.update_interval:
            return
            
        try:
            # Fetch data from ESPN API (uses shared cache)
            data = self._fetch_data()
            if not data or 'events' not in data:
                if self._should_log("no_events", 600): # Log less frequently for no events
                    self.logger.warning("[NCAAMensBasketball] No events found in ESPN API response for recent games")
                self.recent_games = []
                self.current_game = None
                self.last_update = current_time
                return

            events = data['events']

            # Process games
            new_recent_games = []
            for event in events:
                game = self._extract_game_details(event)
                # Filter for recent games: must be final and within the time window
                if game and game['is_final'] and game['is_within_window']:
                    new_recent_games.append(game)

            # Filter for favorite teams
            new_team_games = [game for game in new_recent_games
                         if not self.favorite_teams or # Show all if no favorites
                            game['home_abbr'] in self.favorite_teams or
                            game['away_abbr'] in self.favorite_teams]

            # Sort by game time (most recent first)
            new_team_games.sort(key=lambda g: g.get('start_time_utc', datetime.min.replace(tzinfo=timezone.utc)), reverse=True)

            # Only log if there's a change in games or enough time has passed
            should_log = (
                current_time - self.last_log_time >= self.log_interval or
                len(new_team_games) != len(self.recent_games) or
                (new_team_games and not self.recent_games) # Log if we found games after having none
            )

            if should_log:
                if new_team_games:
                    self.logger.info(f"[NCAAMensBasketball] Found {len(new_team_games)} recent games for favorite teams")
                elif self.favorite_teams: # Only log "none found" if favorites are configured
                    self.logger.info("[NCAAMensBasketball] No recent games found for favorite teams")
                self.last_log_time = current_time

            if new_team_games:
                 # Check if the games list actually changed before resetting index
                if (len(new_team_games) != len(self.recent_games) or
                    any(g1 != g2 for g1, g2 in zip(new_team_games, self.recent_games))):
                    self.recent_games = new_team_games
                    self.current_game_index = 0
                    self.current_game = self.recent_games[0]
                    self.last_game_switch = current_time # Reset switch timer on list update
            else:
                self.recent_games = []
                self.current_game = None


            self.last_update = current_time

        except Exception as e:
            self.logger.error(f"[NCAAMensBasketball] Error updating recent games: {e}", exc_info=True)
            self.recent_games = [] # Clear games on error
            self.current_game = None
            self.last_update = current_time # Still update time to prevent fast retry loops

    def display(self, force_clear=False):
        """Display recent games."""
        if not self.recent_games:
            current_time = time.time()
            if current_time - self.last_warning_time > self.warning_cooldown:
                 # Only log if favorite teams are configured
                if self.favorite_teams:
                    self.logger.info("[NCAAMensBasketball] No recent games for favorite teams to display")
                else:
                    self.logger.info("[NCAAMensBasketball] No recent games to display")
                self.last_warning_time = current_time
            # Explicitly clear display if there's nothing to show
            img = Image.new('RGB', (self.display_width, self.display_height), (0, 0, 0))
            self.display_manager.image.paste(img, (0, 0))
            self.display_manager.update_display()
            return

        try:
            current_time = time.time()

            # Check if it's time to switch games (only if more than one game)
            if len(self.recent_games) > 1 and current_time - self.last_game_switch >= self.game_display_duration:
                # Move to next game
                self.current_game_index = (self.current_game_index + 1) % len(self.recent_games)
                self.current_game = self.recent_games[self.current_game_index]
                self.last_game_switch = current_time
                force_clear = True  # Force clear when switching games

            # If only one game, ensure it's set correctly
            elif len(self.recent_games) == 1:
                self.current_game = self.recent_games[0]

            # Draw the scorebug layout
            if self.current_game: # Ensure we have a game before drawing
                 self._draw_scorebug_layout(self.current_game, force_clear)
                 # Update display
                 self.display_manager.update_display()
            else:
                 self.logger.warning("[NCAAMensBasketball] Current game is None in RecentManager display, despite having recent_games list.")


        except Exception as e:
            self.logger.error(f"[NCAAMensBasketball] Error displaying recent game: {e}", exc_info=True)

class NCAAMensBasketballUpcomingManager(BaseNCAAMensBasketballManager):
    """Manager for upcoming NCAA Mens Basketball games."""
    def __init__(self, config: Dict[str, Any], display_manager: DisplayManager):
        super().__init__(config, display_manager)
        self.upcoming_games = []
        self.current_game_index = 0
        self.last_update = 0
        self.update_interval = 3600  # 1 hour for upcoming games
        self.last_warning_time = 0
        self.warning_cooldown = 300  # Only show warning every 5 minutes
        self.last_game_switch = 0
        self.game_display_duration = self.ncaa_mens_basketball_config.get("upcoming_game_duration", 15) # Configurable duration
        self.logger.info(f"Initialized NCAAMensBasketballUpcomingManager with {len(self.favorite_teams)} favorite teams")

    def update(self):
        """Update upcoming games data."""
        current_time = time.time()
        if current_time - self.last_update < self.update_interval:
            return

        try:
            # Fetch data from ESPN API (uses shared cache)
            data = self._fetch_data()
            if not data or 'events' not in data:
                if self._should_log("no_events_upcoming", 600):
                     self.logger.warning("[NCAAMensBasketball] No events found in ESPN API response for upcoming games")
                self.upcoming_games = []
                self.current_game = None
                self.last_update = current_time
                return

            events = data['events']
            if self._should_log("fetch_success_upcoming", 300):
                self.logger.info(f"[NCAAMensBasketball] Successfully fetched {len(events)} events from ESPN API for upcoming check")

            # Process games
            new_upcoming_games = []
            for event in events:
                game = self._extract_game_details(event)
                if game and game['is_upcoming']:
                    new_upcoming_games.append(game)
                    self.logger.debug(f"Processing upcoming game: {game['away_abbr']} vs {game['home_abbr']}")

            # Filter for favorite teams
            team_games = [game for game in new_upcoming_games
                         if not self.favorite_teams or # Show all if no favorites
                            game['home_abbr'] in self.favorite_teams or
                            game['away_abbr'] in self.favorite_teams]

             # Sort by game time (soonest first)
            team_games.sort(key=lambda g: g.get('start_time_utc', datetime.max.replace(tzinfo=timezone.utc)))


            if self._should_log("team_games_upcoming", 300):
                 if team_games:
                    self.logger.info(f"[NCAAMensBasketball] Found {len(team_games)} upcoming games for favorite teams")
                 elif self.favorite_teams: # Only log "none found" if favorites configured
                     self.logger.info("[NCAAMensBasketball] No upcoming games found for favorite teams")


            if team_games:
                # Check if the games list actually changed before resetting index
                if (len(team_games) != len(self.upcoming_games) or
                    any(g1 != g2 for g1, g2 in zip(team_games, self.upcoming_games))):
                    self.upcoming_games = team_games
                    self.current_game_index = 0
                    self.current_game = self.upcoming_games[0]
                    self.last_game_switch = current_time # Reset switch timer
            else:
                self.upcoming_games = []
                self.current_game = None

            self.last_update = current_time

        except Exception as e:
            self.logger.error(f"[NCAAMensBasketball] Error updating upcoming games: {e}", exc_info=True)
            self.upcoming_games = [] # Clear games on error
            self.current_game = None
            self.last_update = current_time # Still update time

    def display(self, force_clear=False):
        """Display upcoming games."""
        if not self.upcoming_games:
            current_time = time.time()
            if current_time - self.last_warning_time > self.warning_cooldown:
                if self.favorite_teams:
                     self.logger.info("[NCAAMensBasketball] No upcoming games for favorite teams to display")
                else:
                    self.logger.info("[NCAAMensBasketball] No upcoming games to display")
                self.last_warning_time = current_time
             # Explicitly clear display if there's nothing to show
            img = Image.new('RGB', (self.display_width, self.display_height), (0, 0, 0))
            self.display_manager.image.paste(img, (0, 0))
            self.display_manager.update_display()
            return

        try:
            current_time = time.time()

            # Check if it's time to switch games (only if more than one game)
            if len(self.upcoming_games) > 1 and current_time - self.last_game_switch >= self.game_display_duration:
                # Move to next game
                self.current_game_index = (self.current_game_index + 1) % len(self.upcoming_games)
                self.current_game = self.upcoming_games[self.current_game_index]
                self.last_game_switch = current_time
                force_clear = True  # Force clear when switching games

            # If only one game, ensure it's set
            elif len(self.upcoming_games) == 1:
                 self.current_game = self.upcoming_games[0]


            # Draw the scorebug layout
            if self.current_game: # Ensure we have a game to draw
                 self._draw_scorebug_layout(self.current_game, force_clear)
                 # Update display
                 self.display_manager.update_display()
            else:
                 self.logger.warning("[NCAAMensBasketball] Current game is None in UpcomingManager display, despite having upcoming_games list.")


        except Exception as e:
            self.logger.error(f"[NCAAMensBasketball] Error displaying upcoming game: {e}", exc_info=True) 