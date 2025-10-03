#!/usr/bin/env python3
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, send_file, Response
from flask_socketio import SocketIO, emit
import json
import os
import subprocess
import threading
import time
import base64
import psutil
from pathlib import Path
from src.config_manager import ConfigManager
from src.display_manager import DisplayManager
from src.cache_manager import CacheManager
from src.clock import Clock
from src.weather_manager import WeatherManager
from src.stock_manager import StockManager
from src.stock_news_manager import StockNewsManager
from src.odds_ticker_manager import OddsTickerManager
from src.calendar_manager import CalendarManager
from src.youtube_display import YouTubeDisplay
from src.text_display import TextDisplay
from src.static_image_manager import StaticImageManager
from src.news_manager import NewsManager
from werkzeug.utils import secure_filename
from src.nhl_managers import NHLLiveManager, NHLRecentManager, NHLUpcomingManager
from src.nba_managers import NBALiveManager, NBARecentManager, NBAUpcomingManager
from src.mlb_manager import MLBLiveManager, MLBRecentManager, MLBUpcomingManager
from src.milb_manager import MiLBLiveManager, MiLBRecentManager, MiLBUpcomingManager
from src.soccer_managers import SoccerLiveManager, SoccerRecentManager, SoccerUpcomingManager
from src.nfl_managers import NFLLiveManager, NFLRecentManager, NFLUpcomingManager
from src.ncaa_fb_managers import NCAAFBLiveManager, NCAAFBRecentManager, NCAAFBUpcomingManager
from src.ncaa_baseball_managers import NCAABaseballLiveManager, NCAABaseballRecentManager, NCAABaseballUpcomingManager
from src.ncaam_basketball_managers import NCAAMBasketballLiveManager, NCAAMBasketballRecentManager, NCAAMBasketballUpcomingManager
from src.ncaam_hockey_managers import NCAAMHockeyLiveManager, NCAAMHockeyRecentManager, NCAAMHockeyUpcomingManager
from PIL import Image
import io
import signal
import sys
import logging

app = Flask(__name__)
app.secret_key = os.urandom(24)

# Custom Jinja2 filter for safe nested dictionary access
@app.template_filter('safe_get')
def safe_get(obj, key_path, default=''):
    """Safely access nested dictionary values using dot notation.
    
    Usage: {{ main_config|safe_get('display.hardware.brightness', 95) }}
    """
    try:
        keys = key_path.split('.')
        current = obj
        for key in keys:
            if hasattr(current, key):
                current = getattr(current, key)
            elif isinstance(current, dict) and key in current:
                current = current[key]
            else:
                return default
        return current if current is not None else default
    except (AttributeError, KeyError, TypeError):
        return default

# Template context processor to provide safe access methods
@app.context_processor
def inject_safe_access():
    """Inject safe access methods into template context."""
    def safe_config_get(config, *keys, default=''):
        """Safely get nested config values with fallback."""
        try:
            current = config
            for key in keys:
                if hasattr(current, key):
                    current = getattr(current, key)
                    # Check if we got an empty DictWrapper
                    if isinstance(current, DictWrapper):
                        data = object.__getattribute__(current, '_data')
                        if not data:  # Empty DictWrapper means missing config
                            return default
                elif isinstance(current, dict) and key in current:
                    current = current[key]
                else:
                    return default
            
            # Final check for empty values
            if current is None or (hasattr(current, '_data') and not object.__getattribute__(current, '_data')):
                return default
            return current
        except (AttributeError, KeyError, TypeError):
            return default
    
    return dict(safe_config_get=safe_config_get)
# Prefer eventlet when available, but allow forcing threading via env for troubleshooting
force_threading = os.getenv('USE_THREADING', '0') == '1' or os.getenv('FORCE_THREADING', '0') == '1'
if force_threading:
    ASYNC_MODE = 'threading'
else:
    try:
        import eventlet  # noqa: F401
        ASYNC_MODE = 'eventlet'
    except Exception:
        ASYNC_MODE = 'threading'

socketio = SocketIO(app, cors_allowed_origins="*", async_mode=ASYNC_MODE)

# Global variables
config_manager = ConfigManager()
display_manager = None
display_thread = None
display_running = False
editor_mode = False
current_display_data = {}

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

class DictWrapper:
    """Wrapper to make dictionary accessible via dot notation for Jinja2 templates."""
    def __init__(self, data=None):
        # Store the original data
        object.__setattr__(self, '_data', data if isinstance(data, dict) else {})
        
        # Set attributes from the dictionary
        if isinstance(data, dict):
            for key, value in data.items():
                if isinstance(value, dict):
                    object.__setattr__(self, key, DictWrapper(value))
                elif isinstance(value, list):
                    object.__setattr__(self, key, value)
                else:
                    object.__setattr__(self, key, value)
    
    def __getattr__(self, name):
        # Return a new empty DictWrapper for missing attributes
        # This allows chaining like main_config.display.hardware.rows
        return DictWrapper({})
    
    def __str__(self):
        # Return empty string for missing values to avoid template errors
        data = object.__getattribute__(self, '_data')
        if not data:
            return ''
        return str(data)
    
    def __int__(self):
        # Return 0 for missing numeric values
        data = object.__getattribute__(self, '_data')
        if not data:
            return 0
        try:
            return int(data)
        except (ValueError, TypeError):
            return 0
    
    def __bool__(self):
        # Return False for missing boolean values
        data = object.__getattribute__(self, '_data')
        if not data:
            return False
        return bool(data)
    
    def __getitem__(self, key):
        # Support bracket notation
        return getattr(self, key, DictWrapper({}))
    
    def items(self):
        # Support .items() method for iteration
        data = object.__getattribute__(self, '_data')
        if data:
            return data.items()
        return {}.items()
    
    def get(self, key, default=None):
        # Support .get() method like dictionaries
        data = object.__getattribute__(self, '_data')
        if data and key in data:
            return data[key]
        return default
    
    def has_key(self, key):
        # Check if key exists
        data = object.__getattribute__(self, '_data')
        return data and key in data
    
    def keys(self):
        # Support .keys() method
        data = object.__getattribute__(self, '_data')
        return data.keys() if data else []
    
    def values(self):
        # Support .values() method
        data = object.__getattribute__(self, '_data')
        return data.values() if data else []
    
    def __str__(self):
        # Return empty string for missing values to avoid template errors
        return ''
    
    def __repr__(self):
        # Return empty string for missing values
        return ''
    
    def __html__(self):
        # Support for MarkupSafe HTML escaping
        return ''
    
    def __bool__(self):
        # Return False for empty wrappers, True if has data
        data = object.__getattribute__(self, '_data')
        return bool(data)
    
    def __len__(self):
        # Support len() function
        data = object.__getattribute__(self, '_data')
        return len(data) if data else 0

class DisplayMonitor:
    def __init__(self):
        self.running = False
        self.thread = None

    def start(self):
        if not self.running:
            self.running = True
            # Use SocketIO background task for better async compatibility
            self.thread = socketio.start_background_task(self._monitor_loop)

    def stop(self):
        self.running = False
        # Background task will exit on next loop; no join needed

    def _monitor_loop(self):
        global display_manager, current_display_data
        snapshot_path = "/tmp/led_matrix_preview.png"
        while self.running:
            try:
                # Prefer service-provided snapshot if available (works when ledmatrix service is running)
                if os.path.exists(snapshot_path):
                    # Read atomically by reopening; ignore partials by skipping this frame
                    try:
                        with open(snapshot_path, 'rb') as f:
                            img_bytes = f.read()
                    except Exception:
                        img_bytes = None

                    if img_bytes:
                        img_str = base64.b64encode(img_bytes).decode()
                        # If we can infer dimensions from display_manager, include them; else leave 0
                        width = display_manager.width if display_manager else 0
                        height = display_manager.height if display_manager else 0
                        current_display_data = {
                            'image': img_str,
                            'width': width,
                            'height': height,
                            'timestamp': time.time()
                        }
                        socketio.emit('display_update', current_display_data)
                        # Yield and continue to next frame
                        socketio.sleep(0.1)
                        continue
                    # If snapshot exists but couldn't be read (partial write/permissions), skip this frame
                    # and try again on next loop rather than emitting an invalid payload.
                elif display_manager and hasattr(display_manager, 'image'):
                    # Fallback to in-process manager image
                    img_buffer = io.BytesIO()
                    display_manager.image.save(img_buffer, format='PNG')
                    img_str = base64.b64encode(img_buffer.getvalue()).decode()
                    current_display_data = {
                        'image': img_str,
                        'width': display_manager.width,
                        'height': display_manager.height,
                        'timestamp': time.time()
                    }
                    socketio.emit('display_update', current_display_data)

            except Exception:
                # Swallow errors in the monitor loop to avoid log spam
                pass

            # Yield to the async loop; target ~5-10 FPS
            try:
                socketio.sleep(0.1)
            except Exception:
                time.sleep(0.1)

display_monitor = DisplayMonitor()


class OnDemandRunner:
    """Run a single display mode on demand until stopped."""
    def __init__(self):
        self.running = False
        self.thread = None
        self.mode = None
        self.force_clear_next = False
        self.cache_manager = None
        self.config = None

    def _ensure_infra(self):
        """Ensure config, cache, and display manager are initialized."""
        global display_manager
        if self.cache_manager is None:
            self.cache_manager = CacheManager()
        if self.config is None:
            self.config = config_manager.load_config()
        if not display_manager:
            # Initialize with hardware if possible
            try:
                # Suppress the startup test pattern to avoid random lines flash during on-demand
                display_manager = DisplayManager(self.config, suppress_test_pattern=True)
                logger.info("DisplayManager initialized successfully for on-demand")
            except Exception as e:
                logger.warning(f"Failed to initialize DisplayManager with config, using fallback: {e}")
                try:
                    display_manager = DisplayManager({'display': {'hardware': {}}}, force_fallback=True, suppress_test_pattern=True)
                    logger.info("DisplayManager initialized in fallback mode for on-demand")
                except Exception as fallback_error:
                    logger.error(f"Failed to initialize DisplayManager even in fallback mode: {fallback_error}")
                    raise RuntimeError(f"Cannot initialize display manager for on-demand: {fallback_error}")
            display_monitor.start()

    def _is_service_active(self) -> bool:
        try:
            result = subprocess.run(['systemctl', 'is-active', 'ledmatrix'], capture_output=True, text=True)
            return result.stdout.strip() == 'active'
        except Exception:
            return False

    def start(self, mode: str):
        """Start on-demand mode. Throws RuntimeError if service is active."""
        if self._is_service_active():
            raise RuntimeError('LEDMatrix service is active. Stop it first to use On-Demand.')

        # If already running same mode, no-op
        if self.running and self.mode == mode:
            logger.info(f"On-demand mode {mode} is already running")
            return
        # Switch from previous
        if self.running:
            logger.info(f"Stopping previous on-demand mode {self.mode} to start {mode}")
            self.stop()

        try:
            self._ensure_infra()
            self.mode = mode
            self.running = True
            self.force_clear_next = True
            # Use SocketIO bg task for cooperative sleeping
            self.thread = socketio.start_background_task(self._run_loop)
            logger.info(f"On-demand mode {mode} started successfully")
        except Exception as e:
            logger.error(f"Failed to start on-demand mode {mode}: {e}")
            self.running = False
            self.mode = None
            raise RuntimeError(f"Failed to start on-demand mode: {e}")

    def stop(self):
        """Stop on-demand display and clear the screen."""
        self.running = False
        self.mode = None
        self.thread = None
        
        # Clear the display to stop showing content
        global display_manager
        if display_manager:
            try:
                display_manager.clear()
                # Force update to show the cleared display
                display_manager.update_display()
            except Exception as e:
                logger.error(f"Error clearing display during on-demand stop: {e}")

    def status(self) -> dict:
        return {
            'running': self.running,
            'mode': self.mode,
        }

    # --- Mode construction helpers ---
    def _build_manager(self, mode: str):
        global display_manager
        cfg = self.config or {}
        # Non-sport managers
        if mode == 'clock':
            mgr = Clock(display_manager)
            self._force_enable(mgr)
            return mgr, lambda fc=False: mgr.display_time(force_clear=fc), None, 1.0
        if mode == 'weather_current':
            mgr = WeatherManager(cfg, display_manager)
            self._force_enable(mgr)
            return mgr, lambda fc=False: mgr.display_weather(force_clear=fc), lambda: mgr.get_weather(), float(cfg.get('weather', {}).get('update_interval', 1800))
        if mode == 'weather_hourly':
            mgr = WeatherManager(cfg, display_manager)
            self._force_enable(mgr)
            return mgr, lambda fc=False: mgr.display_hourly_forecast(force_clear=fc), lambda: mgr.get_weather(), float(cfg.get('weather', {}).get('update_interval', 1800))
        if mode == 'weather_daily':
            mgr = WeatherManager(cfg, display_manager)
            self._force_enable(mgr)
            return mgr, lambda fc=False: mgr.display_daily_forecast(force_clear=fc), lambda: mgr.get_weather(), float(cfg.get('weather', {}).get('update_interval', 1800))
        if mode == 'stocks':
            mgr = StockManager(cfg, display_manager)
            self._force_enable(mgr)
            return mgr, lambda fc=False: mgr.display_stocks(force_clear=fc), lambda: mgr.update_stock_data(), float(cfg.get('stocks', {}).get('update_interval', 600))
        if mode == 'stock_news':
            mgr = StockNewsManager(cfg, display_manager)
            self._force_enable(mgr)
            return mgr, lambda fc=False: mgr.display_news(), lambda: mgr.update_news_data(), float(cfg.get('stock_news', {}).get('update_interval', 300))
        if mode == 'odds_ticker':
            mgr = OddsTickerManager(cfg, display_manager)
            self._force_enable(mgr)
            return mgr, lambda fc=False: mgr.display(force_clear=fc), lambda: mgr.update(), float(cfg.get('odds_ticker', {}).get('update_interval', 300))
        if mode == 'calendar':
            mgr = CalendarManager(display_manager, cfg)
            self._force_enable(mgr)
            return mgr, lambda fc=False: mgr.display(force_clear=fc), lambda: mgr.update(time.time()), 60.0
        if mode == 'youtube':
            mgr = YouTubeDisplay(display_manager, cfg)
            self._force_enable(mgr)
            return mgr, lambda fc=False: mgr.display(force_clear=fc), lambda: mgr.update(), float(cfg.get('youtube', {}).get('update_interval', 30))
        if mode == 'text_display':
            mgr = TextDisplay(display_manager, cfg)
            self._force_enable(mgr)
            return mgr, lambda fc=False: mgr.display(), lambda: getattr(mgr, 'update', lambda: None)(), 5.0
        if mode == 'static_image':
            mgr = StaticImageManager(display_manager, cfg)
            self._force_enable(mgr)
            return mgr, lambda fc=False: mgr.display(force_clear=fc), lambda: mgr.update(), float(cfg.get('static_image', {}).get('display_duration', 10))
        if mode == 'of_the_day':
            from src.of_the_day_manager import OfTheDayManager  # local import to avoid circulars
            mgr = OfTheDayManager(display_manager, cfg)
            self._force_enable(mgr)
            return mgr, lambda fc=False: mgr.display(force_clear=fc), lambda: mgr.update(time.time()), 300.0
        if mode == 'news_manager':
            mgr = NewsManager(cfg, display_manager)
            self._force_enable(mgr)
            return mgr, lambda fc=False: mgr.display_news(), None, 0

        # Sports managers mapping helper
        def sport(kind: str, variant: str):
            # kind examples: nhl, nba, mlb, milb, soccer, nfl, ncaa_fb, ncaa_baseball, ncaam_basketball
            # variant: live/recent/upcoming
            if kind == 'nhl':
                cls = {'live': NHLLiveManager, 'recent': NHLRecentManager, 'upcoming': NHLUpcomingManager}[variant]
            elif kind == 'nba':
                cls = {'live': NBALiveManager, 'recent': NBARecentManager, 'upcoming': NBAUpcomingManager}[variant]
            elif kind == 'mlb':
                cls = {'live': MLBLiveManager, 'recent': MLBRecentManager, 'upcoming': MLBUpcomingManager}[variant]
            elif kind == 'milb':
                cls = {'live': MiLBLiveManager, 'recent': MiLBRecentManager, 'upcoming': MiLBUpcomingManager}[variant]
            elif kind == 'soccer':
                cls = {'live': SoccerLiveManager, 'recent': SoccerRecentManager, 'upcoming': SoccerUpcomingManager}[variant]
            elif kind == 'nfl':
                cls = {'live': NFLLiveManager, 'recent': NFLRecentManager, 'upcoming': NFLUpcomingManager}[variant]
            elif kind == 'ncaa_fb':
                cls = {'live': NCAAFBLiveManager, 'recent': NCAAFBRecentManager, 'upcoming': NCAAFBUpcomingManager}[variant]
            elif kind == 'ncaa_baseball':
                cls = {'live': NCAABaseballLiveManager, 'recent': NCAABaseballRecentManager, 'upcoming': NCAABaseballUpcomingManager}[variant]
            elif kind == 'ncaam_basketball':
                cls = {'live': NCAAMBasketballLiveManager, 'recent': NCAAMBasketballRecentManager, 'upcoming': NCAAMBasketballUpcomingManager}[variant]
            elif kind == 'ncaam_hockey':
                cls = {'live': NCAAMHockeyLiveManager, 'recent': NCAAMHockeyRecentManager, 'upcoming': NCAAMHockeyUpcomingManager}[variant]
            else:
                raise ValueError(f"Unsupported sport kind: {kind}")
            mgr = cls(cfg, display_manager, self.cache_manager)
            self._force_enable(mgr)
            return mgr, lambda fc=False: mgr.display(force_clear=fc), lambda: mgr.update(), float(getattr(mgr, 'update_interval', 60))

        if mode.endswith('_live'):
            return sport(mode.replace('_live', ''), 'live')
        if mode.endswith('_recent'):
            return sport(mode.replace('_recent', ''), 'recent')
        if mode.endswith('_upcoming'):
            return sport(mode.replace('_upcoming', ''), 'upcoming')

        raise ValueError(f"Unknown on-demand mode: {mode}")

    def _force_enable(self, mgr):
        try:
            if hasattr(mgr, 'is_enabled'):
                setattr(mgr, 'is_enabled', True)
        except Exception:
            pass

    def _run_loop(self):
        """Background loop: update and display selected mode until stopped."""
        mode = self.mode
        logger.info(f"Starting on-demand loop for mode: {mode}")
        
        try:
            manager, display_fn, update_fn, update_interval = self._build_manager(mode)
            logger.info(f"On-demand manager for {mode} initialized successfully")
        except Exception as e:
            logger.error(f"Failed to initialize on-demand manager for mode {mode}: {e}")
            self.running = False
            # Emit error to client
            try:
                socketio.emit('ondemand_error', {'mode': mode, 'error': str(e)})
            except Exception:
                pass
            return

        last_update = 0.0
        loop_count = 0
        
        while self.running and self.mode == mode:
            try:
                # Check running status more frequently
                if not self.running:
                    logger.info(f"On-demand loop for {mode} stopping - running flag is False")
                    break
                    
                if self.mode != mode:
                    logger.info(f"On-demand loop for {mode} stopping - mode changed to {self.mode}")
                    break
                
                now = time.time()
                if update_fn and (now - last_update >= max(1e-3, update_interval)):
                    update_fn()
                    last_update = now

                # Call display frequently for smooth animation where applicable
                try:
                    display_fn(self.force_clear_next)
                except TypeError:
                    # Fallback if callable ignores force_clear
                    display_fn()

                if self.force_clear_next:
                    self.force_clear_next = False
                    
                # Log every 100 loops for debugging
                loop_count += 1
                if loop_count % 100 == 0:
                    logger.debug(f"On-demand loop for {mode} - iteration {loop_count}")
                    
            except Exception as loop_err:
                logger.error(f"Error in on-demand loop for {mode}: {loop_err}")
                # Emit error to client
                try:
                    socketio.emit('ondemand_error', {'mode': mode, 'error': str(loop_err)})
                except Exception:
                    pass
                # small backoff to avoid tight error loop
                try:
                    socketio.sleep(0.5)
                except Exception:
                    time.sleep(0.5)
                continue

            # Target higher FPS for ticker; moderate for others
            sleep_seconds = 0.02 if mode == 'odds_ticker' else 0.08
            try:
                socketio.sleep(sleep_seconds)
            except Exception:
                time.sleep(sleep_seconds)
        
        logger.info(f"On-demand loop for {mode} exited")


on_demand_runner = OnDemandRunner()

@app.route('/')
def index():
    try:
        main_config = config_manager.load_config()
        schedule_config = main_config.get('schedule', {})
        
        # Get system status including CPU utilization
        system_status = get_system_status()
        
        # Get raw config data for JSON editors
        main_config_data = config_manager.get_raw_file_content('main')
        secrets_config_data = config_manager.get_raw_file_content('secrets')
        # Normalize secrets structure for template safety
        try:
            if not isinstance(secrets_config_data, dict):
                secrets_config_data = {}
            if 'weather' not in secrets_config_data or not isinstance(secrets_config_data['weather'], dict):
                secrets_config_data['weather'] = {}
            if 'api_key' not in secrets_config_data['weather']:
                secrets_config_data['weather']['api_key'] = ''
        except Exception:
            secrets_config_data = {'weather': {'api_key': ''}}
        main_config_json = json.dumps(main_config_data, indent=4)
        secrets_config_json = json.dumps(secrets_config_data, indent=4)
        
        return render_template('index_v2.html', 
                             schedule_config=schedule_config,
                             main_config=DictWrapper(main_config),
                             main_config_data=main_config_data,
                             secrets_config=secrets_config_data,
                             main_config_json=main_config_json,
                             secrets_config_json=secrets_config_json,
                             main_config_path=config_manager.get_config_path(),
                             secrets_config_path=config_manager.get_secrets_path(),
                             system_status=system_status,
                             editor_mode=editor_mode)
                             
    except Exception as e:
        # Return a minimal, valid response to avoid template errors when keys are missing
        logger.error(f"Error loading configuration on index: {e}", exc_info=True)
        safe_system_status = get_system_status()
        safe_secrets = {'weather': {'api_key': ''}}
        return render_template('index_v2.html',
                               schedule_config={},
                               main_config=DictWrapper({}),
                               main_config_data={},
                               secrets_config=safe_secrets,
                               main_config_json="{}",
                               secrets_config_json="{}",
                               main_config_path="",
                               secrets_config_path="",
                               system_status=safe_system_status,
                               editor_mode=False)

def get_system_status():
    """Get current system status including display state, performance metrics, and CPU utilization."""
    try:
        # Check if display service is running
        result = subprocess.run(['systemctl', 'is-active', 'ledmatrix'], 
                              capture_output=True, text=True)
        service_active = result.stdout.strip() == 'active'
        
        # Get memory usage using psutil for better accuracy
        memory = psutil.virtual_memory()
        mem_used_percent = round(memory.percent, 1)
        
        # Get CPU utilization (non-blocking to avoid stalling the event loop)
        cpu_percent = round(psutil.cpu_percent(interval=None), 1)
        
        # Get CPU temperature
        try:
            with open('/sys/class/thermal/thermal_zone0/temp', 'r') as f:
                temp = int(f.read().strip()) / 1000
        except:
            temp = 0
            
        # Get uptime
        with open('/proc/uptime', 'r') as f:
            uptime_seconds = float(f.read().split()[0])
        
        uptime_hours = int(uptime_seconds // 3600)
        uptime_minutes = int((uptime_seconds % 3600) // 60)
        
        # Get disk usage
        disk = psutil.disk_usage('/')
        disk_used_percent = round((disk.used / disk.total) * 100, 1)
        
        status = {
            'service_active': service_active,
            'memory_used_percent': mem_used_percent,
            'cpu_percent': cpu_percent,
            'cpu_temp': round(temp, 1),
            'disk_used_percent': disk_used_percent,
            'uptime': f"{uptime_hours}h {uptime_minutes}m",
            'display_connected': display_manager is not None,
            'editor_mode': editor_mode,
            'on_demand': on_demand_runner.status()
        }
        return status
    except Exception as e:
        return {
            'service_active': False,
            'memory_used_percent': 0,
            'cpu_percent': 0,
            'cpu_temp': 0,
            'disk_used_percent': 0,
            'uptime': '0h 0m',
            'display_connected': False,
            'editor_mode': False,
            'error': str(e)
        }

@app.route('/api/display/start', methods=['POST'])
def start_display():
    """Start the LED matrix display."""
    global display_manager, display_running
    
    try:
        if not display_manager:
            config = config_manager.load_config()
            try:
                display_manager = DisplayManager(config)
                logger.info("DisplayManager initialized successfully")
            except Exception as dm_error:
                logger.error(f"Failed to initialize DisplayManager: {dm_error}")
                # Re-attempt with explicit fallback mode for web preview
                display_manager = DisplayManager({'display': {'hardware': {}}}, force_fallback=True)
                logger.info("Using fallback DisplayManager for web simulation")
            
            display_monitor.start()
            # Immediately publish a snapshot for the client
            try:
                img_buffer = io.BytesIO()
                display_manager.image.save(img_buffer, format='PNG')
                img_str = base64.b64encode(img_buffer.getvalue()).decode()
                snapshot = {
                    'image': img_str,
                    'width': display_manager.width,
                    'height': display_manager.height,
                    'timestamp': time.time()
                }
                # Update global and notify clients
                global current_display_data
                current_display_data = snapshot
                socketio.emit('display_update', snapshot)
            except Exception as snap_err:
                logger.error(f"Failed to publish initial snapshot: {snap_err}")
            
        display_running = True
        
        return jsonify({
            'status': 'success',
            'message': 'Display started successfully',
            'dimensions': {
                'width': getattr(display_manager, 'width', 0),
                'height': getattr(display_manager, 'height', 0)
            },
            'fallback': display_manager.matrix is None
        })
    except Exception as e:
        logger.error(f"Error in start_display: {e}", exc_info=True)
        return jsonify({
            'status': 'error',
            'message': f'Error starting display: {e}'
        }), 500

@app.route('/api/display/stop', methods=['POST'])
def stop_display():
    """Stop the LED matrix display."""
    global display_manager, display_running
    
    try:
        display_running = False
        display_monitor.stop()
        
        if display_manager:
            display_manager.clear()
            display_manager.cleanup()
            display_manager = None
            
        return jsonify({
            'status': 'success',
            'message': 'Display stopped successfully'
        })
    except Exception as e:
        return jsonify({
            'status': 'error',
            'message': f'Error stopping display: {e}'
        }), 500

@app.route('/api/editor/toggle', methods=['POST'])
def toggle_editor_mode():
    """Toggle display editor mode."""
    global editor_mode, display_running, display_manager
    
    try:
        editor_mode = not editor_mode
        
        if editor_mode:
            # Stop normal display operation
            display_running = False
            # Initialize display manager for editor if needed
            if not display_manager:
                config = config_manager.load_config()
                try:
                    display_manager = DisplayManager(config)
                    logger.info("DisplayManager initialized for editor mode")
                except Exception as dm_error:
                    logger.error(f"Failed to initialize DisplayManager for editor: {dm_error}")
                    # Create a fallback display manager for web simulation
                    display_manager = DisplayManager(config, force_fallback=True)
                    logger.info("Using fallback DisplayManager for editor simulation")
                display_monitor.start()
        else:
            # Resume normal display operation
            display_running = True
            
        return jsonify({
            'status': 'success',
            'editor_mode': editor_mode,
            'message': f'Editor mode {"enabled" if editor_mode else "disabled"}'
        })
    except Exception as e:
        logger.error(f"Error toggling editor mode: {e}", exc_info=True)
        return jsonify({
            'status': 'error',
            'message': f'Error toggling editor mode: {e}'
        }), 500

@app.route('/api/editor/preview', methods=['POST'])
def preview_display():
    """Preview display with custom layout."""
    global display_manager
    
    try:
        if not display_manager:
            return jsonify({
                'status': 'error',
                'message': 'Display not initialized'
            }), 400
            
        layout_data = request.get_json()
        
        # Clear display
        display_manager.clear()
        
        # Render preview based on layout data
        for element in layout_data.get('elements', []):
            render_element(display_manager, element)
            
        display_manager.update_display()
        
        return jsonify({
            'status': 'success',
            'message': 'Preview updated'
        })
    except Exception as e:
        return jsonify({
            'status': 'error',
            'message': f'Error updating preview: {e}'
        }), 500

def render_element(display_manager, element):
    """Render a single display element."""
    element_type = element.get('type')
    x = element.get('x', 0)
    y = element.get('y', 0)
    
    if element_type == 'text':
        text = element.get('text', 'Sample Text')
        color = tuple(element.get('color', [255, 255, 255]))
        font_size = element.get('font_size', 'normal')
        
        font = display_manager.small_font if font_size == 'small' else display_manager.regular_font
        display_manager.draw_text(text, x, y, color, font=font)
        
    elif element_type == 'weather_icon':
        condition = element.get('condition', 'sunny')
        size = element.get('size', 16)
        display_manager.draw_weather_icon(condition, x, y, size)
        
    elif element_type == 'rectangle':
        width = element.get('width', 10)
        height = element.get('height', 10)
        color = tuple(element.get('color', [255, 255, 255]))
        display_manager.draw.rectangle([x, y, x + width, y + height], outline=color)
        
    elif element_type == 'line':
        x2 = element.get('x2', x + 10)
        y2 = element.get('y2', y)
        color = tuple(element.get('color', [255, 255, 255]))
        display_manager.draw.line([x, y, x2, y2], fill=color)

@app.route('/api/config/save', methods=['POST'])
def save_config():
    """Save configuration changes."""
    try:
        data = request.get_json()
        config_type = data.get('type', 'main')
        config_data = data.get('data', {})
        
        if config_type == 'main':
            current_config = config_manager.load_config()
            # Deep merge the changes
            merge_dict(current_config, config_data)
            config_manager.save_config(current_config)
        elif config_type == 'layout':
            # Save custom layout configuration
            with open('config/custom_layouts.json', 'w') as f:
                json.dump(config_data, f, indent=2)
                
        return jsonify({
            'status': 'success',
            'message': 'Configuration saved successfully'
        })
    except Exception as e:
        return jsonify({
            'status': 'error',
            'message': f'Error saving configuration: {e}'
        }), 500

def merge_dict(target, source):
    """Deep merge source dict into target dict."""
    for key, value in source.items():
        if key in target and isinstance(target[key], dict) and isinstance(value, dict):
            merge_dict(target[key], value)
        else:
            target[key] = value

@app.route('/api/system/action', methods=['POST'])
def system_action():
    """Execute system actions like restart, update, etc."""
    try:
        data = request.get_json()
        action = data.get('action')
        
        if action == 'restart_service':
            result = subprocess.run(['sudo', '-n', 'systemctl', 'restart', 'ledmatrix'], 
                                  capture_output=True, text=True)
        elif action == 'stop_service':
            result = subprocess.run(['sudo', '-n', 'systemctl', 'stop', 'ledmatrix'], 
                                  capture_output=True, text=True)
        elif action == 'start_service':
            result = subprocess.run(['sudo', '-n', 'systemctl', 'start', 'ledmatrix'], 
                                  capture_output=True, text=True)
        elif action == 'reboot_system':
            result = subprocess.run(['sudo', '-n', 'reboot'], 
                                  capture_output=True, text=True)
        elif action == 'shutdown_system':
            result = subprocess.run(['sudo', '-n', 'poweroff'], 
                                  capture_output=True, text=True)
        elif action == 'git_pull':
            # Run git pull from the repository directory where this file lives
            repo_dir = Path(__file__).resolve().parent
            if not (repo_dir / '.git').exists():
                return jsonify({
                    'status': 'error',
                    'message': f'Not a git repository: {repo_dir}'
                }), 400
            result = subprocess.run(['git', 'pull'],
                                   capture_output=True, text=True, cwd=str(repo_dir), check=False)
        elif action == 'migrate_config':
            # Run config migration script
            repo_dir = Path(__file__).resolve().parent
            migrate_script = repo_dir / 'migrate_config.sh'
            if not migrate_script.exists():
                return jsonify({
                    'status': 'error',
                    'message': f'Migration script not found: {migrate_script}'
                }), 400
            
            result = subprocess.run(['bash', str(migrate_script)], 
                                  cwd=str(repo_dir), capture_output=True, text=True, check=False)
        else:
            return jsonify({
                'status': 'error',
                'message': f'Unknown action: {action}'
            }), 400
            
        return jsonify({
            'status': 'success' if result.returncode == 0 else 'error',
            'message': f'Action {action} completed',
            'output': result.stdout,
            'error': result.stderr
        })
        
    except Exception as e:
        return jsonify({
            'status': 'error',
            'message': f'Error executing action: {e}. If this action requires sudo, ensure NOPASSWD is configured or run the command manually.'
        }), 500

@app.route('/api/system/status')
def get_system_status_api():
    """Get system status as JSON."""
    try:
        return jsonify(get_system_status())
    except Exception as e:
        # Ensure a valid JSON response is always produced
        return jsonify({'status': 'error', 'message': str(e)}), 500

# --- On-Demand Controls ---
@app.route('/api/ondemand/start', methods=['POST'])
def api_ondemand_start():
    try:
        data = request.get_json(force=True)
        mode = (data or {}).get('mode')
        if not mode:
            return jsonify({'status': 'error', 'message': 'Missing mode'}), 400
        
        # Validate mode format
        if not isinstance(mode, str) or not mode.strip():
            return jsonify({'status': 'error', 'message': 'Invalid mode format'}), 400
            
        # Refuse if service is running
        if on_demand_runner._is_service_active():
            return jsonify({'status': 'error', 'message': 'Service is active. Stop it first to use On-Demand.'}), 400
        
        logger.info(f"Starting on-demand mode: {mode}")
        on_demand_runner.start(mode)
        return jsonify({'status': 'success', 'message': f'On-Demand started: {mode}', 'on_demand': on_demand_runner.status()})
    except RuntimeError as rte:
        logger.error(f"Runtime error starting on-demand {mode}: {rte}")
        return jsonify({'status': 'error', 'message': str(rte)}), 400
    except Exception as e:
        logger.error(f"Unexpected error starting on-demand {mode}: {e}")
        return jsonify({'status': 'error', 'message': f'Error starting on-demand: {e}'}), 500

@app.route('/api/ondemand/stop', methods=['POST'])
def api_ondemand_stop():
    try:
        logger.info("Stopping on-demand display...")
        on_demand_runner.stop()
        
        # Give the thread a moment to stop
        import time
        time.sleep(0.1)
        
        status = on_demand_runner.status()
        logger.info(f"On-demand stopped. Status: {status}")
        
        return jsonify({'status': 'success', 'message': 'On-Demand stopped', 'on_demand': status})
    except Exception as e:
        logger.error(f"Error stopping on-demand: {e}")
        return jsonify({'status': 'error', 'message': f'Error stopping on-demand: {e}'}), 500

@app.route('/api/ondemand/status', methods=['GET'])
def api_ondemand_status():
    try:
        status = on_demand_runner.status()
        logger.debug(f"On-demand status requested: {status}")
        return jsonify({'status': 'success', 'on_demand': status})
    except Exception as e:
        logger.error(f"Error getting on-demand status: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500

# --- API Call Metrics (simple in-memory counters) ---
api_counters = {
    'weather': {'used': 0},
    'stocks': {'used': 0},
    'sports': {'used': 0},
    'news': {'used': 0},
    'odds': {'used': 0},
    'music': {'used': 0},
    'youtube': {'used': 0},
}
api_window_start = time.time()
api_window_seconds = 24 * 3600

def increment_api_counter(kind: str, count: int = 1):
    global api_window_start
    now = time.time()
    if now - api_window_start > api_window_seconds:
        # Reset window
        api_window_start = now
        for v in api_counters.values():
            v['used'] = 0
    if kind in api_counters:
        api_counters[kind]['used'] = api_counters[kind].get('used', 0) + count

@app.route('/api/metrics')
def get_metrics():
    """Expose lightweight API usage counters and simple forecasts based on config."""
    try:
        config = config_manager.load_config()
        forecast = {}
        # Weather forecasted calls per 24h
        try:
            w_int = int(config.get('weather', {}).get('update_interval', 1800))
            forecast['weather'] = max(1, int(api_window_seconds / max(1, w_int)))
        except Exception:
            forecast['weather'] = 0
        # Stocks
        try:
            s_int = int(config.get('stocks', {}).get('update_interval', 600))
            forecast['stocks'] = max(1, int(api_window_seconds / max(1, s_int)))
        except Exception:
            forecast['stocks'] = 0
        # Sports (aggregate of enabled leagues using their recent update intervals)
        sports_leagues = [
            ('nhl_scoreboard','recent_update_interval'),
            ('nba_scoreboard','recent_update_interval'),
            ('mlb','recent_update_interval'),
            ('milb','recent_update_interval'),
            ('soccer_scoreboard','recent_update_interval'),
            ('nfl_scoreboard','recent_update_interval'),
            ('ncaa_fb_scoreboard','recent_update_interval'),
            ('ncaa_baseball_scoreboard','recent_update_interval'),
            ('ncaam_basketball_scoreboard','recent_update_interval'),
        ]
        sports_calls = 0
        for key, interval_key in sports_leagues:
            sec = config.get(key, {})
            if sec.get('enabled', False):
                ival = int(sec.get(interval_key, 3600))
                sports_calls += max(1, int(api_window_seconds / max(1, ival)))
        forecast['sports'] = sports_calls

        # News manager
        try:
            n_int = int(config.get('news_manager', {}).get('update_interval', 300))
            forecast['news'] = max(1, int(api_window_seconds / max(1, n_int)))
        except Exception:
            forecast['news'] = 0

        # Odds ticker
        try:
            o_int = int(config.get('odds_ticker', {}).get('update_interval', 3600))
            forecast['odds'] = max(1, int(api_window_seconds / max(1, o_int)))
        except Exception:
            forecast['odds'] = 0

        # Music manager (image downloads)
        try:
            m_int = int(config.get('music', {}).get('POLLING_INTERVAL_SECONDS', 5))
            forecast['music'] = max(1, int(api_window_seconds / max(1, m_int)))
        except Exception:
            forecast['music'] = 0

        # YouTube display
        try:
            y_int = int(config.get('youtube', {}).get('update_interval', 300))
            forecast['youtube'] = max(1, int(api_window_seconds / max(1, y_int)))
        except Exception:
            forecast['youtube'] = 0

        return jsonify({
            'status': 'success',
            'window_seconds': api_window_seconds,
            'since': api_window_start,
            'forecast': forecast,
            'used': {k: v.get('used', 0) for k, v in api_counters.items()}
        })
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

# Add all the routes from the original web interface for compatibility
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
                # Add all the missing LED Matrix hardware options
                main_config['display']['hardware']['scan_mode'] = int(request.form.get('scan_mode', 0))
                main_config['display']['hardware']['pwm_bits'] = int(request.form.get('pwm_bits', 9))
                main_config['display']['hardware']['pwm_dither_bits'] = int(request.form.get('pwm_dither_bits', 1))
                main_config['display']['hardware']['pwm_lsb_nanoseconds'] = int(request.form.get('pwm_lsb_nanoseconds', 130))
                main_config['display']['hardware']['disable_hardware_pulsing'] = 'disable_hardware_pulsing' in request.form
                main_config['display']['hardware']['inverse_colors'] = 'inverse_colors' in request.form
                main_config['display']['hardware']['show_refresh_rate'] = 'show_refresh_rate' in request.form
                main_config['display']['hardware']['limit_refresh_rate_hz'] = int(request.form.get('limit_refresh_rate_hz', 120))
                main_config['display']['use_short_date_format'] = 'use_short_date_format' in request.form
            
            # If config_data is provided as JSON, merge it
            if config_data_str:
                try:
                    new_data = json.loads(config_data_str)
                    # Merge the new data with existing config
                    for key, value in new_data.items():
                        if key in main_config:
                            if isinstance(value, dict) and isinstance(main_config[key], dict):
                                merge_dict(main_config[key], value)
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
            result = subprocess.run(['sudo', '-n', 'systemctl', 'start', 'ledmatrix'], 
                                 capture_output=True, text=True)
        elif action == 'stop_display':
            result = subprocess.run(['sudo', '-n', 'systemctl', 'stop', 'ledmatrix'], 
                                 capture_output=True, text=True)
        elif action == 'enable_autostart':
            result = subprocess.run(['sudo', '-n', 'systemctl', 'enable', 'ledmatrix'], 
                                 capture_output=True, text=True)
        elif action == 'disable_autostart':
            result = subprocess.run(['sudo', '-n', 'systemctl', 'disable', 'ledmatrix'], 
                                 capture_output=True, text=True)
        elif action == 'reboot_system':
            result = subprocess.run(['sudo', '-n', 'reboot'], 
                                 capture_output=True, text=True)
        elif action == 'shutdown_system':
            result = subprocess.run(['sudo', '-n', 'poweroff'], 
                                  capture_output=True, text=True)
        elif action == 'git_pull':
            repo_dir = Path(__file__).resolve().parent
            if not (repo_dir / '.git').exists():
                return jsonify({
                    'status': 'error',
                    'message': f'Not a git repository: {repo_dir}'
                }), 400
            result = subprocess.run(['git', 'pull'],
                                 capture_output=True, text=True, cwd=str(repo_dir), check=False)
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
        # Prefer journalctl logs for ledmatrix; apply a timeout to avoid UI hangs
        journal_cmd = ['journalctl', '-u', 'ledmatrix.service', '-n', '500', '--no-pager', '--output=cat']
        try:
            result = subprocess.run(journal_cmd, capture_output=True, text=True, check=False, timeout=5)
            if result.returncode == 0:
                return jsonify({'status': 'success', 'logs': result.stdout})
            # Try sudo fallback (in case group membership hasn't applied yet)
            sudo_result = subprocess.run(['sudo', '-n'] + journal_cmd, capture_output=True, text=True, check=False, timeout=5)
            if sudo_result.returncode == 0:
                return jsonify({'status': 'success', 'logs': sudo_result.stdout})
            error_msg = result.stderr or sudo_result.stderr or 'permission denied'
        except subprocess.TimeoutExpired:
            error_msg = 'journalctl timed out'

        # Permission denied or other error: fall back to web UI log and return hint
        fallback_logs = ''
        try:
            with open('/tmp/web_interface_v2.log', 'r') as f:
                fallback_logs = f.read()
        except Exception:
            fallback_logs = '(No fallback web UI logs found)'
        hint = 'Insufficient permissions or timeout reading system journal. Ensure the web user is in the systemd-journal group, restart the service to pick up group changes, or configure sudoers for journalctl.'
        return jsonify({'status': 'error', 'message': f'Error fetching logs: {error_msg}\n\nHint: {hint}', 'fallback': fallback_logs}), 500
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

# Add news manager routes for compatibility
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

@app.route('/logs')
def view_logs():
    """View system logs."""
    try:
        result = subprocess.run(
            ['journalctl', '-u', 'ledmatrix.service', '-n', '500', '--no-pager'],
            capture_output=True, text=True, check=False
        )
        logs = result.stdout if result.returncode == 0 else ''
        if result.returncode != 0:
            try:
                with open('/tmp/web_interface_v2.log', 'r') as f:
                    logs = f.read()
            except Exception:
                logs = 'Insufficient permissions to read journal. Add user to systemd-journal or configure sudoers for journalctl.'
        
        # Return logs as HTML page
        return f"""
        <!DOCTYPE html>
        <html>
        <head>
            <title>System Logs</title>
            <style>
                body {{ font-family: monospace; background: #1e1e1e; color: #fff; padding: 20px; }}
                .log-container {{ background: #2d2d2d; padding: 20px; border-radius: 8px; }}
                .log-line {{ margin: 2px 0; }}
                .error {{ color: #ff6b6b; }}
                .warning {{ color: #feca57; }}
                .info {{ color: #48dbfb; }}
            </style>
        </head>
        <body>
            <h1>LED Matrix Service Logs</h1>
            <div class="log-container">
                <pre>{logs}</pre>
            </div>
            <script>
                // Auto-scroll to bottom
                window.scrollTo(0, document.body.scrollHeight);
            </script>
        </body>
        </html>
        """
    except subprocess.CalledProcessError as e:
        return f"Error fetching logs: {e.stderr}", 500
    except Exception as e:
        return f"Error: {str(e)}", 500

@app.route('/api/display/current')
def get_current_display():
    """Get current display image as base64."""
    try:
        # Get display dimensions from config if not available in current_display_data
        if not current_display_data or not current_display_data.get('width') or not current_display_data.get('height'):
            try:
                config = config_manager.load_config()
                display_config = config.get('display', {}).get('hardware', {})
                rows = display_config.get('rows', 32)
                cols = display_config.get('cols', 64)
                chain_length = display_config.get('chain_length', 1)
                parallel = display_config.get('parallel', 1)
                
                # Calculate total display dimensions
                total_width = cols * chain_length
                total_height = rows * parallel
                
                # Update current_display_data with config dimensions if missing
                if not current_display_data:
                    current_display_data = {}
                if not current_display_data.get('width'):
                    current_display_data['width'] = total_width
                if not current_display_data.get('height'):
                    current_display_data['height'] = total_height
            except Exception as config_error:
                # Fallback to default dimensions if config fails
                if not current_display_data:
                    current_display_data = {}
                if not current_display_data.get('width'):
                    current_display_data['width'] = 128
                if not current_display_data.get('height'):
                    current_display_data['height'] = 32
        
        return jsonify(current_display_data)
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e), 'image': None}), 500

@app.route('/upload_image', methods=['POST'])
def upload_image():
    """Upload an image for static image display."""
    try:
        if 'image' not in request.files:
            return jsonify({'success': False, 'error': 'No image file provided'})
        
        file = request.files['image']
        if file.filename == '':
            return jsonify({'success': False, 'error': 'No image file selected'})
        
        if file:
            # Secure the filename
            filename = secure_filename(file.filename)
            # Ensure we have a valid extension
            if not filename.lower().endswith(('.png', '.jpg', '.jpeg', '.gif', '.bmp')):
                return jsonify({'success': False, 'error': 'Invalid file type. Only image files are allowed.'})
            
            # Create the static images directory if it doesn't exist
            static_images_dir = os.path.join(os.path.dirname(__file__), 'assets', 'static_images')
            os.makedirs(static_images_dir, exist_ok=True)
            
            # Save the file
            file_path = os.path.join(static_images_dir, filename)
            file.save(file_path)
            
            # Return the relative path for the config
            relative_path = f"assets/static_images/{filename}"
            
            logger.info(f"Image uploaded successfully: {relative_path}")
            return jsonify({'success': True, 'path': relative_path})
        
    except Exception as e:
        logger.error(f"Error uploading image: {e}")
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/editor/layouts', methods=['GET'])
def get_custom_layouts():
    """Return saved custom layouts for the editor if available."""
    try:
        layouts_path = Path('config') / 'custom_layouts.json'
        if not layouts_path.exists():
            return jsonify({'status': 'success', 'data': {'elements': []}})
        with open(layouts_path, 'r') as f:
            data = json.load(f)
        return jsonify({'status': 'success', 'data': data})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@socketio.on('connect')
def handle_connect():
    """Handle client connection."""
    try:
        emit('connected', {'status': 'Connected to LED Matrix Interface'})
    except Exception:
        # If emit failed before a response started, just return
        return
    # Send current display state immediately after connect
    try:
        if display_manager and hasattr(display_manager, 'image'):
            img_buffer = io.BytesIO()
            display_manager.image.save(img_buffer, format='PNG')
            img_str = base64.b64encode(img_buffer.getvalue()).decode()
            payload = {
                'image': img_str,
                'width': display_manager.width,
                'height': display_manager.height,
                'timestamp': time.time()
            }
            emit('display_update', payload)
        elif current_display_data:
            emit('display_update', current_display_data)
    except Exception as e:
        logger.error(f"Error sending initial display_update on connect: {e}")

@socketio.on('disconnect')
def handle_disconnect():
    """Handle client disconnection."""
    print('Client disconnected')

def signal_handler(sig, frame):
    """Handle shutdown signals."""
    print('Shutting down web interface...')
    display_monitor.stop()
    if display_manager:
        display_manager.cleanup()
    sys.exit(0)

if __name__ == '__main__':
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    # Start the display monitor (runs even if display is not started yet for web preview)
    display_monitor.start()
    
    # Run the app
    # In threading mode this uses Werkzeug; allow it explicitly for systemd usage
    # Use eventlet server when available; fall back to Werkzeug in threading mode
    logger.info(f"Starting web interface on http://0.0.0.0:5001 (async_mode={ASYNC_MODE})")
    # When running without eventlet/gevent, Flask-SocketIO uses Werkzeug, which now
    # enforces a production guard unless explicitly allowed. Enable it here.
    socketio.run(
        app,
        host='0.0.0.0',
        port=5001,
        debug=False,
        use_reloader=False
    )