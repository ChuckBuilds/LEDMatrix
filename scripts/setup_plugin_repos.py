#!/usr/bin/env python3
"""
Setup plugin repository references for multi-root workspace.

This script creates symlinks in plugin-repos/ pointing to the actual
plugin repositories in the parent directory, allowing the system to
find plugins without modifying the LEDMatrix project structure.
"""

import json
import os
import sys
from pathlib import Path

# Paths
PROJECT_ROOT = Path(__file__).parent.parent
PLUGIN_REPOS_DIR = PROJECT_ROOT / "plugin-repos"
GITHUB_DIR = PROJECT_ROOT.parent
CONFIG_FILE = PROJECT_ROOT / "config" / "config.json"


def get_workspace_plugins():
    """Get list of plugins from workspace file."""
    workspace_file = PROJECT_ROOT / "LEDMatrix.code-workspace"
    if not workspace_file.exists():
        return []
    
    try:
        with open(workspace_file, 'r') as f:
            workspace = json.load(f)
    except json.JSONDecodeError as e:
        print(f"Error: Failed to parse workspace file {workspace_file}: {e}")
        print("Please check that the workspace file contains valid JSON.")
        return []
    
    plugins = []
    for folder in workspace.get('folders', []):
        path = folder.get('path', '')
        if path.startswith('../') and path != '../ledmatrix-plugins':
            plugin_name = path.replace('../', '')
            plugins.append({
                'name': plugin_name,
                'workspace_path': path,
                'actual_path': GITHUB_DIR / plugin_name,
                'link_path': PLUGIN_REPOS_DIR / plugin_name
            })
    
    return plugins


def create_symlinks():
    """Create symlinks in plugin-repos/ pointing to actual repos."""
    plugins = get_workspace_plugins()
    
    if not plugins:
        print("No plugins found in workspace configuration")
        return False
    
    # Ensure plugin-repos directory exists
    PLUGIN_REPOS_DIR.mkdir(exist_ok=True)
    
    created = 0
    skipped = 0
    errors = 0
    
    print(f"Setting up plugin repository links...")
    print(f"  Source: {GITHUB_DIR}")
    print(f"  Links:  {PLUGIN_REPOS_DIR}")
    print()
    
    for plugin in plugins:
        actual_path = plugin['actual_path']
        link_path = plugin['link_path']
        
        if not actual_path.exists():
            print(f"  ⚠️  {plugin['name']} - source not found: {actual_path}")
            errors += 1
            continue
        
        # Remove existing link/file if it exists
        if link_path.exists() or link_path.is_symlink():
            if link_path.is_symlink():
                # Check if it points to the right place
                try:
                    if link_path.resolve() == actual_path.resolve():
                        print(f"  ✓  {plugin['name']} - link already exists")
                        skipped += 1
                        continue
                    else:
                        # Remove old symlink pointing elsewhere
                        link_path.unlink()
                except Exception as e:
                    print(f"  ⚠️  {plugin['name']} - error checking link: {e}")
                    link_path.unlink()
            else:
                # It's a directory/file, not a symlink
                print(f"  ⚠️  {plugin['name']} - {link_path.name} exists but is not a symlink")
                print(f"      Skipping (manual cleanup required)")
                skipped += 1
                continue
        
        # Create symlink
        try:
            # Use relative path for symlink portability
            relative_path = os.path.relpath(actual_path, link_path.parent)
            link_path.symlink_to(relative_path)
            print(f"  ✓  {plugin['name']} - linked")
            created += 1
        except Exception as e:
            print(f"  ✗  {plugin['name']} - failed to create link: {e}")
            errors += 1
    
    print()
    print(f"✅ Created {created} links, skipped {skipped}, errors {errors}")
    
    return errors == 0


def update_config_path():
    """Update config to use absolute path to parent directory (alternative approach)."""
    # This is an alternative - set plugins_directory to absolute path
    # Currently not implemented as symlinks are preferred
    pass


def main():
    """Main function."""
    print("🔗 Setting up plugin repository symlinks...")
    print()
    
    if not GITHUB_DIR.exists():
        print(f"Error: GitHub directory not found: {GITHUB_DIR}")
        return 1
    
    success = create_symlinks()
    
    if success:
        print()
        print("✅ Plugin repository setup complete!")
        print()
        print("Plugins are now accessible via symlinks in plugin-repos/")
        print("You can update plugins independently in their git repos.")
        return 0
    else:
        print()
        print("⚠️  Setup completed with some errors. Check output above.")
        return 1


if __name__ == '__main__':
    sys.exit(main())
