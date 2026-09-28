"""A reloaded plugin imports its own bare-name modules, not another plugin's.

Scoreboard plugins each ship a ``sports.py`` and import it by bare name. Each
plugin's directory goes on sys.path when it loads, and a bare import resolves
to the first directory that has the file. The loader used to add a directory
only if it was missing, so after alpha, then beta, loaded, re-enabling alpha
from the web UI left beta's directory in front: alpha's ``from sports import
...`` got beta's copy. On a Pi, re-enabling UFC with hockey running failed
with "cannot import name '_status_is_final' from 'sports'".
"""

import sys

import pytest

from src.plugin_system.plugin_loader import PluginLoader


def _write_plugin(root, name):
    d = root / name
    d.mkdir(parents=True)
    (d / "sports.py").write_text(f"WHO = {name!r}\n", encoding="utf-8")
    (d / "manager.py").write_text("from sports import WHO\n", encoding="utf-8")
    return d


@pytest.fixture
def plugins(tmp_path):
    before_path = list(sys.path)
    before_modules = set(sys.modules)
    dirs = {name: _write_plugin(tmp_path, name) for name in ("alpha", "beta")}
    yield dirs
    sys.path[:] = before_path
    for key in set(sys.modules) - before_modules:
        sys.modules.pop(key, None)


def _unload(loader, plugin_id):
    # What PluginManager.unload_plugin does to the module entries.
    sys.modules.pop(f"plugin_{plugin_id}", None)
    loader.unregister_plugin_modules(plugin_id)


def test_each_plugin_gets_its_own_bare_module(plugins):
    loader = PluginLoader()
    assert loader.load_module("alpha", plugins["alpha"], "manager.py").WHO == "alpha"
    assert loader.load_module("beta", plugins["beta"], "manager.py").WHO == "beta"


def test_a_reloaded_plugin_still_gets_its_own_bare_module(plugins):
    loader = PluginLoader()
    loader.load_module("alpha", plugins["alpha"], "manager.py")
    loader.load_module("beta", plugins["beta"], "manager.py")

    _unload(loader, "alpha")
    reloaded = loader.load_module("alpha", plugins["alpha"], "manager.py")

    assert reloaded.WHO == "alpha"
    assert sys.path.index(str(plugins["alpha"])) < sys.path.index(str(plugins["beta"]))
    assert sys.path.count(str(plugins["alpha"])) == 1
