"""A composer plugin id must never resolve outside the plugins directory.

CodeQL reported sixteen high-severity py/path-injection alerts against
web_interface/blueprints/composer.py: a request-supplied plugin_id reaching
Path(plugins_dir) / plugin_id, which is then created, written to, deleted
(shutil.rmtree) and read back.

The id was already validated by an anchored regex, so every traversal payload
was in fact rejected. What was missing was the guarantee living *with* the path
building rather than in a regex several hundred lines away -- loosen that regex
later and the traversal opens silently, with nothing at the filesystem boundary
to catch it. _plugin_dir() closes that, and is the form static analysis can see.
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from web_interface.blueprints import composer as C  # noqa: E402

#: Anything that has ever been used to climb out of a directory.
TRAVERSAL = [
    "../../etc/passwd", "..", ".", "a/../../etc", "good/../../..",
    "/etc/passwd", "//etc/passwd", "a\\..\\..", "a%2f..%2f..",
    "....//....//etc", "a/./../../etc", "~", "~root",
    "plugin/../../../../../../etc/shadow",
]

#: Rejected for shape, not traversal -- but rejected all the same.
MALFORMED = ["", "A-upper", "1-leading-digit", "-leading-dash", "has_underscore",
             "has space", "has.dot", "a" * 64, "plugin\n", "plugin\n../../etc",
             "\n", "plug\x00in"]


@pytest.fixture
def plugins_dir(tmp_path, monkeypatch):
    base = tmp_path / "plugin-repos"
    base.mkdir()
    monkeypatch.setattr(C.composer_bp, "plugins_dir", str(base), raising=False)
    return base


@pytest.mark.parametrize("payload", TRAVERSAL)
def test_traversal_payloads_are_refused(plugins_dir, payload):
    assert C._plugin_dir(payload) is None


@pytest.mark.parametrize("payload", MALFORMED)
def test_malformed_ids_are_refused(plugins_dir, payload):
    assert C._plugin_dir(payload) is None


@pytest.mark.parametrize("payload", ["a", "my-plugin", "x9", "a" * 63])
def test_valid_ids_resolve_inside_the_base(plugins_dir, payload):
    resolved = C._plugin_dir(payload)
    assert resolved is not None, f"{payload!r} was rejected but is valid"
    assert resolved.parent == plugins_dir.resolve(), (
        f"{payload!r} resolved to {resolved}, outside {plugins_dir}")


def test_no_payload_can_escape_even_if_the_regex_is_loosened(plugins_dir, monkeypatch):
    """The containment check must stand on its own.

    This is the whole point of resolving at the filesystem boundary: if the id
    pattern is ever relaxed, traversal must still be impossible. Replace the
    regex with one that permits slashes and dots, then re-run the payloads.
    """
    import re
    monkeypatch.setattr(C, "_PLUGIN_ID_RE", re.compile(r"\A[\w./\\~-]+\Z"))
    import os
    escaped = []
    base = os.path.realpath(str(plugins_dir))
    for payload in TRAVERSAL:
        resolved = C._plugin_dir(payload)
        if resolved is None:
            continue
        real = os.path.realpath(str(resolved))
        if real != base and os.path.commonpath([base, real]) != base:
            escaped.append((payload, real))
    assert not escaped, f"these escaped the base with a loosened regex: {escaped}"


def test_a_sibling_directory_with_a_shared_prefix_is_not_inside(tmp_path, monkeypatch):
    """commonpath, not startswith.

    "/x/plugins-evil" starts with "/x/plugins" but is a different directory, so
    a prefix test would accept it.
    """
    base = tmp_path / "plugins"
    base.mkdir()
    (tmp_path / "plugins-evil").mkdir()
    monkeypatch.setattr(C.composer_bp, "plugins_dir", str(base), raising=False)
    import re
    monkeypatch.setattr(C, "_PLUGIN_ID_RE", re.compile(r"\A[\w./\\~-]+\Z"))
    assert C._plugin_dir("../plugins-evil") is None


def test_a_trailing_newline_is_not_a_valid_id():
    r"""Python's `$` also matches before a trailing newline, so the original
    `^...$` accepted "myplugin\n" and would have created a directory whose
    name ends in one. \Z does not."""
    assert C._PLUGIN_ID_RE.match("myplugin") is not None
    assert C._PLUGIN_ID_RE.match("myplugin\n") is None
