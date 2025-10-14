from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify
import json
from pathlib import Path

# Will be initialized when blueprint is registered
config_manager = None
plugin_manager = None
plugin_store_manager = None

pages_v3 = Blueprint('pages_v3', __name__)

@pages_v3.route('/')
def index():
    """Main v3 interface page"""
    try:
        if pages_v3.config_manager:
            # Load configuration data
            main_config = pages_v3.config_manager.load_config()
            schedule_config = main_config.get('schedule', {})

            # Get raw config files for JSON editor
            main_config_data = pages_v3.config_manager.get_raw_file_content('main')
            secrets_config_data = pages_v3.config_manager.get_raw_file_content('secrets')
            main_config_json = json.dumps(main_config_data, indent=4)
            secrets_config_json = json.dumps(secrets_config_data, indent=4)
        else:
            raise Exception("Config manager not initialized")

    except Exception as e:
        flash(f"Error loading configuration: {e}", "error")
        schedule_config = {}
        main_config_json = "{}"
        secrets_config_json = "{}"
        main_config_data = {}
        secrets_config_data = {}
        main_config_path = ""
        secrets_config_path = ""

    return render_template('v3/index.html',
                           schedule_config=schedule_config,
                           main_config_json=main_config_json,
                           secrets_config_json=secrets_config_json,
                           main_config_path=pages_v3.config_manager.get_config_path() if pages_v3.config_manager else "",
                           secrets_config_path=pages_v3.config_manager.get_secrets_path() if pages_v3.config_manager else "",
                           main_config=main_config_data,
                           secrets_config=secrets_config_data)

@pages_v3.route('/partials/<partial_name>')
def load_partial(partial_name):
    """Load HTMX partials dynamically"""
    try:
        # Map partial names to specific data loading
        if partial_name == 'overview':
            return _load_overview_partial()
        elif partial_name == 'general':
            return _load_general_partial()
        elif partial_name == 'display':
            return _load_display_partial()
        elif partial_name == 'durations':
            return _load_durations_partial()
        elif partial_name == 'schedule':
            return _load_schedule_partial()
        elif partial_name == 'sports':
            return _load_sports_partial()
        elif partial_name == 'weather':
            return _load_weather_partial()
        elif partial_name == 'stocks':
            return _load_stocks_partial()
        elif partial_name == 'plugins':
            return _load_plugins_partial()
        elif partial_name == 'fonts':
            return _load_fonts_partial()
        elif partial_name == 'logs':
            return _load_logs_partial()
        elif partial_name == 'raw-json':
            return _load_raw_json_partial()
        else:
            return f"Partial '{partial_name}' not found", 404

    except Exception as e:
        return f"Error loading partial '{partial_name}': {str(e)}", 500

def _load_overview_partial():
    """Load overview partial with system stats"""
    try:
        if pages_v3.config_manager:
            main_config = pages_v3.config_manager.load_config()
            # This would be populated with real system stats via SSE
            return render_template('v3/partials/overview.html',
                                 main_config=main_config)
    except Exception as e:
        return f"Error: {str(e)}", 500

def _load_general_partial():
    """Load general settings partial"""
    try:
        if pages_v3.config_manager:
            main_config = pages_v3.config_manager.load_config()
            return render_template('v3/partials/general.html',
                                 main_config=main_config)
    except Exception as e:
        return f"Error: {str(e)}", 500

def _load_display_partial():
    """Load display settings partial"""
    try:
        if pages_v3.config_manager:
            main_config = pages_v3.config_manager.load_config()
            return render_template('v3/partials/display.html',
                                 main_config=main_config)
    except Exception as e:
        return f"Error: {str(e)}", 500

def _load_durations_partial():
    """Load display durations partial"""
    try:
        if pages_v3.config_manager:
            main_config = pages_v3.config_manager.load_config()
            return render_template('v3/partials/durations.html',
                                 main_config=main_config)
    except Exception as e:
        return f"Error: {str(e)}", 500

def _load_schedule_partial():
    """Load schedule settings partial"""
    try:
        if pages_v3.config_manager:
            main_config = pages_v3.config_manager.load_config()
            schedule_config = main_config.get('schedule', {})
            return render_template('v3/partials/schedule.html',
                                 schedule_config=schedule_config)
    except Exception as e:
        return f"Error: {str(e)}", 500

def _load_sports_partial():
    """Load sports configuration partial"""
    try:
        if pages_v3.config_manager:
            main_config = pages_v3.config_manager.load_config()
            # Sports configuration would be loaded here
            return render_template('v3/partials/sports.html',
                                 main_config=main_config)
    except Exception as e:
        return f"Error: {str(e)}", 500

def _load_weather_partial():
    """Load weather configuration partial"""
    try:
        if pages_v3.config_manager:
            main_config = pages_v3.config_manager.load_config()
            return render_template('v3/partials/weather.html',
                                 main_config=main_config)
    except Exception as e:
        return f"Error: {str(e)}", 500

def _load_stocks_partial():
    """Load stocks configuration partial"""
    try:
        if pages_v3.config_manager:
            main_config = pages_v3.config_manager.load_config()
            return render_template('v3/partials/stocks.html',
                                 main_config=main_config)
    except Exception as e:
        return f"Error: {str(e)}", 500

def _load_plugins_partial():
    """Load plugins management partial"""
    try:
        # Load plugin data from the plugin system
        plugins_data = []

        # Get installed plugins if managers are available
        if pages_v3.plugin_manager and pages_v3.plugin_store_manager:
            try:
                # Get all installed plugin info
                all_plugin_info = pages_v3.plugin_manager.get_all_plugin_info()

                # Format for the web interface
                for plugin_info in all_plugin_info:
                    plugin_id = plugin_info.get('id')

                    # Get enabled status from loaded plugin or config
                    plugin_instance = pages_v3.plugin_manager.get_plugin(plugin_id)
                    enabled = plugin_instance.enabled if plugin_instance else False

                    # Get verified status from store registry
                    store_info = pages_v3.plugin_store_manager.get_plugin_info(plugin_id)
                    verified = store_info.get('verified', False) if store_info else False

                    plugins_data.append({
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
            except Exception as e:
                print(f"Error loading plugin data: {e}")

        return render_template('v3/partials/plugins.html',
                             plugins=plugins_data)
    except Exception as e:
        return f"Error: {str(e)}", 500

def _load_fonts_partial():
    """Load fonts management partial"""
    try:
        # This would load font data from the font system
        fonts_data = {}  # Placeholder for font data
        return render_template('v3/partials/fonts.html',
                             fonts=fonts_data)
    except Exception as e:
        return f"Error: {str(e)}", 500

def _load_logs_partial():
    """Load logs viewer partial"""
    try:
        return render_template('v3/partials/logs.html')
    except Exception as e:
        return f"Error: {str(e)}", 500

def _load_raw_json_partial():
    """Load raw JSON editor partial"""
    try:
        if pages_v3.config_manager:
            main_config_data = pages_v3.config_manager.get_raw_file_content('main')
            secrets_config_data = pages_v3.config_manager.get_raw_file_content('secrets')
            main_config_json = json.dumps(main_config_data, indent=4)
            secrets_config_json = json.dumps(secrets_config_data, indent=4)

            return render_template('v3/partials/raw_json.html',
                                 main_config_json=main_config_json,
                                 secrets_config_json=secrets_config_json,
                                 main_config_path=pages_v3.config_manager.get_config_path(),
                                 secrets_config_path=pages_v3.config_manager.get_secrets_path())
    except Exception as e:
        return f"Error: {str(e)}", 500
