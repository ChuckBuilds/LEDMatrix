"""Guard: relative markdown links in active docs must resolve.

Scans repo-root *.md and docs/ (excluding docs/archive/, which is allowed
to rot). External URLs, mailto links, and pure anchors are skipped, as are
links inside fenced code blocks.
"""
import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
LINK_RE = re.compile(r'\[[^\]]*\]\(([^)\s]+)\)')
FENCE_RE = re.compile(r'^(```|~~~)')


def _md_files():
    yield from PROJECT_ROOT.glob('*.md')
    for path in PROJECT_ROOT.glob('docs/**/*.md'):
        if 'archive' not in path.parts:
            yield path


def test_relative_markdown_links_resolve():
    broken = []
    for md in _md_files():
        in_fence = False
        for lineno, line in enumerate(md.read_text(encoding='utf-8').splitlines(), 1):
            if FENCE_RE.match(line.strip()):
                in_fence = not in_fence
                continue
            if in_fence:
                continue
            for target in LINK_RE.findall(line):
                if target.startswith(('http://', 'https://', 'mailto:', '#')):
                    continue
                resolved = (md.parent / target.split('#')[0]).resolve()
                if not resolved.exists():
                    broken.append(
                        f'{md.relative_to(PROJECT_ROOT)}:{lineno} -> {target}'
                    )
    assert not broken, 'Broken relative markdown links:\n' + '\n'.join(broken)
