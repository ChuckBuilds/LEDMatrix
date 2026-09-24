#!/usr/bin/env python3
"""
Centralized logo downloader utility for automatically fetching team logos from ESPN API.
This module provides functionality to download missing team logos for various sports leagues,
with special support for FCS teams and other NCAA divisions.
"""

import os
import re
import tempfile
import threading
import time
import logging
import requests
import json
from typing import Dict, List, Optional, Tuple
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont, UnidentifiedImageError
from src.common.font_layout import load_truetype, resolve_asset_path
from PIL.PngImagePlugin import PngInfo
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from src.common.api_helper import DEFAULT_HTTP_HEADERS
from src.common.logo_helper import MAX_LOGO_BYTES
from src.common.permission_utils import (
    ensure_directory_permissions,
    ensure_file_permissions,
    get_assets_dir_mode,
    get_assets_file_mode
)

logger = logging.getLogger(__name__)

#: Accept header for logo image requests (the JSON default is for the API).
LOGO_ACCEPT = 'image/png,image/*;q=0.8'


def _is_image_content_type(content_type: str) -> bool:
    """True for an ``image/*`` media type, ignoring parameters and case.

    Only a cheap gate before the body is read; Pillow decoding the bytes is
    what actually decides whether they are an image.
    """
    return content_type.split(';', 1)[0].strip().lower().startswith('image/')


def _to_rgba(img: Image.Image) -> Image.Image:
    """``img`` as RGBA, keeping its transparency.

    One conversion covers every mode: Pillow folds a palette's or a
    greyscale/RGB image's ``transparency`` entry into the alpha channel when
    converting to RGBA, and an image without one gets an opaque alpha. Plugins
    paste logos with the image as its own mask, so the alpha is the part that
    has to survive.
    """
    return img.copy() if img.mode == 'RGBA' else img.convert('RGBA')


def _publish(tmp_path: Path, filepath: Path) -> None:
    """Give a finished temp file the asset mode, then move it into place.

    ``mkstemp`` creates files 0600, so the mode is set before the rename: the
    logo must never be visible under its real name unreadable to the web
    service's user. ``os.replace`` within one directory is atomic, so a reader
    sees the old file or the new one, never a half-written one.
    """
    ensure_file_permissions(tmp_path, get_assets_file_mode())
    os.replace(tmp_path, filepath)


def _temp_beside(filepath: Path) -> Tuple[int, Path]:
    """A unique temp file in ``filepath``'s directory.

    Unique rather than a fixed ``<name>.part`` because two plugins can ask for
    the same logo at once; a shared name would let them interleave writes into
    one file, or delete each other's partial. The same directory keeps
    ``os.replace`` atomic.
    """
    fd, tmp_name = tempfile.mkstemp(
        dir=str(filepath.parent), prefix=filepath.name + '.', suffix='.part')
    return fd, Path(tmp_name)


def save_png_atomically(image: Image.Image, filepath: Path,
                        pnginfo: Optional[PngInfo] = None) -> None:
    """Save ``image`` as a PNG at ``filepath`` without ever exposing a partial file.

    Raises on failure (including PermissionError for an unwritable
    directory), leaving any previous file at ``filepath`` untouched and no
    temp file behind.
    """
    filepath = Path(filepath)
    fd, tmp_path = _temp_beside(filepath)
    try:
        with os.fdopen(fd, 'wb') as f:
            if pnginfo is not None:
                image.save(f, 'PNG', pnginfo=pnginfo)
            else:
                image.save(f, 'PNG')
        _publish(tmp_path, filepath)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise


def fetch_logo(session: requests.Session, url: str, filepath: Path, *,
               headers: Optional[Dict[str, str]] = None, timeout: float = 30,
               max_bytes: Optional[int] = None) -> None:
    """Download the image at ``url`` and save it at ``filepath`` as an RGBA PNG.

    The one hardened logo download; ``LogoDownloader.download_logo`` and
    ``LogoHelper._download_logo`` both go through it. A logo URL is remote
    input, and whatever lands at ``filepath`` is cached and loaded on every
    later frame, so nothing is written there until the bytes have been
    size-checked and decoded:

    - the response must be ``image/*`` and is streamed, counted as it
      arrives, and abandoned past ``max_bytes`` (``MAX_LOGO_BYTES`` by
      default) -- ``response.content`` would buffer a body that never ends
      before any check could run;
    - the bytes go to a unique temp file beside ``filepath`` and must decode
      with Pillow (which also enforces its decompression-bomb limit);
    - the image is converted to RGBA once, rewritten as PNG, and moved into
      place atomically.

    Raises on any failure, with no temp file left and any previous file at
    ``filepath`` intact. The caller creates the directory.
    """
    if max_bytes is None:
        max_bytes = MAX_LOGO_BYTES
    filepath = Path(filepath)
    fd, tmp_path = _temp_beside(filepath)
    try:
        # fdopen outermost so the descriptor is adopted and closed even when
        # the request itself raises.
        with os.fdopen(fd, 'wb') as f:
            with session.get(url, headers=headers, timeout=timeout,
                             stream=True) as response:
                response.raise_for_status()
                content_type = response.headers.get('content-type') or ''
                if not _is_image_content_type(content_type):
                    raise ValueError(
                        f"Logo at {url} is not an image "
                        f"(content-type {content_type!r}); not saved")
                received = 0
                for chunk in response.iter_content(chunk_size=64 * 1024):
                    if not chunk:
                        continue
                    received += len(chunk)
                    if received > max_bytes:
                        raise ValueError(
                            f"Logo at {url} exceeds the "
                            f"{max_bytes}-byte limit; not saved")
                    f.write(chunk)

        # UnidentifiedImageError (an OSError) for bytes that are not an
        # image, DecompressionBombError past Pillow's pixel limit, OSError for
        # a truncated one.
        with Image.open(tmp_path) as img:
            img.load()
            rgba = _to_rgba(img)
        with open(tmp_path, 'wb') as f:
            rgba.save(f, 'PNG')
        _publish(tmp_path, filepath)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise


#: PNG text key stamped into a generated placeholder so a later run can tell it
#: apart from a real logo that happens to be small.
PLACEHOLDER_MARKER = "ledmatrix_placeholder"

#: Geometry of a generated placeholder, used to recognise ones written before
#: the marker existed. Those are already on users' disks and would otherwise
#: never be retried.
PLACEHOLDER_SIZE = (64, 64)
PLACEHOLDER_BG = (100, 100, 100, 255)

#: How long a placeholder is trusted before the real logo is attempted again.
#: A placeholder means the download failed, and download failures are usually
#: transient (no network at boot, ESPN blipping). Retrying every frame would
#: hammer the API from a Pi that is also driving a panel; never retrying leaves
#: the team a grey box forever, which is the bug this exists to avoid.
PLACEHOLDER_RETRY_SECONDS = 6 * 60 * 60


def is_placeholder_logo(filepath: Path) -> bool:
    """True if the file at ``filepath`` is a generated placeholder, not a logo.

    Checks the marker first, then falls back to matching the placeholder's
    exact geometry and background colour so files written before the marker was
    introduced are still recognised.
    """
    try:
        with Image.open(filepath) as img:
            if img.info.get(PLACEHOLDER_MARKER):
                return True
            if img.size != PLACEHOLDER_SIZE:
                return False
            return img.convert("RGBA").getpixel((0, 0)) == PLACEHOLDER_BG
    except Exception:
        # Unreadable file: not provably a placeholder, and the caller's own
        # error handling is better placed to deal with it.
        return False


def should_attempt_download(filepath: Path, force_download: bool = False) -> bool:
    """Whether a real logo is worth (re)fetching for ``filepath``.

    True when nothing is there, when the caller forced it, or when what is
    there is a placeholder old enough to retry. A *fresh* placeholder says a
    download just failed, so retrying it immediately would hammer the API for
    a result that is very unlikely to have changed.
    """
    if force_download or not filepath.exists():
        return True
    if not is_placeholder_logo(filepath):
        return False
    age = placeholder_age_seconds(filepath)
    return age is None or age >= PLACEHOLDER_RETRY_SECONDS


def refresh_placeholder_timestamp(filepath: Path) -> bool:
    """Restamp a placeholder so a failed retry restarts the back-off clock.

    Without this a stale placeholder stays stale: every later call sees an
    expired timestamp, retries, fails, and leaves the timestamp untouched --
    which is a download attempt per call, the opposite of what the back-off is
    for.
    """
    try:
        if not is_placeholder_logo(filepath):
            return False
        metadata = PngInfo()
        metadata.add_text(PLACEHOLDER_MARKER, str(time.time()))
        with Image.open(filepath) as img:
            img.copy().save(filepath, "PNG", pnginfo=metadata)
        return True
    except Exception:
        logger.debug("Could not refresh placeholder timestamp for %s", filepath,
                     exc_info=True)
        return False


def placeholder_age_seconds(filepath: Path) -> Optional[float]:
    """Seconds since a placeholder was written, or None if unknown."""
    try:
        with Image.open(filepath) as img:
            stamped = img.info.get(PLACEHOLDER_MARKER)
        if stamped and stamped != "1":
            return max(0.0, time.time() - float(stamped))
    except Exception:
        pass
    try:
        return max(0.0, time.time() - filepath.stat().st_mtime)
    except OSError:
        return None

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
        'ncaaw_hockey': 'https://site.api.espn.com/apis/site/v2/sports/hockey/womens-college-hockey/teams',
        'ncaam_lacrosse': 'https://site.api.espn.com/apis/site/v2/sports/lacrosse/mens-college-lacrosse/teams',
        'ncaaw_lacrosse': 'https://site.api.espn.com/apis/site/v2/sports/lacrosse/womens-college-lacrosse/teams',
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
        'ncaam_lacrosse': 'assets/sports/ncaa_logos',
        'ncaaw_lacrosse': 'assets/sports/ncaa_logos',
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
        
        # Core's shared API headers; a plain dict so callers may adjust theirs.
        self.headers = dict(DEFAULT_HTTP_HEADERS)
    
    @staticmethod
    def normalize_abbreviation(abbreviation: str) -> str:
        """Normalize team abbreviation for consistent filename usage.

        Public API: sports scoreboard plugins call this directly.
        NOTE: LogoHelper.normalize_abbreviation (src/common/logo_helper.py)
        is a deliberately different variant (strips spaces, fewer character
        replacements) — keep both behaviors stable; logo filenames on
        existing installs depend on them.
        """
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
        """Filenames a logo for ``abbreviation`` may be stored under: the
        upper-cased abbreviation as given, then its normalize_abbreviation()
        form (``TA&M.png``, then ``TAANDM.png``)."""
        original = abbreviation.upper()
        normalized = LogoDownloader.normalize_abbreviation(abbreviation)
        return [f"{original}.png", f"{normalized}.png"]
    
    # Allowlist for a league name or code that goes into a filesystem path or
    # an ESPN URL: lower-case alphanumerics, underscores and dashes only.
    _SAFE_LEAGUE_RE = re.compile(r'^[a-z0-9_-]+$')

    def get_logo_directory(self, league: str) -> str:
        """Get the logo directory for a given league."""
        directory = LogoDownloader.LOGO_DIRECTORIES.get(league)
        if not directory:
            # Custom soccer leagues share the same logo directory as predefined ones
            if league.startswith('soccer_'):
                directory = 'assets/sports/soccer_logos'
            else:
                # Validate league before using it in a filesystem path
                if not self._SAFE_LEAGUE_RE.match(league):
                    logger.warning(f"Rejecting unsafe league name for directory construction: {league!r}")
                    raise ValueError(f"Unsafe league name: {league!r}")
                directory = f'assets/sports/{league}_logos'
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
                logger.error("Please run: sudo ./scripts/fix_perms/fix_assets_permissions.sh")
                return False
            except Exception as e:
                logger.error(f"Failed to test write access to directory {path}: {e}")
                return False
                
        except Exception as e:
            logger.error(f"Failed to create logo directory {path}: {e}")
            return False
    
    def download_logo(self, logo_url: str, filepath: Path, team_abbreviation: str) -> bool:
        """Download a single logo from URL and save it to filepath as an RGBA PNG.

        Returns False (and logs why) on any failure; see ``fetch_logo`` for
        the guarantees -- in particular a failure never leaves a partial file,
        and never replaces a logo already at ``filepath``.
        """
        filepath = Path(filepath)
        try:
            fetch_logo(self.session, logo_url, filepath,
                       headers={**self.headers, 'Accept': LOGO_ACCEPT},
                       timeout=self.request_timeout)
            logger.info(f"Successfully downloaded and converted logo for {team_abbreviation} -> {filepath.name}")
            return True
        except PermissionError as e:
            logger.error(f"Permission denied downloading logo for {team_abbreviation}: {e}")
            logger.error("Please run: sudo ./scripts/fix_perms/fix_assets_permissions.sh")
            return False
        except requests.exceptions.RequestException as e:
            logger.error(f"Failed to download logo for {team_abbreviation}: {e}")
            return False
        except (ValueError, UnidentifiedImageError, Image.DecompressionBombError) as e:
            logger.error(f"Rejected downloaded logo for {team_abbreviation}: {e}")
            return False
        except Exception as e:
            logger.error(f"Unexpected error downloading logo for {team_abbreviation}: {e}")
            return False

    def _resolve_api_url(self, league: str) -> Optional[str]:
        """Resolve the ESPN API teams URL for a league, with dynamic fallback for custom soccer leagues."""
        api_url = self.API_ENDPOINTS.get(league)
        if not api_url and league.startswith('soccer_'):
            league_code = league[len('soccer_'):]
            if not self._SAFE_LEAGUE_RE.match(league_code):
                logger.warning(f"Rejecting unsafe league_code for ESPN URL construction: {league_code!r}")
                return None
            api_url = f'https://site.api.espn.com/apis/site/v2/sports/soccer/{league_code}/teams'
            logger.info(f"Using dynamic ESPN endpoint for custom soccer league: {league}")
        return api_url

    def fetch_teams_data(self, league: str) -> Optional[Dict]:
        """Fetch team data from ESPN API for a specific league."""
        api_url = self._resolve_api_url(league)
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
        """Fetch one team's record (``<teams endpoint>/<team_id>``) from the
        ESPN API; None on any request or parse failure."""
        api_url = self._resolve_api_url(league)
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
            logger.error(f"Error parsing JSON response for {team_id} in {league}: {e}")
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
            
            # A placeholder does not count as existing -- it is a previous
            # failure, and a bulk pass is exactly where it should get another
            # chance, subject to the same back-off as everywhere else.
            if not should_attempt_download(filepath, force_download):
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
            
            # Same eligibility rule as every other download site: a stale
            # placeholder is a failed download, not a logo.
            if not should_attempt_download(filepath, force_download):
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
        except (KeyError, IndexError, TypeError):
            # A team without logos comes back with an empty list.
            logger.debug(f"No logo URL for team {team_id} in {league}")
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
            if not self._resolve_api_url(league):
                logger.warning(f"Skipping unknown league: {league}")
                continue
            
            downloaded, failed = self.download_missing_logos_for_league(league, force_download)
            results[league] = (downloaded, failed)
            total_downloaded += downloaded
            total_failed += failed
        
        logger.info(f"Overall logo download results: {total_downloaded} downloaded, {total_failed} failed")
        return results
    
    def create_placeholder_logo(self, team_abbreviation: str, logo_dir: str,
                                filepath: Optional[Path] = None) -> bool:
        """Write a grey placeholder with the team abbreviation on it, for when
        the real logo cannot be downloaded.

        Args:
            team_abbreviation: Drawn on the placeholder.
            logo_dir: Directory for the file when ``filepath`` is not given;
                the file is then ``<normalize_abbreviation(abbr)>.png``.
            filepath: The exact path to write, which is where the caller will
                look for the logo. ``logo_dir`` is ignored when it is given.

        Returns:
            True if the placeholder was written, False otherwise (logged).
        """
        if filepath is not None:
            filepath = Path(filepath)
            logo_dir = str(filepath.parent)
        try:
            if not self.ensure_logo_directory(logo_dir):
                logger.error(f"Failed to create logo directory: {logo_dir}")
                return False

            if filepath is None:
                filename = f"{self.normalize_abbreviation(team_abbreviation)}.png"
                filepath = Path(logo_dir) / filename

            logo = Image.new('RGBA', PLACEHOLDER_SIZE, PLACEHOLDER_BG)
            draw = ImageDraw.Draw(logo)
            
            # Try to load a font, fallback to default
            try:
                font = load_truetype(resolve_asset_path("assets/fonts/PressStart2P-Regular.ttf"), 12)
            except (OSError, IOError):
                try:
                    font = ImageFont.load_default()
                except (OSError, IOError):
                    font = None
            
            # Draw team abbreviation
            text = team_abbreviation
            if font:
                # Center the text
                bbox = draw.textbbox((0, 0), text, font=font)
                text_width = bbox[2] - bbox[0]
                text_height = bbox[3] - bbox[1]
                x = (PLACEHOLDER_SIZE[0] - text_width) // 2
                y = (PLACEHOLDER_SIZE[1] - text_height) // 2
                draw.text((x, y), text, font=font, fill=(255, 255, 255, 255))
            else:
                # Fallback without font
                draw.text((16, 24), text, fill=(255, 255, 255, 255))
            
            # Stamp it so a later run can tell this apart from a real logo and
            # retry the download, instead of treating the file's existence as
            # proof the logo was fetched.
            metadata = PngInfo()
            metadata.add_text(PLACEHOLDER_MARKER, str(time.time()))
            # Atomic, and it sets the asset mode; an unwritable directory
            # surfaces here as PermissionError.
            save_png_atomically(logo, filepath, pnginfo=metadata)

            logger.info(f"Created placeholder logo for {team_abbreviation} at {filepath}")
            return True

        except PermissionError as e:
            logger.error(f"Permission denied: Cannot write placeholder logo to {logo_dir}: {e}")
            return False
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


_thread_state = threading.local()


def shared_downloader() -> LogoDownloader:
    """The calling thread's reusable LogoDownloader.

    ``download_missing_logo`` used to build a new downloader -- a new
    ``requests.Session``, retry adapter and connection pool -- for every logo.
    Reusing one keeps connections to ESPN's CDN alive between logos.

    One per thread rather than one behind a lock: ``requests.Session`` is not
    documented as safe for concurrent use, and plugins download on worker
    threads (football-scoreboard runs a small pool precisely so downloads
    overlap). A lock would serialise every plugin's downloads behind the
    slowest one -- up to 30s per attempt, with retries. Pool threads persist,
    so each still reuses its own session; a thread's downloader goes with the
    thread.
    """
    downloader = getattr(_thread_state, 'downloader', None)
    if downloader is None:
        downloader = LogoDownloader()
        _thread_state.downloader = downloader
    return downloader


# Convenience function for easy integration
def download_missing_logo(league: str, team_id: str, team_abbreviation: str, logo_path: Path, logo_url: str | None = None, create_placeholder: bool = True) -> bool:
    """
    Convenience function to download a missing team logo.
    
    Args:
        league: League identifier (e.g., 'ncaa_fb', 'nfl')
        team_id: ESPN team id, used to look up the logo URL when
            ``logo_url`` is not given
        team_abbreviation: Team abbreviation (e.g., 'UGA', 'BAMA', 'TA&M')
        logo_path: Where the logo (or placeholder) is written; relative paths
            are relative to the install root
        logo_url: Optional direct URL to the logo
        create_placeholder: Whether to create a placeholder if download fails
        
    Returns:
        True if a logo or placeholder is at ``logo_path`` afterwards (it was
        already there, was downloaded, or a placeholder was written),
        False otherwise.
    """
    downloader = shared_downloader()

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
    
    if filepath.exists() and not should_attempt_download(filepath):
        # Either a real logo, or a placeholder too fresh to be worth retrying.
        logger.debug(f"Logo already exists for {team_abbreviation} ({league})")
        return True
    if filepath.exists():
        # A placeholder is a *failed* download wearing the real logo's
        # filename. Treating it as "already exists" is what pinned a team to a
        # grey box permanently after one transient failure.
        logger.info(
            "Logo for %s (%s) is a placeholder from a failed download; "
            "retrying the real logo", team_abbreviation, league,
        )
    
    # Try to download the real logo first
    logger.info(f"Attempting to download logo for {team_abbreviation} from {league}")
    if logo_url:
        success = downloader.download_logo(logo_url, filepath, team_abbreviation)
        if success:
            time.sleep(0.1)  # Small delay
        if not success and create_placeholder:
            logger.info(f"Creating placeholder logo for {team_abbreviation}")
            success = downloader.create_placeholder_logo(team_abbreviation, logo_dir, filepath=filepath)
        return success

    success = downloader.download_missing_logo_for_team(league, team_id, team_abbreviation, logo_path)
    
    if not success and create_placeholder:
        logger.info(f"Creating placeholder logo for {team_abbreviation}")
        # Create placeholder as fallback
        success = downloader.create_placeholder_logo(team_abbreviation, logo_dir, filepath=filepath)
    
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
    downloader = shared_downloader()
    return downloader.download_missing_logos_for_league(league, force_download)
