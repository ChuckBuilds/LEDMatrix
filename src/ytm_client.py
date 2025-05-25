import logging
import json
import os
import time
import threading
import requests

# Ensure application-level logging is configured (as it is)
# logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Reduce verbosity of socketio and engineio libraries
logging.getLogger('socketio.client').setLevel(logging.WARNING)
logging.getLogger('socketio.server').setLevel(logging.WARNING)
logging.getLogger('engineio.client').setLevel(logging.WARNING)
logging.getLogger('engineio.server').setLevel(logging.WARNING)
logging.getLogger('src.ytm_client').setLevel(logging.DEBUG)

# Define paths relative to this file's location
CONFIG_DIR = os.path.join(os.path.dirname(__file__), '..', 'config')
CONFIG_PATH = os.path.join(CONFIG_DIR, 'config.json')
# Resolve to an absolute path
CONFIG_PATH = os.path.abspath(CONFIG_PATH)

# Path for the separate YTM authentication token file
YTM_AUTH_CONFIG_PATH = os.path.join(CONFIG_DIR, 'ytm_auth.json')
YTM_AUTH_CONFIG_PATH = os.path.abspath(YTM_AUTH_CONFIG_PATH)

class YTMClient:
    def __init__(self, update_callback=None):
        self.base_url = None
        self.ytm_token = None
        self.load_config() # Loads URL and token
        self.session = requests.Session()
        self.last_known_track_data = None
        self._data_lock = threading.Lock()
        self.external_update_callback = update_callback
        self.polling_thread = None
        self.stop_polling_event = threading.Event()
        self.last_auth_failure = False # Added to track auth failures

        if self.external_update_callback and self.ytm_token:
            self.start_polling()

    def load_config(self):
        default_url = "http://localhost:9863"
        self.base_url = default_url # Start with default
        
        # Load base_url from main config.json
        if not os.path.exists(CONFIG_PATH):
            logging.warning(f"Main config file not found at {CONFIG_PATH}. Using default YTM URL: {self.base_url}")
        else:
            try:
                with open(CONFIG_PATH, 'r') as f:
                    loaded_config = json.load(f)
                    music_config = loaded_config.get("music", {})
                    self.base_url = music_config.get("YTM_COMPANION_URL", default_url)
                    if not self.base_url:
                        logging.warning("YTM_COMPANION_URL missing or empty in config.json music section, using default.")
                        self.base_url = default_url
            except json.JSONDecodeError:
                logging.error(f"Error decoding JSON from main config {CONFIG_PATH}. Using default YTM URL.")
            except Exception as e:
                logging.error(f"Error loading YTM_COMPANION_URL from main config {CONFIG_PATH}: {e}. Using default YTM URL.")

        logging.info(f"YTM Companion URL set to: {self.base_url}")

        if self.base_url and self.base_url.startswith("ws://"):
            self.base_url = "http://" + self.base_url[5:]
        elif self.base_url and self.base_url.startswith("wss://"):
            self.base_url = "https://" + self.base_url[6:]

        # Load ytm_token from ytm_auth.json
        self.ytm_token = None # Reset token before trying to load
        if os.path.exists(YTM_AUTH_CONFIG_PATH):
            try:
                with open(YTM_AUTH_CONFIG_PATH, 'r') as f:
                    auth_data = json.load(f)
                    self.ytm_token = auth_data.get("YTM_COMPANION_TOKEN")
                if self.ytm_token:
                    logging.info(f"YTM Companion token loaded from {YTM_AUTH_CONFIG_PATH}.")
                else:
                    logging.warning(f"YTM_COMPANION_TOKEN not found in {YTM_AUTH_CONFIG_PATH}. YTM features will be disabled until token is present.")
            except json.JSONDecodeError:
                logging.error(f"Error decoding JSON from YTM auth file {YTM_AUTH_CONFIG_PATH}. YTM features will be disabled.")
            except Exception as e:
                logging.error(f"Error loading YTM auth config {YTM_AUTH_CONFIG_PATH}: {e}. YTM features will be disabled.")
        else:
            logging.warning(f"YTM auth file not found at {YTM_AUTH_CONFIG_PATH}. Run the authentication script to generate it. YTM features will be disabled.")

    def _ensure_connected(self, timeout=5):
        """Checks if the server is reachable and token is likely valid."""
        if not self.ytm_token:
            self.last_auth_failure = False # No token, so not an auth failure in the sense of an invalid token
            return False
        
        # This method now effectively becomes part of is_available or a health check.
        # For REST, "connected" is ephemeral per request. We check reachability.
        try:
            state_url = f"{self.base_url}/api/v1/state" # A common endpoint to check
            headers = {"Authorization": f"Bearer {self.ytm_token}"}
            # Use a short timeout for this check
            response = self.session.get(state_url, headers=headers, timeout=timeout)
            if response.status_code == 200:
                self.last_auth_failure = False
                return True
            elif response.status_code == 401:
                logging.warning(f"YTM Companion: Authentication failed (401) during connectivity check. Token may be invalid.")
                self.last_auth_failure = True
                return False # Treat as not 'connected' for practical purposes
            else:
                logging.warning(f"YTM Companion: Server check returned status {response.status_code}.")
                self.last_auth_failure = False
                return False
        except requests.exceptions.RequestException as e:
            logging.warning(f"YTM Companion: Could not connect to server at {self.base_url} for connectivity check: {e}")
            self.last_auth_failure = False
            return False

    def is_available(self):
        """Checks if the YTM service is available (server reachable and token works)."""
        return self._ensure_connected(timeout=5) # Use a reasonable timeout for availability check

    def get_current_track(self):
        if not self.ytm_token:
            logging.debug("No YTM token, cannot get current track.")
            self.last_auth_failure = False # No token, so not an auth failure
            return None

        state_url = f"{self.base_url}/api/v1/state"
        headers = {"Authorization": f"Bearer {self.ytm_token}"}

        try:
            response = self.session.get(state_url, headers=headers, timeout=5) # Using self.session
            if response.status_code == 200:
                data = response.json()
                self.last_auth_failure = False # Successful call
                with self._data_lock:
                    # Update last_known_track_data even if it's the same,
                    # as the external_update_callback logic (when polling)
                    # will handle the "changed" logic.
                    # However, for direct get_current_track calls,
                    # it's good to update it here.
                    self.last_known_track_data = data 
                return data
            elif response.status_code == 401:
                logging.error(f"Authentication failed (401) when trying to get YTM state. Check YTM_COMPANION_TOKEN.")
                self.last_auth_failure = True
                return None
            else:
                logging.error(f"Error getting YTM state: {response.status_code} - {response.text}")
                self.last_auth_failure = False
                return None
        except requests.exceptions.RequestException as e:
            logging.error(f"RequestException while getting YTM state: {e}")
            self.last_auth_failure = False
            return None
        except json.JSONDecodeError as e:
            logging.error(f"JSONDecodeError while parsing YTM state: {e}")
            self.last_auth_failure = False
            return None

    def disconnect_client(self):
        self.stop_polling()
        logging.info("YTM client polling stopped and session closed.")
        self.session.close() # Close the session when disconnecting

    def _poll_for_updates(self, interval=5):
        """Polls the YTM state endpoint and calls the update_callback if data changes."""
        logging.info("YTM Polling thread started.")
        while not self.stop_polling_event.is_set():
            current_data = self.get_current_track() # This already uses the REST API

            new_data_received = False
            # Check if data actually changed before calling callback
            with self._data_lock:
                if self.last_known_track_data != current_data: # current_data can be None
                    self.last_known_track_data = current_data # Update with potentially new data
                    new_data_received = True
            
            if new_data_received and self.external_update_callback:
                if current_data is not None: # Only callback if there's actual data
                    try:
                        self.external_update_callback(current_data)
                    except Exception as cb_ex:
                        logging.error(f"Error executing YTMClient external_update_callback: {cb_ex}")
                # else:
                    # Optionally, could call callback with None if that's desired behavior
                    # logging.debug("Polling: No track data or error, not calling update_callback.")

            time.sleep(interval)
        logging.info("YTM Polling thread finished.")

    def start_polling(self, interval=5):
        if not self.external_update_callback:
            logging.warning("No update_callback provided, polling will not start.")
            return
        if not self.ytm_token:
            logging.warning("No YTM token, polling will not start. Run authentication script.")
            return

        if self.polling_thread is None or not self.polling_thread.is_alive():
            self.stop_polling_event.clear()
            self.polling_thread = threading.Thread(target=self._poll_for_updates, args=(interval,), daemon=True)
            self.polling_thread.start()
        else:
            logging.info("Polling thread already running.")

    def stop_polling(self):
        if self.polling_thread and self.polling_thread.is_alive():
            logging.info("Stopping YTM polling thread...")
            self.stop_polling_event.set()
            self.polling_thread.join(timeout=10) # Wait for thread to finish
            if self.polling_thread.is_alive():
                logging.warning("YTM polling thread did not stop in time.")
            self.polling_thread = None
        # else:
            # logging.debug("Polling thread not running or already stopped.")

    def is_experiencing_auth_failure(self) -> bool:
        """Returns True if the last connection attempt failed due to a 401 auth error."""
        return self.last_auth_failure

# Example Usage (for testing - needs to be adapted for Socket.IO async nature)
# if __name__ == '__main__':
#     def my_callback(data):
#         print("Callback received new data:")
#         print(json.dumps(data, indent=2))

#     # Create client with the callback
#     client = YTMClient(update_callback=my_callback)

#     if client.is_available():
#         print(f"YTM Server is available via REST API at {client.base_url}.")
        
#         # Polling is started automatically if callback is provided and token exists.
#         # Let it run for a bit to demonstrate polling.
#         print("Polling for YTM updates for 20 seconds...")
#         try:
#             time.sleep(20) # Keep the main thread alive to see polling
#         except KeyboardInterrupt:
#             print("Interrupted by user.")
#         finally:
#             print("Disconnecting client...")
#             client.disconnect_client()
#             print("Client disconnected.")

#     else:
#         print(f"YTM Server not available at {client.base_url} (REST API). Is YTMD running with companion server enabled and token generated?")
#         if client.is_experiencing_auth_failure():
#             print("This unavailability is due to an authentication failure (401).")

#     # Example of a direct call (polling will also be active if callback was set)
#     print("\nAttempting a direct get_current_track call:")
#     track_info = client.get_current_track()
#     if track_info:
#         print(json.dumps(track_info, indent=2))
#     else:
#         print("No track currently playing or error fetching via REST API.")
    
#     if not client.external_update_callback: # If no callback, disconnect is simpler
#        client.disconnect_client() # Ensure session is closed if no polling was started
    
#     if __name__ == '__main__':
#         client = YTMClient()
#         if client.is_available(): 
#             print("YTM Server is available (Socket.IO).")
#             try:
#                 for _ in range(10): # Poll for a few seconds
#                     track = client.get_current_track()
#                     if track:
#                         print(json.dumps(track, indent=2))
#                     else:
#                         print("No track currently playing or error fetching (Socket.IO).")
#                     time.sleep(2)
#             finally:
#                 client.disconnect_client()
#         else:
#             print(f"YTM Server not available at {client.base_url} (Socket.IO). Is YTMD running with companion server enabled and token generated?") 