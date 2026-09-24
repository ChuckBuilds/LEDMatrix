# Scripts

Helper scripts for installing, repairing, diagnosing and developing
LEDMatrix. Most users only ever run the one-shot installer (see the project
README); everything else here is for troubleshooting or development.

Status key: **keep** — part of install/runtime or referenced by docs, CI,
tests or code; **dev-only** — for plugin/core development, not needed on a
display; **diagnostic** — run by hand on a Pi when something is wrong.

## Directories

| Directory | Status | What it holds |
|---|---|---|
| [`install/`](install/README.md) | keep | The installers: one-shot, services, sudoers/WiFi permissions, cache setup, and the shared `lib_*.sh` helpers `first_time_install.sh` sources |
| [`fix_perms/`](fix_perms/README.md) | keep | Permission repair scripts, plus the two root helpers the web interface runs through sudo (`safe_plugin_rm.sh`, `safe_pip_install.sh`) |
| [`utils/`](utils/README.md) | keep | Scripts run by systemd units or the web interface (conditional web start, WiFi monitor, update verify, DNS fix, Pixlet config editor, cache clearing) |
| [`dev/`](dev/README.md) | dev-only | Plugin linking, emulator runner, Vegas density audit, Pillow smoke test |
| `templates/` | dev-only | `dev_preview.html`, the page `dev_server.py` serves |

## Top-level scripts

| Script | Status | What it does |
|---|---|---|
| `build_rgbmatrix_nogil.sh` | keep | Rebuilds the rgbmatrix Python binding so `SwapOnVSync` releases the GIL (docs/SCROLL_PERFORMANCE.md) |
| `check_plugin.py` | dev-only | Renders a plugin across every mode and matrix size and fails on crashes, overflow or golden-image drift |
| `check_release_version.py` | keep | Checks a release tag, CHANGELOG and `src.__version__` agree (release-version-check workflow) |
| `check_system_compatibility.sh` | diagnostic | Pre-install check of hardware, OS (Trixie only), kernel, Python, packages, disk and network |
| `dev_server.py` | dev-only | Browser preview server for plugins without the display loop (http://localhost:5001) |
| `diagnose_dependencies.sh` | diagnostic | Investigates pip installs stuck on "Preparing metadata" |
| `diagnose_web_interface.sh` | diagnostic | Checks why the web interface is not reachable |
| `download_pixlet.sh` | keep | Downloads the bundled Pixlet binaries for Starlark apps (also run from the web UI) |
| `emergency_reconnect.sh` | diagnostic | Reconnects to your WiFi network if captive-portal testing leaves the Pi offline |
| `install_dependencies_apt.py` | keep | Dependency installer that tries apt packages first, then pip (installer Step 7, plugin loader) |
| `install_plugin_dependencies.sh` | diagnostic | Installs plugin requirements by hand when the automatic install fails |
| `prove_security.py` | keep | Security property checks run by pre-commit |
| `render_plugin.py` | dev-only | Runs a plugin's `update()` + `display()` and saves the frame as a PNG |
| `run_plugin_tests.py` | dev-only | Discovers and runs plugin test suites |
| `scroll_speeds.py` | keep | Shows and tries the scroll speeds your panel can display cleanly |
| `troubleshoot_captive_portal.sh` | diagnostic | Troubleshoots captive-portal WiFi setup after you can SSH back in |
| `update_plugin_repos.py` | dev-only | Pulls the latest `ledmatrix-plugins` monorepo |
| `verify_installation.sh` | diagnostic | Checks that an installation completed correctly |
| `verify_wifi_setup.sh` | diagnostic | Health check of the WiFi management setup |

## Candidates for removal

Nothing in the repo (docs, CI, tests, other scripts or code) refers to these.
They are kept for now; each one needs an owner decision before it goes.

| Script | What it does |
|---|---|
| `add_defaults_to_schemas.py` | One-off: adds missing `default` values to plugin config schemas |
| `analyze_plugin_schemas.py` | One-off: reports duplicate/inconsistent fields across plugin schemas |
| `audit_plugins.py` | AST security audit of plugin code; says it is "designed to run in CI" but no workflow runs it |
| `audit_render_path.py` | Finds blocking calls reachable from a plugin's `display()` |
| `sports_scroll_check.py` | Drives a sports scoreboard scroll on the panel and reports its pacing |
| `test_captive_portal.sh` | Tests the captive portal from a device connected to the AP |
| `verify_wifi_before_testing.sh` | Pre-flight check before unplugging Ethernet to test WiFi |
| `dev/test_pillow_compat.py` | Pillow API smoke test to run after upgrading Pillow |
