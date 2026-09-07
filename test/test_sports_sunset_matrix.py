"""What happens to an adopted sports plugin on each core it can meet.

B5 moved the eight scoreboards onto `src.common.sports_scroll` behind a guarded
import, keeping a bundled copy as the fallback. B6 deletes those copies. The
two phases make *different* promises, and only the second one is obvious:

|                    | bundled copy present            | bundled copy removed        |
|--------------------|---------------------------------|-----------------------------|
| **pinned old core**| loads -- B5's whole guarantee   | ERROR, naming the module    |
| **current core**   | loads, using core code          | loads, using core code      |

The top-left cell is the one worth having: nothing else in this suite proves an
adopted plugin still runs on a core that predates the module, and that claim is
the entire basis for having shipped B5 ahead of B6's gate.

The bottom-left cell is the B6 failure mode, and it is asserted through
`PluginManager.load_plugin` rather than a bare import on purpose. The manager
catches the `ModuleNotFoundError`, so nothing propagates to a caller: a test
that expected `pytest.raises` would pass against a core where the module is
merely *broken* rather than absent, and would say nothing about what the user
actually experiences. What they get is a plugin parked in ERROR and one log
line -- which is precisely why B6 needs the install gate rather than trusting
the failure to be noticed.

This covers the load path. The install/update gate -- which is what should stop
a sunset plugin reaching an old core in the first place -- is the other half of
the guarantee and is tested in test_plugin_compatibility_gate.py.

See docs/SPORTS_UNIFICATION.md, "B6 -- why the sunset needs more than a version
floor".
"""

import itertools
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

project_root = Path(__file__).parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from src.plugin_system.plugin_manager import PluginManager
from src.plugin_system.plugin_state import PluginState


_PLUGIN_IDS = itertools.count()

CORE_MODULE = "src.common.sports_scroll"

# Only the leaf. A pre-3.2.0 core still ships `src/common/` -- scroll_helper
# and friends live there -- and it is `sports_scroll.py` alone that is absent.
# Hiding the whole package would be a different, harsher core than any that
# shipped, and it would make the load failure name the package rather than the
# module, which is the thing a reader needs to see.
HIDDEN = (CORE_MODULE,)


class _PinnedOldCore:
    """Make `src.common.sports_scroll` un-importable for the duration.

    A meta_path finder rather than a monkeypatched `__import__`: the plugin is
    executed by the real loader through `exec_module`, so the block has to live
    in the import system itself to be reached.
    """

    def __init__(self, *names):
        self.names = set(names)
        self._saved = {}

    def find_spec(self, fullname, path=None, target=None):
        if fullname in self.names:
            raise ModuleNotFoundError(f"No module named {fullname!r}", name=fullname)
        return None

    def __enter__(self):
        for name in list(sys.modules):
            if name in self.names:
                self._saved[name] = sys.modules.pop(name)
        sys.meta_path.insert(0, self)
        return self

    def __exit__(self, *exc):
        sys.meta_path.remove(self)
        sys.modules.update(self._saved)
        return False


# The two source shapes, kept as literals rather than copied from a plugin at
# runtime: this file lives in the core repo and must not depend on a plugin
# checkout being present, and pinning the shapes here means a plugin that
# drifts away from one is a visible edit, not a silently weakened test.

# B5, as the eight scoreboards ship today: prefer the core module, fall back to
# the bundled copy. The except clause is narrow on purpose -- a bare
# `except ImportError` would also swallow a failure raised *inside* a core
# module that is present, quietly loading the legacy copy and hiding a broken
# core install.
ADOPTED_WITH_FALLBACK = '''
_USING_CORE_SCROLL = False
try:
    from src.common.sports_scroll import SportsScrollDisplay as _Base
    _USING_CORE_SCROLL = True
except ModuleNotFoundError as exc:
    if exc.name not in {"src", "src.common", "src.common.sports_scroll"}:
        raise
    _Base = None

if not _USING_CORE_SCROLL:
    from scroll_display_legacy import LegacyScrollDisplay as _Base
'''

# B6, once the copies are deleted: there is nothing to fall back to, so the
# guard goes with them. Keeping the try/except while removing the file it
# falls back to would only mislabel the failure -- the plugin would report a
# missing `scroll_display_legacy` and say nothing about the core module that
# is actually absent.
SUNSET_NO_FALLBACK = '''
from src.common.sports_scroll import SportsScrollDisplay as _Base

_USING_CORE_SCROLL = True
'''

PLUGIN_BODY = '''
from src.plugin_system.base_plugin import BasePlugin


class SunsetProbe(BasePlugin):
    """Records which scroll implementation the guarded import selected."""

    using_core_scroll = _USING_CORE_SCROLL
    scroll_base = _Base

    def update(self):
        pass

    def display(self, force_clear=False):
        pass
'''

LEGACY_COPY = '''
class LegacyScrollDisplay:
    """Stands in for the bundled pre-3.2.0 implementation."""
'''


def _write_plugin(plugins_dir: Path, plugin_id: str, *, bundled_copy: bool) -> Path:
    path = plugins_dir / plugin_id
    path.mkdir(parents=True)
    (path / "manifest.json").write_text(
        json.dumps({
            "id": plugin_id,
            "name": "Sunset Probe",
            "version": "1.0.0",
            "entry_point": "manager.py",
            "class_name": "SunsetProbe",
            "display_modes": ["sunset_probe"],
        }),
        encoding="utf-8",
    )
    shape = ADOPTED_WITH_FALLBACK if bundled_copy else SUNSET_NO_FALLBACK
    (path / "manager.py").write_text(shape + PLUGIN_BODY, encoding="utf-8")
    if bundled_copy:
        (path / "scroll_display_legacy.py").write_text(LEGACY_COPY, encoding="utf-8")
    return path


@pytest.fixture
def load(tmp_path):
    """Load a synthetic adopted plugin through the real PluginManager.

    Returns a callable taking the two axes of the matrix and handing back the
    manager, so the caller can ask it for state and recorded error.
    """
    plugins_dir = tmp_path / "plugin-repos"
    plugins_dir.mkdir()

    def _load(*, core_has_module: bool, bundled_copy: bool, hide=HIDDEN):
        # A distinct id per cell, from a counter that spans the whole session.
        # The loader names plugin modules after the plugin id and sys.modules
        # is process-global, so a per-test counter would hand the second test
        # the first test's already-imported module -- which passes or fails on
        # the wrong plugin's import.
        plugin_id = f"sunset-probe-{next(_PLUGIN_IDS)}"
        _write_plugin(plugins_dir, plugin_id, bundled_copy=bundled_copy)

        with patch('src.common.permission_utils.ensure_directory_permissions'):
            manager = PluginManager(
                plugins_dir=str(plugins_dir),
                config_manager=MagicMock(),
                display_manager=MagicMock(),
                cache_manager=MagicMock(),
                font_manager=MagicMock(),
            )
            manager.discover_plugins()
            if core_has_module:
                ok = manager.load_plugin(plugin_id)
            else:
                with _PinnedOldCore(*hide):
                    ok = manager.load_plugin(plugin_id)
        return manager, plugin_id, ok

    return _load


def _assert_loaded(manager, plugin_id, ok):
    assert ok is True, (
        f"load_plugin returned False; state is "
        f"{manager.state_manager.get_state(plugin_id)}, error "
        f"{manager.state_manager.get_error_info(plugin_id)}"
    )
    assert manager.state_manager.get_state(plugin_id) is not PluginState.ERROR


class TestBundledCopyPresent:
    """B5's shape: the guarded import with the fallback still shipped."""

    def test_old_core_falls_back_and_still_loads(self, load):
        """The claim that made it safe to ship B5 before B6's gate."""
        manager, plugin_id, ok = load(core_has_module=False, bundled_copy=True)

        _assert_loaded(manager, plugin_id, ok)
        plugin = manager.plugins[plugin_id]
        assert plugin.using_core_scroll is False, (
            "the core module was hidden, so the plugin must be on its bundled copy"
        )
        assert plugin.scroll_base.__name__ == "LegacyScrollDisplay"

    def test_current_core_prefers_the_core_module(self, load):
        manager, plugin_id, ok = load(core_has_module=True, bundled_copy=True)

        _assert_loaded(manager, plugin_id, ok)
        plugin = manager.plugins[plugin_id]
        assert plugin.using_core_scroll is True, (
            "the bundled copy must not win while the core module is importable"
        )
        assert plugin.scroll_base.__name__ == "SportsScrollDisplay"


    def test_a_core_without_the_package_at_all_still_falls_back(self, load):
        """The guard's other accepted names.

        Its except clause accepts `src` and `src.common` as well as the module
        itself, so those branches exist in all eight shipped plugins. No core
        that old is likely still running, but the code claiming to handle it is
        real and nothing else exercises it -- an untested branch in a fallback
        is exactly the kind that rots unnoticed until the fallback is needed.
        """
        manager, plugin_id, ok = load(
            core_has_module=False, bundled_copy=True, hide=(CORE_MODULE, "src.common")
        )

        _assert_loaded(manager, plugin_id, ok)
        assert manager.plugins[plugin_id].using_core_scroll is False


class TestBundledCopyRemoved:
    """B6's shape: the copies are gone and only the core module remains."""

    def test_current_core_still_loads(self, load):
        manager, plugin_id, ok = load(core_has_module=True, bundled_copy=False)

        _assert_loaded(manager, plugin_id, ok)
        assert manager.plugins[plugin_id].using_core_scroll is True

    def test_old_core_errors_and_records_the_missing_module(self, load):
        """The B6 failure mode, as the user meets it.

        Not `pytest.raises`: load_plugin catches it, so nothing reaches a
        caller. The observable consequences are the ERROR state and the
        recorded error -- and the error has to name the module, or whoever
        reads the log cannot tell a missing core module from any other
        import failure.
        """
        manager, plugin_id, ok = load(core_has_module=False, bundled_copy=False)

        assert ok is False, "a plugin with no scroll implementation must not load"
        assert manager.state_manager.get_state(plugin_id) is PluginState.ERROR
        assert plugin_id not in manager.plugins, (
            "a plugin that failed to load must not be left registered"
        )

        info = manager.state_manager.get_error_info(plugin_id)
        assert info is not None, "ERROR state recorded no error to explain it"
        assert info['error_type'] == 'ModuleNotFoundError', info
        assert CORE_MODULE in info['error'], (
            f"the recorded error must name the module that was missing, got {info['error']!r}"
        )


def test_the_matrix_has_one_failing_cell(load):
    """Guards the shape of the table itself.

    Each cell above is asserted on its own, so a change that broke two of them
    in compensating ways could leave every individual test passing. This says
    the outcome depends on both axes and fails in exactly one combination.
    """
    outcomes = {
        (core, bundled): load(core_has_module=core, bundled_copy=bundled)[2]
        for core in (True, False)
        for bundled in (True, False)
    }
    assert outcomes == {
        (True, True): True,
        (True, False): True,
        (False, True): True,
        (False, False): False,
    }, outcomes
