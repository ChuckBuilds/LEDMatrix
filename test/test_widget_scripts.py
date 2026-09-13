"""Guard: every widget JS file must ship in the widget bundle or be allowlisted.

Widget files register themselves with LEDMatrixWidgets at load time; a file
that exists but is never loaded silently breaks any plugin whose config schema
declares that widget (the field renders as an empty container that polls the
registry forever).

base.html loads them as one concatenated request (web_interface/widget_bundle.py),
so BUNDLE_ORDER is the hand-maintained list this test keeps honest. It also
checks that base.html actually requests the bundle, and that the bundle's
concatenation order puts the registry and base class first.
"""
import re
import sys
from pathlib import Path
from typing import Set

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from web_interface import widget_bundle  # noqa: E402

WIDGETS_DIR = PROJECT_ROOT / 'web_interface' / 'static' / 'v3' / 'js' / 'widgets'
BASE_HTML = PROJECT_ROOT / 'web_interface' / 'templates' / 'v3' / 'base.html'

# Files that must NOT be loaded, with the reason.
ALLOWLIST = set(widget_bundle.EXCLUDED)


def _bundled_names() -> Set[str]:
    return set(widget_bundle.BUNDLE_ORDER)


def test_every_widget_script_is_bundled() -> None:
    """Every non-allowlisted widget file must be in BUNDLE_ORDER."""
    assert WIDGETS_DIR.is_dir(), f'Widget directory missing: {WIDGETS_DIR}'
    bundled = _bundled_names()
    assert bundled, 'BUNDLE_ORDER is empty — widget_bundle drift?'
    missing = [
        js_file.name
        for js_file in sorted(WIDGETS_DIR.glob('*.js'))
        if js_file.name not in ALLOWLIST and js_file.name not in bundled
    ]
    assert not missing, (
        'Widget files exist but are never loaded (plugins declaring these '
        'widgets get blank config fields): ' + ', '.join(missing)
        + '. Add the file to BUNDLE_ORDER in web_interface/widget_bundle.py, '
        'or to EXCLUDED with a reason.'
    )


def test_allowlisted_widgets_are_not_bundled() -> None:
    """Allowlisted (must-not-load) widget files stay out of the bundle."""
    wrongly_included = sorted(ALLOWLIST & _bundled_names())
    assert not wrongly_included, (
        'Allowlisted (must-not-load) widget files are in the bundle: '
        + ', '.join(wrongly_included)
    )


def test_bundle_order_lists_only_existing_files() -> None:
    """A renamed or deleted widget must not linger in BUNDLE_ORDER."""
    stale = [name for name in widget_bundle.BUNDLE_ORDER
             if not (WIDGETS_DIR / name).is_file()]
    assert not stale, f'BUNDLE_ORDER names files that do not exist: {stale}'


def test_registry_and_base_widget_load_first() -> None:
    """Widgets call into the registry and base class as they load."""
    order = widget_bundle.BUNDLE_ORDER
    assert order[0] == 'registry.js', order[:3]
    assert order[1] == 'base-widget.js', order[:3]


def test_base_html_requests_the_bundle() -> None:
    """base.html must load the bundle (and no longer tag widgets one by one)."""
    base_html = BASE_HTML.read_text(encoding='utf-8')
    assert 'widgets_bundle_url()' in base_html, (
        'base.html does not request the widget bundle'
    )
    per_file = re.findall(
        r"""<script\s[^>]*src="\{\{\s*url_for\(\s*'static'\s*,\s*"""
        r"""filename='(v3/js/widgets/[^']+)'""",
        base_html,
    )
    assert not per_file, (
        'base.html still loads widget files individually alongside the '
        f'bundle (they would run twice): {per_file}'
    )


def test_bundle_concatenates_every_file() -> None:
    """The built bundle contains each file, separated so sources can't merge."""
    body, version = widget_bundle.build_bundle()
    assert version > 0
    for name in widget_bundle.BUNDLE_ORDER:
        assert f'/* {name} */' in body, f'{name} missing from the built bundle'
    for name in ALLOWLIST:
        assert f'/* {name} */' not in body, f'{name} must not be bundled'
