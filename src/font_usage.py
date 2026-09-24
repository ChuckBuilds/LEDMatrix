"""Which plugins use which font, published for the web interface.

The display service and the web interface are separate processes. Plugins
run, and register their fonts with ``FontManager.register_manager_font()``,
in the display service only; the web interface's Fonts tab lists the files in
``assets/fonts/`` from its own scan and has no FontManager to ask. So the
display service publishes a small snapshot to the shared cache directory --
the same channel, and the same file permissions, as ``display_current_state``
and ``plugin_error_snapshot``: files are 0660 and carry the cache directory's
group, so root writes and the web user reads.

    FONT_USAGE_KEY  written by the display service only

The snapshot::

    {"generated_at": "2026-09-23T10:00:00",
     "fonts": {"PressStart2P-Regular": ["calendar", "hello-world"],
               "4x6-font": ["calendar"]}}

``fonts`` is keyed the way ``GET /api/v3/fonts/catalog`` keys its rows: the
file name in ``assets/fonts/`` without its extension, as it is on disk. A
plugin names a font by FontManager family ("press_start", "5x7", "6x13b"), by
alias, or by path; :func:`catalog_key_for` resolves each through FontManager's
own catalog to the file it loads and keys it by that file's stem. Fonts that
live outside ``assets/fonts/`` (a plugin's own ``plugin_id::family`` fonts)
and families FontManager cannot resolve are left out: the Fonts tab has no
row for them.

Only registrations are counted. ``get_font()`` does not know which plugin is
calling it, and many plugins open font files directly with PIL, so a font no
plugin registered may still be in use; the web interface says so.
"""

import logging
import os
import threading
import time
from datetime import datetime
from typing import Any, Callable, Dict, FrozenSet, List, Optional, Tuple

logger = logging.getLogger(__name__)

FONT_USAGE_KEY = "font_usage_snapshot"

#: How often the display service checks for a change. A check compares two
#: integers and a set of plugin ids; the snapshot is rebuilt and written only
#: when one of them moved, so plugins that register fonts every frame (the
#: countdown plugin registers per countdown at render time) cost nothing.
TICK_INTERVAL = 10.0

#: Rewrite an unchanged snapshot this often anyway. The cache's disk cleanup
#: deletes entries older than its default retention (30 days), and a display
#: service that runs longer than that without a font change would otherwise
#: turn the Fonts tab back to "unknown".
REFRESH_INTERVAL = 24 * 3600.0

FONT_EXTENSIONS = ('.ttf', '.otf', '.bdf')

# Bounds on what the web interface accepts from the snapshot file.
_MAX_FONTS = 1000
_MAX_PLUGINS_PER_FONT = 200
_MAX_ID_CHARS = 200


def _fonts_dir() -> str:
    from src.common.font_layout import resolve_asset_path
    return resolve_asset_path("assets/fonts")


def _same_dir(a: str, b: str) -> bool:
    try:
        return os.path.normcase(os.path.realpath(a)) == os.path.normcase(os.path.realpath(b))
    except (OSError, ValueError):
        return False


def catalog_key_for(family: Any, font_catalog: Dict[str, str],
                    fonts_dir: Optional[str] = None) -> Optional[str]:
    """The Fonts-tab catalog key for a family a plugin registered, or None.

    Resolution, in order:

    1. ``family`` as a FontManager family -- a scanned file ("5x7",
       "pressstart2p-regular"), an alias ("press_start", "four_by_six",
       "five_by_seven", "tom_thumb"), or a plugin's ``plugin_id::family``.
       FontManager keys scanned files in lower case, so the lookup falls back
       to ``family.lower()``.
    2. ``family`` as a file name or path ("5x7.bdf",
       "assets/fonts/4x6-font.ttf", an absolute path).

    The key is the resolved file's name without its extension, and only for a
    file directly in ``assets/fonts/`` -- anything else has no catalog row.
    """
    if not isinstance(family, str):
        return None
    name = family.strip()
    if not name:
        return None
    fonts_dir = fonts_dir or _fonts_dir()

    path = font_catalog.get(name) or font_catalog.get(name.lower())
    if not path:
        if not name.lower().endswith(FONT_EXTENSIONS):
            return None
        path = name
    directory, filename = os.path.split(str(path))
    stem, ext = os.path.splitext(filename)
    if not stem or ext.lower() not in FONT_EXTENSIONS:
        return None
    if directory:
        if not os.path.isabs(directory):
            from src.common.font_layout import resolve_asset_path
            directory = resolve_asset_path(directory)
        if not _same_dir(directory, fonts_dir):
            return None
    return stem


def build_font_usage(font_manager: Any,
                     plugin_ids: Optional[FrozenSet[str]] = None,
                     fonts_dir: Optional[str] = None) -> Dict[str, List[str]]:
    """``{catalog key: sorted plugin ids}`` from FontManager's registrations.

    ``plugin_ids`` limits it to loaded plugins: a plugin whose constructor
    registered fonts and which then failed validation is not using them.
    """
    fonts_dir = fonts_dir or _fonts_dir()
    # list() copies in one step under the GIL; register_manager_font() runs
    # on the render thread while this runs on the publisher's.
    registrations = list(getattr(font_manager, 'manager_fonts', {}).items())
    font_catalog = dict(getattr(font_manager, 'font_catalog', {}))
    usage: Dict[str, set] = {}
    for manager_id, elements in registrations:
        if not isinstance(manager_id, str):
            continue
        if plugin_ids is not None and manager_id not in plugin_ids:
            continue
        for spec in list(elements.values()) if isinstance(elements, dict) else ():
            family = spec.get('family') if isinstance(spec, dict) else None
            key = catalog_key_for(family, font_catalog, fonts_dir)
            if key is not None:
                usage.setdefault(key, set()).add(manager_id)
    return {key: sorted(ids) for key, ids in sorted(usage.items())}


class FontUsagePublisher:
    """Publishes the display service's font usage to the shared cache.

    tick() is the whole job; start() calls it from a daemon thread every
    TICK_INTERVAL seconds. Nothing here raises: a failure is logged at debug
    and retried on the next tick.
    """

    def __init__(self, cache_manager: Any, font_manager: Any, plugin_manager: Any = None,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.cache_manager = cache_manager
        self.font_manager = font_manager
        self.plugin_manager = plugin_manager
        self._clock = clock
        self._signature: Optional[Tuple[Any, ...]] = None
        # None forces a first write, replacing whatever a previous run of the
        # service left in the cache with this run's usage.
        self._published: Optional[Dict[str, List[str]]] = None
        self._published_at: Optional[float] = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def _loaded_plugin_ids(self) -> Optional[FrozenSet[str]]:
        plugins = getattr(self.plugin_manager, 'plugins', None)
        if not isinstance(plugins, dict):
            return None
        return frozenset(list(plugins))

    def tick(self) -> bool:
        """Publish if the usage changed. True if a snapshot was written."""
        with self._lock:
            try:
                plugin_ids = self._loaded_plugin_ids()
                signature = (
                    getattr(self.font_manager, 'manager_fonts_version', None),
                    getattr(self.font_manager, 'cache_generation', None),
                    plugin_ids,
                )
                now = self._clock()
                refresh_due = (self._published_at is not None
                               and now - self._published_at >= REFRESH_INTERVAL)
                if signature == self._signature and not refresh_due:
                    return False
                usage = build_font_usage(self.font_manager, plugin_ids)
                if usage == self._published and not refresh_due:
                    self._signature = signature
                    return False
                self.cache_manager.set(FONT_USAGE_KEY, {
                    'generated_at': datetime.now().isoformat(timespec='seconds'),
                    'fonts': usage,
                })
                self._signature = signature
                self._published = usage
                self._published_at = now
                return True
            except Exception as err:  # never let reporting break the display
                logger.debug("Could not publish font usage: %s", err, exc_info=True)
                return False

    def start(self, interval: float = TICK_INTERVAL) -> None:
        """Tick now, then from a daemon thread until stop(). No-op while running."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()

        def run() -> None:
            self.tick()
            while not self._stop.wait(interval):
                self.tick()

        self._thread = threading.Thread(target=run, name="font-usage-publisher", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
            self._thread = None


def start_font_usage_publisher(cache_manager: Any, font_manager: Any,
                               plugin_manager: Any) -> Optional[FontUsagePublisher]:
    """Start publishing font usage for the web interface. Never raises.

    Call from the display service, once its plugins are loaded.
    """
    try:
        if cache_manager is None or font_manager is None or plugin_manager is None:
            return None
        publisher = FontUsagePublisher(cache_manager, font_manager, plugin_manager)
        publisher.start()
        return publisher
    except Exception as err:
        logger.warning("Font usage reporting to the web interface is unavailable: %s", err)
        return None


# --- Reading side (web interface) -------------------------------------------

def read_font_usage(cache_manager: Any) -> Optional[Dict[str, Any]]:
    """The display service's latest snapshot, or None if it has not published.

    memory_ttl=0: the display service writes this key, so only the file is
    current. The contents are checked and bounded -- they arrive from another
    process through a shared directory.
    """
    try:
        snapshot = cache_manager.get(FONT_USAGE_KEY, max_age=None, memory_ttl=0)
    except Exception as err:
        logger.debug("Could not read font usage: %s", err, exc_info=True)
        return None
    if not isinstance(snapshot, dict) or not isinstance(snapshot.get('fonts'), dict):
        return None
    fonts: Dict[str, List[str]] = {}
    for key, ids in list(snapshot['fonts'].items())[:_MAX_FONTS]:
        if not isinstance(key, str) or not isinstance(ids, list):
            continue
        clean = sorted({i[:_MAX_ID_CHARS] for i in ids[:_MAX_PLUGINS_PER_FONT]
                        if isinstance(i, str) and i})
        if clean:
            fonts[key[:_MAX_ID_CHARS]] = clean
    generated_at = snapshot.get('generated_at')
    return {
        'generated_at': generated_at if isinstance(generated_at, str) else None,
        'fonts': fonts,
    }
