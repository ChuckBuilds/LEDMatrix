"""Parse an HTTP response body as JSON, with orjson when it is installed.

``requests``' ``response.json()`` uses the stdlib parser. For the payloads the
sports plugins fetch -- a season schedule is tens of MB -- that runs ~1.7x
slower than orjson on a Pi 4 (3.1s against 1.8s for the 53MB MLB season), and
both hold the GIL for the whole parse, which freezes the display for as long.
Nothing else changes: the result is the same Python objects.
"""

from __future__ import annotations

from typing import Any

try:
    import orjson
except ImportError:  # optional dependency; see docs/SCROLL_PERFORMANCE.md
    orjson = None


def response_json(response: Any) -> Any:
    """``response.json()``, parsed by orjson when available."""
    body = getattr(response, "content", None)
    if orjson is None or not isinstance(body, (bytes, bytearray)):
        return response.json()
    try:
        return orjson.loads(body)
    except orjson.JSONDecodeError:
        # Let requests raise its usual error, with its usual message.
        return response.json()
