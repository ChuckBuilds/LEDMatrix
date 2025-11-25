"""
Centralized Logging Configuration

Provides consistent logging configuration across the LEDMatrix application.
Supports structured logging with context information and appropriate log levels.
"""

import logging
import sys
import os
import json
from typing import Optional, Dict, Any
from datetime import datetime


class StructuredFormatter(logging.Formatter):
    """JSON formatter for structured logging in production."""
    
    def format(self, record: logging.LogRecord) -> str:
        """Format log record as JSON."""
        log_data = {
            'timestamp': datetime.fromtimestamp(record.created).isoformat(),
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
        
        # Add extra context if present
        if hasattr(record, 'context'):
            log_data['context'] = record.context
        
        if hasattr(record, 'plugin_id'):
            log_data['plugin_id'] = record.plugin_id
        
        if hasattr(record, 'operation_id'):
            log_data['operation_id'] = record.operation_id
        
        return json.dumps(log_data)


class ContextualFormatter(logging.Formatter):
    """Human-readable formatter with context information."""
    
    def __init__(self, include_context: bool = True, include_location: bool = False):
        """
        Initialize formatter.
        
        Args:
            include_context: Include context information in log messages
            include_location: Include module/function/line information
        """
        if include_location:
            fmt = '%(asctime)s.%(msecs)03d - %(levelname)s - %(name)s - %(module)s.%(funcName)s:%(lineno)d - %(message)s'
        else:
            fmt = '%(asctime)s.%(msecs)03d - %(levelname)s - %(name)s - %(message)s'
        
        super().__init__(fmt=fmt, datefmt='%Y-%m-%d %H:%M:%S')
        self.include_context = include_context
    
    def format(self, record: logging.LogRecord) -> str:
        """Format log record with context."""
        # Add context to message if present
        if self.include_context:
            context_parts = []
            
            if hasattr(record, 'plugin_id'):
                context_parts.append(f"[Plugin: {record.plugin_id}]")
            
            if hasattr(record, 'operation_id'):
                context_parts.append(f"[Op: {record.operation_id}]")
            
            if hasattr(record, 'context') and isinstance(record.context, dict):
                for key, value in record.context.items():
                    context_parts.append(f"[{key}: {value}]")
            
            if context_parts:
                record.msg = ' '.join(context_parts) + ' ' + str(record.msg)
        
        return super().format(record)


def setup_logging(
    level: Optional[int] = None,
    format_type: str = 'readable',
    include_location: bool = False,
    log_file: Optional[str] = None
) -> None:
    """
    Set up centralized logging configuration.
    
    Args:
        level: Log level (defaults to INFO, or DEBUG if LEDMATRIX_DEBUG is set)
        format_type: 'readable' for human-readable, 'json' for structured JSON
        include_location: Include module/function/line in readable format
        log_file: Optional file path for file logging
    """
    # Determine log level
    if level is None:
        if os.environ.get('LEDMATRIX_DEBUG', '').lower() == 'true':
            level = logging.DEBUG
        else:
            level = logging.INFO
    
    # Get root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(level)
    
    # Remove existing handlers to avoid duplicates
    root_logger.handlers.clear()
    
    # Create formatter based on type
    if format_type == 'json':
        formatter = StructuredFormatter()
    else:
        formatter = ContextualFormatter(include_context=True, include_location=include_location)
    
    # Console handler (always add)
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(level)
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)
    
    # File handler (if specified)
    if log_file:
        try:
            file_handler = logging.FileHandler(log_file)
            file_handler.setLevel(level)
            file_handler.setFormatter(formatter)
            root_logger.addHandler(file_handler)
        except (IOError, OSError, PermissionError) as e:
            # Log to stderr since file logging failed
            sys.stderr.write(f"Warning: Could not set up file logging to {log_file}: {e}\n")


def get_logger(name: str, plugin_id: Optional[str] = None) -> logging.Logger:
    """
    Get a logger with consistent configuration.
    
    Args:
        name: Logger name (typically __name__)
        plugin_id: Optional plugin ID for automatic context
        
    Returns:
        Configured logger instance
    """
    logger = logging.getLogger(name)
    
    # Add plugin_id as attribute for formatters
    if plugin_id:
        logger.plugin_id = plugin_id
    
    return logger


def log_with_context(
    logger: logging.Logger,
    level: int,
    message: str,
    context: Optional[Dict[str, Any]] = None,
    plugin_id: Optional[str] = None,
    operation_id: Optional[str] = None,
    exc_info: Optional[Any] = None
) -> None:
    """
    Log a message with context information.
    
    Args:
        logger: Logger instance
        level: Log level (logging.INFO, logging.ERROR, etc.)
        message: Log message
        context: Optional context dictionary
        plugin_id: Optional plugin ID
        operation_id: Optional operation ID for request tracking
        exc_info: Optional exception info for error logging
    """
    extra = {}
    
    if context:
        extra['context'] = context
    
    if plugin_id:
        extra['plugin_id'] = plugin_id
    
    if operation_id:
        extra['operation_id'] = operation_id
    
    logger.log(level, message, extra=extra, exc_info=exc_info)


# Convenience functions for common log operations
def log_info(logger: logging.Logger, message: str, **kwargs) -> None:
    """Log info message with context."""
    log_with_context(logger, logging.INFO, message, **kwargs)


def log_warning(logger: logging.Logger, message: str, **kwargs) -> None:
    """Log warning message with context."""
    log_with_context(logger, logging.WARNING, message, **kwargs)


def log_error(logger: logging.Logger, message: str, **kwargs) -> None:
    """Log error message with context."""
    log_with_context(logger, logging.ERROR, message, **kwargs, exc_info=True)


def log_debug(logger: logging.Logger, message: str, **kwargs) -> None:
    """Log debug message with context."""
    log_with_context(logger, logging.DEBUG, message, **kwargs)

