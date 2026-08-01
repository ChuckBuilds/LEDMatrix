"""SportsCore — the shared fetch/cache/config/render base for the sports
scoreboards. Split out of the former ``src/base_classes/sports.py``; see
docs/SPORTS_UNIFICATION.md for the layering.
"""

import logging
import os
import tempfile
import time
from abc import ABC, abstractmethod
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytz
import requests
from PIL import Image, ImageDraw, ImageFont
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from src.background_data_service import get_background_service

# Import new architecture components (individual classes will import what they need)
from src.base_classes.api_extractors import APIDataExtractor
from src.base_classes.data_sources import DataSource
from src.cache_manager import CacheManager
from src.display_manager import DisplayManager
from src.dynamic_team_resolver import DynamicTeamResolver
from src.logo_downloader import LogoDownloader, download_missing_logo
try:
    from src.base_odds_manager import BaseOddsManager as OddsManager
except ImportError:
    OddsManager = None


class SportsCore(ABC):
    # Which ScoreboardSkin render method this class's display path maps to.
    # SportsLive inherits the default; SportsUpcoming/SportsRecent override.
    SKIN_MODE = "live"

    def __init__(self, config: Dict[str, Any], display_manager: DisplayManager, cache_manager: CacheManager, logger: logging.Logger, sport_key: str):
        self.logger = logger
        self.config = config
        self.cache_manager = cache_manager
        self.config_manager = self.cache_manager.config_manager
        if OddsManager:
            try:
                self.odds_manager = OddsManager(
                    self.cache_manager, self.config_manager)
            except Exception as e:
                self.logger.warning(f"Failed to initialize OddsManager: {e}")
                self.odds_manager = None
        else:
            self.odds_manager = None
            self.logger.warning("OddsManager not available - odds functionality disabled")
        self.display_manager = display_manager
        self.display_width = self.display_manager.matrix.width
        self.display_height = self.display_manager.matrix.height

        self.sport_key = sport_key
        self.sport = None
        self.league = None
        
        # Initialize new architecture components (will be overridden by sport-specific classes)
        self.sport_config = None
        self.api_extractor: APIDataExtractor
        self.data_source: DataSource
        self.mode_config = config.get(f"{sport_key}_scoreboard", {})  # Changed config key
        self.is_enabled: bool = self.mode_config.get("enabled", False)
        self.show_odds: bool = self.mode_config.get("show_odds", False)
        # Use LogoDownloader to get the correct default logo directory for this sport
        default_logo_dir = Path(LogoDownloader().get_logo_directory(sport_key))
        self.logo_dir = self._initialize_logo_dir(default_logo_dir)
        self.update_interval: int = self.mode_config.get(
            "update_interval_seconds", 60)
        self.show_records: bool = self.mode_config.get('show_records', False)
        self.show_ranking: bool = self.mode_config.get('show_ranking', False)
        # Number of games to show (instead of time-based windows)
        self.recent_games_to_show: int = self.mode_config.get(
            "recent_games_to_show", 5)  # Show last 5 games
        self.upcoming_games_to_show: int = self.mode_config.get(
            "upcoming_games_to_show", 10)  # Show next 10 games
        self.show_favorite_teams_only: bool = self.mode_config.get("show_favorite_teams_only", False)
        self.show_all_live: bool = self.mode_config.get("show_all_live", False)

        self.session = requests.Session()
        retry_strategy = Retry(
            total=5,  # increased number of retries
            backoff_factor=1,  # increased backoff factor
            # added 429 to retry list
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["GET", "HEAD", "OPTIONS"]
        )
        adapter = HTTPAdapter(max_retries=retry_strategy)
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)

        self._logo_cache = {}

        # Set up headers
        self.headers = {
            'User-Agent': 'LEDMatrix/1.0 (https://github.com/yourusername/LEDMatrix; contact@example.com)',
            'Accept': 'application/json',
            'Accept-Language': 'en-US,en;q=0.9',
            'Accept-Encoding': 'gzip, deflate, br',
            'Connection': 'keep-alive'
        }
        self.last_update = 0
        self.current_game = None
        self.fonts = self._load_fonts()

        # Optional visual skin (see docs/SKIN_SYSTEM.md). "skin" is either a
        # skin id applied to all modes, or a per-mode mapping like
        # {"live": "retro", "recent": "built-in"}. Loaded lazily on first
        # render so a broken skin can never block startup.
        self._skin_config = self.mode_config.get("skin")
        self.skin_options = self.mode_config.get("skin_options", {}) or {}
        self._skin = None
        self._skin_load_attempted = False
        self._skin_failures = 0
        self._skin_slow_renders = 0
        
        # Initialize dynamic team resolver and resolve favorite teams
        self.dynamic_resolver = DynamicTeamResolver()
        raw_favorite_teams = self.mode_config.get("favorite_teams", [])
        self.favorite_teams = self.dynamic_resolver.resolve_teams(raw_favorite_teams, sport_key)
        
        # Log dynamic team resolution
        if raw_favorite_teams != self.favorite_teams:
            self.logger.info(f"Resolved dynamic teams: {raw_favorite_teams} -> {self.favorite_teams}")
        else:
            self.logger.info(f"Favorite teams: {self.favorite_teams}")
            
        self.logger.setLevel(logging.INFO)
        
        # Initialize team rankings cache
        self._team_rankings_cache = {}
        self._rankings_cache_timestamp = 0
        self._rankings_cache_duration = 3600  # Cache rankings for 1 hour

        # Initialize background data service with optimized settings
        # Hardcoded for memory optimization: 1 worker, 30s timeout, 3 retries
        self.background_service = get_background_service(self.cache_manager, max_workers=1)
        self.background_fetch_requests = {}  # Track background fetch requests
        self.background_enabled = True
        self.logger.info("Background service enabled with 1 worker (memory optimized)")

    def _initialize_logo_dir(self, configured_path: Path) -> Path:
        """Resolve and ensure a writable logo directory, falling back when necessary."""
        downloader = LogoDownloader()
        resolved_configured = self._resolve_project_path(configured_path)
        candidates = [resolved_configured] + self._get_logo_directory_fallbacks(resolved_configured)

        for candidate in candidates:
            candidate_path = self._resolve_project_path(candidate)
            if downloader.ensure_logo_directory(str(candidate_path)):
                if candidate_path != resolved_configured:
                    self.logger.warning(
                        "Configured logo directory '%s' is not writable; using fallback '%s'",
                        resolved_configured,
                        candidate_path,
                    )
                return candidate_path

        self.logger.error(
            "Unable to find a writable logo directory. Logos may fail to download (last attempted: %s)",
            resolved_configured,
        )
        return resolved_configured

    def _resolve_project_path(self, path: Path) -> Path:
        """Convert relative paths to absolute ones rooted at the project directory."""
        if path.is_absolute():
            return path
        project_root = Path(__file__).resolve().parents[2]
        return (project_root / path).resolve()

    def _get_logo_directory_fallbacks(self, configured_dir: Path) -> List[Path]:
        """Return fallback directories to try when the configured directory is not writable."""
        fallbacks: List[Path] = []

        env_override = os.environ.get("LEDMATRIX_LOGO_DIR")
        if env_override:
            env_path = Path(env_override)
            if not env_path.is_absolute():
                env_path = self._resolve_project_path(env_path)
            fallbacks.append(env_path / self.sport_key)

        cache_dir = getattr(self.cache_manager, "cache_dir", None)
        if cache_dir:
            fallbacks.append(Path(cache_dir) / "logos" / self.sport_key)

        try:
            fallbacks.append(Path.home() / ".ledmatrix" / "logos" / self.sport_key)
        except RuntimeError as e:
            self.logger.debug("Could not resolve home directory (expected for service users): %s", e)

        fallbacks.append(Path(tempfile.gettempdir()) / "ledmatrix_logos" / self.sport_key)

        unique_fallbacks: List[Path] = []
        seen = set()
        for candidate in fallbacks:
            if candidate == configured_dir:
                continue
            if candidate not in seen:
                unique_fallbacks.append(candidate)
                seen.add(candidate)

        return unique_fallbacks

    def _get_season_schedule_dates(self) -> tuple[str, str]:
        return "", ""

    def _draw_scorebug_layout(self, game: Dict, force_clear: bool = False) -> None:
        """Placeholder draw method - subclasses should override."""
        # This base method will be simple, subclasses provide specifics
        try:
            img = Image.new('RGB', (self.display_width, self.display_height), (0, 0, 0))
            draw = ImageDraw.Draw(img)
            status = game.get("status_text", "N/A")
            self._draw_text_with_outline(draw, status, (2, 2), self.fonts['status'])
            self.display_manager.image.paste(img, (0, 0))
            # Don't call update_display here, let subclasses handle it after drawing
        except Exception as e:
            self.logger.error(f"Error in base _draw_scorebug_layout: {e}", exc_info=True)


    def _resolve_skin_id(self) -> Optional[str]:
        """The skin id configured for this instance's mode, or None for the
        built-in renderer. Accepts a plain id (all modes) or a per-mode
        mapping ({"live": "retro-baseball", "recent": "built-in"})."""
        skin_id = self._skin_config
        if isinstance(skin_id, dict):
            skin_id = skin_id.get(self.SKIN_MODE)
        if not skin_id or not isinstance(skin_id, str) or skin_id == "built-in":
            return None
        return skin_id

    def _get_skin(self):
        """Lazily load the configured skin once. Returns None (built-in
        renderer) when no skin is configured or loading failed."""
        if not self._skin_load_attempted:
            self._skin_load_attempted = True
            skin_id = self._resolve_skin_id()
            if skin_id:
                try:
                    from src.skin_system import skin_runtime
                    self._skin = skin_runtime.load_skin(
                        skin_id, sport=self.sport, sport_key=self.sport_key,
                        options=self.skin_options)
                except Exception as e:
                    self.logger.error(f"Failed to load skin '{skin_id}': {e}", exc_info=True)
                    self._skin = None
        return self._skin

    def _render_game(self, game: Dict, force_clear: bool = False) -> None:
        """Render one game: try the configured skin first, fall back to the
        built-in _draw_scorebug_layout. A skin that raises 3 times in a row
        is disabled for the rest of the session."""
        skin = self._get_skin()
        if skin is not None and self._skin_failures < 3:
            try:
                from src.skin_system import skin_runtime
                ctx = skin_runtime.build_context(self, game)
                render = getattr(skin, f"render_{self.SKIN_MODE}")
                started = time.monotonic()
                handled = render(ctx, dict(game))
                elapsed = time.monotonic() - started
                if elapsed > 0.15 and self._skin_slow_renders < 5:
                    self._skin_slow_renders += 1
                    self.logger.warning(
                        f"Skin '{self._resolve_skin_id()}' took {elapsed * 1000:.0f}ms to "
                        f"render {self.SKIN_MODE} — slow renders stall the whole display loop")
                if handled:
                    self._skin_failures = 0
                    self.display_manager.image.paste(ctx.canvas, (0, 0))
                    self.display_manager.update_display()
                    return
            except Exception:
                self._skin_failures += 1
                outcome = ("disabling skin for this session" if self._skin_failures >= 3
                           else "falling back to built-in renderer")
                self.logger.error(
                    f"Skin '{self._resolve_skin_id()}' failed rendering {self.SKIN_MODE} "
                    f"({self._skin_failures}/3); {outcome}", exc_info=True)
        self._draw_scorebug_layout(game, force_clear)

    def render_skin_card(self, game: Dict, size: tuple) -> Optional[Image.Image]:
        """Render one game as a standalone card via the configured skin —
        for vegas mode and previews. Tries render_vegas_card at the given
        size, then the mode renderer on a card-sized canvas. Returns None
        when no skin is active or the skin declined, so callers can use
        their default rendering."""
        skin = self._get_skin()
        if skin is None or self._skin_failures >= 3:
            return None
        try:
            from src.skin_system import skin_runtime
            ctx = skin_runtime.build_context(self, game, size=size)
            card = skin.render_vegas_card(ctx, dict(game))
            if card is not None:
                return card
            ctx = skin_runtime.build_context(self, game, size=size)
            render = getattr(skin, f"render_{self.SKIN_MODE}")
            if render(ctx, dict(game)):
                return ctx.canvas
        except Exception:
            # Card failures count toward the same 3-strike session disable
            # as display failures — a skin broken for vegas shouldn't get
            # to throw on every scroll tick forever.
            self._skin_failures += 1
            self.logger.error(
                f"Skin '{self._resolve_skin_id()}' card render failed "
                f"({self._skin_failures}/3)", exc_info=True)
        return None

    def display(self, force_clear: bool = False) -> bool:
        """Common display method for all NCAA FB managers""" # Updated docstring
        if not self.is_enabled: # Check if module is enabled
             return False

        if not self.current_game:
            # Clear display if force_clear is True, even when there's no content
            # This prevents black screens when switching to modes with no content
            if force_clear:
                try:
                    self.display_manager.clear()
                    self.display_manager.update_display()
                except Exception as e:
                    self.logger.debug(f"Error clearing display when no content: {e}")
            
            current_time = time.time()
            if not hasattr(self, '_last_warning_time'):
                self._last_warning_time = 0
            if current_time - getattr(self, '_last_warning_time', 0) > 300:
                self.logger.warning(f"No game data available to display in {self.__class__.__name__}")
                setattr(self, '_last_warning_time', current_time)
            return False

        try:
            self._render_game(self.current_game, force_clear)
            # display_manager.update_display() should be called within subclass draw methods
            # or after calling display() in the main loop. Let's keep it out of the base display.
            return True
        except Exception as e:
             self.logger.error(f"Error during display call in {self.__class__.__name__}: {e}", exc_info=True)
             return False


    def _load_fonts(self):
        """Load fonts used by the scoreboard."""
        fonts = {}
        try:
            fonts['score'] = ImageFont.truetype("assets/fonts/PressStart2P-Regular.ttf", 10)
            fonts['time'] = ImageFont.truetype("assets/fonts/PressStart2P-Regular.ttf", 8)
            fonts['team'] = ImageFont.truetype("assets/fonts/PressStart2P-Regular.ttf", 8)
            fonts['status'] = ImageFont.truetype("assets/fonts/4x6-font.ttf", 6) # Using 4x6 for status
            fonts['detail'] = ImageFont.truetype("assets/fonts/4x6-font.ttf", 6) # Added detail font
            fonts['rank'] = ImageFont.truetype("assets/fonts/PressStart2P-Regular.ttf", 10)
            logging.info("Successfully loaded fonts") # Changed log prefix
        except IOError:
            logging.warning("Fonts not found, using default PIL font.") # Changed log prefix
            fonts['score'] = ImageFont.load_default()
            fonts['time'] = ImageFont.load_default()
            fonts['team'] = ImageFont.load_default()
            fonts['status'] = ImageFont.load_default()
            fonts['detail'] = ImageFont.load_default()
            fonts['rank'] = ImageFont.load_default()
        return fonts

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
        """Draw text with a black outline for better readability."""
        x, y = position
        for dx, dy in [(-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1)]:
            draw.text((x + dx, y + dy), text, font=font, fill=outline_color)
        draw.text((x, y), text, font=font, fill=fill)

    def _load_and_resize_logo(self, team_id: str, team_abbrev: str, logo_path: Path, logo_url: str | None ) -> Optional[Image.Image]:
        """Load and resize a team logo, with caching and automatic download if missing."""
        self.logger.debug(f"Logo path: {logo_path}")
        if team_abbrev in self._logo_cache:
            self.logger.debug(f"Using cached logo for {team_abbrev}")
            return self._logo_cache[team_abbrev]

        try:
            # Try different filename variations first (for cases like TA&M vs TAANDM)
            actual_logo_path = None
            filename_variations = LogoDownloader.get_logo_filename_variations(team_abbrev)
            
            for filename in filename_variations:
                test_path = logo_path.parent / filename
                if test_path.exists():
                    actual_logo_path = test_path
                    self.logger.debug(f"Found logo at alternative path: {actual_logo_path}")
                    break
            
            # If no variation found, try to download missing logo
            if not actual_logo_path and not logo_path.exists():
                self.logger.info(f"Logo not found for {team_abbrev} at {logo_path}. Attempting to download.")
                
                # Try to download the logo from ESPN API (this will create placeholder if download fails)
                download_missing_logo(self.sport_key, team_id, team_abbrev, logo_path, logo_url)
                actual_logo_path = logo_path

            # Use the original path if no alternative was found
            if not actual_logo_path:
                actual_logo_path = logo_path

            # Only try to open the logo if the file exists
            if os.path.exists(actual_logo_path):
                logo = Image.open(actual_logo_path)
            else:
                self.logger.error(f"Logo file still doesn't exist at {actual_logo_path} after download attempt")
                return None
            if logo.mode != 'RGBA':
                logo = logo.convert('RGBA')

            max_width = int(self.display_width * 1.5)
            max_height = int(self.display_height * 1.5)
            logo.thumbnail((max_width, max_height), Image.Resampling.LANCZOS)
            self._logo_cache[team_abbrev] = logo
            return logo

        except Exception as e:
            self.logger.error(f"Error loading logo for {team_abbrev}: {e}", exc_info=True)
            return None

    def _fetch_odds(self, game: Dict) -> None:
        """Fetch odds for a specific game using the new architecture."""
        try:
            if not self.show_odds:
                return
            
            if not self.odds_manager:
                return
            
            # Determine update interval based on game state
            is_live = game.get('is_live', False)
            update_interval = self.mode_config.get("live_odds_update_interval", 60) if is_live \
                else self.mode_config.get("odds_update_interval", 3600)
            
            # Fetch odds using OddsManager
            odds_data = self.odds_manager.get_odds(
                sport=self.sport,
                league=self.league,
                event_id=game['id'],
                update_interval_seconds=update_interval,
            )
            
            if odds_data:
                game['odds'] = odds_data
                self.logger.debug(f"Successfully fetched and attached odds for game {game['id']}")
            else:
                self.logger.debug(f"No odds data returned for game {game['id']}")
                
        except Exception as e:
            self.logger.error(f"Error fetching odds for game {game.get('id', 'N/A')}: {e}")

    def _get_timezone(self):
        try:
            timezone_str = self.config.get('timezone', 'UTC')
            return pytz.timezone(timezone_str)
        except pytz.UnknownTimeZoneError:
            return pytz.utc

    def _should_log(self, warning_type: str, cooldown: int = 60) -> bool:
        """Check if we should log a warning based on cooldown period."""
        current_time = time.time()
        if current_time - self._last_warning_time > cooldown:
            self._last_warning_time = current_time
            return True
        return False

    def _fetch_team_rankings(self) -> Dict[str, int]:
        """Fetch team rankings using the new architecture components."""
        current_time = time.time()
        
        # Check if we have cached rankings that are still valid
        if (self._team_rankings_cache and 
            current_time - self._rankings_cache_timestamp < self._rankings_cache_duration):
            return self._team_rankings_cache
        
        try:
            data = self.data_source.fetch_standings(self.sport, self.league)
            
            rankings = {}
            rankings_data = data.get('rankings', [])
            
            if rankings_data:
                # Use the first ranking (usually AP Top 25)
                first_ranking = rankings_data[0]
                teams = first_ranking.get('ranks', [])
                
                for team_data in teams:
                    team_info = team_data.get('team', {})
                    team_abbr = team_info.get('abbreviation', '')
                    current_rank = team_data.get('current', 0)
                    
                    if team_abbr and current_rank > 0:
                        rankings[team_abbr] = current_rank
            
            # Cache the results
            self._team_rankings_cache = rankings
            self._rankings_cache_timestamp = current_time
            
            self.logger.debug(f"Fetched rankings for {len(rankings)} teams")
            return rankings
            
        except Exception as e:
            self.logger.error(f"Error fetching team rankings: {e}")
            return {}

    def _extract_game_details_common(self, game_event: Dict) -> tuple[Dict | None, Dict | None, Dict | None, Dict | None, Dict | None]:
        if not game_event: 
            return None, None, None, None, None
        try:
            competition = game_event["competitions"][0]
            status = competition["status"]
            competitors = competition["competitors"]
            game_date_str = game_event["date"]
            situation = competition.get("situation")
            start_time_utc = None
            try:
                # Parse the datetime string
                if game_date_str.endswith('Z'):
                    game_date_str = game_date_str.replace('Z', '+00:00')
                dt = datetime.fromisoformat(game_date_str)
                # Ensure the datetime is UTC-aware (fromisoformat may create timezone-aware but not pytz.UTC)
                if dt.tzinfo is None:
                    # If naive, assume it's UTC
                    start_time_utc = dt.replace(tzinfo=pytz.UTC)
                else:
                    # Convert to pytz.UTC for consistency
                    start_time_utc = dt.astimezone(pytz.UTC)
            except ValueError:
                logging.warning(f"Could not parse game date: {game_date_str}")

            home_team = next((c for c in competitors if c.get("homeAway") == "home"), None)
            away_team = next((c for c in competitors if c.get("homeAway") == "away"), None)

            if not home_team or not away_team:
                self.logger.warning(f"Could not find home or away team in event: {game_event.get('id')}")
                return None, None, None, None, None

            try:
                home_abbr = home_team["team"]["abbreviation"]
            except KeyError:
                home_abbr = home_team["team"]["name"][:3]
            try:
                away_abbr = away_team["team"]["abbreviation"]
            except KeyError:
                away_abbr = away_team["team"]["name"][:3]
            
            # Check if this is a favorite team game BEFORE doing expensive logging
            is_favorite_game = (home_abbr in self.favorite_teams or away_abbr in self.favorite_teams)
            
            # Only log debug info for favorite team games
            if is_favorite_game:
                self.logger.debug(f"Processing favorite team game: {game_event.get('id')}")
                self.logger.debug(f"Found teams: {away_abbr}@{home_abbr}, Status: {status['type']['name']}, State: {status['type']['state']}")
            
            game_time, game_date = "", ""
            if start_time_utc:
                local_time = start_time_utc.astimezone(self._get_timezone())
                game_time = local_time.strftime("%I:%M%p").lstrip('0')
                
                # Check date format from config
                use_short_date_format = self.config.get('display', {}).get('use_short_date_format', False)
                if use_short_date_format:
                    game_date = local_time.strftime("%-m/%-d")
                else:
                    game_date = self.display_manager.format_date_with_ordinal(local_time)


            home_record = home_team.get('records', [{}])[0].get('summary', '') if home_team.get('records') else ''
            away_record = away_team.get('records', [{}])[0].get('summary', '') if away_team.get('records') else ''
            
            # Don't show "0-0" records - set to blank instead
            if home_record in {"0-0", "0-0-0"}:
                home_record = ''
            if away_record in {"0-0", "0-0-0"}:
                away_record = ''

            details = {
                "id": game_event.get("id"),
                "game_time": game_time,
                "game_date": game_date,
                "start_time_utc": start_time_utc,
                "status_text": status["type"]["shortDetail"], # e.g., "Final", "7:30 PM", "Q1 12:34"
                "is_live": status["type"]["state"] == "in",
                "is_final": status["type"]["state"] == "post",
                "is_upcoming": (status["type"]["state"] == "pre" or 
                               status["type"]["name"].lower() in ['scheduled', 'pre-game', 'status_scheduled']),
                "is_halftime": status["type"]["state"] == "halftime" or status["type"]["name"] == "STATUS_HALFTIME", # Added halftime check
                "is_period_break": status["type"]["name"] == "STATUS_END_PERIOD", # Added Period Break check
                "home_abbr": home_abbr,
                "home_id": home_team["id"],
                "home_score": home_team.get("score", "0"),
                "home_logo_path": self.logo_dir / Path(f"{LogoDownloader.normalize_abbreviation(home_abbr)}.png"),
                "home_logo_url": home_team["team"].get("logo"),
                "home_record": home_record,
                "away_record": away_record,
                "away_abbr": away_abbr,
                "away_id": away_team["id"],
                "away_score": away_team.get("score", "0"),
                "away_logo_path": self.logo_dir / Path(f"{LogoDownloader.normalize_abbreviation(away_abbr)}.png"),
                "away_logo_url": away_team["team"].get("logo"),
                "is_within_window": True, # Whether game is within display window

            }
            return details, home_team, away_team, status, situation
        except Exception as e:
            # Log the problematic event structure if possible
            logging.error(f"Error extracting game details: {e} from event: {game_event.get('id')}", exc_info=True)
            return None, None, None, None, None

    @abstractmethod
    def _extract_game_details(self, game_event: dict) -> dict | None:
        details, _, _, _, _ = self._extract_game_details_common(game_event)
        return details

    @abstractmethod
    def _fetch_data(self) -> Optional[Dict]:
        pass

    def _fetch_todays_games(self) -> Optional[Dict]:
        """Fetch only today's games for live updates (not entire season)."""
        try:
            tz = pytz.timezone("America/New_York")  # Use full name (not "EST") for DST support
            now = datetime.now(tz)
            yesterday = now - timedelta(days=1)
            formatted_date = now.strftime("%Y%m%d")
            formatted_date_yesterday = yesterday.strftime("%Y%m%d")
            # Fetch todays games only
            url = f"https://site.api.espn.com/apis/site/v2/sports/{self.sport}/{self.league}/scoreboard"
            response = self.session.get(url, params={"dates": f"{formatted_date_yesterday}-{formatted_date}", "limit": 1000}, headers=self.headers, timeout=10)
            response.raise_for_status()
            data = response.json()
            events = data.get('events', [])
            
            self.logger.info(f"Fetched {len(events)} todays games for {self.sport} - {self.league}")
            return {'events': events}
        except requests.exceptions.RequestException as e:
            self.logger.error(f"API error fetching todays games for {self.sport} - {self.league}: {e}")
            return None
        
    def _get_weeks_data(self) -> Optional[Dict]:
        """
        Get partial data for immediate display while background fetch is in progress.
        This fetches current/recent games only for quick response.
        """
        try:
            # Fetch current week and next few days for immediate display
            now = datetime.now(pytz.utc)
            immediate_events = []
            
            start_date = now + timedelta(weeks=-2)
            end_date = now + timedelta(weeks=1)
            date_str = f"{start_date.strftime('%Y%m%d')}-{end_date.strftime('%Y%m%d')}"
            url = f"https://site.api.espn.com/apis/site/v2/sports/{self.sport}/{self.league}/scoreboard"
            response = self.session.get(url, params={"dates": date_str, "limit": 1000},headers=self.headers, timeout=10)
            response.raise_for_status()
            data = response.json()
            immediate_events = data.get('events', [])
                
            if immediate_events:
                self.logger.info(f"Fetched {len(immediate_events)} events {date_str}")
                return {'events': immediate_events}
                
        except requests.exceptions.RequestException as e:
            self.logger.warning(f"Error fetching this weeks games for {self.sport} - {self.league} - {date_str}: {e}")
        return None

    def _custom_scorebug_layout(self, game: dict, draw_overlay: ImageDraw.ImageDraw):
        pass
