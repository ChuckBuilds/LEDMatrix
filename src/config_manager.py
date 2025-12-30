import json
import os
import logging
from pathlib import Path
from typing import Dict, Any, Optional, List
from src.exceptions import ConfigError
from src.logging_config import get_logger
from src.config_manager_atomic import (
    AtomicConfigManager, SaveResult, SaveResultStatus,
    BackupInfo, ValidationResult
)
from src.common.permission_utils import (
    ensure_directory_permissions,
    ensure_file_permissions,
    get_config_file_mode,
    get_config_dir_mode
)

class ConfigManager:
    def __init__(self, config_path: Optional[str] = None, secrets_path: Optional[str] = None) -> None:
        # Use current working directory as base
        self.config_path: str = config_path or "config/config.json"
        self.secrets_path: str = secrets_path or "config/config_secrets.json"
        self.template_path: str = "config/config.template.json"
        self.config: Dict[str, Any] = {}
        self.logger: logging.Logger = get_logger(__name__)
        
        # Initialize atomic config manager
        self._atomic_manager: Optional[AtomicConfigManager] = None

    def get_config_path(self) -> str:
        return self.config_path

    def get_secrets_path(self) -> str:
        return self.secrets_path
    
    def _get_atomic_manager(self) -> AtomicConfigManager:
        """Get or create atomic config manager instance."""
        if self._atomic_manager is None:
            self._atomic_manager = AtomicConfigManager(
                config_path=self.config_path,
                secrets_path=self.secrets_path
            )
        return self._atomic_manager
    
    def save_config_atomic(
        self,
        new_config_data: Dict[str, Any],
        create_backup: bool = True,
        validate_after_write: bool = True
    ) -> SaveResult:
        """
        Save configuration atomically with backup and rollback support.
        
        This method provides atomic file operations to prevent corruption
        and enables recovery from failed saves.
        
        Args:
            new_config_data: New configuration data to save
            create_backup: Whether to create backup before saving (default: True)
            validate_after_write: Whether to validate after writing (default: True)
            
        Returns:
            SaveResult with status and details
        """
        # Load current secrets to preserve them
        secrets_content = {}
        if os.path.exists(self.secrets_path):
            try:
                with open(self.secrets_path, 'r') as f_secrets:
                    secrets_content = json.load(f_secrets)
            except Exception as e:
                self.logger.warning(f"Could not load secrets file {self.secrets_path} during save: {e}")
        
        # Strip secrets from main config before saving
        config_to_write = self._strip_secrets_recursive(new_config_data, secrets_content)
        
        # Use atomic manager to save
        atomic_mgr = self._get_atomic_manager()
        result = atomic_mgr.save_config_atomic(
            new_config=config_to_write,
            new_secrets=secrets_content if secrets_content else None,
            create_backup=create_backup,
            validate_after_write=validate_after_write
        )
        
        # Update in-memory config if save was successful
        if result.status == SaveResultStatus.SUCCESS:
            self.config = new_config_data
            self.logger.info(f"Configuration successfully saved atomically to {os.path.abspath(self.config_path)}")
        elif result.status == SaveResultStatus.ROLLED_BACK:
            # Reload config from file after rollback
            try:
                self.load_config()
            except Exception as e:
                self.logger.error(f"Error reloading config after rollback: {e}")
        
        return result
    
    def rollback_config(self, backup_version: Optional[str] = None) -> bool:
        """
        Rollback configuration to a previous backup.
        
        Args:
            backup_version: Specific backup version to restore (timestamp string).
                          If None, restores most recent backup.
        
        Returns:
            True if rollback successful, False otherwise
        """
        atomic_mgr = self._get_atomic_manager()
        success = atomic_mgr.rollback_config(backup_version)
        
        if success:
            # Reload config after rollback
            try:
                self.load_config()
            except Exception as e:
                self.logger.error(f"Error reloading config after rollback: {e}")
                return False
        
        return success
    
    def list_backups(self) -> List[BackupInfo]:
        """
        List all available configuration backups.
        
        Returns:
            List of BackupInfo objects, sorted by timestamp (newest first)
        """
        atomic_mgr = self._get_atomic_manager()
        return atomic_mgr.list_backups()
    
    def validate_config_file(self, config_path: Optional[str] = None) -> ValidationResult:
        """
        Validate a configuration file.
        
        Args:
            config_path: Path to config file. If None, validates current config_path.
        
        Returns:
            ValidationResult with validation status and errors
        """
        atomic_mgr = self._get_atomic_manager()
        return atomic_mgr.validate_config_file(config_path)

    def load_config(self) -> Dict[str, Any]:
        """Load configuration from JSON files."""
        try:
            # Check if config file exists, if not create from template
            if not os.path.exists(self.config_path):
                self._create_config_from_template()
            
            # Load main config
            self.logger.info(f"Attempting to load config from: {os.path.abspath(self.config_path)}")
            with open(self.config_path, 'r') as f:
                self.config = json.load(f)

            # Migrate config to add any new items from template
            self._migrate_config()

            # Load and merge secrets if they exist (be permissive on errors)
            if os.path.exists(self.secrets_path):
                try:
                    with open(self.secrets_path, 'r') as f:
                        secrets = json.load(f)
                        # Deep merge secrets into config
                        self._deep_merge(self.config, secrets)
                except PermissionError as e:
                    self.logger.warning(f"Secrets file not readable ({self.secrets_path}): {e}. Continuing without secrets.")
                except (json.JSONDecodeError, OSError) as e:
                    self.logger.warning(f"Error reading secrets file ({self.secrets_path}): {e}. Continuing without secrets.")
            
            return self.config
            
        except FileNotFoundError as e:
            if str(e).find('config_secrets.json') == -1:  # Only raise if main config is missing
                error_msg = f"Configuration file not found at {os.path.abspath(self.config_path)}"
                self.logger.error(error_msg, exc_info=True)
                raise ConfigError(error_msg, config_path=self.config_path) from e
            return self.config
        except json.JSONDecodeError as e:
            error_msg = f"Error parsing configuration file {os.path.abspath(self.config_path)}"
            self.logger.error(error_msg, exc_info=True)
            raise ConfigError(error_msg, config_path=self.config_path) from e
        except (IOError, OSError, PermissionError) as e:
            error_msg = f"Error loading configuration from {os.path.abspath(self.config_path)}"
            self.logger.error(error_msg, exc_info=True)
            raise ConfigError(error_msg, config_path=self.config_path) from e
        except Exception as e:
            error_msg = f"Unexpected error loading configuration: {str(e)}"
            self.logger.error(error_msg, exc_info=True)
            raise ConfigError(error_msg, config_path=self.config_path) from e

    def _strip_secrets_recursive(self, data_to_filter: Dict[str, Any], secrets: Dict[str, Any]) -> Dict[str, Any]:
        """Recursively remove secret keys from a dictionary."""
        result = {}
        for key, value in data_to_filter.items():
            if key in secrets:
                if isinstance(value, dict) and isinstance(secrets[key], dict):
                    # This key is a shared group, recurse
                    stripped_sub_dict = self._strip_secrets_recursive(value, secrets[key])
                    if stripped_sub_dict: # Only add if there's non-secret data left
                        result[key] = stripped_sub_dict
                # Else, it's a secret key at this level, so we skip it
            else:
                # This key is not in secrets, so we keep it
                result[key] = value
        return result

    def save_config(self, new_config_data: Dict[str, Any]) -> None:
        """Save configuration to the main JSON file, stripping out secrets."""
        secrets_content = {}
        if os.path.exists(self.secrets_path):
            try:
                with open(self.secrets_path, 'r') as f_secrets:
                    secrets_content = json.load(f_secrets)
            except Exception as e:
                self.logger.warning(f"Could not load secrets file {self.secrets_path} during save: {e}")
                # Continue without stripping if secrets can't be loaded, or handle as critical error
                # For now, we'll proceed cautiously and save the full new_config_data if secrets are unreadable
                # to prevent accidental data loss if the secrets file is temporarily corrupt.
                # A more robust approach might be to fail the save or use a cached version of secrets.

        config_to_write = self._strip_secrets_recursive(new_config_data, secrets_content)

        try:
            with open(self.config_path, 'w') as f:
                json.dump(config_to_write, f, indent=4)
            
            # Update the in-memory config to the new state (which includes secrets for runtime)
            self.config = new_config_data 
            self.logger.info(f"Configuration successfully saved to {os.path.abspath(self.config_path)}")
            if secrets_content:
                 self.logger.info("Secret values were preserved in memory and not written to the main config file.")

        except (IOError, OSError, PermissionError) as e:
            error_msg = f"Error writing configuration to file {os.path.abspath(self.config_path)}"
            self.logger.error(error_msg, exc_info=True)
            raise ConfigError(error_msg, config_path=self.config_path) from e
        except Exception as e:
            error_msg = f"Unexpected error occurred while saving configuration: {str(e)}"
            self.logger.error(error_msg, exc_info=True)
            raise ConfigError(error_msg, config_path=self.config_path) from e

    def get_secret(self, key: str) -> Optional[Any]:
        """Get a secret value by key."""
        try:
            if not os.path.exists(self.secrets_path):
                return None
            with open(self.secrets_path, 'r') as f:
                secrets = json.load(f)
                return secrets.get(key)
        except (json.JSONDecodeError, IOError) as e:
            self.logger.error(f"Error reading secrets file: {e}")
            return None

    def _deep_merge(self, target: Dict[str, Any], source: Dict[str, Any]) -> None:
        """Deep merge source dict into target dict."""
        for key, value in source.items():
            if key in target and isinstance(target[key], dict) and isinstance(value, dict):
                self._deep_merge(target[key], value)
            else:
                target[key] = value

    def _create_config_from_template(self) -> None:
        """Create config.json from template if it doesn't exist."""
        if not os.path.exists(self.template_path):
            error_msg = f"Template file not found at {os.path.abspath(self.template_path)}"
            self.logger.error(error_msg)
            raise ConfigError(error_msg, config_path=self.template_path)
        
        self.logger.info(f"Creating config.json from template at {os.path.abspath(self.template_path)}")
        
        # Ensure config directory exists with proper permissions
        config_dir = Path(self.config_path).parent
        ensure_directory_permissions(config_dir, get_config_dir_mode())
        
        # Copy template to config
        with open(self.template_path, 'r') as template_file:
            template_data = json.load(template_file)
        
        with open(self.config_path, 'w') as config_file:
            json.dump(template_data, config_file, indent=4)
        
        # Set proper file permissions after creation
        config_path_obj = Path(self.config_path)
        ensure_file_permissions(config_path_obj, get_config_file_mode(config_path_obj))
        
        self.logger.info(f"Created config.json from template at {os.path.abspath(self.config_path)}")

    def _migrate_config(self) -> None:
        """Migrate config to add new items from template with defaults."""
        if not os.path.exists(self.template_path):
            self.logger.warning(f"Template file not found at {os.path.abspath(self.template_path)}, skipping migration")
            return
        
        try:
            with open(self.template_path, 'r') as f:
                template_config = json.load(f)
            
            # Check if migration is needed
            if self._config_needs_migration(self.config, template_config):
                self.logger.info("Config migration needed - adding new configuration items with defaults")
                
                # Create backup of current config
                backup_path = f"{self.config_path}.backup"
                with open(backup_path, 'w') as backup_file:
                    json.dump(self.config, backup_file, indent=4)
                self.logger.info(f"Created backup of current config at {os.path.abspath(backup_path)}")
                
                # Merge template defaults into current config
                self._merge_template_defaults(self.config, template_config)
                
                # Save migrated config using atomic save to preserve permissions
                # Load secrets if they exist to pass to atomic save
                secrets_content = {}
                if os.path.exists(self.secrets_path):
                    try:
                        with open(self.secrets_path, 'r') as f_secrets:
                            secrets_content = json.load(f_secrets)
                    except Exception:
                        pass  # Continue without secrets if can't load
                
                # Use atomic save to preserve file permissions
                # Note: save_config_atomic handles secrets internally, no need to pass new_secrets
                result = self.save_config_atomic(
                    new_config_data=self.config,
                    create_backup=False,  # Already created backup above
                    validate_after_write=False  # Skip validation for migration
                )
                
                if result.status.value == "success":
                    self.logger.info(f"Config migration completed and saved to {os.path.abspath(self.config_path)}")
                else:
                    self.logger.warning(f"Config migration completed but save had issues: {result.message}")
            else:
                self.logger.debug("Config is up to date, no migration needed")
                
        except Exception as e:
            self.logger.error(f"Error during config migration: {e}")
            # Don't raise - continue with current config

    def _config_needs_migration(self, current_config: Dict[str, Any], template_config: Dict[str, Any]) -> bool:
        """Check if config needs migration by comparing with template."""
        return self._has_new_keys(current_config, template_config)

    def _has_new_keys(self, current: Dict[str, Any], template: Dict[str, Any]) -> bool:
        """Recursively check if template has keys not in current config."""
        for key, value in template.items():
            if key not in current:
                return True
            if isinstance(value, dict) and isinstance(current[key], dict):
                if self._has_new_keys(current[key], value):
                    return True
        return False

    def _merge_template_defaults(self, current: Dict[str, Any], template: Dict[str, Any]) -> None:
        """Recursively merge template defaults into current config."""
        for key, value in template.items():
            if key not in current:
                # Add new key with template value
                current[key] = value
                self.logger.debug(f"Added new config key: {key}")
            elif isinstance(value, dict) and isinstance(current[key], dict):
                # Recursively merge nested dictionaries
                self._merge_template_defaults(current[key], value)

    def get_timezone(self) -> str:
        """Get the configured timezone."""
        return self.config.get('timezone', 'UTC')

    def get_display_config(self) -> Dict[str, Any]:
        """Get display configuration."""
        return self.config.get('display', {})

    def get_clock_config(self) -> Dict[str, Any]:
        """Get clock configuration."""
        return self.config.get('clock', {})

    def get_config(self) -> Dict[str, Any]:
        """Get the full configuration dictionary.
        
        Returns:
            The complete configuration dictionary. If config hasn't been loaded yet,
            it will be loaded first.
        """
        if not self.config:
            self.load_config()
        return self.config

    def get_raw_file_content(self, file_type: str) -> Dict[str, Any]:
        """Load raw content of 'main' config or 'secrets' config file."""
        path_to_load = ""
        if file_type == "main":
            path_to_load = self.config_path
        elif file_type == "secrets":
            path_to_load = self.secrets_path
        else:
            raise ValueError("Invalid file_type specified. Must be 'main' or 'secrets'.")

        if not os.path.exists(path_to_load):
            # If a secrets file doesn't exist, it's not an error, just return empty
            if file_type == "secrets":
                return {}
            error_msg = f"{file_type.capitalize()} configuration file not found at {os.path.abspath(path_to_load)}"
            self.logger.error(error_msg)
            raise ConfigError(error_msg, config_path=path_to_load)

        try:
            with open(path_to_load, 'r') as f:
                return json.load(f)
        except json.JSONDecodeError as e:
            error_msg = f"Error parsing {file_type} configuration file: {path_to_load}"
            self.logger.error(error_msg, exc_info=True)
            raise ConfigError(error_msg, config_path=path_to_load) from e
        except (IOError, OSError, PermissionError) as e:
            error_msg = f"Error loading {file_type} configuration file {path_to_load}: {str(e)}"
            self.logger.error(error_msg, exc_info=True)
            raise ConfigError(error_msg, config_path=path_to_load) from e
        except Exception as e:
            error_msg = f"Unexpected error loading {file_type} configuration file {path_to_load}: {str(e)}"
            self.logger.error(error_msg, exc_info=True)
            raise ConfigError(error_msg, config_path=path_to_load) from e

    def save_raw_file_content(self, file_type: str, data: Dict[str, Any]) -> None:
        """Save data directly to 'main' config or 'secrets' config file."""
        path_to_save = ""
        if file_type == "main":
            path_to_save = self.config_path
        elif file_type == "secrets":
            path_to_save = self.secrets_path
        else:
            raise ValueError("Invalid file_type specified. Must be 'main' or 'secrets'.")

        try:
            # Create directory if it doesn't exist, especially for config/
            path_obj = Path(path_to_save)
            ensure_directory_permissions(path_obj.parent, get_config_dir_mode())
            
            # Write the file - let the actual write operation determine if it succeeds
            with open(path_to_save, 'w') as f:
                json.dump(data, f, indent=4)
            
            # Set proper file permissions after writing
            try:
                ensure_file_permissions(path_obj, get_config_file_mode(path_obj))
            except OSError as perm_error:
                # If we can't set permissions but file was written, log warning but don't fail
                self.logger.warning(
                    f"File {path_to_save} was written successfully but could not set permissions: {perm_error}. "
                    f"This may cause issues if the file needs to be accessible by other users."
                )
            
            self.logger.info(f"{file_type.capitalize()} configuration successfully saved to {os.path.abspath(path_to_save)}")
            
            # If we just saved the main config or secrets, the merged self.config might be stale.
            # Reload it to reflect the new state.
            # Note: We wrap this in try-except because reload failures (e.g., migration errors)
            # should not cause the save operation to fail - the file was saved successfully.
            if file_type == "main" or file_type == "secrets":
                try:
                    self.load_config()
                except Exception as reload_error:
                    # Log the reload error but don't fail the save operation
                    # The file was saved successfully, reload is just for in-memory consistency
                    self.logger.warning(
                        f"Configuration file saved successfully, but reload failed: {reload_error}. "
                        f"The file on disk is valid, but in-memory config may be stale."
                    )

        except PermissionError as e:
            # Provide helpful error message with fix instructions
            import stat
            try:
                import pwd
                if path_obj.exists():
                    file_stat = path_obj.stat()
                    current_mode = stat.filemode(file_stat.st_mode)
                    try:
                        file_owner = pwd.getpwuid(file_stat.st_uid).pw_name
                    except (ImportError, KeyError):
                        file_owner = f"UID {file_stat.st_uid}"
                    error_msg = (
                        f"Cannot write to {file_type} configuration file {os.path.abspath(path_to_save)}. "
                        f"File is owned by {file_owner} with permissions {current_mode}. "
                        f"To fix, run: sudo chown $USER:$(id -gn) {path_to_save} && sudo chmod 664 {path_to_save}"
                    )
                else:
                    # File doesn't exist - check directory permissions
                    dir_stat = path_obj.parent.stat()
                    dir_mode = stat.filemode(dir_stat.st_mode)
                    try:
                        dir_owner = pwd.getpwuid(dir_stat.st_uid).pw_name
                    except (ImportError, KeyError):
                        dir_owner = f"UID {dir_stat.st_uid}"
                    error_msg = (
                        f"Cannot create {file_type} configuration file {os.path.abspath(path_to_save)}. "
                        f"Directory is owned by {dir_owner} with permissions {dir_mode}. "
                        f"To fix, run: sudo chown $USER:$(id -gn) {path_obj.parent} && sudo chmod 775 {path_obj.parent}"
                    )
            except Exception:
                # Fallback to generic message if we can't get file info
                error_msg = f"Error writing {file_type} configuration to file {os.path.abspath(path_to_save)}: {str(e)}"
            self.logger.error(error_msg, exc_info=True)
            raise ConfigError(error_msg, config_path=path_to_save) from e
        except (IOError, OSError) as e:
            error_msg = f"Error writing {file_type} configuration to file {os.path.abspath(path_to_save)}: {str(e)}"
            self.logger.error(error_msg, exc_info=True)
            raise ConfigError(error_msg, config_path=path_to_save) from e
        except Exception as e:
            error_msg = f"Unexpected error occurred while saving {file_type} configuration: {str(e)}"
            self.logger.error(error_msg, exc_info=True)
            raise ConfigError(error_msg, config_path=path_to_save) from e
    
    def cleanup_plugin_config(self, plugin_id: str, remove_secrets: bool = True) -> None:
        """
        Remove plugin configuration from both main config and secrets config.
        
        Args:
            plugin_id: Plugin identifier to remove
            remove_secrets: If True, also remove plugin secrets
        """
        try:
            # Load current configs
            main_config = self.get_raw_file_content('main')
            secrets_config = self.get_raw_file_content('secrets') if os.path.exists(self.secrets_path) else {}
            
            # Remove plugin from main config
            if plugin_id in main_config:
                del main_config[plugin_id]
                self.save_raw_file_content('main', main_config)
                self.logger.info(f"Removed plugin {plugin_id} from main configuration")
            
            # Remove plugin from secrets config if requested
            if remove_secrets and plugin_id in secrets_config:
                del secrets_config[plugin_id]
                self.save_raw_file_content('secrets', secrets_config)
                self.logger.info(f"Removed plugin {plugin_id} from secrets configuration")
                
        except Exception as e:
            error_msg = f"Error cleaning up plugin config for {plugin_id}"
            self.logger.error(error_msg, exc_info=True)
            raise ConfigError(error_msg, config_path=self.config_path, field=plugin_id) from e
    
    def cleanup_orphaned_plugin_configs(self, valid_plugin_ids: List[str]) -> List[str]:
        """
        Remove configuration sections for plugins that are no longer installed.
        
        Args:
            valid_plugin_ids: List of currently installed plugin IDs
            
        Returns:
            List of plugin IDs that were removed
        """
        removed = []
        try:
            # Load current configs
            main_config = self.get_raw_file_content('main')
            secrets_config = self.get_raw_file_content('secrets') if os.path.exists(self.secrets_path) else {}
            
            valid_set = set(valid_plugin_ids)
            
            # Find orphaned plugins in main config
            main_plugins = set(main_config.keys())
            orphaned_main = main_plugins - valid_set
            
            # Find orphaned plugins in secrets config
            secrets_plugins = set(secrets_config.keys())
            orphaned_secrets = secrets_plugins - valid_set
            
            all_orphaned = orphaned_main | orphaned_secrets
            
            if all_orphaned:
                # Remove from main config
                for plugin_id in orphaned_main:
                    del main_config[plugin_id]
                    removed.append(plugin_id)
                
                # Remove from secrets config
                for plugin_id in orphaned_secrets:
                    del secrets_config[plugin_id]
                
                # Save updated configs
                if orphaned_main:
                    self.save_raw_file_content('main', main_config)
                if orphaned_secrets:
                    self.save_raw_file_content('secrets', secrets_config)
                
                self.logger.info(f"Cleaned up orphaned plugin configs: {', '.join(all_orphaned)}")
            
            return removed
            
        except Exception as e:
            self.logger.error(f"Error cleaning up orphaned plugin configs: {e}")
            return removed
    
    def validate_all_plugin_configs(self, plugin_schema_manager=None) -> Dict[str, Dict[str, Any]]:
        """
        Validate all plugin configurations against their schemas.
        
        Args:
            plugin_schema_manager: Optional SchemaManager instance for validation
            
        Returns:
            Dict mapping plugin_id to validation results: {
                'valid': bool,
                'errors': list of error messages
            }
        """
        results = {}
        
        if not plugin_schema_manager:
            return results
        
        try:
            main_config = self.get_raw_file_content('main')
            
            for plugin_id, plugin_config in main_config.items():
                if not isinstance(plugin_config, dict):
                    continue
                
                # Skip non-plugin config sections
                if plugin_id in ['display', 'schedule', 'timezone', 'plugin_system']:
                    continue
                
                schema = plugin_schema_manager.load_schema(plugin_id, use_cache=True)
                if schema:
                    is_valid, errors = plugin_schema_manager.validate_config_against_schema(
                        plugin_config, schema, plugin_id
                    )
                    results[plugin_id] = {
                        'valid': is_valid,
                        'errors': errors
                    }
                else:
                    results[plugin_id] = {
                        'valid': True,  # No schema = can't validate, but not an error
                        'errors': []
                    }
                    
        except Exception as e:
            self.logger.error(f"Error validating plugin configs: {e}")
        
        return results