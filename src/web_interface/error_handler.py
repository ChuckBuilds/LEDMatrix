"""
Centralized error handling for web interface.

Provides decorators and helpers for consistent error handling across API endpoints.
"""

import functools
import traceback
from typing import Callable, Any, Optional
from flask import jsonify

from src.web_interface.errors import (
    WebInterfaceError, ErrorCode, ErrorCategory
)
from src.logging_config import get_logger


logger = get_logger(__name__)


def handle_errors(
    default_error_code: Optional[ErrorCode] = None,
    default_category: Optional[ErrorCategory] = None,
    log_error: bool = True
):
    """
    Decorator to handle errors in API endpoints.
    
    Catches exceptions and converts them to structured error responses.
    
    Args:
        default_error_code: Default error code if exception doesn't match known types
        default_category: Default error category
        log_error: Whether to log the error
    """
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            try:
                return func(*args, **kwargs)
            except WebInterfaceError as e:
                # Already a structured error
                if log_error:
                    logger.error(
                        f"Error in {func.__name__}: {e.message}",
                        extra={
                            'error_code': e.error_code.value,
                            'category': e.category.value,
                            'context': e.context
                        }
                    )
                return jsonify(e.to_dict()), 500
            
            except Exception as e:
                # Convert to structured error
                web_error = WebInterfaceError.from_exception(
                    e,
                    error_code=default_error_code,
                    context={
                        'function': func.__name__,
                        'endpoint': getattr(func, '__name__', 'unknown')
                    }
                )
                
                if default_category:
                    web_error.category = default_category
                
                if log_error:
                    logger.error(
                        f"Unhandled error in {func.__name__}: {e}",
                        exc_info=True,
                        extra={
                            'error_code': web_error.error_code.value,
                            'category': web_error.category.value,
                            'context': web_error.context
                        }
                    )
                
                return jsonify(web_error.to_dict()), 500
        
        return wrapper
    return decorator


def create_error_response(
    error_code: ErrorCode,
    message: str,
    details: Optional[str] = None,
    context: Optional[dict] = None,
    suggested_fixes: Optional[list] = None,
    status_code: int = 500
) -> tuple:
    """
    Create a standardized error response.
    
    Args:
        error_code: Error code
        message: Error message
        details: Optional detailed error information
        context: Optional context dictionary
        suggested_fixes: Optional list of suggested fixes
        status_code: HTTP status code
    
    Returns:
        Tuple of (jsonify response, status_code)
    """
    error = WebInterfaceError(
        error_code=error_code,
        message=message,
        details=details,
        context=context or {},
        suggested_fixes=suggested_fixes
    )
    
    return jsonify(error.to_dict()), status_code


def create_success_response(
    data: Any = None,
    message: Optional[str] = None,
    metadata: Optional[dict] = None
) -> dict:
    """
    Create a standardized success response.
    
    Args:
        data: Response data
        message: Optional success message
        metadata: Optional metadata (timing, version, etc.)
    
    Returns:
        Dictionary for jsonify
    """
    response = {
        "status": "success"
    }
    
    if data is not None:
        response["data"] = data
    
    if message:
        response["message"] = message
    
    if metadata:
        response["metadata"] = metadata
    
    return response

