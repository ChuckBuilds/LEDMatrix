"""One place to turn a request-supplied name into a path you can open.

Every web handler that opens a file under a fixed directory had grown its own
version of this: a regex here, an ``os.path.basename`` there, a
``str(x).startswith(str(base))`` somewhere else. They were not equivalent.
``startswith`` says ``plugin-repos/foo-evil`` is inside ``plugin-repos/foo``;
validating a name in one place and rebuilding the path from the *raw* value in
another leaves the guard checking something the filesystem never sees.

Two functions, used the same way everywhere:

``safe_path_component(value)``
    ``value`` if it is one harmless path segment, otherwise ``None``.

``resolve_under(base, *parts)``
    the resolved path, or ``None`` if any part is unsafe or the result would
    land outside ``base``.

Both *return the sanitised value* rather than a boolean, so a caller cannot
validate one string and then open another -- and so a scanner can follow what
actually reaches ``open()``. ``os.path.basename`` does the stripping because it
is the sanitiser CodeQL's path-injection query recognises; the equality check
after it means an input with a directory part is rejected outright instead of
being silently truncated to something the caller did not ask for.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, List, Optional, Union

__all__ = [
    'safe_path_component',
    'safe_relative_parts',
    'resolve_under',
]

# Names that are a path component syntactically but never name a real entry a
# caller means to reach.
_RESERVED_COMPONENTS = frozenset({'', '.', '..'})


def safe_path_component(value: Any) -> Optional[str]:
    """Return ``value`` when it is a single, harmless path segment.

    Returns ``None`` for anything else: a non-string, an empty string, ``.`` or
    ``..``, a value carrying a directory separator (either platform's), a drive
    letter, or an embedded NUL.

    The return value is what callers must join -- not the argument.
    """
    if not isinstance(value, str) or not value:
        return None
    if '\x00' in value:
        return None

    # basename strips any directory component, so what a caller joins cannot
    # carry one. Comparing the result against the input rejects rather than
    # truncates: "../etc/passwd" is an error, not a request for "passwd".
    name = os.path.basename(value)
    if name != value or name in _RESERVED_COMPONENTS:
        return None

    # basename only knows the host platform's separator. On POSIX a backslash
    # is an ordinary character, and "C:" is a plausible-looking name that
    # os.path.join would treat as a drive on Windows. Rule both out everywhere
    # so behaviour does not depend on where the service happens to run.
    if '/' in name or '\\' in name or os.sep in name or (os.altsep and os.altsep in name):
        return None
    if ':' in name and len(name) >= 2 and name[1] == ':':
        return None

    return name


def safe_relative_parts(value: Any) -> Optional[List[str]]:
    """Split a multi-segment relative path into safe components.

    For Flask's ``<path:...>`` converter, where ``a/b/c.json`` is legitimate but
    ``../../config/config_secrets.json`` is not. Returns the component list, or
    ``None`` if any component fails :func:`safe_path_component`.
    """
    if not isinstance(value, str) or not value:
        return None
    if value.startswith('/') or value.startswith('\\'):
        return None

    parts: List[str] = []
    for raw in value.replace('\\', '/').split('/'):
        if raw == '':
            # A trailing or doubled slash names nothing; skip it rather than
            # rejecting a path a browser may well send.
            continue
        part = safe_path_component(raw)
        if part is None:
            return None
        parts.append(part)

    return parts or None


def resolve_under(base: Union[str, Path], *parts: Any) -> Optional[Path]:
    """Resolve ``base/parts...``, or ``None`` if that would escape ``base``.

    Each part is validated with :func:`safe_path_component` first, so the value
    that reaches the filesystem is the sanitised one. The containment check is
    kept as well: it is what catches a symlink inside ``base`` pointing out of
    it, which no amount of name validation can see.
    """
    safe_parts: List[str] = []
    for part in parts:
        component = safe_path_component(part)
        if component is None:
            return None
        safe_parts.append(component)

    try:
        base_resolved = Path(base).resolve()
        candidate = base_resolved.joinpath(*safe_parts).resolve()
        candidate.relative_to(base_resolved)
    except (OSError, ValueError, TypeError):
        return None

    return candidate
