#!/bin/bash
#
# Add `options single-request` to the system resolver configuration.
#
# glibc's getaddrinfo() sends the A and AAAA queries for a name in
# parallel on one socket. Some routers answer the A query and drop the
# AAAA one, so the resolver waits out its full timeout -- about five
# seconds -- before returning an address that was already available.
# Disabling IPv6 in the kernel does not help: the resolver still asks.
#
# `single-request` makes it send the two queries one after the other,
# which those routers answer correctly. Anything on the matrix that
# calls an external API pays that five seconds per lookup otherwise, and
# a Starlark app with a render timeout will simply fail instead.
#
# Idempotent, and safe to run on a machine that does not need it. Run by
# ledmatrix-dns-fix.service on every boot, because whatever manages
# resolv.conf regenerates it and drops the option again.
#
# Usage: sudo ./scripts/utils/apply_dns_single_request.sh

set -eu

OPTION="options single-request"
RESOLVCONF_TAIL="/etc/resolvconf/resolv.conf.d/tail"
RESOLV_CONF="/etc/resolv.conf"

log() { echo "[dns-single-request] $*"; }

already_applied() {
    grep -qs "^${OPTION}\$" "$1"
}

# resolvconf regenerates /etc/resolv.conf from these fragments, so the
# tail file is the only place an addition survives. Prefer it when the
# directory exists, whether or not resolvconf has run yet.
if [ -d "$(dirname "$RESOLVCONF_TAIL")" ]; then
    if already_applied "$RESOLVCONF_TAIL"; then
        log "already present in $RESOLVCONF_TAIL"
    else
        echo "$OPTION" >> "$RESOLVCONF_TAIL"
        log "added to $RESOLVCONF_TAIL"
    fi
    command -v resolvconf >/dev/null 2>&1 && resolvconf -u || true
fi

# systemd-resolved owns its stub file and rewrites anything appended to
# it. The equivalent setting there is a resolved.conf drop-in, which is
# a different change than this script makes -- say so rather than
# writing to a file that will be overwritten.
if [ -L "$RESOLV_CONF" ] && readlink -f "$RESOLV_CONF" | grep -q "systemd"; then
    log "$RESOLV_CONF is managed by systemd-resolved; not modifying it."
    log "If lookups are slow, see 'man resolved.conf' -- the resolver options"
    log "there are set per-link, not through resolv.conf."
    exit 0
fi

if already_applied "$RESOLV_CONF"; then
    log "already present in $RESOLV_CONF"
    exit 0
fi

if [ -w "$RESOLV_CONF" ] || [ ! -e "$RESOLV_CONF" ]; then
    echo "$OPTION" >> "$RESOLV_CONF"
    log "added to $RESOLV_CONF"
else
    log "cannot write $RESOLV_CONF (run with sudo?)"
    exit 1
fi
