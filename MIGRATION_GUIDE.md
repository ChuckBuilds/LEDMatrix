# Migration Guide

This guide helps you migrate from older versions of LEDMatrix to the latest version.

## Breaking Changes

### Script Path Reorganization

Scripts have been reorganized into subdirectories for better organization. **If you have automation, cron jobs, or custom tooling that references old script paths, you must update them.**

#### Installation Scripts

All installation scripts have been moved from the project root to `scripts/install/`:

| Old Path | New Path |
|----------|----------|
| `install_service.sh` | `scripts/install/install_service.sh` |
| `install_web_service.sh` | `scripts/install/install_web_service.sh` |
| `install_wifi_monitor.sh` | `scripts/install/install_wifi_monitor.sh` |
| `setup_cache.sh` | `scripts/install/setup_cache.sh` |
| `configure_web_sudo.sh` | `scripts/install/configure_web_sudo.sh` |
| `migrate_config.sh` | `scripts/install/migrate_config.sh` |

#### Permission Fix Scripts

All permission fix scripts have been moved to `scripts/fix_perms/`:

| Old Path | New Path |
|----------|----------|
| `fix_assets_permissions.sh` | `scripts/fix_perms/fix_assets_permissions.sh` |
| `fix_cache_permissions.sh` | `scripts/fix_perms/fix_cache_permissions.sh` |
| `fix_plugin_permissions.sh` | `scripts/fix_perms/fix_plugin_permissions.sh` |
| `fix_web_permissions.sh` | `scripts/fix_perms/fix_web_permissions.sh` |

#### Action Required

1. **Update cron jobs**: If you have any cron jobs that call these scripts, update the paths.
2. **Update automation scripts**: Any custom scripts or automation that references the old paths must be updated.
3. **Update documentation**: Update any internal documentation or runbooks that reference these scripts.

#### Example Updates

**Before:**
```bash
# Old cron job or script
0 2 * * * /path/to/LEDMatrix/fix_cache_permissions.sh
sudo ./install_service.sh
```

**After:**
```bash
# Updated paths
0 2 * * * /path/to/LEDMatrix/scripts/fix_perms/fix_cache_permissions.sh
sudo ./scripts/install/install_service.sh
```

#### Verification

After updating your scripts, verify they still work:

```bash
# Test installation scripts (if needed)
ls scripts/install/*.sh
sudo ./scripts/install/install_service.sh --help

# Test permission scripts
ls scripts/fix_perms/*.sh
sudo ./scripts/fix_perms/fix_cache_permissions.sh
```

---

## Other Changes

### Configuration File Location

No changes to configuration file locations. The configuration system remains backward compatible.

### Plugin System

The plugin system has been enhanced but remains backward compatible with existing plugins.

---

## Getting Help

If you encounter issues during migration:

1. Check the [README.md](README.md) for current installation and usage instructions
2. Review script README files:
   - `scripts/install/README.md` - Installation scripts documentation
   - `scripts/fix_perms/README.md` (if exists) - Permission scripts documentation
3. Check system logs: `journalctl -u ledmatrix -f` or `journalctl -u ledmatrix-web -f`
4. Review the troubleshooting section in the main README

