"""
Files a plugin writes beside itself at runtime, which an update must keep.

A store update replaces a plugin's directory with a fresh download and then
deletes the old copy. Anything the plugin created there -- OAuth tokens, a
client-secrets file, a PKCE verifier, cached state -- is in no release, so the
fresh download does not contain it and deleting the old copy destroys it. On
2026-10-04 updating calendar 1.2.9 -> 1.2.12 that way deleted its
``token.pickle`` and ``credentials.json``, and the calendar stopped until they
were restored from a backup.

What counts as "the plugin's own local file" is the union of:

* :data:`KNOWN_STATE_PATTERNS` -- secret and state files plugins are known to
  write, kept even when a plugin forgot to gitignore them; and
* whatever the plugin's own ``.gitignore`` (old copy or new) excludes. A file
  the author ignores is by definition not part of a release.

A file the new release ships is never overwritten: tracked content wins. Byte
code (``__pycache__``, ``*.pyc``) and ``.git`` are never carried, since they
belong to the old code rather than to the user.
"""

from __future__ import annotations

import fnmatch
import os
import re
import shutil
from pathlib import Path
from typing import Iterable, List, Optional, Pattern, Tuple

__all__ = [
    'KNOWN_STATE_PATTERNS',
    'carry_over_local_files',
    'is_known_state_file',
    'local_files_to_keep',
]

# Basename globs. Kept even when the plugin's .gitignore does not list them.
KNOWN_STATE_PATTERNS: Tuple[str, ...] = (
    'token.pickle',
    '*.pickle',
    'token.json',
    'credentials.json',
    'config_secrets.json',
    '.pkce_code_verifier',
)

_NEVER_CARRY_DIRS = frozenset({'.git', '__pycache__'})
_NEVER_CARRY_SUFFIXES = ('.pyc', '.pyo')


def is_known_state_file(rel_path: str) -> bool:
    """True when ``rel_path``'s basename is a known secret/state file."""
    name = rel_path.replace('\\', '/').rsplit('/', 1)[-1]
    return any(fnmatch.fnmatchcase(name, p) for p in KNOWN_STATE_PATTERNS)


class _GitIgnore:
    """The subset of gitignore semantics plugin .gitignore files use.

    Supports comments, ``!`` negation (last match wins), a trailing ``/`` for
    directory-only patterns, anchoring by a leading or embedded ``/``, ``*``,
    ``?``, ``[...]`` and ``**``. As in git, a file under an ignored directory
    is ignored regardless of later negations.
    """

    def __init__(self, lines: Iterable[str]):
        self._rules: List[Tuple[Pattern[str], bool, bool]] = []
        for raw in lines:
            line = raw.rstrip('\n').rstrip()
            if not line or line.startswith('#'):
                continue
            negate = line.startswith('!')
            if negate:
                line = line[1:]
            elif line.startswith('\\'):
                line = line[1:]
            dir_only = line.endswith('/')
            line = line.rstrip('/')
            if not line:
                continue
            anchored = '/' in line
            line = line.lstrip('/')
            body = self._translate(line)
            regex = body if anchored else r'(?:.*/)?' + body
            self._rules.append((re.compile(r'\A' + regex + r'\Z'), negate, dir_only))

    @staticmethod
    def _translate(pattern: str) -> str:
        out, i, n = [], 0, len(pattern)
        while i < n:
            if pattern.startswith('**/', i):
                out.append(r'(?:.*/)?')
                i += 3
            elif pattern.startswith('/**', i) and i + 3 == n:
                out.append(r'/.*')
                i += 3
            elif pattern.startswith('**', i):
                out.append(r'.*')
                i += 2
            elif pattern[i] == '*':
                out.append(r'[^/]*')
                i += 1
            elif pattern[i] == '?':
                out.append(r'[^/]')
                i += 1
            elif pattern[i] == '[':
                end = pattern.find(']', i + 1)
                if end == -1:
                    out.append(re.escape('['))
                    i += 1
                else:
                    cls = pattern[i + 1:end]
                    if cls.startswith('!'):
                        cls = '^' + cls[1:]
                    out.append('[' + cls.replace('\\', '\\\\') + ']')
                    i = end + 1
            else:
                out.append(re.escape(pattern[i]))
                i += 1
        return ''.join(out)

    def _decide(self, rel: str, is_dir: bool) -> Optional[bool]:
        verdict = None
        for regex, negate, dir_only in self._rules:
            if dir_only and not is_dir:
                continue
            if regex.match(rel):
                verdict = not negate
        return verdict

    def ignores(self, rel_path: str) -> bool:
        if not self._rules:
            return False
        parts = rel_path.replace('\\', '/').split('/')
        for depth in range(1, len(parts)):
            if self._decide('/'.join(parts[:depth]), True):
                return True
        return bool(self._decide('/'.join(parts), False))


def _read_gitignore(plugin_dir: Path) -> List[str]:
    try:
        return (plugin_dir / '.gitignore').read_text(
            encoding='utf-8', errors='replace').splitlines()
    except OSError:
        return []


def local_files_to_keep(old_dir: Path, new_dir: Path) -> List[str]:
    """Relative paths (``/``-separated) in ``old_dir`` to copy into ``new_dir``.

    Regular files only; symlinks and anything the new release already ships
    are skipped.
    """
    old_dir, new_dir = Path(old_dir), Path(new_dir)
    ignore = _GitIgnore(_read_gitignore(old_dir) + _read_gitignore(new_dir))
    keep: List[str] = []
    for root, dirs, files in os.walk(old_dir):
        dirs[:] = sorted(d for d in dirs if d not in _NEVER_CARRY_DIRS
                         and not os.path.islink(os.path.join(root, d)))
        rel_root = os.path.relpath(root, old_dir)
        for name in sorted(files):
            if name.endswith(_NEVER_CARRY_SUFFIXES):
                continue
            full = os.path.join(root, name)
            if os.path.islink(full) or not os.path.isfile(full):
                continue
            rel = name if rel_root == '.' else f"{rel_root}/{name}".replace('\\', '/')
            if not (is_known_state_file(rel) or ignore.ignores(rel)):
                continue
            if os.path.lexists(new_dir / rel):
                continue
            keep.append(rel)
    return keep


def carry_over_local_files(
    old_dir: Path, new_dir: Path
) -> Tuple[List[str], List[Tuple[str, str]]]:
    """Copy the plugin's local files from ``old_dir`` into ``new_dir``.

    Copies rather than moves, so ``old_dir`` stays a complete copy until the
    caller deletes it. Returns ``(copied, failed)`` where ``failed`` pairs a
    relative path with the error; the caller should keep ``old_dir`` when
    anything failed.
    """
    copied: List[str] = []
    failed: List[Tuple[str, str]] = []
    try:
        candidates = local_files_to_keep(old_dir, new_dir)
    except OSError as e:
        return copied, [('.', str(e))]
    for rel in candidates:
        dest = Path(new_dir) / rel
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(Path(old_dir) / rel, dest)
            copied.append(rel)
        except OSError as e:
            failed.append((rel, str(e)))
    return copied, failed
