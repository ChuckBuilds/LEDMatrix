"""
Centralized error handling for web interface.

Provides helpers for consistent error responses across API endpoints.
"""

from typing import Any, Optional
from flask import jsonify

from src.web_interface.errors import (
    WebInterfaceError, ErrorCode, ErrorCategory
)
from src.logging_config import get_logger


logger = get_logger(__name__)


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

