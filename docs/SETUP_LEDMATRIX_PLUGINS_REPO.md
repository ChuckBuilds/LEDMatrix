# Quick Setup: ledmatrix-plugins Repository

Your repository is ready at [https://github.com/ChuckBuilds/ledmatrix-plugins](https://github.com/ChuckBuilds/ledmatrix-plugins). Here's exactly what to add:

## Files to Add

### 1. plugins.json (Required)

**Copy from**: `plugin_registry_template.json` in this repo

This is the **main file** that the Plugin Store reads. Upload it to the root of your ledmatrix-plugins repo.

```bash
# In your LEDMatrix repo
cp plugin_registry_template.json ../ledmatrix-plugins/plugins.json

# In ledmatrix-plugins repo
cd ../ledmatrix-plugins
git add plugins.json
git commit -m "Add plugin registry"
git push
```

### 2. README.md (Update existing)

Update your existing README.md:

````markdown
# LEDMatrix Official Plugins

Official plugin registry for [LEDMatrix](https://github.com/ChuckBuilds/LEDMatrix).

Browse and install plugins through the LEDMatrix web interface or directly via URL.

## Available Plugins

| Plugin | Description | Category | Status |
|--------|-------------|----------|--------|
| [Hello World](https://github.com/ChuckBuilds/LEDMatrix/tree/main/plugins/hello-world) | Example plugin with customizable text | Example | ✓ Verified |
| [Simple Clock](https://github.com/ChuckBuilds/LEDMatrix/tree/main/plugins/clock-simple) | Clean clock display | Time | ✓ Verified |

## Installation

### Via Web Interface
1. Open LEDMatrix web interface (http://your-pi-ip:5050)
2. Go to **Plugin Store** tab
3. Browse or search for plugins
4. Click **Install**

### Via API
```bash
curl -X POST http://your-pi-ip:5050/api/plugins/install \
  -H "Content-Type: application/json" \
  -d '{"plugin_id": "clock-simple"}'
```

### Via GitHub URL
Any plugin can be installed directly from its GitHub repository:

```bash
curl -X POST http://your-pi-ip:5050/api/plugins/install-from-url \
  -H "Content-Type: application/json" \
  -d '{"repo_url": "https://github.com/user/ledmatrix-custom-plugin"}'
```

## Submitting Your Plugin

Want to add your plugin to the official registry?

1. Create your plugin following the [Plugin Developer Guide](https://github.com/ChuckBuilds/LEDMatrix/blob/main/PLUGIN_ARCHITECTURE_SPEC.md)
2. Test it using "Install from URL"
3. Create a GitHub release with version tag
4. Fork this repo
5. Add your plugin to `plugins.json`
6. Submit a Pull Request

See [SUBMISSION.md](SUBMISSION.md) for detailed guidelines.

## Creating Plugins

See the main LEDMatrix repository for:
- [Plugin Architecture Specification](https://github.com/ChuckBuilds/LEDMatrix/blob/main/PLUGIN_ARCHITECTURE_SPEC.md)
- [Plugin Developer Guide](https://github.com/ChuckBuilds/LEDMatrix/blob/main/PLUGIN_DEVELOPER_GUIDE.md)
- [Example Plugins](https://github.com/ChuckBuilds/LEDMatrix/tree/main/plugins)

## Registry Structure

```json
{
  "version": "1.0.0",
  "plugins": [
    {
      "id": "plugin-id",
      "name": "Plugin Name",
      "repo": "https://github.com/user/repo",
      "versions": [...],
      "verified": true
    }
  ]
}
```

## Categories

- **Time**: Clocks, timers, countdowns
- **Sports**: Scoreboards, schedules, statistics
- **Weather**: Forecasts, current conditions
- **Finance**: Stocks, crypto, market data
- **Entertainment**: Games, animations, media
- **Example**: Tutorial and learning plugins
- **Custom**: Unique displays

## Support

- **Issues**: Report problems in the [main LEDMatrix repo](https://github.com/ChuckBuilds/LEDMatrix/issues)
- **Discussions**: Ask questions in [Discussions](https://github.com/ChuckBuilds/LEDMatrix/discussions)
- **Documentation**: See the [Wiki](https://github.com/ChuckBuilds/LEDMatrix/wiki)

## License

This registry is licensed under GPL-3.0. Individual plugins may have their own licenses.
````

### 3. SUBMISSION.md (New file)

````markdown
# Plugin Submission Guidelines

Thank you for contributing to the LEDMatrix plugin ecosystem!

## Before You Submit

Ensure your plugin meets these requirements:

### Required Files
- ✅ `manifest.json` - Complete plugin metadata
- ✅ `manager.py` - Plugin implementation
- ✅ `README.md` - Documentation
- ✅ `config_schema.json` - Configuration validation (recommended)
- ✅ `requirements.txt` - Python dependencies (if any)

### Code Quality
- ✅ Extends `BasePlugin` class
- ✅ Implements `update()` and `display()` methods
- ✅ Proper error handling with try/except
- ✅ Uses logging (not print statements)
- ✅ No hardcoded API keys or secrets
- ✅ Follows PEP 8 style guidelines
- ✅ Has type hints on function parameters

### Testing
- ✅ Tested on Raspberry Pi with LED matrix
- ✅ Works with 64x32 minimum display size
- ✅ No crashes or hanging
- ✅ Reasonable CPU/memory usage

### Documentation
- ✅ Clear README with installation instructions
- ✅ Configuration options explained
- ✅ Example configuration provided
- ✅ License specified

## Submission Process

### 1. Test Your Plugin

Install via URL to verify it works:

```bash
curl -X POST http://your-pi:5050/api/plugins/install-from-url \
  -H "Content-Type: application/json" \
  -d '{"repo_url": "https://github.com/yourusername/ledmatrix-your-plugin"}'
```

### 2. Create a GitHub Release

```bash
git tag v1.0.0
git push origin v1.0.0
```

Create a release on GitHub with:
- Version number (semver: v1.0.0)
- Release notes / changelog
- Screenshots (if applicable)

### 3. Fork This Repository

Click "Fork" on [ledmatrix-plugins](https://github.com/ChuckBuilds/ledmatrix-plugins)

### 4. Add Your Plugin Entry

Edit `plugins.json` and add your plugin:

```json
{
  "id": "your-plugin",
  "name": "Your Plugin Name",
  "description": "Clear description of what your plugin does",
  "author": "YourGitHubUsername",
  "category": "custom",
  "tags": ["tag1", "tag2", "tag3"],
  "repo": "https://github.com/yourusername/ledmatrix-your-plugin",
  "branch": "main",
  "versions": [
    {
      "version": "1.0.0",
      "ledmatrix_min": "2.0.0",
      "released": "2025-01-09",
      "download_url": "https://github.com/yourusername/ledmatrix-your-plugin/archive/refs/tags/v1.0.0.zip",
      "changelog": "Initial release"
    }
  ],
  "stars": 0,
  "downloads": 0,
  "last_updated": "2025-01-09",
  "verified": false,
  "documentation": "https://github.com/yourusername/ledmatrix-your-plugin#readme"
}
```

**Important**: Add to the `plugins` array, maintaining JSON syntax!

### 5. Submit Pull Request

1. Commit your changes:
   ```bash
   git add plugins.json
   git commit -m "Add plugin: your-plugin-name"
   git push
   ```

2. Create Pull Request with:
   - Title: `Add plugin: your-plugin-name`
   - Description: Brief overview and what it does
   - Link to your plugin repo
   - Screenshots (if applicable)

## Review Process

1. **Automated Validation** (~5 min)
   - JSON syntax check
   - Required fields validation
   - Version format check

2. **Code Review** (1-3 days)
   - Manual review of plugin code
   - Security check
   - Best practices verification

3. **Testing** (1-2 days)
   - Install test on Raspberry Pi
   - Functionality test
   - Performance check

4. **Approval** (when ready)
   - PR merged
   - `verified: true` set
   - Announced in releases

## After Approval

- ✅ Plugin appears in official Plugin Store
- ✅ Shows "✓ Verified" badge
- ✅ Listed in README
- ✅ Stats tracked (downloads, stars)

## Updating Your Plugin

To release a new version:

1. Make changes in your plugin repo
2. Create new release tag (`v1.1.0`)
3. Fork ledmatrix-plugins again
4. Add new version to `versions` array (keep old ones):
   ```json
   "versions": [
     {
       "version": "1.1.0",
       "ledmatrix_min": "2.0.0",
       "released": "2025-01-15",
       "download_url": "https://github.com/you/plugin/archive/refs/tags/v1.1.0.zip",
       "changelog": "Added feature X, fixed bug Y"
     },
     {
       "version": "1.0.0",
       ...
     }
   ]
   ```
5. Submit PR: `Update plugin: your-plugin-name to v1.1.0`

## Categories

Choose the most appropriate category:

- `time` - Clocks, timers, countdowns
- `sports` - Scoreboards, schedules, stats
- `weather` - Forecasts, conditions
- `finance` - Stocks, crypto, markets
- `entertainment` - Games, animations, media
- `custom` - Unique/miscellaneous

## Tags

Add 2-5 descriptive tags (lowercase):
- Good: `["nhl", "hockey", "scoreboard"]`
- Bad: `["NHL", "Plugin", "Cool"]`

## Common Rejection Reasons

- ❌ Missing or invalid manifest.json
- ❌ Hardcoded API keys or secrets
- ❌ No error handling
- ❌ Crashes on test
- ❌ Poor documentation
- ❌ Copyright/licensing issues
- ❌ Malicious code

## Need Help?

- Open an issue in this repo
- Ask in [LEDMatrix Discussions](https://github.com/ChuckBuilds/LEDMatrix/discussions)
- Check the [Plugin Architecture Spec](https://github.com/ChuckBuilds/LEDMatrix/blob/main/PLUGIN_ARCHITECTURE_SPEC.md)

## Example Plugins

Study these approved plugins:
- [Hello World](https://github.com/ChuckBuilds/LEDMatrix/tree/main/plugins/hello-world) - Minimal example
- [Simple Clock](https://github.com/ChuckBuilds/LEDMatrix/tree/main/plugins/clock-simple) - Time display

Thank you for contributing! 🎉
````

## Quick Setup Commands

Run these commands to set up your repo:

```bash
# Clone your ledmatrix-plugins repo
git clone https://github.com/ChuckBuilds/ledmatrix-plugins.git
cd ledmatrix-plugins

# Copy the template registry file from your LEDMatrix repo
cp ../LEDMatrix/plugin_registry_template.json plugins.json

# Create SUBMISSION.md (copy content from above)
# Create/update README.md (copy content from above)

# Add and commit
git add plugins.json SUBMISSION.md README.md
git commit -m "Set up plugin registry with initial plugins"
git push origin main
```

## Verify It Works

After pushing to GitHub, test the registry:

```bash
# Should return your plugins.json
curl https://raw.githubusercontent.com/ChuckBuilds/ledmatrix-plugins/main/plugins.json

# Test in LEDMatrix
python3 -c "
from src.plugin_system.store_manager import PluginStoreManager
store = PluginStoreManager()
registry = store.fetch_registry()
print(f'✓ Found {len(registry.get(\"plugins\", []))} plugins')
for plugin in registry.get('plugins', []):
    print(f'  - {plugin[\"name\"]} ({plugin[\"id\"]})')
"
```

## What Happens Next

1. Users can browse plugins in the web interface
2. They can install with one click
3. Community can submit via PR
4. You review and approve submissions
5. Plugin ecosystem grows!

## Note on Existing Plugins

Your current plugins (`hello-world`, `clock-simple`) are in the main LEDMatrix repo. You can:

**Option A**: Keep them there and reference in registry (already done in template)  
**Option B**: Move to separate repos for cleaner separation

The template I provided uses **Option A** for simplicity. Both work fine!

## Done! 🎉

Your plugin store is now ready! Users can install plugins, and developers can submit new ones.

