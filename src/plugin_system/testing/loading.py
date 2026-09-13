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
    """Load and return manifest.json from a plugin directory.

    Read as UTF-8 explicitly, not in the platform default encoding: JSON is
    UTF-8 by RFC 8259, but `open()` honours the locale, which is cp1252 on
    Windows. A manifest carrying any non-ASCII byte (an em dash in a
    description, a degree sign in a mode name) therefore raised
    UnicodeDecodeError and aborted the whole `check_plugin.py --all` run on the
    byte rather than failing just that plugin. The three sibling loaders below
    read JSON from the same plugin trees and had the same bug.
    """
    manifest_path = Path(plugin_dir) / 'manifest.json'
    if not manifest_path.exists():
        raise FileNotFoundError(f"No manifest.json in {plugin_dir}")
    with open(manifest_path, 'r', encoding='utf-8') as f:
        return json.load(f)


def _defaults_from_properties(properties: Dict[str, Any]) -> Dict[str, Any]:
    """Defaults for one `properties` block, recursing into nested objects.

    An object property carries its defaults on its children, not on itself, so
    reading only the top level dropped everything nested. That is most of the
    fleet: config organised by league, or under customization/display_options,
    lost 2,386 defaults across 37 of 44 plugins -- soccer-scoreboard alone lost
    539 of 565 -- and the harness rendered them with a config no install would
    ever have.
    """
    defaults: Dict[str, Any] = {}
    for key, prop in (properties or {}).items():
        if not isinstance(prop, dict):
            continue
        if prop.get('type') == 'object' and isinstance(prop.get('properties'), dict):
            nested = _defaults_from_properties(prop['properties'])
            if nested:
                defaults[key] = nested
        elif 'default' in prop:
            defaults[key] = prop['default']
    return defaults


def merge_config(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    """Deep-merge override onto base, without dropping sibling defaults.

    A shallow merge would let `-c '{"nhl": {"enabled": true}}'` replace the whole
    nhl subtree and silently discard every other nhl default -- the same class of
    bug this function exists to fix.
    """
    merged = dict(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = merge_config(merged[key], value)
        else:
            merged[key] = value
    return merged


def load_config_defaults(plugin_dir: Union[str, Path]) -> Dict[str, Any]:
    """Extract default values from a plugin's config_schema.json (empty if none)."""
    schema_path = Path(plugin_dir) / 'config_schema.json'
    if not schema_path.exists():
        return {}
    with open(schema_path, 'r', encoding='utf-8') as f:
        schema = json.load(f)
    return _defaults_from_properties(schema.get('properties', {}))


def load_harness_spec(plugin_dir: Union[str, Path]) -> Dict[str, Any]:
    """Optional per-plugin harness settings from <plugin>/test/harness.json.

    Lets a plugin opt into golden-image testing by declaring how to render it
    deterministically. All keys optional:
        {
          "config":     {...},            # config overrides
          "mock_data":  "fixtures/mock.json",  # path (relative to plugin dir) to cache fixtures
          "freeze_time": "2025-08-01 15:25:00",
          "skip_update": false,
          "fill_check": "warn",           # or "strict": underfilled big panels FAIL
          "variants": [                   # extra runs with config overlays and
            {                             # their own golden dirs — e.g. an
              "name": "adaptive",         # opt-in adaptive mode tested beside
              "config": {"layout_mode": "adaptive"},   # the classic default
              "golden_dir": "test/golden-adaptive"
            }
          ]
        }
    Returns {} when no harness.json exists.
    """
    spec_path = Path(plugin_dir) / 'test' / 'harness.json'
    if not spec_path.exists():
        return {}
    with open(spec_path, 'r', encoding='utf-8') as f:
        spec = json.load(f)

    # Resolve mock_data path and inline its contents for convenience.
    mock_rel = spec.get('mock_data')
    if mock_rel:
        mock_path = Path(plugin_dir) / mock_rel
        if not mock_path.exists():
            # A declared-but-missing fixture is a harness config error: failing
            # loudly beats silently rendering the plugin with no mock data.
            raise FileNotFoundError(
                f"harness.json references mock_data '{mock_rel}' but "
                f"{mock_path} does not exist"
            )
        with open(mock_path, 'r', encoding='utf-8') as mf:
            spec['mock_data_contents'] = json.load(mf)
    return spec


def build_full_config(
    plugin_dir: Union[str, Path],
    spec: Optional[Dict[str, Any]] = None,
    cli_config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Build the config a plugin sees under test.

    Merge order: config_schema.json defaults, then a forced ``enabled: True``,
    then harness.json's config overlay, then the caller's explicit config --
    most specific wins. `enabled` is re-asserted *after* the schema defaults
    so a plugin that reasonably ships `enabled: false` (e.g. a seasonal or
    opt-in plugin) can't silently make every harness run test "disabled, do
    nothing" by accident -- callers that genuinely want to test the disabled
    path can still do so via `cli_config={"enabled": False}`.
    """
    spec = spec or {}
    config: Dict[str, Any] = {}
    config.update(load_config_defaults(plugin_dir))
    config["enabled"] = True
    config.update(spec.get("config", {}))
    config.update(cli_config or {})
    return config
