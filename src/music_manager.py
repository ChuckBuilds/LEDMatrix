import time
import threading
from enum import Enum, auto
import logging
import json
import os
from io import BytesIO
import requests
from PIL import Image, ImageEnhance, ImageFont

# Use relative imports for clients within the same package (src)
from .spotify_client import SpotifyClient
from .ytm_client import YTMClient
# Removed: import config

# Configure logging
# logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s') # Keep main as INFO or higher
logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG) # Set music_manager's logger to DEBUG

# Define paths relative to this file's location
CONFIG_DIR = os.path.join(os.path.dirname(__file__), '..', 'config')
CONFIG_PATH = os.path.join(CONFIG_DIR, 'config.json')
# SECRETS_PATH is handled within SpotifyClient

class MusicSource(Enum):
    NONE = auto()
    SPOTIFY = auto()
    YTM = auto()

class MusicManager:
    def __init__(self, display_manager, config, update_callback=None, is_music_display_active_callback=None):
        self.display_manager = display_manager
        self.config = config
        self.spotify = None
        self.ytm = None
        self.current_track_info = None
        self.current_source = MusicSource.NONE
        self.update_callback = update_callback
        self.is_music_display_active_callback = is_music_display_active_callback
        self.polling_interval = 2 # Default
        self.enabled = False # Default
        self.preferred_source = "auto" # Default
        self.stop_event = threading.Event()

        # Display related attributes moved from DisplayController
        self.album_art_image = None
        self.last_album_art_url = None
        self.scroll_position_title = 0
        self.scroll_position_artist = 0
        self.title_scroll_tick = 0
        self.artist_scroll_tick = 0
        
        self._load_config() # Load config first
        self._initialize_clients() # Initialize based on loaded config
        self.poll_thread = None

    def _load_config(self):
        default_interval = 2
        default_preferred_source = "auto"
        self.enabled = False # Assume disabled until config proves otherwise

        if not os.path.exists(CONFIG_PATH):
            logging.warning(f"Config file not found at {CONFIG_PATH}. Music manager disabled.")
            return

        try:
            with open(CONFIG_PATH, 'r') as f:
                config_data = json.load(f)
                music_config = config_data.get("music", {})

                self.enabled = music_config.get("enabled", False)
                self.polling_interval = music_config.get("POLLING_INTERVAL_SECONDS", default_interval)
                self.preferred_source = music_config.get("preferred_source", default_preferred_source).lower()

                if not self.enabled:
                    logging.info("Music manager is disabled in config.json.")
                    return # Don't proceed further if disabled

                logging.info(f"Music manager enabled. Polling interval: {self.polling_interval}s. Preferred source: {self.preferred_source}")

        except json.JSONDecodeError:
            logging.error(f"Error decoding JSON from {CONFIG_PATH}. Music manager disabled.")
            self.enabled = False
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
        if self.preferred_source in ["auto", "spotify"]:
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
            logging.info("Spotify client initialization skipped due to preferred_source setting.")
            self.spotify = None

        # Initialize YTM Client if needed
        if self.preferred_source in ["auto", "ytm"]:
            try:
                # Initialize YTMClient but don't auto-start its connection listener
                self.ytm = YTMClient(update_callback=self._handle_ytm_update, auto_start=False)
                # Availability will be checked/connection started in activate_music_mode
                if self.ytm.base_url:
                    logging.info(f"YTMClient initialized with base URL: {self.ytm.base_url}. Will connect when music mode is active.")
                else:
                    logging.error("YTMClient initialized, but base_url is not set. YTM features will be unavailable.")
                    self.ytm = None # Cannot function without a base URL
            except Exception as e:
                logging.error(f"Failed to initialize YTM client: {e}")
                self.ytm = None
        else:
            logging.info("YTM client initialization skipped due to preferred_source setting.")
            self.ytm = None

    def activate_music_mode(self):
        logger.info("MusicManager: Activating music mode.")
        logger.debug(f"MusicManager: Current self.ytm object: {self.ytm}")
        if not self.enabled:
            logger.info("MusicManager: Cannot activate, manager is disabled.")
            return

        activated_successfully = False
        if self.ytm:
            logger.info("MusicManager: Telling YTMClient to start listening.")
            self.ytm.start_client_listening() 
            logger.info("MusicManager: Waiting for YTMClient to connect...")
            # Wait for the YTM client's connection event with a timeout
            if self.ytm.is_connected_event.wait(timeout=7.0): # Wait up to 7 seconds
                logger.info("MusicManager: YTMClient connected (event signaled).")
                # Double check with is_available for sanity, though event should be primary
                if self.ytm.is_available():
                    logger.info("MusicManager: YTMClient also reports as available.")
                    activated_successfully = True
                else:
                    logger.warning("MusicManager: YTMClient event signaled connected, but is_available() is false. Proceeding cautiously.")
                    # Potentially still set activated_successfully = True if event is trusted more
                    activated_successfully = True # Trust the event for now
            else:
                logger.warning("MusicManager: YTMClient did not signal connection within timeout.")
                # Check is_available one last time in case the event was missed but it connected
                if self.ytm.is_available():
                    logger.warning("MusicManager: YTMClient event timed out, but is_available() is now true. Proceeding.")
                    activated_successfully = True
                else:
                    logger.warning("MusicManager: YTMClient still not available after timeout.")

        elif self.spotify and self.spotify.is_authenticated():
            logger.info("MusicManager: Spotify is available.")
            activated_successfully = True
        
        if activated_successfully:
            self.start_polling() 
            self.trigger_immediate_poll_and_update()
        else:
            logger.warning("MusicManager: No music clients became available. Polling not started effectively.")

    def deactivate_music_mode(self):
        logger.info("MusicManager: Deactivating music mode.")
        self.stop_polling() # Stop MusicManager polling thread
        
        if self.ytm:
            logger.info("MusicManager: Telling YTMClient to stop listening.")
            self.ytm.stop_client()
        
        # Clear current track info to ensure fresh state on next activation
        logger.debug("MusicManager: Clearing current track info and album art on deactivation.")
        self.current_track_info = self.get_simplified_track_info(None, MusicSource.NONE) # Reset to nothing playing
        self.current_source = MusicSource.NONE
        self.album_art_image = None
        self.last_album_art_url = None
        # No need to call update_callback here as DisplayController is switching away

    def _handle_ytm_update(self, data):
        """Handles real-time track updates from YTMClient (Socket.IO)."""
        logger.debug(f"MusicManager received direct YTM update: {data}")
        # The 'data' from YTMClient via Socket.IO might be slightly different
        # than what was expected from the old REST API version.
        # We need to ensure it's processed correctly by get_simplified_track_info.
        # Based on ytm_client.py, 'data' should be the track_info dictionary.

        simplified_info = self.get_simplified_track_info(data, MusicSource.YTM)

        if self._is_new_track(simplified_info):
            logger.debug(f"YTM Direct Update: Track change detected. Source: {simplified_info.get('source') if simplified_info else 'NONE'}. Track: {simplified_info.get('title') if simplified_info else 'Nothing Playing'}")
            self.current_track_info = simplified_info
            self.current_source = MusicSource.YTM if simplified_info and simplified_info.get('is_playing') else MusicSource.NONE
            
            # Only call update_callback if music display is active
            if self.is_music_display_active_callback and self.is_music_display_active_callback():
                logger.debug("Music display is active, calling update_callback for YTM.")
                if self.update_callback:
                    self.update_callback(self.current_track_info)
                # If music display is active, also fetch album art immediately.
                if self.current_track_info and self.current_track_info.get('album_art_url'):
                    self._fetch_album_art(self.current_track_info['album_art_url'])
                else:
                    self.album_art_image = None # Clear album art if no URL
                    self.last_album_art_url = None
            else:
                # Music display is not active. Update internal state silently.
                # Album art will be fetched when the display becomes active via self.display()
                logger.debug("Music display is NOT active. Updated YTM track info silently.")
                if self.current_track_info and self.current_track_info.get('album_art_url'):
                    # We still store the URL, but don't fetch the image yet
                    self.last_album_art_url = self.current_track_info['album_art_url']
                    self.album_art_image = None # Ensure image is cleared if it was previously set
                else:
                    self.album_art_image = None
                    self.last_album_art_url = None
        else:
            logger.debug("YTM Direct Update: No change in simplified track info.")
            # If track hasn't changed, but music display became active, and we don't have art, fetch it.
            if self.is_music_display_active_callback and self.is_music_display_active_callback():
                if self.current_track_info and self.current_track_info.get('album_art_url') and not self.album_art_image:
                     if self.last_album_art_url == self.current_track_info['album_art_url']: # check if URL is same
                         logger.debug("Music display active, track unchanged, fetching missing album art.")
                         self._fetch_album_art(self.current_track_info['album_art_url'])

    def _fetch_and_resize_image(self, url: str, target_size: tuple[int, int]) -> Image.Image | None:
        """Fetches an image from a URL, resizes it, and returns a PIL Image object."""
        if not url:
            return None
        try:
            response = requests.get(url, timeout=5) # 5-second timeout for image download
            response.raise_for_status() # Raise an exception for bad status codes
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
            # If music display is not active, sleep and skip polling cycle
            if not (self.is_music_display_active_callback and self.is_music_display_active_callback()):
                logger.debug("MusicManager (_poll_music_data): Music display is NOT active. Sleeping.")
                time.sleep(self.polling_interval) # Still respect polling interval for sleep
                continue

            logger.debug("MusicManager (_poll_music_data): Music display IS active. Proceeding with poll.")
            polled_track_info = None
            polled_source = MusicSource.NONE
            is_playing = False

            # Determine which sources to poll based on preference
            poll_spotify = self.preferred_source in ["auto", "spotify"] and self.spotify and self.spotify.is_authenticated()
            poll_ytm = self.preferred_source in ["auto", "ytm"] and self.ytm # Check if ytm object exists

            # --- Try Spotify First (if allowed and available) ---
            if poll_spotify:
                try:
                    spotify_track = self.spotify.get_current_track()
                    if spotify_track and spotify_track.get('is_playing'):
                        polled_track_info = spotify_track
                        polled_source = MusicSource.SPOTIFY
                        is_playing = True
                        logging.debug(f"Polling Spotify: Active track - {spotify_track.get('item', {}).get('name')}")
                    else:
                        logging.debug("Polling Spotify: No active track or player paused.")
                except Exception as e:
                    logging.error(f"Error polling Spotify: {e}")
                    if "token" in str(e).lower():
                        logging.warning("Spotify auth token issue detected during polling.")

            # --- Try YTM if Spotify isn't playing OR if YTM is preferred ---
            # If YTM is preferred, poll it even if Spotify might be playing (config override)
            # If Auto, only poll YTM if Spotify wasn't found playing
            should_poll_ytm_now = poll_ytm and (self.preferred_source == "ytm" or (self.preferred_source == "auto" and not is_playing))

            if should_poll_ytm_now:
                # Re-check availability just before polling
                if self.ytm.is_available():
                    try:
                        ytm_track = self.ytm.get_current_track()
                        if ytm_track and not ytm_track.get('player', {}).get('isPaused'):
                            # If YTM is preferred, it overrides Spotify even if Spotify was playing
                            if self.preferred_source == "ytm" or not is_playing:
                                polled_track_info = ytm_track
                                polled_source = MusicSource.YTM
                                is_playing = True
                                logging.debug(f"Polling YTM: Active track - {ytm_track.get('track', {}).get('title')}")
                        else:
                             logging.debug("Polling YTM: No active track or player paused.")
                    except Exception as e:
                        logging.error(f"Error polling YTM: {e}")
                else:
                     logging.debug("Skipping YTM poll: Server not available.")
                     # Consider setting self.ytm = None if it becomes unavailable repeatedly?

            # --- Consolidate and Check for Changes ---
            simplified_info = self.get_simplified_track_info(polled_track_info, polled_source)

            has_changed = False
            if simplified_info != self.current_track_info:
                has_changed = True
                
                # Update internal state
                old_album_art_url = self.current_track_info.get('album_art_url') if self.current_track_info else None
                new_album_art_url = simplified_info.get('album_art_url') if simplified_info else None

                self.current_track_info = simplified_info
                self.current_source = polled_source

                if new_album_art_url != old_album_art_url:
                    self.album_art_image = None
                    self.last_album_art_url = new_album_art_url
                
                display_title = self.current_track_info.get('title', 'None') if self.current_track_info else 'None'
                logger.debug(f"Track change detected. Source: {self.current_source.name}. Track: {display_title}")
            else:
                logger.debug("No change in simplified track info.")

            if has_changed and self.update_callback:
                try:
                    self.update_callback(self.current_track_info)
                except Exception as e:
                    logger.error(f"Error executing update callback: {e}")
            
            time.sleep(self.polling_interval)

    # Modified to accept data and source, making it more testable/reusable
    def get_simplified_track_info(self, track_data, source):
        """Provides a consistent format for track info regardless of source."""
        if source == MusicSource.SPOTIFY and track_data:
            item = track_data.get('item', {})
            if not item: return None
            return {
                'source': 'Spotify',
                'title': item.get('name'),
                'artist': ', '.join([a['name'] for a in item.get('artists', [])]),
                'album': item.get('album', {}).get('name'),
                'album_art_url': item.get('album', {}).get('images', [{}])[0].get('url') if item.get('album', {}).get('images') else None,
                'duration_ms': item.get('duration_ms'),
                'progress_ms': track_data.get('progress_ms'),
                'is_playing': track_data.get('is_playing', False),
            }
        elif source == MusicSource.YTM and track_data:
            video_info = track_data.get('video', {}) # Corrected: song details are in 'video'
            player_info = track_data.get('player', {})

            title = video_info.get('title', 'Unknown Title')
            artist = video_info.get('author', 'Unknown Artist')
            album = video_info.get('album') # Can be null, handled by .get in return
            
            duration_seconds = video_info.get('durationSeconds')
            duration_ms = int(duration_seconds * 1000) if duration_seconds is not None else 0

            # Progress is in player_info.videoProgress (in seconds)
            progress_seconds = player_info.get('videoProgress')
            progress_ms = int(progress_seconds * 1000) if progress_seconds is not None else 0

            # Album art
            thumbnails = video_info.get('thumbnails', [])
            album_art_url = thumbnails[0].get('url') if thumbnails else None

            # Play state: player_info.trackState: -1 Unknown, 0 Paused, 1 Playing, 2 Buffering
            track_state = player_info.get('trackState')
            is_playing = (track_state == 1) # 1 means Playing

            # Check for ad playing, treat as 'paused' for track display purposes
            if player_info.get('adPlaying', False):
                is_playing = False # Or handle as a special state if needed
                logging.debug("YTM: Ad is playing, reporting track as not actively playing.")

            return {
                'source': 'YouTube Music',
                'title': title,
                'artist': artist,
                'album': album if album else '', # Ensure album is not None for display
                'album_art_url': album_art_url,
                'duration_ms': duration_ms,
                'progress_ms': progress_ms,
                'is_playing': is_playing,
            }
        else:
             # Return a default structure for 'nothing playing'
            return {
                'source': 'None',
                'title': 'Nothing Playing',
                'artist': '',
                'album': '',
                'album_art_url': None,
                'duration_ms': 0,
                'progress_ms': 0,
                'is_playing': False,
            }

    def get_current_display_info(self):
        """Returns the currently stored track information for display."""
        # This method might be used by DisplayController if it still needs a snapshot
        return self.current_track_info

    def start_polling(self):
        # Only start polling if enabled
        if not self.enabled:
            logging.info("Music manager disabled, polling not started.")
            return

        if not self.poll_thread or not self.poll_thread.is_alive():
            # Ensure at least one client is initialized and potentially usable
            client_ready = False
            if self.ytm and self.ytm.is_available(): # Check if YTM client object exists AND is available
                client_ready = True
            elif self.spotify and self.spotify.is_authenticated(): # Check if Spotify client object exists AND is authenticated
                client_ready = True
            
            if not client_ready:
                 logging.warning("Cannot start polling: No music clients are currently available/ready.")
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

    # Method moved from DisplayController and renamed
    def display(self, force_clear=False):
        """Displays the current music info on the matrix."""
        # This method is called by DisplayController when it's time to show the music screen.
        # It should ensure all necessary data (like album art) is ready.

        if not self.current_track_info or not self.current_track_info.get('is_playing'):
            # If nothing is playing or no track info, display a "Nothing Playing" message
            # This part is handled by the DisplayManager's draw_text_display method
            # which is more flexible for different "nothing playing" scenarios.
            # We can instruct DisplayManager what to show.
            logger.debug("MusicManager.display: No track playing or no info. Requesting clear/default display.")
            # Consider if MusicManager should have a method to explicitly tell DisplayManager
            # to show "Nothing Playing - Music" or similar, or if DisplayController handles this.
            # For now, assume DisplayController handles the "nothing playing" state for the music screen
            # by perhaps showing a static image or text if current_track_info is None from the callback.
            # However, if force_clear is true, we should clear.
            if force_clear:
                 self.display_manager.clear_display()
                 # Optionally, draw a "Nothing Playing" message directly if that's desired behavior for music mode
                 # self.display_manager.draw_text_display("Nothing Playing", font_name="merksam", text_color=(100,100,100))

            # Since display() is called when music mode is active, ensure album art is fetched if missing
            elif self.current_track_info and self.current_track_info.get('album_art_url') and not self.album_art_image:
                logger.debug("MusicManager.display: Music active, track info exists, but no album art image. Fetching.")
                self._fetch_album_art(self.current_track_info['album_art_url'])
            # Fall through to draw the actual content if it exists, even if art is still fetching.
            # The draw_music_display should handle a missing album_art_image gracefully.
            
            # Based on original logic, if nothing playing, the display method might not get called,
            # or DisplayController shows something else. If it *is* called, we must ensure
            # DisplayManager shows something sensible or clears.
            # Let's ensure that if this method IS called and nothing is playing, we clear the music part.
            # The DisplayManager.draw_music_display should ideally handle `None` for track components.
            # For safety, if we are here and nothing playing, but force_clear wasn't set,
            # we should probably clear the music-specific area if it was previously drawn.
            # This is tricky because `force_clear` comes from DisplayController based on mode switches.
            # The best place to handle "nothing playing" is within draw_music_display based on track_info.

            # Let's simplify: if no track, draw_music_display should handle it.
            # If there IS a track, proceed to draw.
            # Ensure album art is ready if we have a URL and no image.
            if self.current_track_info and self.current_track_info.get('album_art_url') and not self.album_art_image:
                logger.debug("MusicManager.display: Music active, track info exists, but no album art image. Fetching.")
                self._fetch_album_art(self.current_track_info['album_art_url'])
            # Fall through to the drawing logic.
        
        # At this point, current_track_info might be None or populated.
        # display_manager.draw_music_display should be robust to this.
        
        title = self.current_track_info.get('title', "Nothing Playing") if self.current_track_info else "Nothing Playing"
        artist = self.current_track_info.get('artist', "") if self.current_track_info else ""
        album = self.current_track_info.get('album', "") if self.current_track_info else ""
        is_playing = self.current_track_info.get('is_playing', False) if self.current_track_info else False
        
        # Use the album art image fetched and stored in self.album_art_image
        # The _fetch_album_art method now updates self.album_art_image directly.

        logger.debug(f"MusicManager.display: Title='{title}', Artist='{artist}', Album='{album}', Playing={is_playing}, ForceClear={force_clear}, HasAlbumArtImage={self.album_art_image is not None}")

        # Scrolling logic remains largely the same
        # but now it's part of MusicManager

        # Max text lengths from config (example values, should be in config.json ideally)
        # These are character counts, not pixel widths, for simplicity here.
        # Real pixel-based truncation/scrolling is better handled by DisplayManager drawing functions.
        max_title_len = self.config.get('music', {}).get('display_max_title_length', 12) # e.g., 12 chars fit
        max_artist_len = self.config.get('music', {}).get('display_max_artist_length', 15)

        # Update scroll positions
        tick_speed_title = 5  # Lower is faster scroll (ticks per character shift)
        tick_speed_artist = 5

        self.title_scroll_tick = (self.title_scroll_tick + 1)
        if self.title_scroll_tick >= tick_speed_title:
            self.title_scroll_tick = 0
            self.scroll_position_title = (self.scroll_position_title + 1) % (len(title) + max_title_len if len(title) > max_title_len else 1) # Loop scroll

        self.artist_scroll_tick = (self.artist_scroll_tick + 1)
        if self.artist_scroll_tick >= tick_speed_artist:
            self.artist_scroll_tick = 0
            self.scroll_position_artist = (self.scroll_position_artist + 1) % (len(artist) + max_artist_len if len(artist) > max_artist_len else 1) # Loop scroll

        # Let DisplayManager handle the actual drawing including text scrolling and image placement
        self.display_manager.draw_music_display(
            track_info=self.current_track_info, # Pass the whole dict
            album_art_image=self.album_art_image,
            scroll_position_title=self.scroll_position_title,
            scroll_position_artist=self.scroll_position_artist,
            force_clear=force_clear # Pass this through
        )

    def trigger_immediate_poll_and_update(self):
        """
        Manually triggers a poll, processes the result, and calls the update_callback
        if the music display is active. This is primarily for when the display
        switches TO music mode, to ensure fresh data is shown immediately.
        """
        logger.debug("MusicManager: trigger_immediate_poll_and_update called.")
        # Perform a poll (this will internally use preferred_source logic)
        # The poll_music_status method will call _is_new_track and handle updating
        # self.current_track_info and self.current_source.

        previous_track_info = self.current_track_info # Store before poll
        
        # Simplified: just call poll_music_status. It now has the logic
        # to check if display is active before calling the main update_callback.
        self.poll_music_status(manual_trigger=True) # Pass a flag if poll_music_status needs it (not currently)

        # After polling, if the display is active and the track *did* change OR if there was no art,
        # ensure the display gets updated.
        # The poll_music_status itself will call the update_callback if active.
        # Here, we just need to ensure art is fetched if needed for immediate display.
        if self.is_music_display_active_callback and self.is_music_display_active_callback():
            newly_updated_track_info = self.current_track_info # Get current state after poll
            
            # If track has changed, or if there's no album art yet for the current track
            if self._is_new_track(newly_updated_track_info, old_track_info=previous_track_info) or \
               (newly_updated_track_info and newly_updated_track_info.get('album_art_url') and not self.album_art_image):
                
                logger.debug("MusicManager (trigger_immediate_poll): Display active. Track changed or art missing. Ensuring display update.")
                
                if newly_updated_track_info and newly_updated_track_info.get('album_art_url') and \
                   (not self.album_art_image or self.last_album_art_url != newly_updated_track_info['album_art_url']):
                    logger.debug("MusicManager (trigger_immediate_poll): Fetching album art.")
                    self._fetch_album_art(newly_updated_track_info['album_art_url'])
                
                # The main update_callback would have been called by poll_music_status if active and track changed.
                # If only art was fetched for an existing track, DisplayController might not know.
                # However, the next call to self.display() will use the new art.
                # If an immediate redraw is desired even if track didn't change but art was just fetched,
                # DisplayController might need another hint or self.display() needs to be called.
                # For now, let's assume poll_music_status handles the callback correctly,
                # and self.display() will pick up the new art on its next call.

                # To be absolutely sure DisplayController gets the latest, even if only art changed:
                # if self.update_callback:
                #    self.update_callback(self.current_track_info) # Potentially redundant if poll_music_status did it
                pass # Logic within poll_music_status and _handle_ytm_update should cover callbacks

    def _is_new_track(self, new_track_info, old_track_info=None):
        """
        Checks if a new track is different from an old track.
        Returns True if the track is new, False if it's the same or no old track is provided.
        """
        if not old_track_info:
            return True
        return (
            new_track_info.get('source') != old_track_info.get('source') or
            new_track_info.get('title') != old_track_info.get('title') or
            new_track_info.get('artist') != old_track_info.get('artist') or
            new_track_info.get('album') != old_track_info.get('album') or
            new_track_info.get('album_art_url') != old_track_info.get('album_art_url') or
            new_track_info.get('duration_ms') != old_track_info.get('duration_ms') or
            new_track_info.get('progress_ms') != old_track_info.get('progress_ms') or
            new_track_info.get('is_playing') != old_track_info.get('is_playing')
        )

    def _fetch_album_art(self, url):
        """Fetches and updates the album art image."""
        if not url:
            return
        try:
            self.album_art_image = self._fetch_and_resize_image(url, (self.display_manager.matrix.width, self.display_manager.matrix.height))
            if self.album_art_image:
                logger.info("MusicManager: Album art fetched and processed successfully.")
            else:
                logger.warning("MusicManager: Failed to fetch or process album art.")
        except Exception as e:
            logger.error(f"Error fetching album art: {e}")

    def poll_music_status(self, manual_trigger=False):
        """
        Manually triggers a poll, processes the result, and calls the update_callback
        if the music display is active. This is primarily for when the display
        switches TO music mode, to ensure fresh data is shown immediately.
        """
        logger.debug("MusicManager: poll_music_status called.")
        # Perform a poll (this will internally use preferred_source logic)
        # The poll_music_data method will call _is_new_track and handle updating
        # self.current_track_info and self.current_source.

        previous_track_info = self.current_track_info # Store before poll
        
        # Simplified: just call poll_music_data. It now has the logic
        # to check if display is active before calling the main update_callback.
        self._poll_music_data() # Pass a flag if poll_music_data needs it (not currently)

        # After polling, if the display is active and the track *did* change OR if there was no art,
        # ensure the display gets updated.
        # The poll_music_data itself will call the update_callback if active.
        # Here, we just need to ensure art is fetched if needed for immediate display.
        if self.is_music_display_active_callback and self.is_music_display_active_callback():
            newly_updated_track_info = self.current_track_info # Get current state after poll
            
            # If track has changed, or if there's no album art yet for the current track
            if self._is_new_track(newly_updated_track_info, old_track_info=previous_track_info) or \
               (newly_updated_track_info and newly_updated_track_info.get('album_art_url') and not self.album_art_image):
                
                logger.debug("MusicManager (poll_music_status): Display active. Track changed or art missing. Ensuring display update.")
                
                if newly_updated_track_info and newly_updated_track_info.get('album_art_url') and \
                   (not self.album_art_image or self.last_album_art_url != newly_updated_track_info['album_art_url']):
                    logger.debug("MusicManager (poll_music_status): Fetching album art.")
                    self._fetch_album_art(newly_updated_track_info['album_art_url'])
                
                # The main update_callback would have been called by poll_music_data if active and track changed.
                # If only art was fetched for an existing track, DisplayController might not know.
                # However, the next call to self.display() will use the new art.
                # If an immediate redraw is desired even if track didn't change but art was just fetched,
                # DisplayController might need another hint or self.display() needs to be called.
                # For now, let's assume poll_music_data handles the callback correctly,
                # and self.display() will pick up the new art on its next call.

                # To be absolutely sure DisplayController gets the latest, even if only art changed:
                # if self.update_callback:
                #    self.update_callback(self.current_track_info) # Potentially redundant if poll_music_data did it
                pass # Logic within poll_music_data and _handle_ytm_update should cover callbacks


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
    # However, MusicManager's _load_config reads from CONFIG_PATH.
    # For a true standalone test, we might need to mock file IO or provide a test config file.

    # Simplified test:
    # manager = MusicManager(display_manager=mock_display, config=mock_config_main) # This won't work due to file reading
    
    # To truly test, you'd point CONFIG_PATH to a test config.json or mock open()
    # For now, this __main__ block is mostly a placeholder.
    logger.info("MusicManager standalone test setup is complex due to file dependencies for config.")
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