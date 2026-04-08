# Cursor Helper Files for LEDMatrix Plugin Development

This directory contains Cursor-specific helper files to assist with plugin development in the LEDMatrix project.

## Files Overview

### `.cursorrules`
Comprehensive rules file that Cursor uses to understand plugin development patterns, best practices, and workflows. This file is automatically loaded by Cursor and helps guide AI-assisted development.

### `plugins_guide.md`
Detailed guide covering:
- Plugin system overview
- Creating new plugins
- Running plugins (emulator and hardware)
- Loading and configuring plugins
- Development workflow
- Testing strategies
- Troubleshooting

### `plugin_templates/`
Template files for quick plugin creation:
- `manifest.json.template` - Plugin metadata template
- `manager.py.template` - Plugin class template
- `config_schema.json.template` - Configuration schema template
- `README.md.template` - Plugin documentation template
- `requirements.txt.template` - Dependencies template
- `QUICK_START.md` - Quick start guide for using templates

## Quick Reference

### Creating a New Plugin

1. **Using templates** (recommended):
```bash
# See QUICK_START.md in plugin_templates/
cd plugins
mkdir my-plugin
cd my-plugin
cp ../../.cursor/plugin_templates/*.template .
# Edit files, replacing PLUGIN_ID and other placeholders
```

2. **Using dev_plugin_setup.sh**:
```bash
# Link from GitHub
./scripts/dev/dev_plugin_setup.sh link-github my-plugin

# Link local repo
./scripts/dev/dev_plugin_setup.sh link my-plugin /path/to/repo
```

### Running the Display

```bash
# Emulator mode (development, no hardware required)
python3 run.py --emulator
# (equivalent: EMULATOR=true python3 run.py)

# Hardware (production, requires the rpi-rgb-led-matrix submodule built)
python3 run.py

# As a systemd service
sudo systemctl start ledmatrix

# Dev preview server (renders plugins to a browser without running run.py)
python3 scripts/dev_server.py  # then open http://localhost:5001
```

The `-e`/`--emulator` CLI flag is defined in `run.py:19-20` and
sets `os.environ["EMULATOR"] = "true"` before any display imports,
which `src/display_manager.py:2` then reads to switch between the
hardware and emulator backends.

### Managing Plugins

```bash
# List plugins
./scripts/dev/dev_plugin_setup.sh list

# Check status
./scripts/dev/dev_plugin_setup.sh status

# Update plugin(s)
./scripts/dev/dev_plugin_setup.sh update [plugin-name]

# Unlink plugin
./scripts/dev/dev_plugin_setup.sh unlink <plugin-name>
```

## Using These Files with Cursor

### `.cursorrules`
Cursor automatically reads this file to understand:
- Plugin structure and requirements
- Development workflows
- Best practices
- Common patterns
- API reference

When asking Cursor to help with plugins, it will use this context to provide better assistance.

### Plugin Templates
Use templates when creating new plugins:
1. Copy templates from `.cursor/plugin_templates/`
2. Replace placeholders (PLUGIN_ID, PluginClassName, etc.)
3. Customize for your plugin's needs
4. Follow the guide in `plugins_guide.md`

### Documentation
Refer to `plugins_guide.md` for:
- Detailed explanations
- Troubleshooting steps
- Best practices
- Examples and patterns

## Plugin Development Workflow

1. **Plan**: Determine plugin functionality and requirements
2. **Create**: Use templates or dev_plugin_setup.sh to create plugin structure
3. **Develop**: Implement plugin logic following BasePlugin interface
4. **Test**: Test with emulator first, then on hardware
5. **Configure**: Add plugin config to config/config.json
6. **Iterate**: Refine based on testing and feedback

## Resources

- **Plugin System**: `src/plugin_system/`
- **Base Plugin**: `src/plugin_system/base_plugin.py`
- **Plugin Manager**: `src/plugin_system/plugin_manager.py`
- **Example Plugins**: see the
  [`ledmatrix-plugins`](https://github.com/ChuckBuilds/ledmatrix-plugins)
  repo for canonical sources (e.g. `plugins/hockey-scoreboard/`,
  `plugins/football-scoreboard/`). Installed plugins land in
  `plugin-repos/` (default) or `plugins/` (dev fallback).
- **Architecture Docs**: `docs/PLUGIN_ARCHITECTURE_SPEC.md`
- **Development Setup**: `scripts/dev/dev_plugin_setup.sh`

## Getting Help

1. Check `plugins_guide.md` for detailed documentation
2. Review `.cursorrules` for development patterns
3. Look at existing plugins for examples
4. Check logs for error messages
5. Review plugin system code in `src/plugin_system/`

