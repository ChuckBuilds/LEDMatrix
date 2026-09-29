"""Marking plugin-facing core APIs for removal.

Plugins live in other repositories, so a method nothing in core calls may
still be called by a plugin nobody has checked. Such methods get
``@deprecated`` for one release before they are removed: the first call in a
process logs a warning naming the method and the release that removes it
(visible in ``journalctl -u ledmatrix``), and emits a DeprecationWarning for
tooling.

Before a release removes anything, ``scripts/plugin_api_usage.py`` scans core,
the official plugin monorepo and the registry's third-party plugins for callers
and overriders of every marked method. Its latest output is
``docs/DEPRECATIONS_3.8.md``; remove only what it reports unused, and move the
rest to a later release. ``test/test_deprecation.py`` fails while any marker
names a release at or below ``src.__version__``, so a release cannot ship with
a removal date it has already passed.
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

        wrapper.__deprecated__ = message  # type: ignore[attr-defined]  # functools' _Wrapped doesn't declare it
        return wrapper  # type: ignore[return-value]

    return decorate
