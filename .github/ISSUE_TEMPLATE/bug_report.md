---
name: Bug report
about: Report a problem with LEDMatrix
title: ''
labels: bug
assignees: ''

---

<!--
Before filing: please check existing issues to see if this is already
reported. For security issues, see SECURITY.md and report privately.
-->

## Describe the bug

<!-- A clear and concise description of what the bug is. -->

## Steps to reproduce

1.
2.
3.

## Expected behavior

<!-- What you expected to happen. -->

## Actual behavior

<!-- What actually happened. Include any error messages. -->

## Hardware

- **Raspberry Pi model**: <!-- e.g. Pi 3B+, Pi 4 8GB, Pi Zero 2W -->
- **OS / kernel**: <!-- output of `cat /etc/os-release` and `uname -a` -->
- **LED matrix panels**: <!-- e.g. 2x Adafruit 64x32, 1x Waveshare 96x48 -->
- **HAT / Bonnet**: <!-- e.g. Adafruit RGB Matrix Bonnet, Electrodragon HAT -->
- **PWM jumper mod soldered?**: <!-- yes / no -->
- **Display chain**: <!-- chain_length × parallel, e.g. "2x1" -->

## LEDMatrix version

<!-- Run `git rev-parse HEAD` in the LEDMatrix directory, or paste the
release tag if you installed from a release. -->

```
git commit:
```

## Plugin involved (if any)

- **Plugin id**:
- **Plugin version** (from `manifest.json`):

## Configuration

<!-- Paste the relevant section from config/config.json. Redact any
API keys before pasting. For display issues, the `display.hardware`
block is most relevant. For plugin issues, paste that plugin's section. -->

```json
```

## Logs

<!-- The first 50 lines of the relevant log are usually enough. Run:
  sudo journalctl -u ledmatrix -n 100 --no-pager
or for the web service:
  sudo journalctl -u ledmatrix-web -n 100 --no-pager
-->

```
```

## Screenshots / video (optional)

<!-- A photo of the actual display, or a screenshot of the web UI,
helps a lot for visual issues. -->

## Additional context

<!-- Anything else that might be relevant: when did this start happening,
what's different about your setup, what have you already tried, etc. -->
