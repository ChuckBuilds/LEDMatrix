from flask import Blueprint, request, jsonify, Response
import json
import os
import subprocess
import time
from pathlib import Path

# Will be initialized when blueprint is registered
config_manager = None
plugin_manager = None
plugin_store_manager = None

# Get project root directory (web_interface/../..)
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

api_v3 = Blueprint('api_v3', __name__)

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
            schedule_config['start_time'] = data.get('start_time', '07:00')
            schedule_config['end_time'] = data.get('end_time', '23:00')
        else:
            # Per-day schedule
            schedule_config['days'] = {}
            days = ['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday']
            
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
                    if start_key in data and data[start_key]:
                        day_config['start_time'] = data[start_key]
                    else:
                        day_config['start_time'] = '07:00'
                    
                    if end_key in data and data[end_key]:
                        day_config['end_time'] = data[end_key]
                    else:
                        day_config['end_time'] = '23:00'
                
                schedule_config['days'][day] = day_config
        
        # Update and save config
        current_config['schedule'] = schedule_config
        api_v3.config_manager.save_config(current_config)
        
        return jsonify({'status': 'success', 'message': 'Schedule configuration saved successfully'})
    except Exception as e:
        import logging
        import traceback
        error_msg = f"Error saving schedule config: {str(e)}\n{traceback.format_exc()}"
        logging.error(error_msg)
        return jsonify({'status': 'error', 'message': str(e)}), 500

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
        is_general_update = any(k in data for k in ['timezone', 'city', 'state', 'country', 'web_display_autostart'])
        
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

        # Handle display settings
        display_fields = ['rows', 'cols', 'chain_length', 'parallel', 'brightness', 'hardware_mapping', 
                         'gpio_slowdown', 'scan_mode', 'disable_hardware_pulsing', 'inverse_colors', 'show_refresh_rate',
                         'pwm_bits', 'pwm_dither_bits', 'pwm_lsb_nanoseconds', 'limit_refresh_rate_hz', 'use_short_date_format']
        
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

        # Merge sports configurations
        if 'nfl_scoreboard' in data:
            current_config['nfl_scoreboard'] = data['nfl_scoreboard']
        if 'mlb_scoreboard' in data:
            current_config['mlb_scoreboard'] = data['mlb_scoreboard']
        if 'nhl_scoreboard' in data:
            current_config['nhl_scoreboard'] = data['nhl_scoreboard']
        if 'nba_scoreboard' in data:
            current_config['nba_scoreboard'] = data['nba_scoreboard']
        if 'ncaa_fb_scoreboard' in data:
            current_config['ncaa_fb_scoreboard'] = data['ncaa_fb_scoreboard']
        if 'soccer_scoreboard' in data:
            current_config['soccer_scoreboard'] = data['soccer_scoreboard']

        # Save the merged config
        api_v3.config_manager.save_config(current_config)

        return jsonify({'status': 'success', 'message': 'Configuration saved successfully'})
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
            status_result = subprocess.run(
                ['git', 'status', '--porcelain'],
                capture_output=True,
                text=True,
                timeout=5,
                cwd=project_dir
            )
            
            has_changes = bool(status_result.stdout.strip())
            stash_info = ""
            
            # Stash local changes if they exist
            if has_changes:
                stash_result = subprocess.run(
                    ['git', 'stash', 'push', '-m', 'LEDMatrix auto-stash before update'],
                    capture_output=True,
                    text=True,
                    timeout=10,
                    cwd=project_dir
                )
                print(f"Stashed local changes: {stash_result.stdout}")
                stash_info = " Local changes were stashed."
            
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

@api_v3.route('/plugins/installed', methods=['GET'])
def get_installed_plugins():
    """Get installed plugins"""
    try:
        if not api_v3.plugin_manager or not api_v3.plugin_store_manager:
            return jsonify({'status': 'error', 'message': 'Plugin managers not initialized'}), 500
        
        # Get all installed plugin info from the plugin manager
        all_plugin_info = api_v3.plugin_manager.get_all_plugin_info()
        
        # Format for the web interface
        plugins = []
        for plugin_info in all_plugin_info:
            plugin_id = plugin_info.get('id')
            
            # Get enabled status from loaded plugin or config
            plugin_instance = api_v3.plugin_manager.get_plugin(plugin_id)
            enabled = plugin_instance.enabled if plugin_instance else False
            
            # Get verified status from store registry (if available)
            store_info = api_v3.plugin_store_manager.get_plugin_info(plugin_id)
            verified = store_info.get('verified', False) if store_info else False
            
            plugins.append({
                'id': plugin_id,
                'name': plugin_info.get('name', plugin_id),
                'author': plugin_info.get('author', 'Unknown'),
                'version': plugin_info.get('version', '1.0.0'),
                'category': plugin_info.get('category', 'General'),
                'description': plugin_info.get('description', 'No description available'),
                'tags': plugin_info.get('tags', []),
                'enabled': enabled,
                'verified': verified,
                'loaded': plugin_info.get('loaded', False)
            })
        
        return jsonify({'status': 'success', 'data': {'plugins': plugins}})
    except Exception as e:
        import traceback
        error_details = traceback.format_exc()
        print(f"Error in get_installed_plugins: {str(e)}")
        print(error_details)
        return jsonify({'status': 'error', 'message': str(e), 'details': error_details}), 500

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
        api_v3.config_manager.save_config(config)
        
        # If plugin is loaded, also call its lifecycle methods
        plugin = api_v3.plugin_manager.get_plugin(plugin_id)
        if plugin:
            if enabled:
                plugin.on_enable()
            else:
                plugin.on_disable()
        
        return jsonify({'status': 'success', 'message': f'Plugin {plugin_id} {"enabled" if enabled else "disabled"}'})
    except Exception as e:
        import traceback
        error_details = traceback.format_exc()
        print(f"Error in toggle_plugin: {str(e)}")
        print(error_details)
        return jsonify({'status': 'error', 'message': str(e)}), 500

@api_v3.route('/plugins/config', methods=['GET'])
def get_plugin_config():
    """Get plugin configuration"""
    try:
        if not api_v3.config_manager:
            return jsonify({'status': 'error', 'message': 'Config manager not initialized'}), 500
        
        plugin_id = request.args.get('plugin_id')
        if not plugin_id:
            return jsonify({'status': 'error', 'message': 'plugin_id required'}), 400

        # Get plugin configuration from config manager
        main_config = api_v3.config_manager.load_config()
        plugin_config = main_config.get(plugin_id, {})
        
        # If no config exists, return defaults
        if not plugin_config:
            plugin_config = {
                'enabled': True,
                'display_duration': 30
            }

        return jsonify({'status': 'success', 'data': plugin_config})
    except Exception as e:
        import traceback
        error_details = traceback.format_exc()
        print(f"Error in get_plugin_config: {str(e)}")
        print(error_details)
        return jsonify({'status': 'error', 'message': str(e)}), 500

@api_v3.route('/plugins/update', methods=['POST'])
def update_plugin():
    """Update plugin"""
    try:
        if not api_v3.plugin_store_manager:
            return jsonify({'status': 'error', 'message': 'Plugin store manager not initialized'}), 500
        
        data = request.get_json()
        if not data or 'plugin_id' not in data:
            return jsonify({'status': 'error', 'message': 'plugin_id required'}), 400

        plugin_id = data['plugin_id']
        
        # Update the plugin
        success = api_v3.plugin_store_manager.update_plugin(plugin_id)
        
        if success:
            # Rediscover plugins to refresh manifest info (including new version number)
            if api_v3.plugin_manager:
                api_v3.plugin_manager.discover_plugins()
                
                # Reload the plugin if it was loaded
                if plugin_id in api_v3.plugin_manager.plugins:
                    api_v3.plugin_manager.reload_plugin(plugin_id)
            
            return jsonify({'status': 'success', 'message': f'Plugin {plugin_id} updated successfully'})
        else:
            return jsonify({'status': 'error', 'message': f'Failed to update plugin {plugin_id}'}), 500
            
    except Exception as e:
        import traceback
        error_details = traceback.format_exc()
        print(f"Error in update_plugin: {str(e)}")
        print(error_details)
        return jsonify({'status': 'error', 'message': str(e)}), 500

@api_v3.route('/plugins/uninstall', methods=['POST'])
def uninstall_plugin():
    """Uninstall plugin"""
    try:
        if not api_v3.plugin_store_manager:
            return jsonify({'status': 'error', 'message': 'Plugin store manager not initialized'}), 500
        
        data = request.get_json()
        if not data or 'plugin_id' not in data:
            return jsonify({'status': 'error', 'message': 'plugin_id required'}), 400

        plugin_id = data['plugin_id']
        
        # Unload the plugin first if it's loaded
        if api_v3.plugin_manager and plugin_id in api_v3.plugin_manager.plugins:
            api_v3.plugin_manager.unload_plugin(plugin_id)
        
        # Uninstall the plugin
        success = api_v3.plugin_store_manager.uninstall_plugin(plugin_id)
        
        if success:
            return jsonify({'status': 'success', 'message': f'Plugin {plugin_id} uninstalled successfully'})
        else:
            return jsonify({'status': 'error', 'message': f'Failed to uninstall plugin {plugin_id}'}), 500
            
    except Exception as e:
        import traceback
        error_details = traceback.format_exc()
        print(f"Error in uninstall_plugin: {str(e)}")
        print(error_details)
        return jsonify({'status': 'error', 'message': str(e)}), 500

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
        
        # Install the plugin
        success = api_v3.plugin_store_manager.install_plugin(plugin_id)
        
        if success:
            # Discover and load the new plugin
            if api_v3.plugin_manager:
                api_v3.plugin_manager.discover_plugins()
                api_v3.plugin_manager.load_plugin(plugin_id)
            
            return jsonify({'status': 'success', 'message': f'Plugin {plugin_id} installed successfully'})
        else:
            return jsonify({'status': 'error', 'message': f'Failed to install plugin {plugin_id}'}), 500
            
    except Exception as e:
        import traceback
        error_details = traceback.format_exc()
        print(f"Error in install_plugin: {str(e)}")
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

        # Search plugins from the registry
        plugins = api_v3.plugin_store_manager.search_plugins(query=query, category=category, tags=tags)
        
        # Format plugins for the web interface
        formatted_plugins = []
        for plugin in plugins:
            # Get the latest version - check multiple sources
            version_str = None
            
            # First, check for latest_version field
            if 'latest_version' in plugin:
                version_str = plugin.get('latest_version')
            
            # If not found, extract from versions array
            if not version_str:
                versions = plugin.get('versions', [])
                if versions and isinstance(versions, list) and len(versions) > 0:
                    latest_version = versions[0]
                    if isinstance(latest_version, dict):
                        version_str = latest_version.get('version')
            
            # Fallback to top-level version field
            if not version_str:
                version_str = plugin.get('version', '1.0.0')
            
            formatted_plugins.append({
                'id': plugin.get('id'),
                'name': plugin.get('name'),
                'author': plugin.get('author'),
                'version': version_str,
                'category': plugin.get('category'),
                'description': plugin.get('description'),
                'tags': plugin.get('tags', []),
                'stars': plugin.get('stars', 0),
                'verified': plugin.get('verified', False),
                'repo': plugin.get('repo', ''),
                'last_updated': plugin.get('last_updated', '')
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
        
        # Force refresh the registry
        registry = api_v3.plugin_store_manager.fetch_registry(force_refresh=True)
        plugin_count = len(registry.get('plugins', []))
        
        return jsonify({
            'status': 'success', 
            'message': 'Plugin store refreshed', 
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
            return jsonify({'status': 'error', 'message': 'Config manager not initialized'}), 500

        data = request.get_json()
        if not data or 'plugin_id' not in data:
            return jsonify({'status': 'error', 'message': 'plugin_id required'}), 400

        plugin_id = data['plugin_id']
        plugin_config = data.get('config', {})

        # Load plugin schema to identify secret fields (supports nested schemas)
        plugins_dir = Path('plugins')
        schema_path = plugins_dir / plugin_id / 'config_schema.json'
        secret_fields = set()
        
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
                    # Find fields marked with x-secret: true (supports nested)
                    if 'properties' in schema:
                        secret_fields = find_secret_fields(schema['properties'])
            except Exception as e:
                print(f"Error reading schema for secret detection: {e}")

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
        current_config[plugin_id] = deep_merge(current_config[plugin_id], regular_config)

        # Deep merge plugin secrets in secrets config
        if secrets_config:
            if plugin_id not in current_secrets:
                current_secrets[plugin_id] = {}
            current_secrets[plugin_id] = deep_merge(current_secrets[plugin_id], secrets_config)
            # Save secrets file
            api_v3.config_manager.save_raw_file_content('secrets', current_secrets)

        # Save the updated main config
        api_v3.config_manager.save_config(current_config)

        secret_count = len(secrets_config)
        message = f'Plugin {plugin_id} configuration saved successfully'
        if secret_count > 0:
            message += f' ({secret_count} secret field(s) saved to config_secrets.json)'

        return jsonify({'status': 'success', 'message': message})
    except Exception as e:
        import traceback
        error_details = traceback.format_exc()
        print(f"Error in save_plugin_config: {str(e)}")
        print(error_details)
        return jsonify({'status': 'error', 'message': str(e)}), 500

@api_v3.route('/plugins/schema', methods=['GET'])
def get_plugin_schema():
    """Get plugin configuration schema"""
    try:
        plugin_id = request.args.get('plugin_id')
        if not plugin_id:
            return jsonify({'status': 'error', 'message': 'plugin_id required'}), 400

        # Try to get the plugin directory using plugin manager if available
        schema_path = None
        if api_v3.plugin_manager:
            plugin_dir = api_v3.plugin_manager.get_plugin_directory(plugin_id)
            if plugin_dir:
                schema_path = Path(plugin_dir) / 'config_schema.json'
        
        # Fallback to direct path if plugin manager not available
        if not schema_path:
            plugins_dir = Path('plugins')
            schema_path = plugins_dir / plugin_id / 'config_schema.json'

        if schema_path.exists():
            try:
                with open(schema_path, 'r', encoding='utf-8') as f:
                    schema = json.load(f)
                return jsonify({'status': 'success', 'data': {'schema': schema}})
            except Exception as e:
                print(f"Error reading schema file for {plugin_id}: {e}")

        # Return a simple default schema if file not found
        schema = {
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

        return jsonify({'status': 'success', 'data': {'schema': schema}})
    except Exception as e:
        import traceback
        error_details = traceback.format_exc()
        print(f"Error in get_plugin_schema: {str(e)}")
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