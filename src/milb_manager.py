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
import pytz

# Get logger
logger = logging.getLogger(__name__)

class BaseMiLBManager:
    """Base class for MiLB managers with common functionality."""
    def __init__(self, config: Dict[str, Any], display_manager):
        self.config = config
        self.display_manager = display_manager
        self.milb_config = config.get('milb', {})
        self.favorite_teams = self.milb_config.get('favorite_teams', [])
        self.show_records = self.milb_config.get('show_records', False)
        self.cache_manager = CacheManager()
        self.logger = logging.getLogger(__name__)
        self.logger.setLevel(logging.INFO)  # Set logger level to INFO
        
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
        if game_data['status'] == 'status_scheduled':
            # Show "Next Game" at the top using NHL-style font
            status_text = "Next Game"
            # Set font size for BDF font
            self.display_manager.calendar_font.set_char_size(height=7*64)  # 7 pixels high, 64 units per pixel
            status_width = self.display_manager.get_text_width(status_text, self.display_manager.calendar_font)
            status_x = (width - status_width) // 2
            status_y = 2
            # Draw on the current image
            self.display_manager.draw = draw
            self.display_manager._draw_bdf_text(status_text, status_x, status_y, color=(255, 255, 255), font=self.display_manager.calendar_font)
            # Update the display
            self.display_manager.update_display()
            
            # Format game date and time
            game_time_str = game_data.get('start_time')
            if not game_time_str or 'TBD' in game_time_str:
                game_date = "TBD"
                game_time_str = ""
            else:
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
                
                # Check date format from config
                use_short_date_format = self.config.get('display', {}).get('use_short_date_format', False)
                if use_short_date_format:
                    game_date = local_time.strftime("%-m/%-d")
                else:
                    game_date = self.display_manager.format_date_with_ordinal(local_time)

                game_time_str = self._format_game_time(game_data['start_time'])
            
            # Draw date and time using NHL-style fonts
            date_font = ImageFont.truetype("assets/fonts/PressStart2P-Regular.ttf", 8)
            time_font = ImageFont.truetype("assets/fonts/PressStart2P-Regular.ttf", 8)
            
            # Draw date in center
            date_width = draw.textlength(game_date, font=date_font)
            date_x = (width - date_width) // 2
            date_y = (height - date_font.size) // 2 - 3
            # draw.text((date_x, date_y), game_date, font=date_font, fill=(255, 255, 255))
            self._draw_text_with_outline(draw, game_date, (date_x, date_y), date_font)
            
            # Draw time below date
            time_width = draw.textlength(game_time_str, font=time_font)
            time_x = (width - time_width) // 2
            time_y = date_y + 10
            # draw.text((time_x, time_y), game_time_str, font=time_font, fill=(255, 255, 255))
            self._draw_text_with_outline(draw, game_time_str, (time_x, time_y), time_font)
        
        # For recent/final games, show scores and status
        elif game_data['status'] in ['status_final', 'final', 'completed']:
            # Show "Final" at the top using NHL-style font
            status_text = "Final"
            # Set font size for BDF font
            self.display_manager.calendar_font.set_char_size(height=7*64)  # 7 pixels high, 64 units per pixel
            status_width = self.display_manager.get_text_width(status_text, self.display_manager.calendar_font)
            status_x = (width - status_width) // 2
            status_y = 2
            # Draw on the current image
            self.display_manager.draw = draw
            self.display_manager._draw_bdf_text(status_text, status_x, status_y, color=(255, 255, 255), font=self.display_manager.calendar_font)
            # Update the display
            self.display_manager.update_display()
            
            # Draw scores at the bottom using NHL-style font
            away_score = str(game_data['away_score'])
            home_score = str(game_data['home_score'])
            score_text = f"{away_score}-{home_score}"
            score_font = ImageFont.truetype("assets/fonts/PressStart2P-Regular.ttf", 12)
            
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
            except IOError:
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
            
            return local_dt.strftime("%I:%M%p").lstrip('0')
        except Exception as e:
            logger.error(f"Error formatting game time: {e}")
            return "TBD"

    def _fetch_milb_api_data(self, use_cache: bool = True) -> Dict[str, Any]:
        """Fetch MiLB game data from the MLB Stats API."""
        cache_key = "milb_live_api_data"
        if use_cache:
            cached_data = self.cache_manager.get_with_auto_strategy(cache_key)
            if cached_data:
                self.logger.info("Using cached MiLB API data.")
                return cached_data

        try:
            # Check if test mode is enabled
            if self.milb_config.get('test_mode', False):
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
                    except requests.exceptions.RequestException as e:
                        self.logger.error(f"Error fetching data from {url}: {e}")
                        continue

                    if not data.get('dates') or not data['dates'][0].get('games'):
                        continue

                    for event in data['dates'][0]['games']:
                        game_pk = event['gamePk']
                        
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

                        is_favorite_game = (home_abbr in self.favorite_teams or away_abbr in self.favorite_teams)
                        
                        if not self.favorite_teams or is_favorite_game:
                            status_obj = event['status']
                            status_state = status_obj.get('abstractGameState', 'Preview') # Changed default to 'Preview'

                            # Map status to a consistent format
                            status_map = {
                                'in progress': 'status_in_progress',
                                'final': 'status_final',
                                'scheduled': 'status_scheduled',
                                'preview': 'status_scheduled'
                            }
                            mapped_status = status_map.get(status_obj.get('detailedState', '').lower(), 'status_other')
                            mapped_status_state = 'in' if mapped_status == 'status_in_progress' else 'post' if mapped_status == 'status_final' else 'pre'

                            game_data = {
                                'id': game_pk,
                                'away_team': away_abbr,
                                'home_team': home_abbr,
                                'away_score': event['teams']['away'].get('score', 0),
                                'home_score': event['teams']['home'].get('score', 0),
                                'status': mapped_status,
                                'status_state': mapped_status_state,
                                'start_time': event['gameDate'],
                                'away_record': away_record_str,
                                'home_record': home_record_str
                            }

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
                            
                            all_games[game_pk] = game_data

            if use_cache:
                self.cache_manager.set(cache_key, all_games)
            return all_games
            
        except Exception as e:
            self.logger.error(f"Error fetching MiLB data from MLB Stats API: {e}", exc_info=True)
            return {}

    def _extract_game_details(self, game) -> Dict:
        game_pk = game.get('id')
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
            status_obj = game['status']
            status_state = status_obj.get('abstractGameState', 'Final')

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

            game_data = {
                'away_team': away_abbr,
                'home_team': home_abbr,
                'away_score': game['away']['score'],
                'home_score': game['home']['score'],
                'status': mapped_status,
                'status_state': mapped_status_state,
                'start_time': game['date'],
                'away_record': f"{game['away'].get('record', {}).get('wins', 0)}-{game['away'].get('record', {}).get('losses', 0)}",
                'home_record': f"{game['home'].get('record', {}).get('wins', 0)}-{game['home'].get('record', {}).get('losses', 0)}"
            }

            if status_state == 'Live':
                linescore = game.get('linescore', {})
                game_data['inning'] = linescore.get('currentInning', 1)
                inning_state = linescore.get('inningState', 'Top').lower()
                game_data['inning_half'] = 'bottom' if 'bottom' in inning_state else 'top'
                
                if is_favorite_game:
                    try:
                        live_url = f"http://statsapi.mlb.com/api/v1.1/game/{game_pk}/feed/live"
                        live_response = self.session.get(live_url, headers=self.headers, timeout=5)
                        live_response.raise_for_status()
                        live_data = live_response.json().get('liveData', {})
                        
                        linescore_live = live_data.get('linescore', {})

                        # Overwrite score and inning data with more accurate live data from the live feed
                        if linescore_live:
                            game_data['away_score'] = linescore_live.get('teams', {}).get('away', {}).get('runs', game_data['away_score'])
                            game_data['home_score'] = linescore_live.get('teams', {}).get('home', {}).get('runs', game_data['home_score'])
                            game_data['inning'] = linescore_live.get('currentInning', game_data['inning'])
                            inning_state_live = linescore_live.get('inningState', '').lower()
                            if inning_state_live:
                                game_data['inning_half'] = 'bottom' if 'bottom' in inning_state_live else 'top'

                        game_data['balls'] = linescore_live.get('balls', 0)
                        game_data['strikes'] = linescore_live.get('strikes', 0)
                        game_data['outs'] = linescore_live.get('outs', 0)
                        
                        offense = linescore_live.get('offense', {})
                        game_data['bases_occupied'] = [
                            'first' in offense,
                            'second' in offense,
                            'third' in offense
                        ]
                    except Exception as e:
                        self.logger.warning(f"Could not fetch live details for game {game_pk}: {e}")
                        game_data.update({'balls': 0, 'strikes': 0, 'outs': 0, 'bases_occupied': [False]*3})
                else:
                    game_data.update({'balls': 0, 'strikes': 0, 'outs': 0, 'bases_occupied': [False]*3})
            else:
                game_data.update({'inning': 1, 'inning_half': 'top', 'balls': 0, 'strikes': 0, 'outs': 0, 'bases_occupied': [False]*3})
            
            return game_data
        return {}

class MiLBLiveManager(BaseMiLBManager):
    """Manager for displaying live MiLB games."""
    def __init__(self, config: Dict[str, Any], display_manager):
        super().__init__(config, display_manager)
        self.logger.info("Initialized MiLB Live Manager")
        self.live_games = []
        self.current_game = None  # Initialize current_game to None
        self.current_game_index = 0
        self.last_update = 0
        self.update_interval = self.milb_config.get('live_update_interval', 20)
        self.no_data_interval = 300  # 5 minutes when no live games
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
        
        if current_time - self.last_update >= interval:
            self.last_update = current_time
            
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
            else:
                # Fetch live game data from MiLB API
                games = self._fetch_milb_api_data(use_cache=False)
                if games:
                    # Find all live games involving favorite teams
                    new_live_games = []
                    for game in games.values():
                        # Only process games that are actually in progress
                        if game['status_state'] == 'in' and game['status'] == 'status_in_progress':
                            if not self.favorite_teams or (
                                game['home_team'] in self.favorite_teams or 
                                game['away_team'] in self.favorite_teams
                            ):
                                # Ensure scores are valid numbers
                                try:
                                    game['home_score'] = int(game['home_score'])
                                    game['away_score'] = int(game['away_score'])
                                    new_live_games.append(game)
                                except (ValueError, TypeError):
                                    self.logger.warning(f"Invalid score format for game {game['away_team']} @ {game['home_team']}")
                    
                    # Only log if there's a change in games or enough time has passed
                    should_log = (
                        current_time - self.last_log_time >= self.log_interval or
                        len(new_live_games) != len(self.live_games) or
                        not self.live_games  # Log if we had no games before
                    )
                    
                    if should_log:
                        if new_live_games:
                            logger.info(f"[MiLB] Found {len(new_live_games)} live games")
                            for game in new_live_games:
                                logger.info(f"[MiLB] Live game: {game['away_team']} vs {game['home_team']} - {game['inning_half']}{game['inning']}, {game['balls']}-{game['strikes']}")
                        else:
                            logger.info("[MiLB] No live games found")
                        self.last_log_time = current_time
                    
                    if new_live_games:
                        # Update the current game with the latest data
                        for new_game in new_live_games:
                            if self.current_game and (
                                (new_game['home_team'] == self.current_game['home_team'] and 
                                 new_game['away_team'] == self.current_game['away_team']) or
                                (new_game['home_team'] == self.current_game['away_team'] and 
                                 new_game['away_team'] == self.current_game['home_team'])
                            ):
                                self.current_game = new_game
                                break
                        
                        # Only update the games list if we have new games
                        if not self.live_games or set(game['away_team'] + game['home_team'] for game in new_live_games) != set(game['away_team'] + game['home_team'] for game in self.live_games):
                            self.live_games = new_live_games
                            # If we don't have a current game or it's not in the new list, start from the beginning
                            if not self.current_game or self.current_game not in self.live_games:
                                self.current_game_index = 0
                                self.current_game = self.live_games[0]
                                self.last_game_switch = current_time
                        
                        # Always update display when we have new data, but limit to once per second
                        if current_time - self.last_display_update >= 1.0:
                            # self.display(force_clear=True) # REMOVED: DisplayController handles this
                            self.last_display_update = current_time
                    else:
                        # No live games found
                        self.live_games = []
                        self.current_game = None
                
                # Check if it's time to switch games
                if len(self.live_games) > 1 and (current_time - self.last_game_switch) >= self.game_display_duration:
                    self.current_game_index = (self.current_game_index + 1) % len(self.live_games)
                    self.current_game = self.live_games[self.current_game_index]
                    self.last_game_switch = current_time
                    # Force display update when switching games
                    # self.display(force_clear=True) # REMOVED: DisplayController handles this
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
        bdf_font.set_char_size(height=7*64) # Set 7px height
        count_text_width = self.display_manager.get_text_width(count_text, bdf_font)
        
        # Position below the base/out cluster
        cluster_bottom_y = overall_start_y + base_cluster_height # Find the bottom of the taller part (bases)
        count_y = cluster_bottom_y + 2 # Start 2 pixels below cluster
        
        # Center horizontally within the BASE cluster width
        count_x = bases_origin_x + (base_cluster_width - count_text_width) // 2
        
        # Ensure draw object is set and draw text
        self.display_manager.draw = draw 
        # self.display_manager._draw_bdf_text(count_text, count_x, count_y, text_color, font=bdf_font)
        # Use _draw_text_with_outline for count text
        # self._draw_text_with_outline(draw, count_text, (count_x, count_y), bdf_font, fill=text_color)

        # Draw Balls-Strikes Count with outline using BDF font
        # Define outline color (consistent with _draw_text_with_outline default)
        outline_color_for_bdf = (0, 0, 0)
        
        # Draw outline
        for dx_offset, dy_offset in [(-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1)]:
            self.display_manager._draw_bdf_text(count_text, count_x + dx_offset, count_y + dy_offset, color=outline_color_for_bdf, font=bdf_font)
        
        # Draw main text
        self.display_manager._draw_bdf_text(count_text, count_x, count_y, color=text_color, font=bdf_font)

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
            return
            
        try:
            # Create and display the game image using the new method
            game_image = self._create_live_game_display(self.current_game)
            # Set the image in the display manager
            self.display_manager.image = game_image
            self.display_manager.draw = ImageDraw.Draw(self.display_manager.image)
            # Update the display
            self.display_manager.update_display()
        except Exception as e:
            logger.error(f"[MiLB] Error displaying live game: {e}", exc_info=True)

class MiLBRecentManager(BaseMiLBManager):
    """Manager for displaying recent MiLB games."""
    def __init__(self, config: Dict[str, Any], display_manager):
        super().__init__(config, display_manager)
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
                # Convert game time to UTC datetime
                game_time_str = game['start_time'].replace('Z', '+00:00')
                game_time = datetime.fromisoformat(game_time_str)
                if game_time.tzinfo is None:
                    game_time = game_time.replace(tzinfo=timezone.utc)
                
                # Check if this is a favorite team game
                is_favorite_game = (game['home_team'] in self.favorite_teams or 
                                  game['away_team'] in self.favorite_teams)
                
                # Log all games for debugging
                game_info = f"{game['away_team']} @ {game['home_team']} (Status: {game['status']}, State: {game['status_state']})"
                all_games_log.append(game_info)
                
                if is_favorite_game:
                    favorite_games_log.append(game_info)
                    logger.info(f"[MiLB] Found favorite team game: {game['away_team']} @ {game['home_team']}")
                    logger.info(f"[MiLB] Game time (UTC): {game_time}")
                    logger.info(f"[MiLB] Game status: {game['status']}, State: {game['status_state']}")
                    logger.info(f"[MiLB] Scores: {game['away_team']} {game.get('away_score', 0)} - {game['home_team']} {game.get('home_score', 0)}")
                
                # Use status_state to determine if game is final
                is_final = game['status_state'] in ['post', 'final', 'completed']
                
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
            
            # Sort by game time (most recent first) and limit to recent_games_to_show
            new_recent_games.sort(key=lambda x: x['start_time'], reverse=True)
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
                    if (game['home_team'] in self.favorite_teams or game['away_team'] in self.favorite_teams):
                        game_time_str = game['start_time'].replace('Z', '+00:00')
                        game_time = datetime.fromisoformat(game_time_str)
                        if game_time.tzinfo is None:
                            game_time = game_time.replace(tzinfo=timezone.utc)
                        
                        # Include any game from the last 7 days
                        if game_time >= datetime.now(timezone.utc) - timedelta(days=7):
                            fallback_games.append(game)
                            logger.info(f"[MiLB] Added fallback game: {game['away_team']} @ {game['home_team']} (Status: {game['status_state']})")
                
                fallback_games.sort(key=lambda x: x['start_time'], reverse=True)
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
            if current_time - self.last_game_switch >= self.game_display_duration:
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

class MiLBUpcomingManager(BaseMiLBManager):
    """Manager for upcoming MiLB games."""
    def __init__(self, config: Dict[str, Any], display_manager):
        super().__init__(config, display_manager)
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
        self.logger.debug(f"[MiLB] show_favorite_teams_only: {self.milb_config.get('show_favorite_teams_only', False)}")
        self.logger.debug(f"[MiLB] favorite_teams: {self.favorite_teams}")
        if self.last_update != 0 and (current_time - self.last_update < self.update_interval):
            return
            
        try:
            # Fetch data from MiLB API
            games = self._fetch_milb_api_data(use_cache=True)
            if not games:
                self.logger.warning("[MiLB] No games returned from API for upcoming games update.")
                return

            # --- Optimization: Filter for favorite teams before processing ---
            if self.milb_config.get("show_favorite_teams_only", False) and self.favorite_teams:
                games = {
                    game_id: game for game_id, game in games.items()
                    if game['home_team'] in self.favorite_teams or game['away_team'] in self.favorite_teams
                }
                self.logger.info(f"[MiLB Upcoming] Filtered to {len(games)} games for favorite teams.")

            # Process games
            new_upcoming_games = []
            
            self.logger.info(f"[MiLB] Processing {len(games)} games for upcoming games...")
            
            now_utc = datetime.now(timezone.utc)
            for game_id, game in games.items():
                self.logger.debug(f"[MiLB] Processing game {game_id} for upcoming games...")
                
                game_time = datetime.fromisoformat(game['start_time'].replace('Z', '+00:00'))
                if game_time.tzinfo is None:
                    game_time = game_time.replace(tzinfo=timezone.utc)
                
                is_upcoming = (
                    game['status_state'] not in ['post', 'final', 'completed'] and
                    game_time > now_utc
                )
                
                # Add debug logging for upcoming games logic
                self.logger.debug(f"[MiLB] Game {game['away_team']} @ {game['home_team']}:")
                self.logger.debug(f"[MiLB]   Game time: {game_time}")
                self.logger.debug(f"[MiLB]   Current time: {now_utc}")
                self.logger.debug(f"[MiLB]   Status state: {game['status_state']}")
                self.logger.debug(f"[MiLB]   Is upcoming: {is_upcoming}")
                
                if is_upcoming:
                    new_upcoming_games.append(game)
                    self.logger.info(f"[MiLB] Added upcoming game: {game['away_team']} @ {game['home_team']} at {game_time}")
                
            # Sort by game time (soonest first) and limit to upcoming_games_to_show
            new_upcoming_games.sort(key=lambda x: x['start_time'])
            new_upcoming_games = new_upcoming_games[:self.upcoming_games_to_show]
                
            if new_upcoming_games:
                self.logger.info(f"[MiLB] Found {len(new_upcoming_games)} upcoming games for favorite teams")
                self.upcoming_games = new_upcoming_games
                if not self.current_game:
                    self.current_game = self.upcoming_games[0]
            else:
                self.logger.info("[MiLB] No upcoming games found for favorite teams")
                self.upcoming_games = []
                self.current_game = None
                
            self.last_update = current_time
                
        except Exception as e:
            self.logger.error(f"[MiLB] Error updating upcoming games: {e}", exc_info=True)

    def display(self, force_clear: bool = False):
        """Display upcoming games."""
        if not self.upcoming_games:
            current_time = time.time()
            if current_time - self.last_warning_time > self.warning_cooldown:
                self.logger.info("[MiLB] No upcoming games to display")
                self.last_warning_time = current_time
            return  # Skip display update entirely
            
        try:
            current_time = time.time()
            
            # Check if it's time to switch games
            if current_time - self.last_game_switch >= self.game_display_duration:
                # Move to next game
                self.current_game_index = (self.current_game_index + 1) % len(self.upcoming_games)
                self.current_game = self.upcoming_games[self.current_game_index]
                self.last_game_switch = current_time
                force_clear = True  # Force clear when switching games
            
            # Create and display the game image
            if self.current_game:
                game_image = self._create_game_display(self.current_game)
                self.display_manager.image = game_image
                self.display_manager.draw = ImageDraw.Draw(self.display_manager.image)
                self.display_manager.update_display()
            
        except Exception as e:
            self.logger.error(f"[MiLB] Error displaying upcoming game: {e}", exc_info=True) 