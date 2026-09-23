"""Marking plugin-facing core APIs for removal.

Plugins live in other repositories, so a method nothing in core calls may
still be called by a plugin nobody has checked. Such methods get
``@deprecated`` for one release before they are removed: the first call in a
process logs a warning naming the method and the release that removes it
(visible in ``journalctl -u ledmatrix``), and emits a DeprecationWarning for
tooling.
"""

import functools
import threading
import warnings
from typing import Callable, Optional, TypeVar

from src.logging_config import get_logger

logger = get_logger(__name__)

F = TypeVar("F", bound=Callable)

_warned = set()
_warned_lock = threading.Lock()


def deprecated(removal: str, alternative: Optional[str] = None) -> Callable[[F], F]:
    """Decorate a function or method that will be removed in ``removal``."""

    def decorate(func: F) -> F:
        message = f"{func.__qualname__}() is deprecated and will be removed in LEDMatrix {removal}"
        if alternative:
            message += f"; {alternative}"

        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            with _warned_lock:
                first = func.__qualname__ not in _warned
                _warned.add(func.__qualname__)
            if first:
                logger.warning(message)
                warnings.warn(message, DeprecationWarning, stacklevel=2)
            return func(*args, **kwargs)

        wrapper.__deprecated__ = message
        return wrapper  # type: ignore[return-value]

    return decorate
