from flask import Flask, Blueprint, render_template, request, redirect, url_for, flash, jsonify, Response
import json
import os
import sys
import subprocess
import time
from pathlib import Path
from datetime import datetime, timedelta

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.config_manager import ConfigManager
from src.plugin_system.plugin_manager import PluginManager
from src.plugin_system.store_manager import PluginStoreManager
from src.plugin_system.saved_repositories import SavedRepositoriesManager
from src.plugin_system.schema_manager import SchemaManager
from src.plugin_system.operation_queue import PluginOperationQueue
from src.plugin_system.state_manager import PluginStateManager
from src.plugin_system.operation_history import OperationHistory
from src.plugin_system.health_monitor import PluginHealthMonitor
from src.wifi_manager import WiFiManager

# Create Flask app
app = Flask(__name__)
app.secret_key = os.urandom(24)
config_manager = ConfigManager()

# Initialize CSRF protection (optional for local-only, but recommended for defense-in-depth)
try:
    from flask_wtf.csrf import CSRFProtect
    csrf = CSRFProtect(app)
    # Exempt SSE streams from CSRF (read-only)
    from functools import wraps
    from flask import request
    
    def csrf_exempt(f):
        """Decorator to exempt a route from CSRF protection."""
        f.csrf_exempt = True
        return f
    
    # Mark SSE streams and API endpoints as exempt
    @app.before_request
    def check_csrf_exempt():
        """Check if route should be exempt from CSRF."""
        if request.endpoint:
            # SSE streams are read-only, exempt from CSRF
            if 'stream' in request.endpoint:
                pass
            # All API v3 endpoints are programmatic API calls (HTMX/fetch) and don't include CSRF tokens
            elif request.endpoint.startswith('api_v3.'):
                # Exempt this endpoint from CSRF validation
                from flask_wtf.csrf import exempt
                # Get the view function for this endpoint
                view_func = app.view_functions.get(request.endpoint)
                if view_func:
                    view_func.csrf_exempt = True
except ImportError:
    # flask-wtf not installed, CSRF protection disabled
    csrf = None
    pass

# Initialize rate limiting (prevent accidental abuse, not security)
try:
    from flask_limiter import Limiter
    from flask_limiter.util import get_remote_address
    
    limiter = Limiter(
        app=app,
        key_func=get_remote_address,
        default_limits=["1000 per minute"],  # Generous limit for local use
        storage_uri="memory://"  # In-memory storage for simplicity
    )
except ImportError:
    # flask-limiter not installed, rate limiting disabled
    limiter = None
    pass

# Import cache functions from separate module to avoid circular imports
from web_interface.cache import get_cached, set_cached, invalidate_cache

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

# Initialize schema manager
schema_manager = SchemaManager(
    plugins_dir=plugins_dir,
    project_root=project_root,
    logger=None
)

# Initialize operation queue for plugin operations
# Use lazy_load=True to defer file loading until first use (improves startup time)
operation_queue = PluginOperationQueue(
    history_file=str(project_root / "data" / "plugin_operations.json"),
    max_history=500,
    lazy_load=True
)

# Initialize plugin state manager
# Use lazy_load=True to defer file loading until first use (improves startup time)
plugin_state_manager = PluginStateManager(
    state_file=str(project_root / "data" / "plugin_state.json"),
    auto_save=True,
    lazy_load=True
)

# Initialize operation history
# Use lazy_load=True to defer file loading until first use (improves startup time)
operation_history = OperationHistory(
    history_file=str(project_root / "data" / "operation_history.json"),
    max_records=1000,
    lazy_load=True
)

# Initialize health monitoring (if health tracker is available)
# Deferred until first request to improve startup time
health_monitor = None
_health_monitor_initialized = False

# Plugin discovery is deferred until first API request that needs it
# This improves startup time - endpoints will call discover_plugins() when needed

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
api_v3.schema_manager = schema_manager
api_v3.operation_queue = operation_queue
api_v3.plugin_state_manager = plugin_state_manager
api_v3.operation_history = operation_history
api_v3.health_monitor = health_monitor
# Initialize cache manager for API endpoints
from src.cache_manager import CacheManager
api_v3.cache_manager = CacheManager()

app.register_blueprint(pages_v3, url_prefix='/v3')
app.register_blueprint(api_v3, url_prefix='/api/v3')

# Exempt entire API v3 blueprint from CSRF (all endpoints are programmatic API calls)
# This is safer and cleaner than exempting individual endpoints
if csrf:
    csrf.exempt(api_v3)

# Helper function to check if AP mode is active
def is_ap_mode_active():
    """
    Check if access point mode is currently active.
    
    Returns:
        bool: True if AP mode is active, False otherwise.
              Returns False on error to avoid breaking normal operation.
    """
    try:
        wifi_manager = WiFiManager()
        return wifi_manager._is_ap_mode_active()
    except Exception as e:
        # Log error but don't break normal operation
        # Default to False so normal web interface works even if check fails
        print(f"Warning: Could not check AP mode status: {e}")
        return False

# Captive portal detection endpoints
# These help devices detect that a captive portal is active
@app.route('/hotspot-detect.html')
def hotspot_detect():
    """iOS/macOS captive portal detection endpoint"""
    # Return simple HTML that redirects to setup page
    return '<HTML><HEAD><TITLE>Success</TITLE></HEAD><BODY>Success</BODY></HTML>', 200

@app.route('/generate_204')
def generate_204():
    """Android captive portal detection endpoint"""
    # Return 204 No Content - Android checks for this
    return '', 204

@app.route('/connecttest.txt')
def connecttest_txt():
    """Windows captive portal detection endpoint"""
    # Return simple text response
    return 'Microsoft Connect Test', 200

@app.route('/success.txt')
def success_txt():
    """Firefox captive portal detection endpoint"""
    # Return simple text response
    return 'success', 200

# Initialize logging
try:
    from web_interface.logging_config import setup_web_interface_logging, log_api_request
    # Use JSON logging in production, readable logs in development
    use_json_logging = os.environ.get('LEDMATRIX_JSON_LOGGING', 'false').lower() == 'true'
    setup_web_interface_logging(level='INFO', use_json=use_json_logging)
except ImportError:
    # Logging config not available, use default
    log_api_request = None
    pass

# Request timing and logging middleware
@app.before_request
def before_request():
    """Track request start time for logging."""
    from flask import request
    request.start_time = time.time()

@app.after_request
def after_request_logging(response):
    """Log API requests after response."""
    if log_api_request:
        try:
            from flask import request
            duration_ms = (time.time() - getattr(request, 'start_time', time.time())) * 1000
            ip_address = request.remote_addr if hasattr(request, 'remote_addr') else None
            log_api_request(
                method=request.method,
                path=request.path,
                status_code=response.status_code,
                duration_ms=duration_ms,
                ip_address=ip_address
            )
        except Exception:
            pass  # Don't break response if logging fails
    return response

# Global error handlers
@app.errorhandler(404)
def not_found_error(error):
    """Handle 404 errors."""
    return jsonify({
        'status': 'error',
        'error_code': 'NOT_FOUND',
        'message': 'Resource not found',
        'path': request.path
    }), 404

@app.errorhandler(500)
def internal_error(error):
    """Handle 500 errors."""
    import traceback
    error_details = traceback.format_exc()
    
    # Log the error
    import logging
    logger = logging.getLogger('web_interface')
    logger.error(f"Internal server error: {error}", exc_info=True)
    
    # Return user-friendly error (hide internal details in production)
    return jsonify({
        'status': 'error',
        'error_code': 'INTERNAL_ERROR',
        'message': 'An internal error occurred',
        'details': error_details if app.debug else None
    }), 500

@app.errorhandler(Exception)
def handle_exception(error):
    """Handle all unhandled exceptions."""
    import traceback
    import logging
    logger = logging.getLogger('web_interface')
    logger.error(f"Unhandled exception: {error}", exc_info=True)
    
    return jsonify({
        'status': 'error',
        'error_code': 'UNKNOWN_ERROR',
        'message': str(error) if app.debug else 'An error occurred',
        'details': traceback.format_exc() if app.debug else None
    }), 500

# Captive portal redirect middleware
@app.before_request
def captive_portal_redirect():
    """
    Redirect all HTTP requests to WiFi setup page when AP mode is active.
    This creates a captive portal experience where users are automatically
    directed to the WiFi configuration page.
    """
    # Check if AP mode is active
    if not is_ap_mode_active():
        return None  # Continue normal request processing
    
    # Get the request path
    path = request.path
    
    # List of paths that should NOT be redirected (allow normal operation)
    # This ensures the full web interface works normally when in AP mode
    allowed_paths = [
        '/v3',  # Main interface and all sub-paths
        '/api/v3/',  # All API endpoints (plugins, config, wifi, stream, etc.)
        '/static/',  # Static files (CSS, JS, images)
        '/hotspot-detect.html',  # iOS/macOS detection
        '/generate_204',  # Android detection
        '/connecttest.txt',  # Windows detection
        '/success.txt',  # Firefox detection
        '/favicon.ico',  # Favicon
    ]
    
    # Check if this path should be allowed
    for allowed_path in allowed_paths:
        if path.startswith(allowed_path):
            return None  # Allow this request to proceed normally
    
    # For all other paths, redirect to main interface
    # This ensures users see the WiFi setup page when they try to access any website
    # The main interface (/v3) is already in allowed_paths, so it won't redirect
    # Static files (/static/) and API calls (/api/v3/) are also allowed
    return redirect(url_for('pages_v3.index'), code=302)

# Add security headers and caching to all responses
@app.after_request
def add_security_headers(response):
    """Add security headers and caching to all responses"""
    # Only set standard security headers - avoid Permissions-Policy to prevent browser warnings
    # about unrecognized features
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'SAMEORIGIN'
    response.headers['X-XSS-Protection'] = '1; mode=block'
    
    # Add caching headers for static assets
    if request.path.startswith('/static/'):
        # Cache static assets for 1 year (with versioning via query params)
        response.headers['Cache-Control'] = 'public, max-age=31536000, immutable'
        response.headers['Expires'] = (datetime.now() + timedelta(days=365)).strftime('%a, %d %b %Y %H:%M:%S GMT')
    elif request.path.startswith('/api/v3/'):
        # Short cache for API responses (5 seconds) to allow for quick updates
        # but reduce server load for repeated requests
        if request.method == 'GET' and 'stream' not in request.path:
            response.headers['Cache-Control'] = 'private, max-age=5, must-revalidate'
    else:
        # No cache for HTML pages to ensure fresh content
        response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
        response.headers['Pragma'] = 'no-cache'
        response.headers['Expires'] = '0'
    
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
        time.sleep(10)  # Update every 10 seconds (reduced frequency for better performance)

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
        
        time.sleep(0.5)  # Check 2 times per second (reduced frequency for better performance)

# Logs generator for SSE
def logs_generator():
    """Generate log updates from journalctl"""
    while True:
        try:
            # Get recent logs from journalctl (simplified version)
            # Note: User should be in systemd-journal group to read logs without sudo
            try:
                result = subprocess.run(
                    ['journalctl', '-u', 'ledmatrix.service', '-n', '50', '--no-pager'],
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

        time.sleep(5)  # Update every 5 seconds (reduced frequency for better performance)

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

# Exempt SSE streams from CSRF and add rate limiting
if csrf:
    csrf.exempt(stream_stats)
    csrf.exempt(stream_display)
    csrf.exempt(stream_logs)
    # Note: api_v3 blueprint is exempted above after registration

if limiter:
    limiter.limit("20 per minute")(stream_stats)
    limiter.limit("20 per minute")(stream_display)
    limiter.limit("20 per minute")(stream_logs)

# Main route - redirect to v3 interface as default
@app.route('/')
def index():
    """Redirect to v3 interface"""
    return redirect(url_for('pages_v3.index'))

@app.route('/favicon.ico')
def favicon():
    """Return 204 No Content for favicon to avoid 404 errors"""
    return '', 204

def _initialize_health_monitor():
    """Initialize health monitoring after server is ready to accept requests."""
    global health_monitor, _health_monitor_initialized
    if _health_monitor_initialized:
        return
    
    if health_monitor is None and hasattr(plugin_manager, 'health_tracker') and plugin_manager.health_tracker:
        try:
            health_monitor = PluginHealthMonitor(
                health_tracker=plugin_manager.health_tracker,
                check_interval=60.0,  # Check every minute
                degraded_threshold=0.5,
                unhealthy_threshold=0.8,
                max_response_time=5.0
            )
            health_monitor.start_monitoring()
            print("✓ Plugin health monitoring started")
        except Exception as e:
            print(f"⚠ Could not start health monitoring: {e}")
    
    _health_monitor_initialized = True

# Initialize health monitor on first request (using before_request for compatibility)
@app.before_request
def check_health_monitor():
    """Ensure health monitor is initialized on first request."""
    if not _health_monitor_initialized:
        _initialize_health_monitor()

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
