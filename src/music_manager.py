import time
import threading
from enum import Enum, auto
import logging
import json
import os
from io import BytesIO
import requests
from PIL import Image, ImageEnhance

# Use relative imports for clients within the same package (src)
from .spotify_client import SpotifyClient
from .ytm_client import YTMClient
# Removed: import config

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Define paths relative to this file's location
CONFIG_DIR = os.path.join(os.path.dirname(__file__), '..', 'config')
CONFIG_PATH = os.path.join(CONFIG_DIR, 'config.json')
# SECRETS_PATH is handled within SpotifyClient

class MusicSource(Enum):
    NONE = auto()
    SPOTIFY = auto()
    YTM = auto()

class MusicManager:
    def __init__(self, display_manager, config, update_callback=None, display_controller_obj=None):
        self.display_manager = display_manager
        self.display_controller_obj = display_controller_obj
        self.config = config
        self.spotify = None
        self.ytm = None
        self.current_track_info = None
        self.current_source = MusicSource.NONE
        self.update_callback = update_callback
        self.polling_interval = 1 # Default
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
        default_interval = 1 # Default polling interval set to 1 second
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
                    # The SpotifyClient will log more details if cache loading failed.
                    # No need to attempt auth URL generation here.
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
                self.ytm = YTMClient()
                if not self.ytm.is_available():
                    logging.warning(f"YTM Companion server not reachable at {self.ytm.base_url}. YTM features disabled.")
                    self.ytm = None
                else:
                    logging.info(f"YTM Companion server connected at {self.ytm.base_url}.")
            except Exception as e:
                logging.error(f"Failed to initialize YTM client: {e}")
                self.ytm = None
        else:
            logging.info("YTM client initialization skipped due to preferred_source setting.")
            self.ytm = None

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
            final_track_info_for_update = self.get_simplified_track_info(None, MusicSource.NONE)
            polled_source_for_update = MusicSource.NONE
            
            # Determine if the music display is currently active
            music_display_is_active = False
            if self.display_controller_obj and hasattr(self.display_controller_obj, 'is_display_active'):
                music_display_is_active = self.display_controller_obj.is_display_active("music")
            else:
                # If display_controller_obj is not available (e.g., testing), assume display might be active for polling purposes.
                # Or, could default to False if strictness is required.
                # For now, let's assume it could be active to allow polling in tests without full controller.
                music_display_is_active = True 
                logging.debug("Poll Loop: display_controller_obj not available, assuming music display potentially active for polling.")

            if self.preferred_source == "spotify":
                if self.spotify and self.spotify.is_authenticated():
                    try:
                        spotify_track_data = self.spotify.get_current_track()
                        if spotify_track_data and spotify_track_data.get('is_playing'):
                            final_track_info_for_update = self.get_simplified_track_info(spotify_track_data, MusicSource.SPOTIFY)
                            polled_source_for_update = MusicSource.SPOTIFY
                            logging.debug(f"Polling Spotify (preferred): Active track - {spotify_track_data.get('item', {}).get('name')}")
                        else:
                            logging.debug("Polling Spotify (preferred): No active track or player paused.")
                    except Exception as e:
                        logging.error(f"Error polling Spotify (preferred): {e}")
                else:
                    logging.debug("Spotify is preferred source, but client not available/authenticated.")
            
            elif self.preferred_source == "ytm":
                if music_display_is_active: # Only poll YTM if its display is active
                    if self.ytm and self.ytm.is_available():
                        try:
                            ytm_track_data = self.ytm.get_current_track()
                            if ytm_track_data:
                                player_info = ytm_track_data.get('player', {})
                                is_actually_playing_ytm = (player_info.get('trackState') == 1) and not player_info.get('adPlaying', False)
                                if is_actually_playing_ytm:
                                    final_track_info_for_update = self.get_simplified_track_info(ytm_track_data, MusicSource.YTM)
                                    polled_source_for_update = MusicSource.YTM
                                    logging.debug(f"Polling YTM (preferred, display active): Active track - {ytm_track_data.get('track', {}).get('title')}")
                                else:
                                    logging.debug("Polling YTM (preferred, display active): Track data present but player not in active playing state.")
                            else:
                                logging.debug("Polling YTM (preferred, display active): No track data from YTM client.")
                        except Exception as e:
                            logging.error(f"Error polling YTM (preferred, display active): {e}")
                    else:
                        logging.debug("YTM is preferred, music display active, but YTM client not available.")
                else:
                    logging.debug("YTM is preferred, but music display NOT active. Skipping YTM poll.")
                    # If YTM was the last source, and display is no longer active, ensure we don't show stale YTM info indefinitely
                    if self.current_source == MusicSource.YTM:
                        # final_track_info_for_update is already "Nothing Playing" by default, which is desired here.
                        polled_source_for_update = MusicSource.NONE # Explicitly mark that no source was found active in this poll cycle
                        logging.debug("YTM was current source, but display no longer active. Reverting to Nothing Playing state for this cycle.")

            elif self.preferred_source == "auto":
                spotify_active = False
                if self.spotify and self.spotify.is_authenticated():
                    try:
                        spotify_track_data = self.spotify.get_current_track()
                        if spotify_track_data and spotify_track_data.get('is_playing'):
                            final_track_info_for_update = self.get_simplified_track_info(spotify_track_data, MusicSource.SPOTIFY)
                            polled_source_for_update = MusicSource.SPOTIFY
                            spotify_active = True
                            logging.debug(f"Polling (auto mode) Spotify: Active track - {spotify_track_data.get('item', {}).get('name')}")
                        else:
                            logging.debug("Polling (auto mode) Spotify: No active track or player paused.")
                    except Exception as e:
                        logging.error(f"Error polling Spotify (auto mode): {e}")
                
                if not spotify_active:
                    if music_display_is_active: # Only poll YTM if its display is active and Spotify wasn't playing
                        if self.ytm and self.ytm.is_available():
                            try:
                                ytm_track_data = self.ytm.get_current_track()
                                if ytm_track_data:
                                    player_info = ytm_track_data.get('player', {})
                                    is_actually_playing_ytm = (player_info.get('trackState') == 1) and not player_info.get('adPlaying', False)
                                    if is_actually_playing_ytm:
                                        final_track_info_for_update = self.get_simplified_track_info(ytm_track_data, MusicSource.YTM)
                                        polled_source_for_update = MusicSource.YTM
                                        logging.debug(f"Polling (auto mode, display active) YTM: Active track - {ytm_track_data.get('track', {}).get('title')}")
                                    else:
                                        logging.debug("Polling (auto mode, display active) YTM: Track data present but player not in active playing state.")
                                else:
                                    logging.debug("Polling (auto mode, display active) YTM: No track data from YTM client.")
                            except Exception as e:
                                logging.error(f"Error polling YTM (auto mode, display active): {e}")
                        else:
                            logging.debug("Auto mode, Spotify not active, YTM music display active, but YTM client not available.")
                    else:
                        logging.debug("Auto mode, Spotify not active, music display NOT active. Skipping YTM poll.")
                        # If YTM was the last source, and display is no longer active, ensure we don't show stale YTM info indefinitely
                        if self.current_source == MusicSource.YTM:
                            polled_source_for_update = MusicSource.NONE # Explicitly mark that no source was found active for this cycle
                            logging.debug("YTM was current source (auto mode), but display no longer active. Reverting to Nothing Playing state for this cycle.")
            
            # 2. Check for changes and update state
            has_changed = False
            if final_track_info_for_update != self.current_track_info or polled_source_for_update != self.current_source:
                has_changed = True
                old_album_art_url = self.current_track_info.get('album_art_url') if self.current_track_info else None
                
                self.current_track_info = final_track_info_for_update
                self.current_source = polled_source_for_update

                new_album_art_url = self.current_track_info.get('album_art_url')
                if new_album_art_url != old_album_art_url:
                    self.album_art_image = None
                    self.last_album_art_url = new_album_art_url
                
                display_title = self.current_track_info.get('title', 'None')
                is_playing_status = self.current_track_info.get('is_playing', False)
                logger.debug(f"Poll Loop: Music state updated. Source: {self.current_source.name}. Track: {display_title}. Playing: {is_playing_status}")
            else:
                logger.debug(f"Poll Loop: No change from poll. Current source: {self.current_source.name}, Track: {self.current_track_info.get('title', 'None') if self.current_track_info else 'None'}")

            # 3. Callback if changed and display is active
            if has_changed and self.update_callback:
                try:
                    should_callback = True
                    if self.display_controller_obj and hasattr(self.display_controller_obj, 'is_display_active'):
                        if not self.display_controller_obj.is_display_active("music"):
                            logger.debug("Poll Loop: Music display not active. Suppressing update_callback.")
                            should_callback = False
                    
                    if should_callback:
                        self.update_callback(self.current_track_info)
                except Exception as e:
                    logger.error(f"Error executing update callback from poll loop: {e}")
            
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

    # Method moved from DisplayController and renamed
    def display(self, force_clear: bool = False):
        if force_clear: # Removed self.force_clear as it's passed directly
            self.display_manager.clear()
            # self.force_clear = False # Not needed here

        # Use self.current_track_info which is updated by _poll_music_data
        display_info = self.current_track_info 

        if not display_info or not display_info.get('is_playing', False) or display_info.get('title') == 'Nothing Playing':
            # Debounce "Nothing playing" log for this manager
            if not hasattr(self, '_last_nothing_playing_log_time') or \
               time.time() - getattr(self, '_last_nothing_playing_log_time', 0) > 30:
                logger.info("Music Screen (MusicManager): Nothing playing or info unavailable.")
                self._last_nothing_playing_log_time = time.time()
            
            self.display_manager.clear() # Clear before drawing "Nothing Playing"
            text_width = self.display_manager.get_text_width("Nothing Playing", self.display_manager.regular_font)
            x_pos = (self.display_manager.matrix.width - text_width) // 2
            y_pos = (self.display_manager.matrix.height // 2) - 4
            self.display_manager.draw_text("Nothing Playing", x=x_pos, y=y_pos, font=self.display_manager.regular_font)
            self.display_manager.update_display()
            self.scroll_position_title = 0
            self.scroll_position_artist = 0
            self.title_scroll_tick = 0 
            self.artist_scroll_tick = 0
            self.album_art_image = None # Clear album art if nothing is playing
            self.last_album_art_url = None # Also clear the URL
            return

        # Ensure screen is cleared if not force_clear but needed (e.g. transition from "Nothing Playing")
        # This might be handled by DisplayController's force_clear logic, but can be an internal check too.
        # For now, assuming DisplayController manages the initial clear for a new mode.
        self.display_manager.draw.rectangle([0, 0, self.display_manager.matrix.width, self.display_manager.matrix.height], fill=(0, 0, 0))


        # Album Art Configuration
        matrix_height = self.display_manager.matrix.height
        album_art_size = matrix_height - 2 
        album_art_target_size = (album_art_size, album_art_size)
        album_art_x = 1
        album_art_y = 1
        text_area_x_start = album_art_x + album_art_size + 2 
        text_area_width = self.display_manager.matrix.width - text_area_x_start - 1 

        # Fetch and display album art using self.last_album_art_url and self.album_art_image
        if self.last_album_art_url and not self.album_art_image:
            logger.info(f"MusicManager: Fetching album art from: {self.last_album_art_url}")
            self.album_art_image = self._fetch_and_resize_image(self.last_album_art_url, album_art_target_size)
            if self.album_art_image:
                 logger.info(f"MusicManager: Album art fetched and processed successfully.")
            else:
                logger.warning(f"MusicManager: Failed to fetch or process album art.")

        if self.album_art_image:
            self.display_manager.image.paste(self.album_art_image, (album_art_x, album_art_y))
        else:
            self.display_manager.draw.rectangle([album_art_x, album_art_y, 
                                                 album_art_x + album_art_size -1, album_art_y + album_art_size -1],
                                                 outline=(50,50,50), fill=(10,10,10))


        title = display_info.get('title', ' ')
        artist = display_info.get('artist', ' ')
        album = display_info.get('album', ' ') 

        font_title = self.display_manager.small_font
        font_artist_album = self.display_manager.bdf_5x7_font
        line_height_title = 8 
        line_height_artist_album = 7 
        padding_between_lines = 1 

        TEXT_SCROLL_DIVISOR = 5 

        # --- Title --- 
        y_pos_title = 2 
        title_width = self.display_manager.get_text_width(title, font_title)
        current_title_display_text = title
        if title_width > text_area_width:
            # Ensure scroll_position_title is valid for the current title length
            if self.scroll_position_title >= len(title):
                self.scroll_position_title = 0
            current_title_display_text = title[self.scroll_position_title:] + "   " + title[:self.scroll_position_title]
        
        self.display_manager.draw_text(current_title_display_text, 
                                     x=text_area_x_start, y=y_pos_title, color=(255, 255, 255), font=font_title)
        if title_width > text_area_width:
            self.title_scroll_tick += 1
            if self.title_scroll_tick % TEXT_SCROLL_DIVISOR == 0:
                self.scroll_position_title = (self.scroll_position_title + 1) % len(title)
                self.title_scroll_tick = 0 
        else:
            self.scroll_position_title = 0
            self.title_scroll_tick = 0

        # --- Artist --- 
        y_pos_artist = y_pos_title + line_height_title + padding_between_lines
        artist_width = self.display_manager.get_text_width(artist, font_artist_album)
        current_artist_display_text = artist
        if artist_width > text_area_width:
            # Ensure scroll_position_artist is valid for the current artist length
            if self.scroll_position_artist >= len(artist):
                self.scroll_position_artist = 0
            current_artist_display_text = artist[self.scroll_position_artist:] + "   " + artist[:self.scroll_position_artist]

        self.display_manager.draw_text(current_artist_display_text, 
                                      x=text_area_x_start, y=y_pos_artist, color=(180, 180, 180), font=font_artist_album)
        if artist_width > text_area_width:
            self.artist_scroll_tick += 1
            if self.artist_scroll_tick % TEXT_SCROLL_DIVISOR == 0:
                self.scroll_position_artist = (self.scroll_position_artist + 1) % len(artist)
                self.artist_scroll_tick = 0
        else:
            self.scroll_position_artist = 0
            self.artist_scroll_tick = 0
            
        # --- Album ---
        y_pos_album = y_pos_artist + line_height_artist_album + padding_between_lines
        if (matrix_height - y_pos_album - 5) >= line_height_artist_album : 
            album_width = self.display_manager.get_text_width(album, font_artist_album)
            if album_width <= text_area_width: 
                 self.display_manager.draw_text(album, x=text_area_x_start, y=y_pos_album, color=(150, 150, 150), font=font_artist_album)

        # --- Progress Bar --- 
        progress_bar_height = 3
        progress_bar_y = matrix_height - progress_bar_height - 1 
        duration_ms = display_info.get('duration_ms', 0)
        progress_ms = display_info.get('progress_ms', 0)

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