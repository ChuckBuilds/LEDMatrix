"""src.common.sports_font_path: the plugins' ``_resolve_font_path``, path for path.

The plugins' copy probes the core for ``FontManager._resolve_asset_path``
and falls back to its own install-root join; ``resolve_font_path`` is what
that comes to on a core that ships it. The bodies differ, so instead of an
AST comparison this runs both on the same paths -- found in the cwd only,
under the install root only, in both, absolute, and nowhere -- from a
temporary cwd, and requires the same string back. The plugin copies are read
from LEDMATRIX_PLUGINS (every sports.py and game_renderer.py that still has
one); without it, the comparison is against the copy transcribed below.
"""

import ast
import os
from pathlib import Path

import pytest

from src.common.font_layout import resolve_asset_path
from src.common.sports_font_path import resolve_font_path

REPO = Path(__file__).resolve().parents[1]
BUNDLED = "assets/fonts/PressStart2P-Regular.ttf"

#: ledmatrix-plugins 56c4f15, plugins/*-scoreboard/sports.py (docstring and
#: comments dropped). The same body is in every sports.py and game_renderer.py.
TRANSCRIBED = '''
def _resolve_font_path(path: str) -> str:
    if os.path.exists(path):
        return path
    try:
        import src.font_manager as _core_fonts
        manager = getattr(_core_fonts, "FontManager", None)
        resolver = getattr(manager, "_resolve_asset_path", None)
        if resolver is not None:
            resolved = resolver(path)
            if resolved and os.path.exists(resolved):
                return resolved
        root = os.path.dirname(os.path.dirname(os.path.abspath(_core_fonts.__file__)))
        candidate = os.path.join(root, path)
        if os.path.exists(candidate):
            return candidate
    except (ImportError, AttributeError, OSError):
        return path
    return path
'''


def _compile(source: str):
    namespace = {"os": os}
    exec(compile(source, "<plugin copy>", "exec"), namespace)  # nosec B102 - test-only, source is a plugin file  # nosemgrep
    return namespace["_resolve_font_path"]


def _plugin_copies():
    """(label, function) for every plugin copy, or the transcription."""
    raw = os.environ.get("LEDMATRIX_PLUGINS")
    root = Path(raw) if raw else None
    if root is not None and (root / "plugins").is_dir():
        root = root / "plugins"
    copies = []
    if root is not None and root.is_dir():
        for path in sorted(root.glob("*-scoreboard/*.py")):
            if path.name not in ("sports.py", "game_renderer.py"):
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in tree.body:
                if isinstance(node, ast.FunctionDef) and node.name == "_resolve_font_path":
                    copies.append((f"{path.parent.name}/{path.name}",
                                   _compile(ast.unparse(node))))
    if not copies:
        copies.append(("transcribed", _compile(TRANSCRIBED)))
    return copies


COPIES = _plugin_copies()


@pytest.fixture
def elsewhere(tmp_path, monkeypatch):
    """A cwd that is not the install root, holding one font of its own and a
    shadow of a bundled one."""
    (tmp_path / "cwd_only.ttf").write_bytes(b"x")
    shadow = tmp_path / BUNDLED
    shadow.parent.mkdir(parents=True)
    shadow.write_bytes(b"x")
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _cases(cwd: Path):
    return [
        "cwd_only.ttf",                        # in the cwd only
        BUNDLED,                               # in both: the cwd wins
        "assets/fonts/4x6-font.ttf",           # under the install root only
        str(REPO / BUNDLED),                   # absolute, exists
        str(cwd / "missing.ttf"),              # absolute, missing
        "assets/fonts/no-such-font.ttf",       # relative, nowhere
        "",                                    # empty
    ]


@pytest.mark.parametrize("label,copy", COPIES, ids=[c[0] for c in COPIES])
def test_same_answer_as_the_plugin_copy(label, copy, elsewhere):
    for path in _cases(elsewhere):
        assert resolve_font_path(path) == copy(path), (label, path)


def test_the_cwd_comes_first(elsewhere):
    assert resolve_font_path(BUNDLED) == BUNDLED
    assert resolve_asset_path(BUNDLED) != BUNDLED      # what dropping it would change


def test_the_install_root_after_it(elsewhere):
    found = resolve_font_path("assets/fonts/4x6-font.ttf")
    assert Path(found).is_absolute() and Path(found).is_file()


def test_nowhere_comes_back_unchanged(elsewhere):
    assert resolve_font_path("assets/fonts/no-such-font.ttf") == "assets/fonts/no-such-font.ttf"
