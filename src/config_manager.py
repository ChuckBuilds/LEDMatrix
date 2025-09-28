import json
import os
from typing import Dict, Any, Optional
from src.config.config_models import RootConfig, DisplayConfig, ClockConfig
from src.config.secrets_models import SecretsConfig
class ConfigManager:
    def __init__(self, config_path: str | None = None, secrets_path: str | None = None):
        # Use current working directory as base
        self.config_path = config_path or "config/config.json"
        self.secrets_path = secrets_path or "config/config_secrets.json"
        self.template_path = "config/config.template.json"
        self.config: Dict[str, Any] = {}
        self.new_config: RootConfig = RootConfig()
        self.new_secrets_config: SecretsConfig = SecretsConfig()

    def get_config_path(self) -> str:
        return self.config_path

    def get_secrets_path(self) -> str:
        return self.secrets_path

    def load_new_config(self) -> tuple[RootConfig, SecretsConfig]:
        """Load configuration from JSON files."""
        try:
            # Check if config file exists, if not create from template
            if not os.path.exists(self.config_path):
                self._create_config_from_template()
            
            # Load main config
            print(f"Attempting to load config from: {os.path.abspath(self.config_path)}")
            with open(self.config_path, 'r') as f:
                self.new_config = RootConfig(**json.load(f))

            # Load and merge secrets if they exist (be permissive on errors)
            if os.path.exists(self.secrets_path):
                try:
                    with open(self.secrets_path, 'r') as f:
                        self.new_secrets_config = SecretsConfig(**json.load(f))
                except PermissionError as e:
                    print(f"Secrets file not readable ({self.secrets_path}): {e}. Continuing without secrets.")
                except (json.JSONDecodeError, OSError) as e:
                    print(f"Error reading secrets file ({self.secrets_path}): {e}. Continuing without secrets.")
            
            return self.new_config, self.new_secrets_config
            
        except FileNotFoundError as e:
            if str(e).find('config_secrets.json') == -1:  # Only raise if main config is missing
                print(f"Configuration file not found at {os.path.abspath(self.config_path)}")
                raise
        except json.JSONDecodeError:
            print("Error parsing configuration file")
            raise
        except Exception as e:
            print(f"Error loading configuration: {str(e)}")
            raise
        return RootConfig(), SecretsConfig()


    def load_config(self) -> Dict[str, Any]:
        """Load configuration from JSON files."""
        try:
            # Check if config file exists, if not create from template
            if not os.path.exists(self.config_path):
                self._create_config_from_template()
            
            # Load main config
            print(f"Attempting to load config from: {os.path.abspath(self.config_path)}")
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
                    print(f"Secrets file not readable ({self.secrets_path}): {e}. Continuing without secrets.")
                except (json.JSONDecodeError, OSError) as e:
                    print(f"Error reading secrets file ({self.secrets_path}): {e}. Continuing without secrets.")
            
            return self.config
            
        except FileNotFoundError as e:
            if str(e).find('config_secrets.json') == -1:  # Only raise if main config is missing
                print(f"Configuration file not found at {os.path.abspath(self.config_path)}")
                raise
            return self.config
        except json.JSONDecodeError:
            print("Error parsing configuration file")
            raise
        except Exception as e:
            print(f"Error loading configuration: {str(e)}")
            raise

    def save_config(self, new_config_data: Dict[str, Any]) -> None:
        """Save configuration to the main JSON file, stripping out secrets."""
        try:
            with open(self.config_path, 'w') as f:
                json.dump(new_config_data, f, indent=4)
            
            # Update the in-memory config to the new state (which includes secrets for runtime)
            self.config = new_config_data
            print(f"Configuration successfully saved to {os.path.abspath(self.config_path)}")
        except IOError as e:
            print(f"Error writing configuration to file {os.path.abspath(self.config_path)}: {e}")
            raise
        except Exception as e:
            print(f"An unexpected error occurred while saving configuration: {str(e)}")
            raise

    def get_secret(self, key: str) -> Optional[Any]:
        """Get a secret value by key."""
        try:
            if not os.path.exists(self.secrets_path):
                return None
            with open(self.secrets_path, 'r') as f:
                secrets = json.load(f)
                return secrets.get(key)
        except (json.JSONDecodeError, IOError) as e:
            print(f"Error reading secrets file: {e}")
            return None

    def _deep_merge(self, target: Dict, source: Dict) -> None:
        """Deep merge source dict into target dict."""
        for key, value in source.items():
            if key in target and isinstance(target[key], dict) and isinstance(value, dict):
                self._deep_merge(target[key], value)
            else:
                target[key] = value

    def _create_config_from_template(self) -> None:
        """Create config.json from template if it doesn't exist."""
        if not os.path.exists(self.template_path):
            raise FileNotFoundError(f"Template file not found at {os.path.abspath(self.template_path)}")
        
        print(f"Creating config.json from template at {os.path.abspath(self.template_path)}")
        
        # Ensure config directory exists
        os.makedirs(os.path.dirname(self.config_path), exist_ok=True)
        
        with open(self.config_path, 'w') as config_file:
            json.dump(RootConfig().model_dump(), config_file, indent=4)
        
        print(f"Created config.json from template at {os.path.abspath(self.config_path)}")

    def _migrate_config(self) -> None:
        """Migrate config to add new items from template with defaults."""
        if not os.path.exists(self.template_path):
            print(f"Template file not found at {os.path.abspath(self.template_path)}, skipping migration")
            return
        
        try:
            with open(self.template_path, 'r') as f:
                template_config = json.load(f)
            
            # Check if migration is needed
            if self._config_needs_migration(self.config, template_config):
                print("Config migration needed - adding new configuration items with defaults")
                
                # Create backup of current config
                backup_path = f"{self.config_path}.backup"
                with open(backup_path, 'w') as backup_file:
                    json.dump(self.config, backup_file, indent=4)
                print(f"Created backup of current config at {os.path.abspath(backup_path)}")
                
                # Merge template defaults into current config
                self._merge_template_defaults(self.config, template_config)
                
                # Save migrated config
                with open(self.config_path, 'w') as f:
                    json.dump(self.config, f, indent=4)
                
                print(f"Config migration completed and saved to {os.path.abspath(self.config_path)}")
            else:
                print("Config is up to date, no migration needed")
                
        except Exception as e:
            print(f"Error during config migration: {e}")
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
                print(f"Added new config key: {key}")
            elif isinstance(value, dict) and isinstance(current[key], dict):
                # Recursively merge nested dictionaries
                self._merge_template_defaults(current[key], value)

    def get_timezone(self) -> str:
        """Get the configured timezone."""
        return self.new_config.timezone

    def get_display_config(self) -> DisplayConfig:
        """Get display configuration."""
        return self.new_config.display

    def get_clock_config(self) -> ClockConfig:
        """Get clock configuration."""
        return self.new_config.clock

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
            os.makedirs(os.path.dirname(path_to_save), exist_ok=True)
            with open(path_to_save, 'w') as f:
                json.dump(data, f, indent=4)
            print(f"{file_type.capitalize()} configuration successfully saved to {os.path.abspath(path_to_save)}")
            
            # If we just saved the main config or secrets, the merged self.config might be stale.
            # Reload it to reflect the new state.
            if file_type == "main" or file_type == "secrets":
                self.load_config()

        except IOError as e:
            print(f"Error writing {file_type} configuration to file {os.path.abspath(path_to_save)}: {e}")
            raise
        except Exception as e:
            print(f"An unexpected error occurred while saving {file_type} configuration: {str(e)}")
            raise 