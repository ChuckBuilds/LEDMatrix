"""Guard: every widget JS file must be loaded by base.html or explicitly allowlisted.

Widget files register themselves with LEDMatrixWidgets at load time; a file
that exists but is never <script>-included silently breaks any plugin whose
config schema declares that widget (the field renders as an empty container
that polls the registry forever). base.html's widget list is maintained by
hand, so this test keeps it honest.
"""
import re
from pathlib import Path
from typing import Set

PROJECT_ROOT = Path(__file__).resolve().parent.parent
WIDGETS_DIR = PROJECT_ROOT / 'web_interface' / 'static' / 'v3' / 'js' / 'widgets'
BASE_HTML = PROJECT_ROOT / 'web_interface' / 'templates' / 'v3' / 'base.html'

# Matches url_for('static', filename='...') inside actual <script> tags.
SCRIPT_SRC_RE = re.compile(
    r"""<script\s[^>]*src="\{\{\s*url_for\(\s*'static'\s*,\s*filename='([^']+)'\s*\)\s*\}\}[^"]*"""
)

# Files that must NOT be script-included, with the reason.
ALLOWLIST = {
    # Documentation example (docs/widget-guide.md); registers the name
    # 'color-picker' and would shadow the real color-picker.js if loaded.
    'example-color-picker.js',
}


def _included_widget_scripts() -> Set[str]:
    """Return widget JS basenames referenced by real <script> tags in base.html."""
    base_html = BASE_HTML.read_text(encoding='utf-8')
    return {
        Path(filename).name
        for filename in SCRIPT_SRC_RE.findall(base_html)
        if filename.startswith('v3/js/widgets/')
    }


def test_every_widget_script_is_included_in_base_html() -> None:
    """Every non-allowlisted widget file must be loaded by a <script> tag."""
    assert WIDGETS_DIR.is_dir(), f'Widget directory missing: {WIDGETS_DIR}'
    included = _included_widget_scripts()
    assert included, 'No widget <script> tags found in base.html — regex or template drift?'
    missing = [
        js_file.name
        for js_file in sorted(WIDGETS_DIR.glob('*.js'))
        if js_file.name not in ALLOWLIST and js_file.name not in included
    ]
    assert not missing, (
        'Widget files exist but are never <script>-included in base.html '
        '(plugins declaring these widgets get blank config fields): '
        + ', '.join(missing)
        + '. Add a script tag to base.html or add the file to ALLOWLIST '
        'with a reason.'
    )


def test_allowlisted_widgets_are_not_included() -> None:
    """Allowlisted (must-not-load) widget files must stay out of base.html."""
    included = _included_widget_scripts()
    wrongly_included = [name for name in ALLOWLIST if name in included]
    assert not wrongly_included, (
        'Allowlisted (must-not-load) widget files are script-included in '
        'base.html: ' + ', '.join(wrongly_included)
    )
