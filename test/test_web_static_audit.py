"""
Static-analysis audits for the web UI, as tests so CI enforces them.

1. Breakpoint utility audit: app.css hand-maintains a Tailwind-style utility
   subset, so a template can reference a responsive class (e.g. sm:block)
   that no CSS rule defines — it silently no-ops. This once left the header
   search box and system stats invisible at every screen width. The audit
   diffs classes used in templates against classes defined in app.css.

2. Asset reference audit: every url_for('static', filename=...) in the
   templates must point to a file that exists, so a renamed/moved asset
   can't ship as a broken <script>/<link>/<img>.

3. debugLog globals audit: any static JS file calling debugLog() (a global
   defined in base.html) must declare it in a /* global */ header so linting
   stays clean and the dependency is explicit.
"""

import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
WEB = PROJECT_ROOT / "web_interface"
TEMPLATES = WEB / "templates"
STATIC = WEB / "static"
APP_CSS = STATIC / "v3" / "app.css"

BP_PREFIXES = ("sm", "md", "lg", "xl", "2xl")


def _template_files():
    return sorted(TEMPLATES.rglob("*.html"))


def test_every_used_breakpoint_class_is_defined():
    used = set()
    class_attr = re.compile(r'class="([^"]*)"')
    bp_class = re.compile(r"\b(%s):[A-Za-z0-9_.-]+" % "|".join(BP_PREFIXES))
    for path in _template_files():
        for attr in class_attr.findall(path.read_text()):
            for m in bp_class.finditer(attr):
                used.add(m.group(0))

    css = APP_CSS.read_text()
    defined = {
        m.group(0).lstrip(".").replace("\\:", ":")
        for m in re.finditer(
            r"\.(%s)\\:[A-Za-z0-9_-]+" % "|".join(BP_PREFIXES), css
        )
    }

    missing = sorted(used - defined)
    assert not missing, (
        "Responsive utility classes referenced in templates but never defined "
        f"in app.css (they silently no-op): {missing}"
    )


def test_every_static_url_for_points_to_a_real_file():
    ref = re.compile(
        r"url_for\(\s*['\"]static['\"]\s*,\s*filename\s*=\s*['\"]([^'\"]+)['\"]"
    )
    missing = []
    for path in _template_files():
        for filename in ref.findall(path.read_text()):
            if not (STATIC / filename).is_file():
                missing.append(f"{path.relative_to(PROJECT_ROOT)}: {filename}")
    assert not missing, f"Templates reference missing static assets: {missing}"


def test_js_files_calling_debuglog_declare_the_global():
    undeclared = []
    for path in sorted((STATIC / "v3").rglob("*.js")):
        if "vendor" in path.parts:
            continue
        text = path.read_text()
        # Calls debugLog( but neither defines it nor declares the global
        calls = re.search(r"(?<![.\w])debugLog\(", text)
        defines = "window.debugLog" in text
        declares = re.search(r"/\*\s*global[^*]*\bdebugLog\b", text)
        if calls and not defines and not declares:
            undeclared.append(str(path.relative_to(PROJECT_ROOT)))
    assert not undeclared, (
        f"JS files call debugLog() without a /* global debugLog */ header: {undeclared}"
    )
