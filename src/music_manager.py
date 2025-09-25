import time
import threading
from enum import Enum, auto
import logging
import json
import os
from io import BytesIO
import requests
from typing import Union, Dict, Any, Optional
from PIL import Image, ImageEnhance
import queue # Added import

# Use relative imports for clients within the same package (src)
from .spotify_client import SpotifyClient
from .ytm_client import YTMClient
# Removed: import config

# Import the API counter function from web interface
try:
    from web_interface_v2 import increment_api_counter
except ImportError:
    # Fallback if web interface is not available
    def increment_api_counter(kind: str, count: int = 1):
        pass

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Note: Config is now passed in from DisplayController instead of being loaded separately

class MusicSource(Enum):
    NONE = auto()
    SPOTIFY = auto()
    YTM = auto()

class MusicManager:
    def __init__(self, display_manager, config, update_callback=None):
        self.display_manager = display_manager
        self.config = config
        self.spotify = None
        self.ytm = None
        self.current_track_info = None
        self.current_source = MusicSource.NONE
        self.update_callback = update_callback
        self.polling_interval = 2 # Default
        self.enabled = False # Default
        self.preferred_source = "spotify" # Default changed from "auto"
        self.stop_event = threading.Event()
        self.track_info_lock = threading.Lock() # Added lock

        # Display related attributes moved from DisplayController
        self.album_art_image = None
        self.last_album_art_url = None
        self.scroll_position_title = 0
        self.scroll_position_artist = 0
        self.scroll_position_album = 0
        self.title_scroll_tick = 0
        
        # Track update logging throttling
        self.last_track_log_time = 0
        self.last_logged_track_title = None
        self.track_log_interval = 5.0  # Log track updates every 5 seconds max
        self.artist_scroll_tick = 0
        self.album_scroll_tick = 0
        self.is_music_display_active = False # New state variable
        self.is_currently_showing_nothing_playing = False # To prevent flashing
        self._needs_immediate_full_refresh = False # Flag for forcing refresh from YTM updates
        self.ytm_event_data_queue = queue.Queue(maxsize=1) # Queue for event data
        
        self._load_config() # Load config first
        self._initialize_clients() # Initialize based on loaded config
        self.poll_thread = None

    def _load_config(self):
        default_interval = 2
        self.enabled = False # Assume disabled until config proves otherwise

        # Use the config that was already loaded and passed to us instead of loading our own
        if self.config is None:
            logging.warning("No config provided to MusicManager. Music manager disabled.")
            return

        try:
            music_config = self.config.get("music", {})

            self.enabled = music_config.get("enabled", False)
            if not self.enabled:
                logging.info("Music manager is disabled in config.json (top level 'enabled': false).")
                return # Don't proceed further if disabled

            self.polling_interval = music_config.get("POLLING_INTERVAL_SECONDS", default_interval)
            configured_source = music_config.get("preferred_source", "spotify").lower()

            if configured_source in ["spotify", "ytm"]:
                self.preferred_source = configured_source
                logging.info(f"Music manager enabled. Polling interval: {self.polling_interval}s. Preferred source: {self.preferred_source}")
            else:
                logging.warning(f"Invalid 'preferred_source' ('{configured_source}') in config.json. Must be 'spotify' or 'ytm'. Music manager disabled.")
                self.enabled = False
                return

        except Exception as e:
            logging.error(f"Error loading music config: {e}. Music manager disabled.")
            self.enabled = False

    def _initialize_clients(self):
        # Only initialize if the manager is enabled
        if not self.enabled:
            self.spotify = None
            self.ytm = None
            return

        logging.info("Initializing music clients...")

        # Initialize Spotify Client if needed
        if self.preferred_source == "spotify":
            try:
                self.spotify = SpotifyClient()
                if not self.spotify.is_authenticated():
                    logging.warning("Spotify client initialized but not authenticated. Please run src/authenticate_spotify.py if you want to use Spotify.")
                else:
                    logging.info("Spotify client authenticated.")
            except Exception as e:
                logging.error(f"Failed to initialize Spotify client: {e}")
                self.spotify = None
        else:
            self.spotify = None # Ensure it's None if not preferred

        # Initialize YTM Client if needed
        if self.preferred_source == "ytm":
            try:
                self.ytm = YTMClient(update_callback=self._handle_ytm_direct_update)
                logging.info(f"YTMClient initialized. Connection will be managed on-demand. Configured URL: {self.ytm.base_url}")
            except Exception as e:
                logging.error(f"Failed to initialize YTM client: {e}")
                self.ytm = None
        else:
            self.ytm = None # Ensure it's None if not preferred

    def _process_ytm_data_update(self, ytm_data, source_description: str):
        """
        Core processing logic for YTM data.
        Updates self.current_track_info, handles album art, queues data for display,
        and determines if the update is significant.

        Args:
            ytm_data: The raw data from YTM.
            source_description: A string for logging (e.g., "YTM Event", "YTM Activate Sync").

        Returns:
            tuple: (simplified_info, significant_change_detected)
        """
        if not ytm_data: # Handle case where ytm_data might be None
            simplified_info = self.get_simplified_track_info(None, MusicSource.NONE)
        else:
            ytm_player_info = ytm_data.get('player', {})
            is_actually_playing_ytm = (ytm_player_info.get('trackState') == 1) and not ytm_player_info.get('adPlaying', False)
            simplified_info = self.get_simplified_track_info(ytm_data if is_actually_playing_ytm else None,
                                                           MusicSource.YTM if is_actually_playing_ytm else MusicSource.NONE)

        significant_change_detected = False
        processed_a_meaningful_update = False # Renamed from has_changed

        with self.track_info_lock:
            current_track_info_before_update_str = json.dumps(self.current_track_info) if self.current_track_info else "None"
            simplified_info_str = json.dumps(simplified_info)
            logger.debug(f"MusicManager._process_ytm_data_update ({source_description}): PRE-COMPARE - SimplifiedInfo: {simplified_info_str}, CurrentTrackInfo: {current_track_info_before_update_str}")

            if self.current_track_info is None and simplified_info.get('title') != 'Nothing Playing':
                significant_change_detected = True
                logger.debug(f"({source_description}): First valid track data, marking as significant.")
            elif self.current_track_info is not None and (
                simplified_info.get('title') != self.current_track_info.get('title') or
                simplified_info.get('artist') != self.current_track_info.get('artist') or
                simplified_info.get('album_art_url') != self.current_track_info.get('album_art_url') or
                simplified_info.get('is_playing') != self.current_track_info.get('is_playing')
            ):
                significant_change_detected = True
                logger.debug(f"({source_description}): Significant change (title/artist/art/is_playing) detected.")

            if simplified_info != self.current_track_info:
                processed_a_meaningful_update = True
                old_album_art_url = self.current_track_info.get('album_art_url') if self.current_track_info else None
                
                self.current_track_info = simplified_info # Update main state
                logger.debug(f"MusicManager._process_ytm_data_update ({source_description}): POST-UPDATE (inside lock) - self.current_track_info now: {json.dumps(self.current_track_info)}")

                # Determine current source based on this update
                if simplified_info.get('source') == 'YouTube Music' and simplified_info.get('is_playing'):
                    self.current_source = MusicSource.YTM
                elif self.current_source == MusicSource.YTM and not simplified_info.get('is_playing'): # YTM stopped
                    self.current_source = MusicSource.NONE
                elif simplified_info.get('source') == 'None':
                    self.current_source = MusicSource.NONE
                
                new_album_art_url = simplified_info.get('album_art_url')

                logger.debug(f"({source_description}) Track info comparison: simplified_info != self.current_track_info was TRUE.")
                logger.debug(f"({source_description}) Old Album Art URL: {old_album_art_url}, New Album Art URL: {new_album_art_url}")

                if new_album_art_url != old_album_art_url:
                    logger.info(f"({source_description}) Album art URL changed. Clearing self.album_art_image to force re-fetch.")
                    self.album_art_image = None # Clear cached image
                    self.last_album_art_url = new_album_art_url # Update last known URL
                elif not self.last_album_art_url and new_album_art_url: # New art URL appeared
                    logger.info(f"({source_description}) New album art URL appeared. Clearing image.")
                    self.album_art_image = None
                    self.last_album_art_url = new_album_art_url
                elif new_album_art_url is None and old_album_art_url is not None: # Art URL disappeared
                    logger.info(f"({source_description}) Album art URL disappeared. Clearing image and URL.")
                    self.album_art_image = None
                    self.last_album_art_url = None
                elif self.current_track_info and self.current_track_info.get('album_art_url') and not self.last_album_art_url:
                    # This case might be redundant if new_album_art_url logic covers it
                    self.last_album_art_url = self.current_track_info.get('album_art_url')
                    self.album_art_image = None

                display_title = self.current_track_info.get('title', 'None')
                
                # Throttle track update logging to reduce spam
                current_time = time.time()
                should_log = False
                
                # Log if track title changed or if enough time has passed
                if (display_title != self.last_logged_track_title or 
                    current_time - self.last_track_log_time >= self.track_log_interval):
                    should_log = True
                    self.last_track_log_time = current_time
                    self.last_logged_track_title = display_title
                
                if should_log:
                    logger.info(f"({source_description}) Track info updated. Source: {self.current_source.name}. New Track: {display_title}")
                else:
                    logger.debug(f"({source_description}) Track info updated (throttled). Source: {self.current_source.name}. Track: {display_title}")
            else:
                # simplified_info IS THE SAME as self.current_track_info
                processed_a_meaningful_update = False
                logger.debug(f"({source_description}) No change in simplified track info (simplified_info == self.current_track_info).")
                if self.current_track_info is None and simplified_info.get('title') != 'Nothing Playing':
                    # This ensures that if current_track_info was None and simplified_info is valid,
                    # it's treated as processed and current_track_info gets set.
                    significant_change_detected = True # First load is always significant
                    processed_a_meaningful_update = True
                    self.current_track_info = simplified_info
                    # Also log first valid track data with throttling
                    display_title = simplified_info.get('title', 'None')
                    current_time = time.time()
                    
                    # For first valid data, always log but update throttling variables
                    logger.info(f"({source_description}) First valid track data received (was None), marking significant. Track: {display_title}")
                    self.last_track_log_time = current_time
                    self.last_logged_track_title = display_title

        # Queueing logic - for events or activate_display syncs, not for polling.
        # Polling updates current_track_info directly; display() picks it up.
        # Events and activate_display syncs use queue to ensure display() picks up event-specific data.
        if source_description in ["YTM Event", "YTM Activate Sync"]:
            try:
                while not self.ytm_event_data_queue.empty():
                    self.ytm_event_data_queue.get_nowait()
                self.ytm_event_data_queue.put_nowait(simplified_info)
                logger.debug(f"MusicManager._process_ytm_data_update ({source_description}): Put simplified_info (Title: {simplified_info.get('title')}) into ytm_event_data_queue.")
            except queue.Full:
                logger.warning(f"MusicManager._process_ytm_data_update ({source_description}): ytm_event_data_queue was full.")

        if significant_change_detected:
            logger.info(f"({source_description}) Significant track change detected. Signaling for an immediate full refresh of MusicManager display.")
            self._needs_immediate_full_refresh = True
        elif processed_a_meaningful_update : # A change occurred but wasn't "significant" (e.g. just progress)
            logger.debug(f"({source_description}) Minor track data update (e.g. progress). Display will update without full refresh.")
            # _needs_immediate_full_refresh remains False or as it was.
            # If an event put data on queue, display() will still pick it up.

        return simplified_info, significant_change_detected

    def activate_music_display(self):
        logger.info("Music display activated.")
        self.is_music_display_active = True
        if self.ytm and self.preferred_source == "ytm":
            if not self.ytm.is_connected:
                logger.info("Attempting to connect YTM client due to music display activation.")
                if self.ytm.connect_client(timeout=10):
                    logger.info("YTM client connected successfully on display activation.")
                    # YTM often sends an immediate state update on connect, handled by _handle_ytm_direct_update.
                    # If not, or to be sure, we can fetch current state.
                    latest_data = self.ytm.get_current_track()
                    if latest_data:
                        logger.debug("YTM Activate Sync: Processing current track data after successful connection.")
                        self._process_ytm_data_update(latest_data, "YTM Activate Sync")
                        # Callback to DisplayController will be handled by the display loop picking up queue/flag
                else:
                    logger.warning("YTM client failed to connect on display activation.")
            else: # Already connected
                logger.debug("YTM client already connected during music display activation. Syncing state.")
                latest_data = self.ytm.get_current_track() # Get latest from YTMClient's cache
                if latest_data:
                    self._process_ytm_data_update(latest_data, "YTM Activate Sync")
                    # Callback to DisplayController will be handled by the display loop picking up queue/flag
                else:
                    logger.debug("YTM Activate Sync: No track data available from connected YTM client.")
                    # Process "Nothing Playing" to ensure state is clean if YTM has nothing.
                    self._process_ytm_data_update(None, "YTM Activate Sync (No Data)")


    def deactivate_music_display(self):
        logger.info("Music display deactivated.")
        self.is_music_display_active = False
        if self.ytm and self.ytm.is_connected:
            logger.info("Disconnecting YTM client due to music display deactivation.")
            self.ytm.disconnect_client()

    def _handle_ytm_direct_update(self, ytm_data):
        """Handles a direct state update from YTMClient."""
        raw_title_from_event = ytm_data.get('video', {}).get('title', 'No Title') if isinstance(ytm_data, dict) else 'Data not a dict'
        logger.debug(f"MusicManager._handle_ytm_direct_update: RAW EVENT DATA - Title: '{raw_title_from_event}'")

        if not self.enabled or not self.is_music_display_active:
            logger.debug("Skipping YTM direct update: Manager disabled or music display not active.")
            return

        if self.preferred_source != "ytm":
            logger.debug(f"Skipping YTM direct update: Preferred source is '{self.preferred_source}', not 'ytm'.")
            return
        
        # Process the data and get outcomes
        simplified_info, significant_change = self._process_ytm_data_update(ytm_data, "YTM Event")

        # Callback to DisplayController
        if self.update_callback:
            try:
                self.update_callback(simplified_info, significant_change) 
            except Exception as e:
                logger.error(f"Error executing DisplayController update callback from YTM direct update: {e}")

    def _fetch_and_resize_image(self, url: str, target_size: tuple) -> Union[Image.Image, None]:
        """Fetches an image from a URL, resizes it, and returns a PIL Image object."""
        if not url:
            return None
        try:
            response = requests.get(url, timeout=5) # 5-second timeout for image download
            response.raise_for_status() # Raise an exception for bad status codes
            
            # Increment API counter for music data
            increment_api_counter('music', 1)
            
            img_data = BytesIO(response.content)
            img = Image.open(img_data)
            
            # Ensure image is RGB for compatibility with the matrix
            img = img.convert("RGB") 
            
            img.thumbnail(target_size, Image.Resampling.LANCZOS)

            # Enhance contrast
            enhancer_contrast = ImageEnhance.Contrast(img)
            img = enhancer_contrast.enhance(1.3) # Adjust 1.3 as needed

            # Enhance saturation (Color)
            enhancer_saturation = ImageEnhance.Color(img)
            img = enhancer_saturation.enhance(1.3) # Adjust 1.3 as needed
            
            final_img = Image.new("RGB", target_size, (0,0,0)) # Black background
            paste_x = (target_size[0] - img.width) // 2
            paste_y = (target_size[1] - img.height) // 2
            final_img.paste(img, (paste_x, paste_y))
            
            return final_img
        except requests.exceptions.RequestException as e:
            logger.error(f"Error fetching image from {url}: {e}")
            return None
        except IOError as e:
            logger.error(f"Error processing image from {url}: {e}")
            return None
        except Exception as e:
            logger.error(f"Unexpected error fetching/processing image {url}: {e}")
            return None

    def _poll_music_data(self):
        """Continuously polls music sources for updates, respecting preferences."""
        if not self.enabled:
             logging.warning("Polling attempted while music manager is disabled. Stopping polling thread.")
             return

        while not self.stop_event.is_set():
            polled_track_info_data = None
            source_for_callback = MusicSource.NONE # Used to determine if callback is needed
            significant_change_for_callback = False
            simplified_info_for_callback = None

            if self.preferred_source == "spotify" and self.spotify and self.spotify.is_authenticated():
                try:
                    spotify_track = self.spotify.get_current_track()
                    if spotify_track and spotify_track.get('is_playing'):
                        polled_track_info_data = spotify_track
                        source_for_callback = MusicSource.SPOTIFY
                        simplified_info_poll = self.get_simplified_track_info(polled_track_info_data, MusicSource.SPOTIFY)
                        
                        with self.track_info_lock:
                            if simplified_info_poll != self.current_track_info:
                                # Check for significant changes (same logic as YTM)
                                significant_change_detected = False
                                if self.current_track_info is None and simplified_info_poll.get('title') != 'Nothing Playing':
                                    significant_change_detected = True
                                    logger.debug("Polling Spotify: First valid track data, marking as significant.")
                                elif self.current_track_info is not None and (
                                    simplified_info_poll.get('title') != self.current_track_info.get('title') or
                                    simplified_info_poll.get('artist') != self.current_track_info.get('artist') or
                                    simplified_info_poll.get('album_art_url') != self.current_track_info.get('album_art_url') or
                                    simplified_info_poll.get('is_playing') != self.current_track_info.get('is_playing')
                                ):
                                    significant_change_detected = True
                                    logger.debug("Polling Spotify: Significant change (title/artist/art/is_playing) detected.")
                                else:
                                    logger.debug("Polling Spotify: Only progress changed, not significant.")
                                
                                self.current_track_info = simplified_info_poll
                                self.current_source = MusicSource.SPOTIFY
                                significant_change_for_callback = significant_change_detected
                                simplified_info_for_callback = simplified_info_poll.copy()
                                
                                if significant_change_detected:
                                    self._needs_immediate_full_refresh = True # Reset display state
                                    logger.info("Polling Spotify: Significant change detected.")
                                else:
                                    logger.debug("Polling Spotify: Minor update (progress only), no full refresh needed.")
                                
                                # Handle album art for Spotify if needed (similar to _process_ytm_data_update)
                                old_album_art_url = self.current_track_info.get('album_art_url_prev_spotify') # Need a way to store prev
                                new_album_art_url = simplified_info_poll.get('album_art_url')
                                if new_album_art_url != old_album_art_url:
                                     self.album_art_image = None
                                     self.last_album_art_url = new_album_art_url
                                self.current_track_info['album_art_url_prev_spotify'] = new_album_art_url

                                logger.debug(f"Polling Spotify: Active track - {spotify_track.get('item', {}).get('name')}")
                            else:
                                logger.debug("Polling Spotify: No change in simplified track info.")
                        
                    else:
                        logger.debug("Polling Spotify: No active track or player paused.")
                        # If Spotify was playing and now it's not
                        with self.track_info_lock:
                            if self.current_source == MusicSource.SPOTIFY:
                                simplified_info_for_callback = self.get_simplified_track_info(None, MusicSource.NONE)
                                self.current_track_info = simplified_info_for_callback
                                self.current_source = MusicSource.NONE
                                significant_change_for_callback = True
                                self._needs_immediate_full_refresh = True # Reset display state
                                self.album_art_image = None # Clear art
                                self.last_album_art_url = None
                                logger.info("Polling Spotify: Player stopped. Updating to Nothing Playing.")


                except Exception as e:
                    logging.error(f"Error polling Spotify: {e}")
                    if "token" in str(e).lower():
                        logging.warning("Spotify auth token issue detected during polling.")
            
            elif self.preferred_source == "ytm" and self.ytm: # YTM is preferred
                if self.ytm.is_connected:
                    try:
                        ytm_track_data = self.ytm.get_current_track() # Data from YTMClient's cache
                        # Let _process_ytm_data_update handle the logic
                        simplified_info_for_callback, significant_change_for_callback = self._process_ytm_data_update(ytm_track_data, "YTM Poll")
                        source_for_callback = MusicSource.YTM # Mark that YTM was polled
                        # Note: _process_ytm_data_update updates self.current_track_info
                        if significant_change_for_callback:
                             logger.debug(f"Polling YTM: Change detected via _process_ytm_data_update. Title: {simplified_info_for_callback.get('title')}")
                        else:
                             logger.debug(f"Polling YTM: No change detected via _process_ytm_data_update. Title: {simplified_info_for_callback.get('title')}")

                    except Exception as e:
                        logging.error(f"Error during YTM poll processing: {e}")
                else: # YTM not connected
                    logging.debug("Skipping YTM poll: Client not connected. Will attempt reconnect on next cycle if display active.")
                    if self.is_music_display_active:
                        logger.info("YTM is preferred and display active, attempting reconnect during poll cycle.")
                        if self.ytm.connect_client(timeout=5):
                            logger.info("YTM reconnected during poll cycle. Will process data on next poll/event.")
                            # Potentially sync state right here?
                            latest_data = self.ytm.get_current_track()
                            if latest_data:
                                simplified_info_for_callback, significant_change_for_callback = self._process_ytm_data_update(latest_data, "YTM Poll Reconnect Sync")
                                source_for_callback = MusicSource.YTM
                        else:
                            logger.warning("YTM failed to reconnect during poll cycle.")
                            # If YTM was the source, and failed to reconnect, set to Nothing Playing
                            with self.track_info_lock:
                                if self.current_source == MusicSource.YTM:
                                    simplified_info_for_callback = self.get_simplified_track_info(None, MusicSource.NONE)
                                    self.current_track_info = simplified_info_for_callback
                                    self.current_source = MusicSource.NONE
                                    significant_change_for_callback = True
                                    self.album_art_image = None
                                    self.last_album_art_url = None
                                    logger.info("Polling YTM: Reconnect failed. Updating to Nothing Playing.")


            # Callback to DisplayController if a significant change occurred from any source via polling
            if significant_change_for_callback and self.update_callback and simplified_info_for_callback:
                try:
                    # simplified_info_for_callback already contains the latest data
                    self.update_callback(simplified_info_for_callback, True) # True for significant change from poll
                except Exception as e:
                    logger.error(f"Error executing update callback from poll ({source_for_callback.name}): {e}")
            
            time.sleep(self.polling_interval)

    # Modified to accept data and source, making it more testable/reusable
    def get_simplified_track_info(self, track_data, source):
        """Provides a consistent format for track info regardless of source."""
        
        # Default "Nothing Playing" structure
        nothing_playing_info = {
            'source': 'None',
            'title': 'Nothing Playing',
            'artist': '',
            'album': '',
            'album_art_url': None,
            'duration_ms': 0,
            'progress_ms': 0,
            'is_playing': False,
        }

        if source == MusicSource.SPOTIFY and track_data:
            item = track_data.get('item', {})
            is_playing_spotify = track_data.get('is_playing', False)

            if not item or not is_playing_spotify:
                return nothing_playing_info.copy()

            return {
                'source': 'Spotify',
                'title': item.get('name'),
                'artist': ', '.join([a['name'] for a in item.get('artists', [])]),
                'album': item.get('album', {}).get('name'),
                'album_art_url': item.get('album', {}).get('images', [{}])[0].get('url') if item.get('album', {}).get('images') else None,
                'duration_ms': item.get('duration_ms'),
                'progress_ms': track_data.get('progress_ms'),
                'is_playing': is_playing_spotify, # Should be true here
            }
        elif source == MusicSource.YTM and track_data:
            video_info = track_data.get('video', {})
            player_info = track_data.get('player', {})

            title = video_info.get('title')
            artist = video_info.get('author')
            thumbnails = video_info.get('thumbnails', [])
            album_art_url = thumbnails[0].get('url') if thumbnails else None

            # Primary conditions for "Nothing Playing" for YTM:
            # 1. An ad is currently playing.
            # 2. Essential metadata (title or artist) is missing from the source data.
            if player_info.get('adPlaying', False):
                logging.debug("YTM (get_simplified_track_info): Ad is playing, reporting as Nothing Playing.")
                return nothing_playing_info.copy()
            
            if not title or not artist:
                logging.debug(f"YTM (get_simplified_track_info): No title ('{title}') or artist ('{artist}'), reporting as Nothing Playing.")
                return nothing_playing_info.copy()

            # If we've reached this point, we have a title and artist, and it's not an ad.
            # Proceed to determine the accurate playback state and construct full track details.
            track_state = player_info.get('trackState')
            # is_playing_ytm is True ONLY if trackState is 1 (actively playing).
            # Other states: 0 (loading/buffering), 2 (paused), 3 (stopped/ended) will result in is_playing_ytm = False.
            is_playing_ytm = (track_state == 1) 

            # logging.debug(f"[get_simplified_track_info YTM] Title: {title}, Artist: {artist}, TrackState: {track_state}, IsPlayingYTM: {is_playing_ytm}")

            album = video_info.get('album')
            duration_seconds = video_info.get('durationSeconds')
            duration_ms = int(duration_seconds * 1000) if duration_seconds is not None else 0
            progress_seconds = player_info.get('videoProgress')
            progress_ms = int(progress_seconds * 1000) if progress_seconds is not None else 0
            # album_art_url was already fetched earlier

            return {
                'source': 'YouTube Music',
                'title': title,
                'artist': artist,
                'album': album if album else '', # Ensure album is not None
                'album_art_url': album_art_url,
                'duration_ms': duration_ms,
                'progress_ms': progress_ms,
                'is_playing': is_playing_ytm, # This now accurately reflects if YTM reports the track as playing
            }
        else:
            # This covers cases where source is NONE, or track_data is None for Spotify/YTM
            return nothing_playing_info.copy()

    def get_current_display_info(self):
        """Returns the currently stored track information for display."""
        with self.track_info_lock:
            return self.current_track_info.copy() if self.current_track_info else None

    def start_polling(self):
        # Only start polling if enabled
        if not self.enabled:
            logging.info("Music manager disabled, polling not started.")
            return

        if not self.poll_thread or not self.poll_thread.is_alive():
            # Ensure at least one client is potentially available
            if not self.spotify and not self.ytm:
                 logging.warning("Cannot start polling: No music clients initialized or available.")
                 return

            self.stop_event.clear()
            self.poll_thread = threading.Thread(target=self._poll_music_data, daemon=True)
            self.poll_thread.start()
            logging.info("Music polling started.")

    def stop_polling(self):
        """Stops the music polling thread."""
        logger.info("Music manager: Stopping polling thread...")
        self.stop_event.set()
        if self.poll_thread and self.poll_thread.is_alive():
            self.poll_thread.join(timeout=self.polling_interval + 1) # Wait for thread to finish
        if self.poll_thread and self.poll_thread.is_alive():
            logger.warning("Music manager: Polling thread did not terminate cleanly.")
        else:
            logger.info("Music manager: Polling thread stopped.")
        self.poll_thread = None # Clear the thread object
        # Also ensure YTM client is disconnected when polling stops completely
        if self.ytm:
            logger.info("MusicManager: Shutting down YTMClient resources.")
            if self.ytm.is_connected:
                 self.ytm.disconnect_client()
            self.ytm.shutdown() # Call the new shutdown method for the executor

    # Method moved from DisplayController and renamed
    def display(self, force_clear: bool = False):
        perform_full_refresh_this_cycle = force_clear
        art_url_currently_in_cache = None # Initialize to None
        image_currently_in_cache = None   # Initialize to None
        
        # Check if an event previously signaled a need for immediate refresh (and populated the queue)
        initial_data_from_queue_due_to_event = None
        if self._needs_immediate_full_refresh:
            logger.debug("MusicManager.display: _needs_immediate_full_refresh is True (event-driven).")
            perform_full_refresh_this_cycle = True # An event demanding refresh also implies a full refresh
            try:
                # Try to get data now, it's the freshest from the event
                initial_data_from_queue_due_to_event = self.ytm_event_data_queue.get_nowait()
                logger.info(f"MusicManager.display: Got data from ytm_event_data_queue (due to event flag): Title {initial_data_from_queue_due_to_event.get('title') if initial_data_from_queue_due_to_event else 'None'}")
            except queue.Empty:
                logger.warning("MusicManager.display: _needs_immediate_full_refresh was true, but queue empty. Will refresh with current_track_info.")
            self._needs_immediate_full_refresh = False # Consume the event flag

        current_track_info_snapshot = None

        if perform_full_refresh_this_cycle:
            log_msg_detail = f"force_clear_from_DC={force_clear}, event_driven_refresh_attempted={'Yes' if initial_data_from_queue_due_to_event is not None else 'No'}"
            logger.debug(f"MusicManager.display: Performing full refresh cycle. Details: {log_msg_detail}")
            
            self.display_manager.clear()
            self.activate_music_display() # Call this BEFORE snapshotting data for this cycle.
                                        # This might trigger YTM events if it reconnects.
            self.last_periodic_refresh_time = time.time() # Update timer *after* potential processing in activate
            
            data_from_queue_post_activate = None
            # Check queue again, activate_music_display might have put fresh data via _process_ytm_data_update
            try:
                data_from_queue_post_activate = self.ytm_event_data_queue.get_nowait()
                logger.info(f"MusicManager.display (Full Refresh): Got data from queue POST activate_music_display: Title {data_from_queue_post_activate.get('title') if data_from_queue_post_activate else 'None'}")
            except queue.Empty:
                logger.debug("MusicManager.display (Full Refresh): Queue empty POST activate_music_display.")


            if data_from_queue_post_activate:
                current_track_info_snapshot = data_from_queue_post_activate
            elif initial_data_from_queue_due_to_event: 
                current_track_info_snapshot = initial_data_from_queue_due_to_event
                logger.debug("MusicManager.display (Full Refresh): Using data from initial event queue for snapshot.")
            else:
                with self.track_info_lock:
                    current_track_info_snapshot = self.current_track_info.copy() if self.current_track_info else None
                logger.debug("MusicManager.display (Full Refresh): Using self.current_track_info for snapshot.")
        else: # This is the correctly paired else for 'if perform_full_refresh_this_cycle:'
            with self.track_info_lock:
                current_track_info_snapshot = self.current_track_info.copy() if self.current_track_info else None


        # --- Update cache variables after snapshot is finalized ---
        with self.track_info_lock: # Ensure thread-safe access to shared cache attributes
            art_url_currently_in_cache = self.last_album_art_url
            image_currently_in_cache = self.album_art_image

        snapshot_title_for_log = current_track_info_snapshot.get('title', 'N/A') if current_track_info_snapshot else 'N/A'
        if perform_full_refresh_this_cycle: 
             logger.debug(f"MusicManager.display (Full Refresh Render): Using snapshot - Title: '{snapshot_title_for_log}'")
        
        # --- Original Nothing Playing Logic ---
        if not current_track_info_snapshot or current_track_info_snapshot.get('title') == 'Nothing Playing':
            if not hasattr(self, '_last_nothing_playing_log_time') or time.time() - getattr(self, '_last_nothing_playing_log_time', 0) > 30:
                logger.debug("Music Screen (MusicManager): Nothing playing or info explicitly 'Nothing Playing'.")
                self._last_nothing_playing_log_time = time.time()

            if not self.is_currently_showing_nothing_playing or perform_full_refresh_this_cycle:
                if perform_full_refresh_this_cycle or not self.is_currently_showing_nothing_playing:
                    self.display_manager.clear()
                
                text_width = self.display_manager.get_text_width("Nothing Playing", self.display_manager.regular_font)
                x_pos = (self.display_manager.matrix.width - text_width) // 2
                y_pos = (self.display_manager.matrix.height // 2) - 4
                self.display_manager.draw_text("Nothing Playing", x=x_pos, y=y_pos, font=self.display_manager.regular_font)
                self.display_manager.update_display()
                self.is_currently_showing_nothing_playing = True

            with self.track_info_lock: 
                self.scroll_position_title = 0
                self.scroll_position_artist = 0
                self.scroll_position_album = 0
                self.title_scroll_tick = 0
                self.artist_scroll_tick = 0
                self.album_scroll_tick = 0
                if self.album_art_image is not None or self.last_album_art_url is not None:
                    logger.debug("Clearing album art cache as 'Nothing Playing' is displayed.")
                    self.album_art_image = None
                    self.last_album_art_url = None
            return

        self.is_currently_showing_nothing_playing = False 

        if perform_full_refresh_this_cycle: 
            title_being_displayed = current_track_info_snapshot.get('title','N/A') if current_track_info_snapshot else "N/A"
            logger.debug(f"MusicManager: Resetting scroll positions for track '{title_being_displayed}' due to full refresh signal (periodic or event-driven).")
            self.scroll_position_title = 0
            self.scroll_position_artist = 0
            self.scroll_position_album = 0

        if not self.is_music_display_active and not perform_full_refresh_this_cycle : 
             # If display wasn't active, and this isn't a full refresh cycle that would activate it,
             # then we shouldn't proceed to draw music. This case might be rare if DisplayController
             # manages music display activation properly on mode switch.
             logger.warning("MusicManager.display called when music display not active and not a full refresh. Aborting draw.")
             return
        elif not self.is_music_display_active and perform_full_refresh_this_cycle:
             # This is handled by activate_music_display() called within the full_refresh_this_cycle block
             pass


        if not perform_full_refresh_this_cycle: 
            self.display_manager.draw.rectangle([0, 0, self.display_manager.matrix.width, self.display_manager.matrix.height], fill=(0, 0, 0))

        matrix_height = self.display_manager.matrix.height
        album_art_size = matrix_height # Was matrix_height - 2
        album_art_target_size = (album_art_size, album_art_size)
        album_art_x = 0 # Was 1
        album_art_y = 0 # Was 1
        text_area_x_start = album_art_x + album_art_size + 2
        text_area_width = self.display_manager.matrix.width - text_area_x_start - 1 

        image_to_render_this_cycle = None
        target_art_url_for_current_track = current_track_info_snapshot.get('album_art_url')

        if target_art_url_for_current_track:
            if image_currently_in_cache and art_url_currently_in_cache == target_art_url_for_current_track:
                image_to_render_this_cycle = image_currently_in_cache
                # logger.debug(f"Using cached album art for {target_art_url_for_current_track}") # Can be noisy
            else:
                logger.info(f"MusicManager: Fetching album art for: {target_art_url_for_current_track}")
                fetched_image = self._fetch_and_resize_image(target_art_url_for_current_track, album_art_target_size)
                if fetched_image:
                    logger.info(f"MusicManager: Album art for {target_art_url_for_current_track} fetched successfully.")
                    with self.track_info_lock:
                        latest_known_art_url_in_live_info = self.current_track_info.get('album_art_url') if self.current_track_info else None
                        if target_art_url_for_current_track == latest_known_art_url_in_live_info:
                            self.album_art_image = fetched_image
                            self.last_album_art_url = target_art_url_for_current_track 
                            image_to_render_this_cycle = fetched_image
                            logger.debug(f"Cached and will render new art for {target_art_url_for_current_track}")
                        else:
                            logger.info(f"MusicManager: Discarding fetched art for {target_art_url_for_current_track}; "
                                        f"track changed to '{self.current_track_info.get('title', 'N/A')}' "
                                        f"with art '{latest_known_art_url_in_live_info}' during fetch.")
                else:
                    logger.warning(f"MusicManager: Failed to fetch or process album art for {target_art_url_for_current_track}.")
                    with self.track_info_lock:
                        if self.last_album_art_url == target_art_url_for_current_track:
                             self.album_art_image = None 
        else:
            # logger.debug(f"No album art URL for track: {current_track_info_snapshot.get('title', 'N/A')}. Clearing cache.")
            with self.track_info_lock:
                if self.album_art_image is not None or self.last_album_art_url is not None:
                    self.album_art_image = None
                    self.last_album_art_url = None 

        if image_to_render_this_cycle:
            self.display_manager.image.paste(image_to_render_this_cycle, (album_art_x, album_art_y))
        else:
            self.display_manager.draw.rectangle([album_art_x, album_art_y, 
                                                 album_art_x + album_art_size -1, album_art_y + album_art_size -1],
                                                 outline=(50,50,50), fill=(10,10,10))

        title = current_track_info_snapshot.get('title', ' ')
        artist = current_track_info_snapshot.get('artist', ' ')
        album = current_track_info_snapshot.get('album', ' ') 

        font_title = self.display_manager.small_font
        font_artist_album = self.display_manager.bdf_5x7_font

        # Get line height for the TTF title font
        ascent, descent = font_title.getmetrics()
        line_height_title = ascent + descent
        
        # Use a static value for the BDF font's line height
        LINE_HEIGHT_BDF = 8  # Fixed pixel height for 5x7 BDF font
        PADDING_BETWEEN_LINES = 1

        # Calculate y positions as percentages of display height for scaling
        matrix_height = self.display_manager.matrix.height
        
        # Define positions as percentages (0.0 to 1.0)
        ARTIST_Y_PERCENT = 0.34  # 34% from top  
        ALBUM_Y_PERCENT = 0.60   # 60% from top
        
        # Use fixed positioning to ensure consistency across all songs
        # Add a consistent font baseline shift for BDF fonts (not dynamic)
        FIXED_BDF_BASELINE_SHIFT = 6  # Fixed shift for proper BDF font positioning
        
        y_pos_title_top = 1
        y_pos_artist_top = int(matrix_height * ARTIST_Y_PERCENT) + FIXED_BDF_BASELINE_SHIFT
        y_pos_album_top = int(matrix_height * ALBUM_Y_PERCENT) + FIXED_BDF_BASELINE_SHIFT
        
        TEXT_SCROLL_DIVISOR = 5

        # --- Title --- 
        title_width = self.display_manager.get_text_width(title, font_title)
        current_title_display_text = title
        if title_width > text_area_width:
            if self.scroll_position_title >= len(title):
                self.scroll_position_title = 0
            current_title_display_text = title[self.scroll_position_title:] + "   " + title[:self.scroll_position_title]
        
        self.display_manager.draw_text(current_title_display_text, 
                                     x=text_area_x_start, y=y_pos_title_top, color=(255, 255, 255), font=font_title)
        if title_width > text_area_width:
            self.title_scroll_tick += 1
            if self.title_scroll_tick % TEXT_SCROLL_DIVISOR == 0:
                self.scroll_position_title = (self.scroll_position_title + 1) % len(title)
                self.title_scroll_tick = 0 
        else:
            self.scroll_position_title = 0
            self.title_scroll_tick = 0

        # --- Artist --- 
        artist_width = self.display_manager.get_text_width(artist, font_artist_album)
        current_artist_display_text = artist
        if artist_width > text_area_width:
            if self.scroll_position_artist >= len(artist):
                self.scroll_position_artist = 0
            current_artist_display_text = artist[self.scroll_position_artist:] + "   " + artist[:self.scroll_position_artist]

        self.display_manager.draw_text(current_artist_display_text, 
                                      x=text_area_x_start, y=y_pos_artist_top, color=(180, 180, 180), font=font_artist_album)
        if artist_width > text_area_width:
            self.artist_scroll_tick += 1
            if self.artist_scroll_tick % TEXT_SCROLL_DIVISOR == 0:
                self.scroll_position_artist = (self.scroll_position_artist + 1) % len(artist)
                self.artist_scroll_tick = 0
        else:
            self.scroll_position_artist = 0
            self.artist_scroll_tick = 0
            
        # --- Album ---
        if (matrix_height - y_pos_album_top) >= LINE_HEIGHT_BDF : 
            album_width = self.display_manager.get_text_width(album, font_artist_album)
            # Display album if it fits or can be scrolled (maintains original behavior but adds scrolling)
            if album_width <= text_area_width:
                # Album fits without scrolling - display normally
                self.display_manager.draw_text(album, 
                                             x=text_area_x_start, y=y_pos_album_top, color=(150, 150, 150), font=font_artist_album)
                self.scroll_position_album = 0
                self.album_scroll_tick = 0
            elif album_width > text_area_width:
                # Album is too wide - scroll it
                current_album_display_text = album
                if self.scroll_position_album >= len(album):
                    self.scroll_position_album = 0
                current_album_display_text = album[self.scroll_position_album:] + "   " + album[:self.scroll_position_album]
                
                self.display_manager.draw_text(current_album_display_text, 
                                             x=text_area_x_start, y=y_pos_album_top, color=(150, 150, 150), font=font_artist_album)
                self.album_scroll_tick += 1
                if self.album_scroll_tick % TEXT_SCROLL_DIVISOR == 0:
                    self.scroll_position_album = (self.scroll_position_album + 1) % len(album)
                    self.album_scroll_tick = 0

        # --- Progress Bar --- 
        progress_bar_height = 3
        progress_bar_y = matrix_height - progress_bar_height - 1
        duration_ms = current_track_info_snapshot.get('duration_ms', 0)
        progress_ms = current_track_info_snapshot.get('progress_ms', 0)

        if duration_ms > 0:
            bar_total_width = text_area_width
            filled_ratio = progress_ms / duration_ms
            filled_width = int(filled_ratio * bar_total_width)

            self.display_manager.draw.rectangle([
                text_area_x_start, progress_bar_y, 
                text_area_x_start + bar_total_width -1, progress_bar_y + progress_bar_height -1
            ], outline=(60, 60, 60), fill=(30,30,30)) 
            
            if filled_width > 0:
                self.display_manager.draw.rectangle([
                    text_area_x_start, progress_bar_y, 
                    text_area_x_start + filled_width -1, progress_bar_y + progress_bar_height -1
                ], fill=(200, 200, 200)) 

        self.display_manager.update_display()


# Example usage (for testing this module standalone, if needed)
# def print_update(track_info):
# logging.info(f"Callback: Track update received by dummy callback: {track_info}")

if __name__ == '__main__':
    # This is a placeholder for testing. 
    # To test properly, you'd need a mock DisplayManager and ConfigManager.
    logging.basicConfig(level=logging.DEBUG)
    logger.info("Running MusicManager standalone test (limited)...")

    # Mock DisplayManager and Config objects
    class MockDisplayManager:
        def __init__(self):
            self.matrix = type('Matrix', (), {'width': 64, 'height': 32})() # Mock matrix
            self.image = Image.new("RGB", (self.matrix.width, self.matrix.height))
            self.draw = ImageDraw.Draw(self.image) # Requires ImageDraw
            self.regular_font = None # Needs font loading
            self.small_font = None
            self.extra_small_font = None
            # Add other methods/attributes DisplayManager uses if they are called by MusicManager's display
            # For simplicity, we won't fully mock font loading here.
            # self.regular_font = ImageFont.truetype("path/to/font.ttf", 8) 


        def clear(self): logger.debug("MockDisplayManager: clear() called")
        def get_text_width(self, text, font): return len(text) * 5 # Rough mock
        def draw_text(self, text, x, y, color=(255,255,255), font=None): logger.debug(f"MockDisplayManager: draw_text '{text}' at ({x},{y})")
        def update_display(self): logger.debug("MockDisplayManager: update_display() called")

    class MockConfig:
        def get(self, key, default=None):
            if key == "music":
                return {"enabled": True, "POLLING_INTERVAL_SECONDS": 2, "preferred_source": "auto"}
            return default

    # Need to import ImageDraw for the mock to work if draw_text is complex
    try: from PIL import ImageDraw, ImageFont 
    except ImportError: ImageDraw = None; ImageFont = None; logger.warning("Pillow ImageDraw/ImageFont not fully available for mock")


    mock_display = MockDisplayManager()
    mock_config_main = {"music": {"enabled": True, "POLLING_INTERVAL_SECONDS": 2, "preferred_source": "auto"}}
    
    # The MusicManager expects the overall config, not just the music part directly for its _load_config
    # So we simulate a config object that has a .get('music', {}) method.
    # MusicManager now uses the passed config instead of loading from file.

    # Simplified test:
    manager = MusicManager(display_manager=mock_display, config=mock_config_main)
    
    logger.info("MusicManager standalone test setup completed.")
    logger.info("To test: run the main application and observe logs from MusicManager.")
    # if manager.enabled:
    # manager.start_polling()
    # try:
    # while True:
    #         time.sleep(1)
    #         # In a real test, you might manually call manager.display() after setting some track info
    # except KeyboardInterrupt:
    #         logger.info("Stopping standalone test...")
    # finally:
    # if manager.enabled:
    # manager.stop_polling()
    #         logger.info("Test finished.") 