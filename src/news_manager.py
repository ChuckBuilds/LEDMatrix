import time
import logging
import requests
import xml.etree.ElementTree as ET
import json
import random
from typing import Dict, Any, List, Tuple, Optional
from datetime import datetime, timedelta
import os
import urllib.parse
import re
import html
from src.config_manager import ConfigManager
from PIL import Image, ImageDraw, ImageFont
from src.cache_manager import CacheManager
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class NewsManager:
    def __init__(self, config: Dict[str, Any], display_manager):
        self.config = config
        self.config_manager = ConfigManager()
        self.display_manager = display_manager
        self.news_config = config.get('news_manager', {})
        self.last_update = 0
        self.news_data = {}
        self.current_headline_index = 0
        self.scroll_position = 0
        self.cached_text_image = None
        self.cached_text = None
        self.cache_manager = CacheManager()
        self.current_headlines = []
        self.headline_start_times = []
        self.total_scroll_width = 0
        self.headlines_displayed = set()  # Track displayed headlines for rotation
        self.dynamic_duration = 60  # Default duration in seconds
        
        # Default RSS feeds
        self.default_feeds = {
            'MLB': 'http://espn.com/espn/rss/mlb/news',
            'NFL': 'http://espn.go.com/espn/rss/nfl/news', 
            'NCAA FB': 'https://www.espn.com/espn/rss/ncf/news',
            'NHL': 'https://www.espn.com/espn/rss/nhl/news',
            'NBA': 'https://www.espn.com/espn/rss/nba/news',
            'TOP SPORTS': 'https://www.espn.com/espn/rss/news',
            'BIG10': 'https://www.espn.com/blog/feed?blog=bigten',
            'NCAA': 'https://www.espn.com/espn/rss/ncaa/news',
            'Other': 'https://www.coveringthecorner.com/rss/current.xml'
        }
        
        # Get scroll settings from config
        self.scroll_speed = self.news_config.get('scroll_speed', 2)
        self.scroll_delay = self.news_config.get('scroll_delay', 0.02)
        self.update_interval = self.news_config.get('update_interval', 300)  # 5 minutes
        
        # Get headline settings from config
        self.headlines_per_feed = self.news_config.get('headlines_per_feed', 2)
        self.enabled_feeds = self.news_config.get('enabled_feeds', ['NFL', 'NCAA FB'])
        self.custom_feeds = self.news_config.get('custom_feeds', {})
        
        # Rotation settings
        self.rotation_enabled = self.news_config.get('rotation_enabled', True)
        self.rotation_threshold = self.news_config.get('rotation_threshold', 3)  # After 3 full cycles
        self.rotation_count = 0
        
        # Dynamic duration settings
        self.dynamic_duration_enabled = self.news_config.get('dynamic_duration', True)
        self.min_duration = self.news_config.get('min_duration', 30)
        self.max_duration = self.news_config.get('max_duration', 300)
        self.duration_buffer = self.news_config.get('duration_buffer', 0.1)
        
        # Font settings
        self.font_size = self.news_config.get('font_size', 12)
        self.font_path = self.news_config.get('font_path', '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf')
        
        # Colors
        self.text_color = tuple(self.news_config.get('text_color', [255, 255, 255]))
        self.separator_color = tuple(self.news_config.get('separator_color', [255, 0, 0]))
        
        # Initialize session with retry strategy
        self.session = requests.Session()
        retry_strategy = Retry(
            total=3,
            backoff_factor=1,
            status_forcelist=[429, 500, 502, 503, 504],
        )
        adapter = HTTPAdapter(max_retries=retry_strategy)
        self.session.mount("http://", adapter)
        self.session.mount("https://", adapter)
        
        logger.info(f"NewsManager initialized with feeds: {self.enabled_feeds}")
        logger.info(f"Headlines per feed: {self.headlines_per_feed}")
        logger.info(f"Scroll settings - Speed: {self.scroll_speed} pixels/frame, Delay: {self.scroll_delay*1000:.2f}ms")

    def parse_rss_feed(self, url: str, feed_name: str) -> List[Dict[str, Any]]:
        """Parse RSS feed and return list of headlines"""
        try:
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
            }
            
            response = self.session.get(url, headers=headers, timeout=10)
            response.raise_for_status()
            
            root = ET.fromstring(response.content)
            headlines = []
            
            # Handle different RSS formats
            items = root.findall('.//item')
            if not items:
                items = root.findall('.//entry')  # Atom feed format
                
            for item in items[:self.headlines_per_feed * 2]:  # Get extra to allow for filtering
                title_elem = item.find('title')
                if title_elem is not None:
                    title = html.unescape(title_elem.text or '').strip()
                    
                    # Clean up title
                    title = re.sub(r'<[^>]+>', '', title)  # Remove HTML tags
                    title = re.sub(r'\s+', ' ', title)     # Normalize whitespace
                    
                    if title and len(title) > 10:  # Filter out very short titles
                        pub_date_elem = item.find('pubDate')
                        if pub_date_elem is None:
                            pub_date_elem = item.find('published')  # Atom format
                            
                        pub_date = pub_date_elem.text if pub_date_elem is not None else None
                        
                        headlines.append({
                            'title': title,
                            'feed': feed_name,
                            'pub_date': pub_date,
                            'timestamp': datetime.now().isoformat()
                        })
                        
            logger.info(f"Parsed {len(headlines)} headlines from {feed_name}")
            return headlines[:self.headlines_per_feed]
            
        except Exception as e:
            logger.error(f"Error parsing RSS feed {feed_name} ({url}): {e}")
            return []

    def fetch_news_data(self):
        """Fetch news from all enabled feeds"""
        try:
            all_headlines = []
            
            # Combine default and custom feeds
            all_feeds = {**self.default_feeds, **self.custom_feeds}
            
            for feed_name in self.enabled_feeds:
                if feed_name in all_feeds:
                    url = all_feeds[feed_name]
                    headlines = self.parse_rss_feed(url, feed_name)
                    all_headlines.extend(headlines)
                else:
                    logger.warning(f"Feed '{feed_name}' not found in available feeds")
            
            # Store headlines by feed for rotation management
            self.news_data = {}
            for headline in all_headlines:
                feed = headline['feed']
                if feed not in self.news_data:
                    self.news_data[feed] = []
                self.news_data[feed].append(headline)
            
            # Prepare current headlines for display
            self.prepare_headlines_for_display()
            
            self.last_update = time.time()
            logger.info(f"Fetched {len(all_headlines)} total headlines from {len(self.enabled_feeds)} feeds")
            
        except Exception as e:
            logger.error(f"Error fetching news data: {e}")

    def prepare_headlines_for_display(self):
        """Prepare headlines for scrolling display with rotation"""
        if not self.news_data:
            return
            
        # Get headlines for display, applying rotation if enabled
        display_headlines = []
        
        for feed_name in self.enabled_feeds:
            if feed_name in self.news_data:
                feed_headlines = self.news_data[feed_name]
                
                if self.rotation_enabled and len(feed_headlines) > self.headlines_per_feed:
                    # Rotate headlines to show different ones
                    start_idx = (self.rotation_count * self.headlines_per_feed) % len(feed_headlines)
                    selected = []
                    for i in range(self.headlines_per_feed):
                        idx = (start_idx + i) % len(feed_headlines)
                        selected.append(feed_headlines[idx])
                    display_headlines.extend(selected)
                else:
                    display_headlines.extend(feed_headlines[:self.headlines_per_feed])
        
        # Create scrolling text with separators
        if display_headlines:
            text_parts = []
            for i, headline in enumerate(display_headlines):
                feed_prefix = f"[{headline['feed']}] "
                text_parts.append(feed_prefix + headline['title'])
                
            # Join with separators and add spacing
            separator = " • "
            self.cached_text = separator.join(text_parts) + " • "  # Add separator at end for smooth loop
            
            # Calculate text dimensions for perfect scrolling
            self.calculate_scroll_dimensions()
            
            self.current_headlines = display_headlines
            logger.info(f"Prepared {len(display_headlines)} headlines for display")

    def calculate_scroll_dimensions(self):
        """Calculate exact dimensions needed for smooth scrolling"""
        if not self.cached_text:
            return
            
        try:
            # Load font
            try:
                font = ImageFont.truetype(self.font_path, self.font_size)
            except:
                font = ImageFont.load_default()
                
            # Calculate text width
            temp_img = Image.new('RGB', (1, 1))
            temp_draw = ImageDraw.Draw(temp_img)
            
            # Get text dimensions
            bbox = temp_draw.textbbox((0, 0), self.cached_text, font=font)
            self.total_scroll_width = bbox[2] - bbox[0]
            
            # Calculate dynamic display duration
            self.calculate_dynamic_duration()
            
            logger.info(f"Text width calculated: {self.total_scroll_width} pixels")
            logger.info(f"Dynamic duration calculated: {self.dynamic_duration} seconds")
            
        except Exception as e:
            logger.error(f"Error calculating scroll dimensions: {e}")
            self.total_scroll_width = len(self.cached_text) * 8  # Fallback estimate
            self.calculate_dynamic_duration()

    def calculate_dynamic_duration(self):
        """Calculate the exact time needed to display all headlines"""
        # If dynamic duration is disabled, use fixed duration from config
        if not self.dynamic_duration_enabled:
            self.dynamic_duration = self.news_config.get('fixed_duration', 60)
            logger.info(f"Dynamic duration disabled, using fixed duration: {self.dynamic_duration}s")
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
                logger.info(f"Duration capped to minimum: {self.min_duration}s")
            elif calculated_duration > self.max_duration:
                self.dynamic_duration = self.max_duration
                logger.info(f"Duration capped to maximum: {self.max_duration}s")
            else:
                self.dynamic_duration = calculated_duration
                
            logger.info(f"Dynamic duration calculation:")
            logger.info(f"  Display width: {display_width}px")
            logger.info(f"  Text width: {self.total_scroll_width}px")
            logger.info(f"  Total scroll distance: {total_scroll_distance}px")
            logger.info(f"  Frames needed: {frames_needed:.1f}")
            logger.info(f"  Base time: {total_time:.2f}s")
            logger.info(f"  Buffer time: {buffer_time:.2f}s ({self.duration_buffer*100}%)")
            logger.info(f"  Calculated duration: {calculated_duration}s")
            logger.info(f"  Final duration: {self.dynamic_duration}s")
            
        except Exception as e:
            logger.error(f"Error calculating dynamic duration: {e}")
            self.dynamic_duration = self.min_duration  # Use configured minimum as fallback

    def should_update(self) -> bool:
        """Check if news data should be updated"""
        return (time.time() - self.last_update) > self.update_interval

    def get_news_display(self) -> Image.Image:
        """Generate the scrolling news ticker display"""
        try:
            # Update news if needed
            if self.should_update() or not self.current_headlines:
                self.fetch_news_data()
            
            if not self.cached_text:
                return self.create_no_news_image()
            
            # Create display image
            width = self.display_manager.width
            height = self.display_manager.height
            
            img = Image.new('RGB', (width, height), (0, 0, 0))
            draw = ImageDraw.Draw(img)
            
            # Load font
            try:
                font = ImageFont.truetype(self.font_path, self.font_size)
            except:
                font = ImageFont.load_default()
            
            # Calculate vertical position (center the text)
            text_height = self.font_size
            y_pos = (height - text_height) // 2
            
            # Calculate scroll position for smooth animation
            if self.total_scroll_width > 0:
                # Scroll from right to left
                x_pos = width - self.scroll_position
                
                # Draw the text
                draw.text((x_pos, y_pos), self.cached_text, font=font, fill=self.text_color)
                
                # If text has scrolled partially off screen, draw it again for seamless loop
                if x_pos + self.total_scroll_width < width:
                    draw.text((x_pos + self.total_scroll_width, y_pos), self.cached_text, font=font, fill=self.text_color)
                
                # Update scroll position
                self.scroll_position += self.scroll_speed
                
                # Reset scroll when text has completely passed
                if self.scroll_position >= self.total_scroll_width:
                    self.scroll_position = 0
                    self.rotation_count += 1
                    
                    # Check if we should rotate headlines
                    if (self.rotation_enabled and 
                        self.rotation_count >= self.rotation_threshold and 
                        any(len(headlines) > self.headlines_per_feed for headlines in self.news_data.values())):
                        self.prepare_headlines_for_display()
                        self.rotation_count = 0
            
            return img
            
        except Exception as e:
            logger.error(f"Error generating news display: {e}")
            return self.create_error_image(str(e))

    def create_no_news_image(self) -> Image.Image:
        """Create image when no news is available"""
        width = self.display_manager.width
        height = self.display_manager.height
        
        img = Image.new('RGB', (width, height), (0, 0, 0))
        draw = ImageDraw.Draw(img)
        
        try:
            font = ImageFont.truetype(self.font_path, self.font_size)
        except:
            font = ImageFont.load_default()
        
        text = "Loading news..."
        bbox = draw.textbbox((0, 0), text, font=font)
        text_width = bbox[2] - bbox[0]
        text_height = bbox[3] - bbox[1]
        
        x = (width - text_width) // 2
        y = (height - text_height) // 2
        
        draw.text((x, y), text, font=font, fill=self.text_color)
        return img

    def create_error_image(self, error_msg: str) -> Image.Image:
        """Create image for error display"""
        width = self.display_manager.width
        height = self.display_manager.height
        
        img = Image.new('RGB', (width, height), (0, 0, 0))
        draw = ImageDraw.Draw(img)
        
        try:
            font = ImageFont.truetype(self.font_path, max(8, self.font_size - 2))
        except:
            font = ImageFont.load_default()
        
        text = f"News Error: {error_msg[:50]}..."
        bbox = draw.textbbox((0, 0), text, font=font)
        text_width = bbox[2] - bbox[0]
        text_height = bbox[3] - bbox[1]
        
        x = max(0, (width - text_width) // 2)
        y = (height - text_height) // 2
        
        draw.text((x, y), text, font=font, fill=(255, 0, 0))
        return img

    def display_news(self):
        """Main display method for news ticker"""
        try:
            while True:
                img = self.get_news_display()
                self.display_manager.display_image(img)
                time.sleep(self.scroll_delay)
                
        except KeyboardInterrupt:
            logger.info("News display interrupted by user")
        except Exception as e:
            logger.error(f"Error in news display loop: {e}")

    def add_custom_feed(self, name: str, url: str):
        """Add a custom RSS feed"""
        if name not in self.custom_feeds:
            self.custom_feeds[name] = url
            # Update config
            if 'news_manager' not in self.config:
                self.config['news_manager'] = {}
            self.config['news_manager']['custom_feeds'] = self.custom_feeds
            self.config_manager.save_config(self.config)
            logger.info(f"Added custom feed: {name} -> {url}")

    def remove_custom_feed(self, name: str):
        """Remove a custom RSS feed"""
        if name in self.custom_feeds:
            del self.custom_feeds[name]
            # Update config
            self.config['news_manager']['custom_feeds'] = self.custom_feeds
            self.config_manager.save_config(self.config)
            logger.info(f"Removed custom feed: {name}")

    def set_enabled_feeds(self, feeds: List[str]):
        """Set which feeds are enabled"""
        self.enabled_feeds = feeds
        # Update config
        if 'news_manager' not in self.config:
            self.config['news_manager'] = {}
        self.config['news_manager']['enabled_feeds'] = self.enabled_feeds
        self.config_manager.save_config(self.config)
        logger.info(f"Updated enabled feeds: {self.enabled_feeds}")
        
        # Refresh headlines
        self.fetch_news_data()

    def get_available_feeds(self) -> Dict[str, str]:
        """Get all available feeds (default + custom)"""
        return {**self.default_feeds, **self.custom_feeds}

    def get_feed_status(self) -> Dict[str, Any]:
        """Get status information about feeds"""
        status = {
            'enabled_feeds': self.enabled_feeds,
            'available_feeds': list(self.get_available_feeds().keys()),
            'headlines_per_feed': self.headlines_per_feed,
            'last_update': self.last_update,
            'total_headlines': sum(len(headlines) for headlines in self.news_data.values()),
            'rotation_enabled': self.rotation_enabled,
            'rotation_count': self.rotation_count,
            'dynamic_duration': self.dynamic_duration
        }
        return status

    def get_dynamic_duration(self) -> int:
        """Get the calculated dynamic duration for display"""
        # Ensure we have current data and calculated duration
        if not self.cached_text or self.dynamic_duration == 60:
            # Try to refresh if we don't have current data
            if self.should_update() or not self.current_headlines:
                self.fetch_news_data()
        
        return self.dynamic_duration