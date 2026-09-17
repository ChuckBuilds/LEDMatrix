# Installation Scripts

This directory contains scripts for installing and configuring the LEDMatrix system.

## Scripts

- **`one-shot-install.sh`** - Single-command installer; clones the
  repo, checks prerequisites, then runs `first_time_install.sh`.
  Invoked via `curl ... | bash` from the project root README.
- **`install_service.sh`** - Installs, enables and starts the display
  service (`ledmatrix.service`), the web interface service
  (`ledmatrix-web.service`) and the update-verify units (systemd)
- **`install_web_service.sh`** - Installs only the web interface service
  and the update-verify units (systemd)
- **`install_wifi_monitor.sh`** - Installs the WiFi monitor daemon service
- **`setup_cache.sh`** - Sets up persistent cache directory with proper permissions
- **`configure_web_sudo.sh`** - Configures passwordless sudo access for web interface actions
- **`configure_wifi_permissions.sh`** - Grants the web interface's user
  (the user who runs the script, i.e. the one you installed LEDMatrix as;
  there is no `ledmatrix` system user) the passwordless `nmcli` and related
  WiFi permissions the web interface needs
- **`migrate_config.sh`** - Migrates configuration files to new formats (if needed)
- **`debug_install.sh`** - Diagnostic helper used when an install
  fails; collects environment info and recent logs

## Usage

These scripts are typically called by `first_time_install.sh` in the
project root (which itself is invoked by `one-shot-install.sh`), but
can also be run individually if needed.

**Note:** Most installation scripts require `sudo` privileges to install systemd services and configure system settings.

