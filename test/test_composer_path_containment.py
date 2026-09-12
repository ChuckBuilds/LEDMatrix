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
    # Neutralise the two layers in front so this exercises the containment
    # check itself; otherwise secure_filename rejects the payload first and a
    # startswith regression would go unnoticed here.
    monkeypatch.setattr(C, "_PLUGIN_ID_RE", re.compile(r"\A[\w./\\~-]+\Z"))
    monkeypatch.setattr(C, "secure_filename", lambda v: v)
    assert C._plugin_dir("../plugins-evil") is None


def test_containment_still_holds_if_the_sanitiser_is_defeated(plugins_dir, monkeypatch):
    """Each layer is tested on its own, not just the stack.

    secure_filename's equality guard rejects every traversal payload before the
    containment check sees it, so removing containment does not fail the other
    tests -- which would make it look load-bearing when it is not. Neutralise
    the regex *and* the sanitiser, and the realpath/commonpath check must still
    refuse everything on its own.
    """
    import re
    monkeypatch.setattr(C, "_PLUGIN_ID_RE", re.compile(r"\A[\w./\\~-]+\Z"))
    monkeypatch.setattr(C, "secure_filename", lambda v: v)
    import os
    base = os.path.realpath(str(plugins_dir))
    escaped = []
    for payload in TRAVERSAL:
        resolved = C._plugin_dir(payload)
        if resolved is None:
            continue
        real = os.path.realpath(str(resolved))
        # Inside the base is fine -- "...." and "~" are ordinary directory
        # names on Linux, so they are not escapes. What must never happen is
        # landing outside the base, or on the base itself: install() rmtrees
        # its target, so the plugins root resolving to a "plugin" would wipe
        # every installed plugin.
        if real == base or os.path.commonpath([base, real]) != base:
            escaped.append((payload, real))
    assert not escaped, f"containment alone let these through: {escaped}"


def test_secure_filename_never_rewrites_an_accepted_id(plugins_dir):
    """The sanitiser must be a no-op on everything the regex accepts.

    If secure_filename ever altered an accepted id, _plugin_dir would resolve
    to a *different* plugin's directory than the caller asked for -- a silent
    redirect, which is worse than a refusal. The guard turns that into a
    refusal; this proves the guard never has to fire in practice.
    """
    import random
    from werkzeug.utils import secure_filename
    random.seed(1)
    alphabet = "abcdefghijklmnopqrstuvwxyz0123456789-"
    altered = []
    for _ in range(2000):
        n = random.randint(1, 63)
        cand = random.choice("abcdefghijklmnopqrstuvwxyz") + "".join(
            random.choice(alphabet) for _ in range(n - 1))
        if C._PLUGIN_ID_RE.match(cand) and secure_filename(cand) != cand:
            altered.append((cand, secure_filename(cand)))
    assert not altered, f"secure_filename rewrote accepted ids: {altered[:5]}"


def test_a_trailing_newline_is_not_a_valid_id():
    r"""Python's `$` also matches before a trailing newline, so the original
    `^...$` accepted "myplugin\n" and would have created a directory whose
    name ends in one. \Z does not."""
    assert C._PLUGIN_ID_RE.match("myplugin") is not None
    assert C._PLUGIN_ID_RE.match("myplugin\n") is None


# --- font serving -----------------------------------------------------------

FONT_TRAVERSAL = [
    "../../../etc/passwd", "../config/config.json", "..%2f..%2fetc%2fpasswd",
    "PressStart2P-Regular.ttf/../../../etc/passwd", "/etc/passwd", "",
    "PressStart2P-Regular.TTF",          # case differs -> not the allowlisted name
    "PressStart2P-Regular.ttf ",         # trailing space
]


def test_serve_font_refuses_a_file_that_exists_but_is_not_allowlisted(monkeypatch, tmp_path):
    """The allowlist must be what refuses it, not a missing file.

    Asserting 404 on traversal payloads proves nothing here: Flask's router
    will not match a path segment containing '/', and everything else 404s
    simply because no such file exists. Put a real, readable file next to the
    fonts and confirm it is still refused -- that is the allowlist working.
    """
    fonts = tmp_path / "assets" / "fonts"
    fonts.mkdir(parents=True)
    (fonts / "id_rsa.ttf").write_bytes(b"PRIVATE KEY")
    monkeypatch.setattr(C.composer_bp, "project_root", str(tmp_path), raising=False)
    app = __import__("flask").Flask(__name__)
    app.register_blueprint(C.composer_bp)
    with app.test_client() as client:
        resp = client.get("/api/fonts/id_rsa.ttf")
    assert resp.status_code == 404, (
        "a readable non-allowlisted file was served; the allowlist is not gating")
    assert b"PRIVATE KEY" not in resp.data


@pytest.mark.parametrize("payload", FONT_TRAVERSAL)
def test_serve_font_refuses_anything_not_allowlisted(payload, monkeypatch, tmp_path):
    """The name reaching the filesystem must come from the allowlist constant.

    _ALLOWED_FONTS gates this endpoint, so nothing here was ever exploitable.
    Building the path from the matched constant rather than the request value
    is what makes that provable -- and it is why CodeQL reported two
    high-severity py/path-injection alerts on an endpoint that was already
    safe.
    """
    monkeypatch.setattr(C.composer_bp, "project_root", str(tmp_path), raising=False)
    app = C.composer_bp.name and __import__("flask").Flask(__name__)
    app.register_blueprint(C.composer_bp)
    with app.test_client() as client:
        resp = client.get(f"/api/fonts/{payload}")
    assert resp.status_code in (404, 405, 308), (
        f"{payload!r} was not refused (status {resp.status_code})")


def test_serve_font_still_serves_each_allowlisted_font(monkeypatch, tmp_path):
    fonts = tmp_path / "assets" / "fonts"
    fonts.mkdir(parents=True)
    monkeypatch.setattr(C.composer_bp, "project_root", str(tmp_path), raising=False)
    app = __import__("flask").Flask(__name__)
    app.register_blueprint(C.composer_bp)
    for name in C._ALLOWED_FONTS:
        (fonts / name).write_bytes(b"\x00\x01ttf")
        with app.test_client() as client:
            resp = client.get(f"/api/fonts/{name}")
        assert resp.status_code == 200, f"{name} should be served, got {resp.status_code}"
