#!/usr/bin/env python3
"""
Centralized logo downloader utility for automatically fetching team logos from ESPN API.
This module provides functionality to download missing team logos for various sports leagues,
with special support for FCS teams and other NCAA divisions.
"""

import os
import time
import logging
import requests
import json
from typing import Dict, Any, List, Optional, Tuple
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from src.common.permission_utils import (
    ensure_directory_permissions,
    ensure_file_permissions,
    get_assets_dir_mode,
    get_assets_file_mode
)

logger = logging.getLogger(__name__)

class LogoDownloader:
    """Centralized logo downloader for team logos from ESPN API."""
    
    # ESPN API endpoints for different sports/leagues
    API_ENDPOINTS = {
        'nfl': 'https://site.api.espn.com/apis/site/v2/sports/football/nfl/teams',
        'nba': 'https://site.api.espn.com/apis/site/v2/sports/basketball/nba/teams',
        'mlb': 'https://site.api.espn.com/apis/site/v2/sports/baseball/mlb/teams',
        'nhl': 'https://site.api.espn.com/apis/site/v2/sports/hockey/nhl/teams',
        'ncaa_fb': 'https://site.api.espn.com/apis/site/v2/sports/football/college-football/teams',
        'ncaa_fb_all': 'https://site.api.espn.com/apis/site/v2/sports/football/college-football/teams',  # Includes FCS
        'fcs': 'https://site.api.espn.com/apis/site/v2/sports/football/college-football/teams',  # FCS teams from same endpoint
        'ncaam_basketball': 'https://site.api.espn.com/apis/site/v2/sports/basketball/mens-college-basketball/teams',
        'ncaam': 'https://site.api.espn.com/apis/site/v2/sports/basketball/mens-college-basketball/teams',  # Alias for basketball plugin
        'ncaaw_basketball': 'https://site.api.espn.com/apis/site/v2/sports/basketball/womens-college-basketball/teams',
        'ncaaw': 'https://site.api.espn.com/apis/site/v2/sports/basketball/womens-college-basketball/teams',  # Alias for basketball plugin
        'ncaa_baseball': 'https://site.api.espn.com/apis/site/v2/sports/baseball/college-baseball/teams',
        'ncaam_hockey': 'https://site.api.espn.com/apis/site/v2/sports/hockey/mens-college-hockey/teams',
        # Soccer leagues
        'soccer_eng.1': 'https://site.api.espn.com/apis/site/v2/sports/soccer/eng.1/teams',
        'soccer_esp.1': 'https://site.api.espn.com/apis/site/v2/sports/soccer/esp.1/teams',
        'soccer_ger.1': 'https://site.api.espn.com/apis/site/v2/sports/soccer/ger.1/teams',
        'soccer_ita.1': 'https://site.api.espn.com/apis/site/v2/sports/soccer/ita.1/teams',
        'soccer_fra.1': 'https://site.api.espn.com/apis/site/v2/sports/soccer/fra.1/teams',
        'soccer_por.1': 'https://site.api.espn.com/apis/site/v2/sports/soccer/por.1/teams',
        'soccer_uefa.champions': 'https://site.api.espn.com/apis/site/v2/sports/soccer/uefa.champions/teams',
        'soccer_uefa.europa': 'https://site.api.espn.com/apis/site/v2/sports/soccer/uefa.europa/teams',
        'soccer_usa.1': 'https://site.api.espn.com/apis/site/v2/sports/soccer/usa.1/teams'
    }
    
    # Directory mappings for different leagues
    LOGO_DIRECTORIES = {
        'nfl': 'assets/sports/nfl_logos',
        'nba': 'assets/sports/nba_logos', 
        'wnba': 'assets/sports/wnba_logos', 
        'mlb': 'assets/sports/mlb_logos',
        'nhl': 'assets/sports/nhl_logos',
        # NCAA sports use same directory
        'ncaa_fb': 'assets/sports/ncaa_logos',
        'ncaa_fb_all': 'assets/sports/ncaa_logos',
        'fcs': 'assets/sports/ncaa_logos',
        'ncaam_basketball': 'assets/sports/ncaa_logos',
        'ncaam': 'assets/sports/ncaa_logos',  # Alias for basketball plugin
        'ncaaw_basketball': 'assets/sports/ncaa_logos',
        'ncaaw': 'assets/sports/ncaa_logos',  # Alias for basketball plugin
        'ncaa_baseball': 'assets/sports/ncaa_logos',
        'ncaam_hockey': 'assets/sports/ncaa_logos',
        'ncaaw_hockey': 'assets/sports/ncaa_logos',
        # Soccer leagues - all use the same soccer_logos directory
        'soccer_eng.1': 'assets/sports/soccer_logos',
        'soccer_esp.1': 'assets/sports/soccer_logos',
        'soccer_ger.1': 'assets/sports/soccer_logos',
        'soccer_ita.1': 'assets/sports/soccer_logos',
        'soccer_fra.1': 'assets/sports/soccer_logos',
        'soccer_por.1': 'assets/sports/soccer_logos',
        'soccer_uefa.champions': 'assets/sports/soccer_logos',
        'soccer_uefa.europa': 'assets/sports/soccer_logos',
        'soccer_usa.1': 'assets/sports/soccer_logos'
    }
    
    def __init__(self, request_timeout: int = 30, retry_attempts: int = 3):
        """Initialize the logo downloader with HTTP session and retry logic."""
        self.request_timeout = request_timeout
        self.retry_attempts = retry_attempts
        
        # Set up session with retry logic
        self.session = requests.Session()
        retry_strategy = Retry(
            total=retry_attempts,
            backoff_factor=1,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["GET", "HEAD", "OPTIONS"]
        )
        adapter = HTTPAdapter(max_retries=retry_strategy)
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)
        
        # Set up headers
        self.headers = {
            'User-Agent': 'LEDMatrix/1.0 (https://github.com/yourusername/LEDMatrix; contact@example.com)',
            'Accept': 'application/json',
            'Accept-Language': 'en-US,en;q=0.9',
            'Accept-Encoding': 'gzip, deflate, br',
            'Connection': 'keep-alive'
        }
    
    @staticmethod
    def normalize_abbreviation(abbreviation: str) -> str:
        """Normalize team abbreviation for consistent filename usage."""
        # Handle special characters that can cause filesystem issues
        normalized = abbreviation.upper()
        
        # Replace problematic characters with safe alternatives
        normalized = normalized.replace('&', 'AND')
        normalized = normalized.replace('/', '_')
        normalized = normalized.replace('\\', '_')
        normalized = normalized.replace(':', '_')
        normalized = normalized.replace('*', '_')
        normalized = normalized.replace('?', '_')
        normalized = normalized.replace('"', '_')
        normalized = normalized.replace('<', '_')
        normalized = normalized.replace('>', '_')
        normalized = normalized.replace('|', '_')
        return normalized
    
    @staticmethod
    def get_logo_filename_variations(abbreviation: str) -> list:
        """Get possible filename variations for a team abbreviation."""
        variations = []
        original = abbreviation.upper()
        normalized = LogoDownloader.normalize_abbreviation(abbreviation)
        
        # Add original and normalized versions
        variations.extend([f"{original}.png", f"{normalized}.png"])
        
        # Special handling for known cases
        if original == 'TA&M':
            # TA&M has a file named TA&M.png, but normalize creates TAANDM.png
            variations = [f"{original}.png", f"{normalized}.png"]
        
        return variations
    
    def get_logo_directory(self, league: str) -> str:
        """Get the logo directory for a given league."""
        directory = LogoDownloader.LOGO_DIRECTORIES.get(league, f'assets/sports/{league}_logos')
        path = Path(directory)
        if not path.is_absolute():
            project_root = Path(__file__).resolve().parents[1]
            path = (project_root / path).resolve()
        return str(path)
    
    def ensure_logo_directory(self, logo_dir: str | Path) -> bool:
        """Ensure the logo directory exists, create if necessary."""
        path = Path(logo_dir)
        try:
            # Create directory with proper permissions
            ensure_directory_permissions(path, get_assets_dir_mode())
            
            # Check if we can actually write to the directory
            test_file = path / '.write_test'
            try:
                with open(test_file, 'w') as f:
                    f.write('test')
                test_file.unlink(missing_ok=True)
                logger.debug(f"Directory {path} is writable")
                return True
            except PermissionError:
                logger.error(f"Permission denied: Cannot write to directory {path}")
                logger.error(f"Please run: sudo ./scripts/fix_perms/fix_assets_permissions.sh")
                return False
            except Exception as e:
                logger.error(f"Failed to test write access to directory {path}: {e}")
                return False
                
        except Exception as e:
            logger.error(f"Failed to create logo directory {path}: {e}")
            return False
    
    def download_logo(self, logo_url: str, filepath: Path, team_abbreviation: str) -> bool:
        """Download a single logo from URL and save to filepath."""
        try:
            response = self.session.get(logo_url, headers=self.headers, timeout=self.request_timeout)
            response.raise_for_status()
            
            # Verify it's actually an image
            content_type = response.headers.get('content-type', '').lower()
            if not any(img_type in content_type for img_type in ['image/png', 'image/jpeg', 'image/jpg', 'image/gif']):
                logger.warning(f"Downloaded content for {team_abbreviation} is not an image: {content_type}")
                return False
            
            with open(filepath, 'wb') as f:
                f.write(response.content)
            
            # Verify and convert the downloaded image to RGBA format
            try:
                with Image.open(filepath) as img:
                    # Convert to RGBA to avoid PIL warnings about palette images with transparency
                    if img.mode in ('P', 'LA', 'L'):
                        # Convert palette or grayscale images to RGBA
                        img = img.convert('RGBA')
                    elif img.mode == 'RGB':
                        # Convert RGB to RGBA (add alpha channel)
                        img = img.convert('RGBA')
                    elif img.mode != 'RGBA':
                        # For any other mode, convert to RGBA
                        img = img.convert('RGBA')
                    
                    # Save the converted image
                    img.save(filepath, 'PNG')
                
                # Set proper file permissions after saving
                ensure_file_permissions(filepath, get_assets_file_mode())
                
                logger.info(f"Successfully downloaded and converted logo for {team_abbreviation} -> {filepath.name}")
                return True
            except Exception as e:
                logger.error(f"Downloaded file for {team_abbreviation} is not a valid image or conversion failed: {e}")
                try:
                    os.remove(filepath)  # Remove invalid file
                except:
                    pass
                return False
            
        except PermissionError as e:
            logger.error(f"Permission denied downloading logo for {team_abbreviation}: {e}")
            logger.error(f"Please run: sudo ./scripts/fix_perms/fix_assets_permissions.sh")
            return False
        except requests.exceptions.RequestException as e:
            logger.error(f"Failed to download logo for {team_abbreviation}: {e}")
            return False
        except Exception as e:
            logger.error(f"Unexpected error downloading logo for {team_abbreviation}: {e}")
            return False
    
    def fetch_teams_data(self, league: str) -> Optional[Dict]:
        """Fetch team data from ESPN API for a specific league."""
        api_url = self.API_ENDPOINTS.get(league)
        if not api_url:
            logger.error(f"No API endpoint configured for league: {league}")
            return None
        
        try:
            logger.info(f"Fetching team data for {league} from ESPN API...")
            response = self.session.get(api_url, params={'limit':1000},headers=self.headers, timeout=self.request_timeout)
            response.raise_for_status()
            data = response.json()
            
            logger.info(f"Successfully fetched team data for {league}")
            return data
            
        except requests.exceptions.RequestException as e:
            logger.error(f"Error fetching team data for {league}: {e}")
            return None
        except json.JSONDecodeError as e:
            logger.error(f"Error parsing JSON response for {league}: {e}")
            return None
    
    def fetch_single_team(self, league: str, team_id: str) -> Optional[Dict]:
        """Fetch team data from ESPN API for a specific league."""
        api_url = self.API_ENDPOINTS.get(league)
        if not api_url:
            logger.error(f"No API endpoint configured for league: {league}")
            return None
        
        try:
            logger.info(f"Fetching team data for team {team_id} in {league} from ESPN API...")
            response = self.session.get(f"{api_url}/{team_id}", headers=self.headers, timeout=self.request_timeout)
            response.raise_for_status()
            data = response.json()
            
            logger.info(f"Successfully fetched team data for {team_id} in {league}")
            return data
            
        except requests.exceptions.RequestException as e:
            logger.error(f"Error fetching team data for {team_id} in {league}: {e}")
            return None
        except json.JSONDecodeError as e:
            logger.error(f"Error parsing JSON response for{team_id} in {league}: {e}")
            return None
    
    def extract_teams_from_data(self, data: Dict, league: str) -> List[Dict[str, str]]:
        """Extract team information from ESPN API response."""
        teams = []
        
        try:
            sports = data.get('sports', [])
            for sport in sports:
                leagues_data = sport.get('leagues', [])
                for league_data in leagues_data:
                    teams_data = league_data.get('teams', [])
                    
                    for team_data in teams_data:
                        team_info = team_data.get('team', {})
                        
                        abbreviation = team_info.get('abbreviation', '')
                        display_name = team_info.get('displayName', 'Unknown')
                        logos = team_info.get('logos', [])
                        
                        if not abbreviation or not logos:
                            continue
                        
                        # Get the default logo (first one is usually default)
                        logo_url = logos[0].get('href', '')
                        if not logo_url:
                            continue
                        
                        # For NCAA football, try to determine if it's FCS or FBS
                        team_category = 'FBS'  # Default
                        if league in ['ncaa_fb', 'ncaa_fb_all', 'fcs']:
                            # Check if this is an FCS team by looking at conference or other indicators
                            # ESPN API includes both FBS and FCS teams in the same endpoint
                            # We'll include all teams and let the user decide which ones to use
                            team_category = self._determine_ncaa_football_division(team_info, league_data)
                        
                        teams.append({
                            'abbreviation': abbreviation,
                            'display_name': display_name,
                            'logo_url': logo_url,
                            'league': league,
                            'category': team_category,
                            'conference': league_data.get('name', 'Unknown')
                        })
            
            logger.info(f"Extracted {len(teams)} teams for {league}")
            return teams
            
        except Exception as e:
            logger.error(f"Error extracting teams for {league}: {e}")
            return []
    
    def _determine_ncaa_football_division(self, team_info: Dict, league_data: Dict) -> str:
        """Determine if an NCAA football team is FBS or FCS based on conference and other indicators."""
        conference_name = league_data.get('name', '').lower()
        
        # FBS Conferences (more comprehensive list)
        fbs_conferences = {
            'acc', 'american athletic', 'big 12', 'big ten', 'conference usa', 'c-usa',
            'mid-american', 'mac', 'mountain west', 'pac-12', 'pac-10', 'sec', 
            'sun belt', 'independents', 'big east'
        }
        
        # FCS Conferences (more comprehensive list)
        fcs_conferences = {
            'big sky', 'big south', 'colonial athletic', 'caa', 'ivy league', 
            'meac', 'missouri valley', 'mvfc', 'northeast', 'nec', 
            'ohio valley', 'ovc', 'patriot league', 'pioneer football', 
            'southland', 'southern', 'southwestern athletic', 'swac',
            'western athletic', 'wac', 'ncaa division i-aa'
        }
        
        # Also check for specific team indicators
        team_abbreviation = team_info.get('abbreviation', '').upper()
        
        # Known FBS teams that might be misclassified
        known_fbs_teams = {
            'ASU', 'ARIZ', 'ARK', 'AUB', 'BOIS', 'CSU', 'FLA', 'HAW', 'IDHO', 'USA'
        }
        
        # Check if it's a known FBS team first
        if team_abbreviation in known_fbs_teams:
            return 'FBS'
        
        # Check conference names
        if any(fbs_conf in conference_name for fbs_conf in fbs_conferences):
            return 'FBS'
        elif any(fcs_conf in conference_name for fcs_conf in fcs_conferences):
            return 'FCS'
        
        # If conference is just "NCAA - Football", we need to use other indicators
        if conference_name == 'ncaa - football':
            # Check team name for indicators of FCS (smaller schools, Division II/III)
            team_name = team_info.get('displayName', '').lower()
            fcs_indicators = ['college', 'university', 'state', 'tech', 'community']
            
            # If it has typical FCS naming patterns and isn't a known FBS team
            if any(indicator in team_name for indicator in fcs_indicators):
                return 'FCS'
            else:
                return 'FBS'
        
        # Default to FBS for unknown conferences
        return 'FBS'
    
    def _get_team_name_variations(self, abbreviation: str) -> List[str]:
        """Generate common variations of a team abbreviation for matching."""
        variations = set()
        abbr = abbreviation.upper()
        variations.add(abbr)
        
        # Add normalized version
        variations.add(self.normalize_abbreviation(abbr))
        
        # Common substitutions
        substitutions = {
            '&': ['AND', 'A'],
            'A&M': ['TAMU', 'TA&M', 'TEXASAM'],
            'STATE': ['ST', 'ST.'],
            'UNIVERSITY': ['U', 'UNIV'],
            'COLLEGE': ['C', 'COL'],
            'TECHNICAL': ['TECH', 'T'],
            'NORTHERN': ['NORTH', 'N'],
            'SOUTHERN': ['SOUTH', 'S'],
            'EASTERN': ['EAST', 'E'],
            'WESTERN': ['WEST', 'W']
        }
        
        # Apply substitutions
        for original, replacements in substitutions.items():
            if original in abbr:
                for replacement in replacements:
                    variations.add(abbr.replace(original, replacement))
                    variations.add(abbr.replace(original, ''))  # Remove the word entirely
        
        # Add common abbreviations for Texas A&M
        if 'A&M' in abbr or 'TAMU' in abbr:
            variations.update(['TAMU', 'TA&M', 'TEXASAM', 'TEXAS_A&M', 'TEXAS_AM'])
        
        return list(variations)
    
    def download_missing_logos_for_league(self, league: str, force_download: bool = False) -> Tuple[int, int]:
        """Download missing logos for a specific league."""
        logger.info(f"Starting logo download for league: {league}")
        
        # Get logo directory
        logo_dir = self.get_logo_directory(league)
        if not self.ensure_logo_directory(logo_dir):
            logger.error(f"Failed to create logo directory for {league}")
            return 0, 0
        
        # Fetch team data
        data = self.fetch_teams_data(league)
        if not data:
            logger.error(f"Failed to fetch team data for {league}")
            return 0, 0
        
        # Extract teams
        teams = self.extract_teams_from_data(data, league)
        if not teams:
            logger.warning(f"No teams found for {league}")
            return 0, 0
        
        # Download missing logos
        downloaded_count = 0
        failed_count = 0
        
        for team in teams:
            abbreviation = team['abbreviation']
            display_name = team['display_name']
            logo_url = team['logo_url']
            
            # Create filename
            filename = f"{self.normalize_abbreviation(abbreviation)}.png"
            filepath = Path(logo_dir) / filename
            
            # Skip if already exists and not forcing download
            if filepath.exists() and not force_download:
                logger.debug(f"Skipping {display_name}: {filename} already exists")
                continue
            
            # Download logo
            if self.download_logo(logo_url, filepath, display_name):
                downloaded_count += 1
            else:
                failed_count += 1
            
            # Small delay to be respectful to the API
            time.sleep(0.1)
        
        logger.info(f"Logo download complete for {league}: {downloaded_count} downloaded, {failed_count} failed")
        return downloaded_count, failed_count
    
    def download_all_ncaa_football_logos(self, include_fcs: bool = True, force_download: bool = False) -> Tuple[int, int]:
        """Download all NCAA football team logos including FCS teams."""
        logger.info(f"Starting comprehensive NCAA football logo download (FCS: {include_fcs})")
        
        # Use the comprehensive NCAA football endpoint
        league = 'ncaa_fb_all'
        logo_dir = self.get_logo_directory(league)
        if not self.ensure_logo_directory(logo_dir):
            logger.error(f"Failed to create logo directory for {league}")
            return 0, 0
        
        # Fetch team data
        data = self.fetch_teams_data(league)
        if not data:
            logger.error(f"Failed to fetch team data for {league}")
            return 0, 0
        
        # Extract teams
        teams = self.extract_teams_from_data(data, league)
        if not teams:
            logger.warning(f"No teams found for {league}")
            return 0, 0
        
        # Filter teams based on FCS inclusion
        if not include_fcs:
            teams = [team for team in teams if team.get('category') == 'FBS']
            logger.info(f"Filtered to FBS teams only: {len(teams)} teams")
        
        # Download missing logos
        downloaded_count = 0
        failed_count = 0
        
        for team in teams:
            abbreviation = team['abbreviation']
            display_name = team['display_name']
            logo_url = team['logo_url']
            category = team.get('category', 'Unknown')
            conference = team.get('conference', 'Unknown')
            
            # Create filename
            filename = f"{self.normalize_abbreviation(abbreviation)}.png"
            filepath = Path(logo_dir) / filename
            
            # Skip if already exists and not forcing download
            if filepath.exists() and not force_download:
                logger.debug(f"Skipping {display_name} ({category}, {conference}): {filename} already exists")
                continue
            
            # Download logo
            if self.download_logo(logo_url, filepath, display_name):
                downloaded_count += 1
                logger.info(f"Downloaded {display_name} ({category}, {conference}) -> {filename}")
            else:
                failed_count += 1
                logger.warning(f"Failed to download {display_name} ({category}, {conference})")
            
            # Small delay to be respectful to the API
            time.sleep(0.1)
        
        logger.info(f"Comprehensive NCAA football logo download complete: {downloaded_count} downloaded, {failed_count} failed")
        return downloaded_count, failed_count
    
    def download_missing_logo_for_team(self, league: str, team_id: str, team_abbreviation: str, logo_path: Path) -> bool:
        """Download a specific team's logo if it's missing."""
        
        # Ensure the logo directory exists and is writable
        logo_dir = str(logo_path.parent)
        if not self.ensure_logo_directory(logo_dir):
            logger.error(f"Cannot download logo for {team_abbreviation}: directory {logo_dir} is not writable")
            return False
        
        # Fetch team data to find the logo URL
        data = self.fetch_single_team(league, team_id)
        if not data:
            return False
        try:
            logo_url = data["team"]["logos"][0]["href"]
        except KeyError:
            return False
        # Download the logo
        success = self.download_logo(logo_url, logo_path, team_abbreviation)
        if success:
            time.sleep(0.1)  # Small delay
        return success
    
    def download_all_missing_logos(self, leagues: List[str] | None = None, force_download: bool = False) -> Dict[str, Tuple[int, int]]:
        """Download missing logos for all specified leagues."""
        if leagues is None:
            leagues = list(self.API_ENDPOINTS.keys())
        
        results = {}
        total_downloaded = 0
        total_failed = 0
        
        for league in leagues:
            if league not in self.API_ENDPOINTS:
                logger.warning(f"Skipping unknown league: {league}")
                continue
            
            downloaded, failed = self.download_missing_logos_for_league(league, force_download)
            results[league] = (downloaded, failed)
            total_downloaded += downloaded
            total_failed += failed
        
        logger.info(f"Overall logo download results: {total_downloaded} downloaded, {total_failed} failed")
        return results
    
    def create_placeholder_logo(self, team_abbreviation: str, logo_dir: str) -> bool:
        """Create a placeholder logo when real logo cannot be downloaded."""
        try:
            # Ensure the logo directory exists
            if not self.ensure_logo_directory(logo_dir):
                logger.error(f"Failed to create logo directory: {logo_dir}")
                return False
            
            filename = f"{self.normalize_abbreviation(team_abbreviation)}.png"
            filepath = Path(logo_dir) / filename
            
            # Check if we can write to the directory
            try:
                # Test write permissions by creating a temporary file
                test_file = filepath.parent / "test_write.tmp"
                test_file.touch()
                test_file.unlink()  # Remove the test file
            except PermissionError:
                logger.error(f"Permission denied: Cannot write to directory {logo_dir}")
                return False
            except Exception as e:
                logger.error(f"Directory access error for {logo_dir}: {e}")
                return False
            
            # Create a simple placeholder logo
            logo = Image.new('RGBA', (64, 64), (100, 100, 100, 255))  # Gray background
            draw = ImageDraw.Draw(logo)
            
            # Try to load a font, fallback to default
            try:
                font = ImageFont.truetype("assets/fonts/PressStart2P-Regular.ttf", 12)
            except:
                try:
                    font = ImageFont.load_default()
                except:
                    font = None
            
            # Draw team abbreviation
            text = team_abbreviation
            if font:
                # Center the text
                bbox = draw.textbbox((0, 0), text, font=font)
                text_width = bbox[2] - bbox[0]
                text_height = bbox[3] - bbox[1]
                x = (64 - text_width) // 2
                y = (64 - text_height) // 2
                draw.text((x, y), text, font=font, fill=(255, 255, 255, 255))
            else:
                # Fallback without font
                draw.text((16, 24), text, fill=(255, 255, 255, 255))
            
            logo.save(filepath)
            
            # Set proper file permissions after saving
            ensure_file_permissions(filepath, get_assets_file_mode())
            
            logger.info(f"Created placeholder logo for {team_abbreviation} at {filepath}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to create placeholder logo for {team_abbreviation}: {e}")
            return False
    
    def convert_image_to_rgba(self, filepath: Path) -> bool:
        """Convert an image file to RGBA format to avoid PIL warnings."""
        try:
            with Image.open(filepath) as img:
                if img.mode != 'RGBA':
                    # Convert to RGBA
                    converted_img = img.convert('RGBA')
                    converted_img.save(filepath, 'PNG')
                    logger.debug(f"Converted {filepath.name} from {img.mode} to RGBA")
                    return True
                else:
                    logger.debug(f"{filepath.name} is already in RGBA format")
                    return True
        except Exception as e:
            logger.error(f"Failed to convert {filepath.name} to RGBA: {e}")
            return False
    
    def convert_all_logos_to_rgba(self, league: str) -> Tuple[int, int]:
        """Convert all logos in a league directory to RGBA format."""
        logo_dir = Path(self.get_logo_directory(league))
        if not logo_dir.exists():
            logger.warning(f"Logo directory does not exist: {logo_dir}")
            return 0, 0
        
        converted_count = 0
        failed_count = 0
        
        for logo_file in logo_dir.glob("*.png"):
            if self.convert_image_to_rgba(logo_file):
                converted_count += 1
            else:
                failed_count += 1
        
        logger.info(f"Converted {converted_count} logos to RGBA format for {league}, {failed_count} failed")
        return converted_count, failed_count


# Helper function to map soccer league codes to logo downloader format
def get_soccer_league_key(league_code: str) -> str:
    """
    Map soccer league codes to logo downloader format.
    
    Args:
        league_code: Soccer league code (e.g., 'eng.1', 'por.1')
        
    Returns:
        Logo downloader league key (e.g., 'soccer_eng.1', 'soccer_por.1')
    """
    return f"soccer_{league_code}"


# Convenience function for easy integration
def download_missing_logo(league: str, team_id: str, team_abbreviation: str, logo_path: Path, logo_url: str | None = None, create_placeholder: bool = True) -> bool:
    """
    Convenience function to download a missing team logo.
    
    Args:
        team_abbreviation: Team abbreviation (e.g., 'UGA', 'BAMA', 'TA&M')
        league: League identifier (e.g., 'ncaa_fb', 'nfl')
        logo_path: Full path to where the logo should be saved
        logo_url: Optional direct URL to the logo
        create_placeholder: Whether to create a placeholder if download fails
        
    Returns:
        True if logo exists or was successfully downloaded, False otherwise
    """
    downloader = LogoDownloader()
    
    # Use the directory from the logo_path parameter (respects config settings)
    logo_path = Path(logo_path)
    if not logo_path.is_absolute():
        project_root = Path(__file__).resolve().parents[1]
        logo_path = (project_root / logo_path).resolve()

    logo_dir = str(logo_path.parent)
    
    # Ensure the directory exists and is writable
    if not downloader.ensure_logo_directory(logo_dir):
        logger.error(f"Cannot download logo for {team_abbreviation}: directory {logo_dir} is not writable")
        return False
    
    # Use the exact filepath that was passed in (respects config settings)
    filepath = logo_path
    
    if filepath.exists():
        logger.debug(f"Logo already exists for {team_abbreviation} ({league})")
        return True
    
    # Try to download the real logo first
    logger.info(f"Attempting to download logo for {team_abbreviation} from {league}")
    if logo_url:
        success = downloader.download_logo(logo_url, filepath, team_abbreviation)
        if success:
            time.sleep(0.1)  # Small delay
        if not success and create_placeholder:
            logger.info(f"Creating placeholder logo for {team_abbreviation}")
            success = downloader.create_placeholder_logo(team_abbreviation, logo_dir)
        return success

    success = downloader.download_missing_logo_for_team(league, team_id, team_abbreviation, logo_path)
    
    if not success and create_placeholder:
        logger.info(f"Creating placeholder logo for {team_abbreviation}")
        # Create placeholder as fallback
        success = downloader.create_placeholder_logo(team_abbreviation, logo_dir)
    
    if success:
        logger.info(f"Successfully handled logo for {team_abbreviation}")
    else:
        logger.warning(f"Failed to download or create logo for {team_abbreviation}")
    
    return success


def download_all_logos_for_league(league: str, force_download: bool = False) -> Tuple[int, int]:
    """
    Convenience function to download all missing logos for a league.
    
    Args:
        league: League identifier (e.g., 'ncaa_fb', 'nfl')
        force_download: Whether to re-download existing logos
        
    Returns:
        Tuple of (downloaded_count, failed_count)
    """
    downloader = LogoDownloader()
    return downloader.download_missing_logos_for_league(league, force_download)
