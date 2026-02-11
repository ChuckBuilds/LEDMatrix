#!/usr/bin/env python3
"""
Setup plugin repository symlinks for local development.

Creates symlinks in plugin-repos/ pointing to plugin directories
in the ledmatrix-plugins monorepo.
"""

import json
import os
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
PLUGIN_REPOS_DIR = PROJECT_ROOT / "plugin-repos"
MONOREPO_PLUGINS = PROJECT_ROOT.parent / "ledmatrix-plugins" / "plugins"


def parse_json_with_trailing_commas(text: str) -> dict:
    """Parse JSON that may have trailing commas."""
    text = re.sub(r",\s*([}\]])", r"\1", text)
    return json.loads(text)


def create_symlinks() -> bool:
    """Create symlinks in plugin-repos/ pointing to monorepo plugin dirs."""
    if not MONOREPO_PLUGINS.exists():
        print(f"Error: Monorepo plugins directory not found: {MONOREPO_PLUGINS}")
        return False

    PLUGIN_REPOS_DIR.mkdir(exist_ok=True)

    created = 0
    skipped = 0

    print("Setting up plugin symlinks...")
    print(f"  Source: {MONOREPO_PLUGINS}")
    print(f"  Links:  {PLUGIN_REPOS_DIR}")
    print()

    for plugin_dir in sorted(MONOREPO_PLUGINS.iterdir()):
        if not plugin_dir.is_dir():
            continue
        manifest_path = plugin_dir / "manifest.json"
        if not manifest_path.exists():
            continue

        try:
            with open(manifest_path, "r", encoding="utf-8") as f:
                manifest = parse_json_with_trailing_commas(f.read())
        except (OSError, json.JSONDecodeError) as e:
            print(f"  {plugin_dir.name} - failed to read {manifest_path}: {e}")
            continue
        plugin_id = manifest.get("id", plugin_dir.name)
        link_path = PLUGIN_REPOS_DIR / plugin_id

        if link_path.exists() or link_path.is_symlink():
            if link_path.is_symlink():
                try:
                    if link_path.resolve() == plugin_dir.resolve():
                        skipped += 1
                        continue
                    else:
                        link_path.unlink()
                except Exception:
                    link_path.unlink()
            else:
                print(f"  {plugin_id} - exists but is not a symlink, skipping")
                skipped += 1
                continue

        relative_path = os.path.relpath(plugin_dir, link_path.parent)
        link_path.symlink_to(relative_path)
        print(f"  {plugin_id} - linked")
        created += 1

    print(f"\nCreated {created} links, skipped {skipped}")
    return True


def main():
    print("Setting up plugin repository symlinks from monorepo...\n")
    create_symlinks()


if __name__ == "__main__":
    main()
