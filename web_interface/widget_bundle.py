"""Single-request bundle for the config-form widget scripts.

Every page used to carry one <script> tag per widget (34 of them, ~700 KB
uncompressed). On a Pi Zero 2 W serving a phone over WiFi, each tag is a
separate request the Python server has to answer. The files are plain scripts
that register themselves on window/LEDMatrixWidgets at load time, so
concatenating them in the same order is exactly equivalent to loading them
one by one — no module wrapper, no scope change.

The individual files stay on disk and keep working as direct URLs, which is
what plugin-loader.js and any third-party page expect.

BUNDLE_ORDER is the authority for which widgets ship; test_widget_scripts.py
checks it against the directory so a new widget can't be forgotten.
"""
from pathlib import Path
from threading import Lock

WIDGETS_DIR = Path(__file__).parent / "static" / "v3" / "js" / "widgets"

# Load order matters: the registry and base class must exist before the
# widgets that call them, and notification.js owns window.showNotification.
BUNDLE_ORDER = [
    "registry.js",
    "base-widget.js",
    "notification.js",
    "plugin-order-list.js",
    "file-upload.js",
    "checkbox-group.js",
    "custom-feeds.js",
    "array-table.js",
    "google-calendar-picker.js",
    "google-oauth.js",
    "day-selector.js",
    "time-range.js",
    "time-picker.js",
    "file-upload-single.js",
    "plugin-file-manager.js",
    "schedule-picker.js",
    # Basic input widgets
    "text-input.js",
    "number-input.js",
    "textarea.js",
    "select-dropdown.js",
    "font-selector.js",
    "toggle-switch.js",
    "radio-group.js",
    "date-picker.js",
    "slider.js",
    "color-picker.js",
    "email-input.js",
    "url-input.js",
    "password-input.js",
    "timezone-selector.js",
    "plugin-loader.js",
    # Reusable JSON file manager (used via x-widget: json-file-manager)
    "json-file-manager.js",
]

# Widget files that must NOT be bundled, with the reason.
EXCLUDED = {
    # Documentation example (docs/widget-guide.md); it registers the name
    # 'color-picker' and would shadow the real color-picker.js.
    "example-color-picker.js": "documentation example",
}

_lock = Lock()
_cache = {"version": None, "body": None}


def bundle_paths():
    """Widget files in load order (only those present on disk)."""
    return [WIDGETS_DIR / name for name in BUNDLE_ORDER if (WIDGETS_DIR / name).is_file()]


def bundle_version():
    """Newest mtime across the bundled files — the cache-busting token."""
    try:
        return max(int(p.stat().st_mtime) for p in bundle_paths())
    except ValueError:
        return 0


def build_bundle():
    """Concatenated widget sources, rebuilt only when a file changes."""
    version = bundle_version()
    with _lock:
        if _cache["version"] == version and _cache["body"] is not None:
            return _cache["body"], version
        parts = []
        for path in bundle_paths():
            source = path.read_text(encoding="utf-8")
            # A file ending in a line comment would swallow the next file's
            # first line, and a missing semicolon can join two statements.
            parts.append("/* %s */\n%s\n;\n" % (path.name, source))
        body = "".join(parts)
        _cache["version"] = version
        _cache["body"] = body
        return body, version
