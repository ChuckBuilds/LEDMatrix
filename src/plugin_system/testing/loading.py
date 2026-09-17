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


def load_schema(plugin_dir: Union[str, Path]) -> Optional[Dict[str, Any]]:
    """A plugin's config_schema.json, or None when it has none."""
    schema_path = Path(plugin_dir) / 'config_schema.json'
    if not schema_path.exists():
        return None
    with open(schema_path, 'r', encoding='utf-8') as f:
        return json.load(f)


def load_config_defaults(plugin_dir: Union[str, Path]) -> Dict[str, Any]:
    """Default values from a plugin's config_schema.json (empty if none).

    The device's own extraction (schema_manager.extract_schema_defaults), so a
    harness run starts from the config an install would have: nested objects
    contribute their children's defaults (organised-by-league configs lost
    thousands of them when only the top level was read), and arrays without a
    default start as [].
    """
    from src.plugin_system.schema_manager import extract_schema_defaults
    schema = load_schema(plugin_dir)
    return extract_schema_defaults(schema) if schema else {}


def build_config(plugin_dir: Union[str, Path],
                 overrides: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """The config a device would give this plugin, with ``overrides`` applied.

    Starts from a forced ``enabled: True``, deep-merges ``overrides`` onto it
    (merge_config), then prepares the result exactly as the device does when it
    loads a plugin: legacy booleans read as objects, and schema plus core
    defaults filled in (schema_manager.prepare_plugin_config). Used by the
    harness, check_plugin, render_plugin and the dev preview server.
    """
    from src.plugin_system.schema_manager import (
        plugin_config_defaults, prepare_plugin_config,
    )
    schema = load_schema(plugin_dir)
    requested = merge_config({"enabled": True}, overrides or {})
    return prepare_plugin_config(requested, schema, plugin_config_defaults(schema))


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
    most specific wins, and each layer deep-merges (a nested override such as
    ``{"nhl": {"enabled": true}}`` keeps the other nhl defaults). `enabled` is
    asserted over the schema defaults so a plugin that reasonably ships
    `enabled: false` (e.g. a seasonal or opt-in plugin) can't silently make
    every harness run test "disabled, do nothing" by accident -- callers that
    genuinely want to test the disabled path can still do so via
    `cli_config={"enabled": False}`. See build_config.
    """
    spec = spec or {}
    overrides = merge_config(spec.get("config", {}) or {}, cli_config or {})
    return build_config(plugin_dir, overrides)
