#!/usr/bin/env python3
"""
Update all plugin repositories by pulling the latest changes.
This script updates all plugin repos without needing to modify
the LEDMatrix project itself.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

# Paths
WORKSPACE_FILE = Path(__file__).parent.parent / "LEDMatrix.code-workspace"
GITHUB_DIR = Path(__file__).parent.parent.parent


def load_workspace_plugins():
    """Load plugin paths from workspace file."""
    with open(WORKSPACE_FILE, 'r') as f:
        workspace = json.load(f)
    
    plugins = []
    for folder in workspace.get('folders', []):
        path = folder.get('path', '')
        name = folder.get('name', '')
        
        # Only process plugin folders (those starting with ../)
        if path.startswith('../') and path != '../ledmatrix-plugins':
            plugin_name = path.replace('../', '')
            plugin_path = GITHUB_DIR / plugin_name
            if plugin_path.exists():
                plugins.append({
                    'name': plugin_name,
                    'display_name': name,
                    'path': plugin_path
                })
    
    return plugins


def update_repo(repo_path):
    """Update a git repository by pulling latest changes."""
    if not (repo_path / '.git').exists():
        print(f"  ⚠️  {repo_path.name} is not a git repository, skipping")
        return False
    
    try:
        # Fetch latest changes
        result = subprocess.run(['git', 'fetch', 'origin'],
                              cwd=repo_path, capture_output=True, text=True)
        
        # Get current branch
        branch_result = subprocess.run(['git', 'rev-parse', '--abbrev-ref', 'HEAD'],
                                      cwd=repo_path, capture_output=True, text=True)
        current_branch = branch_result.stdout.strip() if branch_result.returncode == 0 else 'main'
        
        # Pull latest changes
        result = subprocess.run(['git', 'pull', 'origin', current_branch],
                              cwd=repo_path, capture_output=True, text=True)
        
        if result.returncode == 0:
            # Check if there were actual updates
            if 'Already up to date' in result.stdout:
                print(f"  ✓  {repo_path.name} is up to date")
            else:
                print(f"  ✓  Updated {repo_path.name}")
            return True
        else:
            print(f"  ✗  Failed to update {repo_path.name}: {result.stderr.strip()}")
            return False
    except Exception as e:
        print(f"  ✗  Error updating {repo_path.name}: {e}")
        return False


def main():
    """Main function."""
    print("🔍 Finding plugin repositories...")
    
    plugins = load_workspace_plugins()
    
    if not plugins:
        print("  No plugin repositories found!")
        return 1
    
    print(f"  Found {len(plugins)} plugin repositories")
    print(f"\n🚀 Updating plugins in {GITHUB_DIR}...")
    print()
    
    success_count = 0
    for plugin in plugins:
        print(f"Updating {plugin['name']}...")
        if update_repo(plugin['path']):
            success_count += 1
        print()
    
    print(f"\n✅ Updated {success_count}/{len(plugins)} plugins successfully!")
    
    if success_count < len(plugins):
        print("⚠️  Some plugins failed to update. Check the errors above.")
        return 1
    
    return 0


if __name__ == '__main__':
    sys.exit(main())
