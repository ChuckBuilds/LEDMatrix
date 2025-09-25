import time
import logging
import requests
import json
from typing import Dict, Any, List, Optional
from datetime import datetime, timedelta, timezone
import os
from PIL import Image, ImageDraw, ImageFont
import freetype
import numpy as np
from .cache_manager import CacheManager
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import pytz
from src.background_data_service import get_background_service

# Import baseball and standard sports classes
from .base_classes.baseball import Baseball, BaseballLive
from .base_classes.sports import SportsRecent, SportsUpcoming

# Import API counter function
try:
    from web_interface_v2 import increment_api_counter
except ImportError:
    def increment_api_counter(kind: str, count: int = 1):
        pass

# Get logger
logger = logging.getLogger(__name__)

class BaseMiLBManager(Baseball):
    """Base class for MiLB managers using new baseball architecture."""
    def __init__(self, config: Dict[str, Any], display_manager, cache_manager: CacheManager):
        # Initialize with sport_key for MiLB
        super().__init__(config, display_manager, cache_manager, logger, "milb")
        
        # MiLB-specific configuration
        self.milb_config = config.get('milb', {})
        self.favorite_teams = self.milb_config.get('favorite_teams', [])
        self.show_records = self.milb_config.get('show_records', False)
        
        # Load MiLB team mapping
        self.team_mapping = {}
        self.team_name_to_abbr = {}
        team_mapping_path = os.path.join('assets', 'sports', 'milb_logos', 'milb_team_mapping.json')
        try:
            with open(team_mapping_path, 'r') as f:
                self.team_mapping = json.load(f)
            self.team_name_to_abbr = {name: data['abbreviation'] for name, data in self.team_mapping.items()}
            self.logger.info(f"Loaded {len(self.team_name_to_abbr)} MiLB team mappings.")
        except Exception as e:
            self.logger.error(f"Failed to load MiLB team mapping: {e}")
        
        # Logo handling
        self.logo_dir = self.milb_config.get('logo_dir', os.path.join('assets', 'sports', 'milb_logos'))
        if not os.path.exists(self.logo_dir):
            self.logger.warning(f"MiLB logos directory not found: {self.logo_dir}")
            try:
                os.makedirs(self.logo_dir, exist_ok=True)
                self.logger.info(f"Created MiLB logos directory: {self.logo_dir}")
            except Exception as e:
                self.logger.error(f"Failed to create MiLB logos directory: {e}")
        
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
        
        # Initialize background data service
        background_config = self.milb_config.get("background_service", {})
        if background_config.get("enabled", True):  # Default to enabled
            max_workers = background_config.get("max_workers", 3)
            self.background_service = get_background_service(self.cache_manager, max_workers)
            self.background_fetch_requests = {}  # Track background fetch requests
            self.background_enabled = True
            self.logger.info(f"[MiLB] Background service enabled with {max_workers} workers")
        else:
            self.background_service = None
            self.background_fetch_requests = {}
            self.background_enabled = False
            self.logger.info("[MiLB] Background service disabled")

    def _probe_and_update_from_live_feed(self, game_pk: str, game_data: Dict[str, Any]) -> bool:
        """Probe MLB Stats live feed for a game and update game_data in-place if live.

        Returns True if the feed indicates the game is in progress; False otherwise.
        """
        try:
            live_url = f"http://statsapi.mlb.com/api/v1.1/game/{game_pk}/feed/live"
            self.logger.debug(f"[MiLB] Probing live feed for game {game_pk}: {live_url}")
            resp = self.session.get(live_url, headers=self.headers, timeout=6)
            resp.raise_for_status()
            payload = resp.json()
            game_data_obj = payload.get('gameData', {})
            status_obj = game_data_obj.get('status', {})
            status_code = str(status_obj.get('statusCode', '')).upper()
            abstract_state = str(status_obj.get('abstractGameState', '')).lower()

            is_live = (status_code == 'I') or (abstract_state == 'live')
            # Only treat as live if feed status says in-progress
            if not is_live:
                return False

            # Update primary fields from live feed
            live_data = payload.get('liveData', {})
            linescore = live_data.get('linescore', {})

            # Scores
            away_runs = linescore.get('teams', {}).get('away', {}).get('runs')
            home_runs = linescore.get('teams', {}).get('home', {}).get('runs')
            if away_runs is not None:
                game_data['away_score'] = away_runs
            if home_runs is not None:
                game_data['home_score'] = home_runs

            # Inning and half
            inning = linescore.get('currentInning')
            if inning is not None:
                game_data['inning'] = inning
            inning_state_live = str(linescore.get('inningState', '')).lower()
            if inning_state_live:
                game_data['inning_half'] = 'bottom' if 'bottom' in inning_state_live else 'top'

            # Count and outs
            balls = linescore.get('balls')
            strikes = linescore.get('strikes')
            outs = linescore.get('outs')
            if balls is not None:
                game_data['balls'] = balls
            if strikes is not None:
                game_data['strikes'] = strikes
            if outs is not None:
                game_data['outs'] = outs

            offense = linescore.get('offense', {})
            game_data['bases_occupied'] = [
                'first' in offense,
                'second' in offense,
                'third' in offense
            ]

            # Set status to in-progress and record feed status code
            game_data['status'] = 'status_in_progress'
            game_data['status_state'] = 'in'
            game_data['_status_code'] = status_code
            return True
        except Exception as e:
            self.logger.debug(f"[MiLB] Live feed probe failed for {game_pk}: {e}")
            return False

    def _get_team_logo(self, team_abbr: str) -> Optional[Image.Image]:
        """Get team logo from the configured directory."""
        try:
            logo_path = os.path.join(self.logo_dir, f"{team_abbr}.png")
            if os.path.exists(logo_path):
                logo = Image.open(logo_path)
                if logo.mode != 'RGBA':
                    logo = logo.convert('RGBA')
                return logo
            else:
                logger.warning(f"Logo not found for team {team_abbr}")
                return None
        except Exception as e:
            logger.error(f"Error loading logo for team {team_abbr}: {e}")
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
        base_size = 8  # Increased from 6 to 8 for better visibility
        base_spacing = 10  # Increased from 8 to 10 for better spacing
        
        # Draw diamond outline with thicker lines
        diamond_points = [
            (center_x, y),  # Home
            (center_x - base_spacing, y - base_spacing),  # First
            (center_x, y - 2 * base_spacing),  # Second
            (center_x + base_spacing, y - base_spacing)  # Third
        ]
        
        # Draw thicker diamond outline
        for i in range(len(diamond_points)):
            start = diamond_points[i]
            end = diamond_points[(i + 1) % len(diamond_points)]
            draw.line([start, end], fill=(255, 255, 255), width=2)  # Added width parameter for thicker lines
        
        # Draw occupied bases with larger circles and outline
        for i, occupied in enumerate(bases_occupied):
            x = diamond_points[i+1][0] - base_size//2
            y = diamond_points[i+1][1] - base_size//2
            
            # Draw base circle with outline
            if occupied:
                # Draw white outline
                draw.ellipse([x-1, y-1, x + base_size+1, y + base_size+1], fill=(255, 255, 255))
                # Draw filled circle
                draw.ellipse([x+1, y+1, x + base_size-1, y + base_size-1], fill=(0, 0, 0))
            else:
                # Draw empty base with outline
                draw.ellipse([x, y, x + base_size, y + base_size], outline=(255, 255, 255), width=1)

    def _create_game_display(self, game_data: Dict[str, Any]) -> Image.Image:
        """Create a display image for an MiLB game with team logos, score, and game state."""
        # Throttle this info log to avoid spamming (once every 30s)
        now_ts = time.time()
        if not hasattr(self, '_last_create_log_ts') or (now_ts - getattr(self, '_last_create_log_ts', 0)) >= 30:
            self.logger.info(f"[MiLB] Creating game display for: {game_data.get('away_team')} @ {game_data.get('home_team')}")
            self._last_create_log_ts = now_ts
        width = self.display_manager.matrix.width
        height = self.display_manager.matrix.height
        image = Image.new('RGB', (width, height), color=(0, 0, 0))
        
        # Make logos 130% of display dimensions to allow them to extend off screen
        max_width = int(width * 1.3)
        max_height = int(height * 1.3)
        
        # Load team logos
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
            home_x = width - home_logo.width + 22
            home_y = center_y - (home_logo.height // 2)
            
            # Paste the home logo onto the overlay
            overlay.paste(home_logo, (home_x, home_y), home_logo)

            # Draw away team logo (far left, extending beyond screen)
            away_x = -22
            away_y = center_y - (away_logo.height // 2)

            overlay.paste(away_logo, (away_x, away_y), away_logo)
            
            # Composite the overlay with the main image
            image = image.convert('RGBA')
            image = Image.alpha_composite(image, overlay)
            image = image.convert('RGB')
        
        draw = ImageDraw.Draw(image)
        
        # For upcoming games, show date and time stacked in the center
        self.logger.debug(f"[MiLB] Game status: {game_data.get('status')}, status_state: {game_data.get('status_state')}")
        self.logger.debug(f"[MiLB] Full game data: {game_data}")
        is_upcoming_status = (
            game_data.get('status') == 'status_scheduled' or
            game_data.get('status_state') not in ['post', 'final', 'completed']
        )
        if is_upcoming_status:
            # Ensure game_time_str is defined before use
            game_time_str = game_data.get('start_time', '')
            # Show "Next Game" at the top using BDF font when available, else TTF fallback
            status_text = "Next Game"
            try:
                if isinstance(self.display_manager.calendar_font, freetype.Face):
                    # Likely a freetype.Face (BDF). Size to ~7px
                    self.display_manager.calendar_font.set_char_size(height=7*64)
                    status_width = self.display_manager.get_text_width(status_text, self.display_manager.calendar_font)
                    status_x = (width - status_width) // 2
                    status_y = 2
                    # Draw on the current image
                    self.display_manager.draw = draw
                    self.display_manager._draw_bdf_text(status_text, status_x, status_y, color=(255, 255, 255), font=self.display_manager.calendar_font)
                else:
                    # Fallback to small TTF font
                    fallback_font = self.display_manager.small_font
                    status_width = self.display_manager.get_text_width(status_text, fallback_font)
                    status_x = (width - status_width) // 2
                    status_y = 2
                    self._draw_text_with_outline(draw, status_text, (status_x, status_y), fallback_font)
            except Exception as e:
                # As a last resort, draw with default PIL font
                status_x = 2
                status_y = 2
                self._draw_text_with_outline(draw, status_text, (status_x, status_y), ImageFont.load_default())
            
            if not game_time_str or 'TBD' in game_time_str:
                game_date_str = "TBD"
                game_time_formatted_str = ""
                self.logger.debug(f"[MiLB] Game time is TBD or empty: {game_time_str}")
            else:
                self.logger.debug(f"[MiLB] Processing game time: {game_time_str}")
                try:
                    game_time = datetime.fromisoformat(game_time_str.replace('Z', '+00:00'))
                    timezone_str = self.config.get('timezone', 'UTC')
                    try:
                        tz = pytz.timezone(timezone_str)
                    except pytz.exceptions.UnknownTimeZoneError:
                        logger.warning(f"Unknown timezone: {timezone_str}, falling back to UTC")
                        tz = pytz.UTC
                    if game_time.tzinfo is None:
                        game_time = game_time.replace(tzinfo=pytz.UTC)
                    local_time = game_time.astimezone(tz)
                    
                    self.logger.debug(f"[MiLB] Local time: {local_time}")
                    
                    # Check date format from config
                    use_short_date_format = self.config.get('display', {}).get('use_short_date_format', False)
                    if use_short_date_format:
                        game_date_str = local_time.strftime("%-m/%-d")
                    else:
                        game_date_str = self.display_manager.format_date_with_ordinal(local_time)

                    game_time_formatted_str = self._format_game_time(game_data['start_time'])
                    
                    self.logger.debug(f"[MiLB] Formatted date: {game_date_str}, time: {game_time_formatted_str}")
                except Exception as e:
                    self.logger.error(f"[MiLB] Error processing game time: {e}")
                    game_date_str = "TBD"
                    game_time_formatted_str = "TBD"
            
            # Draw date and time using NHL-style fonts
            try:
                # Prefer already loaded small font from DisplayManager to avoid I/O and failures
                date_font = getattr(self.display_manager, 'small_font', None)
                time_font = getattr(self.display_manager, 'small_font', None)
                if date_font is None or time_font is None:
                    try:
                        date_font = ImageFont.truetype("assets/fonts/PressStart2P-Regular.ttf", 8)
                        time_font = ImageFont.truetype("assets/fonts/PressStart2P-Regular.ttf", 8)
                    except Exception as e:
                        self.logger.warning(f"[MiLB] Could not load PressStart2P fonts: {e}, using default")
                        date_font = ImageFont.load_default()
                        time_font = ImageFont.load_default()
                self.logger.debug(f"[MiLB] Fonts prepared successfully")
            except Exception as e:
                self.logger.error(f"[MiLB] Failed to prepare fonts: {e}")
                # Fallback to default font
                date_font = ImageFont.load_default()
                time_font = ImageFont.load_default()
            
            # Draw date in center (use DisplayManager helpers for compatibility)
            try:
                date_width = self.display_manager.get_text_width(game_date_str, date_font)
            except Exception:
                # Fallback: approximate width by character count if helper fails
                date_width = len(game_date_str) * 6
            date_height = self.display_manager.get_font_height(date_font)
            date_x = (width - date_width) // 2
            date_y = (height - date_height) // 2 - 3
            self.logger.debug(f"[MiLB] Drawing date '{game_date_str}' at ({date_x}, {date_y}), size {date_width}x{date_height}")
            self._draw_text_with_outline(draw, game_date_str, (date_x, date_y), date_font)


            # Draw time below date
            try:
                time_width = self.display_manager.get_text_width(game_time_formatted_str, time_font)
            except Exception:
                time_width = len(game_time_formatted_str) * 6
            time_height = self.display_manager.get_font_height(time_font)
            time_x = (width - time_width) // 2
            time_y = date_y + date_height + 2
            self.logger.debug(f"[MiLB] Drawing time '{game_time_formatted_str}' at ({time_x}, {time_y}), size {time_width}x{time_height}")
            self._draw_text_with_outline(draw, game_time_formatted_str, (time_x, time_y), time_font)

            # Removed debug rectangles around text
        
        # For recent/final games, show scores and status
        elif game_data['status'] in ['status_final', 'final', 'completed']:
            # Show "Final" at the top using BDF when available, else TTF fallback
            status_text = "Final"
            font_for_status = self.display_manager.calendar_font
            try:
                if isinstance(font_for_status, freetype.Face):
                    try:
                        font_for_status.set_char_size(height=7*64)  # 7 pixels high, 64 units per pixel
                    except Exception:
                        pass
                    status_width = self.display_manager.get_text_width(status_text, font_for_status)
                    status_x = (width - status_width) // 2
                    status_y = 2
                    # Draw on the current image
                    self.display_manager.draw = draw
                    self.display_manager._draw_bdf_text(status_text, status_x, status_y, color=(255, 255, 255), font=font_for_status)
                else:
                    # Fallback to small TTF font
                    fallback_font = getattr(self.display_manager, 'small_font', ImageFont.load_default())
                    status_width = self.display_manager.get_text_width(status_text, fallback_font)
                    status_x = (width - status_width) // 2
                    status_y = 2
                    self._draw_text_with_outline(draw, status_text, (status_x, status_y), fallback_font)
            except Exception:
                # Last resort
                self._draw_text_with_outline(draw, status_text, (2, 2), ImageFont.load_default())
            
            # Draw scores at the bottom using NHL-style font
            away_score = str(game_data['away_score'])
            home_score = str(game_data['home_score'])
            score_text = f"{away_score}-{home_score}"
            try:
                score_font = ImageFont.truetype("assets/fonts/PressStart2P-Regular.ttf", 12)
            except Exception as e:
                self.logger.warning(f"[MiLB] Could not load PressStart2P font: {e}, using default")
                score_font = ImageFont.load_default()
            
            # Calculate position for the score text
            score_width = draw.textlength(score_text, font=score_font)
            score_x = (width - score_width) // 2
            score_y = height - score_font.size - 2
            # draw.text((score_x, score_y), score_text, font=score_font, fill=(255, 255, 255))
            self._draw_text_with_outline(draw, score_text, (score_x, score_y), score_font)

        # Draw records for upcoming and recent games
        if self.show_records and game_data['status'] in ['status_scheduled', 'status_final', 'final', 'completed']:
            try:
                record_font = ImageFont.truetype("assets/fonts/4x6-font.ttf", 6)
            except Exception as e:
                self.logger.warning(f"[MiLB] Could not load 4x6 font: {e}, using default")
                record_font = ImageFont.load_default()
            
            away_record = game_data.get('away_record', '')
            home_record = game_data.get('home_record', '')
            
            # Using textbbox is more accurate for height than .size
            record_bbox = draw.textbbox((0,0), "0-0", font=record_font)
            record_height = record_bbox[3] - record_bbox[1]
            record_y = height - record_height

            if away_record:
                away_record_x = 0
                self._draw_text_with_outline(draw, away_record, (away_record_x, record_y), record_font)

            if home_record:
                home_record_bbox = draw.textbbox((0,0), home_record, font=record_font)
                home_record_width = home_record_bbox[2] - home_record_bbox[0]
                home_record_x = width - home_record_width
                self._draw_text_with_outline(draw, home_record, (home_record_x, record_y), record_font)
        
        return image

    def _format_game_time(self, game_time: str) -> str:
        """Format game time for display."""
        try:
            self.logger.debug(f"[MiLB] Formatting game time: {game_time}")
            # Get timezone from config
            timezone_str = self.config.get('timezone', 'UTC')
            try:
                tz = pytz.timezone(timezone_str)
            except pytz.exceptions.UnknownTimeZoneError:
                logger.warning(f"Unknown timezone: {timezone_str}, falling back to UTC")
                tz = pytz.UTC
            
            # Convert game time to local timezone
            dt = datetime.fromisoformat(game_time.replace('Z', '+00:00'))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=pytz.UTC)
            local_dt = dt.astimezone(tz)
            
            formatted_time = local_dt.strftime("%I:%M%p").lstrip('0')
            self.logger.debug(f"[MiLB] Formatted time: {formatted_time}")
            return formatted_time
        except Exception as e:
            logger.error(f"Error formatting game time: {e}")
            return "TBD"

    def _fetch_milb_api_data(self, use_cache: bool = True) -> Dict[str, Any]:
        """Fetch MiLB game data from the MLB Stats API."""
        self.logger.info("[MiLB] _fetch_milb_api_data called")
        cache_key = "milb_live_api_data"
        if use_cache:
            cached_data = self.cache_manager.get_with_auto_strategy(cache_key)
            if cached_data:
                self.logger.info("Using cached MiLB API data.")
                return cached_data

        try:
            # Check if test mode is enabled
            test_mode = self.milb_config.get('test_mode', False)
            self.logger.debug(f"[MiLB] Test mode: {test_mode}")
            if test_mode:
                self.logger.info("Using test mode data for MiLB")
                return {
                    'test_game_1': {
                        'away_team': 'TOL',
                        'home_team': 'BUF',
                        'away_score': 3,
                        'home_score': 2,
                        'status': 'status_in_progress',
                        'status_state': 'in',
                        'inning': 7,
                        'inning_half': 'bottom',
                        'balls': 2,
                        'strikes': 1,
                        'outs': 1,
                        'bases_occupied': [True, False, True],  # Runner on 1st and 3rd
                        'start_time': datetime.now(timezone.utc).isoformat()
                    }
                }

            # Check if we're in MiLB season (April-September)
            now = datetime.now()
            current_month = now.month
            in_season = 4 <= current_month <= 9
            
            self.logger.debug(f"[MiLB] Current month: {current_month}, in_season: {in_season}")
            
            if not in_season:
                self.logger.info("MiLB is currently in offseason (October-March). No games expected.")
                self.logger.info("Consider enabling test_mode for offseason testing.")
                return {}

            # MiLB league sport IDs (configurable)
            sport_ids = self.milb_config.get('sport_ids', [10, 11, 12, 13, 14, 15]) # Mexican, AAA, AA, A+, A, Rookie

            now = datetime.now(timezone.utc)
            # Extend date range to look further into the future for upcoming games
            # Look back 1 day and forward 7 days to catch more upcoming games
            dates = [(now + timedelta(days=d)).strftime("%Y-%m-%d") for d in range(-1, 8)]

            all_games = {}

            for date in dates:
                for sport_id in sport_ids:
                    url = f"http://statsapi.mlb.com/api/v1/schedule?sportId={sport_id}&date={date}"
                    try:
                        self.logger.debug(f"Fetching MiLB games from MLB Stats API: {url}")
                        response = self.session.get(url, headers=self.headers, timeout=10)
                        response.raise_for_status()
                        data = response.json()
                        # Increment API counter for successful request
                        increment_api_counter('sports', 1)
                    except requests.exceptions.RequestException as e:
                        self.logger.error(f"Error fetching data from {url}: {e}")
                        continue

                    if not data.get('dates') or not data['dates'][0].get('games'):
                        continue

                    for event in data['dates'][0]['games']:
                        game_pk = event['gamePk']
                        # Debug: Check game_pk type
                        if not isinstance(game_pk, (int, str)):
                            self.logger.warning(f"[MiLB] Unexpected game_pk type: {type(game_pk)}, value: {game_pk}")
                            # Convert to string to ensure it's usable as a key
                            game_pk = str(game_pk)
                        
                        home_team_name = event['teams']['home']['team']['name']
                        away_team_name = event['teams']['away']['team']['name']
                        
                        home_abbr = self.team_name_to_abbr.get(home_team_name)
                        away_abbr = self.team_name_to_abbr.get(away_team_name)

                        if not home_abbr:
                            home_abbr = event['teams']['home']['team'].get('abbreviation', home_team_name[:3].upper())
                            self.logger.debug(f"Could not find team abbreviation for '{home_team_name}'. Using '{home_abbr}'.")
                        if not away_abbr:
                            away_abbr = event['teams']['away']['team'].get('abbreviation', away_team_name[:3].upper())
                            self.logger.debug(f"Could not find team abbreviation for '{away_team_name}'. Using '{away_abbr}'.")

                        # Get team records
                        away_record_data = event['teams']['away'].get('record', {})
                        home_record_data = event['teams']['home'].get('record', {})
                        away_record = away_record_data.get('wins')
                        away_losses = away_record_data.get('losses')
                        home_record = home_record_data.get('wins')
                        home_losses = home_record_data.get('losses')
                        if away_record is not None and away_losses is not None and (away_record != 0 or away_losses != 0):
                            away_record_str = f"{away_record}-{away_losses}"
                        else:
                            away_record_str = ''
                        if home_record is not None and home_losses is not None and (home_record != 0 or home_losses != 0):
                            home_record_str = f"{home_record}-{home_losses}"
                        else:
                            home_record_str = ''

                        if not event.get('gameDate'):
                            self.logger.warning(f"Skipping game {game_pk} due to missing 'gameDate'.")
                            continue
                        
                        self.logger.debug(f"[MiLB] Game {game_pk} gameDate: {event.get('gameDate')}")

                        is_favorite_game = (home_abbr in self.favorite_teams or away_abbr in self.favorite_teams)
                        
                        if not self.favorite_teams or is_favorite_game:
                            status_obj = event['status']
                            status_state = status_obj.get('abstractGameState', 'Preview') # Changed default to 'Preview'
                            detailed_state = status_obj.get('detailedState', '')

                            # Map status to a consistent format using abstractGameState
                            mapped_status = 'status_other'
                            mapped_status_state = 'pre'
                            
                            if status_state == 'Live':
                                mapped_status = 'status_in_progress'
                                mapped_status_state = 'in'
                            elif status_state == 'Final':
                                mapped_status = 'status_final'
                                mapped_status_state = 'post'
                            elif status_state in ['Preview', 'Scheduled']:
                                mapped_status = 'status_scheduled'
                                mapped_status_state = 'pre'

                            game_data = {
                                'id': game_pk,
                                'away_team': away_abbr,
                                'home_team': home_abbr,
                                'away_score': event['teams']['away'].get('score', 0),
                                'home_score': event['teams']['home'].get('score', 0),
                                'status': mapped_status,
                                'status_state': mapped_status_state,
                                'detailed_state': detailed_state,
                                'start_time': event.get('gameDate'),
                                'away_record': away_record_str,
                                'home_record': home_record_str
                            }
                            
                            self.logger.debug(f"[MiLB] Created game data for {game_pk}: status={mapped_status}, status_state={mapped_status_state}, start_time={event.get('gameDate')}")

                            if status_state == 'Live':
                                linescore = event.get('linescore', {})
                                game_data['inning'] = linescore.get('currentInning', 1)
                                inning_state = linescore.get('inningState', 'Top').lower()
                                game_data['inning_half'] = inning_state
                                game_data['balls'] = linescore.get('balls', 0)
                                game_data['strikes'] = linescore.get('strikes', 0)
                                game_data['outs'] = linescore.get('outs', 0)
                                game_data['bases_occupied'] = [
                                    'first' in linescore.get('offense', {}),
                                    'second' in linescore.get('offense', {}),
                                    'third' in linescore.get('offense', {})
                                ]
                            else:
                                # For non-live games, set defaults
                                game_data.update({'inning': 1, 'inning_half': 'top', 'balls': 0, 'strikes': 0, 'outs': 0, 'bases_occupied': [False]*3})
                            
                            # Validate game_data before adding to all_games
                            if isinstance(game_data, dict):
                                all_games[game_pk] = game_data
                            else:
                                self.logger.error(f"[MiLB] Invalid game_data type for game {game_pk}: {type(game_data)}")

            # Filter out any invalid games before returning
            if isinstance(all_games, dict):
                valid_games = {}
                for game_id, game_data in all_games.items():
                    if isinstance(game_data, dict):
                        valid_games[game_id] = game_data
                    else:
                        self.logger.warning(f"[MiLB] Skipping invalid game {game_id} with type {type(game_data)}")
                all_games = valid_games
            
            if use_cache:
                # Validate that all_games is a dictionary before caching
                if isinstance(all_games, dict):
                    # Validate that all values in the dictionary are also dictionaries
                    invalid_games = []
                    for game_id, game_data in all_games.items():
                        if not isinstance(game_data, dict):
                            invalid_games.append((game_id, type(game_data)))
                    
                    if invalid_games:
                        self.logger.error(f"[MiLB] Found invalid game data types: {invalid_games}")
                        # Don't cache corrupted data
                    else:
                        self.cache_manager.set(cache_key, all_games)
                else:
                    self.logger.error(f"[MiLB] Cannot cache invalid data type: {type(all_games)}")
            self.logger.info(f"[MiLB] Returning {len(all_games)} games from API")
            return all_games
            
        except Exception as e:
            self.logger.error(f"Error fetching MiLB data from MLB Stats API: {e}", exc_info=True)
            return {}

    def _extract_game_details(self, game) -> Dict:
        # Validate basic game structure
        if not isinstance(game, dict):
            self.logger.error(f"[MiLB] Game is not a dictionary. Type: {type(game)}, Value: {game}")
            return {}
        
        game_pk = game.get('id')
        
        # Validate home team structure
        if 'home' not in game or not isinstance(game['home'], dict):
            self.logger.error(f"[MiLB] Invalid home team structure: {game.get('home', 'missing')}")
            return {}
        
        # Validate away team structure
        if 'away' not in game or not isinstance(game['away'], dict):
            self.logger.error(f"[MiLB] Invalid away team structure: {game.get('away', 'missing')}")
            return {}
        
        # Validate team name structure
        if 'team' not in game['home'] or 'name' not in game['home']['team']:
            self.logger.error(f"[MiLB] Invalid home team name structure: {game['home']}")
            return {}
        
        if 'team' not in game['away'] or 'name' not in game['away']['team']:
            self.logger.error(f"[MiLB] Invalid away team name structure: {game['away']}")
            return {}
        
        home_team_name = game['home']['team']['name']
        away_team_name = game['away']['team']['name']
        
        home_abbr = self.team_name_to_abbr.get(home_team_name)
        away_abbr = self.team_name_to_abbr.get(away_team_name)

        if not home_abbr:
            home_abbr = game['home']['team'].get('abbreviation', home_team_name[:3].upper())
            self.logger.debug(f"Could not find team abbreviation for '{home_team_name}'. Using '{home_abbr}'.")
        if not away_abbr:
            away_abbr = game['away']['team'].get('abbreviation', away_team_name[:3].upper())
            self.logger.debug(f"Could not find team abbreviation for '{away_team_name}'. Using '{away_abbr}'.")

        # Get team records
        away_record = game['away'].get('record', {}).get('wins', 0)
        away_losses = game['away'].get('record', {}).get('losses', 0)
        home_record = game['home'].get('record', {}).get('wins', 0)
        home_losses = game['home'].get('record', {}).get('losses', 0)
        away_record_str = f"{away_record}-{away_losses}"
        home_record_str = f"{home_record}-{home_losses}"

        is_favorite_game = (home_abbr in self.favorite_teams or away_abbr in self.favorite_teams)
        
        if not self.favorite_teams or is_favorite_game:
            # Validate status object
            if 'status' not in game or not isinstance(game['status'], dict):
                self.logger.error(f"[MiLB] Invalid status structure for {away_abbr} @ {home_abbr}: {game.get('status', 'missing')}")
                return {}
            
            status_obj = game['status']
            status_state = status_obj.get('abstractGameState', 'Final')
            
            # Debug: Log the original status information
            self.logger.debug(f"[MiLB] Status mapping for {away_abbr} @ {home_abbr}: original abstractGameState='{status_state}', full status_obj={status_obj}, game_date={game.get('date', 'N/A')}")

            mapped_status = 'unknown'
            mapped_status_state = 'unknown'
            if status_state == 'Live':
                mapped_status = 'status_in_progress'
                mapped_status_state = 'in'
            elif status_state == 'Final':
                mapped_status = 'status_final'
                mapped_status_state = 'post'
            elif status_state in ['Preview', 'Scheduled']:
                mapped_status = 'status_scheduled'
                mapped_status_state = 'pre'

            # Extract scores with fallback logic
            away_score = game['away'].get('score')
            home_score = game['home'].get('score')
            
            # Debug logging for score extraction
            self.logger.debug(f"Initial scores for {away_abbr} @ {home_abbr}: away={away_score}, home={home_score}")
            
            # If scores are None or missing, try to get from linescore
            if away_score is None or home_score is None:
                linescore = game.get('linescore', {})
                if linescore:
                    teams_in_linescore = linescore.get('teams', {})
                    if away_score is None:
                        away_score = teams_in_linescore.get('away', {}).get('runs', 0)
                        self.logger.debug(f"Got away score from linescore: {away_score}")
                    if home_score is None:
                        home_score = teams_in_linescore.get('home', {}).get('runs', 0)
                        self.logger.debug(f"Got home score from linescore: {home_score}")
            
            # Final fallback to 0 if still None
            away_score = away_score if away_score is not None else 0
            home_score = home_score if home_score is not None else 0
            
            self.logger.debug(f"Final scores for {away_abbr} @ {home_abbr}: away={away_score}, home={home_score}")
            
            # Validate and extract date
            game_date = game.get('date')
            if not game_date:
                self.logger.warning(f"[MiLB] Skipping game data due to missing or empty 'date' field for {away_abbr} @ {home_abbr}")
                return {}
            
            # Handle case where date might be a timestamp instead of ISO string
            if isinstance(game_date, (int, float)):
                try:
                    # Convert timestamp to ISO string
                    from datetime import datetime
                    game_date = datetime.fromtimestamp(game_date).isoformat()
                    self.logger.debug(f"[MiLB] Converted timestamp {game_date} to ISO format for {away_abbr} @ {home_abbr}")
                except Exception as e:
                    self.logger.error(f"[MiLB] Could not convert timestamp {game_date} to date for {away_abbr} @ {home_abbr}: {e}")
                    return {}
            
            game_data = {
                'away_team': away_abbr,
                'home_team': home_abbr,
                'away_score': away_score,
                'home_score': home_score,
                'status': mapped_status,
                'status_state': mapped_status_state,
                'start_time': game_date,
                'away_record': f"{game['away'].get('record', {}).get('wins', 0)}-{game['away'].get('record', {}).get('losses', 0)}",
                'home_record': f"{game['home'].get('record', {}).get('wins', 0)}-{game['home'].get('record', {}).get('losses', 0)}"
            }

            if status_state == 'Live':
                linescore = game.get('linescore', {})
                game_data['inning'] = linescore.get('currentInning', 1)
                inning_state = linescore.get('inningState', 'Top').lower()
                game_data['inning_half'] = 'bottom' if 'bottom' in inning_state else 'top'
                
                # Fetch live data for ALL live games, not just favorite teams
                try:
                    live_url = f"http://statsapi.mlb.com/api/v1.1/game/{game_pk}/feed/live"
                    self.logger.info(f"[MiLB] Fetching live data for {away_abbr} @ {home_abbr} from: {live_url}")
                    live_response = self.session.get(live_url, headers=self.headers, timeout=5)
                    live_response.raise_for_status()
                    live_data = live_response.json().get('liveData', {})
                    
                    linescore_live = live_data.get('linescore', {})
                    self.logger.info(f"[MiLB] Live data response for {away_abbr} @ {home_abbr}: {linescore_live}")
                    
                    # Log the full live data structure for debugging
                    self.logger.debug(f"[MiLB] Full live data structure for {away_abbr} @ {home_abbr}: {json.dumps(live_data, indent=2)}")

                    # Overwrite score and inning data with more accurate live data from the live feed
                    if linescore_live:
                        # Extract scores from live feed with fallback
                        away_runs = linescore_live.get('teams', {}).get('away', {}).get('runs')
                        home_runs = linescore_live.get('teams', {}).get('home', {}).get('runs')
                        
                        # Only update if we got valid scores from live feed
                        if away_runs is not None:
                            game_data['away_score'] = away_runs
                        if home_runs is not None:
                            game_data['home_score'] = home_runs
                        
                        # Update inning info
                        current_inning = linescore_live.get('currentInning')
                        if current_inning is not None:
                            game_data['inning'] = current_inning
                        
                        inning_state_live = linescore_live.get('inningState', '').lower()
                        if inning_state_live:
                            game_data['inning_half'] = 'bottom' if 'bottom' in inning_state_live else 'top'

                    # Always try to get balls, strikes, and outs from live feed
                    balls = linescore_live.get('balls')
                    strikes = linescore_live.get('strikes')
                    outs = linescore_live.get('outs')
                    
                    self.logger.debug(f"[MiLB] Live count data for {away_abbr} @ {home_abbr}: balls={balls}, strikes={strikes}, outs={outs}")
                    
                    game_data['balls'] = balls if balls is not None else 0
                    game_data['strikes'] = strikes if strikes is not None else 0
                    game_data['outs'] = outs if outs is not None else 0
                    
                    offense = linescore_live.get('offense', {})
                    game_data['bases_occupied'] = [
                        'first' in offense,
                        'second' in offense,
                        'third' in offense
                    ]
                    
                    self.logger.info(f"[MiLB] Final live data for {away_abbr} @ {home_abbr}: inning={game_data['inning']}, half={game_data['inning_half']}, count={game_data['balls']}-{game_data['strikes']}, outs={game_data['outs']}, scores={game_data['away_score']}-{game_data['home_score']}")
                    
                except Exception as e:
                    self.logger.warning(f"Could not fetch live details for game {game_pk} ({away_abbr} @ {home_abbr}): {e}")
                    game_data.update({'balls': 0, 'strikes': 0, 'outs': 0, 'bases_occupied': [False]*3})
            else:
                game_data.update({'inning': 1, 'inning_half': 'top', 'balls': 0, 'strikes': 0, 'outs': 0, 'bases_occupied': [False]*3})
            
            return game_data
        return {}

class MiLBLiveManager(BaseMiLBManager, BaseballLive):
    """Manager for displaying live MiLB games."""
    def __init__(self, config: Dict[str, Any], display_manager, cache_manager: CacheManager):
        super().__init__(config, display_manager, cache_manager)
        self.logger.info("Initialized MiLB Live Manager")
        self.live_games = []
        self.current_game = None  # Initialize current_game to None
        self.current_game_index = 0
        self.last_update = 0
        self.update_interval = self.milb_config.get('live_update_interval', 20)
        # Poll at least every 300s when no live games to detect new live starts sooner
        self.no_data_interval = max(300, self.update_interval)
        self.last_game_switch = 0  # Track when we last switched games
        self.game_display_duration = self.milb_config.get('live_game_duration', 30)  # Display each live game for 30 seconds
        self.last_display_update = 0  # Track when we last updated the display
        self.last_log_time = 0
        self.log_interval = 300  # Only log status every 5 minutes
        self.last_count_log_time = 0  # Track when we last logged count data
        self.count_log_interval = 5  # Only log count data every 5 seconds
        self.test_mode = self.milb_config.get('test_mode', False)

        # Initialize with test game only if test mode is enabled
        if self.test_mode:
            self.current_game = {
                "home_team": "TOL",
                "away_team": "BUF",
                "home_score": "3",
                "away_score": "2",
                "status": "live",
                "status_state": "live",
                "inning": 5,
                "inning_half": "top",
                "balls": 2,
                "strikes": 1,
                "outs": 1,
                "bases_occupied": [True, False, True],
                "home_logo_path": os.path.join(self.logo_dir, "TOL.png"),
                "away_logo_path": os.path.join(self.logo_dir, "BUF.png"),
                "start_time": datetime.now(timezone.utc).isoformat(),
            }
            self.live_games = [self.current_game]
            self.logger.info("Initialized MiLBLiveManager with test game: TOL vs BUF")
        else:
            self.logger.info("Initialized MiLBLiveManager in live mode")

    def update(self):
        """Update live game data."""
        current_time = time.time()
        # Use longer interval if no game data
        interval = self.no_data_interval if not self.live_games else self.update_interval

        if current_time - self.last_update < interval:
            time_remaining = interval - (current_time - self.last_update)
            self.logger.debug(f"[MiLB] Update interval not reached yet ({time_remaining:.1f}s remaining)")
            return

        self.last_update = current_time
        self.logger.info(f"[MiLB] Update interval reached ({interval}s), fetching data")

        if self.test_mode:
            # For testing, we'll just update the game state to show it's working
            if self.current_game:
                # Update inning half
                if self.current_game["inning_half"] == "top":
                    self.current_game["inning_half"] = "bottom"
                else:
                    self.current_game["inning_half"] = "top"
                    self.current_game["inning"] += 1
                # Update count
                self.current_game["balls"] = (self.current_game["balls"] + 1) % 4
                self.current_game["strikes"] = (self.current_game["strikes"] + 1) % 3
                # Update outs
                self.current_game["outs"] = (self.current_game["outs"] + 1) % 3
                # Update bases
                self.current_game["bases_occupied"] = [
                    not self.current_game["bases_occupied"][0],
                    not self.current_game["bases_occupied"][1],
                    not self.current_game["bases_occupied"][2]
                ]
                # Update score occasionally
                if self.current_game["inning"] % 2 == 0:
                    self.current_game["home_score"] = str(int(self.current_game["home_score"]) + 1)
                else:
                    self.current_game["away_score"] = str(int(self.current_game["away_score"]) + 1)
            return

        # Fetch live game data from MiLB API
        games = self._fetch_milb_api_data(use_cache=False)
        if not games:
            self.logger.debug("[MiLB] No games returned from API")
        else:
            self.logger.info(f"[MiLB] Fetched {len(games)} games from API")

        if games:
            # Debug: Log all games found
            self.logger.debug(f"[MiLB] Found {len(games)} total games from API")
            for game_id, game in games.items():
                game_date_str = game.get('start_time', 'N/A')
                self.logger.debug(f"[MiLB] Game {game_id}: {game['away_team']} @ {game['home_team']} - Status: {game['status']}, State: {game['status_state']}, Date: {game_date_str}")

            # Find all live games (optionally filtering to favorites)
            new_live_games_map: Dict[str, Dict[str, Any]] = {}
            for game in games.values():
                self.logger.debug(f"[MiLB] Game status check: {game['away_team']} @ {game['home_team']} - status_state='{game['status_state']}', status='{game['status']}', detailed_state='{game.get('detailed_state','')}'")
                is_live_by_flags = (game['status_state'] == 'in' and game['status'] == 'status_in_progress')
                detailed = str(game.get('detailed_state','')).lower()
                is_live_by_detail_hint = any(
                    token in detailed for token in [
                        'in progress', 'game in progress', 'top of the', 'bottom of the', 'middle of the', 'end of the'
                    ]
                )

                # Determine liveness: prefer explicit flags; otherwise require a successful feed probe
                is_live = is_live_by_flags
                feed_confirmed = False

                # Compute timing window relative to now
                game_pk = game.get('id') or game.get('game_pk')
                start_time_str = game.get('start_time')
                start_dt = None
                now_utc = datetime.now(timezone.utc)
                if start_time_str:
                    try:
                        start_dt = datetime.fromisoformat(start_time_str.replace('Z', '+00:00'))
                        if start_dt.tzinfo is None:
                            start_dt = start_dt.replace(tzinfo=timezone.utc)
                    except Exception:
                        start_dt = None

                # Decide whether to probe live feed
                should_probe = False
                # Probe if detail hints suggest activity
                if is_live_by_detail_hint:
                    should_probe = True
                # Probe if schedule flags say live BUT the game starts >5 minutes in the future
                if is_live_by_flags and start_dt is not None:
                    future_seconds = (start_dt - now_utc).total_seconds()
                    if future_seconds > 5 * 60:
                        should_probe = True
                # Always probe favorite team games in a sensible time window (captures unreliable flags)
                is_favorite_game = (game['home_team'] in self.favorite_teams or game['away_team'] in self.favorite_teams)
                if is_favorite_game:
                    if start_dt is None:
                        # If start time missing, still probe favorites
                        should_probe = True
                    else:
                        delta_sec = (now_utc - start_dt).total_seconds()
                        # Within -12h..+12h window relative to start to be robust to TZ/skew
                        if -12 * 3600 <= delta_sec <= 12 * 3600:
                            should_probe = True

                # Also bound probe window to +/- 12 hours from now (failsafe)
                if should_probe and start_dt is not None:
                    if abs((now_utc - start_dt).total_seconds()) > 12 * 3600:
                        should_probe = False

                # Perform probe if needed
                if should_probe and game_pk:
                    if self._probe_and_update_from_live_feed(str(game_pk), game):
                        is_live = True
                        feed_confirmed = True
                        self.logger.info(f"[MiLB] Live confirmed via feed: {game['away_team']} @ {game['home_team']}")

                # If schedule flags claim live but start is >5 minutes in the future AND feed did not confirm, reject
                if is_live and not feed_confirmed and start_dt is not None:
                    future_seconds = (start_dt - now_utc).total_seconds()
                    if future_seconds > 5 * 60:
                        self.logger.info(f"[MiLB] Rejecting schedule-live future game without feed confirmation: {game['away_team']} @ {game['home_team']} (starts in {future_seconds/60:.1f}m)")
                        is_live = False

                # Decision trace for debugging
                try:
                    delta_sec = None
                    if start_dt is not None:
                        delta_sec = (now_utc - start_dt).total_seconds()
                    self.logger.debug(
                        f"[MiLB] Live decision: {game['away_team']}@{game['home_team']} flags={is_live_by_flags} "
                        f"detail_hint={is_live_by_detail_hint} feed={feed_confirmed} probe={should_probe} "
                        f"start_delta_s={delta_sec if delta_sec is not None else 'NA'} result={is_live}"
                    )
                except Exception:
                    pass

                if is_live:
                    # Sanity check on time
                    game_date_str = game.get('start_time', '')
                    if game_date_str:
                        try:
                            game_date = datetime.fromisoformat(game_date_str.replace('Z', '+00:00'))
                            current_utc = datetime.now(timezone.utc)
                            hours_diff = (current_utc - game_date).total_seconds() / 3600
                            # If a game is flagged live, do NOT exclude for future start; only guard against stale past
                            if hours_diff > 48:
                                self.logger.warning(f"[MiLB] Skipping old game marked live: {game['away_team']} @ {game['home_team']}")
                                continue
                            # Note: We intentionally allow future games if API reports live/detailed live
                        except Exception as e:
                            self.logger.warning(f"[MiLB] Could not parse game date {game_date_str}: {e}")

                    # Favorites-only filter if enabled
                    favorites_only = self.milb_config.get('show_favorite_teams_only', False)
                    if favorites_only and self.favorite_teams:
                        is_favorite = (
                            game['home_team'] in self.favorite_teams or
                            game['away_team'] in self.favorite_teams
                        )
                        if not is_favorite:
                            self.logger.debug(f"[MiLB] Skipping non-favorite game in favorites-only mode: {game['away_team']} @ {game['home_team']}")
                            continue

                    self.logger.info(f"[MiLB] Processing live game: {game['away_team']} @ {game['home_team']} - Inning: {game.get('inning', 'N/A')}, Half: {game.get('inning_half', 'N/A')}, Count: {game.get('balls', 0)}-{game.get('strikes', 0)}, Outs: {game.get('outs', 0)}, Scores: {game.get('away_score', 0)}-{game.get('home_score', 0)}")
                    try:
                        game['home_score'] = int(game['home_score'])
                        game['away_score'] = int(game['away_score'])
                        # Deduplicate by game id; prefer feed-confirmed version
                        unique_id = str(game.get('id') or f"{game['away_team']}@{game['home_team']}")
                        if unique_id in new_live_games_map:
                            prev = new_live_games_map[unique_id]
                            prev_confirmed = prev.get('_feed_confirmed', False)
                            if feed_confirmed and not prev_confirmed:
                                game['_feed_confirmed'] = True
                                new_live_games_map[unique_id] = game
                        else:
                            if feed_confirmed:
                                game['_feed_confirmed'] = True
                            new_live_games_map[unique_id] = game
                    except (ValueError, TypeError):
                        self.logger.warning(f"Invalid score format for game {game['away_team']} @ {game['home_team']}")

            new_live_games = list(new_live_games_map.values())
            should_log = (
                current_time - self.last_log_time >= self.log_interval or
                len(new_live_games) != len(self.live_games) or
                not self.live_games
            )
            if should_log:
                if new_live_games:
                    logger.info(f"[MiLB] Found {len(new_live_games)} live games")
                    for game in new_live_games:
                        logger.info(f"[MiLB] Live game: {game['away_team']} vs {game['home_team']} - {game.get('inning_half','?')}{game.get('inning','?')}, {game.get('balls',0)}-{game.get('strikes',0)}, outs={game.get('outs',0)}, scores={game.get('away_score',0)}-{game.get('home_score',0)}")
                else:
                    logger.info("[MiLB] No live games found")
                self.last_log_time = current_time

            logger.debug(f"[MiLB] Live games state: {len(new_live_games)} new; previous: {len(self.live_games)}")

            if new_live_games:
                for new_game in new_live_games:
                    if self.current_game and (
                        (new_game['home_team'] == self.current_game['home_team'] and new_game['away_team'] == self.current_game['away_team']) or
                        (new_game['home_team'] == self.current_game['away_team'] and new_game['away_team'] == self.current_game['home_team'])
                    ):
                        self.current_game = new_game
                        break
                if not self.live_games or set(g['away_team'] + g['home_team'] for g in new_live_games) != set(g['away_team'] + g['home_team'] for g in self.live_games):
                    self.live_games = new_live_games
                    if not self.current_game or self.current_game not in self.live_games:
                        self.current_game_index = 0
                        self.current_game = self.live_games[0]
                        self.last_game_switch = current_time
                if current_time - self.last_display_update >= 1.0:
                    self.last_display_update = current_time
            else:
                self.live_games = []
                self.current_game = None

        # Rotate if multiple live games
        if len(self.live_games) > 1 and (current_time - self.last_game_switch) >= self.game_display_duration:
            self.current_game_index = (self.current_game_index + 1) % len(self.live_games)
            self.current_game = self.live_games[self.current_game_index]
            self.last_game_switch = current_time
            self.last_display_update = current_time

    def _create_live_game_display(self, game_data: Dict[str, Any]) -> Image.Image:
        """Create a display image for a live MiLB game."""
        width = self.display_manager.matrix.width
        height = self.display_manager.matrix.height
        image = Image.new('RGB', (width, height), color=(0, 0, 0))

        # Make logos 130% of display dimensions to allow them to extend off screen
        max_width = int(width * 1.3)
        max_height = int(height * 1.3)
        
        # Load and place team logos
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
            home_x = width - home_logo.width + 18
            home_y = center_y - (home_logo.height // 2)
            
            # Paste the home logo onto the overlay
            overlay.paste(home_logo, (home_x, home_y), home_logo)

            # Draw away team logo (far left, extending beyond screen)
            away_x = -18
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
        
        # Draw Inning (Top Center)
        inning_half_indicator = "▲" if game_data['inning_half'] == 'top' else "▼"
        inning_text = f"{inning_half_indicator}{game_data['inning']}"
        inning_bbox = draw.textbbox((0, 0), inning_text, font=self.display_manager.font)
        inning_width = inning_bbox[2] - inning_bbox[0]
        inning_x = (width - inning_width) // 2
        inning_y = 1 # Position near top center
        # draw.text((inning_x, inning_y), inning_text, fill=(255, 255, 255), font=self.display_manager.font)
        self._draw_text_with_outline(draw, inning_text, (inning_x, inning_y), self.display_manager.font)
        
        # --- REVISED BASES AND OUTS DRAWING --- 
        bases_occupied = game_data['bases_occupied'] # [1st, 2nd, 3rd]
        outs = game_data.get('outs', 0)
        inning_half = game_data['inning_half']
        
        # Define geometry
        base_diamond_size = 7
        out_circle_diameter = 3
        out_vertical_spacing = 2 # Space between out circles
        spacing_between_bases_outs = 3 # Horizontal space between base cluster and out column
        base_vert_spacing = 1 # Internal vertical space in base cluster
        base_horiz_spacing = 1 # Internal horizontal space in base cluster
        
        # Calculate cluster dimensions
        base_cluster_height = base_diamond_size + base_vert_spacing + base_diamond_size
        base_cluster_width = base_diamond_size + base_horiz_spacing + base_diamond_size
        out_cluster_height = 3 * out_circle_diameter + 2 * out_vertical_spacing
        out_cluster_width = out_circle_diameter
        
        # Calculate overall start positions
        overall_start_y = inning_bbox[3] + 0 # Start immediately below inning text (moved up 3 pixels)
        
        # Center the BASE cluster horizontally
        bases_origin_x = (width - base_cluster_width) // 2
        
        # Determine relative positions for outs based on inning half
        if inning_half == 'top': # Away batting, outs on left
            outs_column_x = bases_origin_x - spacing_between_bases_outs - out_cluster_width
        else: # Home batting, outs on right
            outs_column_x = bases_origin_x + base_cluster_width + spacing_between_bases_outs
        
        # Calculate vertical alignment offset for outs column (center align with bases cluster)
        outs_column_start_y = overall_start_y + (base_cluster_height // 2) - (out_cluster_height // 2)

        # --- Draw Bases (Diamonds) ---
        base_color_occupied = (255, 255, 255)
        base_color_empty = (255, 255, 255) # Outline color
        h_d = base_diamond_size // 2 
        
        # 2nd Base (Top center relative to bases_origin_x)
        c2x = bases_origin_x + base_cluster_width // 2 
        c2y = overall_start_y + h_d
        poly2 = [(c2x, overall_start_y), (c2x + h_d, c2y), (c2x, c2y + h_d), (c2x - h_d, c2y)]
        if bases_occupied[1]: draw.polygon(poly2, fill=base_color_occupied)
        else: draw.polygon(poly2, outline=base_color_empty)
        
        base_bottom_y = c2y + h_d # Bottom Y of 2nd base diamond
        
        # 3rd Base (Bottom left relative to bases_origin_x)
        c3x = bases_origin_x + h_d 
        c3y = base_bottom_y + base_vert_spacing + h_d
        poly3 = [(c3x, base_bottom_y + base_vert_spacing), (c3x + h_d, c3y), (c3x, c3y + h_d), (c3x - h_d, c3y)]
        if bases_occupied[2]: draw.polygon(poly3, fill=base_color_occupied)
        else: draw.polygon(poly3, outline=base_color_empty)

        # 1st Base (Bottom right relative to bases_origin_x)
        c1x = bases_origin_x + base_cluster_width - h_d
        c1y = base_bottom_y + base_vert_spacing + h_d
        poly1 = [(c1x, base_bottom_y + base_vert_spacing), (c1x + h_d, c1y), (c1x, c1y + h_d), (c1x - h_d, c1y)]
        if bases_occupied[0]: draw.polygon(poly1, fill=base_color_occupied)
        else: draw.polygon(poly1, outline=base_color_empty)
        
        # --- Draw Outs (Vertical Circles) ---
        circle_color_out = (255, 255, 255) 
        circle_color_empty_outline = (100, 100, 100) 

        for i in range(3):
            cx = outs_column_x
            cy = outs_column_start_y + i * (out_circle_diameter + out_vertical_spacing)
            coords = [cx, cy, cx + out_circle_diameter, cy + out_circle_diameter]
            if i < outs:
                draw.ellipse(coords, fill=circle_color_out)
            else:
                draw.ellipse(coords, outline=circle_color_empty_outline)

        # --- Draw Balls-Strikes Count (BDF Font) --- 
        balls = game_data.get('balls', 0)
        strikes = game_data.get('strikes', 0)
        
        # Add debug logging for count with cooldown
        current_time = time.time()
        if (game_data['home_team'] in self.favorite_teams or game_data['away_team'] in self.favorite_teams) and \
           current_time - self.last_count_log_time >= self.count_log_interval:
            self.logger.debug(f"[MiLB] Displaying count: {balls}-{strikes}")
            self.logger.debug(f"[MiLB] Raw count data: balls={game_data.get('balls')}, strikes={game_data.get('strikes')}")
            self.last_count_log_time = current_time
        
        count_text = f"{balls}-{strikes}"
        bdf_font = self.display_manager.calendar_font
        # Determine font type and compute width
        if isinstance(bdf_font, freetype.Face):
            try:
                bdf_font.set_char_size(height=7*64)  # Set 7px height
            except Exception:
                pass
            count_text_width = self.display_manager.get_text_width(count_text, bdf_font)
            using_bdf = True
        else:
            fallback_font = getattr(self.display_manager, 'small_font', ImageFont.load_default())
            count_text_width = self.display_manager.get_text_width(count_text, fallback_font)
            using_bdf = False
        
        # Position below the base/out cluster
        cluster_bottom_y = overall_start_y + base_cluster_height # Find the bottom of the taller part (bases)
        count_y = cluster_bottom_y + 2 # Start 2 pixels below cluster
        
        # Center horizontally within the BASE cluster width
        count_x = bases_origin_x + (base_cluster_width - count_text_width) // 2
        
        # Draw the count either with BDF or TTF path
        if using_bdf:
            # Ensure draw object is set for BDF draw
            self.display_manager.draw = draw 
            # Outline color consistent with _draw_text_with_outline default
            outline_color_for_bdf = (0, 0, 0)
            # Draw outline
            for dx_offset, dy_offset in [(-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1)]:
                self.display_manager._draw_bdf_text(count_text, count_x + dx_offset, count_y + dy_offset, color=outline_color_for_bdf, font=bdf_font)
            # Draw main text
            self.display_manager._draw_bdf_text(count_text, count_x, count_y, color=text_color, font=bdf_font)
        else:
            # Use TTF fallback with outline helper
            self._draw_text_with_outline(draw, count_text, (count_x, count_y), fallback_font)

        # Draw Team:Score at the bottom
        score_font = self.display_manager.font # Use PressStart2P
        outline_color = (0, 0, 0)
        score_text_color = (255, 255, 255) # Use a specific name for score text color

        # Helper function for outlined text
        def draw_bottom_outlined_text(x, y, text):
            # Draw outline
            # draw.text((x-1, y), text, font=score_font, fill=outline_color)
            # draw.text((x+1, y), text, font=score_font, fill=outline_color)
            # draw.text((x, y-1), text, font=score_font, fill=outline_color)
            # draw.text((x, y+1), text, font=score_font, fill=outline_color)
            # # Draw main text
            # draw.text((x, y), text, font=score_font, fill=score_text_color)
            self._draw_text_with_outline(draw, text, (x,y), score_font, fill=score_text_color, outline_color=outline_color)

        away_abbr = game_data['away_team']
        home_abbr = game_data['home_team']
        away_score_str = str(game_data['away_score'])
        home_score_str = str(game_data['home_score'])

        away_text = f"{away_abbr}:{away_score_str}"
        home_text = f"{home_abbr}:{home_score_str}"
        
        # Calculate Y position (bottom edge)
        # Get font height (approximate or precise)
        try:
            font_height = score_font.getbbox("A")[3] - score_font.getbbox("A")[1]
        except AttributeError:
            font_height = 8 # Fallback for default font
        score_y = height - font_height - 1 # 1 pixel padding from bottom
        
        # Away Team:Score (Bottom Left)
        away_score_x = 2 # 2 pixels padding from left
        # draw.text((away_score_x, score_y), away_text, font=score_font, fill=text_color)
        draw_bottom_outlined_text(away_score_x, score_y, away_text)
        
        # Home Team:Score (Bottom Right)
        home_text_bbox = draw.textbbox((0,0), home_text, font=score_font)
        home_text_width = home_text_bbox[2] - home_text_bbox[0]
        home_score_x = width - home_text_width - 2 # 2 pixels padding from right
        # draw.text((home_score_x, score_y), home_text, font=score_font, fill=text_color)
        draw_bottom_outlined_text(home_score_x, score_y, home_text)

        # TODO: Add Outs display if needed

        return image

    def display(self, force_clear: bool = False):
        """Display live game information."""
        if not self.current_game:
            logger.debug("[MiLB] Display called but no current game, returning early")
            return
            
        try:
            # Only log display calls occasionally to reduce spam
            current_time = time.time()
            if not hasattr(self, '_last_display_log_time') or current_time - getattr(self, '_last_display_log_time', 0) >= 30:
                logger.info(f"[MiLB] Displaying live game: {self.current_game.get('away_team')} @ {self.current_game.get('home_team')}")
                logger.info(f"[MiLB] Game data for display: inning={self.current_game.get('inning')}, half={self.current_game.get('inning_half')}, count={self.current_game.get('balls')}-{self.current_game.get('strikes')}, outs={self.current_game.get('outs')}, scores={self.current_game.get('away_score')}-{self.current_game.get('home_score')}")
                self._last_display_log_time = current_time
            # Create and display the game image using the new method
            game_image = self._create_live_game_display(self.current_game)
            # Set the image in the display manager
            self.display_manager.image = game_image
            self.display_manager.draw = ImageDraw.Draw(self.display_manager.image)
            # Update the display
            self.display_manager.update_display()
        except Exception as e:
            logger.error(f"[MiLB] Error displaying live game: {e}", exc_info=True)

class MiLBRecentManager(BaseMiLBManager, SportsRecent):
    """Manager for displaying recent MiLB games."""
    def __init__(self, config: Dict[str, Any], display_manager, cache_manager: CacheManager):
        super().__init__(config, display_manager, cache_manager)
        self.logger.info("Initialized MiLB Recent Manager")
        self.recent_games = []
        self.current_game = None
        self.current_game_index = 0
        self.last_update = 0
        self.update_interval = self.milb_config.get('recent_update_interval', 3600)  # 1 hour
        self.recent_games_to_show = self.milb_config.get('recent_games_to_show', 5)  # Show last 5 games
        self.last_game_switch = 0  # Track when we last switched games
        self.game_display_duration = 10  # Display each game for 10 seconds
        self.last_warning_time = 0
        self.warning_cooldown = 300  # Only show warning every 5 minutes
        logger.info(f"Initialized MiLBRecentManager with {len(self.favorite_teams)} favorite teams")
        self.last_log_time = 0
        self.log_interval = 300 # 5 minutes

    def update(self):
        """Update recent games data."""
        current_time = time.time()
        if current_time - self.last_update >= self.update_interval:
            self.last_update = current_time
        else:
            return
            
        try:
            # Fetch data from MiLB API
            games = self._fetch_milb_api_data(use_cache=True)
            if not games:
                logger.warning("[MiLB] No games returned from API")
                return
                
            logger.info(f"[MiLB] Fetched {len(games)} total games from API")
                
            # Process games
            new_recent_games = []
            
            logger.info(f"[MiLB] Processing {len(games)} games for recent games...")
            
            # Log all games found for debugging
            all_games_log = []
            favorite_games_log = []
            
            for game_id, game in games.items():
                # Convert game time to UTC datetime, now safely checking for key existence
                start_time_str = game.get('start_time')
                if not start_time_str:
                    self.logger.warning(f"Skipping game {game_id} due to missing 'start_time'.")
                    continue
                
                try:
                    game_time = datetime.fromisoformat(start_time_str.replace('Z', '+00:00'))
                except ValueError:
                    self.logger.warning(f"Could not parse start_time '{start_time_str}' for game {game_id}. Skipping.")
                    continue
                    
                if game_time.tzinfo is None:
                    game_time = game_time.replace(tzinfo=timezone.utc)
                
                # Check if this is a favorite team game
                is_favorite_game = (game.get('home_team') in self.favorite_teams or 
                                  game.get('away_team') in self.favorite_teams)
                
                # Log all games for debugging
                game_info = f"{game.get('away_team')} @ {game.get('home_team')} (Status: {game.get('status')}, State: {game.get('status_state')})"
                all_games_log.append(game_info)
                
                if is_favorite_game:
                    favorite_games_log.append(game_info)
                    self.logger.info(f"[MiLB] Found favorite team game: {game.get('away_team')} @ {game.get('home_team')}")
                    self.logger.info(f"[MiLB] Game time (UTC): {game_time}")
                    self.logger.info(f"[MiLB] Game status: {game.get('status')}, State: {game.get('status_state')}")
                    self.logger.info(f"[MiLB] Scores: {game.get('away_team')} {game.get('away_score', 0)} - {game.get('home_team')} {game.get('home_score', 0)}")
                
                # Use status_state to determine if game is final
                is_final = game.get('status_state') in ['post', 'final', 'completed']
                
                self.logger.info(f"[MiLB] Game Time: {game_time.isoformat()}")
                self.logger.info(f"[MiLB] Is final: {is_final}")
                
                # Only add favorite team games that are final
                if is_final:
                    self.logger.info(f"[MiLB] Adding game {game_id} to recent games list.")
                    new_recent_games.append(game)
                else:
                    self.logger.info(f"[MiLB] Skipping game {game_id} - not final.")
            
            # Log summary of all games found
            logger.info(f"[MiLB] All games found ({len(all_games_log)}): {all_games_log}")
            logger.info(f"[MiLB] Favorite team games found ({len(favorite_games_log)}): {favorite_games_log}")
            
            # Sort by game time (most recent first) and apply per-team logic
            new_recent_games.sort(key=lambda x: x.get('start_time', ''), reverse=True)
            
            # If showing favorite teams only, select one game per team
            if self.milb_config.get("show_favorite_teams_only", False):
                # Select one game per favorite team (most recent game for each team)
                team_games = []
                for team in self.favorite_teams:
                    # Find games where this team is playing
                    team_specific_games = [game for game in new_recent_games
                                          if game.get('home_team') == team or game.get('away_team') == team]
                    
                    if team_specific_games:
                        # Take the most recent (first in sorted list)
                        team_games.append(team_specific_games[0])
                
                # Sort the final list by game time (most recent first)
                team_games.sort(key=lambda x: x.get('start_time', ''), reverse=True)
                new_recent_games = team_games
            else:
                # Limit to configured number if not using favorite teams only
                new_recent_games = new_recent_games[:self.recent_games_to_show]
            
            if new_recent_games:
                logger.info(f"[MiLB] Found {len(new_recent_games)} recent final games for favorite teams: {self.favorite_teams}")
                self.recent_games = new_recent_games
                if not self.current_game:
                    self.current_game = self.recent_games[0]
            else:
                # Fallback: if no final games found, show any recent games for favorite teams
                logger.info("[MiLB] No final games found for favorite teams, checking for any recent games...")
                fallback_games = []
                for game_id, game in games.items():
                    if (game.get('home_team') in self.favorite_teams or game.get('away_team') in self.favorite_teams):
                        start_time_str = game.get('start_time')
                        if not start_time_str:
                            continue
                        
                        try:
                            game_time = datetime.fromisoformat(start_time_str.replace('Z', '+00:00'))
                        except ValueError:
                            continue
                            
                        if game_time.tzinfo is None:
                            game_time = game_time.replace(tzinfo=timezone.utc)
                        
                        # Include any game from the last 7 days
                        if game_time >= datetime.now(timezone.utc) - timedelta(days=7):
                            fallback_games.append(game)
                            logger.info(f"[MiLB] Added fallback game: {game.get('away_team')} @ {game.get('home_team')} (Status: {game.get('status_state')})")
                
                fallback_games.sort(key=lambda x: x.get('start_time', ''), reverse=True)
                fallback_games = fallback_games[:self.recent_games_to_show]
                
                if fallback_games:
                    logger.info(f"[MiLB] Found {len(fallback_games)} fallback games for favorite teams")
                    self.recent_games = fallback_games
                    if not self.current_game:
                        self.current_game = self.recent_games[0]
                else:
                    logger.info("[MiLB] No recent games found for favorite teams (including fallback)")
                    self.recent_games = []
                    self.current_game = None
            
            self.last_update = current_time
            
        except Exception as e:
            logger.error(f"[MiLB] Error updating recent games: {e}", exc_info=True)

    def display(self, force_clear: bool = False):
        """Display recent games."""
        if not self.recent_games:
            current_time = time.time()
            if current_time - self.last_warning_time > self.warning_cooldown:
                logger.info("[MiLB] No recent games to display")
                self.last_warning_time = current_time
            return  # Skip display update entirely
            
        try:
            current_time = time.time()
            
            # Check if it's time to switch games
            if len(self.recent_games) > 1 and current_time - self.last_game_switch >= self.game_display_duration:
                # Move to next game
                self.current_game_index = (self.current_game_index + 1) % len(self.recent_games)
                self.current_game = self.recent_games[self.current_game_index]
                self.last_game_switch = current_time
                force_clear = True  # Force clear when switching games
            
            # Create and display the game image
            game_image = self._create_game_display(self.current_game)
            self.display_manager.image = game_image
            self.display_manager.draw = ImageDraw.Draw(self.display_manager.image)
            self.display_manager.update_display()
            
        except Exception as e:
            logger.error(f"[MiLB] Error displaying recent game: {e}", exc_info=True)

class MiLBUpcomingManager(BaseMiLBManager, SportsUpcoming):
    """Manager for upcoming MiLB games."""
    def __init__(self, config: Dict[str, Any], display_manager, cache_manager: CacheManager):
        super().__init__(config, display_manager, cache_manager)
        self.logger = logging.getLogger(__name__)
        self.upcoming_games = []
        self.current_game = None
        self.current_game_index = 0
        self.last_update = 0
        self.update_interval = self.milb_config.get('upcoming_update_interval', 3600) # 1 hour
        self.upcoming_games_to_show = self.milb_config.get('upcoming_games_to_show', 10)  # Show next 10 games
        self.last_warning_time = 0
        self.warning_cooldown = 300  # Only show warning every 5 minutes
        self.last_game_switch = 0  # Track when we last switched games
        self.game_display_duration = 10  # Display each game for 10 seconds
        self.logger.info(f"Initialized MiLBUpcomingManager with {len(self.favorite_teams)} favorite teams")

    def update(self):
        """Update upcoming games data."""
        current_time = time.time()
        
        # Add a check to see if the manager is enabled
        if not self.milb_config.get('enabled', True):
            # If the manager is disabled, clear any existing games and return
            if self.upcoming_games:
                self.upcoming_games = []
                self.current_game = None
                self.logger.info("[MiLB Upcoming] Manager is disabled, clearing games.")
            return

        self.logger.debug(f"[MiLB] show_favorite_teams_only: {self.milb_config.get('show_favorite_teams_only', False)}")
        self.logger.debug(f"[MiLB] favorite_teams: {self.favorite_teams}")
        
        if self.last_update != 0 and (current_time - self.last_update < self.update_interval):
            return
            
        # Only log when we're actually performing an update
        self.logger.info("[MiLB] Update method called - performing update")
            
        try:
            # Fetch data from MiLB API
            games = self._fetch_milb_api_data(use_cache=True)
            
            # Debug: Check the structure of returned data
            if games is not None:
                self.logger.debug(f"[MiLB] Games data type: {type(games)}")
                if isinstance(games, dict):
                    self.logger.debug(f"[MiLB] Number of games: {len(games)}")
                    if games:
                        sample_key = next(iter(games))
                        sample_value = games[sample_key]
                        self.logger.debug(f"[MiLB] Sample game key: {sample_key}, type: {type(sample_value)}, value: {sample_value}")
                        
                        # Check if the data structure is corrupted
                        if not isinstance(sample_value, dict):
                            self.logger.error(f"[MiLB] Cache data appears corrupted. Clearing cache and refetching.")
                            self.cache_manager.clear_cache("milb_live_api_data")
                            games = self._fetch_milb_api_data(use_cache=False)
                else:
                    self.logger.error(f"[MiLB] Games is not a dictionary: {type(games)}, value: {games}")
                    # Clear cache and try again without cache
                    self.cache_manager.clear_cache("milb_live_api_data")
                    games = self._fetch_milb_api_data(use_cache=False)
            
            if not games:
                self.logger.warning("[MiLB] No games returned from API for upcoming games update.")
                if self.upcoming_games: # Clear games if API returns nothing
                    self.upcoming_games = []
                    self.current_game = None
                return
            
            # Final validation that games is a dictionary
            if not isinstance(games, dict):
                self.logger.error(f"[MiLB] Final validation failed - games is not a dictionary: {type(games)}")
                if self.upcoming_games: # Clear games if data is invalid
                    self.upcoming_games = []
                    self.current_game = None
                return

            # --- Optimization: Filter for favorite teams before processing ---
            show_favorite_only = self.milb_config.get("show_favorite_teams_only", False)
            self.logger.debug(f"[MiLB] show_favorite_teams_only: {show_favorite_only}, favorite_teams: {self.favorite_teams}")
            if show_favorite_only and self.favorite_teams:
                games = {
                    game_id: game for game_id, game in games.items()
                    if game.get('home_team') in self.favorite_teams or game.get('away_team') in self.favorite_teams
                }
                self.logger.info(f"[MiLB Upcoming] Filtered to {len(games)} games for favorite teams.")

            # Process games
            new_upcoming_games = []
            
            self.logger.info(f"[MiLB] Processing {len(games)} games for upcoming games...")
            self.logger.info(f"[MiLB] Games keys: {list(games.keys()) if games else 'None'}")
            
            now_utc = datetime.now(timezone.utc)
            for game_id, game in games.items():
                self.logger.debug(f"[MiLB] Processing game {game_id} for upcoming games...")
                
                # Debug: Check the type of game data
                if not isinstance(game, dict):
                    self.logger.error(f"[MiLB] Game {game_id} is not a dictionary. Type: {type(game)}, Value: {game}")
                    continue
                
                # Ensure start_time exists before processing
                if 'start_time' not in game or not game['start_time']:
                    self.logger.warning(f"Skipping game {game_id} due to missing or empty 'start_time'.")
                    continue

                try:
                    self.logger.debug(f"[MiLB] Parsing start_time: {game['start_time']}")
                    game_time = datetime.fromisoformat(game['start_time'].replace('Z', '+00:00'))
                    if game_time.tzinfo is None:
                        game_time = game_time.replace(tzinfo=timezone.utc)
                    self.logger.debug(f"[MiLB] Parsed game_time: {game_time}")
                except (ValueError, TypeError) as e:
                    self.logger.error(f"Could not parse start_time for game {game_id}: {game['start_time']}. Error: {e}")
                    continue
                
                is_upcoming = (
                    game.get('status_state') not in ['post', 'final', 'completed'] and
                    game_time > now_utc
                )
                
                self.logger.debug(f"[MiLB] Game {game.get('away_team')} @ {game.get('home_team')}:")
                self.logger.debug(f"[MiLB]   Game time: {game_time}")
                self.logger.debug(f"[MiLB]   Current time: {now_utc}")
                self.logger.debug(f"[MiLB]   Status state: {game.get('status_state')}")
                self.logger.debug(f"[MiLB]   Is upcoming: {is_upcoming}")
                
                if is_upcoming:
                    new_upcoming_games.append(game)
                    self.logger.info(f"[MiLB] Added upcoming game: {game.get('away_team')} @ {game.get('home_team')} at {game_time}")
                    self.logger.debug(f"[MiLB] Game data for upcoming: {game}")
                
            # Sort by game time (soonest first) and apply per-team logic
            new_upcoming_games.sort(key=lambda x: x.get('start_time', ''))
            
            # If showing favorite teams only, select one game per team
            if self.milb_config.get("show_favorite_teams_only", False):
                # Select one game per favorite team (earliest upcoming game for each team)
                team_games = []
                for team in self.favorite_teams:
                    # Find games where this team is playing
                    team_specific_games = [game for game in new_upcoming_games
                                          if game.get('home_team') == team or game.get('away_team') == team]
                    
                    if team_specific_games:
                        # Take the earliest (first in sorted list)
                        team_games.append(team_specific_games[0])
                
                # Sort the final list by game time
                team_games.sort(key=lambda x: x.get('start_time', ''))
                new_upcoming_games = team_games
            else:
                # Limit to configured number if not using favorite teams only
                new_upcoming_games = new_upcoming_games[:self.upcoming_games_to_show]
            self.logger.info(f"[MiLB] Found {len(new_upcoming_games)} upcoming games after processing")
                
            # Compare new list with old list to see if an update is needed
            if self.upcoming_games != new_upcoming_games:
                self.logger.info(f"[MiLB] Upcoming games have changed. Updating list.")
                self.upcoming_games = new_upcoming_games
                
                # Reset current_game if the list is updated
                if self.upcoming_games:
                    # Check if the current game is still in the list
                    current_game_id = self.current_game.get('id') if self.current_game else None
                    if not any(g.get('id') == current_game_id for g in self.upcoming_games):
                        self.current_game_index = 0
                        self.current_game = self.upcoming_games[0]
                        self.last_game_switch = current_time
                        self.logger.info(f"[MiLB] Set current game to: {self.current_game.get('away_team')} @ {self.current_game.get('home_team')}")
                else:
                    self.current_game = None # No upcoming games
                    self.logger.info("[MiLB] No upcoming games, cleared current game")
            
            self.last_update = current_time
                
        except Exception as e:
            # Use exc_info=True to log the full traceback
            self.logger.error(f"[MiLB] Error updating upcoming games: {e}", exc_info=True)

    def display(self, force_clear: bool = False):
        """Display upcoming games."""
        # Only log display calls occasionally to reduce spam
        current_time = time.time()
        if not hasattr(self, '_last_display_log_time') or current_time - getattr(self, '_last_display_log_time', 0) >= 30:
            self.logger.info(f"[MiLB] Display called with {len(self.upcoming_games)} upcoming games")
            self._last_display_log_time = current_time
            
        if not self.upcoming_games:
            if current_time - self.last_warning_time > self.warning_cooldown:
                self.logger.info("[MiLB] No upcoming games to display")
                self.last_warning_time = current_time
            return  # Skip display update entirely
            
        try:
            current_time = time.time()
            
            # Check if it's time to switch games
            if len(self.upcoming_games) > 1 and current_time - self.last_game_switch >= self.game_display_duration:
                # Move to next game
                self.current_game_index = (self.current_game_index + 1) % len(self.upcoming_games)
                self.current_game = self.upcoming_games[self.current_game_index]
                self.last_game_switch = current_time
                force_clear = True  # Force clear when switching games
                self.logger.debug(f"[MiLB] Switched to game {self.current_game_index}: {self.current_game.get('away_team')} @ {self.current_game.get('home_team')}")
            
            # Create and display the game image
            if self.current_game:
                self.logger.debug(f"[MiLB] Creating display for current game: {self.current_game.get('away_team')} @ {self.current_game.get('home_team')}")
                game_image = self._create_game_display(self.current_game)
                self.display_manager.image = game_image
                self.display_manager.draw = ImageDraw.Draw(self.display_manager.image)
                self.display_manager.update_display()
            else:
                self.logger.debug(f"[MiLB] No current game to display")
            
        except Exception as e:
            self.logger.error(f"[MiLB] Error displaying upcoming game: {e}", exc_info=True) 