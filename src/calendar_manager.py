import os
import json
import logging
from datetime import datetime, timedelta
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
import pickle
from PIL import Image, ImageDraw, ImageFont
import numpy as np
import pytz
from src.config_manager import ConfigManager
import time

# Configure logger for this module
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)  # Set to INFO level

class CalendarManager:
    def __init__(self, display_manager, config):
        logger.info("Initializing CalendarManager")
        self.display_manager = display_manager
        self.config = config
        self.calendar_config = config.get('calendar', {})
        self.enabled = self.calendar_config.get('enabled', False)
        self.update_interval = self.calendar_config.get('update_interval', 300)
        self.max_events = self.calendar_config.get('max_events', 3)
        self.calendars = self.calendar_config.get('calendars', ['birthdays'])
        self.last_update = 0
        self.last_display_log = 0  # Add timestamp for display message throttling
        self.events = []
        self.service = None
        
        # Log font information during initialization
        logger.info(f"Display Manager fonts:")
        logger.info(f"  Small font: {self.display_manager.small_font}")
        logger.info(f"  Calendar font: {self.display_manager.calendar_font}")
        logger.info(f"  Font types - Small: {type(self.display_manager.small_font)}, Calendar: {type(self.display_manager.calendar_font)}")
        
        logger.info(f"Calendar configuration: enabled={self.enabled}, update_interval={self.update_interval}, max_events={self.max_events}, calendars={self.calendars}")
        
        # Get timezone from config
        timezone_str = self.config.get('timezone', 'UTC')
        logger.info(f"Loading timezone from config: {timezone_str}")
        try:
            self.timezone = pytz.timezone(timezone_str)
            logger.info(f"Successfully loaded timezone: {self.timezone}")
        except pytz.UnknownTimeZoneError:
            logger.warning(f"Unknown timezone '{timezone_str}' in config, defaulting to UTC.")
            self.timezone = pytz.utc
        
        if self.enabled:
            self.authenticate()
        else:
            logger.warning("Calendar manager is disabled in configuration")
        
        # Display properties
        self.text_color = (255, 255, 255)  # White
        self.time_color = (255, 255, 255)  # White
        self.date_color = (255, 255, 255)  # White
        
        # State management
        self.current_event_index = 0
        self.force_clear = False

    def authenticate(self):
        """Authenticate with Google Calendar API."""
        logger.info("Starting calendar authentication")
        creds = None
        token_file = self.calendar_config.get('token_file', 'token.pickle')
        
        if os.path.exists(token_file):
            logger.info(f"Loading credentials from {token_file}")
            with open(token_file, 'rb') as token:
                creds = pickle.load(token)
                
        if not creds or not creds.valid:
            logger.info("Credentials not found or invalid")
            if creds and creds.expired and creds.refresh_token:
                logger.info("Refreshing expired credentials")
                creds.refresh(Request())
            else:
                logger.info("Requesting new credentials")
                flow = InstalledAppFlow.from_client_secrets_file(
                    self.calendar_config.get('credentials_file', 'credentials.json'),
                    ['https://www.googleapis.com/auth/calendar.readonly'])
                creds = flow.run_local_server(port=0)
                
            logger.info(f"Saving credentials to {token_file}")
            with open(token_file, 'wb') as token:
                pickle.dump(creds, token)
                
        self.service = build('calendar', 'v3', credentials=creds)
        logger.info("Calendar service built successfully")
    
    def get_events(self):
        """Fetch upcoming calendar events."""
        if not self.enabled or not self.service:
            return []
        
        try:
            now = datetime.utcnow().isoformat() + 'Z'
            events_result = self.service.events().list(
                calendarId='primary',
                timeMin=now,
                maxResults=self.max_events,
                singleEvents=True,
                orderBy='startTime'
            ).execute()
            
            events = events_result.get('items', [])
            
            # Log event details
            if events:
                logger.info(f"Found {len(events)} calendar events:")
                for event in events:
                    summary = event.get('summary', 'No Title')
                    start = event.get('start', {}).get('dateTime', event.get('start', {}).get('date'))
                    end = event.get('end', {}).get('dateTime', event.get('end', {}).get('date'))
                    logger.info(f"  Event: {summary}")
                    logger.info(f"    Start: {start}")
                    logger.info(f"    End: {end}")
            else:
                logger.info("No upcoming calendar events found")
                
            return events
        except Exception as e:
            logging.error(f"Error fetching calendar events: {str(e)}")
            return []
    
    def draw_event(self, event, y_position=2):
        """Draw a single calendar event with proper spacing to avoid overlap."""
        try:
            # Get date, time, and summary
            date_text = self._format_event_date(event)
            time_text = self._format_event_time(event)
            datetime_text = f"{date_text} {time_text}".strip()
            summary = event.get('summary', 'No Title')

            # Ensure BDF calendar font has a usable pixel height
            if hasattr(self.display_manager.calendar_font, 'set_char_size'):
                # 7px height for 5x7 BDF; FreeType expects 64 units per pixel
                try:
                    self.display_manager.calendar_font.set_char_size(height=7 * 64)
                except Exception:
                    pass

            # Measurements
            matrix_width = self.display_manager.width
            date_font = self.display_manager.regular_font
            title_font = self.display_manager.calendar_font
            date_line_height = self.display_manager.get_font_height(date_font)
            title_line_height = self.display_manager.get_font_height(title_font)

            # Draw centered date/time on first line
            datetime_width = self.display_manager.get_text_width(datetime_text, date_font)
            datetime_x = (matrix_width - datetime_width) // 2
            self.display_manager.draw_text(
                datetime_text,
                datetime_x,
                y_position,
                color=self.time_color,
                element_key="calendar.datetime"
            )

            # Wrap summary to fit width with margins
            available_width = matrix_width - 4  # 2px margin on each side
            title_lines = self._wrap_text(summary, available_width, title_font, max_lines=2)

            # Start summary beneath date/time with small spacing
            y_summary_top = y_position + date_line_height + 2
            for i, line in enumerate(title_lines):
                line_width = self.display_manager.get_text_width(line, title_font)
                line_x = (matrix_width - line_width) // 2
                line_y = y_summary_top + (i * title_line_height)
                self.display_manager.draw_text(
                    line,
                    line_x,
                    line_y,
                    color=self.text_color,
                    element_key="calendar.title"
                )

            return True
        except Exception as e:
            logger.error(f"Error drawing calendar event: {e}", exc_info=True)
            return False

    def _wrap_text(self, text, max_width, font, max_lines=2):
        """Wrap text to fit within max_width using the provided font."""
        if not text:
            return [""]
            
        lines = []
        current_line = []
        words = text.split()
        
        for word in words:
            # Try adding the word to the current line
            test_line = ' '.join(current_line + [word]) if current_line else word
            # Use display_manager's draw_text method to measure text width
            text_width = self.display_manager.get_text_width(test_line, font)
            
            if text_width <= max_width:
                # Word fits, add it to current line
                current_line.append(word)
            else:
                # Word doesn't fit, start a new line
                if current_line:
                    lines.append(' '.join(current_line))
                    current_line = [word]
                else:
                    # Single word too long, truncate it
                    truncated = word
                    while len(truncated) > 0:
                        if self.display_manager.get_text_width(truncated + "...", font) <= max_width:
                            lines.append(truncated + "...")
                            break
                        truncated = truncated[:-1]
                    if not truncated:
                        lines.append(word[:10] + "...")
            
            # Check if we've filled all lines
            if len(lines) >= max_lines:
                break
        
        # Handle any remaining text in current_line
        if current_line and len(lines) < max_lines:
            remaining_text = ' '.join(current_line)
            if len(words) > len(current_line):  # More words remain
                # Try to fit with ellipsis
                while len(remaining_text) > 0:
                    if self.display_manager.get_text_width(remaining_text + "...", font) <= max_width:
                        lines.append(remaining_text + "...")
                        break
                    remaining_text = remaining_text[:-1]
            else:
                lines.append(remaining_text)
        
        # Ensure we have exactly max_lines
        while len(lines) < max_lines:
            lines.append("")
            
        return lines[:max_lines]

    def update(self, current_time):
        """Update calendar events if needed."""
        if not self.enabled:
            logger.debug("Calendar manager is disabled, skipping update")
            return
        
        if current_time - self.last_update > self.update_interval:
            logger.info("Updating calendar events")
            self.events = self.get_events()
            self.last_update = current_time
            if not self.events:
                 logger.info("No upcoming calendar events found.")
            else:
                 logger.info(f"Fetched {len(self.events)} calendar events.")
            # Reset index if events change
            self.current_event_index = 0 
        else:
            # Only log debug message every 5 seconds
            if current_time - self.last_display_log > 5:
                logger.debug("Skipping calendar update - not enough time has passed")
                self.last_display_log = current_time

    def _format_event_date(self, event):
        """Format event date for display"""
        start = event.get('start', {}).get('dateTime', event.get('start', {}).get('date'))
        if not start:
            return ""
            
        try:
            # Handle both date and dateTime formats
            if 'T' in start:
                # The datetime string already includes timezone info (-05:00)
                dt = datetime.fromisoformat(start)
            else:
                dt = datetime.strptime(start, '%Y-%m-%d')
                # Make date object timezone-aware (assume UTC if no tz info)
                dt = pytz.utc.localize(dt)
            
            # No need to convert timezone since it's already in the correct one
            return dt.strftime("%a %-m/%-d") # e.g., "Mon 4/21"
        except ValueError as e:
            logging.error(f"Could not parse date string: {start} - {e}")
            return ""

    def _format_event_time(self, event):
        """Format event time for display"""
        start = event.get('start', {}).get('dateTime', event.get('start', {}).get('date'))
        if not start or 'T' not in start: # Only show time for dateTime events
            return "All Day"
            
        try:
            # The datetime string already includes timezone info (-05:00)
            dt = datetime.fromisoformat(start)
            # No need to convert timezone since it's already in the correct one
            return dt.strftime("%I:%M%p")
        except ValueError as e:
            logging.error(f"Could not parse time string: {start} - {e}")
            return "Invalid Time"

    def display(self, force_clear=False):
        """Display calendar events on the LED matrix."""
        if not self.enabled or not self.events:
            return
            
        try:
            if force_clear:
                self.display_manager.clear()
                self.force_clear = True
                
            if self.current_event_index >= len(self.events):
                self.current_event_index = 0
                
            event = self.events[self.current_event_index]
            
            # Log the event being displayed, but only every 5 seconds
            current_time = time.time()
            if current_time - self.last_display_log > 5:
                summary = event.get('summary', 'No Title')
                date_text = self._format_event_date(event)
                time_text = self._format_event_time(event)
                logger.info(f"Displaying calendar event: {summary}")
                logger.info(f"  Date: {date_text}")
                logger.info(f"  Time: {time_text}")
                self.last_display_log = current_time
            
            # Draw the event
            self.draw_event(event)
            
            # Update the display
            self.display_manager.update_display()
            
        except Exception as e:
            logger.error(f"Error displaying calendar event: {e}", exc_info=True)

    def advance_event(self):
        """Advance to the next event. Called by DisplayManager when calendar display time is up."""
        if not self.enabled:
            logger.debug("Calendar manager is disabled, skipping event advance")
            return
        self.current_event_index += 1
        if self.current_event_index >= len(self.events):
            self.current_event_index = 0
        logger.debug(f"CalendarManager advanced to event index {self.current_event_index}") 