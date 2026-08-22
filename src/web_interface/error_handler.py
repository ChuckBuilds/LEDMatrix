"""
Centralized error handling for web interface.

Provides helpers for consistent error responses across API endpoints.
"""

import re
from typing import Any, Optional
from flask import jsonify

from src.web_interface.errors import (
    WebInterfaceError, ErrorCode, ErrorCategory
)
from src.logging_config import get_logger


logger = get_logger(__name__)


# Credentials that turn up inside exception text. A requests error quotes the
# URL it failed on, and plugins that authenticate by query string put their key
# there, so echoing an exception verbatim can hand out an API key. Redact the
# value, keep the parameter name -- knowing *which* credential was involved is
# part of the diagnosis.
_REDACT_CREDENTIAL = re.compile(
    r'((?:api[_-]?key|access[_-]?token|auth|apikey|key|passwd|password|pwd|'
    r'secret|sig|signature|token)["\']?\s*[=:]\s*["\']?)([^\s&"\'<>,}]+)',
    re.IGNORECASE,
)

# `Authorization: <scheme> <credential>`. The scheme name is kept because it
# says which kind of credential failed; the credential goes. Any scheme
# matches, not a fixed list: ApiKey, Negotiate, NTLM, AWS4-HMAC-SHA256 and
# whatever a plugin's API invents next are all credentials, and a list would
# silently leak the ones nobody thought of. Not covered by the generic pattern
# above, whose value part stops at whitespace and so would keep the credential
# once a space follows the scheme.
_REDACT_AUTH_HEADER = re.compile(
    r'((?:proxy-)?authorization["\']?\s*[=:]\s*["\']?\s*'
    r'(?:[A-Za-z][\w.+-]*[ \t]+)?)'          # optional scheme name, kept
    r'([^\s,"\'<>}]+)',                       # the credential, redacted
    re.IGNORECASE,
)

# Credentials embedded in a URL: https://user:password@host. requests quotes
# the full URL in its exceptions, so this is a realistic leak. The username is
# kept -- it identifies which account failed without being the secret.
_REDACT_URL_USERINFO = re.compile(r'([a-z][a-z0-9+.-]*://[^/\s:@]+:)([^/\s@]+)(@)',
                                  re.IGNORECASE)

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
    text = text or ''
    # Order matters: the URL and header forms are more specific than the
    # generic key=value pattern, which would otherwise chew the scheme.
    text = _REDACT_URL_USERINFO.sub(r'\1<redacted>\3', text)
    text = _REDACT_AUTH_HEADER.sub(r'\1<redacted>', text)
    text = _REDACT_CREDENTIAL.sub(r'\1<redacted>', text)
    # Collapse newlines/tabs so the detail stays one line in a JSON field.
    text = ' '.join(text.split())
    if len(text) > max_length:
        text = text[:max_length - 1].rstrip() + '…'
    return text


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

