#!/usr/bin/env python3
"""
Plugin Visual Renderer

Loads a plugin, calls update() + display(), and saves the resulting
display as a PNG image for visual inspection.

Usage:
    python scripts/render_plugin.py --plugin hello-world --output /tmp/hello.png
    python scripts/render_plugin.py --plugin clock-simple --plugin-dir plugin-repos/ --output /tmp/clock.png
    python scripts/render_plugin.py --plugin hello-world --config '{"message":"Test!"}' --output /tmp/test.png
    python scripts/render_plugin.py --plugin football-scoreboard --mock-data mock_scores.json --output /tmp/football.png
"""

import sys
import os
import json
import argparse
from pathlib import Path

# Add project root to path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# Prevent hardware imports
os.environ['EMULATOR'] = 'true'


def find_plugin_dir(plugin_id, search_dirs):
    """Find a plugin directory by searching multiple paths."""
    from src.plugin_system.plugin_loader import PluginLoader
    loader = PluginLoader()
    for search_dir in search_dirs:
        search_path = Path(search_dir)
        if not search_path.exists():
            continue
        result = loader.find_plugin_directory(plugin_id, search_path)
        if result:
            return result
    return None


def load_manifest(plugin_dir):
    """Load and return manifest.json from plugin directory."""
    manifest_path = plugin_dir / 'manifest.json'
    if not manifest_path.exists():
        raise FileNotFoundError(f"No manifest.json in {plugin_dir}")
    with open(manifest_path, 'r') as f:
        return json.load(f)


def load_config_defaults(plugin_dir):
    """Extract default values from config_schema.json."""
    schema_path = plugin_dir / 'config_schema.json'
    if not schema_path.exists():
        return {}
    with open(schema_path, 'r') as f:
        schema = json.load(f)
    defaults = {}
    for key, prop in schema.get('properties', {}).items():
        if 'default' in prop:
            defaults[key] = prop['default']
    return defaults


def main():
    parser = argparse.ArgumentParser(description='Render a plugin display to a PNG image')
    parser.add_argument('--plugin', '-p', required=True, help='Plugin ID to render')
    parser.add_argument('--plugin-dir', '-d', default=None,
                        help='Directory to search for plugins (default: auto-detect)')
    parser.add_argument('--config', '-c', default='{}',
                        help='Plugin config as JSON string')
    parser.add_argument('--mock-data', '-m', default=None,
                        help='Path to JSON file with mock cache data')
    parser.add_argument('--output', '-o', default='/tmp/plugin_render.png',
                        help='Output PNG path (default: /tmp/plugin_render.png)')
    parser.add_argument('--width', type=int, default=128, help='Display width (default: 128)')
    parser.add_argument('--height', type=int, default=32, help='Display height (default: 32)')
    parser.add_argument('--skip-update', action='store_true',
                        help='Skip calling update() (render display only)')

    args = parser.parse_args()

    # Determine search directories
    if args.plugin_dir:
        search_dirs = [args.plugin_dir]
    else:
        search_dirs = [
            str(PROJECT_ROOT / 'plugins'),
            str(PROJECT_ROOT / 'plugin-repos'),
        ]

    # Find plugin
    plugin_dir = find_plugin_dir(args.plugin, search_dirs)
    if not plugin_dir:
        print(f"Error: Plugin '{args.plugin}' not found in: {search_dirs}")
        return 1

    print(f"Found plugin at: {plugin_dir}")

    # Load manifest
    manifest = load_manifest(Path(plugin_dir))

    # Parse config: start with schema defaults, then apply overrides
    config_defaults = load_config_defaults(Path(plugin_dir))
    try:
        user_config = json.loads(args.config)
    except json.JSONDecodeError as e:
        print(f"Error: Invalid JSON config: {e}")
        return 1

    config = {'enabled': True}
    config.update(config_defaults)
    config.update(user_config)

    # Load mock data if provided
    mock_data = {}
    if args.mock_data:
        mock_data_path = Path(args.mock_data)
        if not mock_data_path.exists():
            print(f"Error: Mock data file not found: {args.mock_data}")
            return 1
        with open(mock_data_path, 'r') as f:
            mock_data = json.load(f)

    # Create visual display manager and mocks
    from src.plugin_system.testing import VisualTestDisplayManager, MockCacheManager, MockPluginManager
    from src.plugin_system.plugin_loader import PluginLoader

    display_manager = VisualTestDisplayManager(width=args.width, height=args.height)
    cache_manager = MockCacheManager()
    plugin_manager = MockPluginManager()

    # Pre-populate cache with mock data
    for key, value in mock_data.items():
        cache_manager.set(key, value)

    # Load and instantiate plugin
    loader = PluginLoader()

    try:
        plugin_instance, module = loader.load_plugin(
            plugin_id=args.plugin,
            manifest=manifest,
            plugin_dir=Path(plugin_dir),
            config=config,
            display_manager=display_manager,
            cache_manager=cache_manager,
            plugin_manager=plugin_manager,
            install_deps=True,
        )
    except Exception as e:
        print(f"Error loading plugin: {e}")
        return 1

    print(f"Plugin '{args.plugin}' loaded successfully")

    # Run update() then display()
    if not args.skip_update:
        try:
            plugin_instance.update()
            print("update() completed")
        except Exception as e:
            print(f"Warning: update() raised: {e}")
            print("Continuing to display()...")

    try:
        plugin_instance.display(force_clear=True)
        print("display() completed")
    except Exception as e:
        print(f"Error in display(): {e}")
        return 1

    # Save the rendered image
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    display_manager.save_snapshot(str(output_path))
    print(f"Rendered image saved to: {output_path}")
    print(f"Dimensions: {args.width}x{args.height}")

    return 0


if __name__ == '__main__':
    sys.exit(main())
