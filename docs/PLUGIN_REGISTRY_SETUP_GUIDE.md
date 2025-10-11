# Plugin Registry Setup Guide

This guide explains how to set up and maintain your official plugin registry at [https://github.com/ChuckBuilds/ledmatrix-plugins](https://github.com/ChuckBuilds/ledmatrix-plugins).

## Overview

Your plugin registry serves as a **central directory** that lists all official, verified plugins. The registry is just a JSON file; the actual plugins live in their own repositories.

## Repository Structure

```
ledmatrix-plugins/
├── README.md              # Main documentation
├── LICENSE               # GPL-3.0
├── plugins.json          # The registry file (main file!)
├── SUBMISSION.md         # Guidelines for submitting plugins
├── VERIFICATION.md       # Verification checklist
└── assets/               # Optional: screenshots, badges
    └── screenshots/
```

## Step 1: Create plugins.json

This is the **core file** that the Plugin Store reads from.

**File**: `plugins.json`

```json
{
  "version": "1.0.0",
  "last_updated": "2025-01-09T12:00:00Z",
  "plugins": [
    {
      "id": "clock-simple",
      "name": "Simple Clock",
      "description": "A clean, simple clock display with date and time",
      "author": "ChuckBuilds",
      "category": "time",
      "tags": ["clock", "time", "date"],
      "repo": "https://github.com/ChuckBuilds/ledmatrix-clock-simple",
      "branch": "main",
      "versions": [
        {
          "version": "1.0.0",
          "ledmatrix_min": "2.0.0",
          "released": "2025-01-09",
          "download_url": "https://github.com/ChuckBuilds/ledmatrix-clock-simple/archive/refs/tags/v1.0.0.zip"
        }
      ],
      "stars": 12,
      "downloads": 156,
      "last_updated": "2025-01-09",
      "verified": true,
      "screenshot": "https://raw.githubusercontent.com/ChuckBuilds/ledmatrix-plugins/main/assets/screenshots/clock-simple.png"
    }
  ]
}
```

## Step 2: Create Plugin Repositories

Each plugin should have its own repository:

### Example: Creating clock-simple Plugin

1. **Create new repo**: `ledmatrix-clock-simple`
2. **Add plugin files**:
   ```
   ledmatrix-clock-simple/
   ├── manifest.json
   ├── manager.py
   ├── requirements.txt
   ├── config_schema.json
   ├── README.md
   └── assets/
   ```
3. **Tag a release**: `git tag v1.0.0 && git push origin v1.0.0`
4. **Add to registry**: Update `plugins.json` in ledmatrix-plugins repo

## Step 3: Update README.md

Create a comprehensive README for your plugin registry:

```markdown
# LEDMatrix Official Plugins

Official plugin registry for [LEDMatrix](https://github.com/ChuckBuilds/LEDMatrix).

## Available Plugins

<!-- This table is auto-generated from plugins.json -->

| Plugin | Description | Category | Version |
|--------|-------------|----------|---------|
| [Simple Clock](https://github.com/ChuckBuilds/ledmatrix-clock-simple) | Clean clock display | Time | 1.0.0 |
| [NHL Scores](https://github.com/ChuckBuilds/ledmatrix-nhl-scores) | Live NHL scores | Sports | 1.0.0 |

## Installation

All plugins can be installed through the LEDMatrix web interface:

1. Open web interface (http://your-pi-ip:5050)
2. Go to Plugin Store tab
3. Browse or search for plugins
4. Click Install

Or via API:
```bash
curl -X POST http://your-pi-ip:5050/api/plugins/install \
  -d '{"plugin_id": "clock-simple"}'
```

## Submitting Plugins

See [SUBMISSION.md](SUBMISSION.md) for guidelines on submitting your plugin.

## Creating Plugins

See the main [LEDMatrix Plugin Developer Guide](https://github.com/ChuckBuilds/LEDMatrix/wiki/Plugin-Development).

## Plugin Categories

- **Time**: Clocks, timers, countdowns
- **Sports**: Scoreboards, schedules, stats
- **Weather**: Forecasts, current conditions
- **Finance**: Stocks, crypto, market data
- **Entertainment**: Games, animations, media
- **Custom**: Unique displays
```

## Step 4: Create SUBMISSION.md

Guidelines for community plugin submissions:

```markdown
# Plugin Submission Guidelines

Want to add your plugin to the official registry? Follow these steps!

## Requirements

Before submitting, ensure your plugin:

- ✅ Has a complete `manifest.json` with all required fields
- ✅ Follows the plugin architecture specification
- ✅ Has comprehensive README documentation
- ✅ Includes example configuration
- ✅ Has been tested on Raspberry Pi hardware
- ✅ Follows coding standards (PEP 8)
- ✅ Has proper error handling
- ✅ Uses logging appropriately
- ✅ Has no hardcoded API keys or secrets

## Submission Process

1. **Test Your Plugin**
   ```bash
   # Install via URL on your Pi
   curl -X POST http://your-pi:5050/api/plugins/install-from-url \
     -d '{"repo_url": "https://github.com/you/ledmatrix-your-plugin"}'
   ```

2. **Create Release**
   ```bash
   git tag v1.0.0
   git push origin v1.0.0
   ```

3. **Fork This Repo**
   Fork [ledmatrix-plugins](https://github.com/ChuckBuilds/ledmatrix-plugins)

4. **Update plugins.json**
   Add your plugin entry:
   ```json
   {
     "id": "your-plugin",
     "name": "Your Plugin Name",
     "description": "What it does",
     "author": "YourName",
     "category": "custom",
     "tags": ["tag1", "tag2"],
     "repo": "https://github.com/you/ledmatrix-your-plugin",
     "branch": "main",
     "versions": [
       {
         "version": "1.0.0",
         "ledmatrix_min": "2.0.0",
         "released": "2025-01-09",
         "download_url": "https://github.com/you/ledmatrix-your-plugin/archive/refs/tags/v1.0.0.zip"
       }
     ],
     "verified": false
   }
   ```

5. **Submit Pull Request**
   Create PR with title: "Add plugin: your-plugin-name"

## Review Process

1. **Automated Checks**: Manifest validation, structure check
2. **Code Review**: Manual review of plugin code
3. **Testing**: Test installation and basic functionality
4. **Approval**: If accepted, merged and marked as verified

## After Approval

- Plugin appears in official store
- `verified: true` badge shown
- Included in plugin count
- Featured in README

## Updating Your Plugin

To release a new version:

1. Create new release in your repo
2. Update `versions` array in plugins.json
3. Submit PR with changes
4. We'll review and merge

## Questions?

Open an issue in this repo or the main LEDMatrix repo.
```

## Step 5: Create VERIFICATION.md

Checklist for verifying plugins:

```markdown
# Plugin Verification Checklist

Use this checklist when reviewing plugin submissions.

## Code Review

- [ ] Follows BasePlugin interface
- [ ] Has proper error handling
- [ ] Uses logging appropriately
- [ ] No hardcoded secrets/API keys
- [ ] Follows Python coding standards
- [ ] Has type hints where appropriate
- [ ] Has docstrings for classes/methods

## Manifest Validation

- [ ] All required fields present
- [ ] Valid JSON syntax
- [ ] Correct version format (semver)
- [ ] Category is valid
- [ ] Tags are descriptive

## Functionality

- [ ] Installs successfully via URL
- [ ] Dependencies install correctly
- [ ] Plugin loads without errors
- [ ] Display output works correctly
- [ ] Configuration schema validates
- [ ] Example config provided

## Documentation

- [ ] README.md exists and is comprehensive
- [ ] Installation instructions clear
- [ ] Configuration options documented
- [ ] Examples provided
- [ ] License specified

## Security

- [ ] No malicious code
- [ ] Safe dependency versions
- [ ] Appropriate permissions
- [ ] No network access without disclosure
- [ ] No file system access outside plugin dir

## Testing

- [ ] Tested on Raspberry Pi
- [ ] Works with 64x32 matrix (minimum)
- [ ] No excessive CPU/memory usage
- [ ] No crashes or freezes

## Approval

Once all checks pass:
- [ ] Set `verified: true` in plugins.json
- [ ] Merge PR
- [ ] Welcome plugin author
- [ ] Update stats (downloads, stars)
```

## Step 6: Workflow for Adding Plugins

### For Your Own Plugins

```bash
# 1. Create plugin in separate repo
mkdir ledmatrix-clock-simple
cd ledmatrix-clock-simple
# ... create plugin files ...

# 2. Push to GitHub
git init
git add .
git commit -m "Initial commit"
git remote add origin https://github.com/ChuckBuilds/ledmatrix-clock-simple
git push -u origin main

# 3. Create release
git tag v1.0.0
git push origin v1.0.0

# 4. Update registry
cd ../ledmatrix-plugins
# Edit plugins.json to add new entry
git add plugins.json
git commit -m "Add clock-simple plugin"
git push
```

### For Community Submissions

```bash
# 1. Receive PR on ledmatrix-plugins repo
# 2. Review using VERIFICATION.md checklist
# 3. Test installation:
curl -X POST http://pi:5050/api/plugins/install-from-url \
  -d '{"repo_url": "https://github.com/contributor/plugin"}'

# 4. If approved, merge PR
# 5. Set verified: true in plugins.json
```

## Step 7: Maintaining the Registry

### Regular Updates

```bash
# Update stars/downloads counts
python3 scripts/update_stats.py

# Validate all plugin entries
python3 scripts/validate_registry.py

# Check for plugin updates
python3 scripts/check_updates.py
```

### Adding New Versions

When a plugin releases a new version, update the `versions` array:

```json
{
  "id": "clock-simple",
  "versions": [
    {
      "version": "1.1.0",
      "ledmatrix_min": "2.0.0",
      "released": "2025-01-15",
      "download_url": "https://github.com/ChuckBuilds/ledmatrix-clock-simple/archive/refs/tags/v1.1.0.zip"
    },
    {
      "version": "1.0.0",
      "ledmatrix_min": "2.0.0",
      "released": "2025-01-09",
      "download_url": "https://github.com/ChuckBuilds/ledmatrix-clock-simple/archive/refs/tags/v1.0.0.zip"
    }
  ]
}
```

## Converting Existing Plugins

To convert your existing plugins (hello-world, clock-simple) to this system:

### 1. Move to Separate Repos

```bash
# For each plugin in plugins/
cd plugins/clock-simple

# Create new repo
git init
git add .
git commit -m "Extract clock-simple plugin"
git remote add origin https://github.com/ChuckBuilds/ledmatrix-clock-simple
git push -u origin main
git tag v1.0.0
git push origin v1.0.0
```

### 2. Add to Registry

Update `plugins.json` in ledmatrix-plugins repo.

### 3. Keep or Remove from Main Repo

Decision:
- **Keep**: Leave in main repo for backward compatibility
- **Remove**: Delete from main repo, users install via store

## Testing the Registry

After setting up:

```bash
# Test registry fetch
curl https://raw.githubusercontent.com/ChuckBuilds/ledmatrix-plugins/main/plugins.json

# Test plugin installation
python3 -c "
from src.plugin_system.store_manager import PluginStoreManager
store = PluginStoreManager()
registry = store.fetch_registry()
print(f'Found {len(registry[\"plugins\"])} plugins')
"
```

## Benefits of This Setup

✅ **Centralized Discovery**: One place to find all official plugins  
✅ **Decentralized Storage**: Each plugin in its own repo  
✅ **Easy Maintenance**: Update registry without touching plugin code  
✅ **Community Friendly**: Anyone can submit via PR  
✅ **Version Control**: Track plugin versions and updates  
✅ **Verified Badge**: Show trust with verified plugins  

## Next Steps

1. Create `plugins.json` in your repo
2. Update the registry URL in LEDMatrix code (already done)
3. Create SUBMISSION.md and README.md
4. Move existing plugins to separate repos
5. Add them to the registry
6. Announce the plugin store!

## References

- Plugin Store Implementation: See `PLUGIN_STORE_IMPLEMENTATION_SUMMARY.md`
- User Guide: See `PLUGIN_STORE_USER_GUIDE.md`
- Architecture: See `PLUGIN_ARCHITECTURE_SPEC.md`

