from flask import Flask, request, redirect, url_for, jsonify, Response, send_from_directory
import json
import logging
import os
import queue
import re
import shutil
import sys
import subprocess
import threading
import time
from pathlib import Path
from datetime import datetime, timedelta

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.config_manager import ConfigManager
from src.exceptions import ConfigError
from src.plugin_system.plugin_manager import PluginManager
from src.plugin_system.store_manager import PluginStoreManager
from src.plugin_system.saved_repositories import SavedRepositoriesManager
from src.plugin_system.schema_manager import SchemaManager
from src.plugin_system.operation_queue import PluginOperationQueue
from src.plugin_system.state_manager import PluginStateManager
from src.plugin_system.operation_history import OperationHistory
from src.plugin_system.health_monitor import PluginHealthMonitor

_JOURNALCTL = shutil.which('journalctl')
_SYSTEMCTL = shutil.which('systemctl')
_VCGENCMD = shutil.which('vcgencmd')

# Create Flask app
app = Flask(__name__)
app.secret_key = os.urandom(24)
config_manager = ConfigManager()

# CSRF protection disabled for local-only application
# CSRF is designed for internet-facing web apps to prevent cross-site request forgery.
# For a local-only Raspberry Pi application, the threat model is different:
# - If an attacker has network access to perform CSRF, they have other attack vectors
# - All API endpoints are programmatic (HTMX/fetch) and don't include CSRF tokens
# - Forms use HTMX which doesn't automatically include CSRF tokens
# If you need CSRF protection (e.g., exposing to internet), properly implement CSRF tokens in HTMX forms
csrf = None

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

# Import cache functions from separate module to avoid circular imports

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
# A core `git pull` update (or any checkout) restores built-in plugins
# committed under plugin-repos/, even ones the user uninstalled. Re-remove any
# the user previously uninstalled at startup so a manual update on the Pi
# doesn't resurrect them.
try:
    _purged = plugin_store_manager.purge_uninstalled_plugins()
    if _purged:
        logging.getLogger(__name__).info(
            "Re-removed %d uninstalled plugin(s) restored since last run: %s",
            len(_purged), ", ".join(_purged),
        )
except (OSError, RuntimeError) as _purge_err:
    logging.getLogger(__name__).warning(
        "Startup plugin purge failed: %s", _purge_err
    )
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

# Route to serve plugin asset files (registered on main app, not blueprint, for /assets/... path)
@app.route('/assets/plugins/<plugin_id>/uploads/<path:filename>', methods=['GET'])
def serve_plugin_asset(plugin_id, filename):
    """Serve uploaded asset files from assets/plugins/{plugin_id}/uploads/"""
    try:
        # Build the asset directory path
        assets_dir = project_root / 'assets' / 'plugins' / plugin_id / 'uploads'
        assets_dir = assets_dir.resolve()
        
        # Security check: ensure the assets directory exists and is within project_root
        if not assets_dir.exists() or not assets_dir.is_dir():
            return jsonify({'status': 'error', 'message': 'Asset directory not found'}), 404
        
        # Ensure we're serving from within the assets directory (prevent directory traversal)
        # Use proper path resolution instead of string prefix matching to prevent bypasses
        assets_dir_resolved = assets_dir.resolve()
        project_root_resolved = project_root.resolve()
        
        # Check that assets_dir is actually within project_root using commonpath
        try:
            common_path = os.path.commonpath([str(assets_dir_resolved), str(project_root_resolved)])
            if common_path != str(project_root_resolved):
                return jsonify({'status': 'error', 'message': 'Invalid asset path'}), 403
        except ValueError:
            # commonpath raises ValueError if paths are on different drives (Windows)
            return jsonify({'status': 'error', 'message': 'Invalid asset path'}), 403
        
        # Resolve the requested file path
        requested_file = (assets_dir / filename).resolve()
        
        # Security check: ensure file is within the assets directory using proper path comparison
        # Use commonpath to ensure assets_dir is a true parent of requested_file
        try:
            common_path = os.path.commonpath([str(requested_file), str(assets_dir_resolved)])
            if common_path != str(assets_dir_resolved):
                return jsonify({'status': 'error', 'message': 'Invalid file path'}), 403
        except ValueError:
            # commonpath raises ValueError if paths are on different drives (Windows)
            return jsonify({'status': 'error', 'message': 'Invalid file path'}), 403
        
        # Check if file exists
        if not requested_file.exists() or not requested_file.is_file():
            return jsonify({'status': 'error', 'message': 'File not found'}), 404
        
        # Determine content type based on file extension
        content_type = 'application/octet-stream'
        if filename.lower().endswith(('.png', '.jpg', '.jpeg')):
            content_type = 'image/jpeg' if filename.lower().endswith(('.jpg', '.jpeg')) else 'image/png'
        elif filename.lower().endswith('.gif'):
            content_type = 'image/gif'
        elif filename.lower().endswith('.bmp'):
            content_type = 'image/bmp'
        elif filename.lower().endswith('.webp'):
            content_type = 'image/webp'
        elif filename.lower().endswith('.svg'):
            content_type = 'image/svg+xml'
        elif filename.lower().endswith('.json'):
            content_type = 'application/json'
        elif filename.lower().endswith('.txt'):
            content_type = 'text/plain'
        
        # Use send_from_directory to serve the file
        return send_from_directory(str(assets_dir), filename, mimetype=content_type)
        
    except Exception:
        app.logger.exception('Error serving plugin asset file')
        return jsonify({
            'status': 'error',
            'message': 'Internal server error'
        }), 500

# Prime psutil CPU measurement once at startup so interval=None returns a real value
try:
    import psutil as _psutil_prime
    _psutil_prime.cpu_percent(interval=None)
except ImportError:
    pass

# Cached AP mode check — avoids creating a WiFiManager per request
_ap_mode_cache = {'value': False, 'timestamp': 0}
_AP_MODE_CACHE_TTL = 30  # seconds — AP mode is user-initiated; 30s is fine

# Cached ledmatrix service status for SSE stats stream
_ledmatrix_service_cache = {'active': False, 'timestamp': 0}
_LEDMATRIX_SERVICE_CACHE_TTL = 15  # seconds

def is_ap_mode_active():
    """
    Check if access point mode is currently active (cached, 30s TTL).
    Uses a direct systemctl check instead of instantiating WiFiManager.
    """
    now = time.time()
    if (now - _ap_mode_cache['timestamp']) < _AP_MODE_CACHE_TTL:
        return _ap_mode_cache['value']
    try:
        result = subprocess.run(
            ['systemctl', 'is-active', 'hostapd'],
            capture_output=True, text=True, timeout=2
        )
        active = result.stdout.strip() == 'active'
        _ap_mode_cache['value'] = active
        _ap_mode_cache['timestamp'] = now
        return active
    except (subprocess.SubprocessError, OSError) as e:
        logging.getLogger('web_interface').error(f"AP mode check failed: {e}")
        return _ap_mode_cache['value']

# Captive portal detection endpoints
# When AP mode is active, return responses that TRIGGER the captive portal popup.
# When not in AP mode, return normal "success" responses so connectivity checks pass.
@app.route('/hotspot-detect.html')
def hotspot_detect():
    """iOS/macOS captive portal detection endpoint"""
    if is_ap_mode_active():
        # Non-"Success" title triggers iOS captive portal popup
        return redirect(url_for('pages_v3.captive_setup'), code=302)
    return '<HTML><HEAD><TITLE>Success</TITLE></HEAD><BODY>Success</BODY></HTML>', 200

@app.route('/generate_204')
def generate_204():
    """Android captive portal detection endpoint"""
    if is_ap_mode_active():
        # Android expects 204 = "internet works". Non-204 triggers portal popup.
        return redirect(url_for('pages_v3.captive_setup'), code=302)
    return '', 204

@app.route('/connecttest.txt')
def connecttest_txt():
    """Windows captive portal detection endpoint"""
    if is_ap_mode_active():
        return redirect(url_for('pages_v3.captive_setup'), code=302)
    return 'Microsoft Connect Test', 200

@app.route('/success.txt')
def success_txt():
    """Firefox captive portal detection endpoint"""
    if is_ap_mode_active():
        return redirect(url_for('pages_v3.captive_setup'), code=302)
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
        except Exception:  # nosec B110 - request logging must never interrupt a live HTTP response
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
    import logging
    logger = logging.getLogger('web_interface')
    logger.error("Internal server error", exc_info=True)
    return jsonify({
        'status': 'error',
        'error_code': 'INTERNAL_ERROR',
        'message': 'An internal error occurred; see logs for details',
    }), 500

@app.errorhandler(Exception)
def handle_exception(error):
    """Handle all unhandled exceptions."""
    import logging
    logger = logging.getLogger('web_interface')
    logger.error("Unhandled exception", exc_info=True)
    return jsonify({
        'status': 'error',
        'error_code': 'UNKNOWN_ERROR',
        'message': 'An error occurred; see logs for details',
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
    allowed_paths = [
        '/v3',  # Main interface and all sub-paths (includes /v3/setup)
        '/api/v3/',  # All API endpoints
        '/static/',  # Static files (CSS, JS, images)
        '/hotspot-detect.html',  # iOS/macOS detection
        '/generate_204',  # Android detection
        '/connecttest.txt',  # Windows detection
        '/success.txt',  # Firefox detection
        '/favicon.ico',  # Favicon
    ]

    for allowed_path in allowed_paths:
        if path.startswith(allowed_path):
            return None

    # Redirect to lightweight captive portal setup page (not the full UI)
    return redirect(url_for('pages_v3.captive_setup'), code=302)

# Append a content-version query param (file mtime) to every static URL so the
# long-lived `immutable` cache (see add_security_headers below) is actually safe:
# when a static file changes its URL changes, so browsers refetch it. Without
# this, edited JS/CSS were served immutable under an unchanging URL and never
# reached clients until a manual cache clear.
@app.url_defaults
def add_static_version(endpoint, values):
    if endpoint == 'static' and values.get('filename'):
        try:
            file_path = os.path.join(app.static_folder, values['filename'])
            values['v'] = int(os.path.getmtime(file_path))
        except OSError:
            # File missing (e.g. plugin asset not yet installed) — skip versioning.
            pass


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

class _StreamBroadcaster:
    """Fan-out broadcaster: one background generator thread pushes to all SSE clients.

    This means N browser tabs share one generator instead of each running their own,
    keeping PIL encodes / subprocess forks constant regardless of how many tabs are open.
    """

    def __init__(self, generator_factory):
        self._generator_factory = generator_factory
        self._clients: set = set()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None

    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=5)
        with self._lock:
            self._clients.add(q)
            if not (self._thread and self._thread.is_alive()):
                self._thread = threading.Thread(target=self._broadcast, daemon=True)
                self._thread.start()
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self._lock:
            self._clients.discard(q)

    def _broadcast(self):
        for data in self._generator_factory():
            with self._lock:
                if not self._clients:
                    # No subscribers — exit so the thread doesn't spin indefinitely.
                    # subscribe() will restart it when a new client arrives.
                    break
                for q in self._clients:
                    try:
                        q.put_nowait(data)
                    except queue.Full:
                        # Client is reading too slowly; drop the oldest item and
                        # deliver the latest so the queue never stalls the client.
                        try:
                            q.get_nowait()
                        except queue.Empty:
                            pass
                        try:
                            q.put_nowait(data)
                        except queue.Full:
                            pass

def _get_power_status():
    """Check Raspberry Pi under-voltage/throttling status via vcgencmd.

    Returns a dict of decoded flags, or None on non-Pi platforms (no
    vcgencmd) or if the call fails for any reason. See:
    https://www.raspberrypi.com/documentation/computers/os.html#get_throttled
    """
    if not _VCGENCMD:
        return None
    try:
        result = subprocess.run(
            [_VCGENCMD, 'get_throttled'], capture_output=True, text=True, timeout=2
        )
        match = re.search(r'0x([0-9a-fA-F]+)', result.stdout)
        if not match:
            return None
        bits = int(match.group(1), 16)
        return {
            'under_voltage_now': bool(bits & 0x1),
            'freq_capped_now': bool(bits & 0x2),
            'throttled_now': bool(bits & 0x4),
            'soft_temp_limit_now': bool(bits & 0x8),
            'under_voltage_occurred': bool(bits & 0x10000),
            'freq_capped_occurred': bool(bits & 0x20000),
            'throttled_occurred': bool(bits & 0x40000),
            'soft_temp_limit_occurred': bool(bits & 0x80000),
        }
    except (subprocess.SubprocessError, OSError, ValueError) as e:
        app.logger.debug("vcgencmd get_throttled failed: %s", e)
        return None

# System status generator for SSE
def system_status_generator():
    """Generate system status updates"""
    while True:
        try:
            # Try to import psutil for system stats
            try:
                import psutil
                # interval=None is non-blocking; primed at module startup above
                cpu_percent = round(psutil.cpu_percent(interval=None), 1)
                memory = psutil.virtual_memory()
                memory_used_percent = round(memory.percent, 1)

                # Try to get CPU temperature (Raspberry Pi specific)
                cpu_temp = 0
                try:
                    with open('/sys/class/thermal/thermal_zone0/temp', 'r') as f:
                        cpu_temp = round(float(f.read()) / 1000.0, 1)
                except (OSError, ValueError):
                    pass

            except ImportError:
                cpu_percent = 0
                memory_used_percent = 0
                cpu_temp = 0

            # Check if display service is running (cached to avoid per-client subprocess forks)
            now = time.time()
            if (now - _ledmatrix_service_cache['timestamp']) >= _LEDMATRIX_SERVICE_CACHE_TTL:
                if _SYSTEMCTL:
                    try:
                        result = subprocess.run([_SYSTEMCTL, 'is-active', 'ledmatrix'],
                                                capture_output=True, text=True, timeout=2)
                        _ledmatrix_service_cache['active'] = result.stdout.strip() == 'active'
                    except (subprocess.SubprocessError, OSError) as e:
                        app.logger.warning("systemctl status check failed: %s", e)
                _ledmatrix_service_cache['timestamp'] = now
            service_active = _ledmatrix_service_cache['active']
            
            status = {
                'timestamp': time.time(),
                'uptime': 'Running',
                'service_active': service_active,
                'cpu_percent': cpu_percent,
                'memory_used_percent': memory_used_percent,
                'cpu_temp': cpu_temp,
                'disk_used_percent': 0,
                'power': _get_power_status()
            }
            yield status
        except Exception as e:
            app.logger.error("SSE generator error", exc_info=True)
            yield {'error': 'An error occurred; see server logs'}
        time.sleep(10)  # Update every 10 seconds (reduced frequency for better performance)

# Display preview generator for SSE
def display_preview_generator():
    """Generate display preview updates from snapshot file"""
    import base64
    from PIL import Image
    import io
    
    snapshot_path = "/tmp/led_matrix_preview.png"  # nosec B108 - fixed path matches display_manager; only read here
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
    except (KeyError, TypeError, ValueError, ConfigError):
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
                    except Exception:  # nosec B110 - SSE preview file may be mid-write; transient error, skip this update
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
            app.logger.error("SSE generator error", exc_info=True)
            yield {'error': 'An error occurred; see server logs'}
        
        time.sleep(1.0)  # Check once per second — halves PIL encode overhead vs 0.5s

# Logs generator for SSE
def logs_generator():
    """Generate log updates from journalctl"""
    while True:
        try:
            # Get recent logs from journalctl (simplified version)
            # Note: User should be in systemd-journal group to read logs without sudo
            try:
                if not _JOURNALCTL:
                    yield {'timestamp': time.time(), 'logs': 'journalctl not found; cannot read logs'}
                    time.sleep(60)
                    continue
                result = subprocess.run(
                    [_JOURNALCTL, '-u', 'ledmatrix.service', '-u', 'ledmatrix-web.service',
                     '-n', '50', '--no-pager', '--output=short-iso'],
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
                            'logs': 'No logs available from ledmatrix or ledmatrix-web service'
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
            except Exception:
                app.logger.error("Error running journalctl", exc_info=True)
                error_data = {
                    'timestamp': time.time(),
                    'logs': 'Error running journalctl; see server logs'
                }
                yield error_data

        except Exception:
            app.logger.error("Unexpected error in logs generator", exc_info=True)
            error_data = {
                'timestamp': time.time(),
                'logs': 'Unexpected error in logs generator; see server logs'
            }
            yield error_data

        time.sleep(5)  # Update every 5 seconds (reduced frequency for better performance)

# One broadcaster per stream — shared across all SSE clients
_stats_broadcaster = _StreamBroadcaster(system_status_generator)
_display_broadcaster = _StreamBroadcaster(display_preview_generator)
_logs_broadcaster = _StreamBroadcaster(logs_generator)


def _sse_stream(broadcaster: _StreamBroadcaster) -> Response:
    """Return a streaming SSE response backed by a shared broadcaster."""
    q = broadcaster.subscribe()

    def generate():
        try:
            while True:
                try:
                    data = q.get(timeout=30)
                    yield f"data: {json.dumps(data)}\n\n"
                except queue.Empty:
                    # Send an SSE comment heartbeat to keep the connection alive
                    # through proxies that close idle connections.
                    yield ": heartbeat\n\n"
        except GeneratorExit:
            pass
        finally:
            broadcaster.unsubscribe(q)

    return Response(generate(), mimetype='text/event-stream')


# SSE endpoints
@app.route('/api/v3/stream/stats')
def stream_stats():
    return _sse_stream(_stats_broadcaster)

@app.route('/api/v3/stream/display')
def stream_display():
    return _sse_stream(_display_broadcaster)

@app.route('/api/v3/stream/logs')
def stream_logs():
    return _sse_stream(_logs_broadcaster)

# Exempt SSE streams from CSRF and apply a generous rate limit.
# SSE connections are long-lived HTTP requests, not repeated API calls, so the
# tight "20 per minute" default would be exhausted quickly on reconnects.
if csrf:
    csrf.exempt(stream_stats)
    csrf.exempt(stream_display)
    csrf.exempt(stream_logs)
    # Note: api_v3 blueprint is exempted above after registration

if limiter:
    limiter.limit("200 per minute")(stream_stats)
    limiter.limit("200 per minute")(stream_display)
    limiter.limit("200 per minute")(stream_logs)

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

_reconciliation_done = False
_reconciliation_started = False
import threading as _threading
_reconciliation_lock = _threading.Lock()

def _run_startup_reconciliation() -> None:
    """Run state reconciliation in background to auto-repair missing plugins.

    Reconciliation runs exactly once per process lifetime, regardless of
    whether every inconsistency could be auto-fixed. Previously, a failed
    auto-repair (e.g. a config entry referencing a plugin that no longer
    exists in the registry) would reset ``_reconciliation_started`` to False,
    causing the ``@app.before_request`` hook to re-trigger reconciliation on
    every single HTTP request — an infinite install-retry loop that pegged
    the CPU and flooded the log. Unresolved issues are now left in place for
    the user to address via the UI; the reconciler itself also caches
    per-plugin unrecoverable failures internally so repeated reconcile calls
    stay cheap.
    """
    global _reconciliation_done
    from src.logging_config import get_logger
    _logger = get_logger('reconciliation')

    try:
        from src.plugin_system.state_reconciliation import StateReconciliation
        reconciler = StateReconciliation(
            state_manager=plugin_state_manager,
            config_manager=config_manager,
            plugin_manager=plugin_manager,
            plugins_dir=plugins_dir,
            store_manager=plugin_store_manager
        )
        result = reconciler.reconcile_state()
        if result.inconsistencies_found:
            _logger.info("[Reconciliation] %s", result.message)
        if result.inconsistencies_fixed:
            plugin_manager.discover_plugins()
        if not result.reconciliation_successful:
            _logger.warning(
                "[Reconciliation] Finished with %d unresolved issue(s); "
                "will not retry automatically. Use the Plugin Store or the "
                "manual 'Reconcile' action to resolve.",
                len(result.inconsistencies_manual),
            )

        # Write status file so the web UI can surface unresolved issues as a
        # banner without the user having to read journalctl. Mirrors the
        # hw_status pattern (/tmp/led_matrix_hw_status.json).
        import json as _json, tempfile as _tempfile, os as _os
        _recon_status = {
            "done": True,
            "successful": result.reconciliation_successful,
            "fixed_count": len(result.inconsistencies_fixed),
            "unresolved": [
                {
                    "plugin_id": inc.plugin_id,
                    "type": inc.inconsistency_type.value,
                    "description": inc.description,
                }
                for inc in result.inconsistencies_manual
            ],
        }
        _recon_path = _os.path.join(_tempfile.gettempdir(), "ledmatrix_reconciliation.json")
        _tmp = None
        try:
            if not _os.path.islink(_recon_path):
                _fd, _tmp = _tempfile.mkstemp(dir=_tempfile.gettempdir(), prefix=".led_recon_")
                with _os.fdopen(_fd, "w") as _f:
                    _json.dump(_recon_status, _f)
                _os.replace(_tmp, _recon_path)
                _tmp = None  # Rename succeeded; nothing to clean up
        except (OSError, ValueError, TypeError) as _e:
            _logger.warning("[Reconciliation] Could not write status file: %s", _e)
        finally:
            if _tmp is not None and _os.path.exists(_tmp):
                try:
                    _os.unlink(_tmp)
                except OSError:
                    pass
    except Exception as e:
        _logger.error("[Reconciliation] Error: %s", e, exc_info=True)
    finally:
        # Always mark done — we do not want an unhandled exception (or an
        # unresolved inconsistency) to cause the @before_request hook to
        # retrigger reconciliation on every subsequent request.
        _reconciliation_done = True

# Initialize health monitor and run reconciliation on first request
@app.before_request
def check_health_monitor():
    """Ensure health monitor is initialized; launch reconciliation in background."""
    global _reconciliation_started
    if not _health_monitor_initialized:
        _initialize_health_monitor()
    with _reconciliation_lock:
        if not _reconciliation_started:
            _reconciliation_started = True
            _threading.Thread(target=_run_startup_reconciliation, daemon=True).start()

if __name__ == '__main__':
    import os as _os
    # threaded=True is Flask's default since 1.0 but stated explicitly so that
    # long-lived /api/v3/stream/* SSE connections don't starve other requests.
    # Debug mode is off by default; opt in with FLASK_DEBUG=1 in the environment.
    _debug = _os.environ.get('FLASK_DEBUG', '0') == '1'
    app.run(host='0.0.0.0', port=5000, debug=_debug, threaded=True)  # nosec B104 - intentional; local network device
