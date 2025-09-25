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

# Import baseball and standard sports classes
from .base_classes.baseball import Baseball, BaseballLive
from .base_classes.sports import SportsRecent, SportsUpcoming

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
        
        # Store reference to config instead of creating new ConfigManager
        self.config_manager = None  # Not used in this class
        self.odds_manager = OddsManager(self.cache_manager, self.config_manager)
        
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
            total=3,
            backoff_factor=1,
            status_forcelist=[429, 500, 502, 503, 504]
        )
        adapter = HTTPAdapter(max_retries=retry_strategy)
        self.session.mount("http://", adapter)
        self.session.mount("https://", adapter)
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }

    def _fetch_odds(self, game: Dict) -> None:
        """Fetch odds for a game and attach it to the game dictionary."""
        # Check if odds should be shown for this sport
        if not self.show_odds:
            return

        # Check if we should only fetch for favorite teams
        is_favorites_only = self.ncaa_baseball_config.get("show_favorite_teams_only", False)
        if is_favorites_only:
            home_team = game.get('home_team')
            away_team = game.get('away_team')
            if not (home_team in self.favorite_teams or away_team in self.favorite_teams):
                self.logger.debug(f"Skipping odds fetch for non-favorite game in favorites-only mode: {away_team}@{home_team}")
                return

        self.logger.debug(f"Proceeding with odds fetch for game: {game.get('id', 'N/A')}")
        
        try:
            odds_data = self.odds_manager.get_odds(
                sport="baseball",
                league="college-baseball",
                event_id=game["id"]
            )
            if odds_data:
                game['odds'] = odds_data
        except Exception as e:
            self.logger.error(f"Error fetching odds for game {game.get('id', 'N/A')}: {e}")

    def _get_team_logo(self, team_abbr: str) -> Optional[Image.Image]:
        """Get team logo from the configured directory or generate a fallback."""
        try:
            logo_path = os.path.join(self.logo_dir, f"{team_abbr}.png")
            if os.path.exists(logo_path):
                img = Image.open(logo_path)
                if img.mode != 'RGBA':
                    img = img.convert('RGBA')
                return img
            else:
                logger.warning(f"[NCAABaseball] Logo not found for team {team_abbr}. Generating fallback.")
                # Create a fallback image with the team abbreviation, ensure it's RGBA
                logo_size = (32,32) # default size for fallback
                image = Image.new('RGBA', logo_size, color=(0, 0, 0, 255)) # RGBA with full opacity
                draw = ImageDraw.Draw(image)
                
                # Attempt to use a small, clear font
                try:
                    font_path = "assets/fonts/PressStart2P-Regular.ttf"
                    # Adjust font size dynamically or pick a generally good small size
                    # For small logos (e.g., 32x32 or 35x35), a font size of 8-12 might work
                    # Max 3 chars like "LSU"
                    font_size = 0
                    if logo_size[0] < 20 or len(team_abbr) > 3: # very small logo or long abbr
                        font_size = 6
                    elif len(team_abbr) > 2:
                        font_size = 8
                    else:
                        font_size = 10

                    if not os.path.exists(font_path): # Fallback if PressStart2P is missing
                         font_path = "arial.ttf" # try a common system font
                         font_size = logo_size[1] // 3 # Adjust size for arial

                    font = ImageFont.truetype(font_path, font_size)
                except IOError:
                    logger.warning(f"Font {font_path} not found. Using default font for fallback logo.")
                    font = ImageFont.load_default() # Fallback to default PIL font
                    # For default font, textbbox might not be available or behave differently
                    # We'll estimate text size or accept potentially non-centered text.

                try:
                    # Get text dimensions using textbbox if available
                    text_bbox = draw.textbbox((0, 0), team_abbr, font=font)
                    text_width = text_bbox[2] - text_bbox[0]
                    text_height = text_bbox[3] - text_bbox[1]
                except AttributeError: 
                    # Fallback for older PIL/Pillow or default font if textbbox is not present
                    text_width = len(team_abbr) * (font_size // 1.5 if hasattr(font, 'size') else 6) # Rough estimate
                    text_height = font_size if hasattr(font, 'size') else 8 # Rough estimate


                x = (logo_size[0] - text_width) / 2
                y = (logo_size[1] - text_height) / 2
                
                self._draw_text_with_outline(draw, team_abbr, (x, y), font)
                return image
        except Exception as e:
            logger.error(f"[NCAABaseball] Error loading or generating logo for team {team_abbr}: {e}")
            return None

    def _draw_text_with_outline(self, draw, text, position, font, fill=(255, 255, 255), outline_color=(0, 0, 0)):
        """
        Draw text with a black outline for better readability.
        
        Args:
            draw: ImageDraw object
            text: Text to draw
            position: (x, y) position to draw the text
            font: Font to use
            fill: Text color (default: white)
            outline_color: Outline color (default: black)
        """
        x, y = position
        
        # Draw the outline by drawing the text in black at 8 positions around the text
        for dx, dy in [(-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1)]:
            draw.text((x + dx, y + dy), text, font=font, fill=outline_color)
        
        # Draw the text in the specified color
        draw.text((x, y), text, font=font, fill=fill)

    def _draw_base_indicators(self, draw: ImageDraw.Draw, bases_occupied: List[bool], center_x: int, y: int) -> None:
        """Draw base indicators on the display."""
        base_size = 8
        base_spacing = 10
        diamond_points = [
            (center_x, y), (center_x - base_spacing, y - base_spacing),
            (center_x, y - 2 * base_spacing), (center_x + base_spacing, y - base_spacing)
        ]
        for i in range(len(diamond_points)):
            start = diamond_points[i]
            end = diamond_points[(i + 1) % len(diamond_points)]
            draw.line([start, end], fill=(255, 255, 255), width=2)
        for i, occupied in enumerate(bases_occupied):
            x = diamond_points[i+1][0] - base_size//2
            y = diamond_points[i+1][1] - base_size//2
            if occupied:
                draw.ellipse([x-1, y-1, x + base_size+1, y + base_size+1], fill=(255, 255, 255))
                draw.ellipse([x+1, y+1, x + base_size-1, y + base_size-1], fill=(0, 0, 0))
            else:
                draw.ellipse([x, y, x + base_size, y + base_size], outline=(255, 255, 255), width=1)

    def _create_game_display(self, game_data: Dict[str, Any]) -> Image.Image:
        """Create a display image for an NCAA Baseball game with team logos, score, and game state."""
        width = self.display_manager.matrix.width
        height = self.display_manager.matrix.height
        image = Image.new('RGB', (width, height), color=(0, 0, 0))

        # Make logos 150% of display dimensions to allow them to extend off screen
        max_width = int(width * 1.5)
        max_height = int(height * 1.5)
        
        away_logo = self._get_team_logo(game_data['away_team'])
        home_logo = self._get_team_logo(game_data['home_team'])
        
        if away_logo and home_logo:
            # Resize maintaining aspect ratio
            away_logo.thumbnail((max_width, max_height), Image.Resampling.LANCZOS)
            home_logo.thumbnail((max_width, max_height), Image.Resampling.LANCZOS)

            # Create a single overlay for both logos
            overlay = Image.new('RGBA', (width, height), (0, 0, 0, 0))

            # Calculate vertical center line for alignment
            center_y = height // 2

            # Draw home team logo (far right, extending beyond screen)
            home_x = width - home_logo.width + 2
            home_y = center_y - (home_logo.height // 2)
            
            # Paste the home logo onto the overlay
            overlay.paste(home_logo, (home_x, home_y), home_logo)

            # Draw away team logo (far left, extending beyond screen)
            away_x = -2
            away_y = center_y - (away_logo.height // 2)

            overlay.paste(away_logo, (away_x, away_y), away_logo)

            # Composite the overlay with the main image
            image = image.convert('RGBA')
            image = Image.alpha_composite(image, overlay)
            image = image.convert('RGB')

        draw = ImageDraw.Draw(image)

        # For upcoming games, show date and time stacked in the center
        if game_data['status'] == 'status_scheduled':
            status_text = "Next Game"
            self.display_manager.calendar_font.set_char_size(height=7*64)
            status_width = self.display_manager.get_text_width(status_text, self.display_manager.calendar_font)
            status_x = (width - status_width) // 2
            status_y = 2
            self.display_manager.draw = draw
            script_dir = os.path.dirname(os.path.abspath(__file__))
            font_4x6 = os.path.abspath(os.path.join(script_dir, "../assets/fonts/4x6-font.ttf"))
            status_font = ImageFont.truetype(font_4x6, 6) # Using a default small font
            self._draw_text_with_outline(draw, status_text, (status_x, status_y), status_font)
            
            game_time = datetime.fromisoformat(game_data['start_time'].replace('Z', '+00:00'))
            timezone_str = self.config.get('timezone', 'UTC')
            try:
                tz = pytz.timezone(timezone_str)
            except pytz.exceptions.UnknownTimeZoneError:
                logger.warning(f"[NCAABaseball] Unknown timezone: {timezone_str}, falling back to UTC")
                tz = pytz.UTC
            if game_time.tzinfo is None:
                game_time = game_time.replace(tzinfo=pytz.UTC)
            local_time = game_time.astimezone(tz)
            
            # Check date format from config
            use_short_date_format = self.config.get('display', {}).get('use_short_date_format', False)
            if use_short_date_format:
                game_date = local_time.strftime("%-m/%-d")
            else:
                game_date = self.display_manager.format_date_with_ordinal(local_time)

            game_time_str = self._format_game_time(game_data['start_time'])
            
            ps2p = os.path.abspath(os.path.join(script_dir, "../assets/fonts/PressStart2P-Regular.ttf"))
            date_font = ImageFont.truetype(ps2p, 8)
            time_font = ImageFont.truetype(ps2p, 8)
            
            date_width = draw.textlength(game_date, font=date_font)
            date_x = (width - date_width) // 2
            date_y = (height - date_font.getmetrics()[0]) // 2 - 3 # Adjusted for font metrics
            self._draw_text_with_outline(draw, game_date, (date_x, date_y), date_font)
            
            time_width = draw.textlength(game_time_str, font=time_font)
            time_x = (width - time_width) // 2
            time_y = date_y + 10
            self._draw_text_with_outline(draw, game_time_str, (time_x, time_y), time_font)
        
        # For recent/final games, show scores and status
        elif game_data['status'] in ['status_final', 'final', 'completed']:
            status_text = "Final"
            status_font = ImageFont.truetype(font_4x6, 6) # Using a default small font
            status_width = draw.textlength(status_text, font=status_font)
            status_x = (width - status_width) // 2
            status_y = 2
            self.display_manager.draw = draw
            self._draw_text_with_outline(draw, status_text, (status_x, status_y), status_font)
            
            away_score = str(game_data['away_score'])
            home_score = str(game_data['home_score'])
            score_text = f"{away_score}-{home_score}"
            score_font = ImageFont.truetype(ps2p, 12)
            
            score_width = draw.textlength(score_text, font=score_font)
            score_x = (width - score_width) // 2
            score_y = height - score_font.getmetrics()[0] - 2 # Adjusted for font metrics
            self._draw_text_with_outline(draw, score_text, (score_x, score_y), score_font)

        if self.show_records and game_data['status'] in ['status_scheduled', 'status_final', 'final', 'completed']:
            try:
                record_font = ImageFont.truetype(font_4x6, 6)
            except IOError:
                record_font = ImageFont.load_default()

            away_record = game_data.get('away_record', '')
            home_record = game_data.get('home_record', '')

            record_bbox = draw.textbbox((0, 0), "0-0", font=record_font)
            record_height = record_bbox[3] - record_bbox[1]
            record_y = height - record_height

            if away_record:
                away_record_x = 0
                self._draw_text_with_outline(draw, away_record, (away_record_x, record_y), record_font)

            if home_record:
                home_record_bbox = draw.textbbox((0, 0), home_record, font=record_font)
                home_record_width = home_record_bbox[2] - home_record_bbox[0]
                home_record_x = width - home_record_width
                self._draw_text_with_outline(draw, home_record, (home_record_x, record_y), record_font)

        # Draw betting odds if available and enabled
        if self.show_odds and 'odds' in game_data:
            odds_details = game_data['odds'].get('details', 'N/A')
            home_team_odds = game_data['odds'].get('home_team_odds', {})
            away_team_odds = game_data['odds'].get('away_team_odds', {})

            # Extract spread and format it
            home_spread = home_team_odds.get('point_spread', 'N/A')
            away_spread = away_team_odds.get('point_spread', 'N/A')

            # Add a plus sign to positive spreads
            if isinstance(home_spread, (int, float)) and home_spread > 0:
                home_spread = f"+{home_spread}"
            
            if isinstance(away_spread, (int, float)) and away_spread > 0:
                away_spread = f"+{away_spread}"

            # Define colors for odds text
            # Use a small readable font for odds; fall back to default if not available
            try:
                odds_font = ImageFont.truetype(font_4x6, 6)
            except IOError:
                odds_font = ImageFont.load_default()
            odds_color = (255, 0, 0)  # Red text
            outline_color = (0, 0, 0)   # Black outline

            # Draw away team odds
            if away_spread != 'N/A':
                away_odds_x = 0
                away_odds_y = 0
                self._draw_text_with_outline(draw, str(away_spread), (away_odds_x, away_odds_y), odds_font, odds_color, outline_color)

            # Draw home team odds
            if home_spread != 'N/A':
                home_odds_x = width - draw.textlength(str(home_spread), font=odds_font)
                home_odds_y = 0
                self._draw_text_with_outline(draw, str(home_spread), (home_odds_x, home_odds_y), odds_font, odds_color, outline_color)
        
        return image

    def _format_game_time(self, game_time: str) -> str:
        """Format game time for display."""
        try:
            timezone_str = self.config.get('timezone', 'UTC')
            try:
                tz = pytz.timezone(timezone_str)
            except pytz.exceptions.UnknownTimeZoneError:
                logger.warning(f"[NCAABaseball] Unknown timezone: {timezone_str}, falling back to UTC")
                tz = pytz.UTC
            dt = datetime.fromisoformat(game_time.replace('Z', '+00:00'))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=pytz.UTC)
            local_dt = dt.astimezone(tz)
            return local_dt.strftime("%I:%M%p").lstrip('0')
        except Exception as e:
            logger.error(f"[NCAABaseball] Error formatting game time: {e}")
            return "TBD"

    def _fetch_ncaa_baseball_api_data(self, use_cache: bool = True) -> Dict[str, Any]:
        """Fetch NCAA Baseball game data from the ESPN API."""
        cache_key = "ncaa_baseball_api_data"
        if use_cache:
            cached_data = self.cache_manager.get_with_auto_strategy(cache_key)
            if cached_data:
                self.logger.info("Using cached NCAA Baseball API data.")
                return cached_data

        try:
            # Check if test mode is enabled
            if self.ncaa_baseball_config.get('test_mode', False):
                self.logger.info("Using test mode data for NCAA Baseball")
                return {
                    'test_game_ncaabaseball_1': {
                        'away_team': 'LSU',
                        'home_team': 'FLA',
                        'away_score': 5,
                        'home_score': 4,
                        'status': 'in',
                        'status_state': 'in',
                        'inning': 8,
                        'inning_half': 'top',
                        'balls': 1,
                        'strikes': 2,
                        'outs': 2,
                        'bases_occupied': [True, True, False],
                        'start_time': datetime.now(timezone.utc).isoformat()
                    }
                }
            
            now = datetime.now(timezone.utc)
            yesterday = now - timedelta(days=1)
            tomorrow = now + timedelta(days=1)
            
            dates = [
                yesterday.strftime("%Y%m%d"),
                now.strftime("%Y%m%d"),
                tomorrow.strftime("%Y%m%d")
            ]
            
            all_games = {}
            
            for date in dates:
                # Use NCAA Baseball API URL
                url = f"{ESPN_NCAABB_SCOREBOARD_URL}?dates={date}"
                
                self.logger.info(f"[NCAABaseball] Fetching games from ESPN API for date: {date}")
                response = self.session.get(url, headers=self.headers, timeout=10)
                response.raise_for_status()
                
                data = response.json()
                
                for event in data.get('events', []):
                    game_id = event['id']
                    status = event['status']['type']['name'].lower()
                    status_state = event['status']['type']['state'].lower()
                    
                    competitors = event['competitions'][0]['competitors']
                    home_team = next((c for c in competitors if c['homeAway'] == 'home'), None)
                    away_team = next((c for c in competitors if c['homeAway'] == 'away'), None)

                    if not home_team or not away_team:
                        self.logger.warning(f"[NCAABaseball] Could not find home or away team for event {game_id}")
                        continue

                    home_abbr = home_team['team'].get('abbreviation', 'N/A')
                    away_abbr = away_team['team'].get('abbreviation', 'N/A')
                    home_record = home_team.get('records', [{}])[0].get('summary', '') if home_team.get('records') else ''
                    away_record = away_team.get('records', [{}])[0].get('summary', '') if away_team.get('records') else ''
                    
                    # Don't show "0-0" records - set to blank instead
                    if home_record == "0-0":
                        home_record = ''
                    if away_record == "0-0":
                        away_record = ''
                    
                    is_favorite_game = (home_abbr in self.favorite_teams or away_abbr in self.favorite_teams)
                    
                    if is_favorite_game:
                        self.logger.info(f"[NCAABaseball] Found favorite team game: {away_abbr} @ {home_abbr} (Status: {status}, State: {status_state})")
                        self.logger.debug(f"[NCAABaseball] Full status data: {event['status']}")
                        self.logger.debug(f"[NCAABaseball] Status type: {status}, State: {status_state}")
                        self.logger.debug(f"[NCAABaseball] Status detail: {event['status'].get('detail', '')}")
                        self.logger.debug(f"[NCAABaseball] Status shortDetail: {event['status'].get('shortDetail', '')}")
                    
                    inning = 1
                    inning_half = 'top'
                    balls = 0
                    strikes = 0
                    outs = 0
                    bases_occupied = [False, False, False]
                    
                    if status_state == 'in':
                        inning = event['status'].get('period', 1)
                        status_detail = event['status'].get('detail', '').lower()
                        status_short = event['status'].get('shortDetail', '').lower()
                        
                        if is_favorite_game: self.logger.debug(f"[NCAABaseball] Raw status detail: {event['status'].get('detail')}")
                        if is_favorite_game: self.logger.debug(f"[NCAABaseball] Raw status short: {event['status'].get('shortDetail')}")
                        
                        inning_half = 'top'
                        if 'bottom' in status_detail or 'bot' in status_detail or 'bottom' in status_short or 'bot' in status_short:
                            inning_half = 'bottom'
                            if is_favorite_game: self.logger.debug("[NCAABaseball] Detected bottom of inning")
                        elif 'top' in status_detail or 'mid' in status_detail or 'top' in status_short or 'mid' in status_short:
                            inning_half = 'top'
                            if is_favorite_game: self.logger.debug("[NCAABaseball] Detected top of inning")
                        
                        if is_favorite_game: self.logger.debug(f"[NCAABaseball] Determined inning: {inning_half} {inning}")
                        
                        situation = event['competitions'][0].get('situation', {})
                        if is_favorite_game: self.logger.debug(f"[NCAABaseball] Full situation data: {situation}")
                        
                        # --- Simplified Count Logic --- 
                        # Primarily rely on the direct count fields first
                        count = situation.get('count', {})
                        balls = count.get('balls', 0)
                        strikes = count.get('strikes', 0)
                        outs = situation.get('outs', 0)
                        
                        # Basic logging
                        if is_favorite_game: 
                            self.logger.debug(f"[NCAABaseball] Direct count: B={balls}, S={strikes}, O={outs}")
                        
                        # Keep base occupancy logic
                        bases_occupied = [
                            situation.get('onFirst', False),
                            situation.get('onSecond', False),
                            situation.get('onThird', False)
                        ]
                        if is_favorite_game: self.logger.debug(f"[NCAABaseball] Bases occupied: {bases_occupied}")
                    
                    all_games[game_id] = {
                        'away_team': away_abbr,
                        'home_team': home_abbr,
                        'away_score': away_team.get('score', '0'),
                        'home_score': home_team.get('score', '0'),
                        'away_record': away_record,
                        'home_record': home_record,
                        'status': status,
                        'status_state': status_state,
                        'inning': inning,
                        'inning_half': inning_half,
                        'balls': balls,
                        'strikes': strikes,
                        'outs': outs,
                        'bases_occupied': bases_occupied,
                        'start_time': event['date']
                    }
            
            favorite_games = [game for game in all_games.values() 
                           if game['home_team'] in self.favorite_teams or 
                              game['away_team'] in self.favorite_teams]
            if favorite_games:
                self.logger.info(f"[NCAABaseball] Found {len(favorite_games)} games for favorite teams: {self.favorite_teams}")
                for game in favorite_games:
                    self.logger.info(f"[NCAABaseball] Favorite team game: {game['away_team']} @ {game['home_team']} (Status: {game['status']}, State: {game['status_state']})")
            
            if use_cache:
                self.cache_manager.set(cache_key, all_games)
            return all_games
            
        except Exception as e:
            self.logger.error(f"[NCAABaseball] Error fetching NCAA Baseball data from ESPN API: {e}", exc_info=True)
            return {}

class NCAABaseballLiveManager(BaseNCAABaseballManager, BaseballLive):
    """Manager for displaying live NCAA Baseball games."""
    def __init__(self, config: Dict[str, Any], display_manager, cache_manager: CacheManager):
        super().__init__(config, display_manager, cache_manager)
        self.logger.info("Initialized NCAA Baseball Live Manager")
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

        if self.test_mode:
            self.current_game = {
                "home_team": "FLA",
                "away_team": "LSU",
                "home_score": "4",
                "away_score": "5",
                "status": "live",
                "status_state": "in",
                "inning": 8,
                "inning_half": "top",
                "balls": 1,
                "strikes": 2,
                "outs": 2,
                "bases_occupied": [True, True, False],
                "home_logo_path": os.path.join(self.logo_dir, "FLA.png"),
                "away_logo_path": os.path.join(self.logo_dir, "LSU.png"),
                "start_time": datetime.now(timezone.utc).isoformat(),
            }
            self.live_games = [self.current_game]
            self.logger.info("Initialized NCAABaseballLiveManager with test game: LSU vs FLA")
        else:
            self.logger.info("Initialized NCAABaseballLiveManager in live mode")

    def update(self):
        """Update live game data."""
        current_time = time.time()
        interval = self.no_data_interval if not self.live_games else self.update_interval
        
        if current_time - self.last_update >= interval:
            self.last_update = current_time
            
            if self.test_mode:
                if self.current_game:
                    if self.current_game["inning_half"] == "top": self.current_game["inning_half"] = "bottom"
                    else: self.current_game["inning_half"] = "top"; self.current_game["inning"] += 1
                    self.current_game["balls"] = (self.current_game["balls"] + 1) % 4
                    self.current_game["strikes"] = (self.current_game["strikes"] + 1) % 3
                    self.current_game["outs"] = (self.current_game["outs"] + 1) % 3
                    self.current_game["bases_occupied"] = [not b for b in self.current_game["bases_occupied"]]
                    if self.current_game["inning"] % 2 == 0: self.current_game["home_score"] = str(int(self.current_game["home_score"]) + 1)
                    else: self.current_game["away_score"] = str(int(self.current_game["away_score"]) + 1)
            else:
                games = self._fetch_ncaa_baseball_api_data(use_cache=False)
                if games:
                    new_live_games = []
                    for game in games.values():
                        if game['status_state'] == 'in':
                            # Filter for favorite teams only if the config is set
                            if self.ncaa_baseball_config.get("show_favorite_teams_only", False):
                                if not (game['home_team'] in self.favorite_teams or game['away_team'] in self.favorite_teams):
                                    continue
                            
                            self._fetch_odds(game)
                            try:
                                game['home_score'] = int(game['home_score'])
                                game['away_score'] = int(game['away_score'])
                                new_live_games.append(game)
                            except (ValueError, TypeError):
                                self.logger.warning(f"[NCAABaseball] Invalid score format for game {game['away_team']} @ {game['home_team']}")
                    
                    should_log = (
                        current_time - self.last_log_time >= self.log_interval or
                        len(new_live_games) != len(self.live_games) or
                        not self.live_games
                    )
                    
                    if should_log:
                        if new_live_games:
                            filter_text = "favorite teams" if self.ncaa_baseball_config.get("show_favorite_teams_only", False) else "all teams"
                            logger.info(f"[NCAABaseball] Found {len(new_live_games)} live games involving {filter_text}")
                            for game in new_live_games:
                                logger.info(f"[NCAABaseball] Live game: {game['away_team']} vs {game['home_team']} - {game['inning_half']}{game['inning']}, {game['balls']}-{game['strikes']}")
                        else:
                            filter_text = "favorite teams" if self.ncaa_baseball_config.get("show_favorite_teams_only", False) else "criteria"
                            logger.info(f"[NCAABaseball] No live games found matching {filter_text}")
                        self.last_log_time = current_time
                    
                    if new_live_games:
                        current_game_found_in_new = False
                        if self.current_game:
                            current_id = self.current_game.get('id')
                            for i, new_game in enumerate(new_live_games):
                                if new_game.get('id') == current_id:
                                    self.current_game = new_game
                                    self.current_game_index = i
                                    current_game_found_in_new = True
                                    break
                        
                        if not self.live_games or set(g.get('id') for g in new_live_games) != set(g.get('id') for g in self.live_games):
                             self.live_games = sorted(new_live_games, key=lambda g: g.get('start_time'))
                             if not current_game_found_in_new:
                                 self.current_game_index = 0
                                 self.current_game = self.live_games[0] if self.live_games else None
                                 self.last_game_switch = current_time
                        
                        # Only update display if we have new data and enough time has passed
                        if current_time - self.last_display_update >= 1.0:
                            # self.display(force_clear=True) # REMOVED: DisplayController handles this
                            self.last_display_update = current_time
                    else:
                        self.live_games = []
                        self.current_game = None
            
            if len(self.live_games) > 1 and (current_time - self.last_game_switch) >= self.game_display_duration:
                self.current_game_index = (self.current_game_index + 1) % len(self.live_games)
                self.current_game = self.live_games[self.current_game_index]
                self.last_game_switch = current_time
                
                # Log team switching
                if self.current_game:
                    away_team = self.current_game.get('away_team', 'UNK')
                    home_team = self.current_game.get('home_team', 'UNK')
                    self.logger.info(f"[NCAABASEBALL Live] Showing {away_team} vs {home_team}")
                
                # Force display update when switching games
                # self.display(force_clear=True) # REMOVED: DisplayController handles this
                self.last_display_update = current_time # Track last successful update that *would* have displayed

    def _create_live_game_display(self, game_data: Dict[str, Any]) -> Image.Image:
        """Create a display image for a live NCAA Baseball game."""
        width = self.display_manager.matrix.width
        height = self.display_manager.matrix.height
        image = Image.new('RGB', (width, height), color=(0, 0, 0))

        # Make logos 150% of display dimensions to allow them to extend off screen
        max_width = int(width * 1.5)
        max_height = int(height * 1.5)
        
        away_logo = self._get_team_logo(game_data['away_team'])
        home_logo = self._get_team_logo(game_data['home_team'])
        
        if away_logo and home_logo:
            # Resize maintaining aspect ratio
            away_logo.thumbnail((max_width, max_height), Image.Resampling.LANCZOS)
            home_logo.thumbnail((max_width, max_height), Image.Resampling.LANCZOS)

            # Create a single overlay for both logos
            overlay = Image.new('RGBA', (width, height), (0, 0, 0, 0))

            # Calculate vertical center line for alignment
            center_y = height // 2

            # Draw home team logo (far right, extending beyond screen)
            home_x = width - home_logo.width + 2
            home_y = center_y - (home_logo.height // 2)
            
            # Paste the home logo onto the overlay
            overlay.paste(home_logo, (home_x, home_y), home_logo)

            # Draw away team logo (far left, extending beyond screen)
            away_x = -2
            away_y = center_y - (away_logo.height // 2)

            overlay.paste(away_logo, (away_x, away_y), away_logo)

            # Composite the overlay with the main image
            image = image.convert('RGBA')
            image = Image.alpha_composite(image, overlay)
            image = image.convert('RGB')

        draw = ImageDraw.Draw(image)

        # --- Live Game Specific Elements ---
        
        # Define default text color
        text_color = (255, 255, 255)
        
        inning_half_indicator = "▲" if game_data['inning_half'] == 'top' else "▼"
        inning_text = f"{inning_half_indicator}{game_data['inning']}"
        inning_bbox = draw.textbbox((0, 0), inning_text, font=self.display_manager.font)
        inning_width = inning_bbox[2] - inning_bbox[0]
        inning_x = (width - inning_width) // 2
        inning_y = 1 # Position near top center
        self._draw_text_with_outline(draw, inning_text, (inning_x, inning_y), self.display_manager.font)
        
        # --- REVISED BASES AND OUTS DRAWING --- 
        bases_occupied = game_data['bases_occupied']
        outs = game_data.get('outs', 0)
        inning_half = game_data['inning_half']
        base_diamond_size = 7; out_circle_diameter = 3; out_vertical_spacing = 2
        spacing_between_bases_outs = 3; base_vert_spacing = 1; base_horiz_spacing = 1
        base_cluster_height = base_diamond_size + base_vert_spacing + base_diamond_size
        base_cluster_width = base_diamond_size + base_horiz_spacing + base_diamond_size
        out_cluster_height = 3 * out_circle_diameter + 2 * out_vertical_spacing
        out_cluster_width = out_circle_diameter
        overall_start_y = inning_bbox[3] + 0
        bases_origin_x = (width - base_cluster_width) // 2
        if inning_half == 'top': outs_column_x = bases_origin_x - spacing_between_bases_outs - out_cluster_width
        else: outs_column_x = bases_origin_x + base_cluster_width + spacing_between_bases_outs
        outs_column_start_y = overall_start_y + (base_cluster_height // 2) - (out_cluster_height // 2)
        base_color_occupied = (255, 255, 255); base_color_empty = (255, 255, 255); h_d = base_diamond_size // 2 
        c2x = bases_origin_x + base_cluster_width // 2; c2y = overall_start_y + h_d
        poly2 = [(c2x, overall_start_y), (c2x + h_d, c2y), (c2x, c2y + h_d), (c2x - h_d, c2y)]
        if bases_occupied[1]:
             draw.polygon(poly2, fill=base_color_occupied)
        else:
             draw.polygon(poly2, outline=base_color_empty)
        base_bottom_y = c2y + h_d
        c3x = bases_origin_x + h_d; c3y = base_bottom_y + base_vert_spacing + h_d
        poly3 = [(c3x, base_bottom_y + base_vert_spacing), (c3x + h_d, c3y), (c3x, c3y + h_d), (c3x - h_d, c3y)]
        if bases_occupied[2]:
             draw.polygon(poly3, fill=base_color_occupied)
        else:
             draw.polygon(poly3, outline=base_color_empty)
        c1x = bases_origin_x + base_cluster_width - h_d; c1y = base_bottom_y + base_vert_spacing + h_d
        poly1 = [(c1x, base_bottom_y + base_vert_spacing), (c1x + h_d, c1y), (c1x, c1y + h_d), (c1x - h_d, c1y)]
        if bases_occupied[0]:
             draw.polygon(poly1, fill=base_color_occupied)
        else:
             draw.polygon(poly1, outline=base_color_empty)
        circle_color_out = (255, 255, 255); circle_color_empty_outline = (100, 100, 100) 
        for i in range(3):
            cx = outs_column_x; cy = outs_column_start_y + i * (out_circle_diameter + out_vertical_spacing)
            coords = [cx, cy, cx + out_circle_diameter, cy + out_circle_diameter]
            if i < outs:
                 draw.ellipse(coords, fill=circle_color_out)
            else:
                 draw.ellipse(coords, outline=circle_color_empty_outline)

        balls = game_data.get('balls', 0)
        strikes = game_data.get('strikes', 0)
        current_time = time.time()
        if (game_data['home_team'] in self.favorite_teams or game_data['away_team'] in self.favorite_teams) and current_time - self.last_count_log_time >= self.count_log_interval:
            self.logger.debug(f"[NCAABaseball] Displaying count: {balls}-{strikes}")
            self.logger.debug(f"[NCAABaseball] Raw count data: balls={game_data.get('balls')}, strikes={game_data.get('strikes')}")
            self.last_count_log_time = current_time
        
        count_text = f"{balls}-{strikes}"
        bdf_font = self.display_manager.calendar_font
        bdf_font.set_char_size(height=7*64)
        count_text_width = self.display_manager.get_text_width(count_text, bdf_font)
        cluster_bottom_y = overall_start_y + base_cluster_height
        count_y = cluster_bottom_y + 2
        count_x = bases_origin_x + (base_cluster_width - count_text_width) // 2
        self.display_manager.draw = draw 
        # self._draw_text_with_outline(draw, count_text, (count_x, count_y), bdf_font, fill=text_color)

        # Draw Balls-Strikes Count with outline using BDF font
        outline_color_for_bdf = (0, 0, 0) # Default outline color
        
        # Draw outline
        for dx_offset, dy_offset in [(-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1)]:
            self.display_manager._draw_bdf_text(count_text, count_x + dx_offset, count_y + dy_offset, color=outline_color_for_bdf, font=bdf_font)
        
        # Draw main text
        self.display_manager._draw_bdf_text(count_text, count_x, count_y, color=text_color, font=bdf_font)

        score_font = self.display_manager.font; outline_color = (0, 0, 0); score_text_color = (255, 255, 255)
        def draw_bottom_outlined_text(x, y, text):
            self._draw_text_with_outline(draw, text, (x,y), score_font, fill=score_text_color, outline_color=outline_color)
        away_abbr = game_data['away_team']; home_abbr = game_data['home_team']
        away_score_str = str(game_data['away_score'])
        home_score_str = str(game_data['home_score'])

        away_text = f"{away_abbr}:{away_score_str}"
        home_text = f"{home_abbr}:{home_score_str}"
        
        # Calculate Y position (bottom edge)
        try:
            font_height = score_font.getbbox("A")[3] - score_font.getbbox("A")[1]
        except AttributeError:
            font_height = 8 # Fallback for default font
        score_y = height - font_height - 2 # 2 pixels padding from bottom
        
        # Away Team:Score (Bottom Left)
        away_score_x = 2 # Padding from left
        draw_bottom_outlined_text(away_score_x, score_y, away_text)
        
        # Home Team:Score (Bottom Right)
        home_text_bbox = draw.textbbox((0,0), home_text, font=score_font)
        home_text_width = home_text_bbox[2] - home_text_bbox[0]
        home_score_x = width - home_text_width - 2 # Padding from right
        draw_bottom_outlined_text(home_score_x, score_y, home_text)

        return image

    def display(self, force_clear: bool = False):
        """Display live game information."""
        if not self.current_game:
            return
        try:
            game_image = self._create_live_game_display(self.current_game)
            self.display_manager.image = game_image
            self.display_manager.draw = ImageDraw.Draw(self.display_manager.image)
            self.display_manager.update_display()
        except Exception as e:
            logger.error(f"[NCAABaseball] Error displaying live game: {e}", exc_info=True)

class NCAABaseballRecentManager(BaseNCAABaseballManager, SportsRecent):
    """Manager for displaying recent NCAA Baseball games."""
    def __init__(self, config: Dict[str, Any], display_manager, cache_manager: CacheManager):
        super().__init__(config, display_manager, cache_manager)
        self.logger.info("Initialized NCAA Baseball Recent Manager")
        self.recent_games = []
        self.current_game = None
        self.current_game_index = 0
        self.last_update = 0
        self.update_interval = self.ncaa_baseball_config.get('recent_update_interval', 3600)
        self.recent_games_to_show = self.ncaa_baseball_config.get('recent_games_to_show', 5)  # Number of most recent games to display
        self.last_game_switch = 0
        self.game_display_duration = 10
        self.last_warning_time = 0
        self.warning_cooldown = 300
        logger.info(f"Initialized NCAABaseballRecentManager with {len(self.favorite_teams)} favorite teams")

    def update(self):
        """Update recent games data."""
        current_time = time.time()
        if current_time - self.last_update < self.update_interval:
            return
        self.last_update = current_time
        try:
            games = self._fetch_ncaa_baseball_api_data(use_cache=True)
            if not games:
                logger.warning("[NCAABaseball] No games returned from API")
                self.recent_games = []
                self.current_game = None
                return
            
            new_recent_games = []
            
            for game_id, game in games.items():
                game_time_str = game['start_time'].replace('Z', '+00:00')
                game_time = datetime.fromisoformat(game_time_str)
                if game_time.tzinfo is None: game_time = game_time.replace(tzinfo=timezone.utc)
                
                is_favorite_game = (game['home_team'] in self.favorite_teams or game['away_team'] in self.favorite_teams)
                # Only filter by favorite teams if show_favorite_teams_only is True
                if self.ncaa_baseball_config.get("show_favorite_teams_only", False) and not is_favorite_game:
                    continue
                
                logger.info(f"[NCAABaseball] Checking favorite recent game: {game['away_team']} @ {game['home_team']}")
                logger.info(f"[NCAABaseball] Game time (UTC): {game_time}")
                logger.info(f"[NCAABaseball] Game status: {game['status']}, State: {game['status_state']}")
                
                is_final = game['status_state'] in ['post', 'final', 'completed']
                
                logger.info(f"[NCAABaseball] Is final: {is_final}")
                
                if is_final:
                    self._fetch_odds(game)
                    new_recent_games.append(game)
                    logger.info(f"[NCAABaseball] Added favorite team game to recent list: {game['away_team']} @ {game['home_team']}")
            
            # Filter for favorite teams only if the config is set
            if self.ncaa_baseball_config.get("show_favorite_teams_only", False):
                # Get all games involving favorite teams
                favorite_team_games = [game for game in new_recent_games
                                      if game['home_team'] in self.favorite_teams or
                                         game['away_team'] in self.favorite_teams]
                
                # Select one game per favorite team (most recent game for each team)
                team_games = []
                for team in self.favorite_teams:
                    # Find games where this team is playing
                    team_specific_games = [game for game in favorite_team_games
                                          if game['home_team'] == team or game['away_team'] == team]
                    
                    if team_specific_games:
                        # Sort by game time and take the most recent
                        team_specific_games.sort(key=lambda g: g.get('start_time'), reverse=True)
                        team_games.append(team_specific_games[0])
                
                # Sort the final list by game time (most recent first)
                team_games.sort(key=lambda g: g.get('start_time'), reverse=True)
            else:
                team_games = new_recent_games
                # Sort by game time (most recent first), then limit to recent_games_to_show
                team_games = sorted(team_games, key=lambda g: g.get('start_time'), reverse=True)
                team_games = team_games[:self.recent_games_to_show]

            if team_games:
                logger.info(f"[NCAABaseball] Found {len(team_games)} recent games for favorite teams (limited to {self.recent_games_to_show}): {self.favorite_teams}")
                self.recent_games = team_games
                if not self.current_game or self.current_game.get('id') not in [g.get('id') for g in self.recent_games]:
                    self.current_game_index = 0
                    self.current_game = self.recent_games[0] if self.recent_games else None
            else:
                logger.info("[NCAABaseball] No recent games found for favorite teams")
                self.recent_games = []
                self.current_game = None
            
        except Exception as e:
            logger.error(f"[NCAABaseball] Error updating recent games: {e}", exc_info=True)

    def display(self, force_clear: bool = False):
        """Display recent games."""
        if not self.recent_games:
            current_time = time.time()
            if current_time - self.last_warning_time > self.warning_cooldown:
                logger.info("[NCAABaseball] No recent games to display")
                self.last_warning_time = current_time
            return
        try:
            current_time = time.time()
            if len(self.recent_games) > 1 and current_time - self.last_game_switch >= self.game_display_duration:
                self.current_game_index = (self.current_game_index + 1) % len(self.recent_games)
                self.current_game = self.recent_games[self.current_game_index]
                self.last_game_switch = current_time
                force_clear = True
                
                # Log team switching
                if self.current_game:
                    away_team = self.current_game.get('away_team', 'UNK')
                    home_team = self.current_game.get('home_team', 'UNK')
                    logger.info(f"[NCAABASEBALL Recent] Showing {away_team} vs {home_team}")
            
            if self.current_game:
                game_image = self._create_game_display(self.current_game)
                self.display_manager.image = game_image
                self.display_manager.draw = ImageDraw.Draw(self.display_manager.image)
                self.display_manager.update_display()
            else:
                 logger.warning("[NCAABaseball] Current game is None, cannot display recent game.")

        except Exception as e:
            logger.error(f"[NCAABaseball] Error displaying recent game: {e}", exc_info=True)

class NCAABaseballUpcomingManager(BaseNCAABaseballManager, SportsUpcoming):
    """Manager for displaying upcoming NCAA Baseball games."""
    def __init__(self, config: Dict[str, Any], display_manager, cache_manager: CacheManager):
        super().__init__(config, display_manager, cache_manager)
        self.logger.info("Initialized NCAA Baseball Upcoming Manager")
        self.upcoming_games = []
        self.current_game = None
        self.current_game_index = 0
        self.last_update = 0
        self.update_interval = self.ncaa_baseball_config.get('upcoming_update_interval', 3600)
        self.upcoming_games_to_show = self.ncaa_baseball_config.get('upcoming_games_to_show', 5)  # Number of upcoming games to display
        self.last_warning_time = 0
        self.warning_cooldown = 300
        self.last_game_switch = 0
        self.game_display_duration = 10
        logger.info(f"Initialized NCAABaseballUpcomingManager with {len(self.favorite_teams)} favorite teams")

    def update(self):
        """Update upcoming games data."""
        current_time = time.time()
        if current_time - self.last_update < self.update_interval:
            return
        self.last_update = current_time
        try:
            games = self._fetch_ncaa_baseball_api_data(use_cache=True)
            if games:
                new_upcoming_games = []
                now = datetime.now(timezone.utc)
                
                for game in games.values():
                    is_favorite_game = (game['home_team'] in self.favorite_teams or game['away_team'] in self.favorite_teams)
                    # Only filter by favorite teams if show_favorite_teams_only is True
                    if self.ncaa_baseball_config.get("show_favorite_teams_only", False) and not is_favorite_game:
                        continue
                        
                    game_time = datetime.fromisoformat(game['start_time'].replace('Z', '+00:00'))
                    if game_time.tzinfo is None: game_time = game_time.replace(tzinfo=timezone.utc)
                        
                    logger.info(f"[NCAABaseball] Checking favorite upcoming game: {game['away_team']} @ {game['home_team']} at {game_time}")
                    logger.info(f"[NCAABaseball] Game status: {game['status']}, State: {game['status_state']}")
                    
                    is_upcoming_state = game['status_state'] not in ['post', 'final', 'completed'] and game['status'] == 'status_scheduled'
                    is_future = game_time > now
                    
                    logger.info(f"[NCAABaseball] Is upcoming state: {is_upcoming_state}")
                    logger.info(f"[NCAABaseball] Is future: {is_future}")
                    
                    if is_upcoming_state and is_future:
                        self._fetch_odds(game)
                        new_upcoming_games.append(game)
                        logger.info(f"[NCAABaseball] Added favorite team game to upcoming list: {game['away_team']} @ {game['home_team']}")
                
                # Filter for favorite teams only if the config is set
                if self.ncaa_baseball_config.get("show_favorite_teams_only", False):
                    # Get all games involving favorite teams
                    favorite_team_games = [game for game in new_upcoming_games
                                          if game['home_team'] in self.favorite_teams or
                                             game['away_team'] in self.favorite_teams]
                    
                    # Select one game per favorite team (earliest upcoming game for each team)
                    team_games = []
                    for team in self.favorite_teams:
                        # Find games where this team is playing
                        team_specific_games = [game for game in favorite_team_games
                                              if game['home_team'] == team or game['away_team'] == team]
                        
                        if team_specific_games:
                            # Sort by game time and take the earliest
                            team_specific_games.sort(key=lambda g: g.get('start_time'))
                            team_games.append(team_specific_games[0])
                    
                    # Sort the final list by game time
                    team_games.sort(key=lambda g: g.get('start_time'))
                else:
                    team_games = new_upcoming_games
                    # Sort by game time (soonest first), then limit to configured count
                    team_games = sorted(team_games, key=lambda g: g.get('start_time'))
                    team_games = team_games[:self.upcoming_games_to_show]

                if team_games:
                    logger.info(f"[NCAABaseball] Found {len(team_games)} upcoming games for favorite teams (limited to {self.upcoming_games_to_show})")
                    self.upcoming_games = team_games
                    if not self.current_game or self.current_game.get('id') not in [g.get('id') for g in self.upcoming_games]:
                        self.current_game_index = 0
                        self.current_game = self.upcoming_games[0] if self.upcoming_games else None
                else:
                    logger.info("[NCAABaseball] No upcoming games found for favorite teams")
                    self.upcoming_games = []
                    self.current_game = None
            
        except Exception as e:
            logger.error(f"[NCAABaseball] Error updating upcoming games: {e}", exc_info=True)

    def display(self, force_clear: bool = False):
        """Display upcoming games."""
        if not self.upcoming_games:
            current_time = time.time()
            if current_time - self.last_warning_time > self.warning_cooldown:
                logger.info("[NCAABaseball] No upcoming games to display")
                self.last_warning_time = current_time
            return
        try:
            current_time = time.time()
            if len(self.upcoming_games) > 1 and current_time - self.last_game_switch >= self.game_display_duration:
                self.current_game_index = (self.current_game_index + 1) % len(self.upcoming_games)
                self.current_game = self.upcoming_games[self.current_game_index]
                self.last_game_switch = current_time
                force_clear = True
                
                # Log team switching
                if self.current_game:
                    away_team = self.current_game.get('away_team', 'UNK')
                    home_team = self.current_game.get('home_team', 'UNK')
                    logger.info(f"[NCAABASEBALL Upcoming] Showing {away_team} vs {home_team}")
            
            if self.current_game:
                game_image = self._create_game_display(self.current_game)
                self.display_manager.image = game_image
                self.display_manager.draw = ImageDraw.Draw(self.display_manager.image)
                self.display_manager.update_display()
            else:
                 logger.warning("[NCAABaseball] Current game is None, cannot display upcoming game.")

        except Exception as e:
            logger.error(f"[NCAABaseball] Error displaying upcoming game: {e}", exc_info=True) 