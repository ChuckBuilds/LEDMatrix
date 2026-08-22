#!/bin/bash
#
# Low-memory build helpers for the LED Matrix installer.
#
# Sourced by first_time_install.sh. These live in a separate, sourceable file
# so the pure sizing/detection functions can be unit-tested
# (test/test_install_lowmem.py); first_time_install.sh itself is not sourceable
# because it self-elevates and runs top to bottom.
#
# Why this exists: the rgbmatrix build compiles ~45 C++ translation units, two
# of them Cython-generated (a single cc1plus on those peaks around 400-800MB at
# -O3). Upstream's pyproject.toml sets no [tool.scikit-build] options, so
# scikit-build-core uses Ninja at its default of nproc+2 jobs -- six concurrent
# compiles on a 4-core Pi. On a 512MB-1GB Pi the OOM killer reaps cc1plus and
# pip reports only "Failed building wheel for rgbmatrix".
#
# The caller runs under `set -Eeuo pipefail` with an ERR trap, and these are
# invoked from the middle of numbered steps, so nothing here may call exit and
# the swap helpers must always return 0.

# Overridable so tests can point at fixture files instead of /proc.
LM_MEMINFO="${LM_MEMINFO:-/proc/meminfo}"
LM_SWAPS="${LM_SWAPS:-/proc/swaps}"

# Temporary swapfile created for the build and removed afterwards. Deliberately
# never added to /etc/fstab: a malformed fstab can leave a novice with an
# unbootable Pi, and this swap only needs to outlive the compile.
LM_SWAPFILE="${LM_SWAPFILE:-/var/swap.ledmatrix-install}"

# Bring RAM + real swap up to this much before compiling, capped per swapfile.
LM_SWAP_TARGET_MB="${LM_SWAP_TARGET_MB:-3072}"
LM_SWAP_MAX_MB="${LM_SWAP_MAX_MB:-2048}"

# Worst-case cc1plus footprint on the Cython translation unit, used to size
# build parallelism against available RAM.
LM_MB_PER_JOB="${LM_MB_PER_JOB:-768}"

# Set to 1 once swap is live, so lm_remove_build_swap (wired up as an EXIT
# trap) knows whether there is anything to undo.
LM_TEMP_SWAP_ACTIVE=0

# Human-readable reason no swapfile was created, quoted back in the failure
# message so a user who still OOMs is told why the safety net was absent.
LM_SWAP_SKIP_REASON=""

# ---------------------------------------------------------------------------
# Pure helpers (no side effects; unit-tested)
# ---------------------------------------------------------------------------

# Total physical RAM in MB, or 0 if it cannot be determined.
lm_total_ram_mb() {
    # Defaults are re-resolved here as well as at source time so the function
    # stays safe under the installer's `set -u`.
    awk '/^MemTotal:/ {printf "%d\n", $2 / 1024; found = 1; exit} END {if (!found) print 0}' \
        "${LM_MEMINFO:-/proc/meminfo}" 2>/dev/null || echo 0
}

# Total swap in MB, EXCLUDING zram devices.
#
# zram swap is compressed RAM: it consumes the very resource that is already
# exhausted and does nothing for a build OOM. Counting it would let a
# zram-enabled image decide it has enough swap and then fail exactly as before.
lm_total_swap_mb() {
    awk 'NR > 1 && $1 !~ /^\/dev\/zram/ {total += $3} END {printf "%d\n", total / 1024}' \
        "${LM_SWAPS:-/proc/swaps}" 2>/dev/null || echo 0
}

# lm_build_jobs <ram_mb> <cores> -> max(1, min(cores, ram_mb / LM_MB_PER_JOB))
#
# Computed from RAM alone and never RAM+swap: handing out extra jobs because
# swap exists just guarantees SD-card thrash, which is far slower than
# compiling serially.
lm_build_jobs() {
    local ram_mb="${1:-0}" cores="${2:-1}" jobs
    local per_job="${LM_MB_PER_JOB:-768}"
    if [ "$cores" -lt 1 ]; then
        cores=1
    fi
    jobs=$(( ram_mb / per_job ))
    if [ "$jobs" -lt 1 ]; then
        jobs=1
    fi
    if [ "$jobs" -gt "$cores" ]; then
        jobs="$cores"
    fi
    echo "$jobs"
}

# lm_swap_needed_mb <ram_mb> <existing_swap_mb> -> swapfile size in MB, or 0.
#
# Brings RAM + real swap up to LM_SWAP_TARGET_MB, capped at LM_SWAP_MAX_MB and
# rounded up to a 256MB multiple. Machines with enough memory get 0 and are
# left completely untouched.
lm_swap_needed_mb() {
    local ram_mb="${1:-0}" swap_mb="${2:-0}" needed
    local target="${LM_SWAP_TARGET_MB:-3072}" max="${LM_SWAP_MAX_MB:-2048}"
    needed=$(( target - ram_mb - swap_mb ))
    if [ "$needed" -le 0 ]; then
        echo 0
        return 0
    fi
    if [ "$needed" -gt "$max" ]; then
        needed="$max"
    fi
    echo $(( ( (needed + 255) / 256 ) * 256 ))
}

# lm_build_failed_on_oom <build_output_file> -> 0 if the build was OOM-killed.
#
# Two independent evidence sources, because neither alone is reliable: the
# compiler sometimes reports its own allocation failure, but when the kernel
# OOM killer fires it writes nothing to the build's stdout. That silence is
# exactly why the old handler misdiagnosed this as missing build tools.
lm_build_failed_on_oom() {
    local build_output="${1:-}" kernel_log=""

    if [ -n "$build_output" ] && [ -f "$build_output" ]; then
        if grep -qiE 'cc1plus: out of memory|virtual memory exhausted|Cannot allocate memory|MemoryError|fatal error: Killed signal terminated program|signal 9' \
            "$build_output"; then
            return 0
        fi
    fi

    # LM_KERNEL_LOG_FILE lets tests supply a fixture instead of the real kernel
    # ring buffer, which on a shared CI machine may hold unrelated OOM events.
    if [ -n "${LM_KERNEL_LOG_FILE:-}" ]; then
        if [ -f "$LM_KERNEL_LOG_FILE" ]; then
            kernel_log=$(cat "$LM_KERNEL_LOG_FILE" 2>/dev/null || true)
        fi
    elif command -v dmesg >/dev/null 2>&1; then
        kernel_log=$(dmesg -T 2>/dev/null || dmesg 2>/dev/null || true)
    fi
    if [ -z "$kernel_log" ] && [ -z "${LM_KERNEL_LOG_FILE:-}" ] && command -v journalctl >/dev/null 2>&1; then
        kernel_log=$(journalctl -k --since "30 min ago" --no-pager 2>/dev/null || true)
    fi

    if [ -n "$kernel_log" ]; then
        if printf '%s\n' "$kernel_log" | tail -n 300 | \
            grep -qiE 'Out of memory: Kill|oom_kill|oom-kill|Killed process'; then
            return 0
        fi
    fi

    return 1
}

# lm_disk_backed_tmpdir [candidate] -> a disk-backed temp dir, or nothing.
#
# pip builds in $TMPDIR. Debian 13 mounts /tmp as tmpfs, so the default puts the
# whole C++ build tree in RAM, competing with the compiler we are already trying
# to keep under the limit. Prints a replacement only when the current TMPDIR is
# memory-backed and the candidate is not; otherwise prints nothing and the
# caller keeps its default.
lm_disk_backed_tmpdir() {
    local candidate="${1:-/var/tmp}"
    local current="${TMPDIR:-/tmp}"
    local current_fs="" candidate_fs=""

    current_fs=$(lm_fstype_of "$current")
    case "$current_fs" in
        tmpfs|ramfs) ;;
        *) return 0 ;;
    esac

    candidate_fs=$(lm_fstype_of "$candidate")
    case "$candidate_fs" in
        tmpfs|ramfs|"") return 0 ;;
    esac

    echo "$candidate"
}

# Filesystem type backing a path, or empty if it cannot be determined.
lm_fstype_of() {
    local path="${1:-/}"
    if command -v findmnt >/dev/null 2>&1; then
        findmnt -no FSTYPE --target "$path" 2>/dev/null | head -n 1
        return 0
    fi
    if command -v stat >/dev/null 2>&1; then
        stat -f -c %T "$path" 2>/dev/null | head -n 1
        return 0
    fi
    return 0
}

# ---------------------------------------------------------------------------
# Swap management (requires root; not unit-tested)
# ---------------------------------------------------------------------------

# lm_ensure_build_swap <needed_mb>
#
# Always returns 0. On any refusal it sets LM_SWAP_SKIP_REASON and leaves the
# system untouched -- swap is a safety net for the build, never a precondition.
lm_ensure_build_swap() {
    local needed_mb="${1:-0}"
    local swap_dir free_mb budget

    LM_SWAP_SKIP_REASON=""

    if [ "$needed_mb" -le 0 ]; then
        LM_SWAP_SKIP_REASON="not needed (RAM and existing swap are sufficient)"
        return 0
    fi

    if ! command -v mkswap >/dev/null 2>&1 || ! command -v swapon >/dev/null 2>&1; then
        LM_SWAP_SKIP_REASON="mkswap/swapon are not available on this system"
        echo "⚠ Cannot add build swap: $LM_SWAP_SKIP_REASON"
        return 0
    fi

    # Clear a stale swapfile left by a run that was killed before its cleanup
    # ran, so this is safe to call repeatedly.
    if [ -e "$LM_SWAPFILE" ]; then
        echo "Removing a leftover swapfile from a previous run: $LM_SWAPFILE"
        swapoff "$LM_SWAPFILE" >/dev/null 2>&1 || true
        rm -f "$LM_SWAPFILE" || true
    fi

    # Keep a working margin for the build tree itself; never eat the last GB.
    swap_dir=$(dirname "$LM_SWAPFILE")
    free_mb=$(df -m "$swap_dir" 2>/dev/null | awk 'NR==2{print $4}')
    free_mb=${free_mb:-0}
    budget=$(( free_mb - 1024 ))
    if [ "$budget" -lt 256 ]; then
        LM_SWAP_SKIP_REASON="only ${free_mb}MB free on ${swap_dir}, need about $(( needed_mb + 1024 ))MB"
        echo "⚠ Skipping the build swapfile: $LM_SWAP_SKIP_REASON"
        return 0
    fi
    if [ "$needed_mb" -gt "$budget" ]; then
        echo "⚠ Trimming the build swapfile from ${needed_mb}MB to leave 1GB free on ${swap_dir}"
        needed_mb=$(( ( budget / 256 ) * 256 ))
    fi

    echo "Adding a temporary ${needed_mb}MB swapfile for the build: $LM_SWAPFILE"
    echo "  This is removed automatically once the build finishes."

    # fallocate can produce a sparse file that mkswap rejects, and is not
    # supported on every filesystem; dd always yields a usable file.
    if ! fallocate -l "${needed_mb}M" "$LM_SWAPFILE" 2>/dev/null; then
        if ! dd if=/dev/zero of="$LM_SWAPFILE" bs=1M count="$needed_mb" status=none 2>/dev/null; then
            LM_SWAP_SKIP_REASON="could not allocate ${needed_mb}MB at $LM_SWAPFILE"
            echo "⚠ $LM_SWAP_SKIP_REASON"
            rm -f "$LM_SWAPFILE" || true
            return 0
        fi
    fi

    chmod 600 "$LM_SWAPFILE" || true

    if ! mkswap "$LM_SWAPFILE" >/dev/null 2>&1; then
        LM_SWAP_SKIP_REASON="mkswap failed on $LM_SWAPFILE"
        echo "⚠ $LM_SWAP_SKIP_REASON"
        rm -f "$LM_SWAPFILE" || true
        return 0
    fi

    if ! swapon "$LM_SWAPFILE" >/dev/null 2>&1; then
        LM_SWAP_SKIP_REASON="swapon failed on $LM_SWAPFILE"
        echo "⚠ $LM_SWAP_SKIP_REASON"
        rm -f "$LM_SWAPFILE" || true
        return 0
    fi

    LM_TEMP_SWAP_ACTIVE=1
    echo "✓ Temporary build swap active (${needed_mb}MB; total swap is now $(lm_total_swap_mb)MB)"
    return 0
}

# Remove the temporary swapfile. Safe to call unconditionally and repeatedly.
#
# Wired up as an EXIT trap, so it must never return non-zero -- a failing trap
# would surface as a spurious installer error.
lm_remove_build_swap() {
    if [ "${LM_TEMP_SWAP_ACTIVE:-0}" != "1" ]; then
        return 0
    fi
    LM_TEMP_SWAP_ACTIVE=0
    echo "Removing the temporary build swapfile: $LM_SWAPFILE"
    swapoff "$LM_SWAPFILE" >/dev/null 2>&1 || true
    rm -f "$LM_SWAPFILE" || true
    return 0
}
