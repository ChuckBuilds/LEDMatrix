#!/usr/bin/env python3
"""
Auto-bump plugin version script.

Increments the patch version (x.y.Z) in manifest.json when code changes are pushed.
Can be used as a git pre-push hook or run manually.
"""

import json
import sys
from pathlib import Path
from datetime import datetime
from typing import Optional


def bump_version(version: str, bump_type: str = "patch") -> str:
    """
    Bump a semantic version string.
    
    Args:
        version: Version string (e.g., "1.2.3")
        bump_type: "major", "minor", or "patch" (default)
    
    Returns:
        Bumped version string
    """
    try:
        parts = version.split('.')
        major = int(parts[0])
        minor = int(parts[1]) if len(parts) > 1 else 0
        patch = int(parts[2]) if len(parts) > 2 else 0
        
        if bump_type == "major":
            major += 1
            minor = 0
            patch = 0
        elif bump_type == "minor":
            minor += 1
            patch = 0
        else:  # patch
            patch += 1
        
        return f"{major}.{minor}.{patch}"
    except (ValueError, IndexError):
        # If version format is invalid, default to 1.0.0
        return "1.0.0"


def update_manifest_version(manifest_path: Path, bump_type: str = "patch", dry_run: bool = False) -> Optional[str]:
    """
    Update version in plugin manifest.json.
    
    Args:
        manifest_path: Path to manifest.json
        bump_type: "major", "minor", or "patch" (default)
        dry_run: If True, don't write changes
    
    Returns:
        New version string or None if failed
    """
    if not manifest_path.exists():
        print(f"Error: manifest.json not found at {manifest_path}")
        return None
    
    try:
        with open(manifest_path, 'r', encoding='utf-8') as f:
            manifest = json.load(f)
        
        current_version = manifest.get('version', '1.0.0')
        new_version = bump_version(current_version, bump_type)
        
        if current_version == new_version:
            print(f"Version already at {current_version}, no bump needed")
            return current_version
        
        print(f"Bumping version: {current_version} → {new_version}")
        
        # Update main version field
        manifest['version'] = new_version
        
        # Update versions array - add new version at the beginning
        versions = manifest.get('versions', [])
        new_version_entry = {
            'version': new_version,
            'ledmatrix_min': versions[0].get('ledmatrix_min', '2.0.0') if versions else '2.0.0',
            'released': datetime.now().strftime('%Y-%m-%d')
        }
        versions.insert(0, new_version_entry)
        manifest['versions'] = versions
        
        # Update last_updated
        manifest['last_updated'] = datetime.now().strftime('%Y-%m-%d')
        
        if not dry_run:
            with open(manifest_path, 'w', encoding='utf-8') as f:
                json.dump(manifest, f, indent=2, ensure_ascii=False)
            print(f"✓ Updated {manifest_path}")
        
        return new_version
        
    except json.JSONDecodeError as e:
        print(f"Error: Invalid JSON in manifest.json: {e}")
        return None
    except Exception as e:
        print(f"Error updating manifest: {e}")
        return None


def main():
    """Main entry point."""
    import argparse
    
    parser = argparse.ArgumentParser(
        description='Bump plugin version in manifest.json'
    )
    parser.add_argument(
        '--manifest',
        type=str,
        default='manifest.json',
        help='Path to manifest.json (default: manifest.json)'
    )
    parser.add_argument(
        '--type',
        choices=['major', 'minor', 'patch'],
        default='patch',
        help='Version bump type (default: patch)'
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Show what would be changed without writing'
    )
    
    args = parser.parse_args()
    
    manifest_path = Path(args.manifest)
    if not manifest_path.is_absolute():
        # If relative path, try current directory first
        if not manifest_path.exists():
            # Try parent directories (for git hooks)
            current = Path.cwd()
            for parent in [current, current.parent, current.parent.parent]:
                test_path = parent / manifest_path.name
                if test_path.exists():
                    manifest_path = test_path
                    break
    
    new_version = update_manifest_version(manifest_path, args.type, args.dry_run)
    
    if new_version:
        if args.dry_run:
            print(f"Dry run: Would update to version {new_version}")
            sys.exit(0)
        else:
            print(f"Successfully bumped to version {new_version}")
            sys.exit(0)
    else:
        print("Failed to bump version")
        sys.exit(1)


if __name__ == '__main__':
    main()

