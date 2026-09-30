# Security Policy

## Reporting a vulnerability

If you've found a security issue in LEDMatrix, **please don't open a
public GitHub issue**. Disclose it privately so we can fix it before it's
exploited.

### How to report

Use one of these channels, in order of preference:

1. **GitHub Security Advisories** (preferred). On the LEDMatrix repo,
   go to **Security → Advisories → Report a vulnerability**. This
   creates a private discussion thread visible only to you and the
   maintainer.
   - Direct link: <https://github.com/ChuckBuilds/LEDMatrix/security/advisories/new>
2. **Discord DM**. Send a direct message to a moderator on the
   [LEDMatrix Discord](https://discord.gg/RdrC37rEag). Don't post in
   public channels.

Please include:

- A description of the issue
- The version / commit hash you're testing against
- Steps to reproduce, ideally a minimal proof of concept
- The impact you can demonstrate
- Any suggested mitigation

### What to expect

- An acknowledgement within a few days (this is a hobby project, not
  a 24/7 ops team).
- A discussion of the issue's severity and a plan for the fix.
- Credit in the release notes when the fix ships, unless you'd
  prefer to remain anonymous.
- For high-severity issues affecting active deployments, we'll
  coordinate disclosure timing with you.

## Scope

In scope for this policy:

- The LEDMatrix display controller, web interface, and plugin loader
  in this repository
- The official plugins in
  [`ledmatrix-plugins`](https://github.com/ChuckBuilds/ledmatrix-plugins)
- Installation scripts and systemd unit files

Out of scope (please report upstream):

- Vulnerabilities in `rpi-rgb-led-matrix` itself —
  report to <https://github.com/hzeller/rpi-rgb-led-matrix>
- Vulnerabilities in Python packages we depend on — report to the
  upstream package maintainer
- Issues in third-party plugins not in `ledmatrix-plugins` — report
  to that plugin's repository

## Known security model

LEDMatrix is designed for trusted local networks. Several limitations
are intentional rather than vulnerabilities:

- **Web UI authentication is optional and off by default.** Out of the
  box the web interface assumes the network it's running on is trusted.
  Setting a password under **General > Security** makes every page and
  API route require a login or an API token (`Authorization: Bearer`),
  with wrong passwords rate-limited per address
  (`web_interface/auth.py`). Deliberately left open even then: requests
  from the Pi itself (loopback without proxy headers; a reverse proxy on
  the Pi must add `X-Forwarded-For`, or every request it relays counts as
  local), the Wi-Fi setup flow while the Pi is in access-point mode,
  static files, and a status-only `/api/v3/health`. The password is a
  werkzeug hash and tokens are stored as SHA-256, in
  `config/config_secrets.json`, which no API returns. There is no TLS:
  over plain HTTP the password and tokens cross the LAN in the clear, so
  still don't expose port 5000 to the internet; put a TLS reverse proxy
  or a VPN in front for remote access. Anyone with shell access to the Pi
  can turn login off (`scripts/reset_web_password.py`), which is the
  documented recovery path.
  "Trusted network" does not mean "trusted websites", though: any page
  a LAN user opens could make their browser POST to the Pi. So the
  interface refuses a `POST`/`PUT`/`PATCH`/`DELETE` whose `Origin` (or
  `Referer`) header names another site (`web_interface/origin_guard.py`),
  and `/api/v3/system/action` only accepts JSON or HTMX requests. Tools
  that send neither header (curl, Home Assistant, the MQTT bridge) are
  unaffected. Not covered: DNS rebinding, and anyone who can reach the
  port directly.
- **Plugins run unsandboxed.** Installed plugins execute in the same
  Python process as the display loop with full file-system and
  network access. Review plugin code (especially third-party plugins
  from arbitrary GitHub URLs) before installing. The Plugin Store
  marks community plugins as **Custom** to highlight this.
- **The display service runs as root** for hardware GPIO access. This
  is required by `rpi-rgb-led-matrix`.
- **`config_secrets.json` is plaintext.** API keys and tokens are
  stored unencrypted on the Pi. Lock down filesystem permissions on
  the config directory if this matters for your deployment.

These are documented as known limitations rather than bugs. If you
have ideas for improving them while keeping the project usable on a
Pi, open a discussion — we're interested.

## Supported versions

LEDMatrix is rolling-release on `main`. Security fixes land on `main`
and become available the next time users run **Update Code** from the
web UI's Overview tab (which does a `git pull`). There are no LTS
branches.
