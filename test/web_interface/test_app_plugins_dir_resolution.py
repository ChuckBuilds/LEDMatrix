"""
web_interface/app.py must start with an absolute ``plugins_directory``.

``project_root`` used to be assigned only in the relative-path branch, so an
absolute path in config.json raised NameError at module import (the
SchemaManager construction was the first use). Each case imports the real
module in a fresh interpreter, since app.py does its setup at import time and
the test process may already hold a copy built from the real config.
"""

import json
import subprocess
import sys
import textwrap
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

_IMPORT_APP = textwrap.dedent(
    """
    import json, sys
    from pathlib import Path
    sys.path.insert(0, {root!r})
    from src.config_manager import ConfigManager
    ConfigManager.load_config = lambda self: {config!r}
    import web_interface.app as web_app
    print(json.dumps({{
        "plugins_dir": str(web_app.plugins_dir),
        "project_root": str(web_app.project_root),
        "schema_project_root": str(web_app.schema_manager.project_root),
    }}))
    """
)


def _import_app_with(plugins_directory):
    config = {"plugin_system": {"plugins_directory": plugins_directory}}
    script = _IMPORT_APP.format(root=str(PROJECT_ROOT), config=config)
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(PROJECT_ROOT),
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout.strip().splitlines()[-1])


def test_absolute_plugins_directory_imports(tmp_path):
    plugins = tmp_path / "my-plugins"
    plugins.mkdir()

    info = _import_app_with(str(plugins))

    assert Path(info["plugins_dir"]) == plugins
    assert Path(info["project_root"]) == PROJECT_ROOT
    assert Path(info["schema_project_root"]) == PROJECT_ROOT


def test_relative_plugins_directory_resolves_under_project_root():
    info = _import_app_with("plugin-repos")

    assert Path(info["plugins_dir"]) == PROJECT_ROOT / "plugin-repos"
    assert Path(info["project_root"]) == PROJECT_ROOT
