"""
Per-request logging for the web interface.

Logging itself is configured by ``src.logging_config.setup_logging`` (the same
formatter and journald priorities as the display service); this module only
decides what one HTTP request is worth logging, and at which level.

The UI polls: the error summary, system status, display preview and log
streams are fetched every few seconds by every open tab. Logging each of those
at INFO buried everything else in the journal (``GET /api/v3/errors/summary -
200`` once a minute per tab, forever). So a request that only read something
and succeeded is DEBUG; one that changed something, or failed, is logged at a
level that shows up by default.
"""
import logging
import time

from flask import Flask, request

logger = logging.getLogger('web_interface.api')

#: Methods that do not change server state. A successful one is routine.
_READ_ONLY_METHODS = frozenset({'GET', 'HEAD', 'OPTIONS'})


def request_log_level(method: str, status_code: int) -> int:
    """The level a finished request is logged at."""
    if status_code >= 500:
        return logging.ERROR
    if status_code >= 400:
        return logging.WARNING
    if method.upper() in _READ_ONLY_METHODS:
        return logging.DEBUG
    return logging.INFO


def log_request(method: str, path: str, status_code: int,
                duration_ms: float) -> None:
    """Log one finished request."""
    level = request_log_level(method, status_code)
    if logger.isEnabledFor(level):
        logger.log(level, "%s %s - %d (%.1fms)",
                   method, path, status_code, duration_ms)


def init_app(app: Flask) -> None:
    """Time every request and log it when its response is ready."""

    @app.before_request
    def _start_request_timer():
        request.start_time = time.perf_counter()

    @app.after_request
    def _log_finished_request(response):
        try:
            started = getattr(request, 'start_time', None)
            duration_ms = 0.0 if started is None else (time.perf_counter() - started) * 1000
            log_request(request.method, request.path, response.status_code,
                        duration_ms)
        except Exception:  # nosec B110 - request logging must never interrupt a live HTTP response
            pass
        return response
