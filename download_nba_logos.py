#!/usr/bin/env python3
"""
Script to download all NBA team logos from ESPN API and save them in assets/sports/nba_logos/
"""
import sys
import os
import logging
from typing import Tuple

# Add the src directory to Python path so we can import the logo downloader
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def download_nba_logos(force_download: bool = False) -> Tuple[int, int]:
    """
    Download all NBA team logos from ESPN API.

    Args:
        force_download: Whether to re-download existing logos

    Returns:
        Tuple of (downloaded_count, failed_count)
    """
    try:
        from logo_downloader import download_all_logos_for_league

        logger.info("🏀 Starting NBA logo download...")
        logger.info(f"Target directory: assets/sports/nba_logos/")
        logger.info(f"Force download: {force_download}")

        # Use the existing function to download all NBA logos
        downloaded_count, failed_count = download_all_logos_for_league('nba', force_download)

        logger.info("✅ NBA logo download complete!")
        logger.info(f"📊 Summary: {downloaded_count} downloaded, {failed_count} failed")

        if downloaded_count > 0:
            logger.info("🎉 NBA logos are now ready for use in the leaderboard!")
        else:
            logger.info("ℹ️ All NBA logos were already present or download failed for all teams")

        return downloaded_count, failed_count

    except ImportError as e:
        logger.error(f"❌ Import error: {e}")
        logger.info("💡 Make sure you're running this from the LEDMatrix project directory")
        return 0, 0
    except Exception as e:
        logger.error(f"❌ Unexpected error: {e}")
        return 0, 0

def main():
    """Main function with command line argument parsing."""
    import argparse

    parser = argparse.ArgumentParser(description='Download all NBA team logos from ESPN API')
    parser.add_argument(
        '--force',
        action='store_true',
        help='Force re-download of existing logos'
    )
    parser.add_argument(
        '--quiet',
        action='store_true',
        help='Reduce logging output'
    )

    args = parser.parse_args()

    # Set logging level based on quiet flag
    if args.quiet:
        logging.getLogger().setLevel(logging.WARNING)

    logger.info("🚀 NBA Logo Downloader")
    logger.info("=" * 50)

    # Download the logos
    downloaded, failed = download_nba_logos(args.force)

    # Exit with appropriate code
    if failed > 0 and downloaded == 0:
        logger.error("❌ All downloads failed!")
        sys.exit(1)
    elif failed > 0:
        logger.warning(f"⚠️ {failed} downloads failed, but {downloaded} succeeded")
        sys.exit(0)  # Partial success is still success
    else:
        logger.info("🎉 All NBA logos downloaded successfully!")
        sys.exit(0)

if __name__ == "__main__":
    main()
