"""Guard: every literal render_template() target must exist on disk.

Catches routes that reference templates deleted in a refactor (a real bug
class: the weather/stocks partials 500'd for months because their
templates were removed when those displays became plugins).
"""
import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TEMPLATE_ROOT = PROJECT_ROOT / 'web_interface' / 'templates'
RENDER_RE = re.compile(r"""render_template\(\s*['"]([^'"]+)['"]""")


def _python_sources():
    yield PROJECT_ROOT / 'web_interface' / 'app.py'
    yield from (PROJECT_ROOT / 'web_interface' / 'blueprints').glob('*.py')


def test_all_literal_render_template_targets_exist():
    missing = []
    for source in _python_sources():
        text = source.read_text(encoding='utf-8')
        for lineno, line in enumerate(text.splitlines(), 1):
            for target in RENDER_RE.findall(line):
                if not (TEMPLATE_ROOT / target).is_file():
                    missing.append(
                        f'{source.relative_to(PROJECT_ROOT)}:{lineno} -> {target}'
                    )
    assert not missing, (
        'render_template() references templates that do not exist under '
        f'web_interface/templates/:\n' + '\n'.join(missing)
    )
