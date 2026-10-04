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


# -- sub-packages ------------------------------------------------------------
#
# A plugin that keeps helpers in a package (``providers/feed.py``, imported as
# ``from providers.feed import ...``) leaves dotted entries in sys.modules.
# Only the bare ``providers`` used to be tracked, so ``providers.feed`` outlived
# the plugin: a reload after a store update re-ran the new manager.py against
# the old feed.py, until the display restarted. Elections (providers/),
# flights (enrichment/) and olympics (data/, renderers/) ship packages.


@pytest.fixture
def package_plugin(tmp_path):
    before_path = list(sys.path)
    before_modules = set(sys.modules)
    plugin_dir = tmp_path / "pkgdemo"
    (plugin_dir / "providers").mkdir(parents=True)
    (plugin_dir / "providers" / "__init__.py").write_text("", encoding="utf-8")
    (plugin_dir / "providers" / "feed.py").write_text("VERSION = 'v1'\n", encoding="utf-8")
    (plugin_dir / "manager.py").write_text(
        "from providers.feed import VERSION\n", encoding="utf-8")
    yield plugin_dir
    sys.path[:] = before_path
    for key in set(sys.modules) - before_modules:
        sys.modules.pop(key, None)


def test_a_reloaded_plugin_runs_its_updated_subpackage_module(package_plugin):
    loader = PluginLoader()
    assert loader.load_module("pkgdemo", package_plugin, "manager.py").VERSION == "v1"

    _unload(loader, "pkgdemo")
    # The store update: a different size, so no cached bytecode can match.
    (package_plugin / "providers" / "feed.py").write_text(
        "VERSION = 'v2 from the update'\n", encoding="utf-8")
    reloaded = loader.load_module("pkgdemo", package_plugin, "manager.py")

    assert reloaded.VERSION == "v2 from the update"


def test_unload_drops_the_plugins_subpackage_modules(package_plugin):
    loader = PluginLoader()
    loader.load_module("pkgdemo", package_plugin, "manager.py")
    # Still importable while the plugin runs, as before.
    assert "providers.feed" in sys.modules

    _unload(loader, "pkgdemo")

    assert not [k for k in sys.modules if k.startswith("providers")]


def test_a_failed_load_leaves_no_subpackage_module_behind(package_plugin):
    (package_plugin / "manager.py").write_text(
        "from providers.feed import VERSION\nraise RuntimeError('broken')\n",
        encoding="utf-8")
    loader = PluginLoader()

    with pytest.raises(RuntimeError):
        loader.load_module("pkgdemo", package_plugin, "manager.py")

    assert not [k for k in sys.modules if k.startswith("providers")]


def test_unload_leaves_packages_from_outside_the_plugin_alone(package_plugin, tmp_path):
    # A library the plugin imports is not the plugin's to drop.
    lib_root = tmp_path / "site"
    (lib_root / "extlib").mkdir(parents=True)
    (lib_root / "extlib" / "__init__.py").write_text("", encoding="utf-8")
    (lib_root / "extlib" / "sub.py").write_text("X = 1\n", encoding="utf-8")
    sys.path.append(str(lib_root))
    (package_plugin / "manager.py").write_text(
        "import extlib.sub\nfrom providers.feed import VERSION\n", encoding="utf-8")
    loader = PluginLoader()
    loader.load_module("pkgdemo", package_plugin, "manager.py")

    _unload(loader, "pkgdemo")

    assert "extlib.sub" in sys.modules
    assert "extlib" in sys.modules
