"""
Cross-size / cross-screen plugin safety test.

For every discovered plugin, render every declared screen at every supported
matrix size and assert it: loads, renders without crashing, stays within the
panel bounds, and — for plugins that ship golden images — matches them.

Plugin discovery (first match wins):
  - $LEDMATRIX_PLUGINS_DIR  (os.pathsep-separated list of dirs), else
  - <project_root>/plugin-repos and <project_root>/plugins

A plugin opts into golden-image checks by adding test/golden/<WxH>/<mode>.png
(and usually test/harness.json for deterministic config / mock data / time).
"""

import os
from pathlib import Path

import pytest

from src.plugin_system.testing.harness import (
    render_plugin_matrix, compare_to_goldens,
)
from src.plugin_system.testing.loading import load_config_defaults, load_harness_spec

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _plugin_search_dirs():
    env = os.environ.get("LEDMATRIX_PLUGINS_DIR")
    if env:
        return [Path(p) for p in env.split(os.pathsep) if p]
    return [PROJECT_ROOT / "plugin-repos", PROJECT_ROOT / "plugins"]


def _discover():
    """Map plugin_id -> plugin_dir for all plugins on the search path."""
    found = {}
    for base in _plugin_search_dirs():
        if not base.exists():
            continue
        for child in sorted(base.iterdir()):
            if (child / "manifest.json").exists() and child.name not in found:
                found[child.name] = child
    return found


_PLUGINS = _discover()


@pytest.mark.plugin
@pytest.mark.skipif(not _PLUGINS, reason="no plugins found on the search path")
@pytest.mark.parametrize("plugin_id", sorted(_PLUGINS))
def test_plugin_renders_across_sizes_and_screens(plugin_id):
    plugin_dir = _PLUGINS[plugin_id]
    spec = load_harness_spec(plugin_dir)

    config = {"enabled": True}
    config.update(load_config_defaults(plugin_dir))
    config.update(spec.get("config", {}))

    results = render_plugin_matrix(
        plugin_id=plugin_id,
        plugin_dir=plugin_dir,
        config=config,
        mock_data=spec.get("mock_data_contents", {}),
        run_update=not spec.get("skip_update", False),
        freeze_time=spec.get("freeze_time"),
    )
    compare_to_goldens(results, plugin_dir / "test" / "golden")

    failures = []
    for r in results:
        if r.error is not None:
            failures.append(f"{r.size_label} {r.mode}: crashed: {r.error}")
        elif r.overflow is not None:
            failures.append(f"{r.size_label} {r.mode}: overflow past panel bbox={r.overflow}")
        elif r.golden_checked and r.golden_ok is False:
            failures.append(
                f"{r.size_label} {r.mode}: golden drift {r.golden_diff_pixels}px "
                f"(max Δ={r.golden_max_delta})"
            )

    assert not failures, f"{plugin_id} failed:\n  " + "\n  ".join(failures)
