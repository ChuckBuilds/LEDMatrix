"""
Standardized API response helpers.

Provides consistent API response formatting across all endpoints.
"""

import time
from typing import Any, Optional, Dict, Tuple, Union
from flask import jsonify, request

from src.web_interface.error_handler import create_error_response, create_success_response
from src.web_interface.errors import ErrorCode, ErrorCategory


def success_response(
    data: Any = None,
    message: Optional[str] = None,
    metadata: Optional[Dict] = None
):
    """
    Create a standardized success response.
    
    Args:
        data: Response data
        message: Optional success message
        metadata: Optional metadata (timing, version, etc.)
    
    Returns:
        Flask jsonify response
    """
    response_data = create_success_response(data, message, metadata)
    
    # Add request metadata if available
    if metadata is None:
        metadata = {}
    
    # Add timing if request start time is available
    if hasattr(request, 'start_time'):
        metadata['response_time_ms'] = int((time.time() - request.start_time) * 1000)
    
    if metadata:
        response_data['metadata'] = metadata
    
    return jsonify(response_data)


def error_response(
    error_code: ErrorCode,
    message: str,
    details: Optional[str] = None,
    context: Optional[Dict] = None,
    suggested_fixes: Optional[list] = None,
    status_code: int = 500
):
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
        Flask jsonify response with status code
    """
    return create_error_response(
        error_code=error_code,
        message=message,
        details=details,
        context=context,
        suggested_fixes=suggested_fixes,
        status_code=status_code
    )


def validate_request_json(required_fields: list, data: Optional[Dict] = None) -> Tuple[Optional[Dict], Optional[Any]]:
    """
    Validate request JSON has required fields.
    
    Args:
        required_fields: List of required field names
        data: Optional data dict (if None, reads from request)
    
    Returns:
        Tuple of (data_dict, error_response) or (data_dict, None) if valid
    """
    if data is None:
        data = request.get_json(silent=True)
    
    if not data:
        return None, error_response(
            ErrorCode.INVALID_INPUT,
            "Request body must be valid JSON",
            status_code=400
        )
    
    missing_fields = [field for field in required_fields if field not in data]
    if missing_fields:
        return None, error_response(
            ErrorCode.INVALID_INPUT,
            f"Missing required fields: {', '.join(missing_fields)}",
            context={'missing_fields': missing_fields},
            status_code=400
        )
    
    return data, None


def validate_request_params(required_params: list) -> Tuple[Optional[Dict], Optional[Any]]:
    """
    Validate request has required query parameters.
    
    Args:
        required_params: List of required parameter names
    
    Returns:
        Tuple of (params_dict, error_response) or (params_dict, None) if valid
    """
    missing_params = [param for param in required_params if param not in request.args]
    if missing_params:
        return None, error_response(
            ErrorCode.INVALID_INPUT,
            f"Missing required parameters: {', '.join(missing_params)}",
            context={'missing_params': missing_params},
            status_code=400
        )
    
    params = {param: request.args.get(param) for param in required_params}
    return params, None

