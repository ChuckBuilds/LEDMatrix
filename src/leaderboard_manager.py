import time
import logging
import requests
from typing import Dict, Any, List, Optional
import os
import time
from PIL import Image, ImageDraw, ImageFont
try:
    from .display_manager import DisplayManager
    from .cache_manager import CacheManager
    from .logo_downloader import download_missing_logo
    from .background_data_service import get_background_service
except ImportError:
    # Fallback for direct imports
    from display_manager import DisplayManager
    from cache_manager import CacheManager
    from logo_downloader import download_missing_logo
    from background_data_service import get_background_service

# Import the API counter function from web interface
try:
    from web_interface_v2 import increment_api_counter
except ImportError:
    # Fallback if web interface is not available
    def increment_api_counter(kind: str, count: int = 1):
        pass

# Get logger
logger = logging.getLogger(__name__)

class LeaderboardManager:
    """Manager for displaying scrolling leaderboards for multiple sports leagues."""
    
    def __init__(self, config: Dict[str, Any], display_manager: DisplayManager):
        self.config = config
        self.display_manager = display_manager
        self.leaderboard_config = config.get('leaderboard', {})
        self.is_enabled = self.leaderboard_config.get('enabled', False)
        self.enabled_sports = self.leaderboard_config.get('enabled_sports', {})
        self.update_interval = self.leaderboard_config.get('update_interval', 3600)
        self.scroll_speed = self.leaderboard_config.get('scroll_speed', 1)
        self.scroll_delay = self.leaderboard_config.get('scroll_delay', 0.01)
        self.loop = self.leaderboard_config.get('loop', True)
        self.request_timeout = self.leaderboard_config.get('request_timeout', 30)
        self.time_over = 0
        
        # Duration settings - user can choose between fixed or dynamic (exception-based)
        self.dynamic_duration = self.leaderboard_config.get('dynamic_duration', True)
        # Get duration from main display_durations section
        self.display_duration = config.get('display', {}).get('display_durations', {}).get('leaderboard', 300)
        self.max_display_time = self.leaderboard_config.get('max_display_time', 600)  # 10 minutes maximum
        
        # Initialize managers
        self.cache_manager = CacheManager()
        # Store reference to config instead of creating new ConfigManager
        self.config = config
        
        # Initialize background data service
        background_config = self.leaderboard_config.get("background_service", {})
        if background_config.get("enabled", True):  # Default to enabled
            max_workers = background_config.get("max_workers", 3)
            self.background_service = get_background_service(self.cache_manager, max_workers)
            self.background_fetch_requests = {}  # Track background fetch requests
            self.background_enabled = True
            logger.info(f"[Leaderboard] Background service enabled with {max_workers} workers")
        else:
            self.background_service = None
            self.background_fetch_requests = {}
            self.background_enabled = False
            logger.info("[Leaderboard] Background service disabled")
        
        # State variables
        self.last_update = 0
        self.scroll_position = 0
        self.last_scroll_time = 0
        self.leaderboard_data = []
        self.current_sport_index = 0
        self.leaderboard_image = None  # This will hold the single, wide image
        self.last_display_time = 0
        
        # Font setup
        self.fonts = self._load_fonts()
        
        # League configurations with ESPN API endpoints
        self.league_configs = {
            'nfl': {
                'sport': 'football',
                'league': 'nfl',
                'logo_dir': 'assets/sports/nfl_logos',
                'league_logo': 'assets/sports/nfl_logos/nfl.png',
                'standings_url': 'https://site.api.espn.com/apis/v2/sports/football/nfl/standings',
                'enabled': self.enabled_sports.get('nfl', {}).get('enabled', False),
                'top_teams': self.enabled_sports.get('nfl', {}).get('top_teams', 10),
                'season': self.enabled_sports.get('nfl', {}).get('season', 2025),
                'level': self.enabled_sports.get('nfl', {}).get('level', 1),
                'sort': self.enabled_sports.get('nfl', {}).get('sort', 'winpercent:desc,gamesbehind:asc')
            },
            'nba': {
                'sport': 'basketball',
                'league': 'nba',
                'logo_dir': 'assets/sports/nba_logos',
                'league_logo': 'assets/sports/nba_logos/nba.png',
                'teams_url': 'https://site.api.espn.com/apis/site/v2/sports/basketball/nba/teams',
                'enabled': self.enabled_sports.get('nba', {}).get('enabled', False),
                'top_teams': self.enabled_sports.get('nba', {}).get('top_teams', 10)
            },
            'mlb': {
                'sport': 'baseball',
                'league': 'mlb',
                'logo_dir': 'assets/sports/mlb_logos',
                'league_logo': 'assets/sports/mlb_logos/mlb.png',
                'standings_url': 'https://site.api.espn.com/apis/v2/sports/baseball/mlb/standings',
                'enabled': self.enabled_sports.get('mlb', {}).get('enabled', False),
                'top_teams': self.enabled_sports.get('mlb', {}).get('top_teams', 10),
                'season': self.enabled_sports.get('mlb', {}).get('season', 2025),
                'level': self.enabled_sports.get('mlb', {}).get('level', 1),
                'sort': self.enabled_sports.get('mlb', {}).get('sort', 'winpercent:desc,gamesbehind:asc')
            },
            'ncaa_fb': {
                'sport': 'football',
                'league': 'college-football',
                'logo_dir': 'assets/sports/ncaa_logos',
                'league_logo': 'assets/sports/ncaa_logos/ncaa_fb.png',
                'teams_url': 'https://site.api.espn.com/apis/site/v2/sports/football/college-football/teams',
                'enabled': self.enabled_sports.get('ncaa_fb', {}).get('enabled', False),
                'top_teams': self.enabled_sports.get('ncaa_fb', {}).get('top_teams', 25),
                'show_ranking': self.enabled_sports.get('ncaa_fb', {}).get('show_ranking', True)
            },
            'nhl': {
                'sport': 'hockey',
                'league': 'nhl',
                'logo_dir': 'assets/sports/nhl_logos',
                'league_logo': 'assets/sports/nhl_logos/nhl.png',
                'standings_url': 'https://site.api.espn.com/apis/v2/sports/hockey/nhl/standings',
                'enabled': self.enabled_sports.get('nhl', {}).get('enabled', False),
                'top_teams': self.enabled_sports.get('nhl', {}).get('top_teams', 10),
                'season': self.enabled_sports.get('nhl', {}).get('season', 2025),
                'level': self.enabled_sports.get('nhl', {}).get('level', 1),
                'sort': self.enabled_sports.get('nhl', {}).get('sort', 'winpercent:desc,gamesbehind:asc')
            },
            'ncaam_basketball': {
                'sport': 'basketball',
                'league': 'mens-college-basketball',
                'logo_dir': 'assets/sports/ncaa_logos',
                'league_logo': 'assets/sports/ncaa_logos/ncaam.png',
                'teams_url': 'https://site.api.espn.com/apis/site/v2/sports/basketball/mens-college-basketball/teams',
                'enabled': self.enabled_sports.get('ncaam_basketball', {}).get('enabled', False),
                'top_teams': self.enabled_sports.get('ncaam_basketball', {}).get('top_teams', 25)
            },
            'ncaa_baseball': {
                'sport': 'baseball',
                'league': 'college-baseball',
                'logo_dir': 'assets/sports/ncaa_logos',
                'league_logo': 'assets/sports/ncaa_logos/ncaa_baseball.png',
                'standings_url': 'https://site.api.espn.com/apis/v2/sports/baseball/college-baseball/standings',
                'scoreboard_url': 'https://site.api.espn.com/apis/site/v2/sports/baseball/college-baseball/scoreboard',
                'enabled': self.enabled_sports.get('ncaa_baseball', {}).get('enabled', False),
                'top_teams': self.enabled_sports.get('ncaa_baseball', {}).get('top_teams', 25),
                'season': self.enabled_sports.get('ncaa_baseball', {}).get('season', 2025),
                'level': self.enabled_sports.get('ncaa_baseball', {}).get('level', 1),
                'sort': self.enabled_sports.get('ncaa_baseball', {}).get('sort', 'winpercent:desc,gamesbehind:asc')
            },
            'ncaam_hockey': {
                'sport': 'hockey',
                'league': 'mens-college-hockey',
                'logo_dir': 'assets/sports/ncaa_logos',
                'league_logo': 'assets/sports/ncaa_logos/ncaah.png',
                'teams_url': 'https://site.api.espn.com/apis/site/v2/sports/basketball/mens-college-hockey/teams',
                'enabled': self.enabled_sports.get('ncaam_hockey', {}).get('enabled', False),
                'top_teams': self.enabled_sports.get('ncaam_hockey', {}).get('top_teams', 25)
            },
        }
        
        logger.info(f"LeaderboardManager initialized with enabled sports: {[k for k, v in self.league_configs.items() if v['enabled']]}")
    
    def clear_leaderboard_cache(self) -> None:
        """Clear all leaderboard cache data to force fresh data fetch."""
        try:
            for league_key in self.league_configs.keys():
                # Clear all leaderboard cache variants
                cache_keys = [
                    f"leaderboard_{league_key}",
                    f"leaderboard_{league_key}_rankings", 
                    f"leaderboard_{league_key}_standings"
                ]
                
                for cache_key in cache_keys:
                    self.cache_manager.clear_cache(cache_key)
                    logger.info(f"Cleared cache for {cache_key}")
            
            # Also clear individual team record caches
            for league_key in self.league_configs.keys():
                league_config = self.league_configs[league_key]
                if league_config['enabled']:
                    # Get teams for this league to clear their individual caches
                    standings = self._fetch_standings(league_config)
                    for team in standings:
                        team_cache_key = f"team_record_{league_key}_{team['abbreviation']}"
                        self.cache_manager.clear_cache(team_cache_key)
            
            logger.info("Cleared all leaderboard cache data")
        except Exception as e:
            logger.error(f"Error clearing leaderboard cache: {e}")

    def _load_fonts(self) -> Dict[str, ImageFont.FreeTypeFont]:
        """Load fonts for the leaderboard display with pixel-perfect rendering."""
        fonts = {}
        try:
            # Try to load the Press Start 2P font first
            fonts['small'] = ImageFont.truetype("assets/fonts/PressStart2P-Regular.ttf", 6)
            fonts['medium'] = ImageFont.truetype("assets/fonts/PressStart2P-Regular.ttf", 10)
            fonts['large'] = ImageFont.truetype("assets/fonts/PressStart2P-Regular.ttf", 12)
            fonts['xlarge'] = ImageFont.truetype("assets/fonts/PressStart2P-Regular.ttf", 14)
            logger.info("[Leaderboard] Successfully loaded Press Start 2P font for all text elements")
        except IOError:
            logger.warning("[Leaderboard] Press Start 2P font not found, trying 4x6 font for pixel-perfect rendering.")
            try:
                # Try to load the 4x6 font as a fallback for pixel-perfect rendering
                fonts['small'] = ImageFont.truetype("assets/fonts/4x6-font.ttf", 6)
                fonts['medium'] = ImageFont.truetype("assets/fonts/4x6-font.ttf", 8)
                fonts['large'] = ImageFont.truetype("assets/fonts/4x6-font.ttf", 10)
                fonts['xlarge'] = ImageFont.truetype("assets/fonts/4x6-font.ttf", 12)
                logger.info("[Leaderboard] Successfully loaded 4x6 font for pixel-perfect rendering")
            except IOError:
                logger.warning("[Leaderboard] 4x6 font not found, using default PIL font.")
                # Use default PIL font as a last resort
                fonts['small'] = ImageFont.load_default()
                fonts['medium'] = ImageFont.load_default()
                fonts['large'] = ImageFont.load_default()
                fonts['xlarge'] = ImageFont.load_default()
        except Exception as e:
            logger.error(f"Error loading fonts: {e}")
            fonts = {
                'small': ImageFont.load_default(),
                'medium': ImageFont.load_default(),
                'large': ImageFont.load_default(),
                'xlarge': ImageFont.load_default()
            }
        return fonts

    def _draw_text_with_outline(self, draw, text, position, font, fill=(255, 255, 255), outline_color=(0, 0, 0)):
        """Draw text with a black outline for better readability on LED matrix."""
        x, y = position
        # Draw outline
        for dx, dy in [(-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1)]:
            draw.text((x + dx, y + dy), text, font=font, fill=outline_color)
        # Draw text
        draw.text((x, y), text, font=font, fill=fill)

    def _get_team_logo(self, team_abbr: str, logo_dir: str, league: str = None, team_name: str = None) -> Optional[Image.Image]:
        """Get team logo from the configured directory, downloading if missing."""
        if not team_abbr or not logo_dir:
            logger.debug("Cannot get team logo with missing team_abbr or logo_dir")
            return None
        try:
            logo_path = os.path.join(logo_dir, f"{team_abbr}.png")
            logger.debug(f"Attempting to load logo from path: {logo_path}")
            if os.path.exists(logo_path):
                logo = Image.open(logo_path)
                logger.debug(f"Successfully loaded logo for {team_abbr} from {logo_path}")
                return logo
            else:
                logger.warning(f"Logo not found at path: {logo_path}")
                
                # Try to download the missing logo if we have league information
                if league:
                    logger.info(f"Attempting to download missing logo for {team_abbr} in league {league}")
                    success = download_missing_logo(team_abbr, league, team_name)
                    if success:
                        # Try to load the downloaded logo
                        if os.path.exists(logo_path):
                            logo = Image.open(logo_path)
                            logger.info(f"Successfully downloaded and loaded logo for {team_abbr}")
                            return logo
                
                return None
        except Exception as e:
            logger.error(f"Error loading logo for {team_abbr} from {logo_dir}: {e}")
            return None

    def _get_league_logo(self, league_logo_path: str) -> Optional[Image.Image]:
        """Get league logo from the configured path."""
        if not league_logo_path:
            return None
        try:
            if os.path.exists(league_logo_path):
                logo = Image.open(league_logo_path)
                logger.debug(f"Successfully loaded league logo from {league_logo_path}")
                return logo
            else:
                logger.warning(f"League logo not found at path: {league_logo_path}")
                return None
        except Exception as e:
            logger.error(f"Error loading league logo from {league_logo_path}: {e}")
            return None

    def _fetch_standings(self, league_config: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Fetch standings for a specific league from ESPN API with caching."""
        league_key = league_config['league']
        cache_key = f"leaderboard_{league_key}"
        
        # Try to get cached data first
        cached_data = self.cache_manager.get_cached_data_with_strategy(cache_key, 'leaderboard')
        if cached_data:
            logger.info(f"Using cached leaderboard data for {league_key}")
            return cached_data.get('standings', [])
        
        # Special handling for college football - use rankings endpoint
        if league_key == 'college-football':
            return self._fetch_ncaa_fb_rankings(league_config)
        
        if league_key == 'mens-college-hockey':
            return self._fetch_ncaam_hockey_rankings(league_config)
        
        # Use standings endpoint for NFL, MLB, NHL, and NCAA Baseball
        if league_key in ['nfl', 'mlb', 'nhl', 'college-baseball']:
            return self._fetch_standings_data(league_config)
        
        try:
            logger.info(f"Fetching fresh leaderboard data for {league_key}")
            
            # First, get all teams for the league
            teams_url = league_config['teams_url']
            response = requests.get(teams_url, timeout=self.request_timeout)
            response.raise_for_status()
            data = response.json()
            
            # Increment API counter for sports data
            increment_api_counter('sports', 1)
            
            standings = []
            sports = data.get('sports', [])
            
            if not sports:
                logger.warning(f"No sports data found for {league_config['league']}")
                return []
            
            leagues = sports[0].get('leagues', [])
            if not leagues:
                logger.warning(f"No leagues data found for {league_config['league']}")
                return []
            
            teams = leagues[0].get('teams', [])
            if not teams:
                logger.warning(f"No teams data found for {league_config['league']}")
                return []
            
            logger.info(f"Found {len(teams)} teams for {league_config['league']}")
            
            # For each team, fetch their individual record
            for team_data in teams:
                team = team_data.get('team', {})
                team_abbr = team.get('abbreviation')
                team_name = team.get('name', 'Unknown')
                
                if not team_abbr:
                    logger.warning(f"No abbreviation found for team {team_name}")
                    continue
                
                # Fetch individual team record
                team_record = self._fetch_team_record(team_abbr, league_config)
                
                if team_record:
                    standings.append({
                        'name': team_name,
                        'abbreviation': team_abbr,
                        'wins': team_record.get('wins', 0),
                        'losses': team_record.get('losses', 0),
                        'ties': team_record.get('ties', 0),
                        'win_percentage': team_record.get('win_percentage', 0)
                    })
            
            # Sort by win percentage (descending) and limit to top teams
            standings.sort(key=lambda x: x['win_percentage'], reverse=True)
            top_teams = standings[:league_config['top_teams']]
            
            # Cache the results
            cache_data = {
                'standings': top_teams,
                'timestamp': time.time(),
                'league': league_key
            }
            self.cache_manager.save_cache(cache_key, cache_data)
            
            logger.info(f"Fetched and cached {len(top_teams)} teams for {league_config['league']}")
            return top_teams
            
        except Exception as e:
            logger.error(f"Error fetching standings for {league_config['league']}: {e}")
            return []

    def _fetch_ncaa_fb_rankings(self, league_config: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Fetch NCAA Football rankings from ESPN API using the rankings endpoint."""
        league_key = league_config['league']
        cache_key = f"leaderboard_{league_key}_rankings"
        
        # Try to get cached data first
        cached_data = self.cache_manager.get_cached_data_with_strategy(cache_key, 'leaderboard')
        if cached_data:
            logger.info(f"Using cached rankings data for {league_key}")
            return cached_data.get('standings', [])
        
        try:
            logger.info(f"Fetching fresh rankings data for {league_key}")
            rankings_url = "https://site.api.espn.com/apis/site/v2/sports/football/college-football/rankings"
            
            # Get rankings data
            response = requests.get(rankings_url, timeout=self.request_timeout)
            response.raise_for_status()
            data = response.json()
            
            # Increment API counter for sports data
            increment_api_counter('sports', 1)
            
            logger.info(f"Available rankings: {[rank['name'] for rank in data.get('availableRankings', [])]}")
            logger.info(f"Latest season: {data.get('latestSeason', {})}")
            logger.info(f"Latest week: {data.get('latestWeek', {})}")
            
            rankings_data = data.get('rankings', [])
            if not rankings_data:
                logger.warning("No rankings data found")
                return []
            
            # Use the first ranking (usually AP Top 25)
            first_ranking = rankings_data[0]
            ranking_name = first_ranking.get('name', 'Unknown')
            ranking_type = first_ranking.get('type', 'Unknown')
            teams = first_ranking.get('ranks', [])
            
            logger.info(f"Using ranking: {ranking_name} ({ranking_type})")
            logger.info(f"Found {len(teams)} teams in ranking")
            
            standings = []
            
            # Process each team in the ranking
            for team_data in teams:
                team_info = team_data.get('team', {})
                team_name = team_info.get('name', 'Unknown')
                team_abbr = team_info.get('abbreviation', 'Unknown')
                current_rank = team_data.get('current', 0)
                record_summary = team_data.get('recordSummary', '0-0')
                
                logger.debug(f"  {current_rank}. {team_name} ({team_abbr}): {record_summary}")
                
                # Parse the record string (e.g., "12-1", "8-4", "10-2-1")
                wins = 0
                losses = 0
                ties = 0
                win_percentage = 0
                
                try:
                    parts = record_summary.split('-')
                    if len(parts) >= 2:
                        wins = int(parts[0])
                        losses = int(parts[1])
                        if len(parts) == 3:
                            ties = int(parts[2])
                        
                        # Calculate win percentage
                        total_games = wins + losses + ties
                        win_percentage = wins / total_games if total_games > 0 else 0
                except (ValueError, IndexError):
                    logger.warning(f"Could not parse record for {team_name}: {record_summary}")
                    continue
                
                standings.append({
                    'name': team_name,
                    'abbreviation': team_abbr,
                    'rank': current_rank,
                    'wins': wins,
                    'losses': losses,
                    'ties': ties,
                    'win_percentage': win_percentage,
                    'record_summary': record_summary,
                    'ranking_name': ranking_name
                })
            
            # Limit to top teams (they're already ranked)
            top_teams = standings[:league_config['top_teams']]
            
            # Cache the results
            cache_data = {
                'standings': top_teams,
                'timestamp': time.time(),
                'league': league_key,
                'ranking_name': ranking_name
            }
            self.cache_manager.save_cache(cache_key, cache_data)
            
            logger.info(f"Fetched and cached {len(top_teams)} teams for {league_key} using {ranking_name}")
            return top_teams
            
        except Exception as e:
            logger.error(f"Error fetching rankings for {league_key}: {e}")
            return []

    def _fetch_ncaam_hockey_rankings(self, league_config: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Fetch NCAA Hockey rankings from ESPN API using the rankings endpoint."""
        league_key = league_config['league']
        cache_key = f"leaderboard_{league_key}_rankings"
        
        # Try to get cached data first
        cached_data = self.cache_manager.get_cached_data_with_strategy(cache_key, 'leaderboard')
        if cached_data:
            logger.info(f"Using cached rankings data for {league_key}")
            return cached_data.get('standings', [])
        
        try:
            logger.info(f"Fetching fresh rankings data for {league_key}")
            rankings_url = "https://site.api.espn.com/apis/site/v2/sports/hockey/mens-college-hockey/rankings"
            
            # Get rankings data
            response = requests.get(rankings_url, timeout=self.request_timeout)
            response.raise_for_status()
            data = response.json()
            
            # Increment API counter for sports data
            increment_api_counter('sports', 1)
            
            logger.info(f"Available rankings: {[rank['name'] for rank in data.get('availableRankings', [])]}")
            logger.info(f"Latest season: {data.get('latestSeason', {})}")
            logger.info(f"Latest week: {data.get('latestWeek', {})}")
            
            rankings_data = data.get('rankings', [])
            if not rankings_data:
                logger.warning("No rankings data found")
                return []
            
            # Use the first ranking (usually AP Top 25)
            first_ranking = rankings_data[0]
            ranking_name = first_ranking.get('name', 'Unknown')
            ranking_type = first_ranking.get('type', 'Unknown')
            teams = first_ranking.get('ranks', [])
            
            logger.info(f"Using ranking: {ranking_name} ({ranking_type})")
            logger.info(f"Found {len(teams)} teams in ranking")
            
            standings = []
            
            # Process each team in the ranking
            for team_data in teams:
                team_info = team_data.get('team', {})
                team_name = team_info.get('name', 'Unknown')
                team_abbr = team_info.get('abbreviation', 'Unknown')
                current_rank = team_data.get('current', 0)
                record_summary = team_data.get('recordSummary', '0-0')
                
                logger.debug(f"  {current_rank}. {team_name} ({team_abbr}): {record_summary}")
                
                # Parse the record string (e.g., "12-1", "8-4", "10-2-1")
                wins = 0
                losses = 0
                ties = 0
                win_percentage = 0
                
                try:
                    parts = record_summary.split('-')
                    if len(parts) >= 2:
                        wins = int(parts[0])
                        losses = int(parts[1])
                        if len(parts) == 3:
                            ties = int(parts[2])
                        
                        # Calculate win percentage
                        total_games = wins + losses + ties
                        win_percentage = wins / total_games if total_games > 0 else 0
                except (ValueError, IndexError):
                    logger.warning(f"Could not parse record for {team_name}: {record_summary}")
                    continue
                
                standings.append({
                    'name': team_name,
                    'abbreviation': team_abbr,
                    'rank': current_rank,
                    'wins': wins,
                    'losses': losses,
                    'ties': ties,
                    'win_percentage': win_percentage,
                    'record_summary': record_summary,
                    'ranking_name': ranking_name
                })
            
            # Limit to top teams (they're already ranked)
            top_teams = standings[:league_config['top_teams']]
            
            # Cache the results
            cache_data = {
                'standings': top_teams,
                'timestamp': time.time(),
                'league': league_key,
                'ranking_name': ranking_name
            }
            self.cache_manager.save_cache(cache_key, cache_data)
            
            logger.info(f"Fetched and cached {len(top_teams)} teams for {league_key} using {ranking_name}")
            return top_teams
            
        except Exception as e:
            logger.error(f"Error fetching rankings for {league_key}: {e}")
            return []
        
    def _fetch_standings_data(self, league_config: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Fetch standings data from ESPN API using the standings endpoint."""
        league_key = league_config['league']
        cache_key = f"leaderboard_{league_key}_standings"
        
        # Try to get cached data first
        cached_data = self.cache_manager.get_cached_data_with_strategy(cache_key, 'leaderboard')
        if cached_data:
            logger.info(f"Using cached standings data for {league_key}")
            return cached_data.get('standings', [])
        
        try:
            logger.info(f"Fetching fresh standings data for {league_key}")
            
            # Build the standings URL with query parameters
            standings_url = league_config['standings_url']
            params = {
                'season': league_config.get('season', 2025),
                'level': league_config.get('level', 1),
                'sort': league_config.get('sort', 'winpercent:desc,gamesbehind:asc')
            }
            
            logger.info(f"Fetching standings from: {standings_url} with params: {params}")
            
            response = requests.get(standings_url, params=params, timeout=self.request_timeout)
            response.raise_for_status()
            data = response.json()
            
            # Increment API counter for sports data
            increment_api_counter('sports', 1)
            
            standings = []
            
            # Parse the standings data structure
            # Check if we have direct standings data or children (divisions/conferences)
            if 'standings' in data and 'entries' in data['standings']:
                # Direct standings data (e.g., NFL overall standings)
                standings_data = data['standings']['entries']
                logger.info(f"Processing direct standings data with {len(standings_data)} teams")
                
                for entry in standings_data:
                    team_data = entry.get('team', {})
                    stats = entry.get('stats', [])
                    
                    team_name = team_data.get('displayName', 'Unknown')
                    team_abbr = team_data.get('abbreviation', 'Unknown')
                    
                    # Extract record from stats
                    wins = 0
                    losses = 0
                    ties = 0
                    win_percentage = 0.0
                    
                    # First pass: collect all stat values
                    games_played = 0
                    for stat in stats:
                        stat_type = stat.get('type', '')
                        stat_value = stat.get('value', 0)
                        
                        if stat_type == 'wins':
                            wins = int(stat_value)
                        elif stat_type == 'losses':
                            losses = int(stat_value)
                        elif stat_type == 'ties':
                            ties = int(stat_value)
                        elif stat_type == 'winpercent':
                            win_percentage = float(stat_value)
                        # NHL specific stats
                        elif stat_type == 'overtimelosses' and league_key == 'nhl':
                            ties = int(stat_value)  # NHL uses overtime losses as ties
                        elif stat_type == 'gamesplayed' and league_key == 'nhl':
                            games_played = float(stat_value)
                    
                    # Second pass: calculate win percentage for NHL if not already set
                    if league_key == 'nhl' and win_percentage == 0.0 and games_played > 0:
                        win_percentage = wins / games_played
                    
                    # Create record summary
                    if ties > 0:
                        record_summary = f"{wins}-{losses}-{ties}"
                    else:
                        record_summary = f"{wins}-{losses}"
                    
                    standings.append({
                        'name': team_name,
                        'abbreviation': team_abbr,
                        'wins': wins,
                        'losses': losses,
                        'ties': ties,
                        'win_percentage': win_percentage,
                        'record_summary': record_summary,
                        'division': 'Overall'
                    })
            
            elif 'children' in data:
                # Children structure (divisions/conferences)
                children = data.get('children', [])
                logger.info(f"Processing {len(children)} divisions/conferences")
                
                for child in children:
                    child_name = child.get('displayName', 'Unknown')
                    logger.info(f"Processing {child_name}")
                    
                    standings_data = child.get('standings', {}).get('entries', [])
                    
                    for entry in standings_data:
                        team_data = entry.get('team', {})
                        stats = entry.get('stats', [])
                        
                        team_name = team_data.get('displayName', 'Unknown')
                        team_abbr = team_data.get('abbreviation', 'Unknown')
                        
                        # Extract record from stats
                        wins = 0
                        losses = 0
                        ties = 0
                        win_percentage = 0.0
                        
                        # First pass: collect all stat values
                        games_played = 0
                        for stat in stats:
                            stat_type = stat.get('type', '')
                            stat_value = stat.get('value', 0)
                            
                            if stat_type == 'wins':
                                wins = int(stat_value)
                            elif stat_type == 'losses':
                                losses = int(stat_value)
                            elif stat_type == 'ties':
                                ties = int(stat_value)
                            elif stat_type == 'winpercent':
                                win_percentage = float(stat_value)
                            # NHL specific stats
                            elif stat_type == 'overtimelosses' and league_key == 'nhl':
                                ties = int(stat_value)  # NHL uses overtime losses as ties
                            elif stat_type == 'gamesplayed' and league_key == 'nhl':
                                games_played = float(stat_value)
                        
                        # Second pass: calculate win percentage for NHL if not already set
                        if league_key == 'nhl' and win_percentage == 0.0 and games_played > 0:
                            win_percentage = wins / games_played
                        
                        # Create record summary
                        if ties > 0:
                            record_summary = f"{wins}-{losses}-{ties}"
                        else:
                            record_summary = f"{wins}-{losses}"
                        
                        standings.append({
                            'name': team_name,
                            'abbreviation': team_abbr,
                            'wins': wins,
                            'losses': losses,
                            'ties': ties,
                            'win_percentage': win_percentage,
                            'record_summary': record_summary,
                            'division': child_name
                        })
            else:
                logger.warning(f"No standings or children data found for {league_key}")
                return []
            
            # Sort by win percentage (descending) and limit to top teams
            standings.sort(key=lambda x: x['win_percentage'], reverse=True)
            top_teams = standings[:league_config['top_teams']]
            
            # Cache the results
            cache_data = {
                'standings': top_teams,
                'timestamp': time.time(),
                'league': league_key,
                'season': params['season'],
                'level': params['level']
            }
            self.cache_manager.save_cache(cache_key, cache_data)
            
            logger.info(f"Fetched and cached {len(top_teams)} teams for {league_key} standings")
            return top_teams
            
        except Exception as e:
            logger.error(f"Error fetching standings for {league_key}: {e}")
            return []

    def _fetch_team_record(self, team_abbr: str, league_config: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Fetch individual team record from ESPN API with caching."""
        league = league_config['league']
        cache_key = f"team_record_{league}_{team_abbr}"
        
        # Try to get cached data first
        cached_data = self.cache_manager.get_cached_data_with_strategy(cache_key, 'leaderboard')
        if cached_data:
            return cached_data.get('record')
        
        try:
            sport = league_config['sport']
            
            # Use a more specific endpoint for college sports
            if league == 'college-football':
                url = f"https://site.api.espn.com/apis/site/v2/sports/football/college-football/teams/{team_abbr}"
            else:
                url = f"https://site.api.espn.com/apis/site/v2/sports/{sport}/{league}/teams/{team_abbr}"

            response = requests.get(url, timeout=self.request_timeout)
            response.raise_for_status()
            data = response.json()
            
            # Increment API counter for sports data
            increment_api_counter('sports', 1)
            
            team_data = data.get('team', {})
            stats = team_data.get('stats', [])
            
            # Find wins and losses
            wins = 0
            losses = 0
            ties = 0
            
            for stat in stats:
                if stat.get('name') == 'wins':
                    wins = stat.get('value', 0)
                elif stat.get('name') == 'losses':
                    losses = stat.get('value', 0)
                elif stat.get('name') == 'ties':
                    ties = stat.get('value', 0)
            
            # Calculate win percentage
            total_games = wins + losses + ties
            win_percentage = wins / total_games if total_games > 0 else 0
            
            record = {
                'wins': wins,
                'losses': losses,
                'ties': ties,
                'win_percentage': win_percentage
            }
            
            # Cache the team record
            cache_data = {
                'record': record,
                'timestamp': time.time(),
                'team': team_abbr,
                'league': league
            }
            self.cache_manager.save_cache(cache_key, cache_data)
            
            return record
            
        except Exception as e:
            logger.error(f"Error fetching record for {team_abbr} in league {league_config['league']}: {e}")
            return None

    def _fetch_all_standings(self) -> List[Dict[str, Any]]:
        """Fetch standings for all enabled leagues."""
        all_standings = []
        
        for league_key, league_config in self.league_configs.items():
            if not league_config['enabled']:
                continue
                
            logger.debug(f"Fetching standings for {league_key}")
            standings = self._fetch_standings(league_config)
            
            if standings:
                all_standings.append({
                    'league': league_key,
                    'league_config': league_config,
                    'teams': standings
                })
        
        return all_standings

    def _create_leaderboard_image(self) -> None:
        """Create the scrolling leaderboard image."""
        if not self.leaderboard_data:
            logger.warning("No leaderboard data available")
            return
        
        try:
            # Get display height first
            height = self.display_manager.matrix.height
            
            # Calculate total width needed
            total_width = 0
            spacing = 40  # Spacing between leagues
            
            # Calculate width for each league section
            for league_data in self.leaderboard_data:
                league_key = league_data['league']
                league_config = league_data['league_config']
                teams = league_data['teams']
                
                # Width for league logo section
                league_logo_width = 64  # Fixed width for league logo section
                
                # Calculate total width for all teams in horizontal layout
                teams_width = 0
                # Calculate dynamic logo size (match drawing logic: 120% of display height)
                logo_size = int(height * 1.2)
                
                for i, team in enumerate(teams):
                    # Calculate width for bold number/ranking/record (match drawing logic)
                    if league_key == 'ncaa_fb':
                        if league_config.get('show_ranking', True):
                            # Show ranking number if available
                            if 'rank' in team and team['rank'] > 0:
                                number_text = f"#{team['rank']}"
                            else:
                                # Team is unranked - show position number as fallback
                                number_text = f"{i+1}."
                        else:
                            # Show record instead of ranking
                            if 'record_summary' in team:
                                number_text = team['record_summary']
                            else:
                                number_text = f"{i+1}."
                    else:
                        # For other leagues, show position
                        number_text = f"{i+1}."
                    
                    number_bbox = self.fonts['xlarge'].getbbox(number_text)
                    number_width = number_bbox[2] - number_bbox[0]
                    
                    # Calculate width for team abbreviation (use large font like in drawing)
                    team_text = team['abbreviation']
                    text_bbox = self.fonts['large'].getbbox(team_text)
                    text_width = text_bbox[2] - text_bbox[0]
                    
                    # Total team width: bold number + spacing + logo + spacing + text + spacing
                    team_width = number_width + 4 + logo_size + 4 + text_width + 12  # Spacing between teams
                    teams_width += team_width
                
                # Total league width: logo width + teams width + spacing (match drawing logic)
                league_width = league_logo_width + teams_width + 20
                total_width += league_width + spacing
            
            # Create the main image
            self.leaderboard_image = Image.new('RGB', (total_width, height), (0, 0, 0))
            draw = ImageDraw.Draw(self.leaderboard_image)
            
            current_x = 0
            
            for league_idx, league_data in enumerate(self.leaderboard_data):
                league_key = league_data['league']
                league_config = league_data['league_config']
                teams = league_data['teams']
                
                logger.info(f"Drawing League {league_idx+1} ({league_key}) starting at x={current_x}px")
                
                # Draw league logo section (full height)
                league_logo = self._get_league_logo(league_config['league_logo'])
                if league_logo:
                    # Resize league logo to full height
                    logo_height = height - 4  # Leave small margin
                    logo_width = int(logo_height * league_logo.width / league_logo.height)
                    
                    # Center the logo horizontally in its section
                    logo_x = current_x + (64 - logo_width) // 2
                    logo_y = 2
                    
                    league_logo = league_logo.resize((logo_width, logo_height), Image.Resampling.LANCZOS)
                    self.leaderboard_image.paste(league_logo, (logo_x, logo_y), league_logo if league_logo.mode == 'RGBA' else None)
                    
                    # League name removed - only show league logo
                else:
                    # No league logo available - skip league name display
                    pass
                
                # Move to team section
                current_x += 64 + 10  # League logo width + spacing
                
                # Draw team standings horizontally in a single line
                team_x = current_x
                # Use the same dynamic logo size as Odds Manager ticker
                logo_size = int(height * 1.2)
                
                for i, team in enumerate(teams):
                    # Draw bold team number/ranking/record (centered vertically)
                    if league_key == 'ncaa_fb':
                        if league_config.get('show_ranking', True):
                            # Show ranking number if available
                            if 'rank' in team and team['rank'] > 0:
                                number_text = f"#{team['rank']}"
                            else:
                                # Team is unranked - show position number as fallback
                                number_text = f"{i+1}."
                        else:
                            # Show record instead of ranking
                            if 'record_summary' in team:
                                number_text = team['record_summary']
                            else:
                                number_text = f"{i+1}."
                    else:
                        # For other leagues, show position
                        number_text = f"{i+1}."
                    
                    number_bbox = self.fonts['xlarge'].getbbox(number_text)
                    number_width = number_bbox[2] - number_bbox[0]
                    number_height = number_bbox[3] - number_bbox[1]
                    number_y = (height - number_height) // 2
                    self._draw_text_with_outline(draw, number_text, (team_x, number_y), self.fonts['xlarge'], fill=(255, 255, 0))
                    
                    # Draw team logo (95% of display height, centered vertically)
                    team_logo = self._get_team_logo(team['abbreviation'], league_config['logo_dir'], 
                                                   league=league_key, team_name=team.get('name'))
                    if team_logo:
                        # Resize team logo to dynamic size (95% of display height)
                        team_logo = team_logo.resize((logo_size, logo_size), Image.Resampling.LANCZOS)
                        
                        # Paste team logo after the bold number (centered vertically)
                        logo_x = team_x + number_width + 4
                        logo_y_pos = (height - logo_size) // 2
                        self.leaderboard_image.paste(team_logo, (logo_x, logo_y_pos), team_logo if team_logo.mode == 'RGBA' else None)
                        
                        # Draw team abbreviation after the logo (centered vertically)
                        team_text = team['abbreviation']
                        text_bbox = self.fonts['large'].getbbox(team_text)
                        text_width = text_bbox[2] - text_bbox[0]
                        text_height = text_bbox[3] - text_bbox[1]
                        text_x = logo_x + logo_size + 4
                        text_y = (height - text_height) // 2
                        self._draw_text_with_outline(draw, team_text, (text_x, text_y), self.fonts['large'], fill=(255, 255, 255))
                        
                        # Calculate total width used by this team
                        team_width = number_width + 4 + logo_size + 4 + text_width + 12  # 12px spacing to next team
                    else:
                        # Fallback if no logo - draw team abbreviation after bold number (centered vertically)
                        team_text = team['abbreviation']
                        text_bbox = self.fonts['large'].getbbox(team_text)
                        text_width = text_bbox[2] - text_bbox[0]
                        text_height = text_bbox[3] - text_bbox[1]
                        text_x = team_x + number_width + 4
                        text_y = (height - text_height) // 2
                        self._draw_text_with_outline(draw, team_text, (text_x, text_y), self.fonts['large'], fill=(255, 255, 255))
                        
                        # Calculate total width used by this team
                        team_width = number_width + 4 + text_width + 12  # 12px spacing to next team
                    
                    # Move to next team position
                    team_x += team_width
                
                # Move to next league section (match width calculation logic)
                # Update current_x to where team drawing actually ended
                logger.info(f"League {league_idx+1} ({league_key}) teams ended at x={team_x}px")
                current_x = team_x + 20 + spacing  # team_x is at end of teams, add internal spacing + inter-league spacing
                logger.info(f"Next league will start at x={current_x}px (gap: {20 + spacing}px)")
            
            # Set total scroll width for dynamic duration calculation
            # Use actual content width (current_x at end) instead of pre-calculated total_width
            actual_content_width = current_x - (20 + spacing)  # Remove the final spacing that won't be used
            self.total_scroll_width = actual_content_width
            logger.info(f"Content width - Calculated: {total_width}px, Actual: {actual_content_width}px")
            
            # Log league positioning for debugging and verify layout
            debug_x = 0
            for i, league_data in enumerate(self.leaderboard_data):
                league_key = league_data['league']
                league_config = league_data['league_config']
                teams = league_data['teams']
                
                # Calculate actual widths used in drawing
                league_logo_width = 64
                teams_width = 0
                logo_size = int(height * 1.2)
                
                for j, team in enumerate(teams):
                    # Calculate width for bold number/ranking/record (match drawing logic)
                    if league_key == 'ncaa_fb':
                        if league_config.get('show_ranking', True):
                            if 'rank' in team and team['rank'] > 0:
                                number_text = f"#{team['rank']}"
                            else:
                                number_text = f"{j+1}."
                        else:
                            if 'record_summary' in team:
                                number_text = team['record_summary']
                            else:
                                number_text = f"{j+1}."
                    else:
                        number_text = f"{j+1}."
                    
                    number_bbox = self.fonts['xlarge'].getbbox(number_text)
                    number_width = number_bbox[2] - number_bbox[0]
                    team_text = team['abbreviation']
                    text_bbox = self.fonts['large'].getbbox(team_text)
                    text_width = text_bbox[2] - text_bbox[0]
                    team_width = number_width + 4 + logo_size + 4 + text_width + 12
                    teams_width += team_width
                
                # Calculate where this league should start and end
                league_start_x = debug_x
                league_content_width = league_logo_width + 10 + teams_width + 20  # Logo + spacing + teams + internal spacing
                league_end_x = league_start_x + league_content_width
                
                logger.info(f"League {i+1} ({league_key}): {len(teams)} teams")
                logger.info(f"  Start: {league_start_x}px, Content: {league_content_width}px, End: {league_end_x}px")
                
                # Move to next league start position
                if i < len(self.leaderboard_data) - 1:  # Not the last league
                    debug_x = league_end_x + spacing  # Add inter-league spacing
                    logger.info(f"  Next league starts at: {debug_x}px (gap: {spacing}px)")
                else:
                    logger.info(f"  Final league ends at: {league_end_x}px")
            
            logger.info(f"Total image width: {total_width}px, Display width: {height}px")
            
            logger.info(f"Created leaderboard image with width {total_width}")
            
        except Exception as e:
            logger.error(f"Error creating leaderboard image: {e}")
            self.leaderboard_image = None

    def get_duration(self) -> int:
        """Get the duration for display based on user preference"""
        if self.dynamic_duration:
            # Use long timeout and let content determine when done via StopIteration
            return self.max_display_time
        else:
            # Use fixed duration from config
            return self.display_duration

    def update(self) -> None:
        """Update leaderboard data."""
        current_time = time.time()
        
        if current_time - self.last_update < self.update_interval:
            return
        
        logger.info("Updating leaderboard data")
        
        try:
            self.leaderboard_data = self._fetch_all_standings()
            self.last_update = current_time
            
            if self.leaderboard_data:
                self._create_leaderboard_image()
            else:
                logger.warning("No leaderboard data fetched")
                
        except Exception as e:
            logger.error(f"Error updating leaderboard: {e}")

    def _display_fallback_message(self) -> None:
        """Display a fallback message when no data is available."""
        try:
            width = self.display_manager.matrix.width
            height = self.display_manager.matrix.height
            
            # Create a simple text image
            image = Image.new('RGB', (width, height), (0, 0, 0))
            draw = ImageDraw.Draw(image)
            
            text = "No Leaderboard Data"
            text_bbox = draw.textbbox((0, 0), text, font=self.fonts['medium'])
            text_width = text_bbox[2] - text_bbox[0]
            text_height = text_bbox[3] - text_bbox[1]
            
            x = (width - text_width) // 2
            y = (height - text_height) // 2
            
            self._draw_text_with_outline(draw, text, (x, y), self.fonts['medium'], fill=(255, 255, 255))
            
            self.display_manager.image = image
            self.display_manager.draw = ImageDraw.Draw(self.display_manager.image)
            self.display_manager.update_display()
            
        except Exception as e:
            logger.error(f"Error displaying fallback message: {e}")

    def display(self, force_clear: bool = False) -> None:
        """Display the leaderboard."""
        logger.debug("Entering leaderboard display method")
        logger.debug(f"Leaderboard enabled: {self.is_enabled}")
        logger.debug(f"Current scroll position: {self.scroll_position}")
        logger.debug(f"Leaderboard image width: {self.leaderboard_image.width if self.leaderboard_image else 'None'}")
        logger.debug(f"Using dynamic duration for leaderboard: {self.dynamic_duration}s")
        
        if not self.is_enabled:
            logger.debug("Leaderboard is disabled, exiting display method.")
            return
        
        # Reset display start time when force_clear is True or when starting fresh
        if force_clear or not hasattr(self, '_display_start_time'):
            self._display_start_time = time.time()
            logger.debug(f"Reset/initialized display start time: {self._display_start_time}")
            # Also reset scroll position for clean start
            self.scroll_position = 0
        
        logger.debug(f"Number of leagues in data at start of display method: {len(self.leaderboard_data)}")
        if not self.leaderboard_data:
            logger.warning("Leaderboard has no data. Attempting to update...")
            self.update()
            if not self.leaderboard_data:
                logger.warning("Still no data after update. Displaying fallback message.")
                self._display_fallback_message()
                return
        
        if self.leaderboard_image is None:
            logger.warning("Leaderboard image is not available. Attempting to create it.")
            self._create_leaderboard_image()
            if self.leaderboard_image is None:
                logger.error("Failed to create leaderboard image.")
                self._display_fallback_message()
                return

        try:
            current_time = time.time()
            
            # Check if we should be scrolling
            should_scroll = current_time - self.last_scroll_time >= self.scroll_delay
            
            # Signal scrolling state to display manager
            if should_scroll:
                self.display_manager.set_scrolling_state(True)
            else:
                # If we're not scrolling, check if we should process deferred updates
                self.display_manager.process_deferred_updates()
            
            # Scroll the image
            if should_scroll:
                self.scroll_position += self.scroll_speed
                self.last_scroll_time = current_time
            
            # Calculate crop region
            width = self.display_manager.matrix.width
            height = self.display_manager.matrix.height
            
            # Handle looping based on configuration
            if self.loop:
                # Reset position when we've scrolled past the end for a continuous loop
                if self.scroll_position >= self.leaderboard_image.width:
                    logger.info(f"Leaderboard loop reset: scroll_position {self.scroll_position} >= image width {self.leaderboard_image.width}")
                    self.scroll_position = 0
                    logger.info("Leaderboard starting new loop cycle")
            else:
                # Stop scrolling when we reach the end
                if self.scroll_position >= self.leaderboard_image.width - width:
                    logger.info(f"Leaderboard reached end: scroll_position {self.scroll_position} >= {self.leaderboard_image.width - width}")
                    self.scroll_position = self.leaderboard_image.width - width
                    # Signal that scrolling has stopped
                    self.display_manager.set_scrolling_state(False)
                    logger.info("Leaderboard scrolling stopped - reached end of content")
                    if self.time_over == 0:
                        self.time_over = time.time()
                    elif time.time() - self.time_over >= 2:
                        self.time_over = 0
                        raise StopIteration 
            
            # Simple timeout check - prevent hanging beyond maximum display time
            elapsed_time = current_time - self._display_start_time
            if elapsed_time > self.max_display_time:
                logger.warning(f"Leaderboard display exceeded maximum time ({self.max_display_time}s), forcing next view")
                raise StopIteration("Maximum display time exceeded")
            
            # Create the visible part of the image by cropping from the leaderboard_image
            visible_image = self.leaderboard_image.crop((
                self.scroll_position,
                0,
                self.scroll_position + width,
                height
            ))
            
            # Display the visible portion
            self.display_manager.image = visible_image
            self.display_manager.draw = ImageDraw.Draw(self.display_manager.image)
            self.display_manager.update_display()
            
        except StopIteration as e:
            raise e
        except Exception as e:
            logger.error(f"Error in leaderboard display: {e}")
            self._display_fallback_message()
