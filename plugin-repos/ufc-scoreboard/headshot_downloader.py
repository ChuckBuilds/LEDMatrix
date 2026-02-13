"""
Fighter Headshot Downloader for UFC Scoreboard Plugin

Downloads and caches fighter headshot images from ESPN CDN.
Adapted from LEDMatrix LogoDownloader for MMA fighter headshots.

Based on original work by Alex Resnick (legoguy1000) - PR #137
"""

import logging
import requests
from typing import Optional
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)


class HeadshotDownloader:
    """Fighter headshot downloader from ESPN API."""

    def __init__(self, request_timeout: int = 30, retry_attempts: int = 3):
        """Initialize the headshot downloader with HTTP session and retry logic."""
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
            'User-Agent': 'LEDMatrix/1.0',
            'Accept': 'image/png,image/jpeg,image/*',
            'Accept-Language': 'en-US,en;q=0.9',
            'Accept-Encoding': 'gzip, deflate, br',
            'Connection': 'keep-alive'
        }

    @staticmethod
    def get_headshot_url(fighter_id: str) -> str:
        """Get ESPN headshot URL for a fighter."""
        return (
            f"https://a.espncdn.com/combiner/i?img="
            f"/i/headshots/mma/players/full/{fighter_id}.png"
        )


def download_missing_headshot(
    fighter_id: str,
    fighter_name: str,
    headshot_path: Path,
    headshot_url: str = None
) -> bool:
    """
    Download missing headshot for a fighter.

    Args:
        fighter_id: ESPN fighter ID
        fighter_name: Fighter's display name
        headshot_path: Path where headshot should be saved
        headshot_url: Optional headshot URL (constructed from fighter_id if not provided)

    Returns:
        True if headshot was downloaded or placeholder created successfully
    """
    try:
        # Ensure directory exists and is writable
        headshot_dir = headshot_path.parent
        try:
            headshot_dir.mkdir(parents=True, exist_ok=True)

            # Check if we can write to the directory
            test_file = headshot_dir / '.write_test'
            try:
                test_file.touch()
                test_file.unlink()
            except PermissionError:
                logger.error(f"Permission denied: Cannot write to directory {headshot_dir}")
                return False
        except PermissionError as e:
            logger.error(f"Permission denied: Cannot create directory {headshot_dir}: {e}")
            return False
        except Exception as e:
            logger.error(f"Failed to create headshot directory {headshot_dir}: {e}")
            return False

        # Construct URL if not provided
        if not headshot_url:
            headshot_url = HeadshotDownloader.get_headshot_url(fighter_id)

        # Try to download the headshot
        if headshot_url:
            try:
                response = requests.get(headshot_url, timeout=30)
                if response.status_code == 200:
                    # Verify it's an image
                    content_type = response.headers.get('content-type', '').lower()
                    if any(
                        img_type in content_type
                        for img_type in ['image/png', 'image/jpeg', 'image/jpg', 'image/gif']
                    ):
                        with open(headshot_path, 'wb') as f:
                            f.write(response.content)

                        # Convert to RGBA for consistency
                        try:
                            with Image.open(headshot_path) as img:
                                if img.mode != "RGBA":
                                    img = img.convert("RGBA")
                                    img.save(headshot_path, "PNG")
                        except Exception as e:
                            logger.warning(
                                f"Could not convert headshot for {fighter_name} to RGBA: {e}"
                            )

                        logger.info(f"Downloaded headshot for {fighter_name} from {headshot_url}")
                        return True
                    else:
                        logger.warning(
                            f"Downloaded content for {fighter_name} is not an image: {content_type}"
                        )
            except PermissionError as e:
                logger.error(f"Permission denied downloading headshot for {fighter_name}: {e}")
                return False
            except Exception as e:
                logger.error(f"Failed to download headshot for {fighter_name}: {e}")

        # If no URL or download failed, create a placeholder
        return create_placeholder_headshot(fighter_name, headshot_path)

    except PermissionError as e:
        logger.error(f"Permission denied for {fighter_name}: {e}")
        return False
    except Exception as e:
        logger.error(f"Failed to download headshot for {fighter_name}: {e}")
        # Try to create placeholder as fallback
        try:
            return create_placeholder_headshot(fighter_name, headshot_path)
        except Exception:
            return False


def create_placeholder_headshot(fighter_name: str, headshot_path: Path) -> bool:
    """Create a simple placeholder headshot with fighter initials."""
    try:
        # Ensure directory exists
        headshot_path.parent.mkdir(parents=True, exist_ok=True)

        # Create a simple text-based placeholder
        img = Image.new('RGBA', (64, 64), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)

        # Draw a circle background
        draw.ellipse([4, 4, 60, 60], fill=(60, 60, 60, 200), outline=(100, 100, 100, 255))

        # Try to load a font
        try:
            font = ImageFont.truetype("assets/fonts/PressStart2P-Regular.ttf", 12)
        except Exception:
            font = ImageFont.load_default()

        # Get fighter initials (first letter of first and last name)
        parts = fighter_name.split()
        if len(parts) >= 2:
            initials = parts[0][0].upper() + parts[-1][0].upper()
        elif parts:
            initials = parts[0][:2].upper()
        else:
            initials = "??"

        # Draw initials centered
        bbox = draw.textbbox((0, 0), initials, font=font)
        text_width = bbox[2] - bbox[0]
        text_height = bbox[3] - bbox[1]

        x = (64 - text_width) // 2
        y = (64 - text_height) // 2

        # Draw white text with black outline
        for dx, dy in [(-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1)]:
            draw.text((x + dx, y + dy), initials, font=font, fill=(0, 0, 0))
        draw.text((x, y), initials, font=font, fill=(255, 255, 255))

        # Save the placeholder
        img.save(headshot_path, "PNG")
        logger.info(f"Created placeholder headshot for {fighter_name}")
        return True

    except PermissionError as e:
        logger.error(f"Permission denied creating placeholder headshot for {fighter_name}: {e}")
        return False
    except Exception as e:
        logger.error(f"Failed to create placeholder headshot for {fighter_name}: {e}")
        return False
