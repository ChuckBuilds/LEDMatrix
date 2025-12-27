"""
Error Handling Utilities

Common error handling patterns and utilities for consistent error handling
across the LEDMatrix codebase.
"""

import logging
from typing import Any, Callable, Optional, TypeVar, Dict
from functools import wraps
from src.exceptions import LEDMatrixError

T = TypeVar('T')


def handle_file_operation(
    operation: Callable[[], T],
    error_message: str,
    logger: logging.Logger,
    default: Optional[T] = None,
    context: Optional[Dict[str, Any]] = None
) -> Optional[T]:
    """
    Handle file operations with consistent error handling.
    
    Args:
        operation: Function to execute (file read/write)
        error_message: Base error message
        logger: Logger instance
        default: Default value to return on error
        context: Optional context dictionary for error details
        
    Returns:
        Result of operation or default value
    """
    try:
        return operation()
    except FileNotFoundError as e:
        logger.warning("%s: File not found: %s", error_message, e, exc_info=True)
        return default
    except PermissionError as e:
        logger.error("%s: Permission denied: %s", error_message, e, exc_info=True)
        return default
    except (IOError, OSError) as e:
        logger.error("%s: I/O error: %s", error_message, e, exc_info=True)
        return default
    except Exception as e:
        logger.error("%s: Unexpected error: %s", error_message, e, exc_info=True)
        return default


def handle_json_operation(
    operation: Callable[[], T],
    error_message: str,
    logger: logging.Logger,
    default: Optional[T] = None,
    context: Optional[Dict[str, Any]] = None
) -> Optional[T]:
    """
    Handle JSON operations with consistent error handling.
    
    Args:
        operation: Function to execute (JSON load/dump)
        error_message: Base error message
        logger: Logger instance
        default: Default value to return on error
        context: Optional context dictionary for error details
        
    Returns:
        Result of operation or default value
    """
    try:
        return operation()
    except FileNotFoundError as e:
        logger.warning("%s: File not found: %s", error_message, e, exc_info=True)
        return default
    except PermissionError as e:
        logger.error("%s: Permission denied: %s", error_message, e, exc_info=True)
        return default
    except ValueError as e:
        logger.error("%s: Invalid JSON: %s", error_message, e, exc_info=True)
        return default
    except (IOError, OSError) as e:
        logger.error("%s: I/O error: %s", error_message, e, exc_info=True)
        return default
    except Exception as e:
        logger.error("%s: Unexpected error: %s", error_message, e, exc_info=True)
        return default


def safe_execute(
    operation: Callable[[], T],
    error_message: str,
    logger: logging.Logger,
    default: Optional[T] = None,
    raise_on_error: bool = False,
    exception_type: type = LEDMatrixError
) -> Optional[T]:
    """
    Safely execute an operation with error handling.
    
    Args:
        operation: Function to execute
        error_message: Base error message
        logger: Logger instance
        default: Default value to return on error
        raise_on_error: If True, raise exception instead of returning default
        exception_type: Type of exception to raise if raise_on_error is True
        
    Returns:
        Result of operation or default value (or raises exception)
    """
    try:
        return operation()
    except LEDMatrixError:
        # Re-raise LEDMatrix errors as-is
        raise
    except Exception as e:
        logger.error("%s: %s", error_message, e, exc_info=True)
        if raise_on_error:
            raise exception_type(error_message, context={'original_error': str(e)}) from e
        return default


def retry_on_failure(
    max_attempts: int = 3,
    delay: float = 1.0,
    backoff: float = 2.0,
    exceptions: tuple = (Exception,),
    logger: Optional[logging.Logger] = None
):
    """
    Decorator to retry a function on failure.
    
    Args:
        max_attempts: Maximum number of retry attempts
        delay: Initial delay between retries in seconds
        backoff: Multiplier for delay after each retry
        exceptions: Tuple of exceptions to catch and retry on
        logger: Optional logger instance
        
    Returns:
        Decorator function
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs):
            current_delay = delay
            last_exception = None
            
            for attempt in range(max_attempts):
                try:
                    return func(*args, **kwargs)
                except exceptions as e:
                    last_exception = e
                    if attempt < max_attempts - 1:
                        if logger:
                            logger.warning(
                                "%s failed (attempt %d/%d): %s. Retrying in %.1fs...",
                                func.__name__, attempt + 1, max_attempts, e, current_delay
                            )
                        import time
                        time.sleep(current_delay)
                        current_delay *= backoff
                    else:
                        if logger:
                            logger.error(
                                "%s failed after %d attempts: %s",
                                func.__name__, max_attempts, e, exc_info=True
                            )
            
            # If we get here, all attempts failed
            raise last_exception
        
        return wrapper
    return decorator


def log_and_continue(
    logger: logging.Logger,
    message: str,
    level: int = logging.WARNING,
    context: Optional[Dict[str, Any]] = None
):
    """
    Log a message and continue execution (for non-critical errors).
    
    Args:
        logger: Logger instance
        message: Log message
        level: Log level (default: WARNING)
        context: Optional context dictionary
    """
    if context:
        logger.log(level, "%s (context: %s)", message, context)
    else:
        logger.log(level, message)


def log_and_raise(
    logger: logging.Logger,
    message: str,
    exception_type: type = LEDMatrixError,
    context: Optional[Dict[str, Any]] = None
):
    """
    Log an error and raise an exception.
    
    Args:
        logger: Logger instance
        message: Error message
        exception_type: Type of exception to raise
        context: Optional context dictionary
        
    Raises:
        exception_type: The specified exception type
    """
    logger.error(message, exc_info=True)
    raise exception_type(message, context=context)

