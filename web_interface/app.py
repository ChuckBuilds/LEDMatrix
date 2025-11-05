from flask import Flask, Blueprint, render_template, request, redirect, url_for, flash, jsonify, Response
import json
import os
import sys
import subprocess
import time
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.config_manager import ConfigManager
from src.plugin_system.plugin_manager import PluginManager
from src.plugin_system.store_manager import PluginStoreManager
from src.plugin_system.saved_repositories import SavedRepositoriesManager

# Create Flask app
app = Flask(__name__)
app.secret_key = os.urandom(24)
config_manager = ConfigManager()

# Initialize plugin managers - read plugins directory from config
config = config_manager.load_config()
plugin_system_config = config.get('plugin_system', {})
plugins_dir_name = plugin_system_config.get('plugins_directory', 'plugin-repos')

# Resolve plugin directory - handle both absolute and relative paths
if os.path.isabs(plugins_dir_name):
    plugins_dir = Path(plugins_dir_name)
else:
    # If relative, resolve relative to the project root (LEDMatrix directory)
    project_root = Path(__file__).parent.parent
    plugins_dir = project_root / plugins_dir_name

plugin_manager = PluginManager(
    plugins_dir=str(plugins_dir),
    config_manager=config_manager,
    display_manager=None,  # Not needed for web interface
    cache_manager=None     # Not needed for web interface
)
plugin_store_manager = PluginStoreManager(plugins_dir=str(plugins_dir))
saved_repositories_manager = SavedRepositoriesManager()

# Discover and load plugins
plugin_manager.discover_plugins()
# Note: We don't auto-load plugins here since we only need metadata for the web interface

# Register blueprints
from web_interface.blueprints.pages_v3 import pages_v3
from web_interface.blueprints.api_v3 import api_v3

# Initialize managers in blueprints
pages_v3.config_manager = config_manager
pages_v3.plugin_manager = plugin_manager
pages_v3.plugin_store_manager = plugin_store_manager
pages_v3.saved_repositories_manager = saved_repositories_manager

api_v3.config_manager = config_manager
api_v3.plugin_manager = plugin_manager
api_v3.plugin_store_manager = plugin_store_manager
api_v3.saved_repositories_manager = saved_repositories_manager

app.register_blueprint(pages_v3, url_prefix='/v3')
app.register_blueprint(api_v3, url_prefix='/api/v3')

# Add security headers to all responses
@app.after_request
def add_security_headers(response):
    """Add security headers to all responses"""
    # Only set standard security headers - avoid Permissions-Policy to prevent browser warnings
    # about unrecognized features
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'SAMEORIGIN'
    response.headers['X-XSS-Protection'] = '1; mode=block'
    
    return response

# SSE helper function
def sse_response(generator_func):
    """Helper to create SSE responses"""
    def generate():
        for data in generator_func():
            yield f"data: {json.dumps(data)}\n\n"
    return Response(generate(), mimetype='text/event-stream')

# System status generator for SSE
def system_status_generator():
    """Generate system status updates"""
    while True:
        try:
            # Try to import psutil for system stats
            try:
                import psutil
                cpu_percent = round(psutil.cpu_percent(interval=1), 1)
                memory = psutil.virtual_memory()
                memory_used_percent = round(memory.percent, 1)
                
                # Try to get CPU temperature (Raspberry Pi specific)
                cpu_temp = 0
                try:
                    with open('/sys/class/thermal/thermal_zone0/temp', 'r') as f:
                        cpu_temp = round(float(f.read()) / 1000.0, 1)
                except:
                    pass
                    
            except ImportError:
                cpu_percent = 0
                memory_used_percent = 0
                cpu_temp = 0
            
            # Check if display service is running
            service_active = False
            try:
                result = subprocess.run(['systemctl', 'is-active', 'ledmatrix'], 
                                      capture_output=True, text=True, timeout=2)
                service_active = result.stdout.strip() == 'active'
            except:
                pass
            
            status = {
                'timestamp': time.time(),
                'uptime': 'Running',
                'service_active': service_active,
                'cpu_percent': cpu_percent,
                'memory_used_percent': memory_used_percent,
                'cpu_temp': cpu_temp,
                'disk_used_percent': 0
            }
            yield status
        except Exception as e:
            yield {'error': str(e)}
        time.sleep(5)  # Update every 5 seconds

# Display preview generator for SSE
def display_preview_generator():
    """Generate display preview updates from snapshot file"""
    import base64
    from PIL import Image
    import io
    
    snapshot_path = "/tmp/led_matrix_preview.png"
    last_modified = None
    
    # Get display dimensions from config
    try:
        main_config = config_manager.load_config()
        cols = main_config.get('display', {}).get('hardware', {}).get('cols', 64)
        chain_length = main_config.get('display', {}).get('hardware', {}).get('chain_length', 2)
        rows = main_config.get('display', {}).get('hardware', {}).get('rows', 32)
        parallel = main_config.get('display', {}).get('hardware', {}).get('parallel', 1)
        width = cols * chain_length
        height = rows * parallel
    except:
        width = 128
        height = 64
    
    while True:
        try:
            # Check if snapshot file exists and has been modified
            if os.path.exists(snapshot_path):
                current_modified = os.path.getmtime(snapshot_path)
                
                # Only read if file is new or has been updated
                if last_modified is None or current_modified > last_modified:
                    try:
                        # Read and encode the image
                        with Image.open(snapshot_path) as img:
                            # Convert to PNG and encode as base64
                            buffer = io.BytesIO()
                            img.save(buffer, format='PNG')
                            img_str = base64.b64encode(buffer.getvalue()).decode('utf-8')
                            
                            preview_data = {
                                'timestamp': time.time(),
                                'width': width,
                                'height': height,
                                'image': img_str
                            }
                            last_modified = current_modified
                            yield preview_data
                    except Exception as read_err:
                        # File might be being written, skip this update
                        pass
            else:
                # No snapshot available
                yield {
                    'timestamp': time.time(),
                    'width': width,
                    'height': height,
                    'image': None
                }
                
        except Exception as e:
            yield {'error': str(e)}
        
        time.sleep(0.1)  # Check 10 times per second

# Logs generator for SSE
def logs_generator():
    """Generate log updates from journalctl"""
    while True:
        try:
            # Get recent logs from journalctl (simplified version)
            try:
                result = subprocess.run(
                    ['sudo', 'journalctl', '-u', 'ledmatrix.service', '-n', '50', '--no-pager'],
                    capture_output=True, text=True, timeout=5
                )

                if result.returncode == 0:
                    logs_text = result.stdout.strip()
                    if logs_text:
                        logs_data = {
                            'timestamp': time.time(),
                            'logs': logs_text
                        }
                        yield logs_data
                    else:
                        # No logs available
                        logs_data = {
                            'timestamp': time.time(),
                            'logs': 'No logs available from ledmatrix service'
                        }
                        yield logs_data
                else:
                    # journalctl failed
                    error_data = {
                        'timestamp': time.time(),
                        'logs': f'journalctl failed with return code {result.returncode}: {result.stderr.strip()}'
                    }
                    yield error_data

            except subprocess.TimeoutExpired:
                # Timeout - just skip this update
                pass
            except Exception as e:
                error_data = {
                    'timestamp': time.time(),
                    'logs': f'Error running journalctl: {str(e)}'
                }
                yield error_data

        except Exception as e:
            error_data = {
                'timestamp': time.time(),
                'logs': f'Unexpected error in logs generator: {str(e)}'
            }
            yield error_data

        time.sleep(2)  # Update every 2 seconds

# SSE endpoints
@app.route('/api/v3/stream/stats')
def stream_stats():
    return sse_response(system_status_generator)

@app.route('/api/v3/stream/display')
def stream_display():
    return sse_response(display_preview_generator)

@app.route('/api/v3/stream/logs')
def stream_logs():
    return sse_response(logs_generator)

# Main route - redirect to v3 interface as default
@app.route('/')
def index():
    """Redirect to v3 interface"""
    return redirect(url_for('pages_v3.index'))

@app.route('/favicon.ico')
def favicon():
    """Return 204 No Content for favicon to avoid 404 errors"""
    return '', 204

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
