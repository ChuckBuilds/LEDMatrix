"""src.common and src.plugin_system import their exports lazily (PEP 562).

The web interface imports both packages only for small submodules
(path_safety, snapshot_policy, store_manager, ...). When their __init__
imported every export eagerly, that dragged numpy, freetype and the plugin
manager into a process that never uses them -- about 13 MB of RSS on a Pi.

These tests pin both halves of the change: the package import stays light,
and every exported name still resolves to the very object its home module
defines, so ``isinstance`` and ``is`` checks behave as before.
"""

import importlib
import json
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

#: Modules the bare package import must not load. numpy is the one that
#: matters for memory; the rest are what the eager __init__ used to import.
HEAVY = (
    "numpy",
    "freetype",
    "src.adaptive_layout",
    "src.common.api_helper",
    "src.common.logo_helper",
    "src.common.scroll_helper",
    "src.plugin_system.base_plugin",
    "src.plugin_system.plugin_manager",
)


def _run(script):
    result = subprocess.run(
        [sys.executable, "-c", textwrap.dedent(script)], cwd=str(REPO_ROOT),
        capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout.strip().splitlines()[-1])


@pytest.mark.parametrize("package", ["src.common", "src.plugin_system"])
def test_package_import_loads_nothing_heavy(package):
    # A fresh interpreter: this test process has long since imported them.
    loaded = _run(f"""
        import json, sys
        import {package}
        print(json.dumps(sorted(m for m in {HEAVY!r} if m in sys.modules)))
    """)
    assert loaded == [], f"importing {package} loaded {loaded}"


def test_web_interface_submodules_do_not_load_numpy():
    # The imports web_interface/app.py and its API blueprint make from these
    # packages. numpy here costs the web process ~13 MB for nothing.
    loaded = _run("""
        import json, sys
        from src.common import path_safety, snapshot_policy, sync_manager
        from src.plugin_system import store_manager, schema_manager
        print(json.dumps("numpy" in sys.modules))
    """)
    assert loaded is False


@pytest.mark.parametrize("package", ["src.common", "src.plugin_system"])
def test_every_exported_name_resolves_to_its_home_object(package):
    pkg = importlib.import_module(package)
    assert sorted(pkg.__all__) == sorted(pkg._LAZY), "__all__ and _LAZY differ"
    for name in pkg.__all__:
        module_name, attr = pkg._LAZY[name]
        home = importlib.import_module(module_name)
        expected = home if attr is None else getattr(home, attr)
        assert getattr(pkg, name) is expected, name
        assert name in dir(pkg)


def test_from_import_forms_plugins_use():
    # Every form found in ledmatrix-plugins and core: names, aliases,
    # submodules through the package, dotted submodule imports.
    from src.common import ScrollHelper, LogoHelper
    from src.common import scroll_config as _scroll_config
    from src.common import sports_card as _card
    from src.common import draw_fitted_text
    from src.plugin_system import BasePlugin, PluginManager
    from src.plugin_system import compatibility
    import src.common.scroll_helper
    import src.plugin_system.base_plugin
    import src.common
    import src.plugin_system

    assert ScrollHelper is src.common.scroll_helper.ScrollHelper
    assert LogoHelper is src.common.LogoHelper
    assert _scroll_config is src.common.scroll_config
    assert _card is importlib.import_module("src.common.sports_card")
    assert draw_fitted_text is importlib.import_module("src.adaptive_layout").draw_fitted_text
    assert BasePlugin is src.plugin_system.base_plugin.BasePlugin
    assert PluginManager is importlib.import_module(
        "src.plugin_system.plugin_manager").PluginManager
    assert compatibility is importlib.import_module("src.plugin_system.compatibility")
    assert src.plugin_system.__version__ == "1.0.0"


def test_star_import_still_binds_everything():
    namespace = {}
    exec("from src.common import *", namespace)
    import src.common
    assert set(src.common.__all__) <= set(namespace)


@pytest.mark.parametrize("package", ["src.common", "src.plugin_system"])
def test_unknown_name_raises_attribute_error(package):
    pkg = importlib.import_module(package)
    with pytest.raises(AttributeError, match="no_such_name"):
        pkg.no_such_name  # noqa: B018
    with pytest.raises(ImportError):
        exec(f"from {package} import no_such_name")
