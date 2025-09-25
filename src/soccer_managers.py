import os
import time
import logging
import requests
import json
from typing import Dict, Any, Optional, List
from PIL import Image, ImageDraw, ImageFont
import random # Import random for placeholder logo generation
from pathlib import Path
from datetime import datetime, timedelta, timezone
from src.display_manager import DisplayManager
from src.cache_manager import CacheManager
from src.config_manager import ConfigManager
from src.odds_manager import OddsManager
from src.logo_downloader import download_missing_logo, get_soccer_league_key
from src.background_cache_mixin import BackgroundCacheMixin
import pytz

# Import the API counter function from web interface
try:
    from web_interface_v2 import increment_api_counter
except ImportError:
    # Fallback if web interface is not available
    def increment_api_counter(kind: str, count: int = 1):
        pass

# Constants
# ESPN_SOCCER_SCOREBOARD_URL = "https://site.api.espn.com/apis/site/v2/sports/soccer/scoreboards" # Old URL
ESPN_SOCCER_LEAGUE_SCOREBOARD_URL_FORMAT = "http://site.api.espn.com/apis/site/v2/sports/soccer/{}/scoreboard" # New format string
# Common league slugs (add more as needed)
LEAGUE_SLUGS = {
    "eng.1": "Premier League",
    "esp.1": "La Liga",
    "ger.1": "Bundesliga",
    "ita.1": "Serie A",
    "fra.1": "Ligue 1",
    "por.1": "Liga Portugal",
    "uefa.champions": "Champions League",
    "uefa.europa": "Europa League",
    "usa.1": "MLS",
    # Add other leagues here if needed
}

# Configure logging to match main configuration
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s.%(msecs)03d - %(levelname)s:%(name)s:%(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

class BaseSoccerManager(BackgroundCacheMixin):
    """Base class for Soccer managers with common functionality."""
    # Class variables for warning tracking
    _no_data_warning_logged = False
    _last_warning_time = 0
    _warning_cooldown = 60  # Only log warnings once per minute
    _shared_data = {}  # Dictionary to hold shared data per league/date
    _last_shared_update = {} # Dictionary for update times per league/date
    _soccer_config_shared = {} 
    _team_league_map = {} # In-memory cache for the map
    _map_last_updated = 0
    logger = logging.getLogger(__name__)  # Class-level logger for class methods
    logger.setLevel(logging.INFO)  # Set log level at class level

    def __init__(self, config: Dict[str, Any], display_manager: DisplayManager, cache_manager: CacheManager):
        self.display_manager = display_manager
        # Store reference to config instead of creating new ConfigManager
        self.config_manager = None  # Not used in this class
        self.config = config
        self.soccer_config = config.get("soccer_scoreboard", {}) # Use 'soccer_scoreboard' config
        BaseSoccerManager._soccer_config_shared = self.soccer_config # Store for class methods
        self.cache_manager = cache_manager
        self.odds_manager = OddsManager(self.cache_manager, None)
        self.is_enabled = self.soccer_config.get("enabled", False)
        self.show_odds = self.soccer_config.get("show_odds", False)
        self.test_mode = self.soccer_config.get("test_mode", False)
        self.logo_dir = self.soccer_config.get("logo_dir", "assets/sports/soccer_logos") # Soccer logos
        self.update_interval = self.soccer_config.get("update_interval_seconds", 60) # General fallback
        self.show_records = self.soccer_config.get('show_records', False)
        self.last_update = 0
        self.current_game = None
        self.fonts = self._load_fonts()
        self.favorite_teams = self.soccer_config.get("favorite_teams", [])
        self.target_leagues_config = self.soccer_config.get("leagues", list(LEAGUE_SLUGS.keys())) # Get target leagues from config
        self.recent_games_to_show = self.soccer_config.get("recent_games_to_show", 5) # Number of most recent games to display
        self.upcoming_games_to_show = self.soccer_config.get("upcoming_games_to_show", 5) # Number of upcoming games to display
        self.upcoming_fetch_days = self.soccer_config.get("upcoming_fetch_days", 7) # Days ahead to fetch (default: tomorrow)
        self.team_map_file = self.soccer_config.get("team_map_file", "assets/data/team_league_map.json")
        self.team_map_update_days = self.soccer_config.get("team_map_update_days", 7) # How often to update the map

        self.display_width = self.display_manager.matrix.width
        self.display_height = self.display_manager.matrix.height
        
        self._logo_cache = {}
        
        # Initialize background data service
        background_config = self.soccer_config.get("background_service", {})
        if background_config.get("enabled", True):  # Default to enabled
            max_workers = background_config.get("max_workers", 3)
            from src.background_data_service import BackgroundDataService
            self.background_service = BackgroundDataService(self.cache_manager, max_workers)
            self.background_fetch_requests = {}  # Track background fetch requests
            self.background_enabled = True
            self.logger.info(f"[Soccer] Background service enabled with {max_workers} workers")
        else:
            self.background_service = None
            self.background_fetch_requests = {}
            self.background_enabled = False
            self.logger.info("[Soccer] Background service disabled")

        # Ensure data directory exists
        os.makedirs(os.path.dirname(self.team_map_file), exist_ok=True)
        # Load or build the team map
        self._update_team_league_map_if_needed()
        
        self.logger.info(f"Initialized Soccer manager with display dimensions: {self.display_width}x{self.display_height}")
        self.logger.info(f"Logo directory: {self.logo_dir}")
        self.logger.info(f"Configured target leagues: {self.target_leagues_config}")
        self.logger.info(f"Upcoming fetch days: {self.upcoming_fetch_days}") # Log new setting
        self.logger.info(f"Recent games to show: {self.recent_games_to_show}") # Log new setting
        self.logger.info(f"Team map file: {self.team_map_file}")
        self.logger.info(f"Team map update interval: {self.team_map_update_days} days")
        
        # Log favorite teams configuration
        show_favorites_only = self.soccer_config.get("show_favorite_teams_only", False)
        if show_favorites_only:
            self.logger.info(f"Favorite teams filtering enabled. Favorite teams: {self.favorite_teams}")
        else:
            self.logger.info("Favorite teams filtering disabled. Showing all teams.")

    def _get_timezone(self):
        try:
            timezone_str = self.config.get('timezone', 'UTC')
            return pytz.timezone(timezone_str)
        except pytz.UnknownTimeZoneError:
            self.logger.warning(f"[Soccer] Unknown timezone: {timezone_str}, falling back to UTC")
            return pytz.utc
        except Exception as e:
            self.logger.error(f"[Soccer] Error getting timezone: {e}, falling back to UTC")
            return pytz.utc

    def _fetch_odds(self, game: Dict) -> None:
        """Fetch odds for a game and attach it to the game dictionary."""
        # Check if odds should be shown for this sport
        if not self.show_odds:
            return

        # Check if we should only fetch for favorite teams
        is_favorites_only = self.soccer_config.get("show_favorite_teams_only", False)
        if is_favorites_only:
            home_abbr = game.get('home_abbr')
            away_abbr = game.get('away_abbr')
            if not (home_abbr in self.favorite_teams or away_abbr in self.favorite_teams):
                self.logger.debug(f"Skipping odds fetch for non-favorite game in favorites-only mode: {away_abbr}@{home_abbr}")
                return

        self.logger.debug(f"Proceeding with odds fetch for game: {game.get('id', 'N/A')}")
        
        try:
            odds_data = self.odds_manager.get_odds(
                sport="soccer",
                league=game["league_slug"],
                event_id=game["id"]
            )
            if odds_data:
                game['odds'] = odds_data
        except Exception as e:
            self.logger.error(f"Error fetching odds for game {game.get('id', 'N/A')}: {e}")

    # --- Team League Map Management ---
    @classmethod
    def _load_team_league_map(cls) -> None:
        """Load the team-league map from the JSON file."""
        map_file = cls._soccer_config_shared.get("team_map_file", "assets/data/team_league_map.json")
        try:
            if os.path.exists(map_file):
                with open(map_file, 'r') as f:
                    data = json.load(f)
                    cls._team_league_map = data.get("map", {})
                    cls._map_last_updated = data.get("last_updated", 0)
                    cls.logger.info(f"[Soccer] Loaded team-league map ({len(cls._team_league_map)} teams) from {map_file}")
            else:
                cls.logger.info(f"[Soccer] Team-league map file not found: {map_file}. Will attempt to build.")
                cls._team_league_map = {}
                cls._map_last_updated = 0
        except (IOError, json.JSONDecodeError) as e:
            cls.logger.error(f"[Soccer] Error loading team-league map from {map_file}: {e}")
            cls._team_league_map = {}
            cls._map_last_updated = 0

    @classmethod
    def _save_team_league_map(cls) -> None:
        """Save the current team-league map to the JSON file."""
        map_file = cls._soccer_config_shared.get("team_map_file", "assets/data/team_league_map.json")
        try:
            timestamp = time.time()
            with open(map_file, 'w') as f:
                json.dump({"last_updated": timestamp, "map": cls._team_league_map}, f, indent=4)
            cls._map_last_updated = timestamp
            cls.logger.info(f"[Soccer] Saved team-league map ({len(cls._team_league_map)} teams) to {map_file}")
        except IOError as e:
            cls.logger.error(f"[Soccer] Error saving team-league map to {map_file}: {e}")

    @classmethod
    def _build_team_league_map(cls) -> None:
        """Fetch data for all known leagues to build the team-to-league map."""
        cls.logger.info("[Soccer] Building team-league map...")
        new_map = {}
        yesterday = (datetime.now(pytz.utc) - timedelta(days=1)).strftime('%Y%m%d')

        # Fetch data for all leagues defined in LEAGUE_SLUGS to get comprehensive team info
        for league_slug in LEAGUE_SLUGS.keys():
            try:
                url = ESPN_SOCCER_LEAGUE_SCOREBOARD_URL_FORMAT.format(league_slug)
                params = {'dates': yesterday, 'limit': 100}
                response = requests.get(url, params=params, timeout=10) # Add timeout
                response.raise_for_status()
                data = response.json()
                
                # Increment API counter for sports data
                increment_api_counter('sports', 1)
                cls.logger.debug(f"[Soccer Map Build] Fetched data for {league_slug}")

                for event in data.get("events", []):
                    event_league_slug = event.get("league", {}).get("slug")
                    if not event_league_slug: continue # Skip if league slug missing

                    competitors = event.get("competitions", [{}])[0].get("competitors", [])
                    for competitor in competitors:
                        team_abbr = competitor.get("team", {}).get("abbreviation")
                        if team_abbr and team_abbr not in new_map:
                            new_map[team_abbr] = event_league_slug
                            cls.logger.debug(f"[Soccer Map Build] Mapped {team_abbr} to {event_league_slug}")

            except requests.exceptions.RequestException as e:
                # Log errors but continue building map from other leagues
                cls.logger.warning(f"[Soccer Map Build] Error fetching data for {league_slug}: {e}")
            except Exception as e:
                cls.logger.error(f"[Soccer Map Build] Unexpected error processing {league_slug}: {e}", exc_info=True)

        if new_map:
            cls._team_league_map = new_map
            cls._save_team_league_map()
        else:
            cls.logger.warning("[Soccer Map Build] Failed to build team-league map. No team data found.")

    @classmethod
    def _update_team_league_map_if_needed(cls) -> None:
        """Check if the map needs updating and rebuild if necessary."""
        update_interval_seconds = cls._soccer_config_shared.get("team_map_update_days", 7) * 86400 # Convert days to seconds
        
        # Load map initially if not already loaded
        if not cls._team_league_map:
             cls._load_team_league_map()
             
        current_time = time.time()
        if not cls._team_league_map or (current_time - cls._map_last_updated > update_interval_seconds):
            cls.logger.info("[Soccer] Team-league map is missing or stale. Rebuilding...")
            cls._build_team_league_map()
        else:
            cls.logger.info(f"[Soccer] Team-league map is up-to-date (last updated: {datetime.fromtimestamp(cls._map_last_updated).strftime('%Y-%m-%d %H:%M:%S')}).")

    def _fetch_soccer_api_data(self, use_cache: bool = True) -> Optional[Dict]:
        """Fetch and cache data for all managers to share, iterating through target leagues."""
        current_time = time.time()
        all_data = {"events": []}
        favorite_teams = self.soccer_config.get("favorite_teams", [])
        target_leagues_config = self.soccer_config.get("leagues", list(LEAGUE_SLUGS.keys()))
        upcoming_fetch_days = self.soccer_config.get("upcoming_fetch_days", 1)

        leagues_to_fetch = set(target_leagues_config)
        
        today = datetime.now(pytz.utc).date()
        dates_to_fetch = [(today + timedelta(days=i)).strftime('%Y%m%d') for i in range(-1, upcoming_fetch_days + 1)]

        for league_slug in leagues_to_fetch:
            for fetch_date in dates_to_fetch:
                cache_key = f"soccer_{league_slug}_{fetch_date}"
                
                if use_cache:
                    cached_data = self.cache_manager.get(cache_key, max_age=300)
                    if cached_data:
                        self.logger.debug(f"[Soccer] Using cached data for {league_slug} on {fetch_date}")
                        if "events" in cached_data:
                            all_data["events"].extend(cached_data["events"])
                        continue
                
                try:
                    url = ESPN_SOCCER_LEAGUE_SCOREBOARD_URL_FORMAT.format(league_slug)
                    params = {'dates': fetch_date, 'limit': 100}
                    response = requests.get(url, params=params)
                    response.raise_for_status()
                    data = response.json()
                    
                    # Increment API counter for sports data
                    increment_api_counter('sports', 1)
                    
                    self.logger.info(f"[Soccer] Fetched data from ESPN API for {league_slug} on {fetch_date}")
                    
                    if use_cache:
                        self.cache_manager.set(cache_key, data)
                        
                    if "events" in data:
                        all_data["events"].extend(data["events"])

                except requests.exceptions.RequestException as e:
                    if response is not None and response.status_code == 404:
                         self.logger.debug(f"[Soccer] No data found (404) for {league_slug} on {fetch_date}. URL: {url}")
                         if use_cache:
                             self.cache_manager.set(cache_key, {"events": []})
                    else:
                         self.logger.error(f"[Soccer] Error fetching data for {league_slug} on {fetch_date}: {e}")

        return all_data

    def _get_live_leagues_to_fetch(self) -> set:
        """Determine which leagues to fetch for live data based on favorites and map."""
        if self.favorite_teams and self._team_league_map:
            leagues_to_fetch = set()
            for team in self.favorite_teams:
                league = self._team_league_map.get(team)
                if league:
                    leagues_to_fetch.add(league)
                else:
                    self.logger.warning(f"[Soccer Live] Favorite team '{team}' not found in team-league map.")
            # Fallback if map lookups fail
            if not leagues_to_fetch:
                 self.logger.warning("[Soccer Live] No leagues found for favorite teams in map. Falling back to configured leagues.")
                 return set(self.target_leagues_config)
            return leagues_to_fetch
        else:
            # No favorites or no map, use configured leagues
            return set(self.target_leagues_config)

    def _fetch_data(self, date_str: str = None) -> Optional[Dict]:
        """
        Fetch data using background service cache first, fallback to direct API call.
        This eliminates redundant caching and ensures Recent/Upcoming managers
        use the same data source as the background service.
        """
        return self._fetch_data_with_background_cache(
            sport_key='soccer',
            api_fetch_method=self._fetch_soccer_api_data,
            live_manager_class=SoccerLiveManager,
            use_new_extractor_system=True
        )

    def _load_fonts(self):
        """Load fonts used by the scoreboard."""
        fonts = {}
        try:
            fonts['score'] = ImageFont.truetype("assets/fonts/PressStart2P-Regular.ttf", 10) # Slightly larger score
            fonts['time'] = ImageFont.truetype("assets/fonts/PressStart2P-Regular.ttf", 8)
            fonts['team'] = ImageFont.truetype("assets/fonts/4x6-font.ttf", 6) # Keep team abbr small
            fonts['status'] = ImageFont.truetype("assets/fonts/4x6-font.ttf", 6) # Keep status small
            logging.info("[Soccer] Successfully loaded custom fonts")
        except IOError:
            logging.warning("[Soccer] Custom fonts not found, using default PIL font.")
            fonts['score'] = ImageFont.load_default()
            fonts['time'] = ImageFont.load_default()
            fonts['team'] = ImageFont.load_default()
            fonts['status'] = ImageFont.load_default()
        return fonts

    def _draw_text_with_outline(self, draw, text, position, font, fill=(255, 255, 255), outline_color=(0, 0, 0)):
        """Draw text with a black outline for better readability."""
        x, y = position
        # Draw outline
        for dx, dy in [(-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1)]:
            draw.text((x + dx, y + dy), text, font=font, fill=outline_color)
        # Draw text
        draw.text((x, y), text, font=font, fill=fill)

    def _load_and_resize_logo(self, team_abbrev: str) -> Optional[Image.Image]:
        """Load and resize a team logo, with caching."""
        if team_abbrev in self._logo_cache:
            return self._logo_cache[team_abbrev]

        # Try to find the logo file with case-insensitive matching and common variations
        logo_path = None
        expected_path = os.path.join(self.logo_dir, f"{team_abbrev}.png")
        
        # First try the exact path
        if os.path.exists(expected_path):
            logo_path = expected_path
        else:
            # Try case-insensitive matching and common variations
            try:
                for filename in os.listdir(self.logo_dir):
                    filename_lower = filename.lower()
                    team_abbrev_lower = team_abbrev.lower()
                    
                    # Exact case-insensitive match
                    if filename_lower == f"{team_abbrev_lower}.png":
                        logo_path = os.path.join(self.logo_dir, filename)
                        self.logger.debug(f"Found case-insensitive match: {filename} for {team_abbrev}")
                        break
                    
                    # Handle common team abbreviation variations
                    if team_abbrev == "MTL":
                        # Montreal variations
                        if filename_lower in ["cf_montral.png", "mon.png", "montreal.png"]:
                            logo_path = os.path.join(self.logo_dir, filename)
                            self.logger.debug(f"Found Montreal variation: {filename} for {team_abbrev}")
                            break
                    elif team_abbrev == "LAFC":
                        # LAFC variations
                        if filename_lower in ["lafc.png", "la_fc.png"]:
                            logo_path = os.path.join(self.logo_dir, filename)
                            self.logger.debug(f"Found LAFC variation: {filename} for {team_abbrev}")
                            break
                    elif team_abbrev == "NY":
                        # New York variations
                        if filename_lower in ["ny.png", "nycfc.png", "nyrb.png"]:
                            logo_path = os.path.join(self.logo_dir, filename)
                            self.logger.debug(f"Found NY variation: {filename} for {team_abbrev}")
                            break
                            
            except (OSError, PermissionError) as e:
                self.logger.warning(f"Error listing directory {self.logo_dir}: {e}")
        
        if logo_path is None:
            logo_path = expected_path  # Use original path for creation attempts
        
        self.logger.debug(f"Logo path: {logo_path}")

        # Check if logo exists in original path or cache directory
        cache_logo_path = None
        if hasattr(self.cache_manager, 'cache_dir') and self.cache_manager.cache_dir:
            cache_logo_dir = os.path.join(self.cache_manager.cache_dir, 'placeholder_logos')
            cache_logo_path = os.path.join(cache_logo_dir, f"{team_abbrev}.png")
        
        try:
            if not os.path.exists(logo_path) and not (cache_logo_path and os.path.exists(cache_logo_path)):
                self.logger.info(f"Logo not found for {team_abbrev} at {logo_path}. Attempting to download from ESPN.")
                
                # Try to download the logo from ESPN API for each configured league
                download_success = False
                for league_code in self.target_leagues_config:
                    if league_code in LEAGUE_SLUGS:
                        soccer_league_key = get_soccer_league_key(league_code)
                        self.logger.debug(f"Attempting to download {team_abbrev} logo from {league_code} ({soccer_league_key})")
                        
                        success = download_missing_logo(team_abbrev, soccer_league_key, team_abbrev)
                        if success:
                            self.logger.info(f"Successfully downloaded logo for {team_abbrev} from {league_code}")
                            download_success = True
                            break
                        else:
                            self.logger.debug(f"Failed to download {team_abbrev} logo from {league_code}")
                
                if not download_success:
                    self.logger.warning(f"Failed to download logo for {team_abbrev} from any configured league. Creating placeholder.")
                    # Try to create placeholder in cache directory instead of assets directory
                    cache_logo_path = None
                    try:
                        # Use cache directory for placeholder logos
                        if hasattr(self.cache_manager, 'cache_dir') and self.cache_manager.cache_dir:
                            cache_logo_dir = os.path.join(self.cache_manager.cache_dir, 'placeholder_logos')
                            os.makedirs(cache_logo_dir, exist_ok=True)
                            cache_logo_path = os.path.join(cache_logo_dir, f"{team_abbrev}.png")
                            
                            # Create placeholder logo
                            logo = Image.new('RGBA', (36, 36), (random.randint(50, 200), random.randint(50, 200), random.randint(50, 200), 255))
                            draw = ImageDraw.Draw(logo)
                            # Optionally add text to placeholder
                            try:
                                script_dir = os.path.dirname(os.path.abspath(__file__))
                                font_4x6 = os.path.abspath(os.path.join(script_dir, "../assets/fonts/4x6-font.ttf"))
                                placeholder_font = ImageFont.truetype(font_4x6, 12)
                                text_width = draw.textlength(team_abbrev, font=placeholder_font)
                                text_x = (36 - text_width) // 2
                                text_y = 10
                                draw.text((text_x, text_y), team_abbrev, fill=(0,0,0,255), font=placeholder_font)
                            except IOError:
                                pass # Font not found, skip text
                            logo.save(cache_logo_path)
                            self.logger.info(f"Created placeholder logo in cache at {cache_logo_path}")
                            # Update logo_path to use cache version
                            logo_path = cache_logo_path
                        else:
                            # No cache directory available, just use in-memory placeholder
                            raise PermissionError("No writable cache directory available")
                    except (PermissionError, OSError) as pe:
                        self.logger.debug(f"Could not create placeholder logo file for {team_abbrev}: {pe}")
                        # Return a simple in-memory placeholder instead
                        logo = Image.new('RGBA', (36, 36), (random.randint(50, 200), random.randint(50, 200), random.randint(50, 200), 255))
                        self._logo_cache[team_abbrev] = logo
                        return logo

            # Try to load logo from original path or cache directory
            logo_to_load = None
            if os.path.exists(logo_path):
                logo_to_load = logo_path
            elif cache_logo_path and os.path.exists(cache_logo_path):
                logo_to_load = cache_logo_path
                
            if logo_to_load:
                try:
                    logo = Image.open(logo_to_load)
                    if logo.mode != 'RGBA':
                        logo = logo.convert('RGBA')

                    # Resize logo to target size
                    target_size = 36 # Change target size to 36x36
                    # Use resize instead of thumbnail to force size if image is smaller
                    logo = logo.resize((target_size, target_size), Image.Resampling.LANCZOS)
                    self.logger.debug(f"Resized {team_abbrev} logo to {logo.size}")

                    self._logo_cache[team_abbrev] = logo
                    return logo
                except PermissionError as pe:
                    self.logger.warning(f"Permission denied accessing logo for {team_abbrev}: {pe}")
                    # Return a simple in-memory placeholder instead
                    logo = Image.new('RGBA', (36, 36), (random.randint(50, 200), random.randint(50, 200), random.randint(50, 200), 255))
                    self._logo_cache[team_abbrev] = logo
                    return logo

        except Exception as e:
            self.logger.error(f"Error loading logo for {team_abbrev}: {e}", exc_info=True)
            # Return a simple in-memory placeholder as fallback
            logo = Image.new('RGBA', (36, 36), (random.randint(50, 200), random.randint(50, 200), random.randint(50, 200), 255))
            self._logo_cache[team_abbrev] = logo
            return logo

    def _format_game_time(self, status: Dict) -> str:
        """Format game time display for soccer (e.g., HT, FT, 45', 90+2')."""
        status_type = status["type"]["name"]
        clock = status.get("displayClock", "0:00")
        period = status.get("period", 0)

        if status_type == "STATUS_FINAL":
            return "FT"
        if status_type == "STATUS_HALFTIME":
            return "HT"
        if status_type == "STATUS_SCHEDULED":
            return "" # Handled by is_upcoming
        if status_type == "STATUS_POSTPONED":
            return "PPD"
        if status_type == "STATUS_CANCELED":
            return "CANC"
        
        # Handle live game time
        if status_type in ["STATUS_IN_PROGRESS", "STATUS_FIRST_HALF", "STATUS_SECOND_HALF"]:
             # Simple clock display, potentially add period info if needed
             # Remove seconds for cleaner display
             if ':' in clock:
                 clock_parts = clock.split(':')
                 return f"{clock_parts[0]}'" # Display as minutes'
             else:
                 # Handle potential stoppage time format like "90:00+2"
                 if '+' in clock:
                     clock = clock.replace(':00', '') # Remove :00 for cleaner look
                 return clock
                 
        return clock # Default fallback

    def _extract_game_details(self, game_event: Dict) -> Optional[Dict]:
        """Extract relevant game details from ESPN Soccer API response."""
        if not game_event:
            return None

        try:
            competition = game_event["competitions"][0]
            status = competition["status"]
            competitors = competition["competitors"]
            game_date_str = game_event["date"]
            league_info = game_event.get("league", {})
            league_slug = league_info.get("slug", "unknown") # Default if missing
            league_name = league_info.get("name", league_slug)

            try:
                start_time_utc = datetime.fromisoformat(game_date_str.replace("Z", "+00:00"))
            except ValueError:
                logging.warning(f"[Soccer] Could not parse game date: {game_date_str}")
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

            game_time = ""
            game_date = ""
            if start_time_utc:
                local_time = start_time_utc.astimezone(self._get_timezone())
                game_time = local_time.strftime("%I:%M%p").lower().lstrip('0') # e.g., 2:30pm
                
                # Check date format from config
                use_short_date_format = self.config.get('display', {}).get('use_short_date_format', False)
                if use_short_date_format:
                    game_date = local_time.strftime("%-m/%-d")
                else:
                    game_date = self.display_manager.format_date_with_ordinal(local_time)

            status_type = status["type"]["name"]
            is_live = status_type in ["STATUS_IN_PROGRESS", "STATUS_FIRST_HALF", "STATUS_SECOND_HALF"]
            is_final = status_type in ["STATUS_FINAL", "STATUS_FULL_TIME"]
            is_upcoming = status_type == "STATUS_SCHEDULED"
            is_halftime = status_type == "STATUS_HALFTIME"

                    # Note: is_within_window calculation removed as it's no longer used for filtering
        # Recent games are now filtered by count instead of time window

            details = {
                "id": game_event["id"],
                "start_time_utc": start_time_utc,
                "status_text": status["type"]["shortDetail"],
                "game_clock_display": self._format_game_time(status),
                "period": status.get("period", 0), # 1st half, 2nd half, ET periods?
                "is_live": is_live or is_halftime, # Treat halftime as live for display purposes
                "is_final": is_final,
                "is_upcoming": is_upcoming,
                "home_abbr": home_team["team"]["abbreviation"],
                "home_score": home_team.get("score", "0"),
                "home_record": home_record,
                "home_logo": self._load_and_resize_logo(home_team["team"]["abbreviation"]),
                "away_abbr": away_team["team"]["abbreviation"],
                "away_score": away_team.get("score", "0"),
                "away_record": away_record,
                "away_logo": self._load_and_resize_logo(away_team["team"]["abbreviation"]),
                "game_time": game_time, # Formatted local time (e.g., 2:30pm)
                "game_date": game_date, # Formatted local date (e.g., 7/21)
                "league": league_name,
                "league_slug": league_slug
            }

            self.logger.debug(f"[Soccer] Extracted game: {details['away_abbr']} {details['away_score']} @ {details['home_abbr']} {details['home_score']} ({details['game_clock_display']}) - League: {details['league']} - Final: {details['is_final']}, Upcoming: {details['is_upcoming']}, Live: {details['is_live']}")

            # Basic validation (logos handled in loading)
            if not details["home_abbr"] or not details["away_abbr"]:
                 logging.warning(f"[Soccer] Missing team abbreviation in game data: {game_event['id']}")
                 return None

            return details
        except Exception as e:
            logging.error(f"[Soccer] Error extracting game details for event {game_event.get('id', 'N/A')}: {e}", exc_info=True)
            return None

    def _draw_scorebug_layout(self, game: Dict, force_clear: bool = False) -> None:
        """Draw the soccer scorebug layout."""
        try:
            main_img = Image.new('RGB', (self.display_width, self.display_height), (0, 0, 0))
            draw = ImageDraw.Draw(main_img)

            home_logo = game.get("home_logo")
            away_logo = game.get("away_logo")
            logo_size = 36 # Defined logo size

            # --- Layout Configuration ---
            # Vertically center logos if possible, otherwise place near top
            logo_y = (self.display_height - logo_size) // 2
            if logo_y < 0: logo_y = 0 # Ensure non-negative
            
            # Place logos near edges
            away_logo_x = 1
            home_logo_x = self.display_width - logo_size - 1

            center_x = self.display_width // 2
            abbr_font = self.fonts['team']
            abbr_height = abbr_font.size if hasattr(abbr_font, 'size') else 6
            abbr_y = self.display_height - abbr_height - 1 # Position at the very bottom
            
            status_font = self.fonts['status']
            status_y = 1 # Status/Time at the top center
            score_font = self.fonts['score']
            score_height = score_font.size if hasattr(score_font, 'size') else 10
            score_y = (self.display_height - score_height) // 2 # Center score vertically

            # --- Draw Logos ---
            if away_logo:
                main_img.paste(away_logo, (away_logo_x, logo_y), away_logo)
            if home_logo:
                main_img.paste(home_logo, (home_logo_x, logo_y), home_logo)

            # --- Draw Team Abbreviations (Bottom) ---
            away_abbr = game.get("away_abbr", "AWAY")
            home_abbr = game.get("home_abbr", "HOME")
            
            away_abbr_width = draw.textlength(away_abbr, font=abbr_font)
            home_abbr_width = draw.textlength(home_abbr, font=abbr_font)

            # Position abbreviations: One left-aligned, one right-aligned
            # Add a small margin from the edges
            away_abbr_x = 2 
            home_abbr_x = self.display_width - home_abbr_width - 2

            self._draw_text_with_outline(draw, away_abbr, (away_abbr_x, abbr_y), abbr_font)
            self._draw_text_with_outline(draw, home_abbr, (home_abbr_x, abbr_y), abbr_font)

            # --- Draw Score / Game Time / Status ---
            # Determine fonts to use based on game state
            score_font = self.fonts['score']
            # Use 'time' font for status in Live/Final, 'status' font for "Next Game" in Upcoming
            status_font_top = self.fonts['status'] if game.get("is_upcoming") else self.fonts['time'] 
            time_font_center = self.fonts['time']
            center_y = self.display_height // 2 # Re-calculate or ensure it's available here

            if game.get("is_upcoming"):
                # Upcoming: "Next Game" top center, Date/Time vertically centered like NHL
                game_date = game.get("game_date", "")
                game_time = game.get("game_time", "")
                
                # Draw "Next Game" at the top center
                status_text = "Next Game"
                status_width = draw.textlength(status_text, font=status_font_top)
                status_x = (self.display_width - status_width) // 2
                status_y_top = 2 # Specific Y position from NHL spec
                self._draw_text_with_outline(draw, status_text, (status_x, status_y_top), status_font_top)

                # Calculate position for the date text (centered horizontally, above vertical center)
                date_width = draw.textlength(game_date, font=time_font_center)
                date_x = (self.display_width - date_width) // 2
                date_y = center_y - 5 # Specific Y position from NHL spec
                self._draw_text_with_outline(draw, game_date, (date_x, date_y), time_font_center)

                # Calculate position for the time text (centered horizontally, below date)
                time_width = draw.textlength(game_time, font=time_font_center)
                time_x = (self.display_width - time_width) // 2
                time_y = date_y + 10 # Specific Y position from NHL spec (relative to date_y)
                self._draw_text_with_outline(draw, game_time, (time_x, time_y), time_font_center)

            else:
                # Live/Final: Show Score centered vertically, Status top center
                home_score = str(game.get("home_score", "0"))
                away_score = str(game.get("away_score", "0"))
                score_text = f"{away_score}-{home_score}"

                score_width = draw.textlength(score_text, font=score_font)
                score_x = center_x - score_width // 2
                score_y = (self.display_height - score_height) // 2 # Keep score centered vertically

                self._draw_text_with_outline(draw, score_text, (score_x, score_y), score_font)

                # Use status_font_top (which is self.fonts['time'] in this else block) for consistency
                status_text = game.get("game_clock_display", "")
                status_width = draw.textlength(status_text, font=status_font_top) 
                status_x = center_x - status_width // 2
                status_y_top = 1 # Original Y position for live/final status
                self._draw_text_with_outline(draw, status_text, (status_x, status_y_top), status_font_top)

            # Display odds if available
            if 'odds' in game:
                odds = game['odds']
                spread = odds.get('spread', {}).get('point', None)
                if spread is not None:
                    # Format spread text
                    spread_text = f"{spread:+.1f}" if spread > 0 else f"{spread:.1f}"
                    
                    # Choose color and position based on which team has the spread
                    if odds.get('spread', {}).get('team') == game['home_abbr']:
                        text_color = (255, 100, 100) # Reddish
                        spread_x = self.display_width - draw.textlength(spread_text, font=self.fonts['status'])
                    else:
                        text_color = (100, 255, 100) # Greenish
                        spread_x = 0
                    
                    spread_y = 0
                    self._draw_text_with_outline(draw, spread_text, (spread_x, spread_y), self.fonts['status'], fill=text_color)

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

            # --- Display Image ---
            self.display_manager.image.paste(main_img, (0, 0))
            self.display_manager.update_display()

        except Exception as e:
            self.logger.error(f"Error displaying soccer game: {e}", exc_info=True)

    def display(self, force_clear: bool = False) -> None:
        """Common display method for all Soccer managers"""
        if not self.current_game:
            current_time = time.time()
            if not hasattr(self, '_last_warning_time'):
                self._last_warning_time = 0
            if current_time - self._last_warning_time > 300:
                self.logger.warning("[Soccer] No game data available to display")
                self._last_warning_time = current_time
            return

        self._draw_scorebug_layout(self.current_game, force_clear)

# ===============================================
# Manager Implementations (Live, Recent, Upcoming)
# ===============================================

class SoccerLiveManager(BaseSoccerManager):
    """Manager for live Soccer games."""
    def __init__(self, config: Dict[str, Any], display_manager: DisplayManager, cache_manager: CacheManager):
        super().__init__(config, display_manager, cache_manager)
        self.update_interval = self.soccer_config.get("live_update_interval", 20) # Slightly longer for soccer?
        self.no_data_interval = 300
        self.last_update = 0
        self.logger.info("Initialized Soccer Live Manager")
        self.live_games = []
        self.current_game_index = 0
        self.last_game_switch = 0
        self.game_display_duration = self.soccer_config.get("live_game_duration", 20)
        self.last_display_update = 0
        self.last_log_time = 0
        self.log_interval = 300

        if self.test_mode:
            # Simple test game
            self.current_game = {
                "id": "test001",
                "home_abbr": "FCB", "away_abbr": "RMA", "home_score": "1", "away_score": "1",
                "game_clock_display": "65'", "period": 2, "is_live": True, "is_final": False, "is_upcoming": False,
                "home_logo": self._load_and_resize_logo("FCB"), "away_logo": self._load_and_resize_logo("RMA"),
                "league": "Test League"
            }
            self.live_games = [self.current_game]
            logging.info("[Soccer] Initialized SoccerLiveManager with test game: FCB vs RMA")
        else:
            logging.info("[Soccer] Initialized SoccerLiveManager in live mode")

    def update(self):
        """Update live game data."""
        current_time = time.time()
        interval = self.no_data_interval if not self.live_games else self.update_interval

        if current_time - self.last_update >= interval:
            self.last_update = current_time

            if self.test_mode:
                # Basic test mode clock update
                if self.current_game and self.current_game["is_live"]:
                    try:
                        minutes_str = self.current_game["game_clock_display"].replace("'", "")
                        minutes = int(minutes_str)
                        minutes += 1
                        if minutes == 45: self.current_game["game_clock_display"] = "HT"
                        elif minutes == 46: self.current_game["period"] = 2 # Start 2nd half
                        elif minutes > 90: self.current_game["game_clock_display"] = "FT"; self.current_game["is_live"]=False; self.current_game["is_final"]=True
                        else: self.current_game["game_clock_display"] = f"{minutes}'"
                    except ValueError: # Handle HT, FT states
                         if self.current_game["game_clock_display"] == "HT":
                             self.current_game["game_clock_display"] = "46'" # Start 2nd half after HT
                             self.current_game["period"] = 2
                         pass # Do nothing if FT or other non-numeric
                    # Always update display in test mode
                    # self.display(force_clear=True) # Display handled by controller loop
            else:
                # Fetch live game data
                data = self._fetch_data()
                new_live_games = []
                if data and "events" in data:
                    for event in data["events"]:
                        details = self._extract_game_details(event)
                        # Ensure it's live
                        if details and details["is_live"]:
                            self._fetch_odds(details)
                            new_live_games.append(details)
                    
                    # Filter for favorite teams only if the config is set
                    if self.soccer_config.get("show_favorite_teams_only", False):
                        new_live_games = [game for game in new_live_games if game['home_abbr'] in self.favorite_teams or game['away_abbr'] in self.favorite_teams]

                    # Logging
                    should_log = (current_time - self.last_log_time >= self.log_interval or
                                  len(new_live_games) != len(self.live_games) or
                                  not self.live_games)
                    if should_log:
                        if new_live_games:
                            filter_text = "favorite teams" if self.soccer_config.get("show_favorite_teams_only", False) else "all teams"
                            self.logger.info(f"[Soccer] Found {len(new_live_games)} live games involving {filter_text}.")
                            for game in new_live_games:
                                self.logger.info(f"[Soccer] Live game: {game['away_abbr']} vs {game['home_abbr']} ({game['game_clock_display']}) - {game['league']}")
                        else:
                            filter_text = "favorite teams" if self.soccer_config.get("show_favorite_teams_only", False) and self.favorite_teams else "criteria"
                            self.logger.info(f"[Soccer] No live games found matching {filter_text}.")
                        self.last_log_time = current_time

                    # Update game list and current game
                    if new_live_games:
                         # Check if the list of games actually changed (based on ID)
                         new_game_ids = {game['id'] for game in new_live_games}
                         current_game_ids = {game['id'] for game in self.live_games}

                         if new_game_ids != current_game_ids:
                             self.live_games = sorted(new_live_games, key=lambda x: x['start_time_utc'] or datetime.now(pytz.utc)) # Sort by time
                             # Reset index if current game is gone or list is new
                             if not self.current_game or self.current_game['id'] not in new_game_ids:
                                 self.current_game_index = 0
                                 if self.live_games:
                                     self.current_game = self.live_games[0]
                                     self.last_game_switch = current_time # Reset switch timer
                                 else:
                                     self.current_game = None
                             else:
                                # Update the currently displayed game data if it still exists
                                 try:
                                     current_game_id = self.current_game['id']
                                     self.current_game = next(g for g in new_live_games if g['id'] == current_game_id)
                                 except StopIteration:
                                     # Should not happen if check above works, but handle defensively
                                     self.current_game_index = 0
                                     self.current_game = self.live_games[0] if self.live_games else None
                                     self.last_game_switch = current_time

                         else: # Games are the same, just update data
                             updated_live_games = []
                             for existing_game in self.live_games:
                                try:
                                    updated_game = next(g for g in new_live_games if g['id'] == existing_game['id'])
                                    updated_live_games.append(updated_game)
                                    # Update current_game if it's the one being displayed
                                    if self.current_game and self.current_game['id'] == updated_game['id']:
                                        self.current_game = updated_game
                                except StopIteration:
                                    pass # Game disappeared between checks?
                             self.live_games = updated_live_games


                         # Limit display updates
                         # if current_time - self.last_display_update >= 1.0:
                         #    self.display(force_clear=True) # Display handled by controller
                         #    self.last_display_update = current_time
                    else:
                        # No live games found
                        if self.live_games: # Log only if previously had games
                            filter_text = "favorite teams" if self.soccer_config.get("show_favorite_teams_only", False) else "criteria"
                            self.logger.info(f"[Soccer] All live games have ended or no longer match {filter_text}.")
                        self.live_games = []
                        self.current_game = None


                # Check if it's time to switch displayed game
                if len(self.live_games) > 1 and (current_time - self.last_game_switch) >= self.game_display_duration:
                    self.current_game_index = (self.current_game_index + 1) % len(self.live_games)
                    self.current_game = self.live_games[self.current_game_index]
                    self.last_game_switch = current_time
                    # Force display handled by controller on mode switch
                    # self.display(force_clear=True) # Display handled by controller
                    # self.last_display_update = current_time

    def display(self, force_clear: bool = False):
        """Display live game information."""
        # This method might be redundant if controller handles display calls
        # but keep it for potential direct calls or consistency
        if not self.current_game:
            # Optionally clear screen or show 'No Live Games' message
            # self.display_manager.clear_display() # Example
            return
        super().display(force_clear)


class SoccerRecentManager(BaseSoccerManager):
    """Manager for recently completed Soccer games."""
    def __init__(self, config: Dict[str, Any], display_manager: DisplayManager, cache_manager: CacheManager):
        super().__init__(config, display_manager, cache_manager)
        self.recent_games = [] # Holds all fetched recent games matching criteria
        self.games_list = []   # Holds games filtered by favorite teams (if applicable)
        self.current_game_index = 0
        self.last_update = 0
        # Use configurable update interval, default to 300s (5 min)
        self.update_interval = self.soccer_config.get("recent_update_interval", 300) 
        self.last_game_switch = 0
        self.game_display_duration = 15 # Short display time for recent/upcoming
        self.logger.info(f"Initialized SoccerRecentManager (Update Interval: {self.update_interval}s)")

    def update(self):
        """Update recent games data."""
        current_time = time.time()
        if current_time - self.last_update < self.update_interval:
            return

        self.last_update = current_time
        try:
            data = self._fetch_data() # Fetches shared data (past/present/future)
            if not data or 'events' not in data:
                self.logger.warning("[Soccer] No recent events found in ESPN API response")
                self.recent_games = []
                self.games_list = []
                self.current_game = None
                return

            # Process and filter games
            new_recent_games = []

            for event in data['events']:
                game = self._extract_game_details(event)
                if game and game['is_final'] and game.get('start_time_utc'):
                    self._fetch_odds(game)
                    new_recent_games.append(game)

            # Filter for favorite teams only if the config is set
            if self.soccer_config.get("show_favorite_teams_only", False):
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
                        team_specific_games.sort(key=lambda g: g['start_time_utc'], reverse=True)
                        team_games.append(team_specific_games[0])
                
                # Sort the final list by game time (most recent first)
                team_games.sort(key=lambda g: g['start_time_utc'], reverse=True)
            else:
                team_games = new_recent_games
                # Sort games by start time, most recent first, and limit to recent_games_to_show
                team_games.sort(key=lambda x: x['start_time_utc'], reverse=True)
                team_games = team_games[:self.recent_games_to_show]

            # Update only if the list content changes
            new_ids = {g['id'] for g in team_games}
            current_ids = {g['id'] for g in self.games_list}

            if new_ids != current_ids:
                self.logger.info(f"[Soccer] Found {len(team_games)} recent games (showing {self.recent_games_to_show} most recent).")
                self.recent_games = team_games
                self.games_list = team_games
                
                # Reset display index if needed
                if not self.current_game or self.current_game['id'] not in new_ids:
                    self.current_game_index = 0
                    if self.games_list:
                        self.current_game = self.games_list[0]
                        self.last_game_switch = current_time # Reset timer when list changes
                    else:
                        self.current_game = None

        except Exception as e:
            self.logger.error(f"[Soccer] Error updating recent games: {e}", exc_info=True)
            self.games_list = []
            self.current_game = None

    def display(self, force_clear=False):
        """Display recent games, rotating through games_list."""
        if not self.games_list:
            # self.logger.debug("[Soccer] No recent games to display") # Too noisy
            return # Skip display update entirely

        try:
            current_time = time.time()

            # Check if it's time to switch games
            if len(self.games_list) > 1 and current_time - self.last_game_switch >= self.game_display_duration:
                self.current_game_index = (self.current_game_index + 1) % len(self.games_list)
                self.current_game = self.games_list[self.current_game_index]
                self.last_game_switch = current_time
                force_clear = True # Force clear when switching games

            # Ensure current_game is set (it might be None initially)
            if not self.current_game and self.games_list:
                 self.current_game = self.games_list[self.current_game_index]
                 force_clear = True # Force clear on first display

            if self.current_game:
                 self._draw_scorebug_layout(self.current_game, force_clear)
                 # Display update handled by controller loop
                 # self.display_manager.update_display()

        except Exception as e:
            self.logger.error(f"[Soccer] Error displaying recent game: {e}", exc_info=True)


class SoccerUpcomingManager(BaseSoccerManager):
    """Manager for upcoming Soccer games."""
    def __init__(self, config: Dict[str, Any], display_manager: DisplayManager, cache_manager: CacheManager):
        super().__init__(config, display_manager, cache_manager)
        self.upcoming_games = [] # Filtered list for display
        self.current_game_index = 0
        self.last_update = 0
        # Use configurable update interval, default to 300s (5 min)
        self.update_interval = self.soccer_config.get("upcoming_update_interval", 300) 
        self.last_log_time = 0
        self.log_interval = 300
        self.last_warning_time = 0
        self.warning_cooldown = 300
        self.last_game_switch = 0
        self.game_display_duration = 15 # Short display time
        self.logger.info(f"Initialized SoccerUpcomingManager (Update Interval: {self.update_interval}s)")

    def update(self):
        """Update upcoming games data."""
        current_time = time.time()
        if current_time - self.last_update < self.update_interval:
            return

        self.last_update = current_time
        try:
            data = self._fetch_data() # Fetches shared data
            if not data or 'events' not in data:
                self.logger.warning("[Soccer] No upcoming events found in ESPN API response")
                self.upcoming_games = []
                self.current_game = None
                return

            # Process and filter games
            new_upcoming_games = []
            now_utc = datetime.now(pytz.utc)

            for event in data['events']:
                game = self._extract_game_details(event)
                # Must be upcoming and have a start time
                if game and game['is_upcoming'] and game.get('start_time_utc') and \
                   game['start_time_utc'] >= now_utc:
                    self._fetch_odds(game)
                    new_upcoming_games.append(game)
            
            # Filter for favorite teams only if the config is set
            if self.soccer_config.get("show_favorite_teams_only", False):
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
                        team_specific_games.sort(key=lambda g: g['start_time_utc'])
                        team_games.append(team_specific_games[0])
                
                # Sort the final list by game time
                team_games.sort(key=lambda g: g['start_time_utc'])
            else:
                team_games = new_upcoming_games
                # Sort games by start time, soonest first, then limit to configured count
                team_games.sort(key=lambda x: x['start_time_utc'])
                team_games = team_games[:self.upcoming_games_to_show]

             # Update only if the list content changes
            new_ids = {g['id'] for g in team_games}
            current_ids = {g['id'] for g in self.upcoming_games}

            if new_ids != current_ids:
                # Logging
                should_log = (current_time - self.last_log_time >= self.log_interval or
                              len(team_games) != len(self.upcoming_games) or
                              not self.upcoming_games)
                if should_log:
                    if team_games:
                        self.logger.info(f"[Soccer] Found {len(team_games)} upcoming games matching criteria.")
                        # Log first few games for brevity
                        for game in team_games[:3]:
                            self.logger.info(f"[Soccer] Upcoming game: {game['away_abbr']} vs {game['home_abbr']} ({game['game_date']} {game['game_time']}) - {game['league']}")
                    else:
                        self.logger.info("[Soccer] No upcoming games found matching criteria.")
                    self.last_log_time = current_time

                self.upcoming_games = team_games

                # Reset display index if needed
                if not self.current_game or self.current_game['id'] not in new_ids:
                    self.current_game_index = 0
                    if self.upcoming_games:
                        self.current_game = self.upcoming_games[0]
                        self.last_game_switch = current_time # Reset timer
                    else:
                        self.current_game = None

        except Exception as e:
            self.logger.error(f"[Soccer] Error updating upcoming games: {e}", exc_info=True)
            self.upcoming_games = []
            self.current_game = None

    def display(self, force_clear=False):
        """Display upcoming games, rotating through upcoming_games list."""
        if not self.upcoming_games:
            current_time = time.time()
            if current_time - self.last_warning_time > self.warning_cooldown:
                # self.logger.info("[Soccer] No upcoming games to display") # Too noisy
                self.last_warning_time = current_time
            return # Skip display update entirely

        try:
            current_time = time.time()

            # Check if it's time to switch games
            if len(self.upcoming_games) > 1 and current_time - self.last_game_switch >= self.game_display_duration:
                self.current_game_index = (self.current_game_index + 1) % len(self.upcoming_games)
                self.current_game = self.upcoming_games[self.current_game_index]
                self.last_game_switch = current_time
                force_clear = True # Force clear when switching games

            # Ensure current_game is set
            if not self.current_game and self.upcoming_games:
                 self.current_game = self.upcoming_games[self.current_game_index]
                 force_clear = True

            if self.current_game:
                 self._draw_scorebug_layout(self.current_game, force_clear)
                 # Update display handled by controller loop
                 # self.display_manager.update_display()

        except Exception as e:
            self.logger.error(f"[Soccer] Error displaying upcoming game: {e}", exc_info=True) 