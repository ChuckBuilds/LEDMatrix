# LEDMatrix Plugin Development Guide

This guide explains how to set up a development workflow for plugins that are maintained in separate Git repositories while still being able to test them within the LEDMatrix project.

> **Rendering guidance:** plugins should read the display size dynamically
> (`self.display_manager.width/height`) rather than hardcoding one
> panel. Don't read `display_manager.matrix.width/height`: `matrix` is
> `None` when hardware init fails, while the `width`/`height` properties
> fall back to the canvas size. For plugins that want to *scale* their layout to any panel, the
> opt-in adaptive layout system ([ADAPTIVE_LAYOUT.md](ADAPTIVE_LAYOUT.md))
> provides the shared helpers — fonts, images, and composite layouts that
> scale. Existing plugins keep their classic rendering unless they adopt
> those APIs; nothing migrates automatically.

> **Want a different look for an existing sports scoreboard?** Skins are
> meant for that, but they are **not supported yet**: the current scoreboard
> plugins don't render them (see [SKIN_SYSTEM.md](SKIN_SYSTEM.md#status-not-supported-yet)).
> For now, change the look through the plugin's own display settings or its
> code.

## Overview

When developing plugins in separate repositories, you need a way to:
- Test plugins within the LEDMatrix project
- Make changes and commit them back to the plugin repository
- Avoid git conflicts between LEDMatrix and plugin repositories
- Easily switch between development and production modes

The solution uses **symbolic links** to connect plugin repositories to the `plugins/` directory, combined with a helper script to manage the linking process.

> **Plugin directory note:** the dev workflow described here puts
> symlinks in `plugins/`. The plugin loader's *production* default is
> `plugin-repos/` (set by `plugin_system.plugins_directory` in
> `config.json`). Importantly, the main discovery path
> (`PluginManager.discover_plugins()`) only scans the configured
> directory — it does **not** fall back to `plugins/`. Two narrower
> paths do: the Plugin Store install/update logic in `store_manager.py`,
> and `schema_manager.get_schema_path()` (which the web UI form
> generator uses to find `config_schema.json`). That's why plugins
> installed via the Plugin Store still work even with symlinks in
> `plugins/`, but your own dev plugin won't appear in the rotation
> until you either move it to `plugin-repos/` or change
> `plugin_system.plugins_directory` to `plugins` in the General tab
> of the web UI. The latter is the smoother dev setup.

## Quick Start

Official plugins all live in one repository,
[ledmatrix-plugins](https://github.com/ChuckBuilds/ledmatrix-plugins), with
one directory per plugin under `plugins/` (there are no per-plugin
`ledmatrix-<name>` repositories). The helper script links a plugin directory
from a checkout of that monorepo into LEDMatrix's `plugins/` directory.

### 1. Link an Official Plugin

```bash
./scripts/dev/dev_plugin_setup.sh link-github football-scoreboard
```

This will:
- Clone `https://github.com/ChuckBuilds/ledmatrix-plugins.git` to
  `~/.ledmatrix-dev-plugins/ledmatrix-plugins` (or `git pull` it if it is
  already there)
- Find `plugins/football-scoreboard` in it (also accepted:
  `plugins/ledmatrix-<name>`, or a plugin whose manifest `id` is the name)
- Validate that it has a `manifest.json`
- Create a symbolic link named after the plugin's manifest id, e.g.
  `plugins/football-scoreboard` → `~/.ledmatrix-dev-plugins/ledmatrix-plugins/plugins/football-scoreboard`

`link-github music` finds the monorepo's `plugins/ledmatrix-music` directory
and links it into LEDMatrix as `plugins/ledmatrix-music`, because
`ledmatrix-music` is that plugin's manifest id.

To work from your fork of the monorepo, set `github_user` in
`dev_plugins.json` (see [Configuration](#configuration)).

### 2. Link a Local Plugin Directory

If you already have the monorepo (or a third-party plugin repository) cloned
locally:

```bash
./scripts/dev/dev_plugin_setup.sh link hello-world ../ledmatrix-plugins/plugins/hello-world
```

This creates a symlink from `plugins/hello-world` to that directory.

### 3. Check Status

See which plugins are linked and their git status:

```bash
./scripts/dev/dev_plugin_setup.sh status
```

### 4. Work on Your Plugin

```bash
cd plugins/football-scoreboard  # Actually editing the monorepo checkout
# Make your changes, then bump "version" in manifest.json
git add .
git commit -m "feat(football-scoreboard): add new feature"
git push   # to your fork, then open a PR against ledmatrix-plugins
```

In the monorepo, every plugin change must bump `version` in the plugin's
`manifest.json` and run `python update_registry.py`, or users won't receive
the update.

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
./scripts/dev/dev_plugin_setup.sh link football-scoreboard ../ledmatrix-plugins/plugins/football-scoreboard
```

**Notes:**
- The script validates that the repository contains a `manifest.json` file
- If a plugin directory already exists, you'll be prompted to replace it
- The repository path can be absolute or relative

### `link-github <plugin-name> [repo-url]`

Clones a plugin from GitHub and links it.

**Arguments:**
- `plugin-name`: Without `repo-url`, the plugin to link from the monorepo: a
  directory under `plugins/` (`<name>` or `ledmatrix-<name>`) or a manifest
  id. The link is named after the plugin's manifest id. With `repo-url`, the
  name of the link in `plugins/`.
- `repo-url`: (Optional) A plugin that has its own repository (e.g. a
  third-party plugin). The repository root is linked.

**Examples:**
```bash
# Official plugin, from the ledmatrix-plugins monorepo
./scripts/dev/dev_plugin_setup.sh link-github stocks

# Third-party plugin with its own repository
./scripts/dev/dev_plugin_setup.sh link-github custom-plugin https://github.com/OtherUser/custom-plugin.git
```

**Notes:**
- Repositories are cloned to `~/.ledmatrix-dev-plugins/` by default (configurable)
- The monorepo is cloned once and shared by every plugin you link from it
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

By default, GitHub repositories are cloned to `~/.ledmatrix-dev-plugins/`
and official plugins come from `ChuckBuilds/ledmatrix-plugins`. To change
either, copy `dev_plugins.json.example` (in the LEDMatrix root) to
`dev_plugins.json` and edit it. `dev_plugins.json` is git-ignored.

```json
{
  "dev_plugins_dir": "~/.ledmatrix-dev-plugins",
  "github_user": "your-github-user",
  "plugins_repo": "ledmatrix-plugins",
  "plugins_branch": "main"
}
```

**Configuration options** (all optional):
- `dev_plugins_dir`: Where to clone GitHub repositories (default: `~/.ledmatrix-dev-plugins`)
- `github_user`: Owner of the plugin monorepo that `link-github <name>` clones — set it to use your fork (default: `ChuckBuilds`)
- `plugins_repo`: Name of that monorepo (default: `ledmatrix-plugins`)
- `plugins_branch`: Branch to clone it at (default: the repository's default branch). Only applies when the clone is first made.

`github_pattern` from older versions of this guide is no longer used (the
script warns if it is set).

## Development Workflow

### Typical Development Session

1. **Link your plugin for development:**
   ```bash
   ./scripts/dev/dev_plugin_setup.sh link-github clock-simple
   ```

2. **Test in LEDMatrix:**
   ```bash
   # Run LEDMatrix with your plugin (emulator shown)
   python3 run.py -e
   ```

3. **Make changes:**
   ```bash
   cd plugins/clock-simple
   # Edit files...
   # Test changes...
   ```

4. **Commit to the plugin repository:**
   ```bash
   cd plugins/clock-simple  # This is inside your monorepo checkout
   # bump "version" in manifest.json, then from the monorepo root:
   # python update_registry.py
   git add .
   git commit -m "feat(clock-simple): add new feature"
   git push
   ```

5. **Update from remote (if needed):**
   ```bash
   ./scripts/dev/dev_plugin_setup.sh update clock-simple
   ```

6. **When done developing:**
   ```bash
   ./scripts/dev/dev_plugin_setup.sh unlink clock-simple
   ```

### Working with Multiple Plugins

You can have multiple plugins linked simultaneously. Plugins linked from the
monorepo share one checkout:

```bash
./scripts/dev/dev_plugin_setup.sh link-github music
./scripts/dev/dev_plugin_setup.sh link-github stocks
./scripts/dev/dev_plugin_setup.sh link-github football-scoreboard

# Check status of all
./scripts/dev/dev_plugin_setup.sh status

# Update all at once (the shared monorepo checkout is pulled once)
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
   cd ~/.ledmatrix-dev-plugins/ledmatrix-plugins
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

- **Plugin Store:** Installs plugins as regular directories in the configured
  plugins directory (`plugin-repos/` by default)
- **Development Setup:** Links plugin directories as symlinks in `plugins/`

The plugin loader scans only one directory, so while developing set
`plugin_system.plugins_directory` to `plugins` (see the note at the top of
this guide). If `plugins/` already holds a regular directory of the same
name, `link`/`link-github` offers to rename it to
`<name>.backup.<timestamp>` before linking.

`unlink` removes only the symlink. To switch back to the store version, set
`plugins_directory` back to `plugin-repos` (or reinstall the plugin from the
store).

## API Reference

When developing plugins, you'll need to use the APIs provided by the LEDMatrix system:

- **[Plugin API Reference](PLUGIN_API_REFERENCE.md)** - Complete reference for Display Manager, Cache Manager, and Plugin Manager methods
- **[Advanced Plugin Development](ADVANCED_PLUGIN_DEVELOPMENT.md)** - Advanced patterns, examples, and best practices
- **[Developer Quick Reference](DEVELOPER_QUICK_REFERENCE.md)** - Quick reference for common developer tasks

### Key APIs for Plugin Developers

**Display Manager** (`self.display_manager`):
- `clear()`, `update_display()` - Core display operations
- `draw_text()` - Text rendering. For images, paste directly onto
  `display_manager.image` (a PIL Image) and call `update_display()`;
  there is no `draw_image()` helper method.
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
   - Use the [Hello World plugin](https://github.com/ChuckBuilds/ledmatrix-plugins/tree/main/plugins/hello-world) as a starting point
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
- **Bump `version` in `manifest.json` by hand** for every change you ship.
  There is no automatic version-bump hook or bump script.
- **Official (monorepo) plugins**: after bumping the manifest, run
  `python update_registry.py` in the `ledmatrix-plugins` checkout. It copies
  each manifest's version into `plugins.json` as `latest_version`, which is
  what the store compares installed versions against. Without it, users
  won't be offered the update.
- **Plugins in their own repository**: still bump the manifest `version`,
  so users can see which version they run; tagging releases (`v1.2.3`) to
  match is a good habit.

### Submitting to Official Registry

To have your plugin added to the official plugin store:

1. **Ensure quality**:
   - Plugin works reliably
   - Well-documented (README.md)
   - Follows best practices
   - Tested on Raspberry Pi hardware

2. **Choose where it lives** (see `SUBMISSION.md` in
   [ledmatrix-plugins](https://github.com/ChuckBuilds/ledmatrix-plugins)):
   - **In the monorepo (preferred):** fork ledmatrix-plugins, add
     `plugins/<your-plugin-id>/`, and open a pull request
   - **In your own public repository** (conventionally
     `ledmatrix-<plugin-name>`), with a README that covers installation

3. **Contact maintainers** (own-repository plugins):
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
- [Plugin Store Guide](PLUGIN_STORE_GUIDE.md) - Using the plugin store

