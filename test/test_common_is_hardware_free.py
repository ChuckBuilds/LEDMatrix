"""src/common must stay importable without display hardware.

Plugins import src.common.sports_* in place of their bundled copies. If any of
those modules reaches src.display_manager (and through it rgbmatrix) or the
src.base_classes package (whose core.py imports DisplayManager), a scoreboard
adopting it acquires a hardware dependency it never had, and the headless
tooling -- the web preview, check_plugin.py, these tests on a laptop -- stops
being able to load it.

Two checks, because each misses what the other catches:

1. A real import in a fresh interpreter with rgbmatrix made unimportable.
   Catches indirect imports (common -> X -> display_manager) that no text scan
   of src/common would see.
2. An AST scan of src/common/*.py for module-level imports of the hardware and
   plugin-system packages. Catches a direct import even where the target
   happens to import cleanly on this machine.
"""

import ast
import subprocess
import sys
import textwrap
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
COMMON = REPO_ROOT / "src" / "common"

FORBIDDEN = ("src.base_classes", "src.display_manager", "src.plugin_system")

#: Existing module-level violations, by file name, each with the reason it is
#: tolerated. Empty when this test was added (core 3.4.0 + sports_helpers):
#: no src/common module imported any of FORBIDDEN at module level. Add to this
#: only with a comment saying why, never to get a new import past the test.
ALLOWLIST = {}


def test_sports_modules_import_without_hardware():
    modules = sorted(p.stem for p in COMMON.glob("sports_*.py"))
    assert "sports_helpers" in modules
    script = textwrap.dedent(f"""
        import importlib, json, sys
        sys.modules["rgbmatrix"] = None   # any import of it now raises
        importlib.import_module("src.common")
        for name in {modules!r}:
            importlib.import_module("src.common." + name)
        loaded = sorted(m for m in sys.modules
                        if m.startswith({FORBIDDEN!r}))
        print(json.dumps(loaded))
    """)
    result = subprocess.run(
        [sys.executable, "-c", script], cwd=str(REPO_ROOT),
        capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, (
        f"importing src.common without rgbmatrix failed:\n{result.stderr}")
    loaded = result.stdout.strip().splitlines()[-1]
    assert loaded == "[]", f"src.common pulled in hardware modules: {loaded}"


def _module_level_imports(tree):
    """Imports executed at import time: top level, including inside if/try
    blocks, but not inside functions or classes (those are deferred)."""
    stack = list(tree.body)
    while stack:
        node = stack.pop()
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            yield node
        elif isinstance(node, (ast.If, ast.Try, ast.With)):
            stack.extend(node.body)
            stack.extend(getattr(node, "orelse", []))
            stack.extend(getattr(node, "finalbody", []))
            for handler in getattr(node, "handlers", []):
                stack.extend(handler.body)


def _targets(node, package="src.common"):
    """Absolute dotted names an import can load. Relative imports resolve
    against ``package``, the scanned module's package."""
    if isinstance(node, ast.Import):
        return [alias.name for alias in node.names]
    base = node.module
    if node.level:
        parts = package.split(".")
        if node.level > len(parts):
            return []   # beyond the top-level package; Python rejects it too
        anchor = ".".join(parts[:len(parts) - node.level + 1])
        base = f"{anchor}.{node.module}" if node.module else anchor
    if base is None:
        return []
    # `from pkg import name` may load the submodule pkg.name.
    return [base] + [f"{base}.{alias.name}" for alias in node.names
                     if alias.name != "*"]


def _violations():
    found = {}
    for path in sorted(COMMON.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in _module_level_imports(tree):
            for target in _targets(node, "src.common"):
                if target == FORBIDDEN or any(
                        target == f or target.startswith(f + ".") for f in FORBIDDEN):
                    found.setdefault(path.name, []).append(f"line {node.lineno}: {target}")
    return found


def test_no_module_level_hardware_or_plugin_system_imports():
    found = {name: hits for name, hits in _violations().items() if name not in ALLOWLIST}
    assert found == {}, (
        "src/common must not import these at module level (defer the import "
        f"into the function that needs it): {found}")


def test_allowlist_has_no_stale_entries():
    stale = sorted(set(ALLOWLIST) - set(_violations()))
    assert stale == [], f"allowlisted but no longer violating, remove: {stale}"


def test_the_scan_sees_a_direct_import(tmp_path):
    # Guard the guard: the walker must find imports nested in try/if.
    tree = ast.parse(textwrap.dedent("""
        try:
            from src.display_manager import DisplayManager
        except ImportError:
            pass
        if True:
            import src.base_classes.sports
        from src import plugin_system
        def later():
            from src.display_manager import DisplayManager
    """))
    targets = {t for n in _module_level_imports(tree) for t in _targets(n)}
    assert {"src.base_classes.sports", "src.display_manager",
            "src.plugin_system"} <= targets


def test_the_scan_resolves_relative_imports():
    # A module in src/common reaches src.plugin_system through `..`.
    tree = ast.parse(textwrap.dedent("""
        from .. import plugin_system
        from ..plugin_system import plugin_manager
        from . import sports_shared
    """))
    targets = {t for n in _module_level_imports(tree)
               for t in _targets(n, "src.common")}
    assert {"src.plugin_system", "src.plugin_system.plugin_manager",
            "src.common.sports_shared"} <= targets
    # The package decides where `..` points.
    node = ast.parse("from .. import plugin_system").body[0]
    assert _targets(node, "src.common") == ["src", "src.plugin_system"]
    assert _targets(node, "a.b") == ["a", "a.plugin_system"]


def test_the_scan_flags_relative_imports_in_src_common(monkeypatch, tmp_path):
    # End to end through _violations(): both relative forms are reported.
    (tmp_path / "bad.py").write_text(
        "from .. import plugin_system\nfrom ..plugin_system import x\n",
        encoding="utf-8")
    monkeypatch.setitem(globals(), "COMMON", tmp_path)
    found = {name: sorted(hits) for name, hits in _violations().items()}
    assert found == {"bad.py": ["line 1: src.plugin_system",
                                "line 2: src.plugin_system",
                                "line 2: src.plugin_system.x"]}
