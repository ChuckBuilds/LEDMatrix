"""
Permission Utilities

Centralized utility functions for managing file and directory permissions
across the LEDMatrix codebase. Ensures consistent permission handling for
files that need to be accessible by both root service and web user.
"""

import os
import logging
import re
import shutil as _shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Matches the credentials portion of a "scheme://user:pass@host" URL, so pip's
# own error output can be logged/displayed without echoing back a private
# index URL's embedded basic-auth secret verbatim (e.g. from a
# requirements.txt --index-url line or the PIP_INDEX_URL env var).
_URL_CREDENTIALS_RE = re.compile(r'://[^/\s@:]+:[^/\s@]+@')


def _redact_url_credentials(text: Optional[str]) -> str:
    """Replace embedded user:pass@ URL credentials in text with a placeholder.

    Safe to call on any subprocess output destined for logs: it only ever
    shortens/replaces the credential substring, never changes the presence
    or absence of the specific fixed phrases callers check for
    (e.g. "a password is required"), so it can't affect control flow.
    """
    if not text:
        return text or ""
    return _URL_CREDENTIALS_RE.sub('://***:***@', text)

# System directories that should never have their permissions modified
# These directories have special system-level permissions that must be preserved
PROTECTED_SYSTEM_DIRECTORIES = {  # nosec B108 - these are checked to PREVENT permission changes, not to use as temp paths
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


def sudo_remove_directory(path: Path, allowed_bases: Optional[list] = None) -> bool:
    """
    Remove a directory using sudo as a last resort.

    Used when normal removal fails due to root-owned files (e.g., __pycache__
    directories created by the root ledmatrix service). Delegates to the
    safe_plugin_rm.sh helper which validates the path is inside allowed
    plugin directories.

    Before invoking sudo, this function also validates that the resolved
    path is a descendant of at least one allowed base directory.

    Args:
        path: Directory path to remove
        allowed_bases: List of allowed parent directories. If None, defaults
            to plugin-repos/ and plugins/ under the project root.

    Returns:
        True if removal succeeded, False otherwise
    """
    # Determine project root (permission_utils.py is at src/common/)
    project_root = Path(__file__).resolve().parent.parent.parent

    if allowed_bases is None:
        allowed_bases = [
            project_root / "plugin-repos",
            project_root / "plugins",
        ]

    # Resolve the target path to prevent symlink/traversal tricks
    try:
        resolved = path.resolve()
    except (OSError, ValueError) as e:
        logger.error(f"Cannot resolve path {path}: {e}")
        return False

    # Validate the resolved path is a strict child of an allowed base
    is_allowed = False
    for base in allowed_bases:
        try:
            base_resolved = base.resolve()
            if resolved != base_resolved and resolved.is_relative_to(base_resolved):
                is_allowed = True
                break
        except (OSError, ValueError):
            continue

    if not is_allowed:
        logger.error(
            f"sudo_remove_directory DENIED: {resolved} is not inside "
            f"allowed bases {[str(b) for b in allowed_bases]}"
        )
        return False

    # Use the safe_plugin_rm.sh helper which does its own validation
    helper_script = project_root / "scripts" / "fix_perms" / "safe_plugin_rm.sh"
    if not helper_script.exists():
        logger.error(f"Safe removal helper not found: {helper_script}")
        return False

    bash_path = _shutil.which('bash') or '/bin/bash'

    try:
        result = subprocess.run(
            ['sudo', '-n', bash_path, str(helper_script), str(resolved)],
            capture_output=True,
            text=True,
            timeout=30
        )
        if result.returncode == 0 and not resolved.exists():
            logger.info(f"Successfully removed {path} via sudo helper")
            return True
        else:
            stderr = result.stderr.strip()
            logger.error(f"sudo helper failed for {path}: {stderr}")
            return False
    except subprocess.TimeoutExpired:
        logger.error(f"sudo helper timed out for {path}")
        return False
    except FileNotFoundError:
        logger.error("sudo command not found on system")
        return False
    except Exception as e:
        logger.error(f"Unexpected error during sudo helper for {path}: {e}")
        return False


def install_requirements_file(req_file: Path, timeout: int = 300) -> subprocess.CompletedProcess:
    """
    Install a requirements.txt file for a plugin (or the project itself).

    Prefers the vetted sudo wrapper (scripts/fix_perms/safe_pip_install.sh) so
    packages end up visible to root-run ledmatrix.service, not just to
    whichever non-root user happens to run the calling process (e.g. the web
    interface). Falls back to installing with the calling process's own
    interpreter if the wrapper isn't set up yet (the admin hasn't run
    scripts/install/configure_web_sudo.sh), so dependency installation still
    does *something* useful rather than hard-failing.

    Always installs with the interpreter that will actually run the code
    (``sys.executable`` in the fallback path, the wrapper's ``python3`` in the
    sudo path) rather than a bare ``pip``/``pip3`` off PATH, which can
    silently resolve to a different Python installation (e.g. system Python
    vs. a virtualenv) than the one importing the package at runtime.

    Args:
        req_file: Path to a requirements.txt file
        timeout: Subprocess timeout in seconds

    Returns:
        subprocess.CompletedProcess from the pip (or wrapper) invocation.
        Never raises on a non-zero exit; callers should check ``returncode``.
        ``stdout`` is prefixed with an explanatory note when the root wrapper
        was unavailable and the fallback path was used.
    """
    project_root = Path(__file__).resolve().parent.parent.parent
    wrapper = project_root / "scripts" / "fix_perms" / "safe_pip_install.sh"

    if wrapper.exists():
        # See sudo_remove_directory / configure_web_sudo.sh for why bash must
        # be invoked with an explicit, known path rather than relying on the
        # wrapper's shebang: sudoers matches the exact command line.
        bash_candidates = []
        for candidate in ("/usr/bin/bash", "/bin/bash", _shutil.which("bash")):
            if candidate and candidate not in bash_candidates:
                bash_candidates.append(candidate)

        result = None
        for bash_path in bash_candidates:
            # bash_path and wrapper are fixed, known-good paths, and
            # safe_pip_install.sh independently re-validates req_file is an
            # allowed requirements.txt before installing anything as root.
            result = subprocess.run(  # nosec B603 - no shell invoked (list-form argv)  # nosemgrep
                ["sudo", "-n", bash_path, str(wrapper), str(req_file)],
                capture_output=True, text=True, timeout=timeout, cwd=str(project_root)
            )
            # Redact immediately: pip can echo a private index URL's embedded
            # basic-auth credentials back in its own error/progress output
            # (e.g. from a requirements.txt --index-url line). Doesn't affect
            # the fixed-phrase "denied" check below -- those phrases never
            # overlap with URL syntax.
            result.stderr = _redact_url_credentials(result.stderr)
            result.stdout = _redact_url_credentials(result.stdout)
            if result.returncode == 0:
                return result
            # Distinguish "sudo rejected this exact command line" (worth
            # trying the next bash candidate) from "sudo ran it but pip
            # itself failed" (a real error — stop and surface it).
            denied = any(
                phrase in result.stderr
                for phrase in ("a password is required", "is not allowed to run", "no tty present")
            )
            if not denied:
                logger.warning(
                    "Root pip install failed (rc=%s) for %s: %s",
                    result.returncode, req_file, result.stderr.strip()[:500],
                )
                return result

        logger.warning(
            "Root pip install wrapper denied via sudo for %s; falling back to "
            "user-level install: %s",
            req_file, result.stderr.strip()[:500] if result else "no bash candidates found",
        )
        note = (
            f"[Root install unavailable ({(result.stderr.strip() if result else 'sudo denied') or 'sudo denied'}); "
            "installed for the current process's user only. Packages may not be "
            "visible to ledmatrix.service if it runs as a different user — "
            "run scripts/install/configure_web_sudo.sh to fix this.]\n"
        )
    else:
        logger.warning(
            "safe_pip_install.sh not found; falling back to user-level install for %s",
            req_file,
        )
        note = (
            "[safe_pip_install.sh not found; installed for the current process's "
            "user only. Run scripts/install/configure_web_sudo.sh to enable "
            "root installs visible to ledmatrix.service.]\n"
        )

    # sys.executable is this process's own interpreter (not
    # attacker-influenced), and req_file is a Path built internally by callers
    # (store_manager.py plugin paths, PROJECT_ROOT/requirements.txt), never
    # raw external/user input. --ignore-installed matches safe_pip_install.sh:
    # apt-managed packages (e.g. python3-requests) ship no pip RECORD file, so
    # upgrading them would otherwise abort with "uninstall-no-record-file".
    result = subprocess.run(  # nosec B603 - no shell invoked (list-form argv)  # nosemgrep
        [sys.executable, "-m", "pip", "install", "--break-system-packages", "--ignore-installed", "-r", str(req_file)],
        capture_output=True, text=True, timeout=timeout, cwd=str(project_root)
    )
    result.stderr = _redact_url_credentials(result.stderr)
    result.stdout = note + _redact_url_credentials(result.stdout)
    return result

