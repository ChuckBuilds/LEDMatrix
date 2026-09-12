"""web_interface/app.py resolves plugins_dir from config at import time.

`project_root` used to be assigned only inside the `else` branch (the
relative-path case). SchemaManager and composer_bp are wired up with
`project_root` further down the same module, so a device configured with an
*absolute* `plugin_system.plugins_directory` (a supported value -- see
CLAUDE.md) hit `UnboundLocalError` importing web_interface.app at all, taking
the whole web UI down.

app.py isn't imported directly by the test suite -- it has real side effects
(managers, background state) at import time -- so this extracts the exact
resolution block by its stable comment markers and executes it in isolation,
the same way the rest of the suite validates generated/templated code rather
than re-implementing it.
"""
from pathlib import Path

APP_PY = Path(__file__).resolve().parent.parent / "web_interface/app.py"

START_MARKER = "# Resolve plugin directory - handle both absolute and relative paths\n"
END_MARKER = "\nplugin_manager = PluginManager("


def _resolution_block() -> str:
    src = APP_PY.read_text()
    start = src.index(START_MARKER)
    end = src.index(END_MARKER, start)
    return src[start:end]


def test_resolution_block_still_matches_expected_markers():
    """If app.py is restructured enough that these markers move, the exec
    below would silently test nothing -- fail loudly instead."""
    block = _resolution_block()
    assert "plugins_dir" in block and "os.path.isabs" in block


def test_absolute_plugins_directory_does_not_raise_unboundlocalerror():
    import os
    ns = {"os": os, "Path": Path, "__file__": str(APP_PY),
          "plugins_dir_name": "/opt/ledmatrix-plugins"}
    exec(compile(_resolution_block(), str(APP_PY), "exec"), ns)  # noqa: S102
    assert ns["project_root"] == APP_PY.parent.parent
    assert ns["plugins_dir"] == Path("/opt/ledmatrix-plugins")


def test_relative_plugins_directory_still_resolves_under_project_root():
    import os
    ns = {"os": os, "Path": Path, "__file__": str(APP_PY),
          "plugins_dir_name": "plugin-repos"}
    exec(compile(_resolution_block(), str(APP_PY), "exec"), ns)  # noqa: S102
    assert ns["plugins_dir"] == ns["project_root"] / "plugin-repos"
