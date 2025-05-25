import socketio
import logging
import json
import os
import time
import threading
import requests # Added for get_current_track

# Set up logger for this module specifically
logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)
# If you want to ensure handlers are added if run standalone or if main app doesn't configure root logger well:
if not logger.hasHandlers():
    handler = logging.StreamHandler()
    formatter = logging.Formatter('%(asctime)s - %(levelname)s:%(name)s:%(message)s')
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    logger.propagate = False # Prevent duplicate logs if root logger also has a handler

# Reduce verbosity of socketio and engineio libraries
logging.getLogger('socketio.client').setLevel(logging.WARNING)
logging.getLogger('socketio.server').setLevel(logging.WARNING)
logging.getLogger('engineio.client').setLevel(logging.WARNING)
logging.getLogger('engineio.server').setLevel(logging.WARNING)

# Define paths relative to this file's location
CONFIG_DIR = os.path.join(os.path.dirname(__file__), '..', 'config')
CONFIG_PATH = os.path.join(CONFIG_DIR, 'config.json')
# Resolve to an absolute path
CONFIG_PATH = os.path.abspath(CONFIG_PATH)

# Path for the separate YTM auth token
YTM_AUTH_PATH = os.path.join(CONFIG_DIR, 'ytm_auth.json')
YTM_AUTH_PATH = os.path.abspath(YTM_AUTH_PATH)


class YTMClient:
    def __init__(self, base_url=None, token=None, update_callback=None, auto_start=True): # Added auto_start
        self.base_url = base_url
        self.token = token
        self.sio = socketio.Client(reconnection_attempts=5, reconnection_delay=5)
        self.update_callback = update_callback
        self.is_connected_event = threading.Event()
        self.stop_event = threading.Event()
        self.connection_thread = None
        self.headers = {}
        logger.debug("YTMClient __init__ called.")
        self._load_config_and_token() # Load URL and token
        self._setup_event_handlers()

        if auto_start: # Conditionally start connection thread
            self.start_client_listening()

    def _load_config_and_token(self):
        logger.debug(f"YTMClient: _load_config_and_token started. Initial self.base_url: '{self.base_url}'")
        logger.debug(f"YTMClient: Attempting to load config from: {CONFIG_PATH}")
        
        loaded_url_from_config = None
        try:
            if os.path.exists(CONFIG_PATH):
                logger.debug(f"YTMClient: Config file exists at {CONFIG_PATH}")
                with open(CONFIG_PATH, 'r') as f:
                    app_config = json.load(f)
                    logger.debug(f"YTMClient: app_config loaded: {app_config is not None}")
                    music_section = app_config.get('music', {})
                    logger.debug(f"YTMClient: music_section: {music_section}")
                    # Directly get the URL from the music_section, as YTM_COMPANION_URL is not nested further
                    loaded_url_from_config = music_section.get('YTM_COMPANION_URL')
                    logger.debug(f"YTMClient: Value for YTM_COMPANION_URL directly from music_section: '{loaded_url_from_config}'")
            else:
                logger.warning(f"YTMClient: Config file NOT FOUND at {CONFIG_PATH}")
        except json.JSONDecodeError as e:
            logger.error(f"YTMClient: JSONDecodeError reading {CONFIG_PATH}: {e}")
        except Exception as e:
            logger.error(f"YTMClient: Exception reading {CONFIG_PATH}: {e}", exc_info=True)

        # Logic to set self.base_url using the potentially pre-set self.base_url or the loaded one
        if self.base_url: # If base_url was provided to __init__ and is not None/empty
            logger.debug(f"YTMClient: Using pre-existing self.base_url: '{self.base_url}'")
        elif loaded_url_from_config:
            logger.debug(f"YTMClient: Using loaded_url_from_config: '{loaded_url_from_config}'")
            self.base_url = loaded_url_from_config
        else:
            logger.debug("YTMClient: No pre-existing base_url and no URL loaded from config.")
            # self.base_url remains None or its initial value from __init__ which should be None if not passed

        logger.info(f"YTM Companion URL set to: {self.base_url}")

        if not self.base_url:
            logger.error("YTM Companion URL is not set. YTMClient cannot function.")

        try:
            if os.path.exists(YTM_AUTH_PATH):
                with open(YTM_AUTH_PATH, 'r') as f:
                    auth_data = json.load(f)
                    logger.debug(f"YTMClient: Loaded auth_data from {YTM_AUTH_PATH}: {auth_data}") # Log the loaded auth_data
                    # Prioritize explicit token if provided, else use file
                    self.token = self.token or auth_data.get('token')
                if self.token:
                    self.headers['Authorization'] = f'Bearer {self.token}'
                    logger.info(f"YTM Companion token loaded from {YTM_AUTH_PATH}.")
                else:
                    logger.warning(f"Token not found in {YTM_AUTH_PATH}.")
            else:
                logger.warning(f"YTM auth token file not found at {YTM_AUTH_PATH}. Token not loaded.")
        except Exception as e:
            logger.error(f"Error loading YTM auth token from {YTM_AUTH_PATH}: {e}")
        
        if not self.token:
            logger.warning("YTM Authentication token not available. YTM features requiring auth may fail.")


    def _setup_event_handlers(self):
        @self.sio.event
        def connect():
            logger.info(f"Successfully connected to YTM Companion Socket.IO server at {self.base_url} on namespace /api/v1/realtime")
            self.is_connected_event.set()

        @self.sio.event
        def connect_error(data):
            logger.error(f"YTM Socket.IO connection error to {self.base_url}: {data}")
            self.is_connected_event.clear() # Ensure it's clear on error

        @self.sio.event
        def disconnect():
            logger.info(f"Disconnected from YTM Companion Socket.IO server at {self.base_url} on namespace /api/v1/realtime")
            self.is_connected_event.clear()
        
        # Handler for 'state' event from YTM Companion's Socket.IO server
        @self.sio.on('state', namespace='/api/v1/realtime')
        def _on_state_change(data):
            # This is where YTM Companion pushes player state updates.
            # logger.debug(f"YTMClient received 'state' update (raw): {data}") # Very verbose
            if self.update_callback:
                try:
                    self.update_callback(data) # Pass the raw data to MusicManager
                except Exception as e:
                    logger.error(f"Error in YTMClient update_callback: {e}")

    def _connect_socketio(self):
        if not self.base_url:
            logger.error("Cannot connect: YTM Companion URL not set.")
            return False
        if self.sio.connected:
            logger.debug("Socket.IO already connected.")
            return True
        try:
            logger.info(f"Attempting to connect to YTM Socket.IO server: {self.base_url} on namespace /api/v1/realtime")
            self.sio.connect(self.base_url, namespaces=['/api/v1/realtime'], auth={'token': self.token} if self.token else None)
            return True
        except socketio.exceptions.ConnectionError as e:
            logger.error(f"Socket.IO connection failed to {self.base_url}: {e}")
            self.is_connected_event.clear()
            return False
        except Exception as e:
            logger.error(f"Unexpected error during Socket.IO connection to {self.base_url}: {e}")
            self.is_connected_event.clear()
            return False

    def disconnect_socketio(self):
        if self.sio.connected:
            logger.info("YTMClient: Explicitly disconnecting Socket.IO.")
            self.sio.disconnect()
        self.is_connected_event.clear()

    def _connection_loop(self):
        retry_delay = 5 # Initial retry delay
        max_retry_delay = 60 # Maximum retry delay
        
        while not self.stop_event.is_set():
            if not self.sio.connected:
                logger.info(f"YTMClient connection loop: Not connected. Attempting to connect.")
                if self._connect_socketio():
                    # Successfully connected, wait for disconnect or stop signal
                    self.is_connected_event.wait() # Wait until disconnected
                    retry_delay = 5 # Reset retry delay on successful connect then disconnect
                else:
                    # Connection failed
                    logger.info(f"YTMClient connection loop: Connection attempt failed. Retrying in {retry_delay}s.")
                    time.sleep(retry_delay)
                    retry_delay = min(retry_delay * 2, max_retry_delay) # Exponential backoff
            else:
                # Already connected, just wait for a bit or for stop_event
                # This makes the loop responsive to stop_event even when connected.
                self.stop_event.wait(timeout=1) # Check stop_event periodically
        
        logger.info("YTMClient connection loop stopped.")
        # Ensure disconnected when loop exits
        if self.sio.connected:
            self.disconnect_socketio()

    def start_client_listening(self): # Renamed from _start_connection_thread
        if self.connection_thread and self.connection_thread.is_alive():
            logger.info("YTMClient connection thread already running.")
            # If stop_event was set, clear it to allow reconnection attempts
            if self.stop_event.is_set():
                logger.info("YTMClient: Clearing stop_event to resume connection attempts.")
                self.stop_event.clear()
            return

        self.stop_event.clear() # Clear stop event before starting
        self.connection_thread = threading.Thread(target=self._connection_loop, daemon=True)
        self.connection_thread.start()
        logger.info("YTMClient connection_thread started.")

    def stop_client(self):
        logger.info("YTMClient: Stopping client and connection thread...")
        self.stop_event.set()
        self.disconnect_socketio() # Attempt immediate disconnect
        if self.connection_thread and self.connection_thread.is_alive():
            self.connection_thread.join(timeout=5) # Wait for the thread to stop
            if self.connection_thread.is_alive():
                logger.warning("YTMClient connection thread did not stop in time.")
        logger.info("YTMClient stopped.")
        self.connection_thread = None


    def is_available(self):
        """
        Checks basic connectivity to the YTM Companion server using a simple HTTP GET.
        This is a quick check and doesn't guarantee full Socket.IO functionality or auth.
        """
        if not self.base_url:
            return False
        try:
            # Use a different endpoint for a quick check, like /api/v1/player
            # Requires authentication for /api/v1/player, /api/v1/track, etc.
            # A simple health check endpoint on YTM Companion that doesn't require auth would be better.
            # For now, let's try /api/v1/info which might not require auth.
            # If YTM Companion requires auth for all /api/v1/*, this check needs self.headers.
            # The original version used a specific check for connectivity.
            # This method is mostly to see if the server *exists* at the URL.
            
            # Let's try to check if the socket is connected as a primary means of availability
            if self.sio and self.sio.connected:
                return True

            # Fallback to HTTP check if socket not connected (e.g. before first connection)
            # This is tricky because most YTM Companion endpoints require auth.
            # A dedicated unauthenticated health endpoint on YTM Companion is ideal.
            # For now, we'll assume if base_url is set, we *could* connect.
            # A more robust check might try to connect the socket if not connected.

            # Let's use the /api/v1/info endpoint that YTM Desktop provides (unauthenticated)
            # Or a specific health check if available on the companion.
            # Assuming YTM Companion might have a similar info endpoint or a root page.
            # For now, the socket connection status is the most reliable.
            # This method can be enhanced if YTM companion has a specific health check.
            
            # Simplified: if base_url exists, assume it *could* be available.
            # The connection loop handles actual connection attempts.
            # MusicManager will use this, then attempt ytm.connect() or rely on auto-connect.
            
            # Connectivity check based on trying to get /api/v1/info (usually doesn't need token)
            response = requests.get(f"{self.base_url}/api/v1/info", timeout=2)
            if response.status_code == 200:
                 logger.debug("YTM Companion server reachable via /api/v1/info.")
                 return True
            else:
                 logger.warning(f"YTM Companion server check to /api/v1/info failed with status {response.status_code}.")
                 return False
        except requests.exceptions.RequestException as e:
            logger.warning(f"YTM Companion server not reachable at {self.base_url} during HTTP check: {e}")
            return False
        return bool(self.base_url) # Minimal check if base_url is set


    def get_current_track(self):
        """
        Fetches the current track info using a REST GET request.
        This is used for polling when Socket.IO might not be preferred or as a fallback.
        """
        if not self.base_url:
            logger.warning("Cannot get current track: Base URL not set.")
            return None
        if not self.is_available(): # Use the general availability check
             logger.warning("YTM Companion not available, skipping get_current_track.")
             return None
        try:
            # This request requires authentication
            if 'Authorization' not in self.headers:
                logger.warning("Cannot get current track: YTM auth token not available in headers.")
                # Attempt to reload token if not present
                self._load_config_and_token()
                if 'Authorization' not in self.headers:
                    logger.error("Failed to load YTM auth token. Cannot fetch track.")
                    return None

            response = requests.get(f"{self.base_url}/api/v1/track", headers=self.headers, timeout=3)
            if response.status_code == 200:
                track_data = response.json()
                # logger.debug(f"YTMClient get_current_track (REST): {track_data}")
                return track_data
            elif response.status_code == 401:
                logger.warning(f"YTM Companion: Authentication failed (401) for /api/v1/track. Token may be invalid.")
                # Potentially trigger re-authentication or notify user.
            elif response.status_code == 204: # No content - often means nothing playing or YTM desktop not focused
                 logger.debug("YTM Companion /api/v1/track returned 204 No Content (likely nothing playing or player not active).")
                 return None # Treat as nothing playing
            else:
                logger.error(f"Error getting current track from YTM Companion: {response.status_code} - {response.text}")
            return None
        except requests.exceptions.RequestException as e:
            logger.error(f"RequestException getting current track from YTM Companion: {e}")
            return None
        except Exception as e:
            logger.error(f"Unexpected error in get_current_track: {e}")
            return None

# Example of how it might be used (for testing or if YTMClient is run directly)
if __name__ == '__main__':
    logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s:%(name)s:%(message)s')
    
    def my_callback(data):
        # In a real scenario, this callback would be in MusicManager
        logger.info(f"MAIN_CALLBACK received data: Title: {data.get('video',{}).get('title')}, Playing: {not data.get('player',{}).get('isPaused')}")

    # Create client but don't auto-start connection thread
    ytm_client = YTMClient(update_callback=my_callback, auto_start=False)
    
    if not ytm_client.base_url or not ytm_client.token:
        logger.error("YTM Client could not be initialized with URL and Token. Exiting test.")
    else:
        logger.info("YTM Client initialized. Manually starting listener...")
        ytm_client.start_client_listening() # Manually start the connection

        logger.info("Client started. Listening for updates for 30 seconds or until Ctrl+C...")
        try:
            # Keep the main thread alive to observe callbacks
            # In a real app, YTMClient runs in its own thread via start_client_listening()
            # and MusicManager would integrate it.
            
            # Test get_current_track via REST polling
            for i in range(3):
                if not ytm_client.stop_event.is_set():
                    logger.info(f"Polling for track (attempt {i+1})...")
                    track = ytm_client.get_current_track()
                    if track:
                        my_callback(track) # Simulate processing
                    else:
                        logger.info("No track info from polling.")
                    time.sleep(5)
                else:
                    break

            if not ytm_client.stop_event.is_set():
                 logger.info("Continuing to listen for Socket.IO updates for another 15 seconds...")
                 time.sleep(15)


        except KeyboardInterrupt:
            logger.info("Keyboard interrupt received.")
        finally:
            logger.info("Stopping YTM client...")
            ytm_client.stop_client()
            logger.info("YTM client test finished.") 