import time
import logging
import requests
import json
import random
from typing import Dict, Any, List, Tuple
from datetime import datetime
import os
import urllib.parse
import re
from src.config_manager import ConfigManager
from PIL import Image, ImageDraw
from .cache_manager import CacheManager
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# Import the API counter function from web interface
try:
    from web_interface_v2 import increment_api_counter
except ImportError:
    # Fallback if web interface is not available
    def increment_api_counter(kind: str, count: int = 1):
        pass

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class StockNewsManager:
    def __init__(self, config: Dict[str, Any], display_manager):
        self.config = config
        # Store reference to config instead of creating new ConfigManager
        self.config_manager = None  # Not used in this class
        self.display_manager = display_manager
        self.stocks_config = config.get('stocks', {})
        self.stock_news_config = config.get('stock_news', {})
        self.last_update = 0
        self.news_data = {}
        self.current_news_group = 0  # Track which group of headlines we're showing
        self.scroll_position = 0
        self.cached_text_image = None  # Cache for the text image
        self.cached_text = None  # Cache for the text string
        self.cache_manager = CacheManager()
        
        # Get scroll settings from config with faster defaults
        self.scroll_speed = self.stock_news_config.get('scroll_speed', 1)
        self.scroll_delay = self.stock_news_config.get('scroll_delay', 0.01)  # Default to 10ms for 100 FPS
        
        # Get headline settings from config
        self.max_headlines_per_symbol = self.stock_news_config.get('max_headlines_per_symbol', 1)
        self.headlines_per_rotation = self.stock_news_config.get('headlines_per_rotation', 2)
        
        # Dynamic duration settings
        self.dynamic_duration_enabled = self.stock_news_config.get('dynamic_duration', True)
        self.min_duration = self.stock_news_config.get('min_duration', 30)
        self.max_duration = self.stock_news_config.get('max_duration', 300)
        self.duration_buffer = self.stock_news_config.get('duration_buffer', 0.1)
        self.dynamic_duration = 60  # Default duration in seconds
        self.total_scroll_width = 0  # Track total width for dynamic duration calculation
        
        # Log the actual values being used
        logger.info(f"Scroll settings - Speed: {self.scroll_speed} pixels/frame, Delay: {self.scroll_delay*1000:.2f}ms")
        logger.info(f"Headline settings - Max per symbol: {self.max_headlines_per_symbol}, Per rotation: {self.headlines_per_rotation}")
        
        # Initialize frame rate tracking
        self.frame_count = 0
        self.last_frame_time = time.time()
        self.last_fps_log_time = time.time()
        self.frame_times = []  # Keep track of recent frame times for average FPS
        
        # Background image generation
        self.background_image = None  # The image being generated in background
        self.is_generating_image = False  # Flag to track if we're currently generating
        self.last_generation_start = 0  # When we started generating
        self.generation_timeout = 5  # Max seconds to spend generating
        
        # Rotation tracking
        self.all_news_items = []  # Store all available news items
        self.current_rotation_index = 0  # Track which rotation we're on
        self.rotation_complete = False  # Flag to indicate when a full rotation is complete
        
        self.headers = {
            'User-Agent': 'LEDMatrix/1.0 (https://github.com/yourusername/LEDMatrix; contact@example.com)',
            'Accept': 'application/json',
            'Accept-Language': 'en-US,en;q=0.9',
            'Accept-Encoding': 'gzip, deflate, br',
            'Connection': 'keep-alive'
        }
        
        # Set up session with retry logic
        self.session = requests.Session()
        retry_strategy = Retry(
            total=5,  # increased number of retries
            backoff_factor=1,  # increased backoff factor
            status_forcelist=[429, 500, 502, 503, 504],  # added 429 to retry list
            allowed_methods=["GET", "HEAD", "OPTIONS"]
        )
        adapter = HTTPAdapter(max_retries=retry_strategy)
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)
        
        # Initialize with first update
        self.update_news_data()
        
    def _fetch_news(self, symbol: str) -> List[Dict[str, Any]]:
        """Fetch news data for a stock from Yahoo Finance."""
        try:
            # Use Yahoo Finance query1 API for news data
            url = f"https://query1.finance.yahoo.com/v1/finance/search"
            params = {
                'q': symbol,
                'lang': 'en-US',
                'region': 'US',
                'quotesCount': 0,
                'newsCount': 10,
                'enableFuzzyQuery': False,
                'quotesQueryId': 'tss_match_phrase_query',
                'multiQuoteQueryId': 'multi_quote_single_token_query',
                'newsQueryId': 'news_cie_vespa',
                'enableCb': True,
            }
            
            # Use session with retry logic
            response = self.session.get(
                url,
                headers=self.headers,
                params=params,
                timeout=10,  # Increased timeout
                verify=True  # Enable SSL verification
            )
            
            if response.status_code != 200:
                logger.error(f"Failed to fetch news for {symbol}: HTTP {response.status_code}")
                return []
                
            data = response.json()
            
            # Increment API counter for news data call
            increment_api_counter('news', 1)
            news_items = data.get('news', [])
            
            processed_news = []
            for item in news_items:
                try:
                    processed_news.append({
                        'title': item.get('title', ''),
                        'link': item.get('link', ''),
                        'publisher': item.get('publisher', ''),
                        'published': datetime.fromtimestamp(item.get('providerPublishTime', 0)),
                        'summary': item.get('summary', '')
                    })
                except (ValueError, TypeError) as e:
                    logger.error(f"Error processing news item for {symbol}: {e}")
                    continue
            
            logger.debug(f"Fetched {len(processed_news)} news items for {symbol}")
            return processed_news
            
        except requests.exceptions.SSLError as e:
            logger.error(f"SSL error fetching news for {symbol}: {e}")
            return []
        except requests.exceptions.RequestException as e:
            logger.error(f"Network error fetching news for {symbol}: {e}")
            return []
        except (ValueError, KeyError) as e:
            logger.error(f"Error parsing news data for {symbol}: {e}")
            return []
        except Exception as e:
            logger.error(f"Unexpected error fetching news for {symbol}: {e}")
            return []
            
    def update_news_data(self):
        """Update news data from API."""
        current_time = time.time()
        update_interval = self.stock_news_config.get('update_interval', 3600)
        
        # Check if we're currently scrolling and defer the update if so
        if self.display_manager.is_currently_scrolling():
            logger.debug("Stock news display is currently scrolling, deferring update")
            self.display_manager.defer_update(self._perform_news_update, priority=2)
            return
            
        self._perform_news_update()

    def _perform_news_update(self):
        """Internal method to perform the actual news update."""
        current_time = time.time()
        update_interval = self.stock_news_config.get('update_interval', 3600)
        
        if current_time - self.last_update < update_interval:
            return
            
        try:
            logger.debug("Updating stock news data")
            symbols = self.stocks_config.get('symbols', [])
            
            if not symbols:
                logger.warning("No stock symbols configured for news")
                return
                
            # Fetch news for each symbol
            for symbol in symbols:
                try:
                    news = self._fetch_news(symbol)
                    if news:
                        self.news_data[symbol] = news
                        logger.debug(f"Updated news for {symbol}: {len(news)} headlines")
                except Exception as e:
                    logger.error(f"Error fetching news for {symbol}: {e}")
                    
            self.last_update = current_time
            
            # Clear cached text to force regeneration
            self.cached_text = None
            self.cached_text_image = None
            
        except Exception as e:
            logger.error(f"Error updating stock news data: {e}")
            
    def _create_text_image(self, text: str, color: Tuple[int, int, int] = (255, 255, 255)) -> Image.Image:
        """Create an image containing the text for efficient scrolling."""
        # Get text dimensions
        bbox = self.display_manager.draw.textbbox((0, 0), text, font=self.display_manager.small_font)
        text_width = bbox[2] - bbox[0]
        text_height = bbox[3] - bbox[1]
        
        # Create a new image with the text
        text_image = Image.new('RGB', (text_width, self.display_manager.matrix.height), (0, 0, 0))
        text_draw = ImageDraw.Draw(text_image)
        
        # Draw the text centered vertically
        y = (self.display_manager.matrix.height - text_height) // 2
        text_draw.text((0, y), text, font=self.display_manager.small_font, fill=color)
        
        return text_image
            
    def _log_frame_rate(self):
        """Log frame rate statistics."""
        current_time = time.time()
        
        # Calculate instantaneous frame time
        frame_time = current_time - self.last_frame_time
        self.frame_times.append(frame_time)
        
        # Keep only last 100 frames for average
        if len(self.frame_times) > 100:
            self.frame_times.pop(0)
        
        # Log FPS every second
        if current_time - self.last_fps_log_time >= 1.0:
            avg_frame_time = sum(self.frame_times) / len(self.frame_times)
            avg_fps = 1.0 / avg_frame_time if avg_frame_time > 0 else 0
            instant_fps = 1.0 / frame_time if frame_time > 0 else 0
            
            logger.info(f"Frame stats - Avg FPS: {avg_fps:.1f}, Current FPS: {instant_fps:.1f}, Frame time: {frame_time*1000:.2f}ms")
            self.last_fps_log_time = current_time
            self.frame_count = 0
        
        self.last_frame_time = current_time
        self.frame_count += 1

    def _generate_background_image(self, all_news, width, height):
        """Generate the full image in the background without disrupting display."""
        if self.is_generating_image:
            # Check if we've been generating too long
            if time.time() - self.last_generation_start > self.generation_timeout:
                logger.warning("[StockNews] Background image generation timed out, resetting")
                self.is_generating_image = False
                self.background_image = None
                return False
                
            # Still generating, return False to indicate not ready
            return False
            
        # Start a new background generation
        self.is_generating_image = True
        self.last_generation_start = time.time()
        
        try:
            # Log the number of headlines being displayed
            logger.info(f"[StockNews] Generating image for {len(all_news)} headlines")
            
            # First, create all news images to calculate total width needed
            news_images = []
            total_width = 0
            screen_width_gap = width  # Use a full screen width as the gap
            
            # Add initial gap
            total_width += screen_width_gap
            
            for news in all_news:
                news_text = f"{news['symbol']}: {news['title']}   "
                news_image = self._create_text_image(news_text)
                news_images.append(news_image)
                # Add width of news image plus gap
                total_width += news_image.width + screen_width_gap
            
            # Create the full image with calculated width
            full_image = Image.new('RGB', (total_width, height), (0, 0, 0))
            draw = ImageDraw.Draw(full_image)
            
            # Now paste all news images with proper spacing
            current_x = screen_width_gap  # Start after initial gap
            
            for news_image in news_images:
                # Paste this news image into the full image
                full_image.paste(news_image, (current_x, 0))
                
                # Move to next position: text width + screen width gap
                current_x += news_image.width + screen_width_gap

            # Store the generated image
            self.background_image = full_image
            self.is_generating_image = False
            return True
            
        except Exception as e:
            logger.error(f"[StockNews] Error generating background image: {e}")
            self.is_generating_image = False
            return False

    def display_news(self):
        """Display news headlines by scrolling them across the screen."""
        if not self.stock_news_config.get('enabled', False):
            return
            
        # Start update in background if needed
        if time.time() - self.last_update >= self.stock_news_config.get('update_interval', 300):
            self.update_news_data()
            
        if not self.news_data:
            logger.warning("No news data available to display")
            return
            
        # Get all news items from all symbols, respecting max_headlines_per_symbol
        if not self.all_news_items:
            self.all_news_items = []
            for symbol, news_items in self.news_data.items():
                # Limit the number of headlines per symbol
                limited_items = news_items[:self.max_headlines_per_symbol]
                for item in limited_items:
                    self.all_news_items.append({
                        "symbol": symbol,
                        "title": item["title"],
                        "publisher": item["publisher"]
                    })
            
            # Shuffle the news items for variety
            random.shuffle(self.all_news_items)
            logger.info(f"Prepared {len(self.all_news_items)} news items for rotation")
                
        if not self.all_news_items:
            return

        # Get the current rotation of headlines
        start_idx = self.current_rotation_index * self.headlines_per_rotation
        end_idx = min(start_idx + self.headlines_per_rotation, len(self.all_news_items))
        
        # If we've reached the end, shuffle and start over
        if start_idx >= len(self.all_news_items):
            self.current_rotation_index = 0
            random.shuffle(self.all_news_items)  # Reshuffle when we've shown all headlines
            start_idx = 0
            end_idx = min(self.headlines_per_rotation, len(self.all_news_items))
            self.rotation_complete = True
            logger.info("Completed a full rotation of news headlines, reshuffling for next round")
        
        # Get the current batch of headlines
        current_news = self.all_news_items[start_idx:end_idx]
        
        # Define width and height here, so they are always available
        width = self.display_manager.matrix.width
        height = self.display_manager.matrix.height

        # Check if we need to generate a new image
        if self.cached_text_image is None or self.rotation_complete:
            # Reset rotation complete flag
            self.rotation_complete = False
            
            # Try to generate the image in the background
            if self._generate_background_image(current_news, width, height):
                # If generation completed successfully, use the background image
                self.cached_text_image = self.background_image
                self.scroll_position = 0
                self.background_image = None  # Clear the background image
                
                # Calculate total scroll width for dynamic duration
                self.total_scroll_width = self.cached_text_image.width
                self.calculate_dynamic_duration()
                
                # Move to next rotation for next time
                self.current_rotation_index += 1
            else:
                # If still generating or failed, show a simple message
                self.display_manager.image.paste(Image.new('RGB', (width, height), (0, 0, 0)), (0, 0))
                draw = ImageDraw.Draw(self.display_manager.image)
                draw.text((width//4, height//2), "Loading news...", font=self.display_manager.small_font, fill=(255, 255, 255))
                self.display_manager.update_display()
                # Removed sleep delay to improve scrolling performance
                return True
        
        # --- Scrolling logic remains the same --- 
        if self.cached_text_image is None:
            logger.warning("[StockNews] Cached image is None, cannot scroll.")
            return False
            
        total_width = self.cached_text_image.width
        
        # If total_width is somehow less than screen width, don't scroll
        if total_width <= width:
            # Signal that we're not scrolling for this frame
            self.display_manager.set_scrolling_state(False)
            # Process any deferred updates
            self.display_manager.process_deferred_updates()
            
            self.display_manager.image.paste(self.cached_text_image, (0, 0))
            self.display_manager.update_display()
            time.sleep(self.stock_news_config.get('item_display_duration', 5)) # Hold static image
            self.cached_text_image = None # Force recreation next cycle
            return True

        # Signal that we're scrolling
        self.display_manager.set_scrolling_state(True)
        
        # Process any deferred updates (though news is usually always scrolling)
        self.display_manager.process_deferred_updates()

        # Update scroll position
        self.scroll_position += self.scroll_speed
        if self.scroll_position >= total_width:
            self.scroll_position = 0 # Wrap around
            # When we wrap around, move to next rotation
            self.cached_text_image = None
            return True

        # Calculate the visible portion
        # Handle wrap-around drawing
        visible_end = self.scroll_position + width
        if visible_end <= total_width:
            # Normal case: Paste single crop
            visible_portion = self.cached_text_image.crop((
                self.scroll_position, 0,
                visible_end, height
            ))
            self.display_manager.image.paste(visible_portion, (0, 0))
        else:
            # Wrap-around case: Paste two parts
            width1 = total_width - self.scroll_position
            width2 = width - width1
            portion1 = self.cached_text_image.crop((self.scroll_position, 0, total_width, height))
            portion2 = self.cached_text_image.crop((0, 0, width2, height))
            self.display_manager.image.paste(portion1, (0, 0))
            self.display_manager.image.paste(portion2, (width1, 0))

        self.display_manager.update_display()
        self._log_frame_rate()
        time.sleep(self.scroll_delay)
        return True

    def calculate_dynamic_duration(self):
        """Calculate the exact time needed to display all news headlines"""
        # If dynamic duration is disabled, use fixed duration from config
        if not self.dynamic_duration_enabled:
            self.dynamic_duration = self.stock_news_config.get('fixed_duration', 60)
            logger.debug(f"Dynamic duration disabled, using fixed duration: {self.dynamic_duration}s")
            return
            
        if not self.total_scroll_width:
            self.dynamic_duration = self.min_duration  # Use configured minimum
            return
            
        try:
            # Get display width (assume full width of display)
            display_width = getattr(self.display_manager, 'width', 128)  # Default to 128 if not available
            
            # Calculate total scroll distance needed
            # Text needs to scroll from right edge to completely off left edge
            total_scroll_distance = display_width + self.total_scroll_width
            
            # Calculate time based on scroll speed and delay
            # scroll_speed = pixels per frame, scroll_delay = seconds per frame
            frames_needed = total_scroll_distance / self.scroll_speed
            total_time = frames_needed * self.scroll_delay
            
            # Add buffer time for smooth cycling (configurable %)
            buffer_time = total_time * self.duration_buffer
            calculated_duration = int(total_time + buffer_time)
            
            # Apply configured min/max limits
            if calculated_duration < self.min_duration:
                self.dynamic_duration = self.min_duration
                logger.debug(f"Duration capped to minimum: {self.min_duration}s")
            elif calculated_duration > self.max_duration:
                self.dynamic_duration = self.max_duration
                logger.debug(f"Duration capped to maximum: {self.max_duration}s")
            else:
                self.dynamic_duration = calculated_duration
                
            logger.debug(f"Stock news dynamic duration calculation:")
            logger.debug(f"  Display width: {display_width}px")
            logger.debug(f"  Text width: {self.total_scroll_width}px")
            logger.debug(f"  Total scroll distance: {total_scroll_distance}px")
            logger.debug(f"  Frames needed: {frames_needed:.1f}")
            logger.debug(f"  Base time: {total_time:.2f}s")
            logger.debug(f"  Buffer time: {buffer_time:.2f}s ({self.duration_buffer*100}%)")
            logger.debug(f"  Calculated duration: {calculated_duration}s")
            logger.debug(f"  Final duration: {self.dynamic_duration}s")
            
        except Exception as e:
            logger.error(f"Error calculating dynamic duration: {e}")
            self.dynamic_duration = self.min_duration  # Use configured minimum as fallback

    def get_dynamic_duration(self) -> int:
        """Get the calculated dynamic duration for display"""
        return self.dynamic_duration 