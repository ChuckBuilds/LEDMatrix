# Permission Fix Scripts

This directory contains shell scripts for repairing file/directory
permissions on a LEDMatrix installation. They're typically only needed
when something has gone wrong — for example, after running parts of the
install as the wrong user, after a manual file copy that didn't preserve
ownership, or after a permissions-related error from the display or
web service.

Most of these scripts require `sudo` since they touch directories
owned by the `ledmatrix` service user or by `root`.

## Scripts

- **`fix_assets_permissions.sh`** — Fixes ownership and write
  permissions on the `assets/` tree so plugins can download and cache
  team logos, fonts, and other static content.

- **`fix_cache_permissions.sh`** — Fixes permissions on every cache
  directory the project may use (`/var/cache/ledmatrix/`,
  `~/.cache/ledmatrix/`, `/opt/ledmatrix/cache/`, project-local
  `cache/`). Also creates placeholder logo subdirectories used by the
  sports plugins.

- **`fix_plugin_permissions.sh`** — Fixes ownership on the plugins
  directory so both the root display service and the web service user
  can read and write plugin files (manifests, configs, requirements
  installs).

- **`fix_web_permissions.sh`** — Fixes permissions on log files,
  systemd journal access, and the sudoers entries the web interface
  needs to control the display service.

- **`fix_nhl_cache.sh`** — Targeted fix for NHL plugin cache issues
  (clears the NHL cache and restarts the display service).

- **`safe_plugin_rm.sh`** — Validates that a plugin removal path is
  inside an allowed base directory before deleting it. Used by the web
  interface (via sudo) when a user clicks **Uninstall** on a plugin —
  prevents path-traversal abuse from the web UI.

## When to use these

Most users never need to run these directly. The first-time installer
(`first_time_install.sh`) sets up permissions correctly, and the web
interface manages plugin install/uninstall through the sudoers entries
the installer creates.

Run these scripts only when:

- You see "Permission denied" errors in `journalctl -u ledmatrix` or
  the web UI Logs tab.
- You manually copied files into the project directory as the wrong
  user.
- You restored from a backup that didn't preserve ownership.
- You moved the LEDMatrix directory and need to re-anchor permissions.

## Usage

```bash
# Run from the project root
sudo ./scripts/fix_perms/fix_cache_permissions.sh
sudo ./scripts/fix_perms/fix_assets_permissions.sh
sudo ./scripts/fix_perms/fix_plugin_permissions.sh
sudo ./scripts/fix_perms/fix_web_permissions.sh
```

If you're not sure which one you need, run `fix_cache_permissions.sh`
first — it's the most commonly needed and creates several directories
the other scripts assume exist.
