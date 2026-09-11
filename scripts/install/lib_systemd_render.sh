#!/bin/bash
#
# Shared helper for rendering systemd unit templates via sed.
#
# Sourced by install_service.sh, install_web_service.sh and
# install_wifi_monitor.sh so all three escape sed replacement text the same
# way instead of carrying three copies of the same fix.

# sed_escape_replacement VALUE
#
# Print VALUE escaped for safe use as the replacement side of `sed
# s|pattern|replacement|`. Every one of these scripts builds its sed
# expression by interpolating a shell variable (a path, a username, ...)
# straight into the replacement text. sed gives three characters special
# meaning there: backslash (escape character), & (whole match) and the
# delimiter itself (here `|`). A value containing any of them -- e.g. a
# username or path with an `&`, a literal backslash, or a `|` -- would
# otherwise corrupt the rendered unit file instead of being substituted
# literally. Escape the backslash first so the later escapes aren't
# double-escaped.
sed_escape_replacement() {
    local value="$1"
    value="${value//\\/\\\\}"
    value="${value//&/\\&}"
    value="${value//|/\\|}"
    printf '%s' "$value"
}
