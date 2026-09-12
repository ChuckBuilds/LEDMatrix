# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

Designed novice-first, with power tools kept within reach.

- **Primary: hobbyist builders.** People who assembled an LED matrix panel on a Raspberry Pi, often by following the install video, and are frequently new to Linux and the Pi. They set the display up once (panel size, timezone, WiFi), install and enable a few plugins, then come back occasionally to tweak what the panel shows. They usually reach the control panel from a phone or laptop on their home network, sometimes as an installed home-screen app.
- **Secondary: tinkerers and plugin developers.** Comfortable with SSH, `config.json`, and GitHub. They lean on the Config Editor, Logs, Cache, Operation History, Tools, GitHub-repo installs, and per-plugin config while building or debugging. Their tools must stay reachable without sitting in the novice's path.

## Product Purpose

LEDMatrix turns a Raspberry Pi and an RGB LED matrix panel into an information-rich display (clock, weather, calendar, sports scores, stocks, music, and more) through a plugin platform. The web control panel ("LED Matrix Control") is where the display gets configured, extended, and kept healthy.

Success means a builder gets from a freshly flashed Pi to a working, personalized display without needing a terminal, and can keep it running (updates, recovery, troubleshooting) the same way.

## Positioning

Four strengths define LEDMatrix, and future work must protect all of them:

1. **Plugin ecosystem.** The core ships only `starlark-apps` and `web-ui-info`; everything else comes from the built-in Plugin Store (the official `ledmatrix-plugins` monorepo), third-party GitHub repos, or Starlark (Tidbyt-style) apps. Each installed plugin gets its own configuration tab, generated from its schema.
2. **Runs on tiny Pis.** The UI is served by the same device that drives the matrix, on boards as small as the Pi Zero 2 W (512 MB), Pi 3/3B+, and the 1 GB Pi 4.
3. **Recovers without SSH.** WiFi access-point fallback with a captive setup page, backup & restore, in-UI updates, live logs, diagnostics, service control, and plugin health let users fix problems from the browser.
4. **Open and community-led.** GPL-3.0, a Discord community, and contributions welcome. The maintainer (ChuckBuilds) builds in public and openly relies on AI development tools.

## Operating Context

- **Access.** Served on the local network at `http://<pi-ip>:5000` by the `ledmatrix-web` service. It is installable as a PWA (`web_interface/static/v3/manifest.json`, short name "LEDMatrix").
- **First run.** When the Pi has no network it creates its own WiFi access point, so the captive setup page (`templates/v3/captive_setup.html`) may be the very first screen a user sees, on a phone, with no internet connection.
- **Navigation.**
  - System tabs: Overview, General, WiFi, Schedule, Display, Rotation, Config Editor, Backup & Restore, Fonts, Logs, Cache, Operation History, Tools.
  - A second row holds Plugin Manager (with the Plugin Store), Starlark Apps, and one tab per installed plugin.
- **Live data.** The Overview shows system stats (CPU, memory, temperature, power/throttling) and a live display preview, streamed over SSE.
- **Getting Started checklist.** The Overview's first-run checklist runs: set panel size → set timezone → install a plugin → enable it → configure it.
- **Development.** `python3 scripts/dev_server.py` gives a browser preview without the display loop; `python3 run.py -e` runs the full display in emulator mode.

## Capabilities and Constraints

- **Hard constraint: plugin UI compatibility.** Third-party plugins rely on JSON Schema (Draft-7) generated config forms, the widget registry (`static/v3/js/widgets/`), `x-secret` fields, and plugin web-UI actions. UI changes must keep these working.
- **Config storage.** Plugin configuration lives in `config/config.json` and secrets in `config/config_secrets.json`, never in plugin directories, so configs survive reinstalls.
- **Stack.** An existing Flask + HTMX + Alpine.js app with Jinja templates (`web_interface/templates/v3/`) and static JS/CSS (`web_interface/static/v3/`), with self-hosted vendor assets.
- **Terminology.** Plugin, Plugin Store, Starlark app, rotation, display duration, Vegas Scroll Mode, skin, on-demand, AP mode.
- **Open decisions** (offered during init, not adopted as constraints):
  - Whether the UI must work fully offline, with no CDN fallbacks at runtime.
  - Whether a Node/CSS build step is acceptable for contributors.
  - Whether a formal accessibility standard (e.g. WCAG 2.2 AA) is a requirement.

## Brand Commitments

- **Names.** The product is "LEDMatrix" and the web UI is titled "LED Matrix Control". The maintainer brand is ChuckBuilds.
- **Voice.** Friendly, honest, and learning-in-public, as in the README.
- **App icons.** They live in `web_interface/static/v3/icons/`.

No other visual identity has been made binding.

## Evidence on Hand

- **Photos.** Real photographs of running displays are linked in `README.md` (clock, weather, calendar, NHL/MLB/NFL/NCAA, stocks, music).
- **Video.** YouTube install and walkthrough videos from ChuckBuilds.
- **Docs.** Extensive documentation in `docs/`, e.g. `WEB_INTERFACE_GUIDE.md`, `GETTING_STARTED.md`, `WIFI_NETWORK_SETUP.md`, `LOW_MEMORY_BOARDS.md`, `PLUGIN_STORE_GUIDE.md`.
- **Absences.** There are no testimonials, user counts, or benchmark figures. Do not fabricate them.

## Product Principles

1. **Novice path first, power one click away.** Default views serve the first-time builder, while advanced tools stay discoverable for tinkerers.
2. **Never strand the user at a terminal.** Every setup, recovery, and troubleshooting task has a browser path, including from the AP-mode captive page.
3. **Respect the Pi.** Every feature is paid for in memory and CPU on a Pi Zero 2 W that is also driving the display.
4. **The ecosystem is the product.** Plugins, including third-party ones, must feel first-class and keep working across core UI changes.
5. **Honest and welcoming.** Plain language, truthful status, and no overstated claims, in keeping with an open, community-built project.
