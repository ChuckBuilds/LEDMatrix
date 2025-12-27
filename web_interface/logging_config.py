"""
Structured logging configuration for the web interface.
Provides JSON-formatted logs for production and readable logs for development.
"""
import logging
import json
import sys
from datetime import datetime
from typing import Any, Dict, Optional


class JSONFormatter(logging.Formatter):
    """Formatter that outputs logs as JSON for structured logging."""
    
    def format(self, record: logging.LogRecord) -> str:
        """Format log record as JSON."""
        log_data = {
            'timestamp': datetime.utcnow().isoformat(),
            'level': record.levelname,
            'logger': record.name,
            'message': record.getMessage(),
            'module': record.module,
            'function': record.funcName,
            'line': record.lineno,
        }
        
        # Add exception info if present
        if record.exc_info:
            log_data['exception'] = self.formatException(record.exc_info)
        
        # Add extra fields if present
        if hasattr(record, 'request_id'):
            log_data['request_id'] = record.request_id
        if hasattr(record, 'user_id'):
            log_data['user_id'] = record.user_id
        if hasattr(record, 'ip_address'):
            log_data['ip_address'] = record.ip_address
        if hasattr(record, 'duration_ms'):
            log_data['duration_ms'] = record.duration_ms
        
        return json.dumps(log_data)


def setup_web_interface_logging(level: str = 'INFO', use_json: bool = False):
    """
    Set up logging for the web interface.
    
    Args:
        level: Log level (DEBUG, INFO, WARNING, ERROR)
        use_json: If True, use JSON formatting (for production)
    """
    # Get root logger
    logger = logging.getLogger()
    logger.setLevel(getattr(logging, level.upper()))
    
    # Remove existing handlers
    logger.handlers.clear()
    
    # Create console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(getattr(logging, level.upper()))
    
    # Set formatter
    if use_json:
        formatter = JSONFormatter()
    else:
        formatter = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
    
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)
    
    # Set levels for specific loggers
    logging.getLogger('werkzeug').setLevel(logging.WARNING)  # Reduce Flask noise
    logging.getLogger('urllib3').setLevel(logging.WARNING)  # Reduce HTTP noise


def log_api_request(method: str, path: str, status_code: int, duration_ms: float,
                   ip_address: Optional[str] = None, **kwargs):
    """
    Log an API request with structured data.
    
    Args:
        method: HTTP method
        path: Request path
        status_code: HTTP status code
        duration_ms: Request duration in milliseconds
        ip_address: Client IP address
        **kwargs: Additional context
    """
    logger = logging.getLogger('web_interface.api')
    
    extra = {
        'method': method,
        'path': path,
        'status_code': status_code,
        'duration_ms': round(duration_ms, 2),
        'ip_address': ip_address,
        **kwargs
    }
    
    # Log at appropriate level based on status code
    if status_code >= 500:
        logger.error(f"{method} {path} - {status_code} ({duration_ms}ms)", extra=extra)
    elif status_code >= 400:
        logger.warning(f"{method} {path} - {status_code} ({duration_ms}ms)", extra=extra)
    else:
        logger.info(f"{method} {path} - {status_code} ({duration_ms}ms)", extra=extra)


def log_config_change(change_type: str, target: str, success: bool, **kwargs):
    """
    Log a configuration change.
    
    Args:
        change_type: Type of change (save, delete, update)
        target: What was changed (e.g., 'main_config', 'plugin_config:football-scoreboard')
        success: Whether the change was successful
        **kwargs: Additional context
    """
    logger = logging.getLogger('web_interface.config')
    
    extra = {
        'change_type': change_type,
        'target': target,
        'success': success,
        **kwargs
    }
    
    if success:
        logger.info(f"Config {change_type}: {target}", extra=extra)
    else:
        logger.error(f"Config {change_type} failed: {target}", extra=extra)

