"""
Error text and payloads for web interface responses.

Safe exception descriptions and the bodies for exceptions no route handled.
The standard success/error responses are in api_helpers.
"""

from src.redaction import redact_credentials


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
