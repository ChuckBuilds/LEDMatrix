from flask import Blueprint, request, jsonify, Response
import json
import os
import subprocess
import time
import hashlib
import uuid
from datetime import datetime
from pathlib import Path

# Import new infrastructure
from src.web_interface.api_helpers import success_response, error_response, validate_request_json
from src.web_interface.errors import ErrorCode
from src.plugin_system.operation_types import OperationType
from src.web_interface.logging_config import log_plugin_operation, log_config_change

# Will be initialized when blueprint is registered
config_manager = None
plugin_manager = None
plugin_store_manager = None
saved_repositories_manager = None
cache_manager = None
schema_manager = None
operation_queue = None
plugin_state_manager = None
operation_history = None

# Get project root directory (web_interface/../..)
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

api_v3 = Blueprint('api_v3', __name__)

def _ensure_cache_manager():
    """Ensure cache manager is initialized."""
    global cache_manager
    if cache_manager is None:
        from src.cache_manager import CacheManager
        cache_manager = CacheManager()
    return cache_manager

def _save_config_atomic(config_manager, config_data, create_backup=True):
    """
    Save configuration using atomic save if available, fallback to regular save.
    
    Returns:
        tuple: (success: bool, error_message: str or None)
    """
    if hasattr(config_manager, 'save_config_atomic'):
        result = config_manager.save_config_atomic(config_data, create_backup=create_backup)
        if result.status.value != 'success':
            return False, result.message
        return True, None
    else:
        try:
            config_manager.save_config(config_data)
            return True, None
        except Exception as e:
            return False, str(e)

def _get_display_service_status():
    """Return status information about the ledmatrix service."""
    try:
        result = subprocess.run(
            ['systemctl', 'is-active', 'ledmatrix'],
            capture_output=True,
            text=True,
            timeout=3
        )
        return {
            'active': result.stdout.strip() == 'active',
            'returncode': result.returncode,
            'stdout': result.stdout.strip(),
            'stderr': result.stderr.strip()
        }
    except subprocess.TimeoutExpired:
        return {
            'active': False,
            'returncode': -1,
            'stdout': '',
            'stderr': 'timeout'
        }
    except Exception as err:
        return {
            'active': False,
            'returncode': -1,
            'stdout': '',
            'stderr': str(err)
        }

def _run_systemctl_command(args):
    """Run a systemctl command safely."""
    try:
        result = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=15
        )
        return {
            'returncode': result.returncode,
            'stdout': result.stdout,
            'stderr': result.stderr
        }
    except subprocess.TimeoutExpired:
        return {
            'returncode': -1,
            'stdout': '',
            'stderr': 'timeout'
        }
    except Exception as err:
        return {
            'returncode': -1,
            'stdout': '',
            'stderr': str(err)
        }

def _ensure_display_service_running():
    """Ensure the ledmatrix display service is running."""
    status = _get_display_service_status()
    if status.get('active'):
        status['started'] = False
        return status
    result = _run_systemctl_command(['sudo', 'systemctl', 'start', 'ledmatrix'])
    service_status = _get_display_service_status()
    result['started'] = result.get('returncode') == 0
    result['active'] = service_status.get('active')
    result['status'] = service_status
    return result

def _stop_display_service():
    """Stop the ledmatrix display service."""
    result = _run_systemctl_command(['sudo', 'systemctl', 'stop', 'ledmatrix'])
    status = _get_display_service_status()
    result['active'] = status.get('active')
    result['status'] = status
    return result

@api_v3.route('/config/main', methods=['GET'])
def get_main_config():
    """Get main configuration"""
    try:
        if not api_v3.config_manager:
            return jsonify({'status': 'error', 'message': 'Config manager not initialized'}), 500

        config = api_v3.config_manager.load_config()
        return jsonify({'status': 'success', 'data': config})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@api_v3.route('/config/schedule', methods=['GET'])
def get_schedule_config():
    """Get current schedule configuration"""
    try:
        if not api_v3.config_manager:
            return error_response(
                ErrorCode.CONFIG_LOAD_FAILED,
                'Config manager not initialized',
                status_code=500
            )
        
        config = api_v3.config_manager.load_config()
        schedule_config = config.get('schedule', {})
        
        return success_response(data=schedule_config)
    except Exception as e:
        return error_response(
            ErrorCode.CONFIG_LOAD_FAILED,
            f"Error loading schedule configuration: {str(e)}",
            status_code=500
        )

def _validate_time_format(time_str):
    """Validate time format is HH:MM"""
    try:
        datetime.strptime(time_str, '%H:%M')
        return True, None
    except (ValueError, TypeError):
        return False, f"Invalid time format: {time_str}. Expected HH:MM format."

def _validate_time_range(start_time_str, end_time_str, allow_overnight=True):
    """Validate time range. Returns (is_valid, error_message)"""
    try:
        start_time = datetime.strptime(start_time_str, '%H:%M').time()
        end_time = datetime.strptime(end_time_str, '%H:%M').time()
        
        # Allow overnight schedules (start > end) or same-day schedules
        if not allow_overnight and start_time >= end_time:
            return False, f"Start time ({start_time_str}) must be before end time ({end_time_str}) for same-day schedules"
        
        return True, None
    except (ValueError, TypeError) as e:
        return False, f"Invalid time format: {str(e)}"

@api_v3.route('/config/schedule', methods=['POST'])
def save_schedule_config():
    """Save schedule configuration"""
    try:
        if not api_v3.config_manager:
            return jsonify({'status': 'error', 'message': 'Config manager not initialized'}), 500

        data = request.get_json()
        if not data:
            return jsonify({'status': 'error', 'message': 'No data provided'}), 400

        # Load current config
        current_config = api_v3.config_manager.load_config()
        
        # Build schedule configuration
        # Handle enabled checkbox - can be True, False, or 'on'
        enabled_value = data.get('enabled', False)
        if isinstance(enabled_value, str):
            enabled_value = enabled_value.lower() in ('true', 'on', '1')
        schedule_config = {
            'enabled': enabled_value
        }
        
        mode = data.get('mode', 'global')
        
        if mode == 'global':
            # Simple global schedule
            start_time = data.get('start_time', '07:00')
            end_time = data.get('end_time', '23:00')
            
            # Validate time formats
            is_valid, error_msg = _validate_time_format(start_time)
            if not is_valid:
                return error_response(
                    ErrorCode.VALIDATION_ERROR,
                    error_msg,
                    status_code=400
                )
            
            is_valid, error_msg = _validate_time_format(end_time)
            if not is_valid:
                return error_response(
                    ErrorCode.VALIDATION_ERROR,
                    error_msg,
                    status_code=400
                )
            
            schedule_config['start_time'] = start_time
            schedule_config['end_time'] = end_time
            # Remove days config when switching to global mode
            schedule_config.pop('days', None)
        else:
            # Per-day schedule
            schedule_config['days'] = {}
            # Remove global times when switching to per-day mode
            schedule_config.pop('start_time', None)
            schedule_config.pop('end_time', None)
            days = ['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday']
            enabled_days_count = 0
            
            for day in days:
                day_config = {}
                enabled_key = f'{day}_enabled'
                start_key = f'{day}_start'
                end_key = f'{day}_end'
                
                # Check if day is enabled
                if enabled_key in data:
                    enabled_val = data[enabled_key]
                    # Handle checkbox values that may come as 'on', True, or False
                    if isinstance(enabled_val, str):
                        day_config['enabled'] = enabled_val.lower() in ('true', 'on', '1')
                    else:
                        day_config['enabled'] = bool(enabled_val)
                else:
                    # Default to enabled if not specified
                    day_config['enabled'] = True
                
                # Only add times if day is enabled
                if day_config.get('enabled', True):
                    enabled_days_count += 1
                    start_time = None
                    end_time = None
                    
                    if start_key in data and data[start_key]:
                        start_time = data[start_key]
                    else:
                        start_time = '07:00'
                    
                    if end_key in data and data[end_key]:
                        end_time = data[end_key]
                    else:
                        end_time = '23:00'
                    
                    # Validate time formats
                    is_valid, error_msg = _validate_time_format(start_time)
                    if not is_valid:
                        return error_response(
                            ErrorCode.VALIDATION_ERROR,
                            f"Invalid start time for {day}: {error_msg}",
                            status_code=400
                        )
                    
                    is_valid, error_msg = _validate_time_format(end_time)
                    if not is_valid:
                        return error_response(
                            ErrorCode.VALIDATION_ERROR,
                            f"Invalid end time for {day}: {error_msg}",
                            status_code=400
                        )
                    
                    day_config['start_time'] = start_time
                    day_config['end_time'] = end_time
                
                schedule_config['days'][day] = day_config
            
            # Validate that at least one day is enabled in per-day mode
            if enabled_days_count == 0:
                return error_response(
                    ErrorCode.VALIDATION_ERROR,
                    "At least one day must be enabled in per-day schedule mode",
                    status_code=400
                )
        
        # Update and save config using atomic save
        current_config['schedule'] = schedule_config
        success, error_msg = _save_config_atomic(api_v3.config_manager, current_config, create_backup=True)
        if not success:
            return error_response(
                ErrorCode.CONFIG_SAVE_FAILED,
                f"Failed to save schedule configuration: {error_msg}",
                status_code=500
            )
        
        return success_response(message='Schedule configuration saved successfully')
    except Exception as e:
        import logging
        import traceback
        error_msg = f"Error saving schedule config: {str(e)}\n{traceback.format_exc()}"
        logging.error(error_msg)
        return error_response(
            ErrorCode.CONFIG_SAVE_FAILED,
            f"Error saving schedule configuration: {str(e)}",
            details=traceback.format_exc(),
            status_code=500
        )

@api_v3.route('/config/main', methods=['POST'])
def save_main_config():
    """Save main configuration"""
    try:
        if not api_v3.config_manager:
            return jsonify({'status': 'error', 'message': 'Config manager not initialized'}), 500

        # Try to get JSON data first, fallback to form data
        data = None
        if request.content_type == 'application/json':
            data = request.get_json()
        else:
            # Handle form data
            data = request.form.to_dict()
            # Convert checkbox values
            for key in ['web_display_autostart']:
                if key in data:
                    data[key] = data[key] == 'on'
        
        if not data:
            return jsonify({'status': 'error', 'message': 'No data provided'}), 400
        
        import logging
        logging.error(f"DEBUG: save_main_config received data: {data}")
        logging.error(f"DEBUG: Content-Type header: {request.content_type}")
        logging.error(f"DEBUG: Headers: {dict(request.headers)}")

        # Merge with existing config (similar to original implementation)
        current_config = api_v3.config_manager.load_config()

        # Handle general settings
        # Note: Checkboxes don't send data when unchecked, so we need to check if we're updating general settings
        # If any general setting is present, we're updating the general tab
        is_general_update = any(k in data for k in ['timezone', 'city', 'state', 'country', 'web_display_autostart', 
                                                     'auto_discover', 'auto_load_enabled', 'development_mode', 'plugins_directory'])
        
        if is_general_update:
            # For checkbox: if not present in data during general update, it means unchecked
            current_config['web_display_autostart'] = data.get('web_display_autostart', False)
        
        if 'timezone' in data:
            current_config['timezone'] = data['timezone']
        
        # Handle location settings
        if 'city' in data or 'state' in data or 'country' in data:
            if 'location' not in current_config:
                current_config['location'] = {}
            if 'city' in data:
                current_config['location']['city'] = data['city']
            if 'state' in data:
                current_config['location']['state'] = data['state']
            if 'country' in data:
                current_config['location']['country'] = data['country']
        
        # Handle plugin system settings
        if 'auto_discover' in data or 'auto_load_enabled' in data or 'development_mode' in data or 'plugins_directory' in data:
            if 'plugin_system' not in current_config:
                current_config['plugin_system'] = {}
            
            # Handle plugin system checkboxes
            for checkbox in ['auto_discover', 'auto_load_enabled', 'development_mode']:
                if checkbox in data:
                    current_config['plugin_system'][checkbox] = data.get(checkbox, False)
            
            # Handle plugins_directory
            if 'plugins_directory' in data:
                current_config['plugin_system']['plugins_directory'] = data['plugins_directory']

        # Handle display settings
        display_fields = ['rows', 'cols', 'chain_length', 'parallel', 'brightness', 'hardware_mapping', 
                         'gpio_slowdown', 'scan_mode', 'disable_hardware_pulsing', 'inverse_colors', 'show_refresh_rate',
                         'pwm_bits', 'pwm_dither_bits', 'pwm_lsb_nanoseconds', 'limit_refresh_rate_hz', 'use_short_date_format',
                         'max_dynamic_duration_seconds']
        
        if any(k in data for k in display_fields):
            if 'display' not in current_config:
                current_config['display'] = {}
            if 'hardware' not in current_config['display']:
                current_config['display']['hardware'] = {}
            if 'runtime' not in current_config['display']:
                current_config['display']['runtime'] = {}
            
            # Handle hardware settings
            for field in ['rows', 'cols', 'chain_length', 'parallel', 'brightness', 'hardware_mapping', 'scan_mode', 
                         'pwm_bits', 'pwm_dither_bits', 'pwm_lsb_nanoseconds', 'limit_refresh_rate_hz']:
                if field in data:
                    if field in ['rows', 'cols', 'chain_length', 'parallel', 'brightness', 'scan_mode', 
                               'pwm_bits', 'pwm_dither_bits', 'pwm_lsb_nanoseconds', 'limit_refresh_rate_hz']:
                        current_config['display']['hardware'][field] = int(data[field])
                    else:
                        current_config['display']['hardware'][field] = data[field]
            
            # Handle runtime settings
            if 'gpio_slowdown' in data:
                current_config['display']['runtime']['gpio_slowdown'] = int(data['gpio_slowdown'])
            
            # Handle checkboxes
            for checkbox in ['disable_hardware_pulsing', 'inverse_colors', 'show_refresh_rate']:
                current_config['display']['hardware'][checkbox] = data.get(checkbox, False)
            
            # Handle display-level checkboxes
            if 'use_short_date_format' in data:
                current_config['display']['use_short_date_format'] = data.get('use_short_date_format', False)
            
            # Handle dynamic duration settings
            if 'max_dynamic_duration_seconds' in data:
                if 'dynamic_duration' not in current_config['display']:
                    current_config['display']['dynamic_duration'] = {}
                current_config['display']['dynamic_duration']['max_duration_seconds'] = int(data['max_dynamic_duration_seconds'])

        # Handle display durations
        duration_fields = [k for k in data.keys() if k.endswith('_duration') or k in ['default_duration', 'transition_duration']]
        if duration_fields:
            if 'display' not in current_config:
                current_config['display'] = {}
            if 'display_durations' not in current_config['display']:
                current_config['display']['display_durations'] = {}
            
            for field in duration_fields:
                if field in data:
                    current_config['display']['display_durations'][field] = int(data[field])

        # Handle plugin configurations dynamically
        # Any key that matches a plugin ID should be saved as plugin config
        # This includes proper secret field handling from schema
        plugin_keys_to_remove = []
        for key in data:
            # Check if this key is a plugin ID
            if api_v3.plugin_manager and key in api_v3.plugin_manager.plugin_manifests:
                plugin_id = key
                plugin_config = data[key]
                
                # Load plugin schema to identify secret fields (same logic as save_plugin_config)
                secret_fields = set()
                if api_v3.plugin_manager:
                    plugins_dir = api_v3.plugin_manager.plugins_dir
                else:
                    plugin_system_config = current_config.get('plugin_system', {})
                    plugins_dir_name = plugin_system_config.get('plugins_directory', 'plugin-repos')
                    if os.path.isabs(plugins_dir_name):
                        plugins_dir = Path(plugins_dir_name)
                    else:
                        plugins_dir = PROJECT_ROOT / plugins_dir_name
                schema_path = plugins_dir / plugin_id / 'config_schema.json'
                
                def find_secret_fields(properties, prefix=''):
                    """Recursively find fields marked with x-secret: true"""
                    fields = set()
                    for field_name, field_props in properties.items():
                        full_path = f"{prefix}.{field_name}" if prefix else field_name
                        if field_props.get('x-secret', False):
                            fields.add(full_path)
                        # Check nested objects
                        if field_props.get('type') == 'object' and 'properties' in field_props:
                            fields.update(find_secret_fields(field_props['properties'], full_path))
                    return fields
                
                if schema_path.exists():
                    try:
                        with open(schema_path, 'r', encoding='utf-8') as f:
                            schema = json.load(f)
                            if 'properties' in schema:
                                secret_fields = find_secret_fields(schema['properties'])
                    except Exception as e:
                        print(f"Error reading schema for secret detection: {e}")
                
                # Separate secrets from regular config (same logic as save_plugin_config)
                def separate_secrets(config, secrets_set, prefix=''):
                    """Recursively separate secret fields from regular config"""
                    regular = {}
                    secrets = {}
                    for key, value in config.items():
                        full_path = f"{prefix}.{key}" if prefix else key
                        if isinstance(value, dict):
                            nested_regular, nested_secrets = separate_secrets(value, secrets_set, full_path)
                            if nested_regular:
                                regular[key] = nested_regular
                            if nested_secrets:
                                secrets[key] = nested_secrets
                        elif full_path in secrets_set:
                            secrets[key] = value
                        else:
                            regular[key] = value
                    return regular, secrets
                
                regular_config, secrets_config = separate_secrets(plugin_config, secret_fields)
                
                # PRE-PROCESSING: Preserve 'enabled' state if not in regular_config
                # This prevents overwriting the enabled state when saving config from a form that doesn't include the toggle
                if 'enabled' not in regular_config:
                    try:
                        if plugin_id in current_config and 'enabled' in current_config[plugin_id]:
                            regular_config['enabled'] = current_config[plugin_id]['enabled']
                        elif api_v3.plugin_manager:
                            # Fallback to plugin instance if config doesn't have it
                            plugin_instance = api_v3.plugin_manager.get_plugin(plugin_id)
                            if plugin_instance:
                                regular_config['enabled'] = plugin_instance.enabled
                        # Final fallback: default to True if plugin is loaded (matches BasePlugin default)
                        if 'enabled' not in regular_config:
                            regular_config['enabled'] = True
                    except Exception as e:
                        print(f"Error preserving enabled state for {plugin_id}: {e}")
                        # Default to True on error to avoid disabling plugins
                        regular_config['enabled'] = True
                
                # Get current secrets config
                current_secrets = api_v3.config_manager.get_raw_file_content('secrets')
                
                # Deep merge regular config into main config
                if plugin_id not in current_config:
                    current_config[plugin_id] = {}
                current_config[plugin_id] = deep_merge(current_config[plugin_id], regular_config)
                
                # Deep merge secrets into secrets config
                if secrets_config:
                    if plugin_id not in current_secrets:
                        current_secrets[plugin_id] = {}
                    current_secrets[plugin_id] = deep_merge(current_secrets[plugin_id], secrets_config)
                    # Save secrets file
                    api_v3.config_manager.save_raw_file_content('secrets', current_secrets)
                
                # Mark for removal from data dict (already processed)
                plugin_keys_to_remove.append(key)
                
                # Notify plugin of config change if loaded (with merged config including secrets)
                try:
                    if api_v3.plugin_manager:
                        plugin_instance = api_v3.plugin_manager.get_plugin(plugin_id)
                        if plugin_instance:
                            # Reload merged config (includes secrets) and pass the plugin-specific section
                            merged_config = api_v3.config_manager.load_config()
                            plugin_full_config = merged_config.get(plugin_id, {})
                            if hasattr(plugin_instance, 'on_config_change'):
                                plugin_instance.on_config_change(plugin_full_config)
                except Exception as hook_err:
                    # Don't fail the save if hook fails
                    print(f"Warning: on_config_change failed for {plugin_id}: {hook_err}")
        
        # Remove processed plugin keys from data (they're already in current_config)
        for key in plugin_keys_to_remove:
            del data[key]
        
        # Handle any remaining config keys
        # System settings (timezone, city, etc.) are already handled above
        # Plugin configs should use /api/v3/plugins/config endpoint, but we'll handle them here too for flexibility
        for key in data:
            # Skip system settings that are already handled above
            if key in ['timezone', 'city', 'state', 'country', 
                       'web_display_autostart', 'auto_discover', 
                       'auto_load_enabled', 'development_mode', 
                       'plugins_directory']:
                continue
            # For any remaining keys (including plugin keys), use deep merge to preserve existing settings
            if key in current_config and isinstance(current_config[key], dict) and isinstance(data[key], dict):
                # Deep merge to preserve existing settings
                current_config[key] = deep_merge(current_config[key], data[key])
            else:
                current_config[key] = data[key]

        # Save the merged config using atomic save
        success, error_msg = _save_config_atomic(api_v3.config_manager, current_config, create_backup=True)
        if not success:
            return error_response(
                ErrorCode.CONFIG_SAVE_FAILED,
                f"Failed to save configuration: {error_msg}",
                status_code=500
            )

        return success_response(message='Configuration saved successfully')
    except Exception as e:
        import logging
        import traceback
        error_msg = f"Error saving config: {str(e)}\n{traceback.format_exc()}"
        logging.error(error_msg)
        return jsonify({'status': 'error', 'message': str(e)}), 500

@api_v3.route('/config/secrets', methods=['GET'])
def get_secrets_config():
    """Get secrets configuration"""
    try:
        if not api_v3.config_manager:
            return jsonify({'status': 'error', 'message': 'Config manager not initialized'}), 500

        config = api_v3.config_manager.get_raw_file_content('secrets')
        return jsonify({'status': 'success', 'data': config})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@api_v3.route('/config/raw/main', methods=['POST'])
def save_raw_main_config():
    """Save raw main configuration JSON"""
    try:
        if not api_v3.config_manager:
            return jsonify({'status': 'error', 'message': 'Config manager not initialized'}), 500

        data = request.get_json()
        if not data:
            return jsonify({'status': 'error', 'message': 'No data provided'}), 400

        # Validate that it's valid JSON (already parsed by request.get_json())
        # Save the raw config file
        api_v3.config_manager.save_raw_file_content('main', data)

        return jsonify({'status': 'success', 'message': 'Main configuration saved successfully'})
    except json.JSONDecodeError as e:
        return jsonify({'status': 'error', 'message': f'Invalid JSON: {str(e)}'}), 400
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@api_v3.route('/config/raw/secrets', methods=['POST'])
def save_raw_secrets_config():
    """Save raw secrets configuration JSON"""
    try:
        if not api_v3.config_manager:
            return jsonify({'status': 'error', 'message': 'Config manager not initialized'}), 500

        data = request.get_json()
        if not data:
            return jsonify({'status': 'error', 'message': 'No data provided'}), 400

        # Save the secrets config
        api_v3.config_manager.save_raw_file_content('secrets', data)

        # Reload GitHub token in plugin store manager if it exists
        if api_v3.plugin_store_manager:
            api_v3.plugin_store_manager.github_token = api_v3.plugin_store_manager._load_github_token()

        return jsonify({'status': 'success', 'message': 'Secrets configuration saved successfully'})
    except json.JSONDecodeError as e:
        return jsonify({'status': 'error', 'message': f'Invalid JSON: {str(e)}'}), 400
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@api_v3.route('/system/status', methods=['GET'])
def get_system_status():
    """Get system status"""
    try:
        # This would integrate with actual system monitoring
        status = {
            'timestamp': time.time(),
            'uptime': 'Running',
            'service_active': True,
            'cpu_percent': 0,  # Would need psutil or similar
            'memory_used_percent': 0,
            'cpu_temp': 0,
            'disk_used_percent': 0
        }
        return jsonify({'status': 'success', 'data': status})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

def get_git_version(project_dir=None):
    """Get git version information from the repository"""
    if project_dir is None:
        project_dir = PROJECT_ROOT
    
    try:
        # Try to get tag description (e.g., v2.4-10-g123456)
        result = subprocess.run(
            ['git', 'describe', '--tags', '--dirty'],
            capture_output=True,
            text=True,
            timeout=5,
            cwd=str(project_dir)
        )
        
        if result.returncode == 0:
            return result.stdout.strip()
        
        # Fallback to short commit hash
        result = subprocess.run(
            ['git', 'rev-parse', '--short', 'HEAD'],
            capture_output=True,
            text=True,
            timeout=5,
            cwd=str(project_dir)
        )
        
        if result.returncode == 0:
            return result.stdout.strip()
        
        return 'Unknown'
    except Exception:
        return 'Unknown'

@api_v3.route('/system/version', methods=['GET'])
def get_system_version():
    """Get LEDMatrix repository version"""
    try:
        version = get_git_version()
        return jsonify({'status': 'success', 'data': {'version': version}})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@api_v3.route('/system/action', methods=['POST'])
def execute_system_action():
    """Execute system actions (start/stop/reboot/etc)"""
    try:
        # HTMX sends data as form data, not JSON
        data = request.get_json(silent=True) or {}
        if not data:
            # Try to get from form data if JSON fails
            data = {
                'action': request.form.get('action'),
                'mode': request.form.get('mode')
            }
        
        if not data or 'action' not in data:
            return jsonify({'status': 'error', 'message': 'Action required'}), 400

        action = data['action']
        mode = data.get('mode')  # For on-demand modes

        # Map actions to subprocess calls (similar to original implementation)
        if action == 'start_display':
            if mode:
                # For on-demand modes, we would need to integrate with the display controller
                # For now, just start the display service
                result = subprocess.run(['sudo', 'systemctl', 'start', 'ledmatrix'],
                                     capture_output=True, text=True)
                return jsonify({
                    'status': 'success' if result.returncode == 0 else 'error',
                    'message': f'Started display in {mode} mode',
                    'returncode': result.returncode,
                    'stdout': result.stdout,
                    'stderr': result.stderr
                })
            else:
                result = subprocess.run(['sudo', 'systemctl', 'start', 'ledmatrix'],
                                     capture_output=True, text=True)
        elif action == 'stop_display':
            result = subprocess.run(['sudo', 'systemctl', 'stop', 'ledmatrix'],
                                 capture_output=True, text=True)
        elif action == 'enable_autostart':
            result = subprocess.run(['sudo', 'systemctl', 'enable', 'ledmatrix'],
                                 capture_output=True, text=True)
        elif action == 'disable_autostart':
            result = subprocess.run(['sudo', 'systemctl', 'disable', 'ledmatrix'],
                                 capture_output=True, text=True)
        elif action == 'reboot_system':
            result = subprocess.run(['sudo', 'reboot'],
                                 capture_output=True, text=True)
        elif action == 'git_pull':
            # Use PROJECT_ROOT instead of hardcoded path
            project_dir = str(PROJECT_ROOT)
            
            # Check if there are local changes that need to be stashed
            # Exclude plugins directory - plugins are separate repos and shouldn't be stashed with base project
            # Use --untracked-files=no to skip untracked files check (much faster with symlinked plugins)
            try:
                status_result = subprocess.run(
                    ['git', 'status', '--porcelain', '--untracked-files=no'],
                    capture_output=True,
                    text=True,
                    timeout=30,
                    cwd=project_dir
                )
                # Filter out any changes in plugins directory - plugins are separate repositories
                # Git status format: XY filename (where X is status of index, Y is status of work tree)
                status_lines = [line for line in status_result.stdout.strip().split('\n') 
                               if line.strip() and 'plugins/' not in line]
                has_changes = bool('\n'.join(status_lines).strip())
            except subprocess.TimeoutExpired:
                # If status check times out, assume there might be changes and proceed
                # This is safer than failing the update
                has_changes = True
                status_result = type('obj', (object,), {'stdout': '', 'stderr': 'Status check timed out'})()
            
            stash_info = ""
            
            # Stash local changes if they exist (excluding plugins)
            # Plugins are separate repositories and shouldn't be stashed with base project updates
            if has_changes:
                try:
                    # Use pathspec to exclude plugins directory from stash
                    stash_result = subprocess.run(
                        ['git', 'stash', 'push', '-m', 'LEDMatrix auto-stash before update', '--', ':!plugins'],
                        capture_output=True,
                        text=True,
                        timeout=30,
                        cwd=project_dir
                    )
                    if stash_result.returncode == 0:
                        print(f"Stashed local changes: {stash_result.stdout}")
                        stash_info = " Local changes were stashed."
                    else:
                        # If stash fails, log but continue with pull
                        print(f"Stash failed: {stash_result.stderr}")
                except subprocess.TimeoutExpired:
                    print("Stash operation timed out, proceeding with pull")
            
            # Perform the git pull
            result = subprocess.run(
                ['git', 'pull', '--rebase'],
                capture_output=True,
                text=True,
                timeout=60,
                cwd=project_dir
            )
            
            # Return custom response for git_pull
            if result.returncode == 0:
                pull_message = "Code updated successfully."
                if has_changes:
                    pull_message = f"Code updated successfully. Local changes were automatically stashed.{stash_info}"
                if result.stdout and "Already up to date" not in result.stdout:
                    pull_message = f"Code updated successfully.{stash_info}"
            else:
                pull_message = f"Update failed: {result.stderr or 'Unknown error'}"
            
            return jsonify({
                'status': 'success' if result.returncode == 0 else 'error',
                'message': pull_message,
                'returncode': result.returncode,
                'stdout': result.stdout,
                'stderr': result.stderr
            })
        elif action == 'restart_display_service':
            result = subprocess.run(['sudo', 'systemctl', 'restart', 'ledmatrix'],
                                 capture_output=True, text=True)
        elif action == 'restart_web_service':
            # Try to restart the web service (assuming it's ledmatrix-web.service)
            result = subprocess.run(['sudo', 'systemctl', 'restart', 'ledmatrix-web'],
                                 capture_output=True, text=True)
        else:
            return jsonify({'status': 'error', 'message': f'Unknown action: {action}'}), 400

        return jsonify({
            'status': 'success' if result.returncode == 0 else 'error',
            'message': f'Action {action} completed',
            'returncode': result.returncode,
            'stdout': result.stdout,
            'stderr': result.stderr
        })

    except Exception as e:
        import traceback
        error_details = traceback.format_exc()
        print(f"Error in execute_system_action: {str(e)}")
        print(error_details)
        return jsonify({'status': 'error', 'message': str(e), 'details': error_details}), 500

@api_v3.route('/display/current', methods=['GET'])
def get_display_current():
    """Get current display state"""
    try:
        # This would integrate with the actual display controller
        display_data = {
            'timestamp': time.time(),
            'width': 128,
            'height': 64,
            'image': None  # Base64 encoded image data
        }
        return jsonify({'status': 'success', 'data': display_data})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@api_v3.route('/display/on-demand/status', methods=['GET'])
def get_on_demand_status():
    """Return the current on-demand display state."""
    try:
        cache = _ensure_cache_manager()
        state = cache.get('display_on_demand_state', max_age=120)
        if state is None:
            state = {
                'active': False,
                'status': 'idle',
                'last_updated': None
            }
        service_status = _get_display_service_status()
        return jsonify({
            'status': 'success',
            'data': {
                'state': state,
                'service': service_status
            }
        })
    except Exception as exc:
        import traceback
        error_details = traceback.format_exc()
        print(f"Error in get_on_demand_status: {exc}")
        print(error_details)
        return jsonify({'status': 'error', 'message': str(exc)}), 500

@api_v3.route('/display/on-demand/start', methods=['POST'])
def start_on_demand_display():
    """Request the display controller to run a specific plugin on-demand."""
    try:
        data = request.get_json() or {}
        plugin_id = data.get('plugin_id')
        mode = data.get('mode')
        duration = data.get('duration')
        pinned = bool(data.get('pinned', False))
        start_service = data.get('start_service', True)

        if not plugin_id and not mode:
            return jsonify({'status': 'error', 'message': 'plugin_id or mode is required'}), 400

        resolved_plugin = plugin_id
        resolved_mode = mode

        if api_v3.plugin_manager:
            if resolved_plugin and resolved_plugin not in api_v3.plugin_manager.plugin_manifests:
                return jsonify({'status': 'error', 'message': f'Plugin {resolved_plugin} not found'}), 404

            if resolved_plugin and not resolved_mode:
                modes = api_v3.plugin_manager.get_plugin_display_modes(resolved_plugin)
                resolved_mode = modes[0] if modes else resolved_plugin
            elif resolved_mode and not resolved_plugin:
                resolved_plugin = api_v3.plugin_manager.find_plugin_for_mode(resolved_mode)
                if not resolved_plugin:
                    return jsonify({'status': 'error', 'message': f'Mode {resolved_mode} not found'}), 404

        if api_v3.config_manager and resolved_plugin:
            config = api_v3.config_manager.load_config()
            plugin_config = config.get(resolved_plugin, {})
            if 'enabled' in plugin_config and not plugin_config.get('enabled', False):
                return jsonify({
                    'status': 'error',
                    'message': f'Plugin {resolved_plugin} is disabled in configuration'
                }), 400

        # Check if display service is running (or will be started)
        service_status = _get_display_service_status()
        if not service_status.get('active') and not start_service:
            return jsonify({
                'status': 'error',
                'message': 'Display service is not running. Please start the display service or enable "Start Service" option.',
                'service_status': service_status
            }), 400

        cache = _ensure_cache_manager()
        request_id = data.get('request_id') or str(uuid.uuid4())
        request_payload = {
            'request_id': request_id,
            'action': 'start',
            'plugin_id': resolved_plugin,
            'mode': resolved_mode,
            'duration': duration,
            'pinned': pinned,
            'timestamp': time.time()
        }
        cache.set('display_on_demand_request', request_payload)

        service_result = None
        if start_service:
            service_result = _ensure_display_service_running()
            # Check if service actually started
            if service_result and not service_result.get('active'):
                return jsonify({
                    'status': 'error',
                    'message': 'Failed to start display service. Please check service logs or start it manually.',
                    'service_result': service_result
                }), 500

        response_data = {
            'request_id': request_id,
            'plugin_id': resolved_plugin,
            'mode': resolved_mode,
            'duration': duration,
            'pinned': pinned,
            'service': service_result
        }
        return jsonify({'status': 'success', 'data': response_data})
    except Exception as exc:
        import traceback
        error_details = traceback.format_exc()
        print(f"Error in start_on_demand_display: {exc}")
        print(error_details)
        return jsonify({'status': 'error', 'message': str(exc)}), 500

@api_v3.route('/display/on-demand/stop', methods=['POST'])
def stop_on_demand_display():
    """Request the display controller to stop on-demand mode."""
    try:
        data = request.get_json(silent=True) or {}
        stop_service = data.get('stop_service', False)

        cache = _ensure_cache_manager()
        request_id = data.get('request_id') or str(uuid.uuid4())
        request_payload = {
            'request_id': request_id,
            'action': 'stop',
            'timestamp': time.time()
        }
        cache.set('display_on_demand_request', request_payload)

        service_result = None
        if stop_service:
            service_result = _stop_display_service()

        return jsonify({
            'status': 'success',
            'data': {
                'request_id': request_id,
                'service': service_result
            }
        })
    except Exception as exc:
        import traceback
        error_details = traceback.format_exc()
        print(f"Error in stop_on_demand_display: {exc}")
        print(error_details)
        return jsonify({'status': 'error', 'message': str(exc)}), 500

@api_v3.route('/plugins/installed', methods=['GET'])
def get_installed_plugins():
    """Get installed plugins"""
    try:
        if not api_v3.plugin_manager or not api_v3.plugin_store_manager:
            return jsonify({'status': 'error', 'message': 'Plugin managers not initialized'}), 500
        
        import json
        from pathlib import Path
        
        # Re-discover plugins to ensure we have the latest list
        # This handles cases where plugins are added/removed after app startup
        api_v3.plugin_manager.discover_plugins()
        
        # Get all installed plugin info from the plugin manager
        all_plugin_info = api_v3.plugin_manager.get_all_plugin_info()
        
        # Format for the web interface
        plugins = []
        for plugin_info in all_plugin_info:
            plugin_id = plugin_info.get('id')
            
            # Re-read manifest from disk to ensure we have the latest metadata
            manifest_path = Path(api_v3.plugin_manager.plugins_dir) / plugin_id / "manifest.json"
            if manifest_path.exists():
                try:
                    with open(manifest_path, 'r', encoding='utf-8') as f:
                        fresh_manifest = json.load(f)
                    # Update plugin_info with fresh manifest data
                    plugin_info.update(fresh_manifest)
                except Exception as e:
                    # If we can't read the fresh manifest, use the cached one
                    print(f"Warning: Could not read fresh manifest for {plugin_id}: {e}")
            
            # Get enabled status from config (source of truth)
            # Read from config file first, fall back to plugin instance if config doesn't have the key
            enabled = None
            if api_v3.config_manager:
                full_config = api_v3.config_manager.load_config()
                plugin_config = full_config.get(plugin_id, {})
                # Check if 'enabled' key exists in config (even if False)
                if 'enabled' in plugin_config:
                    enabled = bool(plugin_config['enabled'])
            
            # Fallback to plugin instance if config doesn't have enabled key
            if enabled is None:
                plugin_instance = api_v3.plugin_manager.get_plugin(plugin_id)
                if plugin_instance:
                    enabled = plugin_instance.enabled
                else:
                    # Default to True if no config key and plugin not loaded (matches BasePlugin default)
                    enabled = True
            
            # Get verified status from store registry (if available)
            store_info = api_v3.plugin_store_manager.get_plugin_info(plugin_id)
            verified = store_info.get('verified', False) if store_info else False

            # Get local git info for installed plugin (actual installed commit)
            plugin_path = Path(api_v3.plugin_manager.plugins_dir) / plugin_id
            local_git_info = api_v3.plugin_store_manager._get_local_git_info(plugin_path) if plugin_path.exists() else None

            # Use local git info if available (actual installed commit), otherwise fall back to manifest/store info
            if local_git_info:
                last_commit = local_git_info.get('short_sha') or local_git_info.get('sha', '')[:7] if local_git_info.get('sha') else None
                branch = local_git_info.get('branch')
                # Use commit date from git if available
                last_updated = local_git_info.get('date_iso') or local_git_info.get('date')
            else:
                # Fall back to manifest/store info if no local git info
                last_updated = plugin_info.get('last_updated')
                last_commit = plugin_info.get('last_commit') or plugin_info.get('last_commit_sha')
                branch = plugin_info.get('branch')

                if store_info:
                    last_updated = last_updated or store_info.get('last_updated') or store_info.get('last_updated_iso')
                    last_commit = last_commit or store_info.get('last_commit') or store_info.get('last_commit_sha')
                    branch = branch or store_info.get('branch') or store_info.get('default_branch')

            last_commit_message = plugin_info.get('last_commit_message')
            if store_info and not last_commit_message:
                last_commit_message = store_info.get('last_commit_message')
            
            # Get web_ui_actions from manifest if available
            web_ui_actions = plugin_info.get('web_ui_actions', [])
            
            plugins.append({
                'id': plugin_id,
                'name': plugin_info.get('name', plugin_id),
                'author': plugin_info.get('author', 'Unknown'),
                'category': plugin_info.get('category', 'General'),
                'description': plugin_info.get('description', 'No description available'),
                'tags': plugin_info.get('tags', []),
                'enabled': enabled,
                'verified': verified,
                'loaded': plugin_info.get('loaded', False),
                'last_updated': last_updated,
                'last_commit': last_commit,
                'last_commit_message': last_commit_message,
                'branch': branch,
                'web_ui_actions': web_ui_actions
            })
        
        return jsonify({'status': 'success', 'data': {'plugins': plugins}})
    except Exception as e:
        import traceback
        error_details = traceback.format_exc()
        print(f"Error in get_installed_plugins: {str(e)}")
        print(error_details)
        return jsonify({'status': 'error', 'message': str(e), 'details': error_details}), 500

@api_v3.route('/plugins/health', methods=['GET'])
def get_plugin_health():
    """Get health metrics for all plugins"""
    try:
        if not api_v3.plugin_manager:
            return jsonify({'status': 'error', 'message': 'Plugin manager not initialized'}), 500
        
        # Check if health tracker is available
        if not hasattr(api_v3.plugin_manager, 'health_tracker') or not api_v3.plugin_manager.health_tracker:
            return jsonify({
                'status': 'success',
                'data': {},
                'message': 'Health tracking not available'
            })
        
        # Get health summaries for all plugins
        health_summaries = api_v3.plugin_manager.health_tracker.get_all_health_summaries()
        
        return jsonify({
            'status': 'success',
            'data': health_summaries
        })
    except Exception as e:
        import traceback
        error_details = traceback.format_exc()
        print(f"Error in get_plugin_health: {str(e)}")
        print(error_details)
        return jsonify({'status': 'error', 'message': str(e)}), 500

@api_v3.route('/plugins/health/<plugin_id>', methods=['GET'])
def get_plugin_health_single(plugin_id):
    """Get health metrics for a specific plugin"""
    try:
        if not api_v3.plugin_manager:
            return jsonify({'status': 'error', 'message': 'Plugin manager not initialized'}), 500
        
        # Check if health tracker is available
        if not hasattr(api_v3.plugin_manager, 'health_tracker') or not api_v3.plugin_manager.health_tracker:
            return jsonify({
                'status': 'error',
                'message': 'Health tracking not available'
            }), 503
        
        # Get health summary for specific plugin
        health_summary = api_v3.plugin_manager.health_tracker.get_health_summary(plugin_id)
        
        return jsonify({
            'status': 'success',
            'data': health_summary
        })
    except Exception as e:
        import traceback
        error_details = traceback.format_exc()
        print(f"Error in get_plugin_health_single: {str(e)}")
        print(error_details)
        return jsonify({'status': 'error', 'message': str(e)}), 500

@api_v3.route('/plugins/health/<plugin_id>/reset', methods=['POST'])
def reset_plugin_health(plugin_id):
    """Reset health state for a plugin (manual recovery)"""
    try:
        if not api_v3.plugin_manager:
            return jsonify({'status': 'error', 'message': 'Plugin manager not initialized'}), 500
        
        # Check if health tracker is available
        if not hasattr(api_v3.plugin_manager, 'health_tracker') or not api_v3.plugin_manager.health_tracker:
            return jsonify({
                'status': 'error',
                'message': 'Health tracking not available'
            }), 503
        
        # Reset health state
        api_v3.plugin_manager.health_tracker.reset_health(plugin_id)
        
        return jsonify({
            'status': 'success',
            'message': f'Health state reset for plugin {plugin_id}'
        })
    except Exception as e:
        import traceback
        error_details = traceback.format_exc()
        print(f"Error in reset_plugin_health: {str(e)}")
        print(error_details)
        return jsonify({'status': 'error', 'message': str(e)}), 500

@api_v3.route('/plugins/metrics', methods=['GET'])
def get_plugin_metrics():
    """Get resource metrics for all plugins"""
    try:
        if not api_v3.plugin_manager:
            return jsonify({'status': 'error', 'message': 'Plugin manager not initialized'}), 500
        
        # Check if resource monitor is available
        if not hasattr(api_v3.plugin_manager, 'resource_monitor') or not api_v3.plugin_manager.resource_monitor:
            return jsonify({
                'status': 'success',
                'data': {},
                'message': 'Resource monitoring not available'
            })
        
        # Get metrics summaries for all plugins
        metrics_summaries = api_v3.plugin_manager.resource_monitor.get_all_metrics_summaries()
        
        return jsonify({
            'status': 'success',
            'data': metrics_summaries
        })
    except Exception as e:
        import traceback
        error_details = traceback.format_exc()
        print(f"Error in get_plugin_metrics: {str(e)}")
        print(error_details)
        return jsonify({'status': 'error', 'message': str(e)}), 500

@api_v3.route('/plugins/metrics/<plugin_id>', methods=['GET'])
def get_plugin_metrics_single(plugin_id):
    """Get resource metrics for a specific plugin"""
    try:
        if not api_v3.plugin_manager:
            return jsonify({'status': 'error', 'message': 'Plugin manager not initialized'}), 500
        
        # Check if resource monitor is available
        if not hasattr(api_v3.plugin_manager, 'resource_monitor') or not api_v3.plugin_manager.resource_monitor:
            return jsonify({
                'status': 'error',
                'message': 'Resource monitoring not available'
            }), 503
        
        # Get metrics summary for specific plugin
        metrics_summary = api_v3.plugin_manager.resource_monitor.get_metrics_summary(plugin_id)
        
        return jsonify({
            'status': 'success',
            'data': metrics_summary
        })
    except Exception as e:
        import traceback
        error_details = traceback.format_exc()
        print(f"Error in get_plugin_metrics_single: {str(e)}")
        print(error_details)
        return jsonify({'status': 'error', 'message': str(e)}), 500

@api_v3.route('/plugins/metrics/<plugin_id>/reset', methods=['POST'])
def reset_plugin_metrics(plugin_id):
    """Reset metrics for a plugin"""
    try:
        if not api_v3.plugin_manager:
            return jsonify({'status': 'error', 'message': 'Plugin manager not initialized'}), 500
        
        # Check if resource monitor is available
        if not hasattr(api_v3.plugin_manager, 'resource_monitor') or not api_v3.plugin_manager.resource_monitor:
            return jsonify({
                'status': 'error',
                'message': 'Resource monitoring not available'
            }), 503
        
        # Reset metrics
        api_v3.plugin_manager.resource_monitor.reset_metrics(plugin_id)
        
        return jsonify({
            'status': 'success',
            'message': f'Metrics reset for plugin {plugin_id}'
        })
    except Exception as e:
        import traceback
        error_details = traceback.format_exc()
        print(f"Error in reset_plugin_metrics: {str(e)}")
        print(error_details)
        return jsonify({'status': 'error', 'message': str(e)}), 500

@api_v3.route('/plugins/limits/<plugin_id>', methods=['GET', 'POST'])
def manage_plugin_limits(plugin_id):
    """Get or set resource limits for a plugin"""
    try:
        if not api_v3.plugin_manager:
            return jsonify({'status': 'error', 'message': 'Plugin manager not initialized'}), 500
        
        # Check if resource monitor is available
        if not hasattr(api_v3.plugin_manager, 'resource_monitor') or not api_v3.plugin_manager.resource_monitor:
            return jsonify({
                'status': 'error',
                'message': 'Resource monitoring not available'
            }), 503
        
        if request.method == 'GET':
            # Get limits
            limits = api_v3.plugin_manager.resource_monitor.get_limits(plugin_id)
            if limits:
                return jsonify({
                    'status': 'success',
                    'data': {
                        'max_memory_mb': limits.max_memory_mb,
                        'max_cpu_percent': limits.max_cpu_percent,
                        'max_execution_time': limits.max_execution_time,
                        'warning_threshold': limits.warning_threshold
                    }
                })
            else:
                return jsonify({
                    'status': 'success',
                    'data': None,
                    'message': 'No limits configured for this plugin'
                })
        else:
            # POST - Set limits
            data = request.get_json() or {}
            from src.plugin_system.resource_monitor import ResourceLimits
            
            limits = ResourceLimits(
                max_memory_mb=data.get('max_memory_mb'),
                max_cpu_percent=data.get('max_cpu_percent'),
                max_execution_time=data.get('max_execution_time'),
                warning_threshold=data.get('warning_threshold', 0.8)
            )
            
            api_v3.plugin_manager.resource_monitor.set_limits(plugin_id, limits)
            
            return jsonify({
                'status': 'success',
                'message': f'Resource limits updated for plugin {plugin_id}'
            })
    except Exception as e:
        import traceback
        error_details = traceback.format_exc()
        print(f"Error in manage_plugin_limits: {str(e)}")
        print(error_details)
        return jsonify({'status': 'error', 'message': str(e)}), 500

@api_v3.route('/plugins/toggle', methods=['POST'])
def toggle_plugin():
    """Toggle plugin enabled/disabled"""
    try:
        if not api_v3.plugin_manager or not api_v3.config_manager:
            return jsonify({'status': 'error', 'message': 'Plugin or config manager not initialized'}), 500
        
        data = request.get_json()
        if not data or 'plugin_id' not in data or 'enabled' not in data:
            return jsonify({'status': 'error', 'message': 'plugin_id and enabled required'}), 400

        plugin_id = data['plugin_id']
        enabled = data['enabled']
        
        # Check if plugin exists in manifests (discovered but may not be loaded)
        if plugin_id not in api_v3.plugin_manager.plugin_manifests:
            return jsonify({'status': 'error', 'message': f'Plugin {plugin_id} not found'}), 404
        
        # Update config (this is what the display controller reads)
        config = api_v3.config_manager.load_config()
        if plugin_id not in config:
            config[plugin_id] = {}
        config[plugin_id]['enabled'] = enabled
        
        # Use atomic save if available
        if hasattr(api_v3.config_manager, 'save_config_atomic'):
            result = api_v3.config_manager.save_config_atomic(config, create_backup=True)
            if result.status.value != 'success':
                return error_response(
                    ErrorCode.CONFIG_SAVE_FAILED,
                    f"Failed to save configuration: {result.message}",
                    status_code=500
                )
        else:
            api_v3.config_manager.save_config(config)
        
        # Update state manager if available
        if api_v3.plugin_state_manager:
            api_v3.plugin_state_manager.set_plugin_enabled(plugin_id, enabled)
        
        # Log operation
        if api_v3.operation_history:
            api_v3.operation_history.record_operation(
                "toggle",
                plugin_id=plugin_id,
                status="success" if enabled else "disabled",
                details={"enabled": enabled}
            )
        
        # If plugin is loaded, also call its lifecycle methods
        # Wrap in try/except to prevent lifecycle errors from failing the toggle
        plugin = api_v3.plugin_manager.get_plugin(plugin_id)
        if plugin:
            try:
                if enabled:
                    if hasattr(plugin, 'on_enable'):
                        plugin.on_enable()
                else:
                    if hasattr(plugin, 'on_disable'):
                        plugin.on_disable()
            except Exception as lifecycle_error:
                # Log the error but don't fail the toggle - config is already saved
                import logging
                logging.warning(f"Lifecycle method error for {plugin_id}: {lifecycle_error}", exc_info=True)
        
        return success_response(
            message=f"Plugin {plugin_id} {'enabled' if enabled else 'disabled'} successfully"
        )
    except Exception as e:
        from src.web_interface.errors import WebInterfaceError
        error = WebInterfaceError.from_exception(e, ErrorCode.PLUGIN_OPERATION_CONFLICT)
        if api_v3.operation_history:
            api_v3.operation_history.record_operation(
                "toggle",
                plugin_id=data.get('plugin_id') if 'data' in locals() else None,
                status="failed",
                error=str(e)
            )
        return error_response(
            error.error_code,
            error.message,
            details=error.details,
            context=error.context,
            status_code=500
        )

@api_v3.route('/plugins/operation/<operation_id>', methods=['GET'])
def get_operation_status(operation_id):
    """Get status of a plugin operation"""
    try:
        if not api_v3.operation_queue:
            return error_response(
                ErrorCode.SYSTEM_ERROR,
                'Operation queue not initialized',
                status_code=500
            )
        
        operation = api_v3.operation_queue.get_operation_status(operation_id)
        if not operation:
            return error_response(
                ErrorCode.PLUGIN_NOT_FOUND,
                f'Operation {operation_id} not found',
                status_code=404
            )
        
        return success_response(data=operation.to_dict())
    except Exception as e:
        from src.web_interface.errors import WebInterfaceError
        error = WebInterfaceError.from_exception(e, ErrorCode.SYSTEM_ERROR)
        return error_response(
            error.error_code,
            error.message,
            details=error.details,
            status_code=500
        )

@api_v3.route('/plugins/operation/history', methods=['GET'])
def get_operation_history():
    """Get operation history"""
    try:
        if not api_v3.operation_queue:
            return error_response(
                ErrorCode.SYSTEM_ERROR,
                'Operation queue not initialized',
                status_code=500
            )
        
        limit = request.args.get('limit', 50, type=int)
        plugin_id = request.args.get('plugin_id')
        
        history = api_v3.operation_queue.get_operation_history(limit=limit)
        
        # Filter by plugin_id if provided
        if plugin_id:
            history = [op for op in history if op.plugin_id == plugin_id]
        
        return success_response(data=[op.to_dict() for op in history])
    except Exception as e:
        from src.web_interface.errors import WebInterfaceError
        error = WebInterfaceError.from_exception(e, ErrorCode.SYSTEM_ERROR)
        return error_response(
            error.error_code,
            error.message,
            details=error.details,
            status_code=500
        )

@api_v3.route('/plugins/state', methods=['GET'])
def get_plugin_state():
    """Get plugin state from state manager"""
    try:
        if not api_v3.plugin_state_manager:
            return error_response(
                ErrorCode.SYSTEM_ERROR,
                'State manager not initialized',
                status_code=500
            )
        
        plugin_id = request.args.get('plugin_id')
        
        if plugin_id:
            # Get state for specific plugin
            state = api_v3.plugin_state_manager.get_plugin_state(plugin_id)
            if not state:
                return error_response(
                    ErrorCode.PLUGIN_NOT_FOUND,
                    f'Plugin {plugin_id} not found in state manager',
                    context={'plugin_id': plugin_id},
                    status_code=404
                )
            return success_response(data=state.to_dict())
        else:
            # Get all plugin states
            all_states = api_v3.plugin_state_manager.get_all_states()
            return success_response(data={
                plugin_id: state.to_dict()
                for plugin_id, state in all_states.items()
            })
    except Exception as e:
        from src.web_interface.errors import WebInterfaceError
        error = WebInterfaceError.from_exception(e, ErrorCode.SYSTEM_ERROR)
        return error_response(
            error.error_code,
            error.message,
            details=error.details,
            context=error.context,
            status_code=500
        )

@api_v3.route('/plugins/state/reconcile', methods=['POST'])
def reconcile_plugin_state():
    """Reconcile plugin state across all sources"""
    try:
        if not api_v3.plugin_state_manager or not api_v3.plugin_manager:
            return error_response(
                ErrorCode.SYSTEM_ERROR,
                'State manager or plugin manager not initialized',
                status_code=500
            )
        
        from src.plugin_system.state_reconciliation import StateReconciliation
        
        reconciler = StateReconciliation(
            state_manager=api_v3.plugin_state_manager,
            config_manager=api_v3.config_manager,
            plugin_manager=api_v3.plugin_manager,
            plugins_dir=Path(api_v3.plugin_manager.plugins_dir)
        )
        
        result = reconciler.reconcile_state()
        
        return success_response(
            data={
                'inconsistencies_found': len(result.inconsistencies_found),
                'inconsistencies_fixed': len(result.inconsistencies_fixed),
                'inconsistencies_manual': len(result.inconsistencies_manual),
                'inconsistencies': [
                    {
                        'plugin_id': inc.plugin_id,
                        'type': inc.inconsistency_type.value,
                        'description': inc.description,
                        'fix_action': inc.fix_action.value
                    }
                    for inc in result.inconsistencies_found
                ],
                'fixed': [
                    {
                        'plugin_id': inc.plugin_id,
                        'type': inc.inconsistency_type.value,
                        'description': inc.description
                    }
                    for inc in result.inconsistencies_fixed
                ],
                'manual_fix_required': [
                    {
                        'plugin_id': inc.plugin_id,
                        'type': inc.inconsistency_type.value,
                        'description': inc.description
                    }
                    for inc in result.inconsistencies_manual
                ]
            },
            message=result.message
        )
    except Exception as e:
        from src.web_interface.errors import WebInterfaceError
        error = WebInterfaceError.from_exception(e, ErrorCode.SYSTEM_ERROR)
        return error_response(
            error.error_code,
            error.message,
            details=error.details,
            context=error.context,
            status_code=500
        )

@api_v3.route('/plugins/config', methods=['GET'])
def get_plugin_config():
    """Get plugin configuration"""
    try:
        if not api_v3.config_manager:
            return error_response(
                ErrorCode.SYSTEM_ERROR,
                'Config manager not initialized',
                status_code=500
            )
        
        plugin_id = request.args.get('plugin_id')
        if not plugin_id:
            return error_response(
                ErrorCode.INVALID_INPUT,
                'plugin_id required',
                context={'missing_params': ['plugin_id']},
                status_code=400
            )

        # Get plugin configuration from config manager
        main_config = api_v3.config_manager.load_config()
        plugin_config = main_config.get(plugin_id, {})
        
        # If no config exists, return defaults
        if not plugin_config:
            plugin_config = {
                'enabled': True,
                'display_duration': 30
            }

        return success_response(data=plugin_config)
    except Exception as e:
        from src.web_interface.errors import WebInterfaceError
        error = WebInterfaceError.from_exception(e, ErrorCode.CONFIG_LOAD_FAILED)
        return error_response(
            error.error_code,
            error.message,
            details=error.details,
            context=error.context,
            status_code=500
        )

@api_v3.route('/plugins/update', methods=['POST'])
def update_plugin():
    """Update plugin"""
    try:
        # Validate request
        data, error = validate_request_json(['plugin_id'])
        if error:
            return error
        
        if not api_v3.plugin_store_manager:
            return error_response(
                ErrorCode.SYSTEM_ERROR,
                'Plugin store manager not initialized',
                status_code=500
            )

        plugin_id = data['plugin_id']
        
        # Use operation queue if available
        if api_v3.operation_queue:
            def update_callback(operation):
                """Callback to execute plugin update."""
                plugin_dir = Path(api_v3.plugin_store_manager.plugins_dir) / plugin_id
                manifest_path = plugin_dir / "manifest.json"

                current_last_updated = None
                current_commit = None
                current_branch = None

                if manifest_path.exists():
                    try:
                        import json
                        with open(manifest_path, 'r', encoding='utf-8') as f:
                            manifest = json.load(f)
                            current_last_updated = manifest.get('last_updated')
                    except Exception as e:
                        print(f"Warning: Could not read local manifest for {plugin_id}: {e}")

                if api_v3.plugin_store_manager:
                    git_info_before = api_v3.plugin_store_manager._get_local_git_info(plugin_dir)
                    if git_info_before:
                        current_commit = git_info_before.get('sha')
                        current_branch = git_info_before.get('branch')

                remote_info = api_v3.plugin_store_manager.get_plugin_info(plugin_id, fetch_latest_from_github=True)
                remote_commit = remote_info.get('last_commit_sha') if remote_info else None
                remote_branch = remote_info.get('branch') if remote_info else None

                # Update the plugin
                success = api_v3.plugin_store_manager.update_plugin(plugin_id)
                
                if not success:
                    error_msg = f'Failed to update plugin {plugin_id}'
                    if api_v3.operation_history:
                        api_v3.operation_history.record_operation(
                            "update",
                            plugin_id=plugin_id,
                            status="failed",
                            error=error_msg
                        )
                    raise Exception(error_msg)
                
                # Get updated info
                updated_last_updated = current_last_updated
                try:
                    if manifest_path.exists():
                        import json
                        with open(manifest_path, 'r', encoding='utf-8') as f:
                            manifest = json.load(f)
                            updated_last_updated = manifest.get('last_updated', current_last_updated)
                except Exception as e:
                    print(f"Warning: Could not read updated manifest for {plugin_id}: {e}")

                updated_commit = None
                updated_branch = remote_branch or current_branch
                if api_v3.plugin_store_manager:
                    git_info_after = api_v3.plugin_store_manager._get_local_git_info(plugin_dir)
                    if git_info_after:
                        updated_commit = git_info_after.get('sha')
                        updated_branch = git_info_after.get('branch') or updated_branch

                message = f'Plugin {plugin_id} updated successfully'
                if current_commit and updated_commit and current_commit == updated_commit:
                    message = f'Plugin {plugin_id} already up to date (commit {updated_commit[:7]})'
                elif updated_commit:
                    message = f'Plugin {plugin_id} updated to commit {updated_commit[:7]}'
                    if updated_branch:
                        message += f' on branch {updated_branch}'
                elif updated_last_updated and updated_last_updated != current_last_updated:
                    message = f'Plugin {plugin_id} refreshed (Last Updated {updated_last_updated})'

                remote_commit_short = remote_commit[:7] if remote_commit else None
                if remote_commit_short and updated_commit and remote_commit_short != updated_commit[:7]:
                    message += f' (remote latest {remote_commit_short})'

                # Invalidate schema cache
                if api_v3.schema_manager:
                    api_v3.schema_manager.invalidate_cache(plugin_id)
                
                # Rediscover plugins
                if api_v3.plugin_manager:
                    api_v3.plugin_manager.discover_plugins()
                    if plugin_id in api_v3.plugin_manager.plugins:
                        api_v3.plugin_manager.reload_plugin(plugin_id)
                
                # Update state manager
                if api_v3.plugin_state_manager:
                    api_v3.plugin_state_manager.update_plugin_state(
                        plugin_id,
                        {'last_updated': datetime.now()}
                    )
                
                # Record in history
                if api_v3.operation_history:
                    api_v3.operation_history.record_operation(
                        "update",
                        plugin_id=plugin_id,
                        status="success",
                        details={
                            "commit": updated_commit,
                            "branch": updated_branch,
                            "last_updated": updated_last_updated
                        }
                    )
                
                return {
                    'success': True,
                    'message': message,
                    'last_updated': updated_last_updated,
                    'commit': updated_commit
                }
            
            # Enqueue operation
            operation_id = api_v3.operation_queue.enqueue_operation(
                OperationType.UPDATE,
                plugin_id,
                operation_callback=update_callback
            )
            
            return success_response(
                data={'operation_id': operation_id},
                message=f'Plugin {plugin_id} update queued'
            )
        else:
            # Fallback to direct update
            plugin_dir = Path(api_v3.plugin_store_manager.plugins_dir) / plugin_id
            manifest_path = plugin_dir / "manifest.json"

            current_last_updated = None
            current_commit = None
            current_branch = None

            if manifest_path.exists():
                try:
                    import json
                    with open(manifest_path, 'r', encoding='utf-8') as f:
                        manifest = json.load(f)
                        current_last_updated = manifest.get('last_updated')
                except Exception as e:
                    print(f"Warning: Could not read local manifest for {plugin_id}: {e}")

            if api_v3.plugin_store_manager:
                git_info_before = api_v3.plugin_store_manager._get_local_git_info(plugin_dir)
                if git_info_before:
                    current_commit = git_info_before.get('sha')
                    current_branch = git_info_before.get('branch')

            remote_info = api_v3.plugin_store_manager.get_plugin_info(plugin_id, fetch_latest_from_github=True)
            remote_commit = remote_info.get('last_commit_sha') if remote_info else None
            remote_branch = remote_info.get('branch') if remote_info else None

            # Update the plugin
            success = api_v3.plugin_store_manager.update_plugin(plugin_id)
        
            if success:
                updated_last_updated = current_last_updated
                try:
                    if manifest_path.exists():
                        import json
                        with open(manifest_path, 'r', encoding='utf-8') as f:
                            manifest = json.load(f)
                            updated_last_updated = manifest.get('last_updated', current_last_updated)
                except Exception as e:
                    print(f"Warning: Could not read updated manifest for {plugin_id}: {e}")

                updated_commit = None
                updated_branch = remote_branch or current_branch
                if api_v3.plugin_store_manager:
                    git_info_after = api_v3.plugin_store_manager._get_local_git_info(plugin_dir)
                    if git_info_after:
                        updated_commit = git_info_after.get('sha')
                        updated_branch = git_info_after.get('branch') or updated_branch

                message = f'Plugin {plugin_id} updated successfully'
                if current_commit and updated_commit and current_commit == updated_commit:
                    message = f'Plugin {plugin_id} already up to date (commit {updated_commit[:7]})'
                elif updated_commit:
                    message = f'Plugin {plugin_id} updated to commit {updated_commit[:7]}'
                    if updated_branch:
                        message += f' on branch {updated_branch}'
                elif updated_last_updated and updated_last_updated != current_last_updated:
                    message = f'Plugin {plugin_id} refreshed (Last Updated {updated_last_updated})'

                remote_commit_short = remote_commit[:7] if remote_commit else None
                if remote_commit_short and updated_commit and remote_commit_short != updated_commit[:7]:
                    message += f' (remote latest {remote_commit_short})'

                # Invalidate schema cache
                if api_v3.schema_manager:
                    api_v3.schema_manager.invalidate_cache(plugin_id)
                
                # Rediscover plugins
                if api_v3.plugin_manager:
                    api_v3.plugin_manager.discover_plugins()
                    if plugin_id in api_v3.plugin_manager.plugins:
                        api_v3.plugin_manager.reload_plugin(plugin_id)
                
                # Update state and history
                if api_v3.plugin_state_manager:
                    api_v3.plugin_state_manager.update_plugin_state(
                        plugin_id,
                        {'last_updated': datetime.now()}
                    )
                if api_v3.operation_history:
                    api_v3.operation_history.record_operation(
                        "update",
                        plugin_id=plugin_id,
                        status="success",
                        details={
                            "last_updated": updated_last_updated,
                            "commit": updated_commit
                        }
                    )
                
                return success_response(
                    data={
                        'last_updated': updated_last_updated,
                        'commit': updated_commit
                    },
                    message=message
                )
            else:
                error_msg = f'Failed to update plugin {plugin_id}'
                plugin_path_dir = Path(api_v3.plugin_store_manager.plugins_dir) / plugin_id
                if not plugin_path_dir.exists():
                    error_msg += ': Plugin not found'
                else:
                    plugin_info = api_v3.plugin_store_manager.get_plugin_info(plugin_id)
                    if not plugin_info:
                        error_msg += ': Plugin not found in registry'
                
                if api_v3.operation_history:
                    api_v3.operation_history.record_operation(
                        "update",
                        plugin_id=plugin_id,
                        status="failed",
                        error=error_msg
                    )
                
                return error_response(
                    ErrorCode.PLUGIN_UPDATE_FAILED,
                    error_msg,
                    status_code=500
                )
            
    except Exception as e:
        from src.web_interface.errors import WebInterfaceError
        error = WebInterfaceError.from_exception(e, ErrorCode.PLUGIN_UPDATE_FAILED)
        if api_v3.operation_history:
            api_v3.operation_history.record_operation(
                "update",
                plugin_id=data.get('plugin_id') if 'data' in locals() else None,
                status="failed",
                error=str(e)
            )
        return error_response(
            error.error_code,
            error.message,
            details=error.details,
            context=error.context,
            status_code=500
        )

@api_v3.route('/plugins/uninstall', methods=['POST'])
def uninstall_plugin():
    """Uninstall plugin"""
    try:
        # Validate request
        data, error = validate_request_json(['plugin_id'])
        if error:
            return error
        
        if not api_v3.plugin_store_manager:
            return error_response(
                ErrorCode.SYSTEM_ERROR,
                'Plugin store manager not initialized',
                status_code=500
            )

        plugin_id = data['plugin_id']
        preserve_config = data.get('preserve_config', False)
        
        # Use operation queue if available
        if api_v3.operation_queue:
            def uninstall_callback(operation):
                """Callback to execute plugin uninstallation."""
                # Unload the plugin first if it's loaded
                if api_v3.plugin_manager and plugin_id in api_v3.plugin_manager.plugins:
                    api_v3.plugin_manager.unload_plugin(plugin_id)
                
                # Uninstall the plugin
                success = api_v3.plugin_store_manager.uninstall_plugin(plugin_id)
                
                if not success:
                    error_msg = f'Failed to uninstall plugin {plugin_id}'
                    if api_v3.operation_history:
                        api_v3.operation_history.record_operation(
                            "uninstall",
                            plugin_id=plugin_id,
                            status="failed",
                            error=error_msg
                        )
                    raise Exception(error_msg)
                
                # Invalidate schema cache
                if api_v3.schema_manager:
                    api_v3.schema_manager.invalidate_cache(plugin_id)
                
                # Clean up plugin configuration if not preserving
                if not preserve_config:
                    try:
                        api_v3.config_manager.cleanup_plugin_config(plugin_id, remove_secrets=True)
                    except Exception as cleanup_err:
                        print(f"Warning: Failed to cleanup config for {plugin_id}: {cleanup_err}")
                
                # Remove from state manager
                if api_v3.plugin_state_manager:
                    api_v3.plugin_state_manager.remove_plugin_state(plugin_id)
                
                # Record in history
                if api_v3.operation_history:
                    api_v3.operation_history.record_operation(
                        "uninstall",
                        plugin_id=plugin_id,
                        status="success",
                        details={"preserve_config": preserve_config}
                    )
                
                return {'success': True, 'message': f'Plugin {plugin_id} uninstalled successfully'}
            
            # Enqueue operation
            operation_id = api_v3.operation_queue.enqueue_operation(
                OperationType.UNINSTALL,
                plugin_id,
                operation_callback=uninstall_callback
            )
            
            return success_response(
                data={'operation_id': operation_id},
                message=f'Plugin {plugin_id} uninstallation queued'
            )
        else:
            # Fallback to direct uninstall
            # Unload the plugin first if it's loaded
            if api_v3.plugin_manager and plugin_id in api_v3.plugin_manager.plugins:
                api_v3.plugin_manager.unload_plugin(plugin_id)
            
            # Uninstall the plugin
            success = api_v3.plugin_store_manager.uninstall_plugin(plugin_id)
            
            if success:
                # Invalidate schema cache
                if api_v3.schema_manager:
                    api_v3.schema_manager.invalidate_cache(plugin_id)
                
                # Clean up plugin configuration if not preserving
                if not preserve_config:
                    try:
                        api_v3.config_manager.cleanup_plugin_config(plugin_id, remove_secrets=True)
                    except Exception as cleanup_err:
                        print(f"Warning: Failed to cleanup config for {plugin_id}: {cleanup_err}")
                
                # Remove from state manager
                if api_v3.plugin_state_manager:
                    api_v3.plugin_state_manager.remove_plugin_state(plugin_id)
                
                # Record in history
                if api_v3.operation_history:
                    api_v3.operation_history.record_operation(
                        "uninstall",
                        plugin_id=plugin_id,
                        status="success",
                        details={"preserve_config": preserve_config}
                    )
                
                return success_response(message=f'Plugin {plugin_id} uninstalled successfully')
            else:
                if api_v3.operation_history:
                    api_v3.operation_history.record_operation(
                        "uninstall",
                        plugin_id=plugin_id,
                        status="failed",
                        error=f'Failed to uninstall plugin {plugin_id}'
                    )
                
                return error_response(
                    ErrorCode.PLUGIN_UNINSTALL_FAILED,
                    f'Failed to uninstall plugin {plugin_id}',
                    status_code=500
                )
            
    except Exception as e:
        from src.web_interface.errors import WebInterfaceError
        error = WebInterfaceError.from_exception(e, ErrorCode.PLUGIN_UNINSTALL_FAILED)
        if api_v3.operation_history:
            api_v3.operation_history.record_operation(
                "uninstall",
                plugin_id=data.get('plugin_id') if 'data' in locals() else None,
                status="failed",
                error=str(e)
            )
        return error_response(
            error.error_code,
            error.message,
            details=error.details,
            context=error.context,
            status_code=500
        )

@api_v3.route('/plugins/install', methods=['POST'])
def install_plugin():
    """Install plugin from store"""
    try:
        if not api_v3.plugin_store_manager:
            return jsonify({'status': 'error', 'message': 'Plugin store manager not initialized'}), 500
        
        data = request.get_json()
        if not data or 'plugin_id' not in data:
            return jsonify({'status': 'error', 'message': 'plugin_id required'}), 400

        plugin_id = data['plugin_id']
        branch = data.get('branch')  # Optional branch parameter
        
        # Install the plugin
        # Log the plugins directory being used for debugging
        plugins_dir = api_v3.plugin_store_manager.plugins_dir
        branch_info = f" (branch: {branch})" if branch else ""
        print(f"Installing plugin {plugin_id}{branch_info} to directory: {plugins_dir}", flush=True)
        
        # Use operation queue if available
        if api_v3.operation_queue:
            def install_callback(operation):
                """Callback to execute plugin installation."""
                success = api_v3.plugin_store_manager.install_plugin(plugin_id, branch=branch)
                
                if success:
                    # Invalidate schema cache
                    if api_v3.schema_manager:
                        api_v3.schema_manager.invalidate_cache(plugin_id)
                    
                    # Discover and load the new plugin
                    if api_v3.plugin_manager:
                        api_v3.plugin_manager.discover_plugins()
                        api_v3.plugin_manager.load_plugin(plugin_id)
                    
                    # Update state manager
                    if api_v3.plugin_state_manager:
                        api_v3.plugin_state_manager.set_plugin_installed(plugin_id)
                    
                    # Record in history
                    if api_v3.operation_history:
                        api_v3.operation_history.record_operation(
                            "install",
                            plugin_id=plugin_id,
                            status="success"
                        )
                    
                    branch_msg = f" (branch: {branch})" if branch else ""
                    return {'success': True, 'message': f'Plugin {plugin_id} installed successfully{branch_msg}'}
                else:
                    error_msg = f'Failed to install plugin {plugin_id}'
                    if branch:
                        error_msg += f' (branch: {branch})'
                    plugin_info = api_v3.plugin_store_manager.get_plugin_info(plugin_id)
                    if not plugin_info:
                        error_msg += ' (plugin not found in registry)'
                    
                    # Record failure in history
                    if api_v3.operation_history:
                        api_v3.operation_history.record_operation(
                            "install",
                            plugin_id=plugin_id,
                            status="failed",
                            error=error_msg
                        )
                    
                    raise Exception(error_msg)
            
            # Enqueue operation
            operation_id = api_v3.operation_queue.enqueue_operation(
                OperationType.INSTALL,
                plugin_id,
                operation_callback=install_callback
            )
            
            branch_msg = f" (branch: {branch})" if branch else ""
            return success_response(
                data={'operation_id': operation_id},
                message=f'Plugin {plugin_id} installation queued{branch_msg}'
            )
        else:
            # Fallback to direct installation
            success = api_v3.plugin_store_manager.install_plugin(plugin_id, branch=branch)
            
            if success:
                if api_v3.schema_manager:
                    api_v3.schema_manager.invalidate_cache(plugin_id)
                if api_v3.plugin_manager:
                    api_v3.plugin_manager.discover_plugins()
                    api_v3.plugin_manager.load_plugin(plugin_id)
                if api_v3.plugin_state_manager:
                    api_v3.plugin_state_manager.set_plugin_installed(plugin_id)
                if api_v3.operation_history:
                    api_v3.operation_history.record_operation("install", plugin_id=plugin_id, status="success")
                
                branch_msg = f" (branch: {branch})" if branch else ""
                return success_response(message=f'Plugin {plugin_id} installed successfully{branch_msg}')
            else:
                error_msg = f'Failed to install plugin {plugin_id}'
                if branch:
                    error_msg += f' (branch: {branch})'
                plugin_info = api_v3.plugin_store_manager.get_plugin_info(plugin_id)
                if not plugin_info:
                    error_msg += ' (plugin not found in registry)'
                
                return error_response(
                    ErrorCode.PLUGIN_INSTALL_FAILED,
                    error_msg,
                    status_code=500
                )
            
    except Exception as e:
        import traceback
        error_details = traceback.format_exc()
        print(f"Error in install_plugin: {str(e)}")
        print(error_details)
        return jsonify({'status': 'error', 'message': str(e)}), 500

@api_v3.route('/plugins/install-from-url', methods=['POST'])
def install_plugin_from_url():
    """Install plugin from custom GitHub URL"""
    try:
        if not api_v3.plugin_store_manager:
            return jsonify({'status': 'error', 'message': 'Plugin store manager not initialized'}), 500
        
        data = request.get_json()
        if not data or 'repo_url' not in data:
            return jsonify({'status': 'error', 'message': 'repo_url required'}), 400

        repo_url = data['repo_url'].strip()
        plugin_id = data.get('plugin_id')  # Optional, for monorepo installations
        plugin_path = data.get('plugin_path')  # Optional, for monorepo subdirectory
        branch = data.get('branch')  # Optional branch parameter
        
        # Install the plugin
        result = api_v3.plugin_store_manager.install_from_url(
            repo_url=repo_url,
            plugin_id=plugin_id,
            plugin_path=plugin_path,
            branch=branch
        )
        
        if result.get('success'):
            # Invalidate schema cache for the installed plugin
            installed_plugin_id = result.get('plugin_id')
            if api_v3.schema_manager and installed_plugin_id:
                api_v3.schema_manager.invalidate_cache(installed_plugin_id)
            
            # Discover and load the new plugin
            if api_v3.plugin_manager and installed_plugin_id:
                api_v3.plugin_manager.discover_plugins()
                api_v3.plugin_manager.load_plugin(installed_plugin_id)
            
            branch_msg = f" (branch: {result.get('branch', branch)})" if (result.get('branch') or branch) else ""
            response_data = {
                'status': 'success',
                'message': f"Plugin {installed_plugin_id} installed successfully{branch_msg}",
                'plugin_id': installed_plugin_id,
                'name': result.get('name')
            }
            if result.get('branch'):
                response_data['branch'] = result.get('branch')
            return jsonify(response_data)
        else:
            return jsonify({
                'status': 'error',
                'message': result.get('error', 'Failed to install plugin from URL')
            }), 500
            
    except Exception as e:
        import traceback
        error_details = traceback.format_exc()
        print(f"Error in install_plugin_from_url: {str(e)}")
        print(error_details)
        return jsonify({'status': 'error', 'message': str(e)}), 500

@api_v3.route('/plugins/registry-from-url', methods=['POST'])
def get_registry_from_url():
    """Get plugin list from a registry-style monorepo URL"""
    try:
        if not api_v3.plugin_store_manager:
            return jsonify({'status': 'error', 'message': 'Plugin store manager not initialized'}), 500
        
        data = request.get_json()
        if not data or 'repo_url' not in data:
            return jsonify({'status': 'error', 'message': 'repo_url required'}), 400

        repo_url = data['repo_url'].strip()
        
        # Get registry from the URL
        registry = api_v3.plugin_store_manager.fetch_registry_from_url(repo_url)
        
        if registry:
            return jsonify({
                'status': 'success',
                'plugins': registry.get('plugins', []),
                'registry_url': repo_url
            })
        else:
            return jsonify({
                'status': 'error',
                'message': 'Failed to fetch registry from URL or URL does not contain a valid registry'
            }), 400
            
    except Exception as e:
        import traceback
        error_details = traceback.format_exc()
        print(f"Error in get_registry_from_url: {str(e)}")
        print(error_details)
        return jsonify({'status': 'error', 'message': str(e)}), 500

@api_v3.route('/plugins/saved-repositories', methods=['GET'])
def get_saved_repositories():
    """Get all saved repositories"""
    try:
        if not api_v3.saved_repositories_manager:
            return jsonify({'status': 'error', 'message': 'Saved repositories manager not initialized'}), 500
        
        repositories = api_v3.saved_repositories_manager.get_all()
        return jsonify({'status': 'success', 'data': {'repositories': repositories}})
    except Exception as e:
        import traceback
        error_details = traceback.format_exc()
        print(f"Error in get_saved_repositories: {str(e)}")
        print(error_details)
        return jsonify({'status': 'error', 'message': str(e)}), 500

@api_v3.route('/plugins/saved-repositories', methods=['POST'])
def add_saved_repository():
    """Add a repository to saved list"""
    try:
        if not api_v3.saved_repositories_manager:
            return jsonify({'status': 'error', 'message': 'Saved repositories manager not initialized'}), 500
        
        data = request.get_json()
        if not data or 'repo_url' not in data:
            return jsonify({'status': 'error', 'message': 'repo_url required'}), 400
        
        repo_url = data['repo_url'].strip()
        name = data.get('name')
        
        success = api_v3.saved_repositories_manager.add(repo_url, name)
        
        if success:
            return jsonify({
                'status': 'success',
                'message': 'Repository saved successfully',
                'data': {'repositories': api_v3.saved_repositories_manager.get_all()}
            })
        else:
            return jsonify({
                'status': 'error',
                'message': 'Repository already exists or failed to save'
            }), 400
    except Exception as e:
        import traceback
        error_details = traceback.format_exc()
        print(f"Error in add_saved_repository: {str(e)}")
        print(error_details)
        return jsonify({'status': 'error', 'message': str(e)}), 500

@api_v3.route('/plugins/saved-repositories', methods=['DELETE'])
def remove_saved_repository():
    """Remove a repository from saved list"""
    try:
        if not api_v3.saved_repositories_manager:
            return jsonify({'status': 'error', 'message': 'Saved repositories manager not initialized'}), 500
        
        data = request.get_json()
        if not data or 'repo_url' not in data:
            return jsonify({'status': 'error', 'message': 'repo_url required'}), 400
        
        repo_url = data['repo_url']
        
        success = api_v3.saved_repositories_manager.remove(repo_url)
        
        if success:
            return jsonify({
                'status': 'success',
                'message': 'Repository removed successfully',
                'data': {'repositories': api_v3.saved_repositories_manager.get_all()}
            })
        else:
            return jsonify({
                'status': 'error',
                'message': 'Repository not found'
            }), 404
    except Exception as e:
        import traceback
        error_details = traceback.format_exc()
        print(f"Error in remove_saved_repository: {str(e)}")
        print(error_details)
        return jsonify({'status': 'error', 'message': str(e)}), 500

@api_v3.route('/plugins/store/list', methods=['GET'])
def list_plugin_store():
    """Search plugin store"""
    try:
        if not api_v3.plugin_store_manager:
            return jsonify({'status': 'error', 'message': 'Plugin store manager not initialized'}), 500
        
        query = request.args.get('query', '')
        category = request.args.get('category', '')
        tags = request.args.getlist('tags')
        # Default to fetching commit metadata to ensure accurate commit timestamps
        fetch_commit_param = request.args.get('fetch_commit_info', request.args.get('fetch_latest_versions', '')).lower()
        fetch_commit = fetch_commit_param != 'false'

        # Search plugins from the registry (including saved repositories)
        plugins = api_v3.plugin_store_manager.search_plugins(
            query=query, 
            category=category,
            tags=tags,
            fetch_commit_info=fetch_commit,
            include_saved_repos=True,
            saved_repositories_manager=api_v3.saved_repositories_manager
        )
        
        # Format plugins for the web interface
        formatted_plugins = []
        for plugin in plugins:
            formatted_plugins.append({
                'id': plugin.get('id'),
                'name': plugin.get('name'),
                'author': plugin.get('author'),
                'category': plugin.get('category'),
                'description': plugin.get('description'),
                'tags': plugin.get('tags', []),
                'stars': plugin.get('stars', 0),
                'verified': plugin.get('verified', False),
                'repo': plugin.get('repo', ''),
                'last_updated': plugin.get('last_updated') or plugin.get('last_updated_iso', ''),
                'last_updated_iso': plugin.get('last_updated_iso', ''),
                'last_commit': plugin.get('last_commit') or plugin.get('last_commit_sha'),
                'last_commit_message': plugin.get('last_commit_message'),
                'last_commit_author': plugin.get('last_commit_author'),
                'branch': plugin.get('branch') or plugin.get('default_branch'),
                'default_branch': plugin.get('default_branch')
            })

        return jsonify({'status': 'success', 'data': {'plugins': formatted_plugins}})
    except Exception as e:
        import traceback
        error_details = traceback.format_exc()
        print(f"Error in list_plugin_store: {str(e)}")
        print(error_details)
        return jsonify({'status': 'error', 'message': str(e)}), 500

@api_v3.route('/plugins/store/github-status', methods=['GET'])
def get_github_auth_status():
    """Check if GitHub authentication is configured"""
    try:
        if not api_v3.plugin_store_manager:
            return jsonify({'status': 'error', 'message': 'Plugin store manager not initialized'}), 500
        
        # Check if GitHub token is configured
        has_token = api_v3.plugin_store_manager.github_token is not None and len(api_v3.plugin_store_manager.github_token) > 0
        
        return jsonify({
            'status': 'success',
            'data': {
                'authenticated': has_token,
                'rate_limit': 5000 if has_token else 60,
                'message': 'GitHub API authenticated' if has_token else 'No GitHub token configured'
            }
        })
    except Exception as e:
        import traceback
        error_details = traceback.format_exc()
        print(f"Error in get_github_auth_status: {str(e)}")
        print(error_details)
        return jsonify({'status': 'error', 'message': str(e)}), 500

@api_v3.route('/plugins/store/refresh', methods=['POST'])
def refresh_plugin_store():
    """Refresh plugin store repository"""
    try:
        if not api_v3.plugin_store_manager:
            return jsonify({'status': 'error', 'message': 'Plugin store manager not initialized'}), 500
        
        data = request.get_json() or {}
        fetch_commit_info = data.get('fetch_commit_info', data.get('fetch_latest_versions', False))
        
        # Force refresh the registry
        registry = api_v3.plugin_store_manager.fetch_registry(force_refresh=True)
        plugin_count = len(registry.get('plugins', []))
        
        message = 'Plugin store refreshed'
        if fetch_commit_info:
            message += ' (with refreshed commit metadata from GitHub)'
        
        return jsonify({
            'status': 'success', 
            'message': message, 
            'plugin_count': plugin_count
        })
    except Exception as e:
        import traceback
        error_details = traceback.format_exc()
        print(f"Error in refresh_plugin_store: {str(e)}")
        print(error_details)
        return jsonify({'status': 'error', 'message': str(e)}), 500

def deep_merge(base_dict, update_dict):
    """
    Deep merge update_dict into base_dict.
    For nested dicts, recursively merge. For other types, update_dict takes precedence.
    """
    result = base_dict.copy()
    for key, value in update_dict.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            # Recursively merge nested dicts
            result[key] = deep_merge(result[key], value)
        else:
            # For non-dict values or new keys, use the update value
            result[key] = value
    return result

@api_v3.route('/plugins/config', methods=['POST'])
def save_plugin_config():
    """Save plugin configuration, separating secrets from regular config"""
    try:
        if not api_v3.config_manager:
            return error_response(
                ErrorCode.SYSTEM_ERROR,
                'Config manager not initialized',
                status_code=500
            )

        # Validate request
        data, error = validate_request_json(['plugin_id'])
        if error:
            return error

        plugin_id = data['plugin_id']
        plugin_config = data.get('config', {})
        
        # Get schema manager instance
        schema_mgr = api_v3.schema_manager
        if not schema_mgr:
            return error_response(
                ErrorCode.SYSTEM_ERROR,
                'Schema manager not initialized',
                status_code=500
            )
        
        # Load plugin schema using SchemaManager (force refresh to get latest schema)
        schema = schema_mgr.load_schema(plugin_id, use_cache=False)
        
        # PRE-PROCESSING: Preserve 'enabled' state if not in request
        # This prevents overwriting the enabled state when saving config from a form that doesn't include the toggle
        if 'enabled' not in plugin_config:
            try:
                current_config = api_v3.config_manager.load_config()
                if plugin_id in current_config and 'enabled' in current_config[plugin_id]:
                    plugin_config['enabled'] = current_config[plugin_id]['enabled']
                    # logger.debug(f"Preserving enabled state for {plugin_id}: {plugin_config['enabled']}")
                elif api_v3.plugin_manager:
                    # Fallback to plugin instance if config doesn't have it
                    plugin_instance = api_v3.plugin_manager.get_plugin(plugin_id)
                    if plugin_instance:
                        plugin_config['enabled'] = plugin_instance.enabled
                # Final fallback: default to True if plugin is loaded (matches BasePlugin default)
                if 'enabled' not in plugin_config:
                    plugin_config['enabled'] = True
            except Exception as e:
                print(f"Error preserving enabled state: {e}")
                # Default to True on error to avoid disabling plugins
                plugin_config['enabled'] = True

        # Find secret fields (supports nested schemas)
        secret_fields = set()
        
        def find_secret_fields(properties, prefix=''):
            """Recursively find fields marked with x-secret: true"""
            fields = set()
            if not isinstance(properties, dict):
                return fields
            for field_name, field_props in properties.items():
                full_path = f"{prefix}.{field_name}" if prefix else field_name
                if isinstance(field_props, dict) and field_props.get('x-secret', False):
                    fields.add(full_path)
                # Check nested objects
                if isinstance(field_props, dict) and field_props.get('type') == 'object' and 'properties' in field_props:
                    fields.update(find_secret_fields(field_props['properties'], full_path))
            return fields
        
        if schema and 'properties' in schema:
            secret_fields = find_secret_fields(schema['properties'])
        
        # Apply defaults from schema to config BEFORE validation
        # This ensures required fields with defaults are present before validation
        # Store preserved enabled value before merge to protect it from defaults
        preserved_enabled = None
        if 'enabled' in plugin_config:
            preserved_enabled = plugin_config['enabled']
        
        if schema:
            defaults = schema_mgr.generate_default_config(plugin_id, use_cache=True)
            plugin_config = schema_mgr.merge_with_defaults(plugin_config, defaults)
        
        # Ensure enabled state is preserved after defaults merge
        # Defaults should not overwrite an explicitly preserved enabled value
        if preserved_enabled is not None:
            # Restore preserved value if it was changed by defaults merge
            if plugin_config.get('enabled') != preserved_enabled:
                plugin_config['enabled'] = preserved_enabled
        
        # Normalize config data: convert string numbers to integers/floats where schema expects numbers
        # This handles form data which sends everything as strings
        def normalize_config_values(config, schema_props, prefix=''):
            """Recursively normalize config values based on schema types"""
            if not isinstance(config, dict) or not isinstance(schema_props, dict):
                return config
            
            normalized = {}
            for key, value in config.items():
                field_path = f"{prefix}.{key}" if prefix else key
                
                if key not in schema_props:
                    # Field not in schema, keep as-is (will be caught by additionalProperties check if needed)
                    normalized[key] = value
                    continue
                
                prop_schema = schema_props[key]
                prop_type = prop_schema.get('type')
                
                # Handle union types (e.g., ["integer", "null"])
                if isinstance(prop_type, list):
                    # Check if null is allowed and value is empty/null
                    if 'null' in prop_type:
                        # Handle various representations of null/empty
                        if value is None:
                            normalized[key] = None
                            continue
                        elif isinstance(value, str):
                            # Strip whitespace and check for null representations
                            value_stripped = value.strip()
                            if value_stripped == '' or value_stripped.lower() in ('null', 'none', 'undefined'):
                                normalized[key] = None
                                continue
                    
                    # Try to normalize based on non-null types in the union
                    # Check integer first (more specific than number)
                    if 'integer' in prop_type:
                        if isinstance(value, str):
                            value_stripped = value.strip()
                            if value_stripped == '':
                                # Empty string with null allowed - already handled above, but double-check
                                if 'null' in prop_type:
                                    normalized[key] = None
                                    continue
                            try:
                                normalized[key] = int(value_stripped)
                                continue
                            except (ValueError, TypeError):
                                pass
                        elif isinstance(value, (int, float)):
                            normalized[key] = int(value)
                            continue
                    
                    # Check number (less specific, but handles floats)
                    if 'number' in prop_type:
                        if isinstance(value, str):
                            value_stripped = value.strip()
                            if value_stripped == '':
                                # Empty string with null allowed - already handled above, but double-check
                                if 'null' in prop_type:
                                    normalized[key] = None
                                    continue
                            try:
                                normalized[key] = float(value_stripped)
                                continue
                            except (ValueError, TypeError):
                                pass
                        elif isinstance(value, (int, float)):
                            normalized[key] = float(value)
                            continue
                    
                    # Check boolean
                    if 'boolean' in prop_type:
                        if isinstance(value, str):
                            normalized[key] = value.strip().lower() in ('true', '1', 'on', 'yes')
                            continue
                    
                    # If no conversion worked and null is allowed, try to set to None
                    # This handles cases where the value is an empty string or can't be converted
                    if 'null' in prop_type:
                        if isinstance(value, str):
                            value_stripped = value.strip()
                            if value_stripped == '' or value_stripped.lower() in ('null', 'none', 'undefined'):
                                normalized[key] = None
                                continue
                        # If it's already None, keep it
                        if value is None:
                            normalized[key] = None
                            continue
                    
                    # If no conversion worked, keep original value (will fail validation, but that's expected)
                    # Log a warning for debugging
                    logger.warning(f"Could not normalize field {field_path}: value={repr(value)}, type={type(value)}, schema_type={prop_type}")
                    normalized[key] = value
                    continue
                
                if isinstance(value, dict) and prop_type == 'object' and 'properties' in prop_schema:
                    # Recursively normalize nested objects
                    normalized[key] = normalize_config_values(value, prop_schema['properties'], field_path)
                elif isinstance(value, list) and prop_type == 'array' and 'items' in prop_schema:
                    # Normalize array items
                    items_schema = prop_schema['items']
                    item_type = items_schema.get('type')
                    
                    # Handle union types in array items
                    if isinstance(item_type, list):
                        normalized_array = []
                        for v in value:
                            # Check if null is allowed
                            if 'null' in item_type:
                                if v is None or v == '' or (isinstance(v, str) and v.lower() in ('null', 'none')):
                                    normalized_array.append(None)
                                    continue
                            
                            # Try to normalize based on non-null types
                            if 'integer' in item_type:
                                if isinstance(v, str):
                                    try:
                                        normalized_array.append(int(v))
                                        continue
                                    except (ValueError, TypeError):
                                        pass
                                elif isinstance(v, (int, float)):
                                    normalized_array.append(int(v))
                                    continue
                            elif 'number' in item_type:
                                if isinstance(v, str):
                                    try:
                                        normalized_array.append(float(v))
                                        continue
                                    except (ValueError, TypeError):
                                        pass
                                elif isinstance(v, (int, float)):
                                    normalized_array.append(float(v))
                                    continue
                            
                            # If no conversion worked, keep original value
                            normalized_array.append(v)
                        normalized[key] = normalized_array
                    elif item_type == 'integer':
                        # Convert string numbers to integers
                        normalized_array = []
                        for v in value:
                            if isinstance(v, str):
                                try:
                                    normalized_array.append(int(v))
                                except (ValueError, TypeError):
                                    normalized_array.append(v)
                            elif isinstance(v, (int, float)):
                                normalized_array.append(int(v))
                            else:
                                normalized_array.append(v)
                        normalized[key] = normalized_array
                    elif item_type == 'number':
                        # Convert string numbers to floats
                        normalized_array = []
                        for v in value:
                            if isinstance(v, str):
                                try:
                                    normalized_array.append(float(v))
                                except (ValueError, TypeError):
                                    normalized_array.append(v)
                            else:
                                normalized_array.append(v)
                        normalized[key] = normalized_array
                    elif item_type == 'object' and 'properties' in items_schema:
                        # Recursively normalize array of objects
                        normalized_array = []
                        for v in value:
                            if isinstance(v, dict):
                                normalized_array.append(
                                    normalize_config_values(v, items_schema['properties'], f"{field_path}[]")
                                )
                            else:
                                normalized_array.append(v)
                        normalized[key] = normalized_array
                    else:
                        normalized[key] = value
                elif prop_type == 'integer':
                    # Convert string to integer
                    if isinstance(value, str):
                        try:
                            normalized[key] = int(value)
                        except (ValueError, TypeError):
                            normalized[key] = value
                    else:
                        normalized[key] = value
                elif prop_type == 'number':
                    # Convert string to float
                    if isinstance(value, str):
                        try:
                            normalized[key] = float(value)
                        except (ValueError, TypeError):
                            normalized[key] = value
                    else:
                        normalized[key] = value
                elif prop_type == 'boolean':
                    # Convert string booleans
                    if isinstance(value, str):
                        normalized[key] = value.lower() in ('true', '1', 'on', 'yes')
                    else:
                        normalized[key] = value
                else:
                    normalized[key] = value
            
            return normalized
        
        # Normalize config before validation
        if schema and 'properties' in schema:
            plugin_config = normalize_config_values(plugin_config, schema['properties'])
        
        # Debug logging for union type fields (temporary)
        if 'rotation_settings' in plugin_config and 'random_seed' in plugin_config.get('rotation_settings', {}):
            seed_value = plugin_config['rotation_settings']['random_seed']
            logger.debug(f"After normalization, random_seed value: {repr(seed_value)}, type: {type(seed_value)}")
        
        # Validate configuration against schema before saving
        if schema:
            # Log what we're validating for debugging
            import logging
            logger = logging.getLogger(__name__)
            logger.info(f"Validating config for {plugin_id}")
            logger.info(f"Config keys being validated: {list(plugin_config.keys())}")
            logger.info(f"Full config: {plugin_config}")
            
            # Get enhanced schema keys (including injected core properties)
            # We need to create an enhanced schema to get the actual allowed keys
            import copy
            enhanced_schema = copy.deepcopy(schema)
            if "properties" not in enhanced_schema:
                enhanced_schema["properties"] = {}
            
            # Core properties that are always injected during validation
            core_properties = ["enabled", "display_duration", "live_priority"]
            for prop_name in core_properties:
                if prop_name not in enhanced_schema["properties"]:
                    # Add placeholder to get the full list of allowed keys
                    enhanced_schema["properties"][prop_name] = {"type": "any"}
            
            is_valid, validation_errors = schema_mgr.validate_config_against_schema(
                plugin_config, schema, plugin_id
            )
            if not is_valid:
                # Log validation errors for debugging
                logger.error(f"Config validation failed for {plugin_id}")
                logger.error(f"Validation errors: {validation_errors}")
                logger.error(f"Config that failed: {plugin_config}")
                logger.error(f"Schema properties: {list(enhanced_schema.get('properties', {}).keys())}")
                # Also print to console for immediate visibility
                print(f"[ERROR] Config validation failed for {plugin_id}")
                print(f"[ERROR] Validation errors: {validation_errors}")
                print(f"[ERROR] Config keys: {list(plugin_config.keys())}")
                print(f"[ERROR] Schema property keys: {list(enhanced_schema.get('properties', {}).keys())}")
                return error_response(
                    ErrorCode.CONFIG_VALIDATION_FAILED,
                    'Configuration validation failed',
                    details='; '.join(validation_errors) if validation_errors else 'Unknown validation error',
                    context={
                        'plugin_id': plugin_id,
                        'validation_errors': validation_errors,
                        'config_keys': list(plugin_config.keys()),
                        'schema_keys': list(enhanced_schema.get('properties', {}).keys())
                    },
                    suggested_fixes=[
                        'Review validation errors above',
                        'Check config against schema',
                        'Verify all required fields are present'
                    ],
                    status_code=400
                )

        # Separate secrets from regular config (handles nested configs)
        def separate_secrets(config, secrets_set, prefix=''):
            """Recursively separate secret fields from regular config"""
            regular = {}
            secrets = {}
            
            for key, value in config.items():
                full_path = f"{prefix}.{key}" if prefix else key
                
                if isinstance(value, dict):
                    # Recursively handle nested dicts
                    nested_regular, nested_secrets = separate_secrets(value, secrets_set, full_path)
                    if nested_regular:
                        regular[key] = nested_regular
                    if nested_secrets:
                        secrets[key] = nested_secrets
                elif full_path in secrets_set:
                    secrets[key] = value
                else:
                    regular[key] = value
            
            return regular, secrets
        
        regular_config, secrets_config = separate_secrets(plugin_config, secret_fields)

        # Get current configs
        current_config = api_v3.config_manager.load_config()
        current_secrets = api_v3.config_manager.get_raw_file_content('secrets')

        # Deep merge plugin configuration in main config (preserves nested structures)
        if plugin_id not in current_config:
            current_config[plugin_id] = {}
        
        # Debug logging for live_priority before merge
        if plugin_id == 'football-scoreboard':
            print(f"[DEBUG] Before merge - current NFL live_priority: {current_config[plugin_id].get('nfl', {}).get('live_priority')}")
            print(f"[DEBUG] Before merge - regular_config NFL live_priority: {regular_config.get('nfl', {}).get('live_priority')}")
        
        current_config[plugin_id] = deep_merge(current_config[plugin_id], regular_config)
        
        # Debug logging for live_priority after merge
        if plugin_id == 'football-scoreboard':
            print(f"[DEBUG] After merge - NFL live_priority: {current_config[plugin_id].get('nfl', {}).get('live_priority')}")
            print(f"[DEBUG] After merge - NCAA FB live_priority: {current_config[plugin_id].get('ncaa_fb', {}).get('live_priority')}")

        # Deep merge plugin secrets in secrets config
        if secrets_config:
            if plugin_id not in current_secrets:
                current_secrets[plugin_id] = {}
            current_secrets[plugin_id] = deep_merge(current_secrets[plugin_id], secrets_config)
            # Save secrets file
            api_v3.config_manager.save_raw_file_content('secrets', current_secrets)

        # Save the updated main config using atomic save
        success, error_msg = _save_config_atomic(api_v3.config_manager, current_config, create_backup=True)
        if not success:
            return error_response(
                ErrorCode.CONFIG_SAVE_FAILED,
                f"Failed to save configuration: {error_msg}",
                status_code=500
            )

        # If the plugin is loaded, notify it of the config change with merged config
        try:
            if api_v3.plugin_manager:
                plugin_instance = api_v3.plugin_manager.get_plugin(plugin_id)
                if plugin_instance:
                    # Reload merged config (includes secrets) and pass the plugin-specific section
                    merged_config = api_v3.config_manager.load_config()
                    plugin_full_config = merged_config.get(plugin_id, {})
                    if hasattr(plugin_instance, 'on_config_change'):
                        plugin_instance.on_config_change(plugin_full_config)
                    
                    # Update plugin state manager and call lifecycle methods based on enabled state
                    # This ensures the plugin state is synchronized with the config
                    enabled = plugin_full_config.get('enabled', plugin_instance.enabled)
                    
                    # Update state manager if available
                    if api_v3.plugin_state_manager:
                        api_v3.plugin_state_manager.set_plugin_enabled(plugin_id, enabled)
                    
                    # Call lifecycle methods to ensure plugin state matches config
                    try:
                        if enabled:
                            if hasattr(plugin_instance, 'on_enable'):
                                plugin_instance.on_enable()
                        else:
                            if hasattr(plugin_instance, 'on_disable'):
                                plugin_instance.on_disable()
                    except Exception as lifecycle_error:
                        # Log the error but don't fail the save - config is already saved
                        import logging
                        logging.warning(f"Lifecycle method error for {plugin_id}: {lifecycle_error}", exc_info=True)
        except Exception as hook_err:
            # Do not fail the save if hook fails; just log
            print(f"Warning: on_config_change failed for {plugin_id}: {hook_err}")

        secret_count = len(secrets_config)
        message = f'Plugin {plugin_id} configuration saved successfully'
        if secret_count > 0:
            message += f' ({secret_count} secret field(s) saved to config_secrets.json)'

        return success_response(message=message)
    except Exception as e:
        from src.web_interface.errors import WebInterfaceError
        error = WebInterfaceError.from_exception(e, ErrorCode.CONFIG_SAVE_FAILED)
        if api_v3.operation_history:
            api_v3.operation_history.record_operation(
                "configure",
                plugin_id=data.get('plugin_id') if 'data' in locals() else None,
                status="failed",
                error=str(e)
            )
        return error_response(
            error.error_code,
            error.message,
            details=error.details,
            context=error.context,
            status_code=500
        )

@api_v3.route('/plugins/schema', methods=['GET'])
def get_plugin_schema():
    """Get plugin configuration schema"""
    try:
        plugin_id = request.args.get('plugin_id')
        if not plugin_id:
            return jsonify({'status': 'error', 'message': 'plugin_id required'}), 400

        # Get schema manager instance
        schema_mgr = api_v3.schema_manager
        if not schema_mgr:
            return jsonify({'status': 'error', 'message': 'Schema manager not initialized'}), 500
        
        # Load schema using SchemaManager (uses caching)
        schema = schema_mgr.load_schema(plugin_id, use_cache=True)
        
        if schema:
            return jsonify({'status': 'success', 'data': {'schema': schema}})

        # Return a simple default schema if file not found
        default_schema = {
            'type': 'object',
            'properties': {
                'enabled': {
                    'type': 'boolean',
                    'title': 'Enable Plugin',
                    'description': 'Enable or disable this plugin',
                    'default': True
                },
                'display_duration': {
                    'type': 'integer',
                    'title': 'Display Duration',
                    'description': 'How long to show content (seconds)',
                    'minimum': 5,
                    'maximum': 300,
                    'default': 30
                }
            }
        }

        return jsonify({'status': 'success', 'data': {'schema': default_schema}})
    except Exception as e:
        import traceback
        error_details = traceback.format_exc()
        print(f"Error in get_plugin_schema: {str(e)}")
        print(error_details)
        return jsonify({'status': 'error', 'message': str(e)}), 500

@api_v3.route('/plugins/config/reset', methods=['POST'])
def reset_plugin_config():
    """Reset plugin configuration to schema defaults"""
    try:
        if not api_v3.config_manager:
            return jsonify({'status': 'error', 'message': 'Config manager not initialized'}), 500

        data = request.get_json() or {}
        plugin_id = data.get('plugin_id')
        preserve_secrets = data.get('preserve_secrets', True)
        
        if not plugin_id:
            return jsonify({'status': 'error', 'message': 'plugin_id required'}), 400
        
        # Get schema manager instance
        schema_mgr = api_v3.schema_manager
        if not schema_mgr:
            return jsonify({'status': 'error', 'message': 'Schema manager not initialized'}), 500
        
        # Generate defaults from schema
        defaults = schema_mgr.generate_default_config(plugin_id, use_cache=True)
        
        # Get current configs
        current_config = api_v3.config_manager.load_config()
        current_secrets = api_v3.config_manager.get_raw_file_content('secrets')
        
        # Load schema to identify secret fields
        schema = schema_mgr.load_schema(plugin_id, use_cache=True)
        secret_fields = set()
        
        def find_secret_fields(properties, prefix=''):
            """Recursively find fields marked with x-secret: true"""
            fields = set()
            if not isinstance(properties, dict):
                return fields
            for field_name, field_props in properties.items():
                full_path = f"{prefix}.{field_name}" if prefix else field_name
                if isinstance(field_props, dict) and field_props.get('x-secret', False):
                    fields.add(full_path)
                if isinstance(field_props, dict) and field_props.get('type') == 'object' and 'properties' in field_props:
                    fields.update(find_secret_fields(field_props['properties'], full_path))
            return fields
        
        if schema and 'properties' in schema:
            secret_fields = find_secret_fields(schema['properties'])
        
        # Separate defaults into regular and secret configs
        def separate_secrets(config, secrets_set, prefix=''):
            """Recursively separate secret fields from regular config"""
            regular = {}
            secrets = {}
            for key, value in config.items():
                full_path = f"{prefix}.{key}" if prefix else key
                if isinstance(value, dict):
                    nested_regular, nested_secrets = separate_secrets(value, secrets_set, full_path)
                    if nested_regular:
                        regular[key] = nested_regular
                    if nested_secrets:
                        secrets[key] = nested_secrets
                elif full_path in secrets_set:
                    secrets[key] = value
                else:
                    regular[key] = value
            return regular, secrets
        
        default_regular, default_secrets = separate_secrets(defaults, secret_fields)
        
        # Update main config with defaults
        current_config[plugin_id] = default_regular
        
        # Update secrets config (preserve existing secrets if preserve_secrets=True)
        if preserve_secrets:
            # Keep existing secrets for this plugin
            if plugin_id in current_secrets:
                # Merge defaults with existing secrets
                existing_secrets = current_secrets[plugin_id]
                for key, value in default_secrets.items():
                    if key not in existing_secrets or not existing_secrets[key]:
                        existing_secrets[key] = value
            else:
                current_secrets[plugin_id] = default_secrets
        else:
            # Replace all secrets with defaults
            current_secrets[plugin_id] = default_secrets
        
        # Save updated configs
        api_v3.config_manager.save_config(current_config)
        if default_secrets or not preserve_secrets:
            api_v3.config_manager.save_raw_file_content('secrets', current_secrets)
        
        # Notify plugin of config change if loaded
        try:
            if api_v3.plugin_manager:
                plugin_instance = api_v3.plugin_manager.get_plugin(plugin_id)
                if plugin_instance:
                    merged_config = api_v3.config_manager.load_config()
                    plugin_full_config = merged_config.get(plugin_id, {})
                    if hasattr(plugin_instance, 'on_config_change'):
                        plugin_instance.on_config_change(plugin_full_config)
        except Exception as hook_err:
            print(f"Warning: on_config_change failed for {plugin_id}: {hook_err}")
        
        return jsonify({
            'status': 'success',
            'message': f'Plugin {plugin_id} configuration reset to defaults',
            'data': {'config': defaults}
        })
    except Exception as e:
        import traceback
        error_details = traceback.format_exc()
        print(f"Error in reset_plugin_config: {str(e)}")
        print(error_details)
        return jsonify({'status': 'error', 'message': str(e)}), 500

@api_v3.route('/plugins/action', methods=['POST'])
def execute_plugin_action():
    """Execute a plugin-defined action (e.g., authentication)"""
    try:
        data = request.get_json() or {}
        plugin_id = data.get('plugin_id')
        action_id = data.get('action_id')
        action_params = data.get('params', {})
        
        if not plugin_id or not action_id:
            return jsonify({'status': 'error', 'message': 'plugin_id and action_id required'}), 400
        
        # Get plugin directory
        if api_v3.plugin_manager:
            plugin_dir = api_v3.plugin_manager.get_plugin_directory(plugin_id)
        else:
            plugin_dir = PROJECT_ROOT / 'plugins' / plugin_id
        
        if not plugin_dir or not Path(plugin_dir).exists():
            return jsonify({'status': 'error', 'message': f'Plugin {plugin_id} not found'}), 404
        
        # Load manifest to get action definition
        manifest_path = Path(plugin_dir) / 'manifest.json'
        if not manifest_path.exists():
            return jsonify({'status': 'error', 'message': 'Plugin manifest not found'}), 404
        
        with open(manifest_path, 'r', encoding='utf-8') as f:
            manifest = json.load(f)
        
        web_ui_actions = manifest.get('web_ui_actions', [])
        action_def = None
        for action in web_ui_actions:
            if action.get('id') == action_id:
                action_def = action
                break
        
        if not action_def:
            return jsonify({'status': 'error', 'message': f'Action {action_id} not found in plugin manifest'}), 404
        
        # Set LEDMATRIX_ROOT environment variable
        env = os.environ.copy()
        env['LEDMATRIX_ROOT'] = str(PROJECT_ROOT)
        
        # Execute action based on type
        action_type = action_def.get('type', 'script')
        
        if action_type == 'script':
            # Execute a Python script
            script_path = action_def.get('script')
            if not script_path:
                return jsonify({'status': 'error', 'message': 'Script path not defined for action'}), 400
            
            script_file = Path(plugin_dir) / script_path
            if not script_file.exists():
                return jsonify({'status': 'error', 'message': f'Script not found: {script_path}'}), 404
            
            # Handle multi-step actions (like Spotify OAuth)
            step = action_params.get('step')
            
            if step == '2' and action_params.get('redirect_url'):
                # Step 2: Complete authentication with redirect URL
                redirect_url = action_params.get('redirect_url')
                import tempfile
                import json as json_lib
                
                redirect_url_escaped = json_lib.dumps(redirect_url)
                with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as wrapper:
                    wrapper.write(f'''import sys
import subprocess
import os

# Set LEDMATRIX_ROOT
os.environ['LEDMATRIX_ROOT'] = r"{PROJECT_ROOT}"

# Run the script and provide redirect URL
proc = subprocess.Popen(
    [sys.executable, r"{script_file}"],
    stdin=subprocess.PIPE,
    stdout=subprocess.PIPE,
    stderr=subprocess.STDOUT,
    text=True,
    env=os.environ
)

# Send redirect URL to stdin
redirect_url = {redirect_url_escaped}
stdout, _ = proc.communicate(input=redirect_url + "\\n", timeout=120)
print(stdout)
sys.exit(proc.returncode)
''')
                    wrapper_path = wrapper.name
                
                try:
                    result = subprocess.run(
                        ['python3', wrapper_path],
                        capture_output=True,
                        text=True,
                        timeout=120,
                        env=env
                    )
                    os.unlink(wrapper_path)
                    
                    if result.returncode == 0:
                        return jsonify({
                            'status': 'success',
                            'message': action_def.get('success_message', 'Action completed successfully'),
                            'output': result.stdout
                        })
                    else:
                        return jsonify({
                            'status': 'error',
                            'message': action_def.get('error_message', 'Action failed'),
                            'output': result.stdout + result.stderr
                        }), 400
                except subprocess.TimeoutExpired:
                    if os.path.exists(wrapper_path):
                        os.unlink(wrapper_path)
                    return jsonify({'status': 'error', 'message': 'Action timed out'}), 408
            else:
                # Step 1: Get initial data (like auth URL)
                # For OAuth flows, we might need to import the script as a module
                if action_def.get('oauth_flow'):
                    # Import script as module to get auth URL
                    import sys
                    import importlib.util
                    
                    spec = importlib.util.spec_from_file_location("plugin_action", script_file)
                    action_module = importlib.util.module_from_spec(spec)
                    sys.modules["plugin_action"] = action_module
                    
                    try:
                        spec.loader.exec_module(action_module)
                        
                        # Try to get auth URL using common patterns
                        auth_url = None
                        if hasattr(action_module, 'get_auth_url'):
                            auth_url = action_module.get_auth_url()
                        elif hasattr(action_module, 'load_spotify_credentials'):
                            # Spotify-specific pattern
                            client_id, client_secret, redirect_uri = action_module.load_spotify_credentials()
                            if all([client_id, client_secret, redirect_uri]):
                                from spotipy.oauth2 import SpotifyOAuth
                                sp_oauth = SpotifyOAuth(
                                    client_id=client_id,
                                    client_secret=client_secret,
                                    redirect_uri=redirect_uri,
                                    scope=getattr(action_module, 'SCOPE', ''),
                                    cache_path=getattr(action_module, 'SPOTIFY_AUTH_CACHE_PATH', None),
                                    open_browser=False
                                )
                                auth_url = sp_oauth.get_authorize_url()
                        
                        if auth_url:
                            return jsonify({
                                'status': 'success',
                                'message': action_def.get('step1_message', 'Authorization URL generated'),
                                'auth_url': auth_url,
                                'requires_step2': True
                            })
                        else:
                            return jsonify({
                                'status': 'error',
                                'message': 'Could not generate authorization URL'
                            }), 400
                    except Exception as e:
                        import traceback
                        error_details = traceback.format_exc()
                        print(f"Error executing action step 1: {e}")
                        print(error_details)
                        return jsonify({
                            'status': 'error',
                            'message': f'Error executing action: {str(e)}'
                        }), 500
                else:
                    # Simple script execution
                    result = subprocess.run(
                        ['python3', str(script_file)],
                        capture_output=True,
                        text=True,
                        timeout=60,
                        env=env
                    )
                    
                    if result.returncode == 0:
                        return jsonify({
                            'status': 'success',
                            'message': action_def.get('success_message', 'Action completed successfully'),
                            'output': result.stdout
                        })
                    else:
                        return jsonify({
                            'status': 'error',
                            'message': action_def.get('error_message', 'Action failed'),
                            'output': result.stdout + result.stderr
                        }), 400
        
        elif action_type == 'endpoint':
            # Call a plugin-defined HTTP endpoint (future feature)
            return jsonify({'status': 'error', 'message': 'Endpoint actions not yet implemented'}), 501
        
        else:
            return jsonify({'status': 'error', 'message': f'Unknown action type: {action_type}'}), 400
            
    except subprocess.TimeoutExpired:
        return jsonify({'status': 'error', 'message': 'Action timed out'}), 408
    except Exception as e:
        import traceback
        error_details = traceback.format_exc()
        print(f"Error in execute_plugin_action: {str(e)}")
        print(error_details)
        return jsonify({'status': 'error', 'message': str(e)}), 500

@api_v3.route('/plugins/authenticate/spotify', methods=['POST'])
def authenticate_spotify():
    """Run Spotify authentication script"""
    try:
        data = request.get_json() or {}
        redirect_url = data.get('redirect_url', '').strip()
        
        # Get plugin directory
        plugin_id = 'ledmatrix-music'
        if api_v3.plugin_manager:
            plugin_dir = api_v3.plugin_manager.get_plugin_directory(plugin_id)
        else:
            plugin_dir = PROJECT_ROOT / 'plugins' / plugin_id
        
        if not plugin_dir or not Path(plugin_dir).exists():
            return jsonify({'status': 'error', 'message': f'Plugin {plugin_id} not found'}), 404
        
        auth_script = Path(plugin_dir) / 'authenticate_spotify.py'
        if not auth_script.exists():
            return jsonify({'status': 'error', 'message': 'Authentication script not found'}), 404
        
        # Set LEDMATRIX_ROOT environment variable
        env = os.environ.copy()
        env['LEDMATRIX_ROOT'] = str(PROJECT_ROOT)
        
        if redirect_url:
            # Step 2: Complete authentication with redirect URL
            # Create a wrapper script that provides the redirect URL as input
            import tempfile
            
            # Create a wrapper script that provides the redirect URL
            import json
            redirect_url_escaped = json.dumps(redirect_url)  # Properly escape the URL
            with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as wrapper:
                wrapper.write(f'''import sys
import subprocess
import os

# Set LEDMATRIX_ROOT
os.environ['LEDMATRIX_ROOT'] = r"{PROJECT_ROOT}"

# Run the auth script and provide redirect URL
proc = subprocess.Popen(
    [sys.executable, r"{auth_script}"],
    stdin=subprocess.PIPE,
    stdout=subprocess.PIPE,
    stderr=subprocess.STDOUT,
    text=True,
    env=os.environ
)

# Send redirect URL to stdin
redirect_url = {redirect_url_escaped}
stdout, _ = proc.communicate(input=redirect_url + "\\n", timeout=120)
print(stdout)
sys.exit(proc.returncode)
''')
                wrapper_path = wrapper.name
            
            try:
                result = subprocess.run(
                    ['python3', wrapper_path],
                    capture_output=True,
                    text=True,
                    timeout=120,
                    env=env
                )
                os.unlink(wrapper_path)
                
                if result.returncode == 0:
                    return jsonify({
                        'status': 'success',
                        'message': 'Spotify authentication completed successfully',
                        'output': result.stdout
                    })
                else:
                    return jsonify({
                        'status': 'error',
                        'message': 'Spotify authentication failed',
                        'output': result.stdout + result.stderr
                    }), 400
            except subprocess.TimeoutExpired:
                if os.path.exists(wrapper_path):
                    os.unlink(wrapper_path)
                return jsonify({'status': 'error', 'message': 'Authentication timed out'}), 408
        else:
            # Step 1: Get authorization URL
            # Import the script's functions directly to get the auth URL
            import sys
            import importlib.util
            
            # Load the authentication script as a module
            spec = importlib.util.spec_from_file_location("auth_spotify", auth_script)
            auth_module = importlib.util.module_from_spec(spec)
            sys.modules["auth_spotify"] = auth_module
            
            # Set LEDMATRIX_ROOT before loading
            os.environ['LEDMATRIX_ROOT'] = str(PROJECT_ROOT)
            
            try:
                spec.loader.exec_module(auth_module)
                
                # Get credentials and create OAuth object
                client_id, client_secret, redirect_uri = auth_module.load_spotify_credentials()
                if not all([client_id, client_secret, redirect_uri]):
                    return jsonify({
                        'status': 'error',
                        'message': 'Could not load Spotify credentials. Please check config/config_secrets.json.'
                    }), 400
                
                from spotipy.oauth2 import SpotifyOAuth
                sp_oauth = SpotifyOAuth(
                    client_id=client_id,
                    client_secret=client_secret,
                    redirect_uri=redirect_uri,
                    scope=auth_module.SCOPE,
                    cache_path=auth_module.SPOTIFY_AUTH_CACHE_PATH,
                    open_browser=False
                )
                
                auth_url = sp_oauth.get_authorize_url()
                
                return jsonify({
                    'status': 'success',
                    'message': 'Authorization URL generated',
                    'auth_url': auth_url
                })
            except Exception as e:
                import traceback
                error_details = traceback.format_exc()
                print(f"Error getting Spotify auth URL: {e}")
                print(error_details)
                return jsonify({
                    'status': 'error',
                    'message': f'Error generating authorization URL: {str(e)}'
                }), 500
                
    except Exception as e:
        import traceback
        error_details = traceback.format_exc()
        print(f"Error in authenticate_spotify: {str(e)}")
        print(error_details)
        return jsonify({'status': 'error', 'message': str(e)}), 500

@api_v3.route('/plugins/authenticate/ytm', methods=['POST'])
def authenticate_ytm():
    """Run YouTube Music authentication script"""
    try:
        # Get plugin directory
        plugin_id = 'ledmatrix-music'
        if api_v3.plugin_manager:
            plugin_dir = api_v3.plugin_manager.get_plugin_directory(plugin_id)
        else:
            plugin_dir = PROJECT_ROOT / 'plugins' / plugin_id
        
        if not plugin_dir or not Path(plugin_dir).exists():
            return jsonify({'status': 'error', 'message': f'Plugin {plugin_id} not found'}), 404
        
        auth_script = Path(plugin_dir) / 'authenticate_ytm.py'
        if not auth_script.exists():
            return jsonify({'status': 'error', 'message': 'Authentication script not found'}), 404
        
        # Set LEDMATRIX_ROOT environment variable
        env = os.environ.copy()
        env['LEDMATRIX_ROOT'] = str(PROJECT_ROOT)
        
        # Run the authentication script
        result = subprocess.run(
            ['python3', str(auth_script)],
            capture_output=True,
            text=True,
            timeout=60,
            env=env
        )
        
        if result.returncode == 0:
            return jsonify({
                'status': 'success',
                'message': 'YouTube Music authentication completed successfully',
                'output': result.stdout
            })
        else:
            return jsonify({
                'status': 'error',
                'message': 'YouTube Music authentication failed',
                'output': result.stdout + result.stderr
            }), 400
            
    except subprocess.TimeoutExpired:
        return jsonify({'status': 'error', 'message': 'Authentication timed out'}), 408
    except Exception as e:
        import traceback
        error_details = traceback.format_exc()
        print(f"Error in authenticate_ytm: {str(e)}")
        print(error_details)
        return jsonify({'status': 'error', 'message': str(e)}), 500

@api_v3.route('/fonts/catalog', methods=['GET'])
def get_fonts_catalog():
    """Get fonts catalog"""
    try:
        # This would integrate with the actual font system
        # For now, return sample fonts
        catalog = {
            'press_start': 'assets/fonts/press-start-2p.ttf',
            'four_by_six': 'assets/fonts/4x6.bdf',
            'cozette_bdf': 'assets/fonts/cozette.bdf',
            'matrix_light_6': 'assets/fonts/matrix-light-6.bdf'
        }
        return jsonify({'status': 'success', 'data': {'catalog': catalog}})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@api_v3.route('/fonts/tokens', methods=['GET'])
def get_font_tokens():
    """Get font size tokens"""
    try:
        # This would integrate with the actual font system
        # For now, return sample tokens
        tokens = {
            'xs': 6,
            'sm': 8,
            'md': 10,
            'lg': 12,
            'xl': 14,
            'xxl': 16
        }
        return jsonify({'status': 'success', 'data': {'tokens': tokens}})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@api_v3.route('/fonts/overrides', methods=['GET'])
def get_fonts_overrides():
    """Get font overrides"""
    try:
        # This would integrate with the actual font system
        # For now, return empty overrides
        overrides = {}
        return jsonify({'status': 'success', 'data': {'overrides': overrides}})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@api_v3.route('/fonts/overrides', methods=['POST'])
def save_fonts_overrides():
    """Save font overrides"""
    try:
        data = request.get_json()
        if not data:
            return jsonify({'status': 'error', 'message': 'No data provided'}), 400

        # This would integrate with the actual font system
        return jsonify({'status': 'success', 'message': 'Font overrides saved'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@api_v3.route('/fonts/overrides/<element_key>', methods=['DELETE'])
def delete_font_override(element_key):
    """Delete font override"""
    try:
        # This would integrate with the actual font system
        return jsonify({'status': 'success', 'message': f'Font override for {element_key} deleted'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@api_v3.route('/fonts/upload', methods=['POST'])
def upload_font():
    """Upload font file"""
    try:
        if 'font_file' not in request.files:
            return jsonify({'status': 'error', 'message': 'No font file provided'}), 400

        font_file = request.files['font_file']
        font_family = request.form.get('font_family', '')

        if not font_file or not font_family:
            return jsonify({'status': 'error', 'message': 'Font file and family name required'}), 400

        # Validate file type
        allowed_extensions = ['.ttf', '.bdf']
        file_extension = font_file.filename.lower().split('.')[-1]
        if f'.{file_extension}' not in allowed_extensions:
            return jsonify({'status': 'error', 'message': 'Only .ttf and .bdf files are allowed'}), 400

        # Validate font family name
        if not font_family.replace('_', '').replace('-', '').isalnum():
            return jsonify({'status': 'error', 'message': 'Font family name must contain only letters, numbers, underscores, and hyphens'}), 400

        # This would integrate with the actual font system to save the file
        # For now, just return success
        return jsonify({'status': 'success', 'message': f'Font {font_family} uploaded successfully', 'font_family': font_family})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@api_v3.route('/plugins/assets/upload', methods=['POST'])
def upload_plugin_asset():
    """Upload asset files for a plugin"""
    try:
        plugin_id = request.form.get('plugin_id')
        if not plugin_id:
            return jsonify({'status': 'error', 'message': 'plugin_id is required'}), 400
        
        if 'files' not in request.files:
            return jsonify({'status': 'error', 'message': 'No files provided'}), 400
        
        files = request.files.getlist('files')
        if not files or all(not f.filename for f in files):
            return jsonify({'status': 'error', 'message': 'No files provided'}), 400
        
        # Validate file count
        if len(files) > 10:
            return jsonify({'status': 'error', 'message': 'Maximum 10 files per upload'}), 400
        
        # Setup plugin assets directory
        assets_dir = PROJECT_ROOT / 'assets' / 'plugins' / plugin_id / 'uploads'
        assets_dir.mkdir(parents=True, exist_ok=True)
        
        # Load metadata file
        metadata_file = assets_dir / '.metadata.json'
        if metadata_file.exists():
            with open(metadata_file, 'r') as f:
                metadata = json.load(f)
        else:
            metadata = {}
        
        uploaded_files = []
        total_size = 0
        max_size_per_file = 5 * 1024 * 1024  # 5MB
        max_total_size = 50 * 1024 * 1024  # 50MB
        
        # Calculate current total size
        for entry in metadata.values():
            if 'size' in entry:
                total_size += entry.get('size', 0)
        
        for file in files:
            if not file.filename:
                continue
            
            # Validate file type
            allowed_extensions = ['.png', '.jpg', '.jpeg', '.bmp', '.gif']
            file_ext = '.' + file.filename.lower().split('.')[-1]
            if file_ext not in allowed_extensions:
                return jsonify({
                    'status': 'error', 
                    'message': f'Invalid file type: {file_ext}. Allowed: {allowed_extensions}'
                }), 400
            
            # Read file to check size and validate
            file.seek(0, os.SEEK_END)
            file_size = file.tell()
            file.seek(0)
            
            if file_size > max_size_per_file:
                return jsonify({
                    'status': 'error',
                    'message': f'File {file.filename} exceeds 5MB limit'
                }), 400
            
            if total_size + file_size > max_total_size:
                return jsonify({
                    'status': 'error',
                    'message': f'Upload would exceed 50MB total storage limit'
                }), 400
            
            # Validate file is actually an image (check magic bytes)
            file_content = file.read(8)
            file.seek(0)
            is_valid_image = False
            if file_content.startswith(b'\x89PNG\r\n\x1a\n'):  # PNG
                is_valid_image = True
            elif file_content[:2] == b'\xff\xd8':  # JPEG
                is_valid_image = True
            elif file_content[:2] == b'BM':  # BMP
                is_valid_image = True
            elif file_content[:6] in [b'GIF87a', b'GIF89a']:  # GIF
                is_valid_image = True
            
            if not is_valid_image:
                return jsonify({
                    'status': 'error',
                    'message': f'File {file.filename} is not a valid image file'
                }), 400
            
            # Generate unique filename
            timestamp = int(time.time())
            file_hash = hashlib.md5(file_content + file.filename.encode()).hexdigest()[:8]
            safe_filename = f"image_{timestamp}_{file_hash}{file_ext}"
            file_path = assets_dir / safe_filename
            
            # Ensure filename is unique
            counter = 1
            while file_path.exists():
                safe_filename = f"image_{timestamp}_{file_hash}_{counter}{file_ext}"
                file_path = assets_dir / safe_filename
                counter += 1
            
            # Save file
            file.save(str(file_path))
            
            # Make file readable
            os.chmod(file_path, 0o644)
            
            # Generate unique ID
            image_id = str(uuid.uuid4())
            
            # Store metadata
            relative_path = f"assets/plugins/{plugin_id}/uploads/{safe_filename}"
            metadata[image_id] = {
                'id': image_id,
                'filename': safe_filename,
                'path': relative_path,
                'size': file_size,
                'uploaded_at': datetime.utcnow().isoformat() + 'Z',
                'original_filename': file.filename
            }
            
            uploaded_files.append({
                'id': image_id,
                'filename': safe_filename,
                'path': relative_path,
                'size': file_size,
                'uploaded_at': metadata[image_id]['uploaded_at']
            })
            
            total_size += file_size
        
        # Save metadata
        with open(metadata_file, 'w') as f:
            json.dump(metadata, f, indent=2)
        
        return jsonify({
            'status': 'success',
            'uploaded_files': uploaded_files,
            'total_files': len(metadata)
        })
        
    except Exception as e:
        import traceback
        return jsonify({'status': 'error', 'message': str(e), 'traceback': traceback.format_exc()}), 500

@api_v3.route('/plugins/calendar/upload-credentials', methods=['POST'])
def upload_calendar_credentials():
    """Upload credentials.json file for calendar plugin"""
    try:
        if 'file' not in request.files:
            return jsonify({'status': 'error', 'message': 'No file provided'}), 400
        
        file = request.files['file']
        if not file or not file.filename:
            return jsonify({'status': 'error', 'message': 'No file provided'}), 400
        
        # Validate file extension
        if not file.filename.lower().endswith('.json'):
            return jsonify({'status': 'error', 'message': 'File must be a JSON file (.json)'}), 400
        
        # Validate file size (max 1MB for credentials)
        file.seek(0, os.SEEK_END)
        file_size = file.tell()
        file.seek(0)
        
        if file_size > 1024 * 1024:  # 1MB
            return jsonify({'status': 'error', 'message': 'File exceeds 1MB limit'}), 400
        
        # Validate it's valid JSON
        try:
            file_content = file.read()
            file.seek(0)
            json.loads(file_content)
        except json.JSONDecodeError:
            return jsonify({'status': 'error', 'message': 'File is not valid JSON'}), 400
        
        # Validate it looks like Google OAuth credentials
        try:
            file.seek(0)
            creds_data = json.loads(file.read())
            file.seek(0)
            
            # Check for required Google OAuth fields
            if 'installed' not in creds_data and 'web' not in creds_data:
                return jsonify({
                    'status': 'error', 
                    'message': 'File does not appear to be a valid Google OAuth credentials file'
                }), 400
        except Exception:
            pass  # Continue even if validation fails
        
        # Get plugin directory
        plugin_id = 'calendar'
        if api_v3.plugin_manager:
            plugin_dir = api_v3.plugin_manager.get_plugin_directory(plugin_id)
        else:
            plugin_dir = PROJECT_ROOT / 'plugins' / plugin_id
        
        if not plugin_dir or not Path(plugin_dir).exists():
            return jsonify({'status': 'error', 'message': f'Plugin {plugin_id} not found'}), 404
        
        # Save file to plugin directory
        credentials_path = Path(plugin_dir) / 'credentials.json'
        
        # Backup existing file if it exists
        if credentials_path.exists():
            backup_path = Path(plugin_dir) / f'credentials.json.backup.{int(time.time())}'
            import shutil
            shutil.copy2(credentials_path, backup_path)
        
        # Save new file
        file.save(str(credentials_path))
        
        # Set proper permissions
        os.chmod(credentials_path, 0o600)  # Read/write for owner only
        
        return jsonify({
            'status': 'success',
            'message': 'Credentials file uploaded successfully',
            'path': str(credentials_path)
        })
        
    except Exception as e:
        import traceback
        error_details = traceback.format_exc()
        print(f"Error in upload_calendar_credentials: {str(e)}")
        print(error_details)
        return jsonify({'status': 'error', 'message': str(e)}), 500

@api_v3.route('/plugins/assets/delete', methods=['POST'])
def delete_plugin_asset():
    """Delete an asset file for a plugin"""
    try:
        data = request.get_json()
        plugin_id = data.get('plugin_id')
        image_id = data.get('image_id')
        
        if not plugin_id or not image_id:
            return jsonify({'status': 'error', 'message': 'plugin_id and image_id are required'}), 400
        
        # Get asset directory
        assets_dir = PROJECT_ROOT / 'assets' / 'plugins' / plugin_id / 'uploads'
        metadata_file = assets_dir / '.metadata.json'
        
        if not metadata_file.exists():
            return jsonify({'status': 'error', 'message': 'Metadata file not found'}), 404
        
        # Load metadata
        with open(metadata_file, 'r') as f:
            metadata = json.load(f)
        
        if image_id not in metadata:
            return jsonify({'status': 'error', 'message': 'Image not found'}), 404
        
        # Delete file
        file_path = PROJECT_ROOT / metadata[image_id]['path']
        if file_path.exists():
            file_path.unlink()
        
        # Remove from metadata
        del metadata[image_id]
        
        # Save metadata
        with open(metadata_file, 'w') as f:
            json.dump(metadata, f, indent=2)
        
        return jsonify({'status': 'success', 'message': 'Image deleted successfully'})
        
    except Exception as e:
        import traceback
        return jsonify({'status': 'error', 'message': str(e), 'traceback': traceback.format_exc()}), 500

@api_v3.route('/plugins/assets/list', methods=['GET'])
def list_plugin_assets():
    """List asset files for a plugin"""
    try:
        plugin_id = request.args.get('plugin_id')
        if not plugin_id:
            return jsonify({'status': 'error', 'message': 'plugin_id is required'}), 400
        
        # Get asset directory
        assets_dir = PROJECT_ROOT / 'assets' / 'plugins' / plugin_id / 'uploads'
        metadata_file = assets_dir / '.metadata.json'
        
        if not metadata_file.exists():
            return jsonify({'status': 'success', 'data': {'assets': []}})
        
        # Load metadata
        with open(metadata_file, 'r') as f:
            metadata = json.load(f)
        
        # Convert to list
        assets = list(metadata.values())
        
        return jsonify({'status': 'success', 'data': {'assets': assets}})
        
    except Exception as e:
        import traceback
        return jsonify({'status': 'error', 'message': str(e), 'traceback': traceback.format_exc()}), 500

@api_v3.route('/fonts/delete/<font_family>', methods=['DELETE'])
def delete_font(font_family):
    """Delete font"""
    try:
        # This would integrate with the actual font system
        return jsonify({'status': 'success', 'message': f'Font {font_family} deleted'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@api_v3.route('/logs', methods=['GET'])
def get_logs():
    """Get system logs from journalctl"""
    try:
        # Get recent logs from journalctl
        result = subprocess.run(
            ['sudo', 'journalctl', '-u', 'ledmatrix.service', '-n', '100', '--no-pager'],
            capture_output=True,
            text=True,
            timeout=5
        )
        
        if result.returncode == 0:
            logs_text = result.stdout.strip()
            return jsonify({
                'status': 'success',
                'data': {
                    'logs': logs_text if logs_text else 'No logs available from ledmatrix service'
                }
            })
        else:
            return jsonify({
                'status': 'error',
                'message': f'Failed to get logs: {result.stderr}'
            }), 500
            
    except subprocess.TimeoutExpired:
        return jsonify({
            'status': 'error',
            'message': 'Timeout while fetching logs'
        }), 500
    except Exception as e:
        return jsonify({
            'status': 'error',
            'message': f'Error fetching logs: {str(e)}'
        }), 500

# WiFi Management Endpoints
@api_v3.route('/wifi/status', methods=['GET'])
def get_wifi_status():
    """Get current WiFi connection status"""
    try:
        from src.wifi_manager import WiFiManager
        
        wifi_manager = WiFiManager()
        status = wifi_manager.get_wifi_status()
        
        # Get auto-enable setting from config
        auto_enable_ap = wifi_manager.config.get("auto_enable_ap_mode", True)  # Default: True
        
        return jsonify({
            'status': 'success',
            'data': {
                'connected': status.connected,
                'ssid': status.ssid,
                'ip_address': status.ip_address,
                'signal': status.signal,
                'ap_mode_active': status.ap_mode_active,
                'auto_enable_ap_mode': auto_enable_ap
            }
        })
    except Exception as e:
        return jsonify({
            'status': 'error',
            'message': f'Error getting WiFi status: {str(e)}'
        }), 500

@api_v3.route('/wifi/scan', methods=['GET'])
def scan_wifi_networks():
    """Scan for available WiFi networks
    
    If AP mode is active, it will be temporarily disabled during scanning
    and automatically re-enabled afterward. Users connected to the AP will
    be briefly disconnected during this process.
    """
    try:
        from src.wifi_manager import WiFiManager
        
        wifi_manager = WiFiManager()
        
        # Check if AP mode is active before scanning (for user notification)
        ap_was_active = wifi_manager._is_ap_mode_active()
        
        # Perform the scan (this will handle AP mode disabling/enabling internally)
        networks = wifi_manager.scan_networks()
        
        # Convert to dict format
        networks_data = [
            {
                'ssid': net.ssid,
                'signal': net.signal,
                'security': net.security,
                'frequency': net.frequency
            }
            for net in networks
        ]
        
        response_data = {
            'status': 'success',
            'data': networks_data
        }
        
        # Inform user if AP mode was temporarily disabled
        if ap_was_active:
            response_data['message'] = (
                f'Found {len(networks_data)} networks. '
                'Note: AP mode was temporarily disabled during scanning and has been re-enabled. '
                'If you were connected to the setup network, you may need to reconnect.'
            )
        
        return jsonify(response_data)
    except Exception as e:
        error_message = f'Error scanning WiFi networks: {str(e)}'
        
        # Provide more specific error messages for common issues
        error_str = str(e).lower()
        if 'permission' in error_str or 'sudo' in error_str:
            error_message = (
                'Permission error while scanning. '
                'The WiFi scan requires appropriate permissions. '
                'Please ensure the application has necessary privileges.'
            )
        elif 'timeout' in error_str:
            error_message = (
                'WiFi scan timed out. '
                'The scan took too long to complete. '
                'This may happen if the WiFi interface is busy or in use.'
            )
        elif 'no wifi' in error_str or 'not available' in error_str:
            error_message = (
                'WiFi scanning tools are not available. '
                'Please ensure NetworkManager (nmcli) or iwlist is installed.'
            )
        
        return jsonify({
            'status': 'error',
            'message': error_message
        }), 500

@api_v3.route('/wifi/connect', methods=['POST'])
def connect_wifi():
    """Connect to a WiFi network"""
    try:
        from src.wifi_manager import WiFiManager
        
        data = request.get_json()
        if not data:
            return jsonify({
                'status': 'error',
                'message': 'Request body is required'
            }), 400
        
        if 'ssid' not in data:
            return jsonify({
                'status': 'error',
                'message': 'SSID is required'
            }), 400
        
        ssid = data['ssid']
        if not ssid or not ssid.strip():
            return jsonify({
                'status': 'error',
                'message': 'SSID cannot be empty'
            }), 400
        
        ssid = ssid.strip()
        password = data.get('password', '') or ''
        
        wifi_manager = WiFiManager()
        success, message = wifi_manager.connect_to_network(ssid, password)
        
        if success:
            return jsonify({
                'status': 'success',
                'message': message
            })
        else:
            return jsonify({
                'status': 'error',
                'message': message or 'Failed to connect to network'
            }), 400
    except Exception as e:
        import logging
        import traceback
        logger = logging.getLogger(__name__)
        logger.error(f"Error connecting to WiFi: {e}\n{traceback.format_exc()}")
        return jsonify({
            'status': 'error',
            'message': f'Error connecting to WiFi: {str(e)}'
        }), 500

@api_v3.route('/wifi/ap/enable', methods=['POST'])
def enable_ap_mode():
    """Enable access point mode"""
    try:
        from src.wifi_manager import WiFiManager
        
        wifi_manager = WiFiManager()
        success, message = wifi_manager.enable_ap_mode()
        
        if success:
            return jsonify({
                'status': 'success',
                'message': message
            })
        else:
            return jsonify({
                'status': 'error',
                'message': message
            }), 400
    except Exception as e:
        return jsonify({
            'status': 'error',
            'message': f'Error enabling AP mode: {str(e)}'
        }), 500

@api_v3.route('/wifi/ap/disable', methods=['POST'])
def disable_ap_mode():
    """Disable access point mode"""
    try:
        from src.wifi_manager import WiFiManager
        
        wifi_manager = WiFiManager()
        success, message = wifi_manager.disable_ap_mode()
        
        if success:
            return jsonify({
                'status': 'success',
                'message': message
            })
        else:
            return jsonify({
                'status': 'error',
                'message': message
            }), 400
    except Exception as e:
        return jsonify({
            'status': 'error',
            'message': f'Error disabling AP mode: {str(e)}'
        }), 500

@api_v3.route('/wifi/ap/auto-enable', methods=['GET'])
def get_auto_enable_ap_mode():
    """Get auto-enable AP mode setting"""
    try:
        from src.wifi_manager import WiFiManager
        
        wifi_manager = WiFiManager()
        auto_enable = wifi_manager.config.get("auto_enable_ap_mode", True)  # Default: True
        
        return jsonify({
            'status': 'success',
            'data': {
                'auto_enable_ap_mode': auto_enable
            }
        })
    except Exception as e:
        return jsonify({
            'status': 'error',
            'message': f'Error getting auto-enable setting: {str(e)}'
        }), 500

@api_v3.route('/wifi/ap/auto-enable', methods=['POST'])
def set_auto_enable_ap_mode():
    """Set auto-enable AP mode setting"""
    try:
        from src.wifi_manager import WiFiManager
        
        data = request.get_json()
        if data is None or 'auto_enable_ap_mode' not in data:
            return jsonify({
                'status': 'error',
                'message': 'auto_enable_ap_mode is required'
            }), 400
        
        auto_enable = bool(data['auto_enable_ap_mode'])
        
        wifi_manager = WiFiManager()
        wifi_manager.config["auto_enable_ap_mode"] = auto_enable
        wifi_manager._save_config()
        
        return jsonify({
            'status': 'success',
            'message': f'Auto-enable AP mode set to {auto_enable}',
            'data': {
                'auto_enable_ap_mode': auto_enable
            }
        })
    except Exception as e:
        return jsonify({
            'status': 'error',
            'message': f'Error setting auto-enable: {str(e)}'
        }), 500

@api_v3.route('/cache/list', methods=['GET'])
def list_cache_files():
    """List all cache files with metadata"""
    try:
        if not api_v3.cache_manager:
            # Initialize cache manager if not already initialized
            from src.cache_manager import CacheManager
            api_v3.cache_manager = CacheManager()
        
        cache_files = api_v3.cache_manager.list_cache_files()
        cache_dir = api_v3.cache_manager.get_cache_dir()
        
        return jsonify({
            'status': 'success',
            'data': {
                'cache_files': cache_files,
                'cache_dir': cache_dir,
                'total_files': len(cache_files)
            }
        })
    except Exception as e:
        import traceback
        error_details = traceback.format_exc()
        print(f"Error in list_cache_files: {str(e)}")
        print(error_details)
        return jsonify({'status': 'error', 'message': str(e)}), 500

@api_v3.route('/cache/delete', methods=['POST'])
def delete_cache_file():
    """Delete a specific cache file by key"""
    try:
        if not api_v3.cache_manager:
            # Initialize cache manager if not already initialized
            from src.cache_manager import CacheManager
            api_v3.cache_manager = CacheManager()
        
        data = request.get_json()
        if not data or 'key' not in data:
            return jsonify({'status': 'error', 'message': 'cache key is required'}), 400
        
        cache_key = data['key']
        
        # Delete the cache file
        api_v3.cache_manager.clear_cache(cache_key)
        
        return jsonify({
            'status': 'success',
            'message': f'Cache file for key "{cache_key}" deleted successfully'
        })
    except Exception as e:
        import traceback
        error_details = traceback.format_exc()
        print(f"Error in delete_cache_file: {str(e)}")
        print(error_details)
        return jsonify({'status': 'error', 'message': str(e)}), 500