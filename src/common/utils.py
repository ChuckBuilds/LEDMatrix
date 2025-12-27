"""
Utility Functions

Common utility functions for LED matrix plugins.
Extracted from LEDMatrix core to provide reusable functionality for plugins.
"""

import logging
import re
from datetime import datetime, timezone
from typing import Optional, Tuple, Union
import pytz


def normalize_team_abbreviation(team_abbr: str) -> str:
    """
    Normalize team abbreviation for consistent usage.
    
    Args:
        team_abbr: Raw team abbreviation
        
    Returns:
        Normalized abbreviation
    """
    if not team_abbr:
        return ""
    
    # Remove spaces and convert to uppercase
    normalized = team_abbr.strip().upper()
    
    # Handle special characters
    normalized = normalized.replace('&', 'AND')
    normalized = normalized.replace(' ', '')
    normalized = normalized.replace('-', '')
    
    return normalized


def format_time(dt: datetime, timezone_str: str = 'UTC', 
                format_str: str = "%I:%M%p") -> str:
    """
    Format datetime for display.
    
    Args:
        dt: Datetime object
        timezone_str: Target timezone
        format_str: Time format string
        
    Returns:
        Formatted time string
    """
    try:
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        
        target_tz = pytz.timezone(timezone_str)
        local_time = dt.astimezone(target_tz)
        
        formatted = local_time.strftime(format_str)
        # Remove leading zero from hour
        if formatted.startswith('0'):
            formatted = formatted[1:]
        
        return formatted
    except Exception:
        return ""


def format_date(dt: datetime, timezone_str: str = 'UTC',
                format_str: str = "%B %d") -> str:
    """
    Format date for display.
    
    Args:
        dt: Datetime object
        timezone_str: Target timezone
        format_str: Date format string
        
    Returns:
        Formatted date string
    """
    try:
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        
        target_tz = pytz.timezone(timezone_str)
        local_time = dt.astimezone(target_tz)
        
        return local_time.strftime(format_str)
    except Exception:
        return ""


def get_timezone(timezone_str: str) -> pytz.BaseTzInfo:
    """
    Get timezone object from string.
    
    Args:
        timezone_str: Timezone string
        
    Returns:
        Timezone object
    """
    try:
        return pytz.timezone(timezone_str)
    except pytz.UnknownTimeZoneError:
        logging.getLogger(__name__).warning(f"Unknown timezone: {timezone_str}, using UTC")
        return pytz.utc


def validate_dimensions(width: int, height: int) -> bool:
    """
    Validate display dimensions.
    
    Args:
        width: Display width
        height: Display height
        
    Returns:
        True if dimensions are valid
    """
    return (isinstance(width, int) and isinstance(height, int) and 
            width > 0 and height > 0 and width <= 1000 and height <= 1000)


def parse_team_abbreviation(text: str) -> str:
    """
    Parse team abbreviation from various text formats.
    
    Args:
        text: Text containing team abbreviation
        
    Returns:
        Extracted team abbreviation
    """
    if not text:
        return ""
    
    # Remove common prefixes/suffixes
    text = re.sub(r'^(Team|Club|FC|SC)\s+', '', text, flags=re.IGNORECASE)
    text = re.sub(r'\s+(Team|Club|FC|SC)$', '', text, flags=re.IGNORECASE)
    
    # Extract abbreviation (usually 2-4 uppercase letters)
    match = re.search(r'\b[A-Z]{2,4}\b', text.upper())
    if match:
        return match.group()
    
    # Fallback to first 3 characters
    return text[:3].upper()


def format_score(home_score: Union[str, int], away_score: Union[str, int]) -> str:
    """
    Format score for display.
    
    Args:
        home_score: Home team score
        away_score: Away team score
        
    Returns:
        Formatted score string
    """
    return f"{away_score}-{home_score}"


def format_period(period: int, sport: str = "basketball") -> str:
    """
    Format period/quarter/inning for display.
    
    Args:
        period: Period number
        sport: Sport type
        
    Returns:
        Formatted period string
    """
    if sport == "basketball":
        if period <= 4:
            return f"Q{period}"
        else:
            return f"OT{period - 4}"
    elif sport == "football":
        if period <= 4:
            return f"Q{period}"
        else:
            return f"OT{period - 4}"
    elif sport == "hockey":
        if period <= 3:
            return f"P{period}"
        else:
            return f"OT{period - 3}"
    elif sport == "baseball":
        return f"INN {period}"
    else:
        return f"P{period}"


def is_live_game(status: str) -> bool:
    """
    Check if game status indicates live play.
    
    Args:
        status: Game status string
        
    Returns:
        True if game is live
    """
    live_indicators = ['live', 'in progress', 'halftime', 'overtime', 'ot']
    return any(indicator in status.lower() for indicator in live_indicators)


def is_final_game(status: str) -> bool:
    """
    Check if game status indicates final.
    
    Args:
        status: Game status string
        
    Returns:
        True if game is final
    """
    final_indicators = ['final', 'completed', 'finished', 'ended']
    return any(indicator in status.lower() for indicator in final_indicators)


def is_upcoming_game(status: str) -> bool:
    """
    Check if game status indicates upcoming.
    
    Args:
        status: Game status string
        
    Returns:
        True if game is upcoming
    """
    upcoming_indicators = ['scheduled', 'upcoming', 'pre-game', 'not started']
    return any(indicator in status.lower() for indicator in upcoming_indicators)


def sanitize_filename(filename: str) -> str:
    """
    Sanitize filename for safe file operations.
    
    Args:
        filename: Original filename
        
    Returns:
        Sanitized filename
    """
    # Remove or replace invalid characters
    filename = re.sub(r'[<>:"/\\|?*]', '_', filename)
    # Remove multiple underscores
    filename = re.sub(r'_+', '_', filename)
    # Remove leading/trailing underscores and dots
    filename = filename.strip('_.')
    
    return filename


def truncate_text(text: str, max_length: int, suffix: str = "...") -> str:
    """
    Truncate text to maximum length.
    
    Args:
        text: Text to truncate
        max_length: Maximum length
        suffix: Suffix to add when truncating
        
    Returns:
        Truncated text
    """
    if len(text) <= max_length:
        return text
    
    return text[:max_length - len(suffix)] + suffix


def parse_boolean(value: Union[str, bool, int]) -> bool:
    """
    Parse various boolean representations.
    
    Args:
        value: Value to parse
        
    Returns:
        Boolean value
    """
    if isinstance(value, bool):
        return value
    
    if isinstance(value, int):
        return bool(value)
    
    if isinstance(value, str):
        return value.lower() in ('true', '1', 'yes', 'on', 'enabled')
    
    return False


def get_logger(name: str, level: int = logging.INFO) -> logging.Logger:
    """
    Get a logger with consistent configuration.
    
    Note: This function is deprecated. Use src.logging_config.get_logger() instead.
    This function is kept for backward compatibility.
    
    Args:
        name: Logger name
        level: Log level
        
    Returns:
        Configured logger
    """
    # Use centralized logging configuration
    try:
        from src.logging_config import get_logger as get_logger_centralized
        return get_logger_centralized(name)
    except ImportError:
        # Fallback to basic logging if centralized config not available
        logger = logging.getLogger(name)
        logger.setLevel(level)
        
        if not logger.handlers:
            handler = logging.StreamHandler()
            formatter = logging.Formatter(
                '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
            )
            handler.setFormatter(formatter)
            logger.addHandler(handler)
        
        return logger
