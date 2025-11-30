"""
Structured logging configuration for web interface.

Provides JSON-formatted structured logging for better debugging and monitoring.
"""

import json
import logging
import sys
from datetime import datetime
from typing import Dict, Any, Optional


class StructuredFormatter(logging.Formatter):
    """
    JSON formatter for structured logging.
    
    Formats log records as JSON for easy parsing and analysis.
    """
    
    def format(self, record: logging.LogRecord) -> str:
        """Format log record as JSON."""
        log_data = {
            'timestamp': datetime.utcnow().isoformat(),
            'level': record.levelname,
            'logger': record.name,
            'message': record.getMessage(),
            'module': record.module,
            'function': record.funcName,
            'line': record.lineno
        }
        
        # Add exception info if present
        if record.exc_info:
            log_data['exception'] = self.formatException(record.exc_info)
        
        # Add extra fields from record
        if hasattr(record, 'extra'):
            log_data.update(record.extra)
        
        # Add context from record
        if hasattr(record, 'context'):
            log_data['context'] = record.context
        
        return json.dumps(log_data)
    
    def formatException(self, exc_info) -> Dict[str, Any]:
        """Format exception as structured data."""
        import traceback
        return {
            'type': exc_info[0].__name__ if exc_info[0] else None,
            'message': str(exc_info[1]) if exc_info[1] else None,
            'traceback': traceback.format_exception(*exc_info)
        }


def setup_structured_logging(
    level: int = logging.INFO,
    use_json: bool = False,
    output_stream = sys.stdout
) -> None:
    """
    Set up structured logging for web interface.
    
    Args:
        level: Logging level
        use_json: Whether to use JSON formatting
        output_stream: Output stream for logs
    """
    root_logger = logging.getLogger()
    root_logger.setLevel(level)
    
    # Remove existing handlers
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)
    
    # Create handler
    handler = logging.StreamHandler(output_stream)
    handler.setLevel(level)
    
    # Set formatter
    if use_json:
        formatter = StructuredFormatter()
    else:
        formatter = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        )
    
    handler.setFormatter(formatter)
    root_logger.addHandler(handler)


def log_plugin_operation(
    logger: logging.Logger,
    operation: str,
    plugin_id: str,
    status: str,
    context: Optional[Dict[str, Any]] = None
) -> None:
    """
    Log a plugin operation with structured data.
    
    Args:
        logger: Logger instance
        operation: Operation name (install, update, uninstall, etc.)
        plugin_id: Plugin identifier
        status: Operation status (success, failed, etc.)
        context: Optional additional context
    """
    extra = {
        'operation': operation,
        'plugin_id': plugin_id,
        'status': status
    }
    
    if context:
        extra['context'] = context
    
    logger.info(
        f"Plugin operation: {operation} for {plugin_id} - {status}",
        extra=extra
    )


def log_config_change(
    logger: logging.Logger,
    config_key: str,
    action: str,
    before: Optional[Dict[str, Any]] = None,
    after: Optional[Dict[str, Any]] = None,
    context: Optional[Dict[str, Any]] = None
) -> None:
    """
    Log a configuration change with before/after values.
    
    Args:
        logger: Logger instance
        config_key: Configuration key that changed
        action: Action performed (save, update, delete, etc.)
        before: Configuration before change
        after: Configuration after change
        context: Optional additional context
    """
    extra = {
        'config_key': config_key,
        'action': action
    }
    
    if before:
        extra['before'] = before
    if after:
        extra['after'] = after
    if context:
        extra['context'] = context
    
    logger.info(
        f"Config change: {action} on {config_key}",
        extra=extra
    )

