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
from src.config_manager import ConfigManager
from src.odds_manager import OddsManager
from src.background_data_service import get_background_service
import pytz

# Import the API counter function from web interface
try:
    from web_interface_v2 import increment_api_counter
except ImportError:
    # Fallback if web interface is not available
    def increment_api_counter(kind: str, count: int = 1):
        pass

# Constants
ESPN_NBA_SCOREBOARD_URL = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard"

# Configure logging to match main configuration
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s.%(msecs)03d - %(levelname)s:%(name)s:%(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

class BaseNBAManager:
    """Base class for NBA managers with common functionality."""
    # Class variables for warning tracking
    _no_data_warning_logged = False
    _last_warning_time = 0
    _warning_cooldown = 60  # Only log warnings once per minute
    _last_log_times = {}
    _shared_data = None
    _last_shared_update = 0
    
    def __init__(self, config: Dict[str, Any], display_manager: DisplayManager, cache_manager: CacheManager):
        self.display_manager = display_manager
        # Store reference to config instead of creating new ConfigManager
        self.config_manager = None  # Not used in this class
        self.config = config
        self.cache_manager = cache_manager
        self.odds_manager = OddsManager(self.cache_manager, None)
        self.logger = logging.getLogger(__name__)
        self.nba_config = config.get("nba_scoreboard", {})
        self.is_enabled = self.nba_config.get("enabled", False)
        self.show_odds = self.nba_config.get("show_odds", False)
        self.test_mode = self.nba_config.get("test_mode", False)
        self.logo_dir = self.nba_config.get("logo_dir", "assets/sports/nba_logos")
        self.show_records = self.nba_config.get('show_records', False)
        self.update_interval = self.nba_config.get("update_interval_seconds", 300)
        self.last_update = 0
        self.current_game = None
        self.fonts = self._load_fonts()
        self.favorite_teams = self.nba_config.get("favorite_teams", [])
        
        # Set logging level to INFO to reduce noise
        self.logger.setLevel(logging.INFO)
        
        # Get display dimensions from matrix
        self.display_width = self.display_manager.matrix.width
        self.display_height = self.display_manager.matrix.height
        
        # Cache for loaded logos
        self._logo_cache = {}
        
        # Initialize background data service
        background_config = self.nba_config.get("background_service", {})
        if background_config.get("enabled", True):  # Default to enabled
            max_workers = background_config.get("max_workers", 3)
            self.background_service = get_background_service(self.cache_manager, max_workers)
            self.background_fetch_requests = {}  # Track background fetch requests
            self.background_enabled = True
            self.logger.info(f"[NBA] Background service enabled with {max_workers} workers")
        else:
            self.background_service = None
            self.background_fetch_requests = {}
            self.background_enabled = False
            self.logger.info("[NBA] Background service disabled")

        self.logger.info(f"Initialized NBA manager with display dimensions: {self.display_width}x{self.display_height}")
        self.logger.info(f"Logo directory: {self.logo_dir}")

    def _get_timezone(self):
        try:
            timezone_str = self.config.get('timezone', 'UTC')
            return pytz.timezone(timezone_str)
        except pytz.UnknownTimeZoneError:
            return pytz.utc

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
        self.logger.info("[NBA] Loading test data")
        
        # Create test data with current time
        now = datetime.now(timezone.utc)
        
        # Create test events for different scenarios
        events = []
        
        # Live game
        live_game = {
            "date": now.isoformat(),
            "competitions": [{
                "status": {
                    "type": {
                        "state": "in",
                        "shortDetail": "Q3 5:23"
                    },
                    "period": 3,
                    "displayClock": "5:23"
                },
                "competitors": [
                    {
                        "homeAway": "home",
                        "team": {"abbreviation": "LAL"},
                        "score": "85"
                    },
                    {
                        "homeAway": "away",
                        "team": {"abbreviation": "GSW"},
                        "score": "82"
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
                    "period": 4,
                    "displayClock": "0:00"
                },
                "competitors": [
                    {
                        "homeAway": "home",
                        "team": {"abbreviation": "BOS"},
                        "score": "112"
                    },
                    {
                        "homeAway": "away",
                        "team": {"abbreviation": "MIA"},
                        "score": "108"
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
                        "shortDetail": "7:30 PM ET"
                    },
                    "period": 0,
                    "displayClock": "0:00"
                },
                "competitors": [
                    {
                        "homeAway": "home",
                        "team": {"abbreviation": "PHX"},
                        "score": "0"
                    },
                    {
                        "homeAway": "away",
                        "team": {"abbreviation": "DEN"},
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
            logging.info("[NBA] Successfully loaded Press Start 2P font for all text elements")
        except IOError:
            logging.warning("[NBA] Press Start 2P font not found, trying 4x6 font.")
            try:
                # Try to load the 4x6 font as a fallback
                fonts['score'] = ImageFont.truetype("assets/fonts/4x6-font.ttf", 12)
                fonts['time'] = ImageFont.truetype("assets/fonts/4x6-font.ttf", 8)
                fonts['team'] = ImageFont.truetype("assets/fonts/4x6-font.ttf", 8)
                fonts['status'] = ImageFont.truetype("assets/fonts/4x6-font.ttf", 9)
                logging.info("[NBA] Successfully loaded 4x6 font for all text elements")
            except IOError:
                logging.warning("[NBA] 4x6 font not found, using default PIL font.")
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
            # Create test logos if they don't exist
            if not os.path.exists(logo_path):
                self.logger.info(f"Creating test logo for {team_abbrev}")
                os.makedirs(os.path.dirname(logo_path), exist_ok=True)
                # Create a simple colored rectangle as a test logo
                logo = Image.new('RGBA', (32, 32), (0, 0, 0, 0))
                draw = ImageDraw.Draw(logo)
                # Use team abbreviation to determine color
                if team_abbrev == "LAL":
                    color = (253, 185, 39, 255)  # Lakers gold
                else:
                    color = (0, 125, 197, 255)  # Warriors blue
                draw.rectangle([4, 4, 28, 28], fill=color)
                # Add team abbreviation
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
            # Make logos 150% of display width to allow them to extend off screen
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

    def _fetch_nba_api_data(self, use_cache: bool = True) -> Optional[Dict]:
        """Fetch and cache data for all managers to share."""
        now = datetime.now(pytz.utc)
        date_str = now.strftime('%Y%m%d')
        cache_key = f"nba_api_data_{date_str}"

        if use_cache:
            cached_data = self.cache_manager.get(cache_key)
            if cached_data:
                self.logger.info(f"[NBA] Using cached data for {date_str}")
                return cached_data
        
        try:
            url = ESPN_NBA_SCOREBOARD_URL
            params = {'dates': date_str}
            response = requests.get(url, params=params)
            response.raise_for_status()
            data = response.json()
            
            # Increment API counter for sports data call
            increment_api_counter('sports', 1)
            
            if use_cache:
                self.cache_manager.set(cache_key, data)
                
            self.logger.info(f"[NBA] Successfully fetched data from ESPN API for {date_str}")
            return data
        except requests.exceptions.RequestException as e:
            self.logger.error(f"[NBA] Error fetching data from ESPN: {e}")
            return None

    def _fetch_data(self, date_str: str = None) -> Optional[Dict]:
        """Fetch data using shared data mechanism."""
        if isinstance(self, NBALiveManager):
            return self._fetch_nba_api_data(use_cache=False)
        else:
            return self._fetch_nba_api_data(use_cache=True)

    def _fetch_odds(self, game: Dict) -> None:
        """Fetch odds for a specific game if conditions are met."""
        # Check if odds should be shown for this sport
        if not self.show_odds:
            return

        # Check if we should only fetch for favorite teams
        is_favorites_only = self.nba_config.get("show_favorite_teams_only", False)
        if is_favorites_only:
            home_abbr = game.get('home_abbr')
            away_abbr = game.get('away_abbr')
            if not (home_abbr in self.favorite_teams or away_abbr in self.favorite_teams):
                self.logger.debug(f"Skipping odds fetch for non-favorite game in favorites-only mode: {away_abbr}@{home_abbr}")
                return

        self.logger.debug(f"Proceeding with odds fetch for game: {game.get('id', 'N/A')}")
        
        # Fetch odds using OddsManager (ESPN API)
        try:
            # Determine update interval based on game state
            is_live = game.get('status', '').lower() == 'in'
            update_interval = self.nba_config.get("live_odds_update_interval", 60) if is_live \
                else self.nba_config.get("odds_update_interval", 3600)

            odds_data = self.odds_manager.get_odds(
                sport="basketball",
                league="nba",
                event_id=game['id'],
                update_interval_seconds=update_interval
            )
            
            if odds_data:
                game['odds'] = odds_data
                self.logger.debug(f"Successfully fetched and attached odds for game {game['id']}")
            else:
                self.logger.debug(f"No odds data returned for game {game['id']}")

        except Exception as e:
            self.logger.error(f"Error fetching odds for game {game.get('id', 'N/A')}: {e}")

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
                self.logger.debug(f"[NBA] Parsed game time: {start_time_utc}")
            except ValueError:
                logging.warning(f"[NBA] Could not parse game date: {game_date_str}")
                start_time_utc = None

            home_team = next(c for c in competitors if c.get("homeAway") == "home")
            away_team = next(c for c in competitors if c.get("homeAway") == "away")
            home_record = home_team.get('records', [{}])[0].get('summary', '') if home_team.get('records') else ''
            away_record = away_team.get('records', [{}])[0].get('summary', '') if away_team.get('records') else ''
            
            # Don't show "0-0" records - set to blank instead
            if home_record == "0-0":
                home_record = ''
            if away_record == "0-0":
                away_record = ''

            # Format game time and date for display
            game_time = ""
            game_date = ""
            if start_time_utc:
                # Convert to local time
                local_time = start_time_utc.astimezone(self._get_timezone())
                game_time = local_time.strftime("%I:%M%p").lstrip('0')
                
                # Check date format from config
                use_short_date_format = self.config.get('display', {}).get('use_short_date_format', False)
                if use_short_date_format:
                    game_date = local_time.strftime("%-m/%-d")
                else:
                    game_date = self.display_manager.format_date_with_ordinal(local_time)

            details = {
                "start_time_utc": start_time_utc,
                "status_text": status["type"]["shortDetail"],
                "period": status.get("period", 0),
                "clock": status.get("displayClock", "0:00"),
                "is_live": status["type"]["state"] in ("in", "halftime"),
                "is_final": status["type"]["state"] == "post",
                "is_upcoming": status["type"]["state"] == "pre",
                "home_abbr": home_team["team"]["abbreviation"],
                "home_score": home_team.get("score", "0"),
                "home_record": home_record,
                "home_logo_path": os.path.join(self.logo_dir, f"{home_team['team']['abbreviation']}.png"),
                "away_abbr": away_team["team"]["abbreviation"],
                "away_score": away_team.get("score", "0"),
                "away_record": away_record,
                "away_logo_path": os.path.join(self.logo_dir, f"{away_team['team']['abbreviation']}.png"),
                "game_time": game_time,
                "game_date": game_date
            }

            # Log game details for debugging
            self.logger.debug(f"[NBA] Extracted game details: {details['away_abbr']} vs {details['home_abbr']}")
            self.logger.debug(f"[NBA] Game status: is_final={details['is_final']}, is_within_window={details['is_within_window']}")

            return details
        except Exception as e:
            logging.error(f"[NBA] Error extracting game details: {e}")
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
                else:
                    period = game.get("period", 0)
                    clock = game.get("clock", "0:00")
                    
                    # Format period text for NBA (quarters)
                    if period > 4:
                        period_text = "OT"
                    else:
                        period_text = f"{period}{'st' if period == 1 else 'nd' if period == 2 else 'rd' if period == 3 else 'th'} Q"
                    
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

            # Draw odds if available
            if 'odds' in game and game['odds']:
                self._draw_dynamic_odds(draw, game['odds'], self.display_width, self.display_height)

            # Draw records if enabled
            if self.show_records:
                try:
                    record_font = ImageFont.truetype("assets/fonts/4x6-font.ttf", 6)
                except IOError:
                    record_font = ImageFont.load_default()
                
                away_record = game.get('away_record', '')
                home_record = game.get('home_record', '')
                
                record_bbox = draw.textbbox((0,0), "0-0", font=record_font)
                record_height = record_bbox[3] - record_bbox[1]
                record_y = self.display_height - record_height

                if away_record:
                    away_record_x = 0
                    self._draw_text_with_outline(draw, away_record, (away_record_x, record_y), record_font)

                if home_record:
                    home_record_bbox = draw.textbbox((0,0), home_record, font=record_font)
                    home_record_width = home_record_bbox[2] - home_record_bbox[0]
                    home_record_x = self.display_width - home_record_width
                    self._draw_text_with_outline(draw, home_record, (home_record_x, record_y), record_font)

            # Display the image
            self.display_manager.image.paste(main_img, (0, 0))
            self.display_manager.update_display()

        except Exception as e:
            self.logger.error(f"Error displaying game: {e}", exc_info=True)

    def display(self, force_clear: bool = False) -> None:
        """Common display method for all NBA managers"""
        if not self.current_game:
            current_time = time.time()
            if not hasattr(self, '_last_warning_time'):
                self._last_warning_time = 0
            if current_time - self._last_warning_time > 300:  # 5 minutes cooldown
                self.logger.warning("[NBA] No game data available to display")
                self._last_warning_time = current_time
            return
            
        self._draw_scorebug_layout(self.current_game, force_clear)

    def _draw_dynamic_odds(self, draw: ImageDraw.Draw, odds: Dict[str, Any], width: int, height: int) -> None:
        """Draw odds with dynamic positioning - only show negative spread and position O/U based on favored team."""
        home_team_odds = odds.get('home_team_odds', {})
        away_team_odds = odds.get('away_team_odds', {})
        home_spread = home_team_odds.get('spread_odds')
        away_spread = away_team_odds.get('spread_odds')

        # Get top-level spread as fallback
        top_level_spread = odds.get('spread')
        
        # If we have a top-level spread and the individual spreads are None or 0, use the top-level
        if top_level_spread is not None:
            if home_spread is None or home_spread == 0.0:
                home_spread = top_level_spread
            if away_spread is None:
                away_spread = -top_level_spread

        # Determine which team is favored (has negative spread)
        home_favored = home_spread is not None and home_spread < 0
        away_favored = away_spread is not None and away_spread < 0
        
        # Only show the negative spread (favored team)
        favored_spread = None
        favored_side = None
        
        if home_favored:
            favored_spread = home_spread
            favored_side = 'home'
            self.logger.debug(f"Home team favored with spread: {favored_spread}")
        elif away_favored:
            favored_spread = away_spread
            favored_side = 'away'
            self.logger.debug(f"Away team favored with spread: {favored_spread}")
        else:
            self.logger.debug("No clear favorite - spreads: home={home_spread}, away={away_spread}")
        
        # Show the negative spread on the appropriate side
        if favored_spread is not None:
            spread_text = str(favored_spread)
            font = self.fonts['detail']  # Use detail font for odds
            
            if favored_side == 'home':
                # Home team is favored, show spread on right side
                spread_width = draw.textlength(spread_text, font=font)
                spread_x = width - spread_width  # Top right
                spread_y = 0
                self._draw_text_with_outline(draw, spread_text, (spread_x, spread_y), font, fill=(0, 255, 0))
                self.logger.debug(f"Showing home spread '{spread_text}' on right side")
            else:
                # Away team is favored, show spread on left side
                spread_x = 0  # Top left
                spread_y = 0
                self._draw_text_with_outline(draw, spread_text, (spread_x, spread_y), font, fill=(0, 255, 0))
                self.logger.debug(f"Showing away spread '{spread_text}' on left side")
        
        # Show over/under on the opposite side of the favored team
        over_under = odds.get('over_under')
        if over_under is not None:
            ou_text = f"O/U: {over_under}"
            font = self.fonts['detail']  # Use detail font for odds
            ou_width = draw.textlength(ou_text, font=font)
            
            if favored_side == 'home':
                # Home team is favored, show O/U on left side (opposite of spread)
                ou_x = 0  # Top left
                ou_y = 0
                self.logger.debug(f"Showing O/U '{ou_text}' on left side (home favored)")
            elif favored_side == 'away':
                # Away team is favored, show O/U on right side (opposite of spread)
                ou_x = width - ou_width  # Top right
                ou_y = 0
                self.logger.debug(f"Showing O/U '{ou_text}' on right side (away favored)")
            else:
                # No clear favorite, show O/U in center
                ou_x = (width - ou_width) // 2
                ou_y = 0
                self.logger.debug(f"Showing O/U '{ou_text}' in center (no clear favorite)")
            
            self._draw_text_with_outline(draw, ou_text, (ou_x, ou_y), font, fill=(0, 255, 0))

    def _draw_text_with_outline(self, draw, text, position, font, fill=(255, 255, 255), outline_color=(0, 0, 0)):
        """Helper to draw text with an outline."""
        draw.text(position, text, font=font, fill=outline_color)
        draw.text(position, text, font=font, fill=fill)


class NBALiveManager(BaseNBAManager):
    """Manager for live NBA games."""
    def __init__(self, config: Dict[str, Any], display_manager: DisplayManager, cache_manager: CacheManager):
        super().__init__(config, display_manager, cache_manager)
        self.update_interval = self.nba_config.get("live_update_interval", 30)
        self.no_data_interval = 300
        self.last_update = 0
        self.logger.info("Initialized NBA Live Manager")
        self.live_games = []
        self.current_game_index = 0
        self.last_game_switch = 0
        self.game_display_duration = self.nba_config.get("live_game_duration", 20)
        self.last_display_update = 0
        self.last_log_time = 0
        self.log_interval = 300

    def update(self):
        """Update live game data."""
        if not self.is_enabled: return
        current_time = time.time()
        interval = self.no_data_interval if not self.live_games else self.update_interval

        if current_time - self.last_update >= interval:
            self.last_update = current_time

            # Fetch live game data
            data = self._fetch_data()
            new_live_games = []
            if data and "events" in data:
                for event in data["events"]:
                    details = self._extract_game_details(event)
                    if details and details["is_live"]:
                        self._fetch_odds(details)
                        new_live_games.append(details)

                # Filter for favorite teams only if the config is set
                if self.nba_config.get("show_favorite_teams_only", False):
                    new_live_games = [game for game in new_live_games 
                                     if game['home_abbr'] in self.favorite_teams or 
                                        game['away_abbr'] in self.favorite_teams]

                # Update game list and current game
                if new_live_games:
                    self.live_games = new_live_games
                    if not self.current_game or self.current_game not in self.live_games:
                        self.current_game_index = 0
                        self.current_game = self.live_games[0] if self.live_games else None
                        self.last_game_switch = current_time
                    else:
                        # Update current game with fresh data
                        self.current_game = new_live_games[self.current_game_index]
                else:
                    self.live_games = []
                    self.current_game = None

    def display(self, force_clear: bool = False) -> None:
        """Display live game information."""
        if not self.current_game:
            return
        super().display(force_clear)


class NBARecentManager(BaseNBAManager):
    """Manager for recently completed NBA games."""
    def __init__(self, config: Dict[str, Any], display_manager: DisplayManager, cache_manager: CacheManager):
        super().__init__(config, display_manager, cache_manager)
        self.recent_games = []
        self.current_game_index = 0
        self.last_update = 0
        self.update_interval = self.nba_config.get("recent_update_interval", 3600)  # Use config, default 1 hour
        self.recent_games_to_show = self.nba_config.get("recent_games_to_show", 5)  # Number of most recent games to display
        self.last_game_switch = 0
        self.game_display_duration = 15  # Display each game for 15 seconds

    def update(self):
        """Update recent games data."""
        current_time = time.time()
        if current_time - self.last_update < self.update_interval:
            return

        try:
            data = self._fetch_data()
            if not data or 'events' not in data:
                return

            events = data['events']
            new_recent_games = []
            for event in events:
                game = self._extract_game_details(event)
                if game and game['is_final']:
                    self._fetch_odds(game)
                    new_recent_games.append(game)

            # Filter for favorite teams only if the config is set
            if self.nba_config.get("show_favorite_teams_only", False):
                # Get all games involving favorite teams
                favorite_team_games = [game for game in new_recent_games
                                      if game['home_abbr'] in self.favorite_teams or
                                         game['away_abbr'] in self.favorite_teams]
                
                # Select one game per favorite team (most recent game for each team)
                team_games = []
                for team in self.favorite_teams:
                    # Find games where this team is playing
                    team_specific_games = [game for game in favorite_team_games
                                          if game['home_abbr'] == team or game['away_abbr'] == team]
                    
                    if team_specific_games:
                        # Sort by game time and take the most recent
                        team_specific_games.sort(key=lambda g: g.get('start_time_utc') or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
                        team_games.append(team_specific_games[0])
                
                # Sort the final list by game time (most recent first)
                team_games.sort(key=lambda g: g.get('start_time_utc') or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
            else:
                team_games = new_recent_games
                # Sort games by start time, most recent first, then limit to recent_games_to_show
                team_games.sort(key=lambda x: x.get('start_time_utc') or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
                team_games = team_games[:self.recent_games_to_show]
            self.recent_games = team_games

            if self.recent_games:
                if not self.current_game or self.current_game['id'] not in {g['id'] for g in self.recent_games}:
                    self.current_game_index = 0
                    self.current_game = self.recent_games[0]
                    self.last_game_switch = current_time
            else:
                self.current_game = None

            self.last_update = current_time

        except Exception as e:
            self.logger.error(f"[NBA] Error updating recent games: {e}", exc_info=True)

    def display(self, force_clear=False):
        """Display recent games."""
        if not self.recent_games:
            return

        try:
            current_time = time.time()

            # Check if it's time to switch games
            if len(self.recent_games) > 1 and current_time - self.last_game_switch >= self.game_display_duration:
                self.current_game_index = (self.current_game_index + 1) % len(self.recent_games)
                self.current_game = self.recent_games[self.current_game_index]
                self.last_game_switch = current_time
                force_clear = True

            # Draw the scorebug layout
            self._draw_scorebug_layout(self.current_game, force_clear)

        except Exception as e:
            self.logger.error(f"[NBA] Error displaying recent game: {e}", exc_info=True)


class NBAUpcomingManager(BaseNBAManager):
    """Manager for upcoming NBA games."""
    def __init__(self, config: Dict[str, Any], display_manager: DisplayManager, cache_manager: CacheManager):
        super().__init__(config, display_manager, cache_manager)
        self.upcoming_games = []
        self.current_game_index = 0
        self.last_update = 0
        self.update_interval = self.nba_config.get("upcoming_update_interval", 3600)  # Use config, default 1 hour
        self.upcoming_games_to_show = self.nba_config.get("upcoming_games_to_show", 5)  # Number of upcoming games to display
        self.last_game_switch = 0
        self.game_display_duration = 15  # Display each game for 15 seconds

    def update(self):
        """Update upcoming games data."""
        current_time = time.time()
        if current_time - self.last_update < self.update_interval:
            return

        try:
            data = self._fetch_data()
            if not data or 'events' not in data:
                return

            events = data['events']
            new_upcoming_games = []
            for event in events:
                game = self._extract_game_details(event)
                if game and game['is_upcoming']:
                    self._fetch_odds(game)
                    new_upcoming_games.append(game)

            # Filter for favorite teams only if the config is set
            if self.nba_config.get("show_favorite_teams_only", False):
                # Get all games involving favorite teams
                favorite_team_games = [game for game in new_upcoming_games
                                      if game['home_abbr'] in self.favorite_teams or
                                         game['away_abbr'] in self.favorite_teams]
                
                # Select one game per favorite team (earliest upcoming game for each team)
                team_games = []
                for team in self.favorite_teams:
                    # Find games where this team is playing
                    team_specific_games = [game for game in favorite_team_games
                                          if game['home_abbr'] == team or game['away_abbr'] == team]
                    
                    if team_specific_games:
                        # Sort by game time and take the earliest
                        team_specific_games.sort(key=lambda g: g.get('start_time_utc') or datetime.max.replace(tzinfo=timezone.utc))
                        team_games.append(team_specific_games[0])
                
                # Sort the final list by game time
                team_games.sort(key=lambda g: g.get('start_time_utc') or datetime.max.replace(tzinfo=timezone.utc))
            else:
                team_games = new_upcoming_games
                # Sort games by start time, soonest first, then limit to configured count
                team_games.sort(key=lambda x: x.get('start_time_utc') or datetime.max.replace(tzinfo=timezone.utc))
                team_games = team_games[:self.upcoming_games_to_show]
            self.upcoming_games = team_games

            if self.upcoming_games:
                if not self.current_game or self.current_game['id'] not in {g['id'] for g in self.upcoming_games}:
                    self.current_game_index = 0
                    self.current_game = self.upcoming_games[0]
            else:
                self.current_game = None
            
            self.last_update = current_time

        except Exception as e:
            self.logger.error(f"[NBA] Error updating upcoming games: {e}", exc_info=True)

    def display(self, force_clear=False):
        """Display upcoming games."""
        if not self.upcoming_games:
            return

        try:
            current_time = time.time()
            
            # Check if it's time to switch games
            if len(self.upcoming_games) > 1 and current_time - self.last_game_switch >= self.game_display_duration:
                # Move to next game
                self.current_game_index = (self.current_game_index + 1) % len(self.upcoming_games)
                self.current_game = self.upcoming_games[self.current_game_index]
                self.last_game_switch = current_time
                force_clear = True
            
            # Draw the scorebug layout
            self._draw_scorebug_layout(self.current_game, force_clear)

        except Exception as e:
            self.logger.error(f"[NBA] Error displaying upcoming game: {e}", exc_info=True) 