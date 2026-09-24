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
- **`install_dns_fix.sh`** - Optional. Installs `ledmatrix-dns-fix.service`,
  which adds `options single-request` to the resolver when API calls time
  out (see `systemd/README.md`)
- **`install_mqtt_bridge.sh`** - Optional. Installs the Home Assistant MQTT
  bridge service (see `integrations/mqtt_bridge/README.md`)

Libraries (sourced, not run):

- **`lib_sudoers.sh`** - The web interface's sudo allow-list
  (`/etc/sudoers.d/ledmatrix_web`), shared by `first_time_install.sh` and
  `configure_web_sudo.sh`
- **`lib_systemd_render.sh`** - `sed_escape_replacement`, used by every
  script that renders a unit from `systemd/*.service`
- **`lib_lowmem.sh`** - Build-job sizing and temporary swap for the C++
  build on low-memory Pis (`first_time_install.sh` Step 6)

## Usage

These scripts are typically called by `first_time_install.sh` in the
project root (which itself is invoked by `one-shot-install.sh`), but
can also be run individually if needed.

**Note:** Most installation scripts require `sudo` privileges to install systemd services and configure system settings.

