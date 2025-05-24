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

# Constants for NCAA Baseball API URL
ESPN_NCAABB_SCOREBOARD_URL = "https://site.api.espn.com/apis/site/v2/sports/baseball/college-baseball/scoreboard"

class BaseNCAAMensBaseballManager:
    """Base class for NCAA Mens Baseball managers with common functionality."""
    def __init__(self, config: Dict[str, Any], display_manager):
        self.config = config
        self.display_manager = display_manager
        self.ncaa_mens_baseball_config = config.get('ncaa_mens_baseball_scoreboard', {})
        self.favorite_teams = self.ncaa_mens_baseball_config.get('favorite_teams', [])
        self.cache_manager = CacheManager()
        self.logger = logging.getLogger(__name__)
        self.logger.setLevel(logging.DEBUG)  # Set logger level to DEBUG
        
        # Logo handling
        self.logo_dir = self.ncaa_mens_baseball_config.get('logo_dir', os.path.join('assets', 'sports', 'ncaa_fbs_logos'))
        if not os.path.exists(self.logo_dir):
            self.logger.warning(f"NCAA Mens Baseball logos directory not found: {self.logo_dir}")
            try:
                os.makedirs(self.logo_dir, exist_ok=True)
                self.logger.info(f"Created NCAA Mens Baseball logos directory: {self.logo_dir}")
            except Exception as e:
                self.logger.error(f"Failed to create NCAA Mens Baseball logos directory: {e}")
        
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
                return Image.open(logo_path)
            else:
                logger.warning(f"[NCAAMensBaseball] Logo not found for team {team_abbr}")
                return None
        except Exception as e:
            logger.error(f"[NCAAMensBaseball] Error loading logo for team {team_abbr}: {e}")
            return None

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
        """Create a display image for an NCAA Mens Baseball game with team logos, score, and game state."""
        width = self.display_manager.matrix.width
        height = self.display_manager.matrix.height
        image = Image.new('RGB', (width, height), color=(0, 0, 0))
        draw = ImageDraw.Draw(image)
        
        max_width = 35
        max_height = 35
        logo_size = (max_width, max_height)
        logo_y_offset = (height - max_height) // 2
        
        away_logo = self._get_team_logo(game_data['away_team'])
        home_logo = self._get_team_logo(game_data['home_team'])
        
        if away_logo and home_logo:
            away_logo = away_logo.resize(logo_size, Image.Resampling.LANCZOS)
            home_logo = home_logo.resize(logo_size, Image.Resampling.LANCZOS)
            away_x = 0
            away_y = logo_y_offset
            home_x = width - home_logo.width 
            home_y = logo_y_offset
            image.paste(away_logo, (away_x, away_y), away_logo)
            image.paste(home_logo, (home_x, home_y), home_logo)
        
        # For upcoming games, show date and time stacked in the center
        if game_data['status'] == 'status_scheduled':
            status_text = "Next Game"
            self.display_manager.calendar_font.set_char_size(height=7*64)
            status_width = self.display_manager.get_text_width(status_text, self.display_manager.calendar_font)
            status_x = (width - status_width) // 2
            status_y = 2
            self.display_manager.draw = draw
            self.display_manager._draw_bdf_text(status_text, status_x, status_y, color=(255, 255, 255), font=self.display_manager.calendar_font)
            
            game_time = datetime.fromisoformat(game_data['start_time'].replace('Z', '+00:00'))
            timezone_str = self.config.get('timezone', 'UTC')
            try:
                tz = pytz.timezone(timezone_str)
            except pytz.exceptions.UnknownTimeZoneError:
                logger.warning(f"[NCAAMensBaseball] Unknown timezone: {timezone_str}, falling back to UTC")
                tz = pytz.UTC
            if game_time.tzinfo is None:
                game_time = game_time.replace(tzinfo=pytz.UTC)
            local_time = game_time.astimezone(tz)
            game_date = local_time.strftime("%b %d")
            game_time_str = self._format_game_time(game_data['start_time'])
            
            date_font = ImageFont.truetype("assets/fonts/PressStart2P-Regular.ttf", 8)
            time_font = ImageFont.truetype("assets/fonts/PressStart2P-Regular.ttf", 8)
            
            date_width = draw.textlength(game_date, font=date_font)
            date_x = (width - date_width) // 2
            date_y = (height - date_font.size) // 2 - 3
            draw.text((date_x, date_y), game_date, font=date_font, fill=(255, 255, 255))
            
            time_width = draw.textlength(game_time_str, font=time_font)
            time_x = (width - time_width) // 2
            time_y = date_y + 10
            draw.text((time_x, time_y), game_time_str, font=time_font, fill=(255, 255, 255))
        
        # For recent/final games, show scores and status
        elif game_data['status'] in ['status_final', 'final', 'completed']:
            status_text = "Final"
            self.display_manager.calendar_font.set_char_size(height=7*64)
            status_width = self.display_manager.get_text_width(status_text, self.display_manager.calendar_font)
            status_x = (width - status_width) // 2
            status_y = 2
            self.display_manager.draw = draw
            self.display_manager._draw_bdf_text(status_text, status_x, status_y, color=(255, 255, 255), font=self.display_manager.calendar_font)
            
            away_score = str(game_data['away_score'])
            home_score = str(game_data['home_score'])
            score_text = f"{away_score}-{home_score}"
            score_font = ImageFont.truetype("assets/fonts/PressStart2P-Regular.ttf", 12)
            
            score_width = draw.textlength(score_text, font=score_font)
            score_x = (width - score_width) // 2
            score_y = height - score_font.size - 2
            draw.text((score_x, score_y), score_text, font=score_font, fill=(255, 255, 255))
        
        return image

    def _format_game_time(self, game_time: str) -> str:
        """Format game time for display."""
        try:
            timezone_str = self.config.get('timezone', 'UTC')
            try:
                tz = pytz.timezone(timezone_str)
            except pytz.exceptions.UnknownTimeZoneError:
                logger.warning(f"[NCAAMensBaseball] Unknown timezone: {timezone_str}, falling back to UTC")
                tz = pytz.UTC
            
            dt = datetime.fromisoformat(game_time.replace('Z', '+00:00'))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=pytz.UTC)
            local_dt = dt.astimezone(tz)
            
            return local_dt.strftime("%I:%M %p")
        except Exception as e:
            logger.error(f"[NCAAMensBaseball] Error formatting game time: {e}")
            return "TBD"

    def _fetch_ncaa_mens_baseball_api_data(self) -> Dict[str, Any]:
        """Fetch NCAA Mens Baseball game data from the ESPN API."""
        try:
            # Check if test mode is enabled
            if self.ncaa_mens_baseball_config.get('test_mode', False):
                self.logger.info("Using test mode data for NCAA Mens Baseball")
                return {
                    'test_game_ncaambb_1': {
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
                
                self.logger.info(f"[NCAAMensBaseball] Fetching games from ESPN API for date: {date}")
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
                        self.logger.warning(f"[NCAAMensBaseball] Could not find home or away team for event {game_id}")
                        continue

                    home_abbr = home_team['team'].get('abbreviation', 'N/A')
                    away_abbr = away_team['team'].get('abbreviation', 'N/A')
                    
                    is_favorite_game = (home_abbr in self.favorite_teams or away_abbr in self.favorite_teams)
                    
                    if is_favorite_game:
                        self.logger.info(f"[NCAAMensBaseball] Found favorite team game: {away_abbr} @ {home_abbr} (Status: {status}, State: {status_state})")
                        self.logger.debug(f"[NCAAMensBaseball] Full status data: {event['status']}")
                        self.logger.debug(f"[NCAAMensBaseball] Status type: {status}, State: {status_state}")
                        self.logger.debug(f"[NCAAMensBaseball] Status detail: {event['status'].get('detail', '')}")
                        self.logger.debug(f"[NCAAMensBaseball] Status shortDetail: {event['status'].get('shortDetail', '')}")
                    
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
                        
                        if is_favorite_game: self.logger.debug(f"[NCAAMensBaseball] Raw status detail: {event['status'].get('detail')}")
                        if is_favorite_game: self.logger.debug(f"[NCAAMensBaseball] Raw status short: {event['status'].get('shortDetail')}")
                        
                        inning_half = 'top'
                        if 'bottom' in status_detail or 'bot' in status_detail or 'bottom' in status_short or 'bot' in status_short:
                            inning_half = 'bottom'
                            if is_favorite_game: self.logger.debug("[NCAAMensBaseball] Detected bottom of inning")
                        elif 'top' in status_detail or 'mid' in status_detail or 'top' in status_short or 'mid' in status_short:
                            inning_half = 'top'
                            if is_favorite_game: self.logger.debug("[NCAAMensBaseball] Detected top of inning")
                        
                        if is_favorite_game: self.logger.debug(f"[NCAAMensBaseball] Determined inning: {inning_half} {inning}")
                        
                        situation = event['competitions'][0].get('situation', {})
                        if is_favorite_game: self.logger.debug(f"[NCAAMensBaseball] Full situation data: {situation}")
                        
                        # --- Simplified Count Logic --- 
                        # Primarily rely on the direct count fields first
                        count = situation.get('count', {})
                        balls = count.get('balls', 0)
                        strikes = count.get('strikes', 0)
                        outs = situation.get('outs', 0)
                        
                        # Basic logging
                        if is_favorite_game: 
                            self.logger.debug(f"[NCAAMensBaseball] Direct count: B={balls}, S={strikes}, O={outs}")
                        
                        # Keep base occupancy logic
                        bases_occupied = [
                            situation.get('onFirst', False),
                            situation.get('onSecond', False),
                            situation.get('onThird', False)
                        ]
                        if is_favorite_game: self.logger.debug(f"[NCAAMensBaseball] Bases occupied: {bases_occupied}")
                    
                    all_games[game_id] = {
                        'away_team': away_abbr,
                        'home_team': home_abbr,
                        'away_score': away_team.get('score', '0'),
                        'home_score': home_team.get('score', '0'),
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
                self.logger.info(f"[NCAAMensBaseball] Found {len(favorite_games)} games for favorite teams: {self.favorite_teams}")
                for game in favorite_games:
                    self.logger.info(f"[NCAAMensBaseball] Favorite team game: {game['away_team']} @ {game['home_team']} (Status: {game['status']}, State: {game['status_state']})")
            
            return all_games
            
        except Exception as e:
            self.logger.error(f"[NCAAMensBaseball] Error fetching NCAA Mens Baseball data from ESPN API: {e}", exc_info=True)
            return {}

class NCAAMensBaseballLiveManager(BaseNCAAMensBaseballManager):
    """Manager for displaying live NCAA Mens Baseball games."""
    def __init__(self, config: Dict[str, Any], display_manager):
        super().__init__(config, display_manager)
        self.logger.info("Initialized NCAA Mens Baseball Live Manager")
        self.live_games = []
        self.current_game = None
        self.current_game_index = 0
        self.last_update = 0
        self.update_interval = self.ncaa_mens_baseball_config.get('live_update_interval', 20)
        self.no_data_interval = 300
        self.last_game_switch = 0
        self.game_display_duration = self.ncaa_mens_baseball_config.get('live_game_duration', 30)
        self.last_display_update = 0
        self.last_log_time = 0
        self.log_interval = 300
        self.last_count_log_time = 0
        self.count_log_interval = 5
        self.test_mode = self.ncaa_mens_baseball_config.get('test_mode', False)

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
            self.logger.info("Initialized NCAAMensBaseballLiveManager with test game: LSU vs FLA")
        else:
            self.logger.info("Initialized NCAAMensBaseballLiveManager in live mode")

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
                games = self._fetch_ncaa_mens_baseball_api_data()
                if games:
                    new_live_games = []
                    for game in games.values():
                        if game['status_state'] == 'in':
                            if not self.favorite_teams or (game['home_team'] in self.favorite_teams or game['away_team'] in self.favorite_teams):
                                try:
                                    game['home_score'] = int(game['home_score'])
                                    game['away_score'] = int(game['away_score'])
                                    new_live_games.append(game)
                                except (ValueError, TypeError):
                                    self.logger.warning(f"[NCAAMensBaseball] Invalid score format for game {game['away_team']} @ {game['home_team']}")
                    
                    should_log = (
                        current_time - self.last_log_time >= self.log_interval or
                        len(new_live_games) != len(self.live_games) or
                        not self.live_games
                    )
                    
                    if should_log:
                        if new_live_games:
                            logger.info(f"[NCAAMensBaseball] Found {len(new_live_games)} live games")
                            for game in new_live_games:
                                logger.info(f"[NCAAMensBaseball] Live game: {game['away_team']} vs {game['home_team']} - {game['inning_half']}{game['inning']}, {game['balls']}-{game['strikes']}")
                        else:
                            logger.info("[NCAAMensBaseball] No live games found")
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
                # Force display update when switching games
                # self.display(force_clear=True) # REMOVED: DisplayController handles this
                self.last_display_update = current_time # Track last successful update that *would* have displayed

    def _create_live_game_display(self, game_data: Dict[str, Any]) -> Image.Image:
        """Create a display image for a live NCAA Mens Baseball game."""
        width = self.display_manager.matrix.width
        height = self.display_manager.matrix.height
        image = Image.new('RGB', (width, height), color=(0, 0, 0))
        draw = ImageDraw.Draw(image)
        
        new_logo_height = 32
        logo_size = (new_logo_height, new_logo_height)
        logo_y_offset = 0
        
        away_logo = self._get_team_logo(game_data['away_team'])
        home_logo = self._get_team_logo(game_data['home_team'])
        
        if away_logo and home_logo:
            away_logo = away_logo.resize(logo_size, Image.Resampling.LANCZOS)
            home_logo = home_logo.resize(logo_size, Image.Resampling.LANCZOS)
            away_x = 2
            away_y = logo_y_offset
            home_x = width - home_logo.width - 2
            home_y = logo_y_offset
            image.paste(away_logo, (away_x, away_y), away_logo)
            image.paste(home_logo, (home_x, home_y), home_logo)

        text_color = (255, 255, 255)
        
        inning_half_indicator = "▲" if game_data['inning_half'] == 'top' else "▼"
        inning_text = f"{inning_half_indicator}{game_data['inning']}"
        inning_bbox = draw.textbbox((0, 0), inning_text, font=self.display_manager.font)
        inning_width = inning_bbox[2] - inning_bbox[0]
        inning_x = (width - inning_width) // 2
        inning_y = 0
        draw.text((inning_x, inning_y), inning_text, fill=(255, 255, 255), font=self.display_manager.font)
        
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
            self.logger.debug(f"[NCAAMensBaseball] Displaying count: {balls}-{strikes}")
            self.logger.debug(f"[NCAAMensBaseball] Raw count data: balls={game_data.get('balls')}, strikes={game_data.get('strikes')}")
            self.last_count_log_time = current_time
        
        count_text = f"{balls}-{strikes}"
        bdf_font = self.display_manager.calendar_font
        bdf_font.set_char_size(height=7*64)
        count_text_width = self.display_manager.get_text_width(count_text, bdf_font)
        cluster_bottom_y = overall_start_y + base_cluster_height
        count_y = cluster_bottom_y + 2
        count_x = bases_origin_x + (base_cluster_width - count_text_width) // 2
        self.display_manager.draw = draw 
        self.display_manager._draw_bdf_text(count_text, count_x, count_y, text_color, font=bdf_font)

        score_font = self.display_manager.font; outline_color = (0, 0, 0); score_text_color = (255, 255, 255)
        def draw_bottom_outlined_text(x, y, text):
            draw.text((x-1, y), text, font=score_font, fill=outline_color); draw.text((x+1, y), text, font=score_font, fill=outline_color)
            draw.text((x, y-1), text, font=score_font, fill=outline_color); draw.text((x, y+1), text, font=score_font, fill=outline_color)
            draw.text((x, y), text, font=score_font, fill=score_text_color)
        away_abbr = game_data['away_team']; home_abbr = game_data['home_team']
        away_score_str = str(game_data['away_score']); home_score_str = str(game_data['home_score'])
        away_text = f"{away_abbr}:{away_score_str}"; home_text = f"{home_abbr}:{home_score_str}"
        try:
             font_height = score_font.getbbox("A")[3] - score_font.getbbox("A")[1]
        except AttributeError:
             font_height = 8
        score_y = height - font_height - 1
        away_score_x = 2
        draw_bottom_outlined_text(away_score_x, score_y, away_text)
        home_text_bbox = draw.textbbox((0,0), home_text, font=score_font)
        home_text_width = home_text_bbox[2] - home_text_bbox[0]
        home_score_x = width - home_text_width - 2
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
            logger.error(f"[NCAAMensBaseball] Error displaying live game: {e}", exc_info=True)

class NCAAMensBaseballRecentManager(BaseNCAAMensBaseballManager):
    """Manager for displaying recent NCAA Mens Baseball games."""
    def __init__(self, config: Dict[str, Any], display_manager):
        super().__init__(config, display_manager)
        self.logger.info("Initialized NCAA Mens Baseball Recent Manager")
        self.recent_games = []
        self.current_game = None
        self.current_game_index = 0
        self.last_update = 0
        self.update_interval = self.ncaa_mens_baseball_config.get('recent_update_interval', 3600)
        self.recent_hours = self.ncaa_mens_baseball_config.get('recent_game_hours', 72)
        self.last_game_switch = 0
        self.game_display_duration = 10
        self.last_warning_time = 0
        self.warning_cooldown = 300
        logger.info(f"Initialized NCAAMensBaseballRecentManager with {len(self.favorite_teams)} favorite teams")

    def update(self):
        """Update recent games data."""
        current_time = time.time()
        if current_time - self.last_update < self.update_interval:
            return
        try:
            games = self._fetch_ncaa_mens_baseball_api_data()
            if not games:
                logger.warning("[NCAAMensBaseball] No games returned from API")
                return
            
            new_recent_games = []
            now = datetime.now(timezone.utc)
            recent_cutoff = now - timedelta(hours=self.recent_hours)
            
            logger.info(f"[NCAAMensBaseball] Time window: {recent_cutoff} to {now}")
            
            for game_id, game in games.items():
                game_time_str = game['start_time'].replace('Z', '+00:00')
                game_time = datetime.fromisoformat(game_time_str)
                if game_time.tzinfo is None: game_time = game_time.replace(tzinfo=timezone.utc)
                
                is_favorite_game = (game['home_team'] in self.favorite_teams or game['away_team'] in self.favorite_teams)
                if not is_favorite_game: continue
                
                logger.info(f"[NCAAMensBaseball] Checking favorite recent game: {game['away_team']} @ {game['home_team']}")
                logger.info(f"[NCAAMensBaseball] Game time (UTC): {game_time}")
                logger.info(f"[NCAAMensBaseball] Game status: {game['status']}, State: {game['status_state']}")
                
                is_final = game['status_state'] in ['post', 'final', 'completed']
                is_within_time = recent_cutoff <= game_time <= now
                
                logger.info(f"[NCAAMensBaseball] Is final: {is_final}")
                logger.info(f"[NCAAMensBaseball] Is within time window: {is_within_time}")
                
                if is_final and is_within_time:
                    new_recent_games.append(game)
                    logger.info(f"[NCAAMensBaseball] Added favorite team game to recent list: {game['away_team']} @ {game['home_team']}")
            
            if new_recent_games:
                logger.info(f"[NCAAMensBaseball] Found {len(new_recent_games)} recent games for favorite teams: {self.favorite_teams}")
                self.recent_games = sorted(new_recent_games, key=lambda g: g.get('start_time'), reverse=True)
                if not self.current_game or self.current_game.get('id') not in [g.get('id') for g in self.recent_games]:
                    self.current_game_index = 0
                    self.current_game = self.recent_games[0] if self.recent_games else None
            else:
                logger.info("[NCAAMensBaseball] No recent games found for favorite teams")
                self.recent_games = []
                self.current_game = None
            
            self.last_update = current_time
        except Exception as e:
            logger.error(f"[NCAAMensBaseball] Error updating recent games: {e}", exc_info=True)

    def display(self, force_clear: bool = False):
        """Display recent games."""
        if not self.recent_games:
            current_time = time.time()
            if current_time - self.last_warning_time > self.warning_cooldown:
                logger.info("[NCAAMensBaseball] No recent games to display")
                self.last_warning_time = current_time
            return
        try:
            current_time = time.time()
            if current_time - self.last_game_switch >= self.game_display_duration:
                self.current_game_index = (self.current_game_index + 1) % len(self.recent_games)
                self.current_game = self.recent_games[self.current_game_index]
                self.last_game_switch = current_time
                force_clear = True
            
            if self.current_game:
                game_image = self._create_game_display(self.current_game)
                self.display_manager.image = game_image
                self.display_manager.draw = ImageDraw.Draw(self.display_manager.image)
                self.display_manager.update_display()
            else:
                 logger.warning("[NCAAMensBaseball] Current game is None, cannot display recent game.")

        except Exception as e:
            logger.error(f"[NCAAMensBaseball] Error displaying recent game: {e}", exc_info=True)

class NCAAMensBaseballUpcomingManager(BaseNCAAMensBaseballManager):
    """Manager for displaying upcoming NCAA Mens Baseball games."""
    def __init__(self, config: Dict[str, Any], display_manager):
        super().__init__(config, display_manager)
        self.logger.info("Initialized NCAA Mens Baseball Upcoming Manager")
        self.upcoming_games = []
        self.current_game = None
        self.current_game_index = 0
        self.last_update = 0
        self.update_interval = self.ncaa_mens_baseball_config.get('upcoming_update_interval', 3600)
        self.last_warning_time = 0
        self.warning_cooldown = 300
        self.last_game_switch = 0
        self.game_display_duration = 10
        logger.info(f"Initialized NCAAMensBaseballUpcomingManager with {len(self.favorite_teams)} favorite teams")

    def update(self):
        """Update upcoming games data."""
        current_time = time.time()
        if current_time - self.last_update < self.update_interval:
            return
        try:
            games = self._fetch_ncaa_mens_baseball_api_data()
            if games:
                new_upcoming_games = []
                now = datetime.now(timezone.utc)
                upcoming_cutoff = now + timedelta(hours=24)
                
                logger.info(f"[NCAAMensBaseball] Looking for games between {now} and {upcoming_cutoff}")
                
                for game in games.values():
                    is_favorite_game = (game['home_team'] in self.favorite_teams or game['away_team'] in self.favorite_teams)
                    if not is_favorite_game: continue
                        
                    game_time = datetime.fromisoformat(game['start_time'].replace('Z', '+00:00'))
                    if game_time.tzinfo is None: game_time = game_time.replace(tzinfo=timezone.utc)
                        
                    logger.info(f"[NCAAMensBaseball] Checking favorite upcoming game: {game['away_team']} @ {game['home_team']} at {game_time}")
                    logger.info(f"[NCAAMensBaseball] Game status: {game['status']}, State: {game['status_state']}")
                    
                    is_within_time = now <= game_time <= upcoming_cutoff
                    is_upcoming_state = game['status_state'] not in ['post', 'final', 'completed'] and game['status'] == 'status_scheduled'
                    
                    logger.info(f"[NCAAMensBaseball] Within time: {is_within_time}")
                    logger.info(f"[NCAAMensBaseball] Is upcoming state: {is_upcoming_state}")
                    
                    if is_within_time and is_upcoming_state:
                        new_upcoming_games.append(game)
                        logger.info(f"[NCAAMensBaseball] Added favorite team game to upcoming list: {game['away_team']} @ {game['home_team']}")
                
                if new_upcoming_games:
                    logger.info(f"[NCAAMensBaseball] Found {len(new_upcoming_games)} upcoming games for favorite teams")
                    self.upcoming_games = sorted(new_upcoming_games, key=lambda g: g.get('start_time'))
                    if not self.current_game or self.current_game.get('id') not in [g.get('id') for g in self.upcoming_games]:
                        self.current_game_index = 0
                        self.current_game = self.upcoming_games[0] if self.upcoming_games else None
                else:
                    logger.info("[NCAAMensBaseball] No upcoming games found for favorite teams")
                    self.upcoming_games = []
                    self.current_game = None
                
                self.last_update = current_time
        except Exception as e:
            logger.error(f"[NCAAMensBaseball] Error updating upcoming games: {e}", exc_info=True)

    def display(self, force_clear: bool = False):
        """Display upcoming games."""
        if not self.upcoming_games:
            current_time = time.time()
            if current_time - self.last_warning_time > self.warning_cooldown:
                logger.info("[NCAAMensBaseball] No upcoming games to display")
                self.last_warning_time = current_time
            return
        try:
            current_time = time.time()
            if current_time - self.last_game_switch >= self.game_display_duration:
                self.current_game_index = (self.current_game_index + 1) % len(self.upcoming_games)
                self.current_game = self.upcoming_games[self.current_game_index]
                self.last_game_switch = current_time
                force_clear = True
            
            if self.current_game:
                game_image = self._create_game_display(self.current_game)
                self.display_manager.image = game_image
                self.display_manager.draw = ImageDraw.Draw(self.display_manager.image)
                self.display_manager.update_display()
            else:
                 logger.warning("[NCAAMensBaseball] Current game is None, cannot display upcoming game.")

        except Exception as e:
            logger.error(f"[NCAAMensBaseball] Error displaying upcoming game: {e}", exc_info=True) 