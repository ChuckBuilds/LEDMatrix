"""
Centralized Logging Configuration

Provides consistent logging configuration across the LEDMatrix application.
Supports structured logging with context information and appropriate log levels.
"""

import copy
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
        """Format log record with context.

        Works on a shallow copy of the record: a record is formatted once
        PER HANDLER, so mutating record.msg in place (the old behavior)
        prepended the context prefix again for every additional handler.
        """
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
                record = copy.copy(record)
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
    # Under systemd, tag each line so the journal records the real severity
    # rather than filing everything as informational. The file handler below
    # keeps the plain formatter: the prefix is meaningful to journald and noise
    # anywhere else.
    console_handler.setFormatter(
        JournalPriorityFormatter(formatter) if _under_systemd() else formatter)
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


#: syslog priorities, which is what systemd parses from a "<N>" prefix on
#: stdout. Mapped from Python's levels.
_SYSLOG_PRIORITY = {
    logging.CRITICAL: 2,   # LOG_CRIT
    logging.ERROR: 3,      # LOG_ERR
    logging.WARNING: 4,    # LOG_WARNING
    logging.INFO: 6,       # LOG_INFO
    logging.DEBUG: 7,      # LOG_DEBUG
}


class JournalPriorityFormatter(logging.Formatter):
    """Wraps a formatter, prefixing each line with its syslog priority.

    Under systemd everything this process writes to stdout lands in the journal
    as PRIORITY=6, whatever the Python level was. Measured on a live rig: 55
    ERROR lines and 13 WARNING lines in a day, every one of them recorded as
    informational, so `journalctl -p err -u ledmatrix` returned nothing at all
    while errors were being logged. Anyone triaging has to grep the message
    text instead, which is both slower and wrong -- a search for "oom" matches
    the radar logging "zoom=9".

    systemd reads a leading "<N>" on each line and uses it as the priority
    (sd-daemon(3)), so this needs no extra dependency. Multi-line records get
    the prefix on every line, since the journal splits them and an unprefixed
    continuation would fall back to the default.
    """

    def __init__(self, inner: logging.Formatter):
        super().__init__()
        self._inner = inner

    @property
    def inner(self) -> logging.Formatter:
        """The formatter doing the actual work.

        Whether journald tagging is applied depends on JOURNAL_STREAM, so it is
        on under systemd and off in a terminal -- and anything asserting which
        formatter setup_logging() selected would otherwise get a different
        answer in CI than on a developer's machine. Exposing the inner one lets
        those checks stay about format_type, which is what they mean.
        """
        return self._inner

    def format(self, record: logging.LogRecord) -> str:
        text = self._inner.format(record)
        prefix = f"<{_SYSLOG_PRIORITY.get(record.levelno, 6)}>"
        return "\n".join(prefix + line for line in text.split("\n"))


def _under_systemd() -> bool:
    """True when stdout really is the journal.

    systemd sets JOURNAL_STREAM to "dev:ino" for services whose output it
    captures. Presence alone is not enough to act on: the variable is
    inherited by child processes and survives redirection, so a subprocess
    whose stdout is a pipe or a file still sees it and would emit the "<N>"
    priority prefixes as literal noise into that output. systemd's own
    guidance is to fstat the descriptor and compare st_dev/st_ino, which is
    what distinguishes "the journal is somewhere in my ancestry" from "my
    stdout is the journal".
    """
    declared = os.environ.get("JOURNAL_STREAM")
    if not declared:
        return False
    try:
        dev_text, ino_text = declared.split(":", 1)
        declared_ids = (int(dev_text), int(ino_text))
    except (ValueError, AttributeError):
        return False
    try:
        stat_result = os.fstat(sys.stdout.fileno())
    except (OSError, ValueError, AttributeError):
        # No usable stdout: captured by pytest, detached, or already closed.
        return False
    return (stat_result.st_dev, stat_result.st_ino) == declared_ids


class PluginLoggerAdapter(logging.LoggerAdapter):
    """LoggerAdapter that stamps every record with its plugin_id.

    A plain `logging.Logger` attribute (the old approach) is never copied
    onto individual `LogRecord`s, so `ContextualFormatter`/`StructuredFormatter`
    only ever saw `plugin_id` on calls that explicitly passed
    `extra={'plugin_id': ...}` (i.e. `log_with_context`). This adapter injects
    it into `extra` on every call, so `self.logger.info(...)` in plugin code
    is tagged automatically.
    """

    def process(self, msg, kwargs):
        extra = dict(kwargs.get('extra') or {})
        extra.setdefault('plugin_id', self.extra.get('plugin_id'))
        kwargs['extra'] = extra
        return msg, kwargs


def get_logger(name: str, plugin_id: Optional[str] = None):
    """
    Get a logger with consistent configuration.

    Args:
        name: Logger name (typically __name__)
        plugin_id: Optional plugin ID for automatic context

    Returns:
        Configured logger instance (or a PluginLoggerAdapter when plugin_id
        is given, which supports the same .debug/.info/.warning/.error API)
    """
    logger = logging.getLogger(name)

    if plugin_id:
        return PluginLoggerAdapter(logger, {'plugin_id': plugin_id})

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
    """Log error message with context. Defaults exc_info=True; a caller
    passing exc_info explicitly wins (the old hardcoded keyword raised
    TypeError on that duplicate)."""
    kwargs.setdefault('exc_info', True)
    log_with_context(logger, logging.ERROR, message, **kwargs)


def log_debug(logger: logging.Logger, message: str, **kwargs) -> None:
    """Log debug message with context."""
    log_with_context(logger, logging.DEBUG, message, **kwargs)

