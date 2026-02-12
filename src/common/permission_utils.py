"""
Permission Utilities

Centralized utility functions for managing file and directory permissions
across the LEDMatrix codebase. Ensures consistent permission handling for
files that need to be accessible by both root service and web user.
"""

import os
import logging
import subprocess
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# System directories that should never have their permissions modified
# These directories have special system-level permissions that must be preserved
PROTECTED_SYSTEM_DIRECTORIES = {
    '/tmp',
    '/var/tmp',
    '/dev',
    '/proc',
    '/sys',
    '/run',
    '/var/run',
    '/etc',
    '/boot',
    '/var',
    '/usr',
    '/lib',
    '/lib64',
    '/bin',
    '/sbin',
}


def ensure_directory_permissions(path: Path, mode: int = 0o775) -> None:
    """
    Create directory and set permissions.

    If the directory already exists and we cannot change its permissions,
    we check if it's usable (readable/writable). If so, we continue without
    raising an exception. This allows the system to work even when running
    as a non-root user who cannot change permissions on existing directories.

    Protected system directories (like /tmp, /etc, /var) are never modified
    to prevent breaking system functionality.

    Args:
        path: Directory path to create/ensure
        mode: Permission mode (default: 0o775 for group-writable directories)

    Raises:
        OSError: If directory creation fails or directory exists but is not usable
    """
    try:
        # Never modify permissions on system directories
        path_str = str(path.resolve() if path.is_absolute() else path)
        if path_str in PROTECTED_SYSTEM_DIRECTORIES:
            logger.debug(f"Skipping permission modification on protected system directory: {path_str}")
            # Verify the directory is usable
            if path.exists() and os.access(path, os.R_OK | os.W_OK):
                return
            elif path.exists():
                logger.warning(f"Protected system directory {path_str} exists but is not writable")
                return
            else:
                raise OSError(f"Protected system directory {path_str} does not exist")

        # Create directory if it doesn't exist
        path.mkdir(parents=True, exist_ok=True)

        # Try to set permissions
        try:
            os.chmod(path, mode)
            logger.debug(f"Set directory permissions {oct(mode)} on {path}")
        except (OSError, PermissionError) as perm_error:
            # If we can't set permissions, check if directory is usable
            if path.exists():
                # Check if directory is readable and writable
                if os.access(path, os.R_OK | os.W_OK):
                    logger.warning(
                        f"Could not set permissions on {path} (may be owned by different user), "
                        f"but directory is usable (readable/writable). Continuing."
                    )
                    return
                else:
                    # Directory exists but is not usable
                    logger.error(
                        f"Directory {path} exists but is not readable/writable. "
                        f"Permission change failed: {perm_error}"
                    )
                    raise OSError(
                        f"Directory {path} exists but is not usable: {perm_error}"
                    ) from perm_error
            else:
                # Directory doesn't exist and we couldn't create it
                raise
    except OSError as e:
        logger.error(f"Failed to ensure directory {path}: {e}")
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
        Permission mode: 0o2775 (rwxrwxr-x + sticky bit) for group-writable directories
    """
    return 0o2775  # rwxrwsr-x (setgid + group writable)


def get_config_dir_mode() -> int:
    """
    Return permission mode for config directory.
    
    Returns:
        Permission mode: 0o2775 (rwxrwxr-x + sticky bit) for group-writable directories
    """
    return 0o2775  # rwxrwsr-x (setgid + group writable)


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
        Permission mode: 0o2775 (rwxrwxr-x + sticky bit) for group-writable directories
    """
    return 0o2775  # rwxrwsr-x (setgid + group writable)


def get_cache_dir_mode() -> int:
    """
    Return permission mode for cache directories.

    Returns:
        Permission mode: 0o2775 (rwxrwxr-x + sticky bit) for group-writable cache directories
    """
    return 0o2775  # rwxrwsr-x (setgid + group writable)


def sudo_remove_directory(path: Path) -> bool:
    """
    Remove a directory using sudo as a last resort.

    Used when normal removal fails due to root-owned files (e.g., __pycache__
    directories created by the root ledmatrix service). Requires a sudoers rule
    allowing the current user to run 'rm -rf' on the specific path.

    Args:
        path: Directory path to remove

    Returns:
        True if removal succeeded, False otherwise
    """
    try:
        result = subprocess.run(
            ['sudo', '-n', '/usr/bin/rm', '-rf', str(path)],
            capture_output=True,
            text=True,
            timeout=30
        )
        if result.returncode == 0 and not path.exists():
            logger.info(f"Successfully removed {path} via sudo")
            return True
        else:
            logger.error(f"sudo rm failed for {path}: {result.stderr.strip()}")
            return False
    except subprocess.TimeoutExpired:
        logger.error(f"sudo rm timed out for {path}")
        return False
    except FileNotFoundError:
        logger.error("sudo command not found on system")
        return False
    except Exception as e:
        logger.error(f"Unexpected error during sudo rm for {path}: {e}")
        return False

