#!/usr/bin/env python3
"""
LEDMatrix Dev Preview Server

A standalone lightweight Flask app for rapid plugin development.
Pick a plugin, tweak its config, and instantly see the rendered display.

Usage:
    python scripts/dev_server.py
    python scripts/dev_server.py --port 5001
    python scripts/dev_server.py --extra-dir /path/to/custom-plugin

Opens at http://localhost:5001
"""

import sys
import os
import json
import time
import argparse
from pathlib import Path

# Add project root to path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# Prevent hardware imports
os.environ['EMULATOR'] = 'true'

from flask import Flask, render_template, request, jsonify

app = Flask(__name__, template_folder=str(Path(__file__).parent / 'templates'))

# Will be set from CLI args
_extra_dirs = []


# --------------------------------------------------------------------------
# Plugin discovery
# --------------------------------------------------------------------------

def get_search_dirs():
    """Get all directories to search for plugins."""
    dirs = [
        PROJECT_ROOT / 'plugins',
        PROJECT_ROOT / 'plugin-repos',
    ]
    for d in _extra_dirs:
        dirs.append(Path(d))
    return dirs


def discover_plugins():
    """Discover all available plugins across search directories."""
    plugins = []
    seen_ids = set()

    for search_dir in get_search_dirs():
        if not search_dir.exists():
            continue
        for item in sorted(search_dir.iterdir()):
            if not item.is_dir() or item.name.startswith('.'):
                continue
            manifest_path = item / 'manifest.json'
            if not manifest_path.exists():
                continue
            try:
                with open(manifest_path, 'r') as f:
                    manifest = json.load(f)
                plugin_id = manifest.get('id', item.name)
                if plugin_id in seen_ids:
                    continue
                seen_ids.add(plugin_id)
                plugins.append({
                    'id': plugin_id,
                    'name': manifest.get('name', plugin_id),
                    'description': manifest.get('description', ''),
                    'author': manifest.get('author', ''),
                    'version': manifest.get('version', ''),
                    'source_dir': str(search_dir),
                    'plugin_dir': str(item),
                })
            except (json.JSONDecodeError, OSError):
                continue

    return plugins


def find_plugin_dir(plugin_id):
    """Find a plugin directory by ID."""
    from src.plugin_system.plugin_loader import PluginLoader
    loader = PluginLoader()
    for search_dir in get_search_dirs():
        if not search_dir.exists():
            continue
        result = loader.find_plugin_directory(plugin_id, search_dir)
        if result:
            return Path(result)
    return None


def load_config_defaults(plugin_dir):
    """Extract default values from config_schema.json."""
    schema_path = Path(plugin_dir) / 'config_schema.json'
    if not schema_path.exists():
        return {}
    with open(schema_path, 'r') as f:
        schema = json.load(f)
    defaults = {}
    for key, prop in schema.get('properties', {}).items():
        if 'default' in prop:
            defaults[key] = prop['default']
    return defaults


# --------------------------------------------------------------------------
# Routes
# --------------------------------------------------------------------------

@app.route('/')
def index():
    """Serve the dev preview page."""
    return render_template('dev_preview.html')


@app.route('/api/plugins')
def api_plugins():
    """List all available plugins."""
    return jsonify({'plugins': discover_plugins()})


@app.route('/api/plugins/<plugin_id>/schema')
def api_plugin_schema(plugin_id):
    """Get a plugin's config_schema.json."""
    plugin_dir = find_plugin_dir(plugin_id)
    if not plugin_dir:
        return jsonify({'error': f'Plugin not found: {plugin_id}'}), 404

    schema_path = plugin_dir / 'config_schema.json'
    if not schema_path.exists():
        return jsonify({'schema': {'type': 'object', 'properties': {}}})

    with open(schema_path, 'r') as f:
        schema = json.load(f)
    return jsonify({'schema': schema})


@app.route('/api/plugins/<plugin_id>/defaults')
def api_plugin_defaults(plugin_id):
    """Get default config values from the schema."""
    plugin_dir = find_plugin_dir(plugin_id)
    if not plugin_dir:
        return jsonify({'error': f'Plugin not found: {plugin_id}'}), 404

    defaults = load_config_defaults(plugin_dir)
    defaults['enabled'] = True
    return jsonify({'defaults': defaults})


@app.route('/api/render', methods=['POST'])
def api_render():
    """Render a plugin and return the display as base64 PNG."""
    data = request.get_json()
    if not data or 'plugin_id' not in data:
        return jsonify({'error': 'plugin_id is required'}), 400

    plugin_id = data['plugin_id']
    user_config = data.get('config', {})
    width = data.get('width', 128)
    height = data.get('height', 32)
    mock_data = data.get('mock_data', {})
    skip_update = data.get('skip_update', False)

    # Find plugin
    plugin_dir = find_plugin_dir(plugin_id)
    if not plugin_dir:
        return jsonify({'error': f'Plugin not found: {plugin_id}'}), 404

    # Load manifest
    manifest_path = plugin_dir / 'manifest.json'
    with open(manifest_path, 'r') as f:
        manifest = json.load(f)

    # Build config: schema defaults + user overrides
    config_defaults = load_config_defaults(plugin_dir)
    config = {'enabled': True}
    config.update(config_defaults)
    config.update(user_config)

    # Create display manager and mocks
    from src.plugin_system.testing import VisualTestDisplayManager, MockCacheManager, MockPluginManager
    from src.plugin_system.plugin_loader import PluginLoader

    display_manager = VisualTestDisplayManager(width=width, height=height)
    cache_manager = MockCacheManager()
    plugin_manager = MockPluginManager()

    # Pre-populate cache with mock data
    for key, value in mock_data.items():
        cache_manager.set(key, value)

    # Load plugin
    loader = PluginLoader()
    errors = []
    warnings = []

    try:
        plugin_instance, module = loader.load_plugin(
            plugin_id=plugin_id,
            manifest=manifest,
            plugin_dir=plugin_dir,
            config=config,
            display_manager=display_manager,
            cache_manager=cache_manager,
            plugin_manager=plugin_manager,
            install_deps=True,
        )
    except Exception as e:
        return jsonify({'error': f'Failed to load plugin: {e}'}), 500

    start_time = time.time()

    # Run update()
    if not skip_update:
        try:
            plugin_instance.update()
        except Exception as e:
            warnings.append(f"update() raised: {e}")

    # Run display()
    try:
        plugin_instance.display(force_clear=True)
    except Exception as e:
        errors.append(f"display() raised: {e}")

    render_time_ms = round((time.time() - start_time) * 1000, 1)

    return jsonify({
        'image': f'data:image/png;base64,{display_manager.get_image_base64()}',
        'width': width,
        'height': height,
        'render_time_ms': render_time_ms,
        'errors': errors,
        'warnings': warnings,
    })


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description='LEDMatrix Dev Preview Server')
    parser.add_argument('--port', type=int, default=5001, help='Port to run on (default: 5001)')
    parser.add_argument('--host', default='127.0.0.1', help='Host to bind to (default: 127.0.0.1)')
    parser.add_argument('--extra-dir', action='append', default=[],
                        help='Extra plugin directory to search (can be repeated)')
    parser.add_argument('--debug', action='store_true', help='Enable Flask debug mode')

    args = parser.parse_args()

    global _extra_dirs
    _extra_dirs = args.extra_dir

    print(f"LEDMatrix Dev Preview Server")
    print(f"Open http://{args.host}:{args.port} in your browser")
    print(f"Plugin search dirs: {[str(d) for d in get_search_dirs()]}")
    print()

    app.run(host=args.host, port=args.port, debug=args.debug)


if __name__ == '__main__':
    main()
