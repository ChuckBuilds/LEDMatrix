# Plugin Dependency Installation Troubleshooting

This guide helps resolve problems installing a plugin's Python packages. For
how installation works, see the [Plugin Dependency Guide](PLUGIN_DEPENDENCY_GUIDE.md).

## Common Error Symptoms

### Permission Errors
```
ERROR: Could not install packages due to an OSError: [Errno 13] Permission denied: '/root/.local'
WARNING: The directory '/root/.cache/pip' or its parent directory is not owned or is not writable
```

### Installed for the wrong user
The pip output shown after a web-UI install starts with:
```
[Root install unavailable (...); installed for the current process's user only.
Packages may not be visible to ledmatrix.service if it runs as a different
user — run scripts/install/configure_web_sudo.sh to fix this.]
```

### Plugin fails to load with `ModuleNotFoundError`
The display service can't see a package the plugin needs.

## Root Cause

Plugin packages must be importable by `ledmatrix.service`, which runs as
root. The web interface (`ledmatrix-web.service`) runs as the user who
installed LEDMatrix, so it installs through a sudo helper
(`scripts/fix_perms/safe_pip_install.sh`). Problems usually come from:

1. The sudoers rule for that helper missing, so the web UI installed the
   packages for its own user only
2. Running `python3 run.py` by hand as a normal user, which installs missing
   packages into `~/.local/`
3. pip's cache directory not being writable for root

## Solutions

### Solution 1: Restore the sudo rule, then reinstall

```bash
cd ~/LEDMatrix
./scripts/install/configure_web_sudo.sh   # as the web user, not with sudo
```

Then reinstall the plugin from the **Plugin Manager** tab, or click
**Reinstall Plugin Deps** on the **Tools** tab.

### Solution 2: Install every plugin's dependencies from the terminal

```bash
sudo ~/LEDMatrix/scripts/install_plugin_dependencies.sh
sudo systemctl restart ledmatrix
```

The script finds each `requirements.txt` in the plugins directory set by
`plugin_system.plugins_directory` in `config/config.json` (default
`plugin-repos/`), installs with `--no-cache-dir`, and reports what it found.

### Solution 3: Install one plugin's dependencies

```bash
# Your configured plugins directory; plugin-repos/ by default
cd ~/LEDMatrix/plugin-repos/PLUGIN-NAME

sudo python3 -m pip install --break-system-packages --no-cache-dir -r requirements.txt

sudo systemctl restart ledmatrix
```

### Solution 4: Let the display service install them

When a plugin loads, the display service installs any missing requirements
itself, as root:

```bash
sudo systemctl restart ledmatrix
sudo journalctl -u ledmatrix -f     # watch for "Installing dependencies for plugin ..."
```

### Solution 5: Fix pip cache permissions

```bash
# Option A: Skip the cache (recommended)
sudo python3 -m pip install --no-cache-dir --break-system-packages -r requirements.txt

# Option B: Fix cache permissions
sudo mkdir -p /root/.cache/pip
sudo chown -R root:root /root/.cache
sudo chmod -R 755 /root/.cache
```

## Prevention

### For Plugin Developers

1. **Keep requirements minimal**: Only include essential packages
2. **Test installation** the way the Pi does it:
   ```bash
   sudo python3 -m pip install --break-system-packages --no-cache-dir -r requirements.txt
   ```
3. **Document dependencies**: Note any system packages needed (via apt)

### For Users

1. **Use the web interface** to install plugins
2. **Use sudo** for installs from SSH/terminal
3. **Restart the service** after manual installations

## Technical Details

### Where installs happen

- **Web UI install/update:** `PluginStoreManager._install_dependencies()`
  → `install_requirements_file()` in `src/common/permission_utils.py`, which
  runs `sudo -n bash scripts/fix_perms/safe_pip_install.sh <requirements.txt>`.
  The helper only accepts the project's `requirements.txt` or one under
  `plugin-repos/` or `plugins/`, and runs
  `pip install --break-system-packages --ignore-installed` as root. If sudo
  refuses, it falls back to a pip install as the web user and says so.
- **Plugin load:** `PluginLoader.install_dependencies()` in
  `src/plugin_system/plugin_loader.py` skips satisfied requirements and
  otherwise runs `pip install --break-system-packages` with the loading
  process's interpreter — root in `ledmatrix.service`.

### Why `--break-system-packages`?

Debian 12+ (Bookworm) and Raspberry Pi OS based on it implement PEP 668, which prevents pip from installing packages system-wide by default. The `--break-system-packages` flag overrides this protection, which is necessary for the plugin system.

### Service Context

- `ledmatrix.service` runs as **root** with `/usr/bin/python3`
- `ledmatrix-web.service` runs as **the installing user**

Dependencies must be installed system-wide (as root) to be visible to the
display service.

## Checking Installation

```bash
# Check as root (how the service sees it)
sudo python3 -c "import package_name; print(package_name.__file__)"

# A path under /home/<user>/.local/ means a user-only install
python3 -m pip show -f package_name
```

## Getting Help

If you continue to experience issues:

1. Check the service logs:
   ```bash
   sudo journalctl -u ledmatrix -f
   ```

2. Verify the plugin manifest and requirements (default plugins directory
   shown):
   ```bash
   cat ~/LEDMatrix/plugin-repos/PLUGIN-NAME/manifest.json
   cat ~/LEDMatrix/plugin-repos/PLUGIN-NAME/requirements.txt
   ```

## Related Documentation

- [Plugin Dependency Guide](PLUGIN_DEPENDENCY_GUIDE.md)
- [Plugin Development Guide](PLUGIN_DEVELOPMENT_GUIDE.md)
- [Troubleshooting](TROUBLESHOOTING.md)
