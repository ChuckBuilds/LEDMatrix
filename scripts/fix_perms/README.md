# Permission Fix Scripts

This directory contains shell scripts for repairing file/directory
permissions on a LEDMatrix installation. They're typically only needed
when something has gone wrong — for example, after running parts of the
install as the wrong user, after a manual file copy that didn't preserve
ownership, or after a permissions-related error from the display or
web service.

Most of these scripts require `sudo` since they touch directories owned
by `root` (the display service's user) or by the user you installed
LEDMatrix as (the web service's user). There is no dedicated `ledmatrix`
system user.

## Scripts

- **`fix_assets_permissions.sh`** — Fixes ownership and write
  permissions on the `assets/` tree so plugins can download and cache
  team logos, fonts, and other static content.

- **`fix_cache_permissions.sh`** — Restores `/var/cache/ledmatrix/` to the
  shared `ledmatrix`-group setup by running
  `scripts/install/setup_cache.sh` (the same script the installer uses),
  and creates/fixes `~/.ledmatrix_cache/` of the user running `sudo`. It
  does not touch the cache manager's other fallbacks
  (`/opt/ledmatrix/cache`, `$TMPDIR/ledmatrix_cache`).

- **`fix_plugin_permissions.sh`** — Fixes ownership on the plugins
  directory so both the root display service and the web service user
  can read and write plugin files (manifests, configs, requirements
  installs).

- **`fix_web_permissions.sh`** — Adds you to the `systemd-journal` and
  `adm` groups so the web UI can read logs, and makes the project
  directory yours again, keeping the root-owned sudo helpers
  (`safe_plugin_rm.sh`, `safe_pip_install.sh`) and `config_secrets.json`
  the way the installer leaves them. Run it as the web interface's user,
  **without** `sudo` (it refuses to run as root and calls `sudo` itself).
  It does not write sudoers rules; that is
  `scripts/install/configure_web_sudo.sh`.

- **`safe_pip_install.sh`** — Installs a `requirements.txt` as root
  after checking it is the project's own or one under `plugin-repos/` or
  `plugins/`. Used by the web interface (via sudo) to install plugin
  dependencies where `ledmatrix.service` can import them.

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

# Run as the web interface's user, without sudo (it asks for sudo itself)
./scripts/fix_perms/fix_web_permissions.sh
```

If you're not sure which one you need, run `fix_cache_permissions.sh`
first — it's the most commonly needed and creates several directories
the other scripts assume exist.
