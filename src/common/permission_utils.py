"""
Permission Utilities

Centralized utility functions for managing file and directory permissions
across the LEDMatrix codebase. Ensures consistent permission handling for
files that need to be accessible by both root service and web user.
"""

import os
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


def ensure_directory_permissions(path: Path, mode: int = 0o775) -> None:
    """
    Create directory and set permissions.
    
    Args:
        path: Directory path to create/ensure
        mode: Permission mode (default: 0o775 for group-writable directories)
    
    Raises:
        OSError: If directory creation or permission setting fails
    """
    try:
        # Create directory if it doesn't exist
        path.mkdir(parents=True, exist_ok=True)
        
        # Set permissions
        os.chmod(path, mode)
        logger.debug(f"Set directory permissions {oct(mode)} on {path}")
    except OSError as e:
        logger.error(f"Failed to set directory permissions on {path}: {e}")
        raise


def ensure_file_permissions(path: Path, mode: int = 0o644) -> None:
    """
    Set file permissions after creation.
    
    Args:
        path: File path to set permissions on
        mode: Permission mode (default: 0o644 for readable files)
    
    Raises:
        OSError: If permission setting fails
    """
    try:
        if path.exists():
            os.chmod(path, mode)
            logger.debug(f"Set file permissions {oct(mode)} on {path}")
        else:
            logger.warning(f"File does not exist, cannot set permissions: {path}")
    except OSError as e:
        logger.error(f"Failed to set file permissions on {path}: {e}")
        raise


def get_config_file_mode(file_path: Path) -> int:
    """
    Return appropriate permission mode for config files.
    
    Args:
        file_path: Path to config file
        
    Returns:
        Permission mode: 0o640 for secrets files, 0o644 for regular config
    """
    if 'secrets' in str(file_path):
        return 0o640  # rw-r-----
    else:
        return 0o644  # rw-r--r--


def get_assets_file_mode() -> int:
    """
    Return permission mode for asset files (logos, images, etc.).
    
    Returns:
        Permission mode: 0o664 (rw-rw-r--) for group-writable assets
    """
    return 0o664  # rw-rw-r--


def get_assets_dir_mode() -> int:
    """
    Return permission mode for asset directories.
    
    Returns:
        Permission mode: 0o775 (rwxrwxr-x) for group-writable directories
    """
    return 0o775  # rwxrwxr-x


def get_config_dir_mode() -> int:
    """
    Return permission mode for config directory.
    
    Returns:
        Permission mode: 0o755 (rwxr-xr-x) for readable directories
    """
    return 0o755  # rwxr-xr-x


def get_plugin_file_mode() -> int:
    """
    Return permission mode for plugin files.
    
    Returns:
        Permission mode: 0o664 (rw-rw-r--) for group-writable plugin files
    """
    return 0o664  # rw-rw-r--


def get_plugin_dir_mode() -> int:
    """
    Return permission mode for plugin directories.
    
    Returns:
        Permission mode: 0o775 (rwxrwxr-x) for group-writable directories
    """
    return 0o775  # rwxrwxr-x


def get_cache_dir_mode() -> int:
    """
    Return permission mode for cache directories.
    
    Returns:
        Permission mode: 0o775 (rwxrwxr-x) for group-writable cache directories
    """
    return 0o775  # rwxrwxr-x

