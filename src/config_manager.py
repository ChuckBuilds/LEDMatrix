import json
import os
from typing import Dict, Any, Optional

class ConfigManager:
    def __init__(self, config_path: str = None, secrets_path: str = None):
        # Use current working directory as base
        self.config_path = config_path or "config/config.json"
        self.secrets_path = secrets_path or "config/config_secrets.json"
        self.template_path = "config/config.template.json"
        self.config: Dict[str, Any] = {}

    def get_config_path(self) -> str:
        return self.config_path

    def get_secrets_path(self) -> str:
        return self.secrets_path

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
                print(f"Warning: Could not load secrets file {self.secrets_path} during save: {e}")
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
            print(f"Configuration successfully saved to {os.path.abspath(self.config_path)}")
            if secrets_content:
                 print("Secret values were preserved in memory and not written to the main config file.")

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
        
        # Copy template to config
        with open(self.template_path, 'r') as template_file:
            template_data = json.load(template_file)
        
        with open(self.config_path, 'w') as config_file:
            json.dump(template_data, config_file, indent=4)
        
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
        return self.config.get('timezone', 'UTC')

    def get_display_config(self) -> Dict[str, Any]:
        """Get display configuration."""
        return self.config.get('display', {})

    def get_clock_config(self) -> Dict[str, Any]:
        """Get clock configuration."""
        return self.config.get('clock', {})

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
            print(f"{file_type.capitalize()} configuration file not found at {os.path.abspath(path_to_load)}")
            raise FileNotFoundError(f"{file_type.capitalize()} configuration file not found at {os.path.abspath(path_to_load)}")

        try:
            with open(path_to_load, 'r') as f:
                return json.load(f)
        except json.JSONDecodeError:
            print(f"Error parsing {file_type} configuration file: {path_to_load}")
            raise
        except Exception as e:
            print(f"Error loading {file_type} configuration file {path_to_load}: {str(e)}")
            raise

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

    def validate_fonts_config(self) -> bool:
        """Validate the fonts configuration section."""
        if 'fonts' not in self.config:
            print("Warning: No 'fonts' section found in configuration. Using defaults.")
            return True
        
        fonts_config = self.config['fonts']
        errors = []
        
        # Validate families
        if 'families' in fonts_config:
            families = fonts_config['families']
            if not isinstance(families, dict):
                errors.append("fonts.families must be a dictionary")
            else:
                for family_name, font_path in families.items():
                    if not isinstance(font_path, str):
                        errors.append(f"fonts.families.{family_name} must be a string path")
                    elif not os.path.exists(font_path):
                        errors.append(f"Font file not found: {font_path}")
                    elif not font_path.lower().endswith(('.ttf', '.bdf')):
                        errors.append(f"Unsupported font type: {font_path} (only .ttf and .bdf supported)")
        
        # Validate tokens
        if 'tokens' in fonts_config:
            tokens = fonts_config['tokens']
            if not isinstance(tokens, dict):
                errors.append("fonts.tokens must be a dictionary")
            else:
                for token_name, token_size in tokens.items():
                    if not isinstance(token_size, (int, float)):
                        errors.append(f"fonts.tokens.{token_name} must be a number")
                    elif token_size <= 0:
                        errors.append(f"fonts.tokens.{token_name} must be positive")
        
        # Validate defaults
        if 'defaults' in fonts_config:
            defaults = fonts_config['defaults']
            if not isinstance(defaults, dict):
                errors.append("fonts.defaults must be a dictionary")
            else:
                # Check family reference
                if 'family' in defaults:
                    family_name = defaults['family']
                    if 'families' in fonts_config and family_name not in fonts_config['families']:
                        errors.append(f"fonts.defaults.family '{family_name}' not found in fonts.families")
                
                # Check size_token reference
                if 'size_token' in defaults:
                    token_name = defaults['size_token']
                    if 'tokens' in fonts_config and token_name not in fonts_config['tokens']:
                        errors.append(f"fonts.defaults.size_token '{token_name}' not found in fonts.tokens")
        
        # Validate overrides
        if 'overrides' in fonts_config:
            overrides = fonts_config['overrides']
            if not isinstance(overrides, dict):
                errors.append("fonts.overrides must be a dictionary")
            else:
                for element_key, override_config in overrides.items():
                    if not isinstance(override_config, dict):
                        errors.append(f"fonts.overrides.{element_key} must be a dictionary")
                    else:
                        # Check family reference
                        if 'family' in override_config:
                            family_name = override_config['family']
                            if 'families' in fonts_config and family_name not in fonts_config['families']:
                                errors.append(f"fonts.overrides.{element_key}.family '{family_name}' not found in fonts.families")
                        
                        # Check size_token reference
                        if 'size_token' in override_config:
                            token_name = override_config['size_token']
                            if 'tokens' in fonts_config and token_name not in fonts_config['tokens']:
                                errors.append(f"fonts.overrides.{element_key}.size_token '{token_name}' not found in fonts.tokens")
        
        if errors:
            print("Font configuration validation errors:")
            for error in errors:
                print(f"  - {error}")
            return False
        
        print("Font configuration validation passed")
        return True
    
    def get_fonts_config(self) -> Dict[str, Any]:
        """Get the fonts configuration section."""
        return self.config.get('fonts', {})