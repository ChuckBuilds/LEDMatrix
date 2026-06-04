"""
Shared helpers for loading a plugin headlessly.

Used by scripts/render_plugin.py, scripts/check_plugin.py, and the harness so
plugin discovery / manifest / config-default logic lives in exactly one place.
"""

import json
from pathlib import Path
from typing import Any, Dict, Optional, Sequence, Union


def find_plugin_dir(plugin_id: str, search_dirs: Sequence[Union[str, Path]]) -> Optional[Path]:
    """Find a plugin directory by searching multiple paths."""
    from src.plugin_system.plugin_loader import PluginLoader
    loader = PluginLoader()
    for search_dir in search_dirs:
        search_path = Path(search_dir)
        if not search_path.exists():
            continue
        result = loader.find_plugin_directory(plugin_id, search_path)
        if result:
            return Path(result)
    return None


def load_manifest(plugin_dir: Union[str, Path]) -> Dict[str, Any]:
    """Load and return manifest.json from a plugin directory."""
    manifest_path = Path(plugin_dir) / 'manifest.json'
    if not manifest_path.exists():
        raise FileNotFoundError(f"No manifest.json in {plugin_dir}")
    with open(manifest_path, 'r') as f:
        return json.load(f)


def load_config_defaults(plugin_dir: Union[str, Path]) -> Dict[str, Any]:
    """Extract default values from a plugin's config_schema.json (empty if none)."""
    schema_path = Path(plugin_dir) / 'config_schema.json'
    if not schema_path.exists():
        return {}
    with open(schema_path, 'r') as f:
        schema = json.load(f)
    defaults: Dict[str, Any] = {}
    for key, prop in schema.get('properties', {}).items():
        if isinstance(prop, dict) and 'default' in prop:
            defaults[key] = prop['default']
    return defaults


def load_harness_spec(plugin_dir: Union[str, Path]) -> Dict[str, Any]:
    """Optional per-plugin harness settings from <plugin>/test/harness.json.

    Lets a plugin opt into golden-image testing by declaring how to render it
    deterministically. All keys optional:
        {
          "config":     {...},            # config overrides
          "mock_data":  "fixtures/mock.json",  # path (relative to plugin dir) to cache fixtures
          "freeze_time": "2025-08-01 15:25:00",
          "skip_update": false
        }
    Returns {} when no harness.json exists.
    """
    spec_path = Path(plugin_dir) / 'test' / 'harness.json'
    if not spec_path.exists():
        return {}
    with open(spec_path, 'r') as f:
        spec = json.load(f)

    # Resolve mock_data path and inline its contents for convenience.
    mock_rel = spec.get('mock_data')
    if mock_rel:
        mock_path = Path(plugin_dir) / mock_rel
        if mock_path.exists():
            with open(mock_path, 'r') as mf:
                spec['mock_data_contents'] = json.load(mf)
    return spec
