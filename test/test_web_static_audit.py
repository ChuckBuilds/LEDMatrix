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
        for attr in class_attr.findall(path.read_text(encoding="utf-8")):
            for m in bp_class.finditer(attr):
                used.add(m.group(0))

    css = APP_CSS.read_text(encoding="utf-8")
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


_VARIANTS = (
    r"(?:(?:sm|md|lg|xl|2xl|hover|focus|focus-visible|active|disabled|first|last"
    r"|group-hover|peer-checked|peer-focus|placeholder|file):)*"
)
_UTILITY = re.compile(
    "^" + _VARIANTS + r"-?(?:"
    r"(?:block|inline|inline-block|inline-flex|flex|grid|hidden|table|contents"
    r"|static|fixed|absolute|relative|sticky|truncate|sr-only|transform"
    r"|uppercase|lowercase|capitalize|italic|underline|line-through|border|shadow"
    r"|rounded|transition|grow|shrink|group|peer)"
    r"|(?:m|p)[trblxy]?-[\w./]+"
    r"|(?:w|h|min-w|min-h|max-w|max-h|gap|gap-x|gap-y|space-x|space-y|top|right"
    r"|bottom|left|inset|inset-x|inset-y|z|opacity|duration|scale|rotate"
    r"|translate-x|translate-y|grid-cols|col-span|leading|tracking)-[\w./\[\]]+"
    r"|(?:text|bg|border|divide|ring|placeholder|accent)-(?:[a-z]+-\d{2,3}(?:/\d+)?"
    r"|white|black|transparent|xs|sm|base|md|lg|xl|[2-5]xl|left|center|right"
    r"|opacity-\d+|[trblxy](?:-\d)?|\d|dashed|dotted|offset-\d)"
    r"|font-(?:mono|sans|serif|thin|light|normal|medium|semibold|bold|extrabold)"
    r"|select-(?:none|all|text|auto)"
    r"|(?:rounded|shadow|items|justify|self|flex|whitespace|break|object|list"
    r"|overflow|overflow-x|overflow-y|cursor|pointer-events|appearance"
    r"|ease|divide|line-clamp)-[a-z0-9]+(?:-[a-z0-9]+)?(?:\[[^\]]+\])?"
    r")$"
)


def _css_light_classes(css):
    """Classes that get a rule outside [data-theme="dark"]."""
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    defined = set()
    for block in re.finditer(r"([^{}]+)\{", css):
        for sel in block.group(1).split(","):
            sel = sel.strip()
            if sel.startswith("@") or sel.startswith('[data-theme="dark"]'):
                continue
            for m in re.finditer(r"\.((?:\\.|[\w-])+)", sel):
                defined.add(m.group(1).replace("\\", ""))
    return defined


def test_every_used_utility_class_is_defined():
    """app.css is the whole stylesheet (no Tailwind build), so a utility class
    it doesn't define silently does nothing. `.hidden` was missing for years,
    which broke every JS show/hide toggle. Scans templates and static JS."""
    attr = re.compile(r"""(?:class|className)\s*[=:]\s*(["'`])(.*?)\1""", re.S)
    class_list = re.compile(r"classList\.(?:add|remove|toggle)\(([^)]*)\)")
    template_expr = re.compile(r"\{\{.*?\}\}|\{%.*?%\}|\$\{[^}]*\}", re.S)
    files = list(_template_files()) + [
        p for p in (STATIC / "v3").rglob("*.js")
        if "vendor" not in p.parts and not p.name.endswith(".min.js")
    ]
    used = {}
    for path in files:
        text = template_expr.sub(" ", path.read_text(encoding="utf-8"))
        chunks = [m.group(2) for m in attr.finditer(text)]
        chunks += re.findall(r"""['"]([^'"]*)['"]""", " ".join(
            m.group(1) for m in class_list.finditer(text)))
        # :class="{ 'hidden': open }" and ternaries hold class names inside
        # string literals; read those as well as the raw chunk.
        bound = re.compile(r""":class\s*=\s*"([^"]*)""")
        for m in bound.finditer(text):
            chunks.append(m.group(1))
        for chunk in chunks:
            candidates = [chunk] + re.findall(r"""['`]([^'`]*)['`]""", chunk)
            for candidate in candidates:
                for token in candidate.split():
                    if _UTILITY.match(token):
                        used.setdefault(token, path.relative_to(PROJECT_ROOT))

    defined = _css_light_classes(APP_CSS.read_text(encoding="utf-8"))
    missing = sorted(f"{cls} ({used[cls]})" for cls in used if cls not in defined)
    assert not missing, (
        "Utility classes used in templates/JS but not defined in app.css "
        f"(they silently no-op): {missing}"
    )


def test_every_static_url_for_points_to_a_real_file():
    ref = re.compile(
        r"url_for\(\s*['\"]static['\"]\s*,\s*filename\s*=\s*['\"]([^'\"]+)['\"]"
    )
    missing = []
    for path in _template_files():
        for filename in ref.findall(path.read_text(encoding="utf-8")):
            if not (STATIC / filename).is_file():
                missing.append(f"{path.relative_to(PROJECT_ROOT)}: {filename}")
    assert not missing, f"Templates reference missing static assets: {missing}"


def test_js_files_calling_debuglog_declare_the_global():
    undeclared = []
    for path in sorted((STATIC / "v3").rglob("*.js")):
        if "vendor" in path.parts:
            continue
        text = path.read_text(encoding="utf-8")
        # Calls debugLog( but neither defines it nor declares the global
        calls = re.search(r"(?<![.\w])debugLog\(", text)
        defines = "window.debugLog" in text
        declares = re.search(r"/\*\s*global[^*]*\bdebugLog\b", text)
        if calls and not defines and not declares:
            undeclared.append(str(path.relative_to(PROJECT_ROOT)))
    assert not undeclared, (
        f"JS files call debugLog() without a /* global debugLog */ header: {undeclared}"
    )
