# Plugin Dependency Installation Guide

## Overview

A plugin lists its Python packages in its `requirements.txt`. LEDMatrix
installs them for you when a plugin is installed, updated or loaded. This
guide explains where they end up and what to do when a plugin can't import a
package.

The rule to remember: **packages must be importable by `ledmatrix.service`,
which runs as root.** Anything installed only into another user's
`~/.local/` is invisible to it.

## Who Runs What

| Service | Runs as | Set by |
|---------|---------|--------|
| `ledmatrix.service` (display) | `root` | `systemd/ledmatrix.service` |
| `ledmatrix-web.service` (web UI) | the user who ran the installer (e.g. `ledpi`) | `User=__USER__` in `systemd/ledmatrix-web.service`, filled in by `scripts/install/install_service.sh` |

## How Dependencies Get Installed

### 1. Installing or updating a plugin from the web UI

The web interface is not root, so it installs through a narrow sudo helper:

1. `PluginStoreManager._install_dependencies()`
   (`src/plugin_system/store_manager.py`) calls
   `install_requirements_file()` (`src/common/permission_utils.py`).
2. That runs `sudo -n bash scripts/fix_perms/safe_pip_install.sh <plugin>/requirements.txt`.
   The helper checks the path is the project's own `requirements.txt` or a
   `requirements.txt` under `plugin-repos/` or `plugins/`, then runs
   `python3 -m pip install --break-system-packages --ignore-installed -r ...`
   **as root**, so the display service can import the packages.
3. The sudoers rule that allows this is written by the installer
   (`first_time_install.sh`) or by `scripts/install/configure_web_sudo.sh`.

If sudo refuses (the rule isn't installed), `install_requirements_file()`
falls back to installing with the web process's own interpreter, as the web
user, and prefixes the pip output with a note like:

```
[Root install unavailable (...); installed for the current process's user only.
Packages may not be visible to ledmatrix.service if it runs as a different
user — run scripts/install/configure_web_sudo.sh to fix this.]
```

Fix it by running `./scripts/install/configure_web_sudo.sh` as the web
user (not with `sudo`; it asks for your password itself), then
reinstall the plugin (or use the manual install below).

The **Reinstall Plugin Deps** button on the web UI's Tools tab goes
through the same helper for every installed plugin.

### 2. Loading a plugin

When a plugin loads, `PluginLoader.install_dependencies()`
(`src/plugin_system/plugin_loader.py`) checks its `requirements.txt`. If the
requirements are already satisfied it does nothing; otherwise it runs
`python3 -m pip install --break-system-packages -r requirements.txt` with the
interpreter of the process doing the loading (retrying with
`--ignore-installed` when a system package without a pip RECORD file is in
the way).

In `ledmatrix.service` that process is root, so restarting the display
service installs anything missing system-wide:

```bash
sudo systemctl restart ledmatrix
```

If you run `python3 run.py` by hand as a normal user instead, pip cannot
write to the system site-packages and installs into your `~/.local/`. That
works for your manual run but not for the service.

## Common Scenarios

### Installing plugins from the web UI (recommended)

Use the **Plugin Manager** tab. Dependencies are installed as root through
the sudo helper and the display service can use them.

### Running the display manually for debugging

```bash
cd ~/LEDMatrix
sudo python3 run.py        # same user as the service
```

Running as your own user works for plugins whose packages are already
installed system-wide, but any *missing* package lands in `~/.local/`.

### A plugin works when run manually but fails in the service

Its packages were installed for your user only. Install them as root (see
below) and restart the service.

## Manual Installation

### All plugins

```bash
sudo ~/LEDMatrix/scripts/install_plugin_dependencies.sh
sudo systemctl restart ledmatrix
```

The script installs every `requirements.txt` found in the plugins directory
configured by `plugin_system.plugins_directory` in `config/config.json`
(default `plugin-repos/`). Run it with `sudo` so the packages are installed
system-wide.

### One plugin

```bash
cd ~/LEDMatrix/plugin-repos/PLUGIN-NAME     # or your configured plugins directory
sudo python3 -m pip install --break-system-packages --no-cache-dir -r requirements.txt
sudo systemctl restart ledmatrix
```

`--no-cache-dir` avoids errors about `/root/.cache/pip` not being writable.

## Troubleshooting

### Permission denied when installing dependencies

```
ERROR: Could not install packages due to an OSError: [Errno 13] Permission denied: '/root/.local'
WARNING: The directory '/root/.cache/pip' or its parent directory is not owned or is not writable
```

Use one of the manual installs above (they pass `--no-cache-dir`).

### Checking where a package is installed

```bash
# How the service sees it
sudo python3 -c "import package_name; print(package_name.__file__)"

# A path under /home/<user>/.local/ means it was installed for that user only
python3 -m pip show -f package_name
```

For more, see the [Plugin Dependency Troubleshooting Guide](PLUGIN_DEPENDENCY_TROUBLESHOOTING.md).

## For Plugin Authors

1. Keep `requirements.txt` minimal and pin only what you need.
2. Test that it installs the way the Pi will install it:
   ```bash
   sudo python3 -m pip install --break-system-packages --no-cache-dir -r requirements.txt
   ```
3. Note any `apt` packages your plugin needs in its README.

## Files to Reference

- Service units: `systemd/ledmatrix.service`, `systemd/ledmatrix-web.service`
- Store installs: `src/plugin_system/store_manager.py` (`_install_dependencies`)
- Root install helper: `src/common/permission_utils.py` (`install_requirements_file`), `scripts/fix_perms/safe_pip_install.sh`
- Load-time installs: `src/plugin_system/plugin_loader.py` (`install_dependencies`)
- Sudo rules: `scripts/install/configure_web_sudo.sh`
- Manual installer: `scripts/install_plugin_dependencies.sh`
