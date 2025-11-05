# Automatic Plugin Version Bumping

This system automatically bumps plugin versions when code changes are pushed to plugin repositories.

## Overview

When you push code changes to a plugin repository, the git pre-push hook automatically:
1. Detects if code files (excluding manifest.json) have changed
2. Bumps the patch version (x.y.Z) in manifest.json
3. Updates the versions array with the new version
4. Stages the manifest.json file for commit

## Quick Start

### Install for All Plugins

```bash
./scripts/install-plugin-version-hook.sh
```

This installs the pre-push hook for all plugin repositories in `plugins/`.

### Install for a Specific Plugin

```bash
./scripts/install-plugin-version-hook.sh plugins/hockey-scoreboard
```

### Manual Installation

```bash
cd plugins/your-plugin
cp ../../scripts/git-hooks/pre-push-plugin-version .git/hooks/pre-push
chmod +x .git/hooks/pre-push
```

## How It Works

### Git Pre-Push Hook

The hook (`scripts/git-hooks/pre-push-plugin-version`) runs automatically before each `git push`:

1. **Checks for code changes**: Compares the commits being pushed with the remote
2. **Detects manifest changes**: If only manifest.json changed, no version bump needed
3. **Bumps version**: If code files changed, automatically bumps patch version
4. **Stages manifest**: Adds the updated manifest.json to staging

### Version Bump Script

The script (`scripts/bump_plugin_version.py`) can be used manually:

```bash
# Bump patch version (1.2.3 → 1.2.4)
python3 scripts/bump_plugin_version.py

# Bump minor version (1.2.3 → 1.3.0)
python3 scripts/bump_plugin_version.py --type minor

# Bump major version (1.2.3 → 2.0.0)
python3 scripts/bump_plugin_version.py --type major

# Dry run (see what would change)
python3 scripts/bump_plugin_version.py --dry-run
```

## What Gets Updated

When a version is bumped, the script updates:

1. **`manifest.json` → `version`**: Main version field
2. **`manifest.json` → `versions` array**: Adds new version entry at the beginning
3. **`manifest.json` → `last_updated`**: Current date

Example:
```json
{
  "version": "1.0.4",  // Updated
  "versions": [
    {
      "version": "1.0.4",  // New entry
      "ledmatrix_min": "2.0.0",
      "released": "2025-11-04"  // Current date
    },
    {
      "version": "1.0.3",  // Previous version
      ...
    }
  ],
  "last_updated": "2025-11-04"  // Updated
}
```

## Workflow

### Typical Workflow

1. **Make code changes** to your plugin
2. **Commit changes**: `git add . && git commit -m "fix: Fix bug in display"`
3. **Push**: `git push`
   - Hook automatically detects code changes
   - Bumps version from 1.0.3 → 1.0.4
   - Stages manifest.json
4. **Amend commit** (recommended): `git commit --amend --no-edit`
   - This includes the version bump in the same commit
5. **Push again**: `git push --force-with-lease`

### Alternative Workflow

If you prefer separate commits:

1. Make code changes and commit
2. Push (hook bumps version)
3. Create new commit: `git commit -m "chore: Bump version to 1.0.4"`
4. Push again

## When Version Bumps

The hook bumps the version when:
- ✅ Code files (Python, JSON configs, etc.) are changed
- ✅ Changes are being pushed to a remote

The hook does NOT bump when:
- ❌ Only manifest.json changed
- ❌ No changes are being pushed (local-only commits)
- ❌ Pushing to a branch that already has these commits

## Version Types

- **Patch** (default): Bug fixes, minor improvements (1.2.3 → 1.2.4)
- **Minor**: New features, backward compatible (1.2.3 → 1.3.0)
- **Major**: Breaking changes (1.2.3 → 2.0.0)

## Troubleshooting

### Hook Not Running

Check if the hook is installed:
```bash
ls -la plugins/your-plugin/.git/hooks/pre-push
```

Reinstall if needed:
```bash
./scripts/install-plugin-version-hook.sh plugins/your-plugin
```

### Version Not Bumping

The hook only bumps if:
- Code files changed (not just manifest.json)
- You're actually pushing commits (not just checking)

To force a version bump manually:
```bash
python3 scripts/bump_plugin_version.py
git add manifest.json
git commit -m "chore: Bump version"
```

### Script Not Found

The hook looks for the script in several locations:
- `scripts/bump_plugin_version.py` (relative to plugin)
- `../../scripts/bump_plugin_version.py` (from plugin directory)
- `./scripts/bump_plugin_version.py`

Make sure the script exists in the main LEDMatrix repository.

## Integration with Plugin Store

The plugin store automatically:
- Fetches latest versions from GitHub manifest.json files
- Shows current versions even if registry hasn't updated yet
- Registry auto-updates every 6 hours via GitHub Actions

So when you bump and push a version:
1. Users see it immediately in the plugin store (fetches from GitHub)
2. Registry updates automatically within 6 hours
3. All systems stay in sync

## Best Practices

1. **Always let the hook bump versions automatically** - it's consistent and reliable
2. **Use patch for bug fixes** - most common case
3. **Use minor for features** - when adding new functionality
4. **Use major sparingly** - only for breaking changes
5. **Amend commits** - include version bump in the same commit as code changes
6. **Test before pushing** - make sure your code works before version bump

## Disabling the Hook

If you need to push without version bumping:

```bash
# Temporarily disable
mv plugins/your-plugin/.git/hooks/pre-push plugins/your-plugin/.git/hooks/pre-push.disabled

# Push your changes
git push

# Re-enable
mv plugins/your-plugin/.git/hooks/pre-push.disabled plugins/your-plugin/.git/hooks/pre-push
```

Or use the `--no-verify` flag (not recommended):
```bash
git push --no-verify
```

