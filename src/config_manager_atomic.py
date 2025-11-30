"""
Atomic configuration save manager with backup and rollback support.

Provides atomic file operations for configuration files to prevent corruption
and enable recovery from failed saves.
"""

import json
import os
import shutil
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Optional, List, Tuple
from dataclasses import dataclass
from enum import Enum

from src.exceptions import ConfigError
from src.logging_config import get_logger


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
    - Atomic file writes (write to temp, validate, atomic move)
    - Automatic backups before saves
    - Backup rotation (keep last N backups)
    - Rollback functionality
    - Post-write validation
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
            secrets_path: Optional path to secrets file (saved atomically with main config)
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
        2. Write to temporary files
        3. Validate written files
        4. Atomically move temp files to final locations
        5. If validation fails, rollback
        
        Args:
            new_config: New configuration data for main config file
            new_secrets: Optional new secrets data
            create_backup: Whether to create backup before saving
            validate_after_write: Whether to validate after writing
            
        Returns:
            SaveResult with status and details
        """
        backup_path = None
        
        try:
            # Step 1: Create backup if requested
            if create_backup:
                backup_result = self._create_backup()
                if backup_result:
                    backup_path = backup_result
                    self.logger.info(f"Created backup: {backup_path}")
                else:
                    self.logger.warning("Failed to create backup, continuing with save")
            
            # Step 2: Write to temporary files
            temp_config_path, temp_secrets_path = self._write_to_temp_files(
                new_config, new_secrets
            )
            
            # Step 3: Validate written files
            if validate_after_write:
                validation_result = self._validate_config_file(temp_config_path)
                if not validation_result.is_valid:
                    # Clean up temp files
                    self._cleanup_temp_files(temp_config_path, temp_secrets_path)
                    
                    # Rollback if backup was created
                    if backup_path:
                        self._rollback_from_backup(backup_path)
                    
                    return SaveResult(
                        status=SaveResultStatus.VALIDATION_FAILED,
                        message="Configuration validation failed after write",
                        backup_path=backup_path,
                        validation_errors=validation_result.errors
                    )
            
            # Step 4: Atomically move temp files to final locations
            self._atomic_move(temp_config_path, self.config_path)
            if temp_secrets_path and self.secrets_path:
                self._atomic_move(temp_secrets_path, self.secrets_path)
            
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
        
        if not self.backup_dir.exists():
            return backups
        
        # Look for backup files (format: config.json.backup.YYYYMMDD_HHMMSS)
        config_name = self.config_path.name
        backup_pattern = f"{config_name}.backup.*"
        
        for backup_file in self.backup_dir.glob(backup_pattern):
            try:
                # Extract timestamp from filename
                # Format: config.json.backup.20240101_120000
                parts = backup_file.stem.split('.')
                if len(parts) >= 3 and parts[-2] == 'backup':
                    timestamp_str = parts[-1]
                    timestamp = datetime.strptime(timestamp_str, "%Y%m%d_%H%M%S")
                else:
                    # Fallback: use file modification time
                    timestamp = datetime.fromtimestamp(backup_file.stat().st_mtime)
                    timestamp_str = timestamp.strftime("%Y%m%d_%H%M%S")
                
                # Validate backup file
                is_valid = self._validate_backup_file(backup_file)
                
                backup_info = BackupInfo(
                    version=timestamp_str,
                    path=str(backup_file),
                    timestamp=timestamp,
                    size=backup_file.stat().st_size,
                    is_valid=is_valid
                )
                backups.append(backup_info)
                
            except Exception as e:
                self.logger.warning(f"Error reading backup {backup_file}: {e}")
        
        # Sort by timestamp (newest first)
        backups.sort(key=lambda b: b.timestamp, reverse=True)
        
        return backups
    
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
            # Generate backup filename with timestamp
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            config_name = self.config_path.name
            backup_filename = f"{config_name}.backup.{timestamp}"
            backup_path = self.backup_dir / backup_filename
            
            # Copy config file to backup
            shutil.copy2(self.config_path, backup_path)
            
            # Also backup secrets file if it exists
            if self.secrets_path and self.secrets_path.exists():
                secrets_backup_filename = f"{self.secrets_path.name}.backup.{timestamp}"
                secrets_backup_path = self.backup_dir / secrets_backup_filename
                shutil.copy2(self.secrets_path, secrets_backup_path)
            
            # Rotate old backups
            self._rotate_backups()
            
            return str(backup_path)
            
        except Exception as e:
            self.logger.error(f"Error creating backup: {e}", exc_info=True)
            return None
    
    def _write_to_temp_files(
        self,
        config_data: Dict[str, Any],
        secrets_data: Optional[Dict[str, Any]] = None
    ) -> Tuple[Path, Optional[Path]]:
        """
        Write configuration data to temporary files.
        
        Returns:
            Tuple of (temp_config_path, temp_secrets_path)
        """
        # Create temp file in same directory as config (for atomic move)
        temp_config = tempfile.NamedTemporaryFile(
            mode='w',
            dir=self.config_path.parent,
            prefix=f".{self.config_path.name}.tmp.",
            delete=False,
            suffix='.json'
        )
        temp_config_path = Path(temp_config.name)
        
        try:
            json.dump(config_data, temp_config, indent=4)
            temp_config.close()
        except Exception as e:
            temp_config.close()
            if temp_config_path.exists():
                temp_config_path.unlink()
            raise ConfigError(f"Error writing temp config file: {e}") from e
        
        # Write secrets to temp file if provided
        temp_secrets_path = None
        if secrets_data is not None and self.secrets_path:
            temp_secrets = tempfile.NamedTemporaryFile(
                mode='w',
                dir=self.secrets_path.parent,
                prefix=f".{self.secrets_path.name}.tmp.",
                delete=False,
                suffix='.json'
            )
            temp_secrets_path = Path(temp_secrets.name)
            
            try:
                json.dump(secrets_data, temp_secrets, indent=4)
                temp_secrets.close()
            except Exception as e:
                temp_secrets.close()
                if temp_secrets_path.exists():
                    temp_secrets_path.unlink()
                # Clean up config temp file too
                if temp_config_path.exists():
                    temp_config_path.unlink()
                raise ConfigError(f"Error writing temp secrets file: {e}") from e
        
        return temp_config_path, temp_secrets_path
    
    def _atomic_move(self, source: Path, destination: Path) -> None:
        """
        Atomically move a file (rename operation).
        
        On most filesystems, rename is atomic, which prevents corruption
        if the process is interrupted.
        """
        try:
            # Ensure destination directory exists
            destination.parent.mkdir(parents=True, exist_ok=True)
            
            # Atomic move (rename)
            source.replace(destination)
            
        except Exception as e:
            raise ConfigError(f"Error during atomic move: {e}") from e
    
    def _validate_config_file(self, config_path: Path) -> ValidationResult:
        """
        Validate a configuration file.
        
        Checks:
        - File exists and is readable
        - Valid JSON format
        - Can be parsed successfully
        """
        errors = []
        warnings = []
        
        if not config_path.exists():
            errors.append(f"Config file does not exist: {config_path}")
            return ValidationResult(is_valid=False, errors=errors, warnings=warnings)
        
        try:
            with open(config_path, 'r') as f:
                data = json.load(f)
            
            # Basic validation: should be a dict
            if not isinstance(data, dict):
                errors.append("Configuration must be a JSON object")
            
            # Check file is not empty
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
        
        Args:
            backup_path: Path to backup file to restore
        
        Returns:
            True if rollback successful
        """
        backup_file = Path(backup_path)
        
        if not backup_file.exists():
            self.logger.error(f"Backup file not found: {backup_path}")
            return False
        
        # Validate backup before restoring
        if not self._validate_backup_file(backup_file):
            self.logger.error(f"Backup file is invalid: {backup_path}")
            return False
        
        try:
            # Restore main config
            shutil.copy2(backup_file, self.config_path)
            self.logger.info(f"Restored config from backup: {backup_path}")
            
            # Try to restore secrets backup if it exists
            if self.secrets_path:
                # Look for corresponding secrets backup
                # Format: config_secrets.json.backup.TIMESTAMP
                backup_name = backup_file.name
                if '.backup.' in backup_name:
                    timestamp = backup_name.split('.backup.')[-1]
                    secrets_backup_name = f"{self.secrets_path.name}.backup.{timestamp}"
                    secrets_backup_path = self.backup_dir / secrets_backup_name
                    
                    if secrets_backup_path.exists():
                        shutil.copy2(secrets_backup_path, self.secrets_path)
                        self.logger.info(f"Restored secrets from backup: {secrets_backup_path}")
            
            return True
            
        except Exception as e:
            self.logger.error(f"Error during rollback: {e}", exc_info=True)
            return False
    
    def _rotate_backups(self) -> None:
        """Remove old backups, keeping only the most recent N backups."""
        backups = self.list_backups()
        
        if len(backups) <= self.max_backups:
            return
        
        # Sort by timestamp (oldest first) and remove excess
        backups.sort(key=lambda b: b.timestamp)
        backups_to_remove = backups[:-self.max_backups]
        
        for backup in backups_to_remove:
            try:
                Path(backup.path).unlink()
                self.logger.debug(f"Removed old backup: {backup.path}")
                
                # Also remove corresponding secrets backup if it exists
                if self.secrets_path:
                    backup_name = Path(backup.path).name
                    if '.backup.' in backup_name:
                        timestamp = backup_name.split('.backup.')[-1]
                        secrets_backup_name = f"{self.secrets_path.name}.backup.{timestamp}"
                        secrets_backup_path = self.backup_dir / secrets_backup_name
                        if secrets_backup_path.exists():
                            secrets_backup_path.unlink()
                            
            except Exception as e:
                self.logger.warning(f"Error removing old backup {backup.path}: {e}")
    
    def _cleanup_temp_files(self, *temp_paths: Path) -> None:
        """Clean up temporary files."""
        for temp_path in temp_paths:
            if temp_path and temp_path.exists():
                try:
                    temp_path.unlink()
                except Exception as e:
                    self.logger.warning(f"Error cleaning up temp file {temp_path}: {e}")

