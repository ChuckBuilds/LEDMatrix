"""Headless single-render service for plugins.

Renders one plugin instance at one panel size to an in-memory PIL image —
no hardware, no singletons, no pip (install_deps is always False). Shared
by the dev server's /api/render endpoints and the production web UI's
config-page live preview.

A fresh plugin instance is created per call (mirroring the safety
harness), so repeated renders never share instance state. The plugin's
module does stay imported in the process — module-level globals persist
across calls, which is fine for previewing but worth knowing.
"""

import json
import time
from pathlib import Path
from typing import Any, Dict, Optional


def render_plugin_once(plugin_id: str, plugin_dir: Path,
                       manifest: Optional[Dict[str, Any]] = None,
                       config: Optional[Dict[str, Any]] = None,
                       mock_data: Optional[Dict[str, Any]] = None,
                       width: int = 128, height: int = 32,
                       skip_update: bool = True) -> Dict[str, Any]:
    """Render one plugin at one size. Returns a response-shaped dict:

        {'image': 'data:image/png;base64,...', 'width', 'height',
         'render_time_ms', 'errors': [...], 'warnings': [...]}

    ``skip_update`` defaults to True: update() may block on live network
    (sports APIs, Spotify) — callers that want real data should prime
    ``mock_data`` (e.g. from the plugin's test/harness.json fixture, see
    ``load_harness_spec``) or explicitly pass skip_update=False.

    Raises on plugin load failure; update()/display() exceptions are
    captured into warnings/errors instead so a broken render still shows
    whatever was drawn.
    """
    from src.plugin_system.plugin_loader import PluginLoader
    from src.plugin_system.testing import (
        MockCacheManager, MockPluginManager, VisualTestDisplayManager)

    plugin_dir = Path(plugin_dir)
    if manifest is None:
        with open(plugin_dir / 'manifest.json', 'r', encoding='utf-8') as f:
            manifest = json.load(f)
    config = config or {'enabled': True}
    mock_data = mock_data or {}

    display_manager = VisualTestDisplayManager(width=width, height=height)
    cache_manager = MockCacheManager()
    plugin_manager = MockPluginManager()

    # Pre-populate cache with mock data
    for key, value in mock_data.items():
        cache_manager.set(key, value)

    loader = PluginLoader()
    errors = []
    warnings = []

    plugin_instance, _module = loader.load_plugin(
        plugin_id=plugin_id,
        manifest=manifest,
        plugin_dir=plugin_dir,
        config=config,
        display_manager=display_manager,
        cache_manager=cache_manager,
        plugin_manager=plugin_manager,
        install_deps=False,
    )

    start_time = time.time()

    if not skip_update:
        try:
            plugin_instance.update()
        except Exception as e:
            warnings.append(f"update() raised: {e}")

    try:
        plugin_instance.display(force_clear=True)
    except Exception as e:
        errors.append(f"display() raised: {e}")

    render_time_ms = round((time.time() - start_time) * 1000, 1)

    return {
        'image': f'data:image/png;base64,{display_manager.get_image_base64()}',
        'width': width,
        'height': height,
        'render_time_ms': render_time_ms,
        'errors': errors,
        'warnings': warnings,
    }
