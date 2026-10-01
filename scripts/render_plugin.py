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

# Import logger after path setup so src.logging_config is importable
from src.logging_config import get_logger  # noqa: E402
from src.plugin_system.testing.loading import (  # noqa: E402
    build_full_config, find_plugin_dir, load_manifest,
)
logger = get_logger("[Render Plugin]")

MIN_DIMENSION = 1
MAX_DIMENSION = 512


def main() -> int:
    """Load a plugin, call update() + display(), and save the result as a PNG image."""
    parser = argparse.ArgumentParser(description='Render a plugin display to a PNG image')
    parser.add_argument('--plugin', '-p', required=True, help='Plugin ID to render')
    parser.add_argument('--plugin-dir', '-d', default=None,
                        help='Directory to search for plugins (default: auto-detect)')
    parser.add_argument('--config', '-c', default='{}',
                        help='Plugin config as JSON string')
    parser.add_argument('--mock-data', '-m', default=None,
                        help='Path to JSON file with mock cache data')
    parser.add_argument('--output', '-o', default='/tmp/plugin_render.png',  # nosec B108 - dev script default; user can override
                        help='Output PNG path (default: /tmp/plugin_render.png)')
    parser.add_argument('--width', type=int, default=128, help='Display width (default: 128)')
    parser.add_argument('--height', type=int, default=32, help='Display height (default: 32)')
    parser.add_argument('--skip-update', action='store_true',
                        help='Skip calling update() (render display only)')
    parser.add_argument('--display-mode', default=None,
                        help='Display mode to render, for plugins that declare '
                             'more than one in their manifest (e.g. nrl_live). '
                             'Omitted, the plugin picks its own default.')
    parser.add_argument('--vegas', action='store_true',
                        help="Render the plugin's block of the Vegas ticker strip "
                             "instead of display(): its live elements if it has "
                             "them, else its Vegas content, laid out as the "
                             "ticker lays them out. Also writes the live "
                             "elements' keys and columns to <output>.json")
    parser.add_argument('--no-live', action='store_true',
                        help="With --vegas: ignore live elements and render the "
                             "plugin's ordinary Vegas content (for before/after)")
    parser.add_argument('--timeline', type=int, default=0, metavar='ROWS',
                        help="With --vegas: render ROWS rows, each the block a "
                             "--timeline-step later as the ticker would update it "
                             "in place (animated elements redrawn for that moment)")
    parser.add_argument('--timeline-step', type=float, default=0.25, metavar='SECONDS',
                        help="Seconds between --timeline rows (default 0.25)")
    parser.add_argument('--timeline-update', action='store_true',
                        help="With --timeline: run update() before each row and "
                             "redraw every live element from the new data")

    args = parser.parse_args()

    if args.timeline > 1 and args.no_live:
        # A timeline shows live elements changing; plain content never does.
        parser.error("--timeline shows live elements; it cannot be combined with --no-live")
    if (args.timeline or args.no_live) and not args.vegas:
        parser.error("--timeline and --no-live need --vegas")

    if not (MIN_DIMENSION <= args.width <= MAX_DIMENSION):
        print(f"Error: --width must be between {MIN_DIMENSION} and {MAX_DIMENSION} (got {args.width})")
        raise SystemExit(1)
    if not (MIN_DIMENSION <= args.height <= MAX_DIMENSION):
        print(f"Error: --height must be between {MIN_DIMENSION} and {MAX_DIMENSION} (got {args.height})")
        raise SystemExit(1)

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
        logger.error("Plugin '%s' not found in: %s", args.plugin, search_dirs)
        return 1

    logger.info("Found plugin at: %s", plugin_dir)

    # Load manifest
    manifest = load_manifest(Path(plugin_dir))

    # Parse config: start with schema defaults, then apply overrides
    try:
        user_config = json.loads(args.config)
    except json.JSONDecodeError as e:
        logger.error("Invalid JSON config: %s", e)
        return 1

    config = build_full_config(Path(plugin_dir), cli_config=user_config)

    # Load mock data if provided
    mock_data = {}
    if args.mock_data:
        mock_data_path = Path(args.mock_data)
        if not mock_data_path.exists():
            logger.error("Mock data file not found: %s", args.mock_data)
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
        plugin_instance, _module = loader.load_plugin(
            plugin_id=args.plugin,
            manifest=manifest,
            plugin_dir=Path(plugin_dir),
            config=config,
            display_manager=display_manager,
            cache_manager=cache_manager,
            plugin_manager=plugin_manager,
            install_deps=False,
        )
    except (ImportError, OSError, ValueError) as e:
        logger.error("Error loading plugin '%s': %s", args.plugin, e)
        return 1

    logger.info("Plugin '%s' loaded successfully", args.plugin)

    # Run update() then display()
    if not args.skip_update:
        try:
            plugin_instance.update()
            logger.debug("update() completed")
        except Exception as e:
            logger.warning("update() raised: %s — continuing to display()", e)

    if args.vegas:
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)

    if args.vegas and args.timeline > 1:
        from src.plugin_system.testing.vegas import render_vegas_timeline
        image, rows = render_vegas_timeline(
            plugin_instance, args.plugin, display_manager, steps=args.timeline,
            step_seconds=args.timeline_step, run_update=args.timeline_update)
        if image is None:
            logger.error("Plugin '%s' has no Vegas content", args.plugin)
            return 1
        image.save(args.output)
        logger.info("Saved a %d-row Vegas timeline (%dx%d) to %s",
                    rows, image.width, image.height, args.output)
        return 0

    if args.vegas:
        from src.plugin_system.testing.vegas import render_vegas_strip
        block, layout = render_vegas_strip(
            plugin_instance, args.plugin, display_manager, live=not args.no_live)
        if block is None:
            logger.error("Plugin '%s' has no Vegas content", args.plugin)
            return 1
        block.save(args.output)
        sidecar = Path(args.output).with_suffix('.json')
        sidecar.write_text(json.dumps(
            {"width": block.width, "height": block.height,
             "live_elements": [{"key": k, "x": x, "width": w} for x, k, w in layout]},
            indent=2) + "\n", encoding="utf-8")
        logger.info("Saved Vegas strip %dx%d (%d live element(s)) to %s and %s",
                    block.width, block.height, len(layout), args.output, sidecar)
        return 0

    # A plugin that declares several display modes usually renders nothing
    # useful without being told which one to draw: the scoreboards keep their
    # state on per-mode sub-managers and their no-argument path returns False.
    # Only pass the argument when asked for, so the many plugins whose display()
    # takes no display_mode keep working untouched.
    try:
        if args.display_mode:
            try:
                plugin_instance.display(display_mode=args.display_mode,
                                        force_clear=True)
            except TypeError as error:
                if ("unexpected keyword argument" not in str(error)
                        or "display_mode" not in str(error)):
                    raise
                logger.warning(
                    "%s.display() does not accept display_mode; rendering its "
                    "default screen instead", args.plugin)
                plugin_instance.display(force_clear=True)
        else:
            plugin_instance.display(force_clear=True)
        logger.debug("display() completed")
    except Exception as e:
        logger.error("Error in display(): %s", e)
        return 1

    # Save the rendered image
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    display_manager.save_snapshot(str(output_path))
    logger.info("Rendered image saved to: %s (%dx%d)", output_path, args.width, args.height)

    return 0


if __name__ == '__main__':
    sys.exit(main())
