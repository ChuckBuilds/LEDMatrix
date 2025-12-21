# LEDMatrix Plugin Development Guide

This guide explains how to set up a development workflow for plugins that are maintained in separate Git repositories while still being able to test them within the LEDMatrix project.

## Overview

When developing plugins in separate repositories, you need a way to:
- Test plugins within the LEDMatrix project
- Make changes and commit them back to the plugin repository
- Avoid git conflicts between LEDMatrix and plugin repositories
- Easily switch between development and production modes

The solution uses **symbolic links** to connect plugin repositories to the `plugins/` directory, combined with a helper script to manage the linking process.

## Quick Start

### 1. Link a Plugin from GitHub

The easiest way to link a plugin that's already on GitHub:

```bash
./scripts/dev/dev_plugin_setup.sh link-github music
```

This will:
- Clone `https://github.com/ChuckBuilds/ledmatrix-music.git` to `~/.ledmatrix-dev-plugins/ledmatrix-music`
- Create a symbolic link from `plugins/music` to the cloned repository
- Validate that the plugin has a proper `manifest.json`

### 2. Link a Local Plugin Repository

If you already have a plugin repository cloned locally:

```bash
./scripts/dev/dev_plugin_setup.sh link music ../ledmatrix-music
```

This creates a symlink from `plugins/music` to your local repository path.

### 3. Check Status

See which plugins are linked and their git status:

```bash
./scripts/dev/dev_plugin_setup.sh status
```

### 4. Work on Your Plugin

```bash
cd plugins/music  # Actually editing the linked repository
# Make your changes
git add .
git commit -m "feat: add new feature"
git push origin main
```

### 5. Update Plugins

Pull latest changes from remote:

```bash
# Update all linked plugins
./scripts/dev/dev_plugin_setup.sh update

# Or update a specific plugin
./scripts/dev/dev_plugin_setup.sh update music
```

### 6. Unlink When Done

Remove the symlink (repository is preserved):

```bash
./scripts/dev/dev_plugin_setup.sh unlink music
```

## Detailed Commands

### `link <plugin-name> <repo-path>`

Links a local plugin repository to the plugins directory.

**Arguments:**
- `plugin-name`: The name of the plugin (will be the directory name in `plugins/`)
- `repo-path`: Path to the plugin repository (absolute or relative)

**Example:**
```bash
./scripts/dev/dev_plugin_setup.sh link football-scoreboard ../ledmatrix-football-scoreboard
```

**Notes:**
- The script validates that the repository contains a `manifest.json` file
- If a plugin directory already exists, you'll be prompted to replace it
- The repository path can be absolute or relative

### `link-github <plugin-name> [repo-url]`

Clones a plugin from GitHub and links it.

**Arguments:**
- `plugin-name`: The name of the plugin (will be the directory name in `plugins/`)
- `repo-url`: (Optional) Full GitHub repository URL. If omitted, constructs from pattern: `https://github.com/ChuckBuilds/ledmatrix-<plugin-name>.git`

**Examples:**
```bash
# Auto-construct URL from plugin name
./scripts/dev/dev_plugin_setup.sh link-github music

# Use explicit URL
./scripts/dev/dev_plugin_setup.sh link-github stocks https://github.com/ChuckBuilds/ledmatrix-stocks.git

# Link from a different GitHub user
./scripts/dev/dev_plugin_setup.sh link-github custom-plugin https://github.com/OtherUser/custom-plugin.git
```

**Notes:**
- Repositories are cloned to `~/.ledmatrix-dev-plugins/` by default (configurable)
- If the repository already exists, it will be updated with `git pull` instead of re-cloning
- The cloned repository is preserved when you unlink the plugin

### `unlink <plugin-name>`

Removes the symlink for a plugin.

**Arguments:**
- `plugin-name`: The name of the plugin to unlink

**Example:**
```bash
./scripts/dev/dev_plugin_setup.sh unlink music
```

**Notes:**
- Only removes the symlink, does NOT delete the repository
- Your work and git history are preserved in the repository location

### `list`

Lists all plugins in the `plugins/` directory and shows their status.

**Example:**
```bash
./scripts/dev/dev_plugin_setup.sh list
```

**Output:**
- ✓ Green checkmark: Plugin is symlinked (development mode)
- ○ Yellow circle: Plugin is a regular directory (production/installed mode)
- Shows the source path for symlinked plugins
- Shows git status (branch, clean/dirty) for linked repos

### `status`

Shows detailed status of all linked plugins.

**Example:**
```bash
./scripts/dev/dev_plugin_setup.sh status
```

**Shows:**
- Link status (working/broken)
- Repository path
- Git branch
- Remote URL
- Git status (clean, uncommitted changes, ahead/behind remote)
- Summary of all plugins

### `update [plugin-name]`

Updates plugin(s) by running `git pull` in their repositories.

**Arguments:**
- `plugin-name`: (Optional) Specific plugin to update. If omitted, updates all linked plugins.

**Examples:**
```bash
# Update all linked plugins
./scripts/dev/dev_plugin_setup.sh update

# Update specific plugin
./scripts/dev/dev_plugin_setup.sh update music
```

## Configuration

### Custom Development Directory

By default, GitHub repositories are cloned to `~/.ledmatrix-dev-plugins/`. You can customize this by creating a `dev_plugins.json` file:

```json
{
  "dev_plugins_dir": "/path/to/your/dev/plugins",
  "github_user": "ChuckBuilds",
  "github_pattern": "ledmatrix-",
  "plugins": {
    "music": {
      "source": "github",
      "url": "https://github.com/ChuckBuilds/ledmatrix-music.git",
      "branch": "main"
    }
  }
}
```

**Configuration options:**
- `dev_plugins_dir`: Where to clone GitHub repositories (default: `~/.ledmatrix-dev-plugins`)
- `github_user`: Default GitHub username for auto-constructing URLs
- `github_pattern`: Pattern for repository names (default: `ledmatrix-`)
- `plugins`: Plugin definitions (optional, for future auto-discovery features)

**Note:** Copy `dev_plugins.json.example` to `dev_plugins.json` and customize it. The `dev_plugins.json` file is git-ignored.

## Development Workflow

### Typical Development Session

1. **Link your plugin for development:**
   ```bash
   ./scripts/dev/dev_plugin_setup.sh link-github music
   ```

2. **Test in LEDMatrix:**
   ```bash
   # Run LEDMatrix with your plugin
   python run.py
   ```

3. **Make changes:**
   ```bash
   cd plugins/music
   # Edit files...
   # Test changes...
   ```

4. **Commit to plugin repository:**
   ```bash
   cd plugins/music  # This is actually your repo
   git add .
   git commit -m "feat: add new feature"
   git push origin main
   ```

5. **Update from remote (if needed):**
   ```bash
   ./scripts/dev/dev_plugin_setup.sh update music
   ```

6. **When done developing:**
   ```bash
   ./scripts/dev/dev_plugin_setup.sh unlink music
   ```

### Working with Multiple Plugins

You can have multiple plugins linked simultaneously:

```bash
./scripts/dev/dev_plugin_setup.sh link-github music
./scripts/dev/dev_plugin_setup.sh link-github stocks
./scripts/dev/dev_plugin_setup.sh link-github football-scoreboard

# Check status of all
./scripts/dev/dev_plugin_setup.sh status

# Update all at once
./scripts/dev/dev_plugin_setup.sh update
```

### Switching Between Development and Production

**Development mode:** Plugins are symlinked to your repositories
- Edit files directly in `plugins/<name>`
- Changes are in the plugin repository
- Git operations work normally

**Production mode:** Plugins are installed normally
- Plugins are regular directories (installed via plugin store or manually)
- Can't edit directly (would need to edit in place or re-install)
- Use `unlink` to remove symlink if you want to switch back to installed version

## Best Practices

### 1. Keep Repositories Outside LEDMatrix

The script clones GitHub repositories to `~/.ledmatrix-dev-plugins/` by default, which is outside the LEDMatrix directory. This:
- Avoids git conflicts
- Keeps plugin repos separate from LEDMatrix repo
- Makes it easy to manage multiple plugin repositories

### 2. Use Descriptive Commit Messages

When committing changes in your plugin repository, use clear commit messages following the project's conventions:

```bash
git commit -m "feat(music): add album art support"
git commit -m "fix(stocks): resolve API timeout issue"
```

### 3. Test Before Committing

Always test your plugin changes in LEDMatrix before committing:

```bash
# Make changes
cd plugins/music
# ... edit files ...

# Test in LEDMatrix
cd ../..
python run.py

# If working, commit
cd plugins/music
git add .
git commit -m "feat: new feature"
```

### 4. Keep Plugins Updated

Regularly update your linked plugins to get the latest changes:

```bash
./scripts/dev/dev_plugin_setup.sh update
```

### 5. Check Status Regularly

Before starting work, check the status of your linked plugins:

```bash
./scripts/dev/dev_plugin_setup.sh status
```

This helps you:
- See if you have uncommitted changes
- Check if you're behind the remote
- Identify any broken symlinks

## Troubleshooting

### Plugin Not Discovered by LEDMatrix

If LEDMatrix doesn't discover your linked plugin:

1. **Check the symlink exists:**
   ```bash
   ls -la plugins/your-plugin-name
   ```

2. **Verify manifest.json exists:**
   ```bash
   ls plugins/your-plugin-name/manifest.json
   ```

3. **Check PluginManager logs:**
   - LEDMatrix logs should show plugin discovery
   - Look for errors related to the plugin

### Broken Symlink

If a symlink is broken (target repository was moved or deleted):

1. **Check status:**
   ```bash
   ./scripts/dev/dev_plugin_setup.sh status
   ```

2. **Unlink and re-link:**
   ```bash
   ./scripts/dev/dev_plugin_setup.sh unlink plugin-name
   ./scripts/dev/dev_plugin_setup.sh link-github plugin-name
   ```

### Git Conflicts

If you have conflicts when updating:

1. **Manually resolve in the plugin repository:**
   ```bash
   cd ~/.ledmatrix-dev-plugins/ledmatrix-music
   git pull
   # Resolve conflicts...
   git add .
   git commit
   ```

2. **Or use the update command:**
   ```bash
   ./scripts/dev/dev_plugin_setup.sh update music
   ```

### Plugin Directory Already Exists

If you try to link a plugin but the directory already exists:

1. **Check if it's already linked:**
   ```bash
   ./scripts/dev/dev_plugin_setup.sh list
   ```

2. **If it's a symlink to the same location, you're done**

3. **If it's a regular directory or different symlink:**
   - The script will prompt you to replace it
   - Or manually backup: `mv plugins/plugin-name plugins/plugin-name.backup`

## Advanced Usage

### Linking Plugins from Different GitHub Users

```bash
./scripts/dev/dev_plugin_setup.sh link-github custom-plugin https://github.com/OtherUser/custom-plugin.git
```

### Using a Custom Development Directory

Create `dev_plugins.json`:

```json
{
  "dev_plugins_dir": "/home/user/my-dev-plugins"
}
```

### Combining Local and GitHub Plugins

You can mix local and GitHub plugins:

```bash
# Link from GitHub
./scripts/dev/dev_plugin_setup.sh link-github music

# Link local repository
./scripts/dev/dev_plugin_setup.sh link custom-plugin ../my-custom-plugin
```

## Integration with Plugin Store

The development workflow is separate from the plugin store installation:

- **Plugin Store:** Installs plugins to `plugins/` as regular directories
- **Development Setup:** Links plugin repositories as symlinks

If you install a plugin via the store, you can still link it for development:

```bash
# Store installs to plugins/music (regular directory)
# Link for development (will prompt to replace)
./scripts/dev/dev_plugin_setup.sh link-github music
```

When you unlink, the directory is removed. If you want to switch back to the store version, re-install it via the plugin store.

## API Reference

When developing plugins, you'll need to use the APIs provided by the LEDMatrix system:

- **[Plugin API Reference](PLUGIN_API_REFERENCE.md)** - Complete reference for Display Manager, Cache Manager, and Plugin Manager methods
- **[Advanced Plugin Development](ADVANCED_PLUGIN_DEVELOPMENT.md)** - Advanced patterns, examples, and best practices
- **[Developer Quick Reference](DEVELOPER_QUICK_REFERENCE.md)** - Quick reference for common developer tasks

### Key APIs for Plugin Developers

**Display Manager** (`self.display_manager`):
- `clear()`, `update_display()` - Core display operations
- `draw_text()`, `draw_image()` - Rendering methods
- `draw_weather_icon()`, `draw_sun()`, `draw_cloud()` - Weather icons
- `get_text_width()`, `get_font_height()` - Text utilities
- `set_scrolling_state()`, `defer_update()` - Scrolling state management

**Cache Manager** (`self.cache_manager`):
- `get()`, `set()`, `delete()` - Basic caching
- `get_cached_data_with_strategy()` - Advanced caching with strategies
- `get_background_cached_data()` - Background service caching

**Plugin Manager** (`self.plugin_manager`):
- `get_plugin()`, `get_all_plugins()` - Access other plugins
- `get_plugin_info()` - Get plugin information

See [PLUGIN_API_REFERENCE.md](PLUGIN_API_REFERENCE.md) for complete documentation.

## 3rd Party Plugin Development

Want to create and share your own plugin? Here's everything you need to know.

### Getting Started

1. **Review the documentation**:
   - [Plugin Architecture Spec](PLUGIN_ARCHITECTURE_SPEC.md) - System architecture
   - [Plugin API Reference](PLUGIN_API_REFERENCE.md) - Available methods
   - [Advanced Plugin Development](ADVANCED_PLUGIN_DEVELOPMENT.md) - Patterns and examples

2. **Start with a template**:
   - Use the [Hello World plugin](https://github.com/ChuckBuilds/ledmatrix-hello-world) as a starting point
   - Or fork an existing plugin and modify it

3. **Follow the plugin structure**:
   ```
   your-plugin/
   ├── manifest.json          # Required: Plugin metadata
   ├── manager.py             # Required: Plugin class
   ├── config_schema.json     # Recommended: Configuration schema
   ├── requirements.txt       # Optional: Python dependencies
   └── README.md              # Recommended: User documentation
   ```

### Plugin Requirements

Your plugin must:

1. **Inherit from BasePlugin**:
   ```python
   from src.plugin_system.base_plugin import BasePlugin
   
   class MyPlugin(BasePlugin):
       def update(self):
           # Fetch data
           pass
       
       def display(self, force_clear=False):
           # Render display
           pass
   ```

2. **Include manifest.json** with required fields:
   ```json
   {
     "id": "my-plugin",
     "name": "My Plugin",
     "version": "1.0.0",
     "class_name": "MyPlugin",
     "entry_point": "manager.py",
     "display_modes": ["my_plugin"],
     "compatible_versions": [">=2.0.0"]
   }
   ```

3. **Match class name**: The class name in `manager.py` must match `class_name` in manifest

### Testing Your Plugin

1. **Test locally**:
   ```bash
   # Link your plugin for development
   ./scripts/dev/dev_plugin_setup.sh link your-plugin /path/to/your-plugin
   
   # Run LEDMatrix with emulator
   python run.py --emulator
   ```

2. **Test on hardware**: Deploy to Raspberry Pi and test on actual LED matrix

3. **Use mocks for unit testing**: See [Advanced Plugin Development](ADVANCED_PLUGIN_DEVELOPMENT.md#testing-plugins-with-mocks)

### Versioning Best Practices

- **Use semantic versioning**: `MAJOR.MINOR.PATCH` (e.g., `1.2.3`)
- **Automatic version bumping**: Use the pre-push git hook for automatic patch version bumps
- **Manual versioning**: Only needed for major/minor bumps or special cases
- **GitHub as source of truth**: Plugin store fetches versions from GitHub releases/tags/manifest

See the [Git Workflow rules](../.cursorrules) for version management details.

### Submitting to Official Registry

To have your plugin added to the official plugin store:

1. **Ensure quality**:
   - Plugin works reliably
   - Well-documented (README.md)
   - Follows best practices
   - Tested on Raspberry Pi hardware

2. **Create GitHub repository**:
   - Repository name: `ledmatrix-<plugin-name>`
   - Public repository
   - Proper README.md with installation instructions

3. **Contact maintainers**:
   - Open a GitHub issue in the [ledmatrix-plugins](https://github.com/ChuckBuilds/ledmatrix-plugins) repository
   - Or reach out on Discord: https://discord.gg/uW36dVAtcT
   - Include: Repository URL, plugin description, why it's useful

4. **Review process**:
   - Code review for quality and security
   - Testing on Raspberry Pi hardware
   - Documentation review
   - If approved, added to official registry

### Plugin Store Integration Requirements

For your plugin to work well in the plugin store:

- **GitHub repository**: Must be publicly accessible on GitHub
- **Releases or tags**: Recommended for version tracking
- **README.md**: Clear installation and configuration instructions
- **config_schema.json**: Recommended for web UI configuration
- **manifest.json**: Required with all required fields
- **requirements.txt**: If your plugin has Python dependencies

### Distribution Options

1. **Official Registry** (Recommended):
   - Listed in default plugin store
   - Automatic updates
   - Verified badge
   - Requires approval

2. **Custom Repository**:
   - Host your own plugin repository
   - Users can install via "Install from GitHub" in web UI
   - Full control over distribution

3. **Direct Installation**:
   - Users can clone and install manually
   - Good for development/testing

### Best Practices for 3rd Party Plugins

1. **Documentation**: Include comprehensive README.md
2. **Configuration**: Provide config_schema.json for web UI
3. **Error handling**: Graceful failures with clear error messages
4. **Logging**: Use plugin logger for debugging
5. **Testing**: Test on actual Raspberry Pi hardware
6. **Versioning**: Follow semantic versioning
7. **Dependencies**: Minimize external dependencies
8. **Performance**: Optimize for Pi's limited resources

## See Also

- [Plugin Architecture Specification](PLUGIN_ARCHITECTURE_SPEC.md) - Complete system specification
- [Plugin API Reference](PLUGIN_API_REFERENCE.md) - Complete API documentation
- [Advanced Plugin Development](ADVANCED_PLUGIN_DEVELOPMENT.md) - Advanced patterns and examples
- [Plugin Quick Reference](PLUGIN_QUICK_REFERENCE.md) - Quick development reference
- [Plugin Configuration Guide](PLUGIN_CONFIGURATION_GUIDE.md) - Configuration setup
- [Plugin Store User Guide](PLUGIN_STORE_USER_GUIDE.md) - Using the plugin store

