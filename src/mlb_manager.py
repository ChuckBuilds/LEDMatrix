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

class BaseMLBManager:
    """Base class for MLB managers with common functionality."""
    def __init__(self, config: Dict[str, Any], display_manager):
        self.config = config
        self.display_manager = display_manager
        self.mlb_config = config.get('mlb', {})
        self.favorite_teams = self.mlb_config.get('favorite_teams', [])
        self.cache_manager = CacheManager()
        self.logger = logging.getLogger(__name__)
        self.logger.setLevel(logging.INFO)  # Set logger level to INFO
        
        # Logo handling
        self.logo_dir = self.mlb_config.get('logo_dir', os.path.join('assets', 'sports', 'mlb_logos'))
        if not os.path.exists(self.logo_dir):
            self.logger.warning(f"MLB logos directory not found: {self.logo_dir}")
            try:
                os.makedirs(self.logo_dir, exist_ok=True)
                self.logger.info(f"Created MLB logos directory: {self.logo_dir}")
            except Exception as e:
                self.logger.error(f"Failed to create MLB logos directory: {e}")
        
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
        """Create a display image for an MLB game with team logos, score, and game state."""
        width = self.display_manager.matrix.width
        height = self.display_manager.matrix.height
        image = Image.new('RGB', (width, height), color=(0, 0, 0))
        
        # Make logos 150% of display dimensions to allow them to extend off screen
        max_width = int(width * 1.5)
        max_height = int(height * 1.5)

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
            game_time = datetime.fromisoformat(game_data['start_time'].replace('Z', '+00:00'))
            timezone_str = self.config.get('timezone', 'UTC')
            try:
                tz = pytz.timezone(timezone_str)
            except pytz.exceptions.UnknownTimeZoneError:
                logger.warning(f"Unknown timezone: {timezone_str}, falling back to UTC")
                tz = pytz.UTC
            if game_time.tzinfo is None:
                game_time = game_time.replace(tzinfo=pytz.UTC)
            local_time = game_time.astimezone(tz)
            game_date = local_time.strftime("%b %d")
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
            
            return local_dt.strftime("%I:%M %p")
        except Exception as e:
            logger.error(f"Error formatting game time: {e}")
            return "TBD"

    def _fetch_mlb_api_data(self) -> Dict[str, Any]:
        """Fetch MLB game data from the ESPN API."""
        try:
            # Check if test mode is enabled
            if self.mlb_config.get('test_mode', False):
                self.logger.info("Using test mode data for MLB")
                return {
                    'test_game_1': {
                        'away_team': 'TB',
                        'home_team': 'TEX',
                        'away_score': 3,
                        'home_score': 2,
                        'status': 'in',
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
            
            # Get dates for API request
            now = datetime.now(timezone.utc)
            yesterday = now - timedelta(days=1)
            tomorrow = now + timedelta(days=1)
            
            # Format dates for API
            dates = [
                yesterday.strftime("%Y%m%d"),
                now.strftime("%Y%m%d"),
                tomorrow.strftime("%Y%m%d")
            ]
            
            all_games = {}
            
            # Fetch games for each date
            for date in dates:
                # ESPN API endpoint for MLB games with date parameter
                url = f"https://site.api.espn.com/apis/site/v2/sports/baseball/mlb/scoreboard?dates={date}"
                
                self.logger.info(f"Fetching MLB games from ESPN API for date: {date}")
                response = self.session.get(url, headers=self.headers, timeout=10)
                response.raise_for_status()
                
                data = response.json()
                
                for event in data.get('events', []):
                    game_id = event['id']
                    status = event['status']['type']['name'].lower()
                    status_state = event['status']['type']['state'].lower()
                    
                    # Get team information
                    competitors = event['competitions'][0]['competitors']
                    home_team = next(c for c in competitors if c['homeAway'] == 'home')
                    away_team = next(c for c in competitors if c['homeAway'] == 'away')
                    
                    # Get team abbreviations
                    home_abbr = home_team['team']['abbreviation']
                    away_abbr = away_team['team']['abbreviation']
                    
                    # Check if this is a favorite team game
                    is_favorite_game = (home_abbr in self.favorite_teams or away_abbr in self.favorite_teams)
                    
                    # Only log detailed information for favorite teams
                    if is_favorite_game:
                        self.logger.info(f"Found favorite team game: {away_abbr} @ {home_abbr} (Status: {status}, State: {status_state})")
                        self.logger.debug(f"[MLB] Full status data: {event['status']}")
                        self.logger.debug(f"[MLB] Status type: {status}, State: {status_state}")
                        self.logger.debug(f"[MLB] Status detail: {event['status']['type'].get('detail', '')}")
                        self.logger.debug(f"[MLB] Status shortDetail: {event['status']['type'].get('shortDetail', '')}")
                    
                    # Get game state information
                    if status_state == 'in':
                        # For live games, get detailed state
                        inning = event['status'].get('period', 1)  # Get inning from status period
                        
                        # Get inning information from status
                        status_detail = event['status']['type'].get('detail', '').lower()
                        status_short = event['status']['type'].get('shortDetail', '').lower()
                        
                        if is_favorite_game:
                            self.logger.debug(f"[MLB] Raw status detail: {event['status']['type'].get('detail')}")
                            self.logger.debug(f"[MLB] Raw status short: {event['status']['type'].get('shortDetail')}")
                        
                        # Determine inning half from status information
                        inning_half = 'top'  # Default

                        # Handle end of inning: next inning is top
                        if 'end' in status_detail or 'end' in status_short:
                            inning_half = 'top'
                            inning += 1
                            if is_favorite_game:
                                self.logger.debug(f"[MLB] Detected end of inning. Setting to Top {inning}")
                        # Handle middle of inning: next is bottom of current inning
                        elif 'mid' in status_detail or 'mid' in status_short:
                            inning_half = 'bottom'
                            if is_favorite_game:
                                self.logger.debug(f"[MLB] Detected middle of inning. Setting to Bottom {inning}")
                        # Handle bottom of inning
                        elif 'bottom' in status_detail or 'bot' in status_detail or 'bottom' in status_short or 'bot' in status_short:
                            inning_half = 'bottom'
                            if is_favorite_game:
                                self.logger.debug(f"[MLB] Detected bottom of inning: {inning}")
                        # Handle top of inning
                        elif 'top' in status_detail or 'top' in status_short:
                            inning_half = 'top'
                            if is_favorite_game:
                                self.logger.debug(f"[MLB] Detected top of inning: {inning}")
                        
                        if is_favorite_game:
                            self.logger.debug(f"[MLB] Status detail: {status_detail}")
                            self.logger.debug(f"[MLB] Status short: {status_short}")
                            self.logger.debug(f"[MLB] Determined inning: {inning_half} {inning}")
                        
                        # Get count and bases from situation
                        situation = event['competitions'][0].get('situation', {})
                        
                        if is_favorite_game:
                            self.logger.debug(f"[MLB] Full situation data: {situation}")
                        
                        # Get count from the correct location in the API response
                        count = situation.get('count', {})
                        balls = count.get('balls', 0)
                        strikes = count.get('strikes', 0)
                        outs = situation.get('outs', 0)
                        
                        # Add detailed logging for favorite team games
                        if is_favorite_game:
                            self.logger.debug(f"[MLB] Full situation data: {situation}")
                            self.logger.debug(f"[MLB] Count object: {count}")
                            self.logger.debug(f"[MLB] Raw count values - balls: {balls}, strikes: {strikes}")
                            self.logger.debug(f"[MLB] Raw outs value: {outs}")
                        
                        # Try alternative locations for count data
                        if balls == 0 and strikes == 0:
                            # First try the summary field
                            if 'summary' in situation:
                                try:
                                    count_summary = situation['summary']
                                    balls, strikes = map(int, count_summary.split('-'))
                                    if is_favorite_game:
                                        self.logger.debug(f"[MLB] Using summary count: {count_summary}")
                                except (ValueError, AttributeError):
                                    if is_favorite_game:
                                        self.logger.debug("[MLB] Could not parse summary count")
                            else:
                                # Check if count is directly in situation
                                balls = situation.get('balls', 0)
                                strikes = situation.get('strikes', 0)
                                if is_favorite_game:
                                    self.logger.debug(f"[MLB] Using direct situation count: balls={balls}, strikes={strikes}")
                                    self.logger.debug(f"[MLB] Full situation keys: {list(situation.keys())}")
                        
                        if is_favorite_game:
                            self.logger.debug(f"[MLB] Final count: balls={balls}, strikes={strikes}")
                        
                        # Get base runners
                        bases_occupied = [
                            situation.get('onFirst', False),
                            situation.get('onSecond', False),
                            situation.get('onThird', False)
                        ]
                        
                        if is_favorite_game:
                            self.logger.debug(f"[MLB] Bases occupied: {bases_occupied}")
                    else:
                        # Default values for non-live games
                        inning = 1
                        inning_half = 'top'
                        balls = 0
                        strikes = 0
                        outs = 0
                        bases_occupied = [False, False, False]
                    
                    all_games[game_id] = {
                        'away_team': away_abbr,
                        'home_team': home_abbr,
                        'away_score': away_team['score'],
                        'home_score': home_team['score'],
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
            
            # Only log favorite team games
            favorite_games = [game for game in all_games.values() 
                           if game['home_team'] in self.favorite_teams or 
                              game['away_team'] in self.favorite_teams]
            if favorite_games:
                self.logger.info(f"Found {len(favorite_games)} games for favorite teams: {self.favorite_teams}")
                for game in favorite_games:
                    self.logger.info(f"Favorite team game: {game['away_team']} @ {game['home_team']} (Status: {game['status']}, State: {game['status_state']})")
            
            return all_games
            
        except Exception as e:
            self.logger.error(f"Error fetching MLB data from ESPN API: {e}")
            return {}

class MLBLiveManager(BaseMLBManager):
    """Manager for displaying live MLB games."""
    def __init__(self, config: Dict[str, Any], display_manager):
        super().__init__(config, display_manager)
        self.logger.info("Initialized MLB Live Manager")
        self.live_games = []
        self.current_game = None  # Initialize current_game to None
        self.current_game_index = 0
        self.last_update = 0
        self.update_interval = self.mlb_config.get('live_update_interval', 20)
        self.no_data_interval = 300  # 5 minutes when no live games
        self.last_game_switch = 0  # Track when we last switched games
        self.game_display_duration = self.mlb_config.get('live_game_duration', 30)  # Display each live game for 30 seconds
        self.last_display_update = 0  # Track when we last updated the display
        self.last_log_time = 0
        self.log_interval = 300  # Only log status every 5 minutes
        self.last_count_log_time = 0  # Track when we last logged count data
        self.count_log_interval = 5  # Only log count data every 5 seconds
        self.test_mode = self.mlb_config.get('test_mode', False)

        # Initialize with test game only if test mode is enabled
        if self.test_mode:
            self.current_game = {
                "home_team": "TB",
                "away_team": "TEX",
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
                "home_logo_path": os.path.join(self.logo_dir, "TB.png"),
                "away_logo_path": os.path.join(self.logo_dir, "TEX.png"),
                "start_time": datetime.now(timezone.utc).isoformat(),
            }
            self.live_games = [self.current_game]
            self.logger.info("Initialized MLBLiveManager with test game: TB vs TEX")
        else:
            self.logger.info("Initialized MLBLiveManager in live mode")

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
                # Fetch live game data from MLB API
                games = self._fetch_mlb_api_data()
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
                            logger.info(f"[MLB] Found {len(new_live_games)} live games")
                            for game in new_live_games:
                                logger.info(f"[MLB] Live game: {game['away_team']} vs {game['home_team']} - {game['inning_half']}{game['inning']}, {game['balls']}-{game['strikes']}")
                        else:
                            logger.info("[MLB] No live games found")
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
        """Create a display image for a live MLB game."""
        width = self.display_manager.matrix.width
        height = self.display_manager.matrix.height
        image = Image.new('RGB', (width, height), color=(0, 0, 0))

        # Make logos 150% of display dimensions to allow them to extend off screen
        max_width = int(width * 1.5)
        max_height = int(height * 1.5)
        
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
        
        # Draw Inning (Top Center)
        inning_half = game_data['inning_half']
        inning_num = game_data['inning']
        if game_data['status'] in ['status_final', 'final', 'completed']:
            inning_text = "FINAL"
        else:
            inning_half_indicator = "▲" if game_data['inning_half'].lower() == 'top' else "▼"
            inning_num = game_data['inning']
            inning_text = f"{inning_half_indicator}{inning_num}"
        
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
            self.logger.debug(f"[MLB] Displaying count: {balls}-{strikes}")
            self.logger.debug(f"[MLB] Raw count data: balls={game_data.get('balls')}, strikes={game_data.get('strikes')}")
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
        score_y = height - font_height - 2 # 2 pixels padding from bottom
        
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
            logger.error(f"[MLB] Error displaying live game: {e}", exc_info=True)

class MLBRecentManager(BaseMLBManager):
    """Manager for displaying recent MLB games."""
    def __init__(self, config: Dict[str, Any], display_manager):
        super().__init__(config, display_manager)
        self.logger.info("Initialized MLB Recent Manager")
        self.recent_games = []
        self.current_game = None
        self.current_game_index = 0
        self.last_update = 0
        self.update_interval = self.mlb_config.get('recent_update_interval', 3600)
        self.recent_hours = self.mlb_config.get('recent_game_hours', 72)  # Increased from 48 to 72 hours
        self.last_game_switch = 0  # Track when we last switched games
        self.game_display_duration = 10  # Display each game for 10 seconds
        self.last_warning_time = 0
        self.warning_cooldown = 300  # Only show warning every 5 minutes
        logger.info(f"Initialized MLBRecentManager with {len(self.favorite_teams)} favorite teams")

    def update(self):
        """Update recent games data."""
        current_time = time.time()
        if current_time - self.last_update < self.update_interval:
            return
            
        try:
            # Fetch data from MLB API
            games = self._fetch_mlb_api_data()
            if not games:
                logger.warning("[MLB] No games returned from API")
                return
                
            # Process games
            new_recent_games = []
            now = datetime.now(timezone.utc)  # Make timezone-aware
            recent_cutoff = now - timedelta(hours=self.recent_hours)
            
            logger.info(f"[MLB] Time window: {recent_cutoff} to {now}")
            
            for game_id, game in games.items():
                # Convert game time to UTC datetime
                game_time_str = game['start_time'].replace('Z', '+00:00')
                game_time = datetime.fromisoformat(game_time_str)
                if game_time.tzinfo is None:
                    game_time = game_time.replace(tzinfo=timezone.utc)
                
                # Check if this is a favorite team game
                is_favorite_game = (game['home_team'] in self.favorite_teams or 
                                  game['away_team'] in self.favorite_teams)
                
                if is_favorite_game:
                    logger.info(f"[MLB] Checking favorite team game: {game['away_team']} @ {game['home_team']}")
                    logger.info(f"[MLB] Game time (UTC): {game_time}")
                    logger.info(f"[MLB] Game status: {game['status']}, State: {game['status_state']}")
                
                # Use status_state to determine if game is final
                is_final = game['status_state'] in ['post', 'final', 'completed']
                is_within_time = recent_cutoff <= game_time <= now
                
                if is_favorite_game:
                    logger.info(f"[MLB] Is final: {is_final}")
                    logger.info(f"[MLB] Is within time window: {is_within_time}")
                    logger.info(f"[MLB] Time comparison: {recent_cutoff} <= {game_time} <= {now}")
                
                # Only add favorite team games that are final and within time window
                if is_favorite_game and is_final and is_within_time:
                    new_recent_games.append(game)
                    logger.info(f"[MLB] Added favorite team game to recent list: {game['away_team']} @ {game['home_team']}")
            
            if new_recent_games:
                logger.info(f"[MLB] Found {len(new_recent_games)} recent games for favorite teams: {self.favorite_teams}")
                self.recent_games = new_recent_games
                if not self.current_game:
                    self.current_game = self.recent_games[0]
            else:
                logger.info("[MLB] No recent games found for favorite teams")
                self.recent_games = []
                self.current_game = None
            
            self.last_update = current_time
            
        except Exception as e:
            logger.error(f"[MLB] Error updating recent games: {e}", exc_info=True)

    def display(self, force_clear: bool = False):
        """Display recent games."""
        if not self.recent_games:
            current_time = time.time()
            if current_time - self.last_warning_time > self.warning_cooldown:
                logger.info("[MLB] No recent games to display")
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
            logger.error(f"[MLB] Error displaying recent game: {e}", exc_info=True)

class MLBUpcomingManager(BaseMLBManager):
    """Manager for displaying upcoming MLB games."""
    def __init__(self, config: Dict[str, Any], display_manager):
        super().__init__(config, display_manager)
        self.logger.info("Initialized MLB Upcoming Manager")
        self.upcoming_games = []
        self.current_game = None
        self.current_game_index = 0
        self.last_update = 0
        self.update_interval = self.mlb_config.get('upcoming_update_interval', 3600)
        self.last_warning_time = 0
        self.warning_cooldown = 300  # Only show warning every 5 minutes
        self.last_game_switch = 0  # Track when we last switched games
        self.game_display_duration = 10  # Display each game for 10 seconds
        logger.info(f"Initialized MLBUpcomingManager with {len(self.favorite_teams)} favorite teams")

    def update(self):
        """Update upcoming games data."""
        current_time = time.time()
        if current_time - self.last_update < self.update_interval:
            return
            
        try:
            # Fetch data from MLB API
            games = self._fetch_mlb_api_data()
            if games:
                # Process games
                new_upcoming_games = []
                now = datetime.now(timezone.utc)  # Make timezone-aware
                upcoming_cutoff = now + timedelta(hours=24)
                
                logger.info(f"Looking for games between {now} and {upcoming_cutoff}")
                
                for game in games.values():
                    # Check if this is a favorite team game first
                    is_favorite_game = (game['home_team'] in self.favorite_teams or 
                                      game['away_team'] in self.favorite_teams)
                    
                    if not is_favorite_game:
                        continue  # Skip non-favorite team games
                        
                    game_time = datetime.fromisoformat(game['start_time'].replace('Z', '+00:00'))
                    # Ensure game_time is timezone-aware (UTC)
                    if game_time.tzinfo is None:
                        game_time = game_time.replace(tzinfo=timezone.utc)
                    logger.info(f"Checking favorite team game: {game['away_team']} @ {game['home_team']} at {game_time}")
                    logger.info(f"Game status: {game['status']}, State: {game['status_state']}")
                    
                    # Check if game is within our time window
                    is_within_time = now <= game_time <= upcoming_cutoff
                    
                    # For upcoming games, we'll consider any game that:
                    # 1. Is within our time window
                    # 2. Is not final (not 'post' or 'final' state)
                    # 3. Has a future start time
                    is_upcoming = (
                        is_within_time and 
                        game['status_state'] not in ['post', 'final', 'completed'] and
                        game_time > now
                    )
                    
                    logger.info(f"Within time window: {is_within_time}")
                    logger.info(f"Is upcoming: {is_upcoming}")
                    logger.info(f"Game time > now: {game_time > now}")
                    logger.info(f"Status state not final: {game['status_state'] not in ['post', 'final', 'completed']}")
                    
                    if is_upcoming:
                        new_upcoming_games.append(game)
                        logger.info(f"Added favorite team game to upcoming list: {game['away_team']} @ {game['home_team']}")
                
                # Filter for favorite teams (though we already filtered above, this is a safety check)
                new_team_games = [game for game in new_upcoming_games 
                             if game['home_team'] in self.favorite_teams or 
                                game['away_team'] in self.favorite_teams]
                
                if new_team_games:
                    logger.info(f"[MLB] Found {len(new_team_games)} upcoming games for favorite teams")
                    self.upcoming_games = new_team_games
                    if not self.current_game:
                        self.current_game = self.upcoming_games[0]
                else:
                    logger.info("[MLB] No upcoming games found for favorite teams")
                    self.upcoming_games = []
                    self.current_game = None
                
                self.last_update = current_time
                
        except Exception as e:
            logger.error(f"[MLB] Error updating upcoming games: {e}", exc_info=True)

    def display(self, force_clear: bool = False):
        """Display upcoming games."""
        if not self.upcoming_games:
            current_time = time.time()
            if current_time - self.last_warning_time > self.warning_cooldown:
                logger.info("[MLB] No upcoming games to display")
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
            game_image = self._create_game_display(self.current_game)
            self.display_manager.image = game_image
            self.display_manager.draw = ImageDraw.Draw(self.display_manager.image)
            self.display_manager.update_display()
            
        except Exception as e:
            logger.error(f"[MLB] Error displaying upcoming game: {e}", exc_info=True) 