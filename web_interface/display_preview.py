"""The display preview the web UI shows, read from the display service's snapshot.

GET /api/v3/display/current and the /api/v3/stream/display SSE stream both
answer with preview_payload(). Free of Flask and app imports, like
system_metrics, so a blueprint can use it without constructing the app.
"""

import base64
import time
from typing import Any, Dict, Optional

#: Where DisplayManager writes the snapshot. Written atomically (a temp file
#: and os.replace), so a read never sees half a PNG.
SNAPSHOT_PATH = "/tmp/led_matrix_preview.png"  # nosec B108 - fixed path shared with display_manager


def read_snapshot_base64(path: Optional[str] = None) -> str:
    """The snapshot PNG's bytes, base64-encoded.

    The file already is a PNG, so it is passed through as it is: decoding and
    re-encoding it produced the same image for more CPU on the Pi. Raises
    OSError when there is no snapshot or it cannot be read.
    """
    with open(path or SNAPSHOT_PATH, 'rb') as f:
        return base64.b64encode(f.read()).decode('ascii')


def preview_payload(width: int, height: int, image: Optional[str]) -> Dict[str, Any]:
    """``{timestamp, width, height, image}``; ``image`` is None when there is
    no snapshot to show."""
    return {
        'timestamp': time.time(),
        'width': width,
        'height': height,
        'image': image,
    }
