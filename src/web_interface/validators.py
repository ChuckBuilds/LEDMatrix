"""
Input validation utilities for the web interface.
Provides validation functions for user inputs to prevent XSS, invalid data, and security issues.
"""
import re
import os
from typing import Optional, Tuple, List
from urllib.parse import urlparse
from pathlib import Path


def escape_html(text: str) -> str:
    """Escape HTML entities in text to prevent XSS."""
    if not isinstance(text, str):
        text = str(text)
    # Use basic HTML entity escaping
    text = text.replace('&', '&amp;')
    text = text.replace('<', '&lt;')
    text = text.replace('>', '&gt;')
    text = text.replace('"', '&quot;')
    text = text.replace("'", '&#x27;')
    return text


def validate_image_url(url: str) -> Tuple[bool, Optional[str]]:
    """
    Validate and sanitize image URLs to prevent XSS and protocol injection.
    
    Returns:
        Tuple of (is_valid, error_message)
    """
    if not url or not isinstance(url, str):
        return False, "URL must be a non-empty string"
    
    url_lower = url.lower().strip()
    
    # Reject dangerous protocols
    dangerous_protocols = ['javascript:', 'data:', 'vbscript:', 'file:']
    for protocol in dangerous_protocols:
        if url_lower.startswith(protocol):
            return False, f"Dangerous protocol '{protocol}' not allowed"
    
    # Reject event handlers
    if any(handler in url_lower for handler in ['onerror=', 'onload=', 'onclick=']):
        return False, "Event handlers not allowed in URLs"
    
    # Allow relative paths starting with /
    if url.startswith('/'):
        # Validate it's a safe relative path (no directory traversal)
        if '..' in url or url.startswith('//'):
            return False, "Invalid relative path"
        return True, None
    
    # Validate absolute URLs
    try:
        parsed = urlparse(url)
        allowed_protocols = ['http', 'https']
        if parsed.scheme not in allowed_protocols:
            return False, f"Only http:// and https:// protocols are allowed"
        return True, None
    except Exception as e:
        return False, f"Invalid URL format: {str(e)}"


def validate_font_awesome_class(class_name: str) -> Tuple[bool, Optional[str]]:
    """
    Validate Font Awesome class names to prevent XSS.
    
    Returns:
        Tuple of (is_valid, error_message)
    """
    if not isinstance(class_name, str):
        return False, "Class name must be a string"
    
    # Whitelist pattern: only allow alphanumeric, dash, underscore, and spaces
    # Must contain 'fa-' for Font Awesome
    fa_pattern = re.compile(r'^[a-zA-Z0-9\s_-]*fa-[a-zA-Z0-9-]+[a-zA-Z0-9\s_-]*$')
    
    if not fa_pattern.match(class_name):
        return False, "Invalid Font Awesome class name format"
    
    if 'fa-' not in class_name:
        return False, "Font Awesome class must contain 'fa-'"
    
    return True, None


def validate_file_upload(filename: str, max_size_mb: int = 10, 
                        allowed_extensions: Optional[List[str]] = None) -> Tuple[bool, Optional[str]]:
    """
    Validate file upload parameters.
    
    Args:
        filename: Name of the file
        max_size_mb: Maximum file size in MB
        allowed_extensions: List of allowed file extensions (e.g., ['.ttf', '.otf'])
    
    Returns:
        Tuple of (is_valid, error_message)
    """
    if not filename or not isinstance(filename, str):
        return False, "Filename must be a non-empty string"
    
    # Check for directory traversal
    if '..' in filename or '/' in filename or '\\' in filename:
        return False, "Filename contains invalid characters"
    
    # Check extension if specified
    if allowed_extensions:
        file_ext = Path(filename).suffix.lower()
        if file_ext not in allowed_extensions:
            return False, f"File extension must be one of: {', '.join(allowed_extensions)}"
    
    return True, None


def validate_mime_type(file_path: str, allowed_types: List[str]) -> Tuple[bool, Optional[str]]:
    """
    Validate file MIME type.
    
    Args:
        file_path: Path to the file
        allowed_types: List of allowed MIME types (e.g., ['image/png', 'image/jpeg'])
    
    Returns:
        Tuple of (is_valid, error_message)
    """
    try:
        import mimetypes
        mime_type, _ = mimetypes.guess_type(file_path)
        
        if not mime_type:
            return False, "Could not determine file type"
        
        if mime_type not in allowed_types:
            return False, f"File type '{mime_type}' not allowed. Allowed types: {', '.join(allowed_types)}"
        
        return True, None
    except Exception as e:
        return False, f"Error validating MIME type: {str(e)}"


def validate_numeric_range(value: float, min_val: Optional[float] = None, 
                          max_val: Optional[float] = None) -> Tuple[bool, Optional[str]]:
    """
    Validate numeric value is within range.
    
    Returns:
        Tuple of (is_valid, error_message)
    """
    if not isinstance(value, (int, float)):
        return False, "Value must be a number"
    
    if min_val is not None and value < min_val:
        return False, f"Value must be at least {min_val}"
    
    if max_val is not None and value > max_val:
        return False, f"Value must be at most {max_val}"
    
    return True, None


def validate_string_length(text: str, min_length: Optional[int] = None, 
                          max_length: Optional[int] = None) -> Tuple[bool, Optional[str]]:
    """
    Validate string length.
    
    Returns:
        Tuple of (is_valid, error_message)
    """
    if not isinstance(text, str):
        return False, "Value must be a string"
    
    length = len(text)
    
    if min_length is not None and length < min_length:
        return False, f"String must be at least {min_length} characters"
    
    if max_length is not None and length > max_length:
        return False, f"String must be at most {max_length} characters"
    
    return True, None


def sanitize_plugin_config(config: dict) -> dict:
    """
    Sanitize plugin configuration input to prevent injection.
    
    Args:
        config: Configuration dictionary
    
    Returns:
        Sanitized configuration dictionary
    """
    sanitized = {}
    
    for key, value in config.items():
        # Sanitize keys (no special characters)
        if not isinstance(key, str) or not re.match(r'^[a-zA-Z0-9_]+$', key):
            continue  # Skip invalid keys
        
        # Sanitize values based on type
        if isinstance(value, str):
            # For string values, escape HTML but preserve the string
            sanitized[key] = value  # Don't escape - let templates handle it
        elif isinstance(value, (int, float, bool)):
            sanitized[key] = value
        elif isinstance(value, list):
            sanitized[key] = [sanitize_plugin_config(item) if isinstance(item, dict) else item for item in value]
        elif isinstance(value, dict):
            sanitized[key] = sanitize_plugin_config(value)
        else:
            # Skip unknown types
            continue
    
    return sanitized

