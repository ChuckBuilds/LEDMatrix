"""
Atomic configuration save manager with backup and rollback support.

Provides atomic file operations for configuration files to prevent corruption
and enable recovery from failed saves.
"""

import json
import os
import re
import shutil
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Any, Optional, List, Tuple, Union
from dataclasses import dataclass
from enum import Enum

from src.exceptions import ConfigError
from src.logging_config import get_logger
from src.common.permission_utils import ensure_shared_group_ownership, get_config_file_mode

# Version stamp in a backup's filename: config.json.backup.<version>.
BACKUP_VERSION_FORMAT = "%Y%m%d_%H%M%S_%f"

# Backups written before the format gained microseconds. Still read, never
# written, so existing restore points on a rig stay usable after an upgrade.
LEGACY_BACKUP_VERSION_FORMAT = "%Y%m%d_%H%M%S"

# The numeric collision suffix _create_backup() appends to break a same-tick
# tie: config.json.backup.<version>-<N>. Only digits count as this suffix, so
# a hand-copied or renamed backup that happens to end in "-something" isn't
# mistaken for one and silently mis-parsed.
_BACKUP_COLLISION_SUFFIX_RE = re.compile(r"^(?P<base>.+)-(?P<collision>\d+)$")

# Windows refuses to rename over a file another process has open (a reader
# mid-load). Linux never does, so this only ever retries on a dev machine.
_WINDOWS_REPLACE_ATTEMPTS = 10
_WINDOWS_REPLACE_DELAY = 0.05


def _replace(source: Path, destination: Path) -> None:
    for attempt in range(_WINDOWS_REPLACE_ATTEMPTS):
        try:
            os.replace(source, destination)
            return
        except PermissionError:
            if os.name != 'nt' or attempt == _WINDOWS_REPLACE_ATTEMPTS - 1:
                raise
            time.sleep(_WINDOWS_REPLACE_DELAY)


def _fsync_directory(directory: Path) -> None:
    """Persist a rename: until the directory entry itself is on disk, a power
    cut can bring back the old file, or on some filesystems neither. Windows
    can't open a directory for fsync, and NTFS journals renames anyway."""
    if os.name == 'nt':
        return
    try:
        fd = os.open(directory, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def atomic_write_text(path: Union[str, Path], text: str, mode: Optional[int] = None) -> None:
    """
    Replace ``path`` with ``text`` so that a crash or power cut at any point
    leaves either the old file or the new one, never a truncated mix.

    The data goes to a temp file in the same directory, is fsynced, and is
    renamed over the target; the directory is then fsynced so the rename
    itself survives. The temp file gets its final mode (0o644, or 0o640 when
    the file name contains "secrets"; a directory name doesn't count) before
    the rename, so no reader ever sees mkstemp's 0o600.

    A rename hands the file to whoever wrote it. When running as root (the
    display service) the previous owner is copied onto the temp file first,
    so a root save doesn't leave the web user's config.json owned by root;
    the group is then moved to the shared one (ensure_shared_group_ownership)
    as before. On failure the temp file is removed, the target is untouched,
    and the error propagates.
    """
    path = Path(path)
    if mode is None:
        mode = get_config_file_mode(Path(path.name))
    try:
        previous = path.stat()
    except OSError:
        previous = None

    fd, temp_name = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.tmp.")
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, 'wb') as f:
            f.write(text.encode('utf-8'))
            f.flush()
            os.fsync(f.fileno())
        if previous is not None and hasattr(os, 'geteuid') and os.geteuid() == 0:
            try:
                os.chown(temp_path, previous.st_uid, previous.st_gid)
            except OSError:
                pass
        os.chmod(temp_path, mode)
        _replace(temp_path, path)
    except BaseException:
        try:
            temp_path.unlink()
        except OSError:
            pass
        raise

    ensure_shared_group_ownership(path)
    _fsync_directory(path.parent)


def atomic_write_json(path: Union[str, Path], data: Any, mode: Optional[int] = None) -> None:
    """Serialize ``data`` the way every config file is written (indent=4) and
    write it with :func:`atomic_write_text`. Serialization happens first, so
    a value json can't encode fails before anything on disk changes."""
    atomic_write_text(path, json.dumps(data, indent=4), mode)


class SaveResultStatus(Enum):
    """Status of a save operation."""
    SUCCESS = "success"
    FAILED = "failed"
    VALIDATION_FAILED = "validation_failed"
    ROLLED_BACK = "rolled_back"


@dataclass
class SaveResult:
    """Result of an atomic save operation."""
    status: SaveResultStatus
    message: str
    backup_path: Optional[str] = None
    validation_errors: Optional[List[str]] = None
    error: Optional[Exception] = None


@dataclass
class BackupInfo:
    """Information about a configuration backup."""
    version: str
    path: str
    timestamp: datetime
    size: int
    is_valid: bool


@dataclass
class ValidationResult:
    """Result of configuration file validation."""
    is_valid: bool
    errors: List[str]
    warnings: List[str]


class AtomicConfigManager:
    """
    Manages atomic configuration saves with backup and rollback support.
    
    Provides:
    - Durable atomic file writes (see atomic_write_text)
    - Automatic backups before saves
    - Backup rotation (keep last N backups)
    - Rollback functionality
    - Validation before the write
    """
    
    def __init__(
        self,
        config_path: str,
        secrets_path: Optional[str] = None,
        backup_dir: Optional[str] = None,
        max_backups: int = 5
    ):
        """
        Initialize atomic config manager.
        
        Args:
            config_path: Path to main configuration file
            secrets_path: Optional path to secrets file (backed up with the main
                config, and rewritten by a save only when its content changes)
            backup_dir: Directory to store backups (default: config/backups/)
            max_backups: Maximum number of backups to keep
        """
        self.config_path = Path(config_path)
        self.secrets_path = Path(secrets_path) if secrets_path else None
        
        # Determine backup directory
        if backup_dir:
            self.backup_dir = Path(backup_dir)
        else:
            # Default to config/backups/ relative to config file
            self.backup_dir = self.config_path.parent / "backups"
        
        self.max_backups = max_backups
        self.logger = get_logger(__name__)
        
        # Ensure backup directory exists
        self.backup_dir.mkdir(parents=True, exist_ok=True)
    
    def save_config_atomic(
        self,
        new_config: Dict[str, Any],
        new_secrets: Optional[Dict[str, Any]] = None,
        create_backup: bool = True,
        validate_after_write: bool = True
    ) -> SaveResult:
        """
        Save configuration atomically with optional backup.
        
        Process:
        1. Create backup if requested
        2. Serialize and validate the new content in memory
        3. Write each file with atomic_write_text (temp file, fsync, rename)

        The secrets file is only rewritten when ``new_secrets`` differs from
        what is already on disk.

        Args:
            new_config: New configuration data for main config file
            new_secrets: Optional new secrets data
            create_backup: Whether to create backup before saving
            validate_after_write: Whether to validate the content before it
                replaces the config file

        Returns:
            SaveResult with status and details
        """
        backup_path = None

        try:
            if create_backup:
                backup_result = self._create_backup()
                if backup_result:
                    backup_path = backup_result
                    self.logger.info(f"Created backup: {backup_path}")
                else:
                    self.logger.warning("Failed to create backup, continuing with save")

            config_text, secrets_text = self._serialize(new_config, new_secrets)

            if validate_after_write:
                validation_result = self._validate_config_text(config_text)
                if not validation_result.is_valid:
                    return SaveResult(
                        status=SaveResultStatus.VALIDATION_FAILED,
                        message="Configuration validation failed after write",
                        backup_path=backup_path,
                        validation_errors=validation_result.errors
                    )

            atomic_write_text(self.config_path, config_text)
            if secrets_text is not None:
                atomic_write_text(self.secrets_path, secrets_text)

            self.logger.info(f"Configuration saved atomically to {self.config_path}")
            
            return SaveResult(
                status=SaveResultStatus.SUCCESS,
                message="Configuration saved successfully",
                backup_path=backup_path
            )
            
        except Exception as e:
            self.logger.error(f"Error during atomic save: {e}", exc_info=True)
            
            # Attempt rollback if backup exists
            if backup_path:
                try:
                    self._rollback_from_backup(backup_path)
                    return SaveResult(
                        status=SaveResultStatus.ROLLED_BACK,
                        message=f"Save failed and rolled back: {str(e)}",
                        backup_path=backup_path,
                        error=e
                    )
                except Exception as rollback_error:
                    self.logger.error(f"Rollback also failed: {rollback_error}", exc_info=True)
            
            return SaveResult(
                status=SaveResultStatus.FAILED,
                message=f"Save failed: {str(e)}",
                backup_path=backup_path,
                error=e
            )
    
    def rollback_config(self, backup_version: Optional[str] = None) -> bool:
        """
        Rollback configuration to a previous backup.
        
        Args:
            backup_version: Specific backup version to restore (timestamp string).
                          If None, restores most recent backup.
        
        Returns:
            True if rollback successful, False otherwise
        """
        try:
            backups = self.list_backups()
            if not backups:
                self.logger.error("No backups available for rollback")
                return False
            
            # Find backup to restore
            if backup_version:
                backup = next((b for b in backups if b.version == backup_version), None)
                if not backup:
                    self.logger.error(f"Backup version {backup_version} not found")
                    return False
            else:
                # Use most recent valid backup
                valid_backups = [b for b in backups if b.is_valid]
                if not valid_backups:
                    self.logger.error("No valid backups available for rollback")
                    return False
                backup = valid_backups[0]  # Most recent
            
            return self._rollback_from_backup(backup.path)
            
        except Exception as e:
            self.logger.error(f"Error during rollback: {e}", exc_info=True)
            return False
    
    def list_backups(self) -> List[BackupInfo]:
        """
        List all available backups.
        
        Returns:
            List of BackupInfo objects, sorted by timestamp (newest first)
        """
        backups = []
        for version, backup_file, timestamp in self._backup_entries():
            try:
                backups.append(BackupInfo(
                    version=version,
                    path=str(backup_file),
                    timestamp=timestamp,
                    size=backup_file.stat().st_size,
                    is_valid=self._validate_backup_file(backup_file)
                ))
            except Exception as e:
                self.logger.warning(f"Error reading backup {backup_file}: {e}")
        return backups

    def _backup_entries(self) -> List[Tuple[str, Path, datetime]]:
        """
        ``(version, path, timestamp)`` for every ``config.json.backup.<version>``
        in the backup directory, newest first. Names only -- no file is opened,
        so rotation can call this on every save without re-parsing each backup.
        """
        entries = []
        if not self.backup_dir.exists():
            return entries

        prefix = f"{self.config_path.name}.backup."
        for backup_file in self.backup_dir.glob(f"{prefix}*"):
            # The version reported here is what rollback_config() matches
            # against, so it has to be the exact string in the filename.
            #
            # It did not used to be. This read .stem, which drops only the
            # last dot-component, so for config.json.backup.20240101_120000
            # parts was ['config', 'json', 'backup'] and parts[-2] was
            # 'json' -- never 'backup'. The filename branch could not be
            # reached, every backup fell through to the mtime fallback, and
            # the version was a second-granularity restamp of the mtime
            # rather than the name on disk. Two backups a second apart could
            # therefore report the same version, and rollback would pick
            # whichever the glob happened to yield first.
            # Strip the exact prefix the glob just matched, so a config
            # whose own name contains '.backup.' can't shift the split.
            version = backup_file.name[len(prefix):]
            timestamp = self._parse_backup_version(version)
            if timestamp is None:
                # Not a version this code wrote (hand-copied, renamed).
                # Order it by mtime, but keep the on-disk version string so
                # it can still be named in a rollback.
                try:
                    timestamp = datetime.fromtimestamp(backup_file.stat().st_mtime)
                except OSError as e:
                    self.logger.warning(f"Error reading backup {backup_file}: {e}")
                    continue
            entries.append((version, backup_file, timestamp))

        entries.sort(key=lambda entry: entry[2], reverse=True)
        return entries
    
    @staticmethod
    def _parse_backup_version(version: str) -> Optional[datetime]:
        """
        Parse the ``<version>`` of a ``config.json.backup.<version>`` filename
        into the time the backup was taken, or None if it is not a version this
        class wrote.

        Accepts the current microsecond format and the legacy second-granularity
        one, with or without the ``-N`` suffix _create_backup() appends to break
        a collision. When that suffix is present, N is folded into the result
        as extra microseconds so same-tick collisions still sort in the order
        they were created rather than tying.
        """
        if not version:
            return None
        base = version
        collision = 0
        match = _BACKUP_COLLISION_SUFFIX_RE.match(version)
        if match:
            base = match.group('base')
            collision = int(match.group('collision'))
        for fmt in (BACKUP_VERSION_FORMAT, LEGACY_BACKUP_VERSION_FORMAT):
            try:
                parsed = datetime.strptime(base, fmt)
            except ValueError:
                continue
            return parsed + timedelta(microseconds=collision) if collision else parsed
        return None

    def validate_config_file(self, config_path: Optional[str] = None) -> ValidationResult:
        """
        Validate a configuration file.
        
        Args:
            config_path: Path to config file. If None, validates current config_path.
        
        Returns:
            ValidationResult with validation status and errors
        """
        path = Path(config_path) if config_path else self.config_path
        return self._validate_config_file(path)
    
    def _create_backup(self) -> Optional[str]:
        """Create a backup of the current configuration file."""
        if not self.config_path.exists():
            self.logger.warning(f"Config file {self.config_path} does not exist, skipping backup")
            return None
        
        try:
            # Generate backup filename with timestamp.
            #
            # This id is the backup's identity: save_config_atomic() returns the
            # path, rollback_config(backup_version=...) looks the version up, and
            # the paired secrets backup is found by reusing the same string. At
            # second granularity two saves inside the same second produced the
            # same filename, so the second copy2() below silently overwrote the
            # first backup -- the path a caller was still holding then pointed at
            # different content, and rolling back to it restored the wrong
            # config. Microseconds make that collision vanishingly unlikely.
            config_name = self.config_path.name
            backup_secrets = bool(self.secrets_path and self.secrets_path.exists())

            # exists() then copy2() is two steps: two concurrent callers can
            # both see the path as free and pick the same one, so the second
            # copy2() silently destroys the first call's restore point.
            # Reserve the filename(s) with exclusive creation instead -- that
            # is atomic, so only one caller can ever win a given timestamp.
            # Each retry bumps the collision suffix, so this always
            # terminates and stays compatible with _parse_backup_version().
            collision = 0
            while True:
                timestamp = datetime.now().strftime(BACKUP_VERSION_FORMAT)
                if collision:
                    timestamp = f"{timestamp}-{collision}"
                backup_path = self.backup_dir / f"{config_name}.backup.{timestamp}"
                secrets_backup_path = (
                    self.backup_dir / f"{self.secrets_path.name}.backup.{timestamp}"
                    if backup_secrets else None
                )
                try:
                    backup_path.touch(exist_ok=False)
                except FileExistsError:
                    collision += 1
                    continue
                if secrets_backup_path is not None:
                    try:
                        secrets_backup_path.touch(exist_ok=False)
                    except FileExistsError:
                        backup_path.unlink(missing_ok=True)
                        collision += 1
                        continue
                break

            # Copy config file to backup
            shutil.copy2(self.config_path, backup_path)

            # Also backup secrets file if it exists
            if secrets_backup_path is not None:
                shutil.copy2(self.secrets_path, secrets_backup_path)
            
            # Rotate old backups
            self._rotate_backups()
            
            return str(backup_path)
            
        except Exception as e:
            self.logger.error(f"Error creating backup: {e}", exc_info=True)
            return None
    
    def _serialize(
        self,
        config_data: Dict[str, Any],
        secrets_data: Optional[Dict[str, Any]] = None
    ) -> Tuple[str, Optional[str]]:
        """
        Serialize both files before either is written, so a value json can't
        encode fails the save before anything on disk changes.
        
        Returns:
            Tuple of (config_text, secrets_text); secrets_text is None when
            there is no secrets file to write or its content is unchanged.
        """
        try:
            config_text = json.dumps(config_data, indent=4)
        except (TypeError, ValueError) as e:
            raise ConfigError(f"Error serializing config: {e}") from e

        if secrets_data is None or not self.secrets_path or self._secrets_unchanged(secrets_data):
            return config_text, None
        try:
            return config_text, json.dumps(secrets_data, indent=4)
        except (TypeError, ValueError) as e:
            raise ConfigError(f"Error serializing secrets: {e}") from e

    def _secrets_unchanged(self, secrets_data: Dict[str, Any]) -> bool:
        try:
            with open(self.secrets_path, 'r') as f:
                return json.load(f) == secrets_data
        except (OSError, ValueError):
            return False
    
    def _validate_config_file(self, config_path: Path) -> ValidationResult:
        """
        Validate a configuration file.
        
        Checks:
        - File exists and is readable
        - Valid JSON format
        - Can be parsed successfully
        """
        if not config_path.exists():
            return ValidationResult(
                is_valid=False,
                errors=[f"Config file does not exist: {config_path}"],
                warnings=[]
            )
        try:
            with open(config_path, 'r') as f:
                text = f.read()
        except Exception as e:
            return ValidationResult(
                is_valid=False,
                errors=[f"Error reading config file: {str(e)}"],
                warnings=[]
            )
        return self._validate_config_text(text)

    @staticmethod
    def _validate_config_text(text: str) -> ValidationResult:
        """Validate serialized configuration: parseable JSON holding an object."""
        errors = []
        warnings = []
        try:
            data = json.loads(text)
            if not isinstance(data, dict):
                errors.append("Configuration must be a JSON object")
            if not data:
                warnings.append("Configuration file is empty")
        except json.JSONDecodeError as e:
            errors.append(f"Invalid JSON: {str(e)}")
        except Exception as e:
            errors.append(f"Error reading config file: {str(e)}")
        return ValidationResult(
            is_valid=len(errors) == 0,
            errors=errors,
            warnings=warnings
        )
    
    def _validate_backup_file(self, backup_path: Path) -> bool:
        """Validate that a backup file is readable and valid JSON."""
        try:
            result = self._validate_config_file(backup_path)
            return result.is_valid
        except Exception:
            return False
    
    def _rollback_from_backup(self, backup_path: str) -> bool:
        """
        Rollback configuration from a backup file.
        
        The backup is written back with atomic_write_text, so a failure
        partway through a restore can't truncate the live config either.
        
        Args:
            backup_path: Path to backup file to restore
        
        Returns:
            True if rollback successful
        """
        backup_file = Path(backup_path)
        
        if not backup_file.exists():
            self.logger.error(f"Backup file not found: {backup_path}")
            return False
        
        try:
            with open(backup_file, 'r') as f:
                config_text = f.read()
        except Exception as e:
            self.logger.error(f"Error reading backup {backup_path}: {e}", exc_info=True)
            return False
        
        if not self._validate_config_text(config_text).is_valid:
            self.logger.error(f"Backup file is invalid: {backup_path}")
            return False
        
        try:
            atomic_write_text(self.config_path, config_text)
            self.logger.info(f"Restored config from backup: {backup_path}")
            
            secrets_backup_path = self._paired_secrets_backup(backup_file)
            if secrets_backup_path is not None and secrets_backup_path.exists():
                with open(secrets_backup_path, 'r') as f:
                    atomic_write_text(self.secrets_path, f.read())
                self.logger.info(f"Restored secrets from backup: {secrets_backup_path}")
            
            return True
            
        except Exception as e:
            self.logger.error(f"Error during rollback: {e}", exc_info=True)
            return False

    def _paired_secrets_backup(self, backup_file: Path) -> Optional[Path]:
        """The config_secrets.json.backup.<version> taken alongside a config backup."""
        if not self.secrets_path or '.backup.' not in backup_file.name:
            return None
        version = backup_file.name.split('.backup.')[-1]
        return self.backup_dir / f"{self.secrets_path.name}.backup.{version}"
    
    def _rotate_backups(self) -> None:
        """Remove old backups, keeping only the most recent N backups."""
        for _, backup_file, _ in self._backup_entries()[self.max_backups:]:
            try:
                backup_file.unlink()
                self.logger.debug(f"Removed old backup: {backup_file}")
                
                secrets_backup_path = self._paired_secrets_backup(backup_file)
                if secrets_backup_path is not None and secrets_backup_path.exists():
                    secrets_backup_path.unlink()
                            
            except Exception as e:
                self.logger.warning(f"Error removing old backup {backup_file}: {e}")
