"""Guard: every literal render_template() target must exist on disk.

Catches routes that reference templates deleted in a refactor (a real bug
class: the weather/stocks partials 500'd for months because their
templates were removed when those displays became plugins).
"""
import re
from pathlib import Path
from typing import Iterator

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TEMPLATE_ROOT = PROJECT_ROOT / 'web_interface' / 'templates'
RENDER_RE = re.compile(r"""render_template\(\s*['"]([^'"]+)['"]""")


def _python_sources() -> Iterator[Path]:
    """Yield every Python source that can call render_template()."""
    yield PROJECT_ROOT / 'web_interface' / 'app.py'
    yield from (PROJECT_ROOT / 'web_interface' / 'blueprints').glob('*.py')


def test_all_literal_render_template_targets_exist() -> None:
    """Every string-literal render_template() target must exist on disk."""
    missing = []
    for source in _python_sources():
        text = source.read_text(encoding='utf-8')
        for match in RENDER_RE.finditer(text):
            target = match.group(1)
            if not (TEMPLATE_ROOT / target).is_file():
                lineno = text.count('\n', 0, match.start()) + 1
                missing.append(
                    f'{source.relative_to(PROJECT_ROOT)}:{lineno} -> {target}'
                )
    assert not missing, (
        'render_template() references templates that do not exist under '
        'web_interface/templates/:\n' + '\n'.join(missing)
    )
