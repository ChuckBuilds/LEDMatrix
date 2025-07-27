from flask import Flask, render_template, request, redirect, url_for, flash, jsonify
import json
import os
import subprocess
from pathlib import Path
from src.config_manager import ConfigManager

app = Flask(__name__)
app.secret_key = os.urandom(24)
config_manager = ConfigManager()

@app.route('/')
def index():
    try:
        main_config = config_manager.load_config()
        schedule_config = main_config.get('schedule', {})
        
        main_config_data = config_manager.get_raw_file_content('main')
        secrets_config_data = config_manager.get_raw_file_content('secrets')
        main_config_json = json.dumps(main_config_data, indent=4)
        secrets_config_json = json.dumps(secrets_config_data, indent=4)
        
    except Exception as e:
        flash(f"Error loading configuration: {e}", "error")
        schedule_config = {}
        main_config_json = "{}"
        secrets_config_json = "{}"
        main_config_data = {}
        secrets_config_data = {}

    return render_template('index.html', 
                           schedule_config=schedule_config,
                           main_config_json=main_config_json,
                           secrets_config_json=secrets_config_json,
                           main_config_path=config_manager.get_config_path(),
                           secrets_config_path=config_manager.get_secrets_path(),
                           main_config=main_config_data,
                           secrets_config=secrets_config_data)

@app.route('/save_schedule', methods=['POST'])
def save_schedule_route():
    try:
        main_config = config_manager.load_config()
        
        schedule_data = {
            'enabled': 'schedule_enabled' in request.form,
            'start_time': request.form.get('start_time', '07:00'),
            'end_time': request.form.get('end_time', '22:00')
        }
        
        main_config['schedule'] = schedule_data
        config_manager.save_config(main_config)
        
        return jsonify({
            'status': 'success',
            'message': 'Schedule updated successfully! Restart the display for changes to take effect.'
        })

    except Exception as e:
        return jsonify({
            'status': 'error',
            'message': f'Error saving schedule: {e}'
        }), 400

@app.route('/save_config', methods=['POST'])
def save_config_route():
    config_type = request.form.get('config_type')
    config_data_str = request.form.get('config_data')
    
    try:
        if config_type == 'main':
            # Handle form-based configuration updates
            main_config = config_manager.load_config()
            
            # Update display settings
            if 'rows' in request.form:
                main_config['display']['hardware']['rows'] = int(request.form.get('rows', 32))
                main_config['display']['hardware']['cols'] = int(request.form.get('cols', 64))
                main_config['display']['hardware']['chain_length'] = int(request.form.get('chain_length', 2))
                main_config['display']['hardware']['parallel'] = int(request.form.get('parallel', 1))
                main_config['display']['hardware']['brightness'] = int(request.form.get('brightness', 95))
                main_config['display']['hardware']['hardware_mapping'] = request.form.get('hardware_mapping', 'adafruit-hat-pwm')
                main_config['display']['runtime']['gpio_slowdown'] = int(request.form.get('gpio_slowdown', 3))
            
            # Update weather settings
            if 'weather_enabled' in request.form:
                main_config['weather']['enabled'] = 'weather_enabled' in request.form
                main_config['location']['city'] = request.form.get('weather_city', 'Dallas')
                main_config['location']['state'] = request.form.get('weather_state', 'Texas')
                main_config['weather']['units'] = request.form.get('weather_units', 'imperial')
                main_config['weather']['update_interval'] = int(request.form.get('weather_update_interval', 1800))
            
            # Update stocks settings
            if 'stocks_enabled' in request.form:
                main_config['stocks']['enabled'] = 'stocks_enabled' in request.form
                symbols = request.form.get('stocks_symbols', '').split(',')
                main_config['stocks']['symbols'] = [s.strip() for s in symbols if s.strip()]
                main_config['stocks']['update_interval'] = int(request.form.get('stocks_update_interval', 600))
                main_config['stocks']['toggle_chart'] = 'stocks_toggle_chart' in request.form
            
            # Update crypto settings
            if 'crypto_enabled' in request.form:
                main_config['crypto']['enabled'] = 'crypto_enabled' in request.form
                symbols = request.form.get('crypto_symbols', '').split(',')
                main_config['crypto']['symbols'] = [s.strip() for s in symbols if s.strip()]
                main_config['crypto']['update_interval'] = int(request.form.get('crypto_update_interval', 600))
                main_config['crypto']['toggle_chart'] = 'crypto_toggle_chart' in request.form
            
            # Update music settings
            if 'music_enabled' in request.form:
                main_config['music']['enabled'] = 'music_enabled' in request.form
                main_config['music']['preferred_source'] = request.form.get('music_preferred_source', 'ytm')
                main_config['music']['YTM_COMPANION_URL'] = request.form.get('ytm_companion_url', 'http://192.168.86.12:9863')
                main_config['music']['POLLING_INTERVAL_SECONDS'] = int(request.form.get('music_polling_interval', 1))
            
            # Update calendar settings
            if 'calendar_enabled' in request.form:
                main_config['calendar']['enabled'] = 'calendar_enabled' in request.form
                main_config['calendar']['max_events'] = int(request.form.get('calendar_max_events', 3))
                main_config['calendar']['update_interval'] = int(request.form.get('calendar_update_interval', 3600))
                calendars = request.form.get('calendar_calendars', '').split(',')
                main_config['calendar']['calendars'] = [c.strip() for c in calendars if c.strip()]
            
            # Update display durations
            if 'clock_duration' in request.form:
                main_config['display']['display_durations']['clock'] = int(request.form.get('clock_duration', 15))
                main_config['display']['display_durations']['weather'] = int(request.form.get('weather_duration', 30))
                main_config['display']['display_durations']['stocks'] = int(request.form.get('stocks_duration', 30))
                main_config['display']['display_durations']['music'] = int(request.form.get('music_duration', 30))
                main_config['display']['display_durations']['calendar'] = int(request.form.get('calendar_duration', 30))
                main_config['display']['display_durations']['youtube'] = int(request.form.get('youtube_duration', 30))
                main_config['display']['display_durations']['text_display'] = int(request.form.get('text_display_duration', 10))
                main_config['display']['display_durations']['of_the_day'] = int(request.form.get('of_the_day_duration', 40))
            
            # Update general settings
            if 'web_display_autostart' in request.form:
                main_config['web_display_autostart'] = 'web_display_autostart' in request.form
                main_config['timezone'] = request.form.get('timezone', 'America/Chicago')
                main_config['location']['country'] = request.form.get('location_country', 'US')
            
            # Update clock settings
            if 'clock_enabled' in request.form:
                main_config['clock']['enabled'] = 'clock_enabled' in request.form
                main_config['clock']['format'] = request.form.get('clock_format', '%I:%M %p')
                main_config['clock']['update_interval'] = int(request.form.get('clock_update_interval', 1))
                main_config['clock']['date_format'] = request.form.get('clock_date_format', 'MM/DD/YYYY')
            
            # Update stock news settings
            if 'stock_news_enabled' in request.form:
                main_config['stock_news']['enabled'] = 'stock_news_enabled' in request.form
                main_config['stock_news']['update_interval'] = int(request.form.get('stock_news_update_interval', 3600))
            
            # Update odds ticker settings
            if 'odds_ticker_enabled' in request.form:
                main_config['odds_ticker']['enabled'] = 'odds_ticker_enabled' in request.form
                main_config['odds_ticker']['update_interval'] = int(request.form.get('odds_ticker_update_interval', 3600))
            
            # Update YouTube settings
            if 'youtube_enabled' in request.form:
                main_config['youtube']['enabled'] = 'youtube_enabled' in request.form
                main_config['youtube']['channel_id'] = request.form.get('youtube_channel_id', '')
                main_config['youtube']['update_interval'] = int(request.form.get('youtube_update_interval', 3600))
            
            # Update text display settings
            if 'text_display_enabled' in request.form:
                main_config['text_display']['enabled'] = 'text_display_enabled' in request.form
                main_config['text_display']['text'] = request.form.get('text_display_text', '')
                if 'text_display_duration' in request.form:
                    main_config['display']['display_durations']['text_display'] = int(request.form.get('text_display_duration', 10))
            
            # Update of the day settings
            if 'of_the_day_enabled' in request.form:
                main_config['of_the_day']['enabled'] = 'of_the_day_enabled' in request.form
                main_config['of_the_day']['update_interval'] = int(request.form.get('of_the_day_update_interval', 3600))
            
            # If config_data is provided as JSON, merge it
            if config_data_str:
                try:
                    new_data = json.loads(config_data_str)
                    # Merge the new data with existing config
                    for key, value in new_data.items():
                        if key in main_config:
                            if isinstance(value, dict) and isinstance(main_config[key], dict):
                                main_config[key].update(value)
                            else:
                                main_config[key] = value
                        else:
                            main_config[key] = value
                except json.JSONDecodeError:
                    return jsonify({
                        'status': 'error',
                        'message': 'Error: Invalid JSON format in config data.'
                    }), 400
            
            config_manager.save_config(main_config)
            return jsonify({
                'status': 'success',
                'message': 'Main configuration saved successfully!'
            })
            
        elif config_type == 'secrets':
            # Handle secrets configuration
            secrets_config = config_manager.get_raw_file_content('secrets')
            
            # Update weather API key
            if 'weather_api_key' in request.form:
                secrets_config['weather']['api_key'] = request.form.get('weather_api_key', '')
            
            # Update YouTube API settings
            if 'youtube_api_key' in request.form:
                secrets_config['youtube']['api_key'] = request.form.get('youtube_api_key', '')
                secrets_config['youtube']['channel_id'] = request.form.get('youtube_channel_id', '')
            
            # Update Spotify API settings
            if 'spotify_client_id' in request.form:
                secrets_config['music']['SPOTIFY_CLIENT_ID'] = request.form.get('spotify_client_id', '')
                secrets_config['music']['SPOTIFY_CLIENT_SECRET'] = request.form.get('spotify_client_secret', '')
                secrets_config['music']['SPOTIFY_REDIRECT_URI'] = request.form.get('spotify_redirect_uri', 'http://127.0.0.1:8888/callback')
            
            # If config_data is provided as JSON, use it
            if config_data_str:
                try:
                    new_data = json.loads(config_data_str)
                    config_manager.save_raw_file_content('secrets', new_data)
                except json.JSONDecodeError:
                    return jsonify({
                        'status': 'error',
                        'message': 'Error: Invalid JSON format for secrets config.'
                    }), 400
            else:
                config_manager.save_raw_file_content('secrets', secrets_config)
            
            return jsonify({
                'status': 'success',
                'message': 'Secrets configuration saved successfully!'
            })
        
    except json.JSONDecodeError:
        return jsonify({
            'status': 'error',
            'message': f'Error: Invalid JSON format for {config_type} config.'
        }), 400
    except Exception as e:
        return jsonify({
            'status': 'error',
            'message': f'Error saving {config_type} configuration: {e}'
        }), 400

@app.route('/run_action', methods=['POST'])
def run_action_route():
    try:
        data = request.get_json()
        action = data.get('action')
        
        if action == 'start_display':
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
            home_dir = str(Path.home())
            project_dir = os.path.join(home_dir, 'LEDMatrix')
            result = subprocess.run(['git', 'pull'], 
                                 capture_output=True, text=True, cwd=project_dir, check=True)
        else:
            return jsonify({
                'status': 'error',
                'message': f'Unknown action: {action}'
            }), 400
        
        return jsonify({
            'status': 'success' if result.returncode == 0 else 'error',
            'message': f'Action {action} completed with return code {result.returncode}',
            'stdout': result.stdout,
            'stderr': result.stderr
        })
        
    except Exception as e:
        return jsonify({
            'status': 'error',
            'message': f'Error running action: {e}'
        }), 400

@app.route('/get_logs', methods=['GET'])
def get_logs():
    try:
        # Get logs from journalctl for the ledmatrix service
        result = subprocess.run(
            ['sudo', 'journalctl', '-u', 'ledmatrix.service', '-n', '500', '--no-pager'],
            capture_output=True, text=True, check=True
        )
        logs = result.stdout
        return jsonify({'status': 'success', 'logs': logs})
    except subprocess.CalledProcessError as e:
        # If the command fails, return the error
        error_message = f"Error fetching logs: {e.stderr}"
        return jsonify({'status': 'error', 'message': error_message}), 500
    except Exception as e:
        # Handle other potential exceptions
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/save_raw_json', methods=['POST'])
def save_raw_json_route():
    try:
        data = request.get_json()
        config_type = data.get('config_type')
        config_data = data.get('config_data')
        
        if not config_type or not config_data:
            return jsonify({
                'status': 'error',
                'message': 'Missing config_type or config_data'
            }), 400
        
        if config_type not in ['main', 'secrets']:
            return jsonify({
                'status': 'error',
                'message': 'Invalid config_type. Must be "main" or "secrets"'
            }), 400
        
        # Validate JSON format
        try:
            parsed_data = json.loads(config_data)
        except json.JSONDecodeError as e:
            return jsonify({
                'status': 'error',
                'message': f'Invalid JSON format: {str(e)}'
            }), 400
        
        # Save the raw JSON
        config_manager.save_raw_file_content(config_type, parsed_data)
        
        return jsonify({
            'status': 'success',
            'message': f'{config_type.capitalize()} configuration saved successfully!'
        })
        
    except Exception as e:
        return jsonify({
            'status': 'error',
            'message': f'Error saving raw JSON: {str(e)}'
        }), 400

@app.route('/news_manager/status', methods=['GET'])
def get_news_manager_status():
    """Get news manager status and configuration"""
    try:
        config = config_manager.load_config()
        news_config = config.get('news_manager', {})
        
        # Try to get status from the running display controller if possible
        status = {
            'enabled': news_config.get('enabled', False),
            'enabled_feeds': news_config.get('enabled_feeds', []),
            'available_feeds': [
                'MLB', 'NFL', 'NCAA FB', 'NHL', 'NBA', 'TOP SPORTS', 
                'BIG10', 'NCAA', 'Other'
            ],
            'headlines_per_feed': news_config.get('headlines_per_feed', 2),
            'rotation_enabled': news_config.get('rotation_enabled', True),
            'custom_feeds': news_config.get('custom_feeds', {})
        }
        
        return jsonify({
            'status': 'success',
            'data': status
        })
        
    except Exception as e:
        return jsonify({
            'status': 'error',
            'message': f'Error getting news manager status: {str(e)}'
        }), 400

@app.route('/news_manager/update_feeds', methods=['POST'])
def update_news_feeds():
    """Update enabled news feeds"""
    try:
        data = request.get_json()
        enabled_feeds = data.get('enabled_feeds', [])
        headlines_per_feed = data.get('headlines_per_feed', 2)
        
        config = config_manager.load_config()
        if 'news_manager' not in config:
            config['news_manager'] = {}
            
        config['news_manager']['enabled_feeds'] = enabled_feeds
        config['news_manager']['headlines_per_feed'] = headlines_per_feed
        
        config_manager.save_config(config)
        
        return jsonify({
            'status': 'success',
            'message': 'News feeds updated successfully!'
        })
        
    except Exception as e:
        return jsonify({
            'status': 'error',
            'message': f'Error updating news feeds: {str(e)}'
        }), 400

@app.route('/news_manager/add_custom_feed', methods=['POST'])
def add_custom_news_feed():
    """Add a custom RSS feed"""
    try:
        data = request.get_json()
        name = data.get('name', '').strip()
        url = data.get('url', '').strip()
        
        if not name or not url:
            return jsonify({
                'status': 'error',
                'message': 'Name and URL are required'
            }), 400
            
        config = config_manager.load_config()
        if 'news_manager' not in config:
            config['news_manager'] = {}
        if 'custom_feeds' not in config['news_manager']:
            config['news_manager']['custom_feeds'] = {}
            
        config['news_manager']['custom_feeds'][name] = url
        config_manager.save_config(config)
        
        return jsonify({
            'status': 'success',
            'message': f'Custom feed "{name}" added successfully!'
        })
        
    except Exception as e:
        return jsonify({
            'status': 'error',
            'message': f'Error adding custom feed: {str(e)}'
        }), 400

@app.route('/news_manager/remove_custom_feed', methods=['POST'])
def remove_custom_news_feed():
    """Remove a custom RSS feed"""
    try:
        data = request.get_json()
        name = data.get('name', '').strip()
        
        if not name:
            return jsonify({
                'status': 'error',
                'message': 'Feed name is required'
            }), 400
            
        config = config_manager.load_config()
        custom_feeds = config.get('news_manager', {}).get('custom_feeds', {})
        
        if name in custom_feeds:
            del custom_feeds[name]
            config_manager.save_config(config)
            
            return jsonify({
                'status': 'success',
                'message': f'Custom feed "{name}" removed successfully!'
            })
        else:
            return jsonify({
                'status': 'error',
                'message': f'Custom feed "{name}" not found'
            }), 404
            
    except Exception as e:
        return jsonify({
            'status': 'error',
            'message': f'Error removing custom feed: {str(e)}'
        }), 400

@app.route('/news_manager/toggle', methods=['POST'])
def toggle_news_manager():
    """Toggle news manager on/off"""
    try:
        data = request.get_json()
        enabled = data.get('enabled', False)
        
        config = config_manager.load_config()
        if 'news_manager' not in config:
            config['news_manager'] = {}
            
        config['news_manager']['enabled'] = enabled
        config_manager.save_config(config)
        
        return jsonify({
            'status': 'success',
            'message': f'News manager {"enabled" if enabled else "disabled"} successfully!'
        })
        
    except Exception as e:
        return jsonify({
            'status': 'error',
            'message': f'Error toggling news manager: {str(e)}'
        }), 400

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True) 