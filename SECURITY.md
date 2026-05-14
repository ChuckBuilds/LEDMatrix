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
   [LEDMatrix Discord](https://discord.gg/uW36dVAtcT). Don't post in
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

- **No web UI authentication.** The web interface assumes the network
  it's running on is trusted. Don't expose port 5000 to the internet.
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
