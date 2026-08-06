"""Guard: every widget JS file must be loaded by base.html or explicitly allowlisted.

Widget files register themselves with LEDMatrixWidgets at load time; a file
that exists but is never <script>-included silently breaks any plugin whose
config schema declares that widget (the field renders as an empty container
that polls the registry forever). base.html's widget list is maintained by
hand, so this test keeps it honest.
"""
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
WIDGETS_DIR = PROJECT_ROOT / 'web_interface' / 'static' / 'v3' / 'js' / 'widgets'
BASE_HTML = PROJECT_ROOT / 'web_interface' / 'templates' / 'v3' / 'base.html'

# Files that must NOT be script-included, with the reason.
ALLOWLIST = {
    # Documentation example (docs/widget-guide.md); registers the name
    # 'color-picker' and would shadow the real color-picker.js if loaded.
    'example-color-picker.js',
}


def test_every_widget_script_is_included_in_base_html():
    base_html = BASE_HTML.read_text(encoding='utf-8')
    missing = []
    for js_file in sorted(WIDGETS_DIR.glob('*.js')):
        if js_file.name in ALLOWLIST:
            continue
        if f'v3/js/widgets/{js_file.name}' not in base_html:
            missing.append(js_file.name)
    assert not missing, (
        'Widget files exist but are never <script>-included in base.html '
        '(plugins declaring these widgets get blank config fields): '
        + ', '.join(missing)
        + '. Add a script tag to base.html or add the file to ALLOWLIST '
        'with a reason.'
    )


def test_allowlisted_widgets_are_not_included():
    base_html = BASE_HTML.read_text(encoding='utf-8')
    wrongly_included = [
        name for name in ALLOWLIST if f'v3/js/widgets/{name}' in base_html
    ]
    assert not wrongly_included, (
        'Allowlisted (must-not-load) widget files are script-included in '
        'base.html: ' + ', '.join(wrongly_included)
    )
