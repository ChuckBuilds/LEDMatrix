#!/usr/bin/env python3
"""
Plugin safety checker.

Renders a plugin across every declared screen (mode) and every supported matrix
size, and fails if any screen crashes, overflows the panel, or (for plugins with
committed golden images) drifts visually.

Usage:
    # Functional + bounds check across all sizes/modes:
    python scripts/check_plugin.py --plugin clock-simple

    # Every discovered plugin:
    python scripts/check_plugin.py --all

    # Dump PNGs for each size/mode so you can eyeball them:
    python scripts/check_plugin.py --plugin ledmatrix-weather --out-dir /tmp/preview

    # Refresh committed golden images after an intentional visual change:
    python scripts/check_plugin.py --plugin clock-simple --update-golden \
        --mock-data plugins/clock-simple/test/fixtures/mock.json

Exit code is non-zero if any (plugin, size, mode) fails.
"""

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Dict, List, Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

os.environ['EMULATOR'] = 'true'

from src.logging_config import get_logger  # noqa: E402
from src.plugin_system.testing.loading import (  # noqa: E402
    find_plugin_dir, load_config_defaults,
)
from src.plugin_system.testing.harness import (  # noqa: E402
    RenderResult, render_plugin_matrix, compare_to_goldens, write_goldens,
)
from src.plugin_system.testing.sizes import (  # noqa: E402
    DEFAULT_TEST_SIZES, parse_size_token, safe_mode_filename, size_label,
)

logger = get_logger("[Check Plugin]")

DEFAULT_SEARCH_DIRS = [
    str(PROJECT_ROOT / 'plugins'),
    str(PROJECT_ROOT / 'plugin-repos'),
]


def discover_plugins(search_dirs: List[str]) -> List[str]:
    """All plugin ids found across the search dirs (dirs containing manifest.json)."""
    found = []
    for d in search_dirs:
        base = Path(d)
        if not base.exists():
            continue
        for child in sorted(base.iterdir()):
            if (child / 'manifest.json').exists() and child.name not in found:
                found.append(child.name)
    return found


def parse_sizes(spec: Optional[str]):
    if not spec:
        return DEFAULT_TEST_SIZES
    sizes = []
    for token in spec.split(','):
        if not token.strip():
            continue
        try:
            sizes.append(parse_size_token(token))
        except ValueError as exc:
            raise SystemExit(str(exc)) from exc
    return sizes


def check_one(plugin_id: str, search_dirs: List[str], sizes, mock_data: Dict,
              config: Dict, run_update: bool, out_dir: Optional[Path],
              update_golden: bool, golden_dir_override: Optional[Path],
              freeze_time: Optional[str]) -> List[RenderResult]:
    plugin_dir = find_plugin_dir(plugin_id, search_dirs)
    if not plugin_dir:
        logger.error("Plugin '%s' not found in: %s", plugin_id, search_dirs)
        return [RenderResult(plugin_id, 0, 0, "<not-found>", error="plugin directory not found")]

    # Start from config_schema defaults so plugins behave like a real install.
    full_config = {"enabled": True}
    full_config.update(load_config_defaults(plugin_dir))
    full_config.update(config)

    results = render_plugin_matrix(
        plugin_id=plugin_id, plugin_dir=plugin_dir, config=full_config,
        mock_data=mock_data, sizes=sizes, run_update=run_update,
        freeze_time=freeze_time,
    )

    golden_dir = golden_dir_override or (plugin_dir / 'test' / 'golden')
    if update_golden:
        written = write_goldens(results, golden_dir)
        logger.info("Wrote %d golden image(s) for %s to %s", written, plugin_id, golden_dir)
    else:
        compare_to_goldens(results, golden_dir)

    if out_dir:
        for r in results:
            if r.image is None:
                continue
            dest = out_dir / plugin_id / size_label(r.width, r.height)
            dest.mkdir(parents=True, exist_ok=True)
            r.image.save(dest / f"{safe_mode_filename(r.mode)}.png", format="PNG")

    return results


def print_report(all_results: Dict[str, List[RenderResult]]) -> bool:
    """Print a per-plugin grid. Returns True if everything passed."""
    everything_ok = True
    for plugin_id, results in all_results.items():
        print(f"\n=== {plugin_id} ===")
        for r in results:
            if r.ok:
                status = "PASS"
                detail = ""
                if r.golden_checked:
                    detail = " (golden ✓)"
                if r.update_error is not None:
                    detail += f" (update warn: {r.update_error})"
            else:
                everything_ok = False
                if r.error is not None:
                    status, detail = "FAIL", f" error={r.error}"
                elif r.overflow is not None:
                    status, detail = "FAIL", f" overflow bbox={r.overflow}"
                elif r.golden_ok is False:
                    status = "FAIL"
                    detail = f" golden drift: {r.golden_diff_pixels}px (max Δ={r.golden_max_delta})"
                else:
                    status, detail = "FAIL", ""
            print(f"  [{status}] {r.size_label:>7}  {r.mode}{detail}")
    print()
    return everything_ok


def main() -> int:
    parser = argparse.ArgumentParser(description="Check a plugin renders safely across sizes & screens")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--plugin', '-p', help='Plugin id to check')
    group.add_argument('--all', action='store_true', help='Check every discovered plugin')
    parser.add_argument('--plugin-dir', '-d', default=None, help='Directory to search for plugins')
    parser.add_argument('--sizes', default=None, help='Comma-separated WxH list (default: all supported)')
    parser.add_argument('--config', '-c', default='{}', help='Plugin config overrides as JSON')
    parser.add_argument('--mock-data', '-m', default=None, help='Path to JSON file with mock cache data')
    parser.add_argument('--out-dir', '-o', default=None, help='Also dump rendered PNGs here')
    parser.add_argument('--skip-update', action='store_true', help='Skip calling update()')
    parser.add_argument('--update-golden', action='store_true', help='Write/refresh golden images')
    parser.add_argument('--golden-dir', default=None, help='Override golden dir (default: <plugin>/test/golden)')
    parser.add_argument('--freeze-time', default=None,
                        help='Freeze wall clock, e.g. "2025-08-01 15:25:00" (for time-dependent plugins)')
    args = parser.parse_args()

    search_dirs = [args.plugin_dir] if args.plugin_dir else DEFAULT_SEARCH_DIRS
    sizes = parse_sizes(args.sizes)

    try:
        config = json.loads(args.config)
    except json.JSONDecodeError as e:
        logger.error("Invalid --config JSON: %s", e)
        return 2
    if not isinstance(config, dict):
        logger.error("--config must be a JSON object, got %s", type(config).__name__)
        return 2

    mock_data = {}
    if args.mock_data:
        mock_path = Path(args.mock_data)
        if not mock_path.exists():
            logger.error("Mock data file not found: %s", args.mock_data)
            return 2
        with open(mock_path) as f:
            mock_data = json.load(f)
        if not isinstance(mock_data, dict):
            logger.error("--mock-data must be a JSON object (key -> cache value), got %s",
                         type(mock_data).__name__)
            return 2

    plugin_ids = discover_plugins(search_dirs) if args.all else [args.plugin]
    if not plugin_ids:
        logger.error("No plugins found in: %s", search_dirs)
        return 2

    out_dir = Path(args.out_dir) if args.out_dir else None
    golden_dir_override = Path(args.golden_dir) if args.golden_dir else None

    all_results: Dict[str, List[RenderResult]] = {}
    for plugin_id in plugin_ids:
        all_results[plugin_id] = check_one(
            plugin_id=plugin_id, search_dirs=search_dirs, sizes=sizes,
            mock_data=mock_data, config=config, run_update=not args.skip_update,
            out_dir=out_dir, update_golden=args.update_golden,
            golden_dir_override=golden_dir_override, freeze_time=args.freeze_time,
        )

    # When refreshing goldens we skip drift comparison, but a crash or overflow
    # still means the plugin is broken — never let --update-golden mask that.
    ok = print_report(all_results)
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
