#!/usr/bin/env python3
"""
Vegas Mode Density Audit

Reports how much of the Vegas ticker is actually showing something. Loads the
real enabled plugins, pulls each one's content through the real
``PluginAdapter``, composes the strip through the real ``ScrollHelper``, then
measures the result.

The headline number is the **dead-frame ratio**: the fraction of viewport
positions across a full cycle that are effectively blank. Because the panel
only ever shows ``display_width`` columns at a time, a blank stretch wider than
the viewport is a stretch where the display looks switched off — so this ratio
tracks perceived dead time rather than just counting unlit pixels.

Runs entirely off-hardware, so it is safe to run alongside a live display.

Usage:
    # Audit every enabled plugin at the display size from config.json
    python scripts/dev/vegas_audit.py

    # Specific plugins, dump each segment as a PNG for eyeballing
    python scripts/dev/vegas_audit.py -p of-the-day,youtube-stats --dump-dir /tmp/vg

    # Machine-readable, for before/after comparison
    python scripts/dev/vegas_audit.py --json > after.json
"""

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# Must precede any src import that may reach for hardware.
os.environ.setdefault('EMULATOR', 'true')

from PIL import Image  # noqa: E402

from src.common.scroll_helper import ScrollHelper  # noqa: E402
from src.plugin_system.testing.loading import (  # noqa: E402
    build_full_config,
    find_plugin_dir,
    load_manifest,
)
from src.vegas_mode.config import VegasModeConfig  # noqa: E402
from src.vegas_mode.geometry import (  # noqa: E402
    DEFAULT_INK_THRESHOLD,
    column_has_ink,
    content_bounds,
    dead_window_stats,
    window_coverage_stats,
)
from src.vegas_mode.plugin_adapter import PluginAdapter  # noqa: E402

# Sampling stride for the dead-window scan. A full cycle can be 30,000px wide;
# 4px granularity keeps the scan instant while staying well under the ~10px a
# single scroll step ever covers, so no dead stretch is missed.
DEAD_SCAN_STEP = 4


def load_main_config(path: Path) -> Dict[str, Any]:
    with open(path, 'r') as fh:
        return json.load(fh)


def display_size_from_config(config: Dict[str, Any]) -> tuple:
    """Derive the logical ticker size the way DisplayManager does."""
    hw = config.get('display', {}).get('hardware', {})
    cols = int(hw.get('cols', 64))
    chain = int(hw.get('chain_length', 1))
    rows = int(hw.get('rows', 32))
    parallel = int(hw.get('parallel', 1))
    return cols * chain, rows * parallel


def enabled_plugin_ids(config: Dict[str, Any]) -> List[str]:
    """Plugin IDs that are enabled in config, excluding non-plugin sections."""
    ids = []
    for key, value in config.items():
        if isinstance(value, dict) and value.get('enabled') is True:
            ids.append(key)
    return ids


def instantiate(plugin_id: str, display_manager, cache_manager, plugin_manager):
    """Load one plugin offline. Returns the instance or None."""
    from src.plugin_system.plugin_loader import PluginLoader

    search_dirs = [
        str(PROJECT_ROOT / 'plugin-repos'),
        str(PROJECT_ROOT / 'plugins'),
    ]
    plugin_dir = find_plugin_dir(plugin_id, search_dirs)
    if not plugin_dir:
        return None

    try:
        manifest = load_manifest(Path(plugin_dir))
        cfg = build_full_config(Path(plugin_dir))
        instance, _ = PluginLoader().load_plugin(
            plugin_id=plugin_id,
            manifest=manifest,
            plugin_dir=Path(plugin_dir),
            config=cfg,
            display_manager=display_manager,
            cache_manager=cache_manager,
            plugin_manager=plugin_manager,
            install_deps=False,
        )
        return instance
    except Exception as exc:  # noqa: BLE001 - audit tool must survive any plugin
        print(f"  ! {plugin_id}: load failed ({type(exc).__name__}: {exc})",
              file=sys.stderr)
        return None


def join_rows(images: List[Image.Image], gap: int) -> Image.Image:
    """Concatenate one plugin's rows, matching RenderPipeline._join_plugin_rows."""
    if len(images) == 1:
        return images[0]
    gap = max(0, gap)
    width = sum(img.width for img in images) + gap * (len(images) - 1)
    height = max(img.height for img in images)
    block = Image.new('RGB', (width, height), (0, 0, 0))
    x = 0
    for img in images:
        block.paste(img, (x, 0))
        x += img.width + gap
    return block


def measure_segment(images: List[Image.Image], display_width: int,
                    scroll_speed: float, threshold: int) -> Dict[str, Any]:
    """Geometry of one plugin's contribution to the ticker."""
    total_width = sum(img.width for img in images)
    combined = Image.new('RGB', (max(1, total_width), images[0].height))
    x = 0
    for img in images:
        combined.paste(img, (x, 0))
        x += img.width

    ink = column_has_ink(combined, threshold)
    bounds = content_bounds(combined, threshold)
    ink_cols = int(ink.sum())

    return {
        'images': len(images),
        'width_px': total_width,
        'ink_cols': ink_cols,
        'ink_pct': round(100.0 * ink_cols / total_width, 1) if total_width else 0.0,
        'lead_black_px': bounds[0] if bounds else total_width,
        'trail_black_px': (total_width - 1 - bounds[1]) if bounds else 0,
        'seconds_on_screen': round(total_width / scroll_speed, 1) if scroll_speed else 0.0,
        'widths': [img.width for img in images],
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description='Audit Vegas mode content density')
    parser.add_argument('--config', default=str(PROJECT_ROOT / 'config' / 'config.json'),
                        help='Path to main config.json')
    parser.add_argument('-p', '--plugins', default=None,
                        help='Comma-separated plugin IDs (default: all enabled)')
    parser.add_argument('--width', type=int, default=None,
                        help='Override display width (default: from config hardware)')
    parser.add_argument('--height', type=int, default=None,
                        help='Override display height (default: from config hardware)')
    parser.add_argument('--dump-dir', default=None,
                        help='Write each segment and the composed strip as PNGs here')
    parser.add_argument('--threshold', type=int, default=DEFAULT_INK_THRESHOLD,
                        help=f'Ink threshold (default: {DEFAULT_INK_THRESHOLD})')
    parser.add_argument('--per-cycle', type=int, default=None,
                        help='Plugins composed per cycle '
                             '(default: buffer_ahead + 1, matching production)')
    parser.add_argument('--json', action='store_true',
                        help='Emit JSON instead of a text report')
    args = parser.parse_args()

    config = load_main_config(Path(args.config))
    vegas = VegasModeConfig.from_config(config)

    cfg_w, cfg_h = display_size_from_config(config)
    width = args.width or cfg_w
    height = args.height or cfg_h
    speed = vegas.scroll_speed

    if args.plugins:
        plugin_ids = [p.strip() for p in args.plugins.split(',') if p.strip()]
    else:
        plugin_ids = vegas.get_ordered_plugins(enabled_plugin_ids(config))

    dump_dir = Path(args.dump_dir) if args.dump_dir else None
    if dump_dir:
        dump_dir.mkdir(parents=True, exist_ok=True)

    from src.plugin_system.testing import (
        MockCacheManager, MockPluginManager, VisualTestDisplayManager,
    )

    display_manager = VisualTestDisplayManager(width=width, height=height)
    cache_manager = MockCacheManager()
    plugin_manager = MockPluginManager()
    adapter = PluginAdapter(display_manager)

    if not args.json:
        print(f"Vegas audit — display {width}x{height}, scroll {speed:g}px/s, "
              f"separator {vegas.separator_width}px")
        print(f"One display width = {width / speed:.1f}s of screen time\n")

    results: List[Dict[str, Any]] = []
    segments: List[Image.Image] = []

    for plugin_id in plugin_ids:
        started = time.time()
        instance = instantiate(plugin_id, display_manager, cache_manager, plugin_manager)
        if instance is None:
            results.append({'plugin': plugin_id, 'status': 'load_failed'})
            continue

        plugin_manager.plugins[plugin_id] = instance
        adapter.invalidate_cache(plugin_id)

        try:
            images = adapter.get_content(instance, plugin_id)
        except Exception as exc:  # noqa: BLE001
            results.append({'plugin': plugin_id, 'status': 'fetch_error',
                            'error': f'{type(exc).__name__}: {exc}'})
            continue

        fetch_ms = round((time.time() - started) * 1000)

        if not images:
            results.append({'plugin': plugin_id, 'status': 'no_content',
                            'fetch_ms': fetch_ms})
            if not args.json:
                print(f"  {plugin_id:28s} NO CONTENT            ({fetch_ms}ms)")
            continue

        entry = {'plugin': plugin_id, 'status': 'ok', 'fetch_ms': fetch_ms}
        entry.update(measure_segment(images, width, speed, args.threshold))
        results.append(entry)
        segments.extend(images)

        if dump_dir:
            for idx, img in enumerate(images):
                img.save(dump_dir / f"{plugin_id}__{idx:02d}.png")

        if not args.json:
            print(f"  {plugin_id:28s} {entry['width_px']:>6d}px  "
                  f"{entry['images']:>2d} img  ink {entry['ink_pct']:>5.1f}%  "
                  f"lead {entry['lead_black_px']:>4d}  tail {entry['trail_black_px']:>4d}  "
                  f"{entry['seconds_on_screen']:>6.1f}s  ({fetch_ms}ms)")

    summary: Dict[str, Any] = {
        'display_width': width,
        'display_height': height,
        'scroll_speed': speed,
        'separator_width': vegas.separator_width,
        'plugins_audited': len(plugin_ids),
        'plugins_with_content': sum(1 for r in results if r.get('status') == 'ok'),
    }

    # Production composes only the plugins sitting in the active buffer, so
    # measuring one giant strip of every plugin would hide the per-cycle costs
    # (most importantly the leading gap, which is charged once per cycle).
    # Group the segments the way the running service does.
    per_cycle = max(1, args.per_cycle or vegas.plugins_per_cycle)

    cycles: List[Dict[str, Any]] = []
    with_content = [r for r in results if r.get('status') == 'ok']

    if segments:
        logger = logging.getLogger('vegas_audit')
        seg_index = 0
        for start in range(0, len(with_content), per_cycle):
            group = with_content[start:start + per_cycle]

            # Mirror RenderPipeline: each plugin's rows are joined by
            # intra_plugin_gap into one block, and separator_width is applied
            # only between blocks. Measuring a flat list here would report gaps
            # the service does not emit.
            blocks: List[Image.Image] = []
            for entry in group:
                count = entry['images']
                rows = segments[seg_index:seg_index + count]
                seg_index += count
                if rows:
                    blocks.append(join_rows(rows, vegas.intra_plugin_gap))
            if not blocks:
                continue

            # ScrollHelper logs unconditionally, so it needs a real logger.
            helper = ScrollHelper(width, height, logger)
            helper.create_scrolling_image(
                content_items=blocks,
                item_gap=vegas.separator_width,
                element_gap=0,
                # Must match RenderPipeline. Omitting this made the audit
                # measure a full-display-width leading gap the service no
                # longer emits, overstating dead space by 512px per cycle.
                lead_gap=vegas.lead_in_width,
            )
            composed = helper.cached_image
            if composed is None:
                continue

            dead = dead_window_stats(composed, width, args.threshold, step=DEAD_SCAN_STEP)
            cover = window_coverage_stats(
                composed, width, args.threshold, step=DEAD_SCAN_STEP)

            if dump_dir:
                composed.save(dump_dir / f"_cycle{len(cycles):02d}.png")

            cycles.append({
                'plugins': [e['plugin'] for e in group],
                'width_px': composed.width,
                'seconds': round(composed.width / speed, 1) if speed else 0.0,
                'dead_pct': round(100 * dead.dead_ratio, 1),
                'longest_dead_seconds': round(
                    dead.longest_dead_run * DEAD_SCAN_STEP / speed, 1) if speed else 0.0,
                'mean_ink_pct': round(100 * cover.mean_ink_ratio, 1),
                'sparse_pct': round(100 * cover.sparse_ratio, 1),
                'longest_sparse_seconds': round(
                    cover.longest_sparse_run * DEAD_SCAN_STEP / speed, 1) if speed else 0.0,
            })

    if cycles:
        total_px = sum(c['width_px'] for c in cycles)
        # Weight each cycle by its width so a long cycle counts proportionally.
        summary.update({
            'cycles': len(cycles),
            'total_px': total_px,
            'full_rotation_seconds': round(total_px / speed, 1) if speed else 0.0,
            'dead_pct': round(
                sum(c['dead_pct'] * c['width_px'] for c in cycles) / total_px, 1),
            'mean_ink_pct': round(
                sum(c['mean_ink_pct'] * c['width_px'] for c in cycles) / total_px, 1),
            'sparse_pct': round(
                sum(c['sparse_pct'] * c['width_px'] for c in cycles) / total_px, 1),
            'worst_dead_seconds': max(c['longest_dead_seconds'] for c in cycles),
            'worst_sparse_seconds': max(c['longest_sparse_seconds'] for c in cycles),
        })

    if args.json:
        print(json.dumps({'summary': summary, 'cycles': cycles, 'plugins': results},
                         indent=2))
    else:
        print(f"\n  Cycles ({per_cycle} plugins each, as production composes them):")
        for idx, cyc in enumerate(cycles):
            print(f"    [{idx}] {cyc['width_px']:>6d}px {cyc['seconds']:>6.1f}s  "
                  f"ink {cyc['mean_ink_pct']:>5.1f}%  blank {cyc['dead_pct']:>5.1f}%  "
                  f"worst blank {cyc['longest_dead_seconds']:>5.1f}s  "
                  f"| {', '.join(cyc['plugins'])}")

        print(f"\n  {'-' * 66}")
        print(f"  full rotation        {summary.get('full_rotation_seconds', 0):>7.1f}s "
              f"over {summary.get('cycles', 0)} cycles")
        print(f"  mean ink coverage    {summary.get('mean_ink_pct', 0):>7.1f}%  "
              f"(higher is better; target >25%)")
        print(f"  fully blank          {summary.get('dead_pct', 0):>7.1f}%  (target <2%)")
        print(f"  reads as empty       {summary.get('sparse_pct', 0):>7.1f}%  (target <15%)")
        print(f"  worst blank stretch  {summary.get('worst_dead_seconds', 0):>7.1f}s  "
              f"(target <1.5s)")
        print(f"  plugins w/ content   {summary.get('plugins_with_content', 0):>7d}"
              f" of {summary['plugins_audited']}")

    return 0


if __name__ == '__main__':
    raise SystemExit(main())
