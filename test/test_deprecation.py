"""@deprecated: plugin-facing APIs nothing in core, the monorepo or the
registry's third-party plugins calls, kept for one release with a warning."""

import ast
import importlib.util
import logging
import os
import sys
import textwrap
import warnings
from pathlib import Path

import pytest
from packaging.version import Version

os.environ.setdefault("EMULATOR", "true")

from src import __version__, deprecation
from src.deprecation import deprecated

REPO = Path(__file__).resolve().parents[1]

#: Everything still deprecated. (The 35 methods deprecated for 3.8.0 were
#: removed in it: docs/DEPRECATIONS_3.8.md found them unused.) Removing one
#: of these, or deprecating another, should be a deliberate edit here too.
#:
#: Deprecated with Vegas participation, for removal in 3.9.0: core never read
#: them (src.plugin_system.base_plugin.VEGAS_LEGACY_REMOVAL).
DEPRECATED_3_9 = {
    "src.plugin_system.base_plugin.BasePlugin": [
        "get_supported_vegas_modes", "get_vegas_segment_width",
    ],
}

#: Every pinned marker: (class path, method) -> the release that removes it.
PINNED = {(path, name): removal
          for removal, table in (("3.9.0", DEPRECATED_3_9),)
          for path, names in table.items() for name in names}


def _cls(path):
    import importlib
    module, name = path.rsplit(".", 1)
    return getattr(importlib.import_module(module), name)


@pytest.mark.parametrize("path", sorted({path for path, _ in PINNED}))
def test_exactly_these_methods_are_deprecated(path):
    cls = _cls(path)
    marked = sorted(name for name, value in vars(cls).items()
                    if hasattr(value, "__deprecated__"))
    assert marked == sorted(name for owner, name in PINNED if owner == path)
    for name in marked:
        assert f"LEDMatrix {PINNED[(path, name)]}" in getattr(cls, name).__deprecated__


def _markers():
    """(file:line, removal) for every ``@deprecated(...)`` under src/."""
    found = []
    for path in sorted((REPO / "src").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), str(path))
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for dec in fn.decorator_list:
                target = dec.func if isinstance(dec, ast.Call) else dec
                name = getattr(target, "id", None) or getattr(target, "attr", None)
                if name != "deprecated":
                    continue
                where = f"{path.relative_to(REPO).as_posix()}:{fn.lineno} {fn.name}"
                arg = dec.args[0] if isinstance(dec, ast.Call) and dec.args else None
                found.append((where, arg.value if isinstance(arg, ast.Constant) else None))
    return found


def test_markers_are_found():
    assert len(_markers()) == len(PINNED)


def test_no_marker_names_a_release_already_shipped():
    """3.7.0 shipped still warning that 35 methods are "removed in 3.7.0".

    Once ``src.__version__`` reaches a marker's release, that release is here:
    remove the method (if scripts/plugin_api_usage.py reports it unused) or
    move the marker to a later release. Either way, never ship a warning that
    names a version the user is already running.
    """
    current = Version(__version__)
    stale = [f"{where} -> {removal!r}" for where, removal in _markers()
             if not isinstance(removal, str) or Version(removal) <= current]
    assert not stale, (f"@deprecated markers at or below src.__version__ {__version__} "
                       f"(or not a literal version): {stale}")


@pytest.fixture(scope="module")
def usage_script():
    path = REPO / "scripts" / "plugin_api_usage.py"
    spec = importlib.util.spec_from_file_location("plugin_api_usage_script", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module          # dataclasses resolve annotations through it
    try:
        spec.loader.exec_module(module)
        yield module
    finally:
        sys.modules.pop(spec.name, None)


def test_usage_script_lists_exactly_the_pinned_markers(usage_script):
    found = {(f"{m.module}.{m.owner}", m.method, m.removal)
             for m in usage_script.find_markers(REPO)}
    assert found == {(path, name, removal) for (path, name), removal in PINNED.items()}


#: A stand-in core for the scanner tests below, so they keep working whichever
#: real markers exist (the 3.8.0 ones they were written against are gone).
FAKE_CORE = {
    "src/cache_manager.py": """\
        class CacheManager:
            @deprecated("9.9.0", "use set()")
            def update_cache(self, key, data):
                pass
        """,
    "src/display_manager.py": """\
        class DisplayManager:
            @deprecated("9.9.0")
            def draw_sun(self, x, y):
                pass

            @deprecated("9.9.0")
            def draw_cloud(self, x, y):
                pass

            @deprecated("9.9.0")
            def draw_rain(self, x, y):
                self.draw_cloud(x, y)

            @deprecated("9.9.0")
            def draw_snow(self, x, y):
                pass

            @deprecated("9.9.0")
            def get_scrolling_stats(self):
                return {}
        """,
    "src/font_manager.py": """\
        class FontManager:
            @deprecated("9.9.0")
            def add_font(self, path, name):
                return True
        """,
}


@pytest.fixture
def fake_core(tmp_path):
    root = tmp_path / "core"
    for rel, source in FAKE_CORE.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(source), encoding="utf-8")
    return root


def test_usage_script_tells_uses_from_name_collisions(usage_script, fake_core, tmp_path):
    """Calls on the owning object and overrides count; same-named methods of
    unrelated classes and hits in test files do not."""
    plugin = tmp_path / "demo"
    (plugin / "test").mkdir(parents=True)
    (plugin / "manager.py").write_text(textwrap.dedent("""\
        class Icons:
            @staticmethod
            def draw_sun(img):
                pass

        class Plugin:
            def draw_cloud(self):
                return self.draw_cloud()

            def display(self):
                Icons.draw_sun(None)
                self.cache_manager.update_cache('k', {})
                dm = self.display_manager
                dm.draw_rain(0, 0)
                thing.get_scrolling_stats()

        class MyFonts(FontManager):
            def add_font(self, path, name):
                return super().add_font(path, name)
        """), encoding="utf-8")
    (plugin / "test" / "test_manager.py").write_text(textwrap.dedent("""\
        def test_x(display_manager):
            display_manager.draw_snow.assert_not_called()
        """), encoding="utf-8")

    markers = usage_script.find_markers(fake_core)
    source = usage_script.Source("demo", "monorepo", plugin)
    usage_script.scan_tree(source, [plugin], plugin, markers, core=False)
    kinds = {key: sorted(("test " if h.test else "") + h.kind for h in hits)
             for key, hits in source.hits.items()}

    assert kinds == {
        "DisplayManager.draw_sun": ["unrelated", "unrelated"],
        "DisplayManager.draw_cloud": ["unrelated", "unrelated"],
        "CacheManager.update_cache": ["call"],
        "DisplayManager.draw_rain": ["call"],
        "DisplayManager.get_scrolling_stats": ["review"],
        "FontManager.add_font": ["call", "override"],
        "DisplayManager.draw_snow": ["test call"],
    }

    status = usage_script.verdicts(markers, [source])
    assert status["CacheManager.update_cache"][0] == "used"
    assert status["DisplayManager.get_scrolling_stats"][0] == "review"
    assert status["DisplayManager.draw_sun"][0] == "unused"      # a collision only
    assert status["DisplayManager.draw_snow"][0] == "unused"     # a test mock only


def test_usage_script_follows_calls_between_deprecated_core_methods(usage_script, fake_core):
    """draw_rain calls draw_cloud; with no outside callers both are unused."""
    markers = usage_script.find_markers(fake_core)
    core = usage_script.Source("core", "core", fake_core)
    usage_script.scan_tree(core, [fake_core / "src" / "display_manager.py"], fake_core,
                           markers, core=True)
    kinds = {h.kind for h in core.hits["DisplayManager.draw_cloud"]}
    assert kinds == {"internal"}
    assert usage_script.verdicts(markers, [core])["DisplayManager.draw_cloud"][0] == "unused"


@pytest.fixture
def fresh(monkeypatch):
    monkeypatch.setattr(deprecation, "_warned", set())


def test_first_call_warns_and_logs_then_stays_quiet(fresh, caplog):
    @deprecated("9.9.9", "use other()")
    def old(x):
        """Doc."""
        return x * 2

    with warnings.catch_warnings(record=True) as caught, caplog.at_level(logging.WARNING):
        warnings.simplefilter("always")
        assert old(2) == 4
        assert old(3) == 6

    assert [str(w.message) for w in caught] == [
        "test_first_call_warns_and_logs_then_stays_quiet.<locals>.old() is deprecated "
        "and will be removed in LEDMatrix 9.9.9; use other()"]
    assert caught[0].category is DeprecationWarning
    assert caught[0].filename == __file__  # points at the caller
    assert sum("will be removed in LEDMatrix 9.9.9" in r.message for r in caplog.records) == 1
    assert old.__name__ == "old" and old.__doc__ == "Doc."
