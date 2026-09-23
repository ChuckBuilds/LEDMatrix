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
from src.redaction import redact_credentials


logger = get_logger(__name__)


# Long enough for an errno string with a path, short enough not to dump a
# parser's worth of context into a JSON field.
_MAX_DETAIL_LENGTH = 400


def describe_exception(exc: BaseException,
                       max_length: int = _MAX_DETAIL_LENGTH) -> str:
    """
    One-line, safe-to-return description of an exception.

    The generic "an error occurred; see logs for details" tells a user nothing
    and, when the failure is bad enough, the logs are unreachable too: a device
    whose storage was failing returned that message from every endpoint
    *including* the log viewer, because journalctl could not be executed. The
    underlying `[Errno 5] Input/output error` named the fault immediately.

    Returns "TypeName: message", credentials redacted and length capped. The
    type alone is worth carrying -- a bare PermissionError says more than any
    generic sentence.

    Args:
        exc: The exception to describe
        max_length: Truncate beyond this many characters

    Returns:
        A single-line description, never empty
    """
    message = str(exc).strip()
    text = f"{type(exc).__name__}: {message}" if message else type(exc).__name__
    return redact_text(text, max_length)


def redact_text(text: str, max_length: int = _MAX_DETAIL_LENGTH) -> str:
    """Make arbitrary text safe to hand back over HTTP.

    Split out of describe_exception because exceptions are not the only thing
    worth returning: a subprocess's stderr, or a message a helper script
    printed, is just as useful to a user and just as capable of carrying a
    token or a password in it.

    Args:
        text: The text to redact
        max_length: Truncate beyond this many characters

    Returns:
        A single line, credentials replaced, length capped.
    """
    text = redact_credentials(text)
    # Collapse newlines/tabs so the detail stays one line in a JSON field.
    text = ' '.join(text.split())
    if len(text) > max_length:
        text = text[:max_length - 1].rstrip() + '…'
    return text


# What a failure nothing anticipated says. The detail beside it carries the
# actual diagnosis; this sentence only points at where the traceback went.
UNHANDLED_ERROR_MESSAGE = 'An error occurred; see logs for details'


def unhandled_exception_payload(exc: BaseException) -> dict:
    """JSON body for an exception no route handled: status, message, details.

    Deliberately no `error_code`. The plugin API client (api_client.js) passes
    a body that has one straight to the rich error modal, and wraps one that
    has none as a plain API_ERROR toast; the api_v3 routes answered this shape
    from their own catch-alls for years, so the UI is built around it.
    """
    return {
        'status': 'error',
        'message': UNHANDLED_ERROR_MESSAGE,
        'details': describe_exception(exc),
    }


def http_exception_payload(error) -> dict:
    """JSON body for a werkzeug HTTPException (405, 400, 415, 413...).

    Same shape web_interface/app.py's global handler returns, so a 4xx raised
    inside an api_v3 route reads the same as one raised anywhere else.
    """
    return {
        'status': 'error',
        'error_code': (error.name or 'HTTP_ERROR').upper().replace(' ', '_'),
        'message': error.description,
    }


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
    
    # All three use `is not None` rather than truthiness: "" and {} are
    # values a caller chose to send, and dropping them silently would make
    # the response shape depend on the data.
    if data is not None:
        response["data"] = data

    if message is not None:
        response["message"] = message

    if metadata is not None:
        response["metadata"] = metadata

    return response

