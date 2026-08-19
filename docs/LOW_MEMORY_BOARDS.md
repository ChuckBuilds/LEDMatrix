# Running on Low-Memory Boards

Applies to the Pi Zero 2 W (512 MB), Pi 3 / 3B+ (1 GB), and the 1 GB Pi 4.
If your board has 2 GB or more you can skip this document.

## The failure this prevents

The display process is the largest thing on the board. On a 1 GB Pi 3B+ with
around 20 plugins enabled it settles near **600 MB of 905 MB usable**, leaving
under 200 MB of headroom for everything else.

When that headroom runs out, the board does not crash cleanly. `fork()` starts
failing, and because a new process is needed to do almost anything, the
symptoms look nothing like "out of memory":

| What you see | Why |
|---|---|
| SSH accepts the connection then closes it instantly, before any banner | `sshd` forks a session per connection; the fork fails |
| The web UI still responds quickly | Already running, serves from existing threads, forks nothing |
| Ping is perfect, 0% loss | Handled entirely in the kernel |
| The panel is dark | The display process was killed and cannot be respawned |
| The clock is wrong after the next boot | `fake-hwclock`'s periodic save is a scheduled job, and it cannot fork either |

The board looks healthy from the outside and cannot be logged into. Only a
power cycle clears it. If you are here because SSH stopped working, also see
[SSH_UNAVAILABLE_AFTER_INSTALL.md](SSH_UNAVAILABLE_AFTER_INSTALL.md), which
covers the more common cause (AP mode).

## Check your headroom

```bash
free -m
ps -eo rss,comm --sort=-rss | head -5
```

If `MemAvailable` is under ~150 MB while the display is running, you are close
to the edge. To watch it over time:

```bash
watch -n 30 'free -m | head -2'
```

Available memory that falls steadily rather than holding flat means you will
reach the wall; it is a question of when.

## What to do

**1. Enable the memory cgroup controller.** Without it, the `MemoryMax=85%` in
`systemd/ledmatrix.service` is accepted by systemd and silently ignored, so the
service has no ceiling and a runaway takes the whole board down instead of just
restarting. Raspberry Pi firmware disables this controller by default.

`first_time_install.sh` does this for you. To check it took effect:

```bash
grep memory /sys/fs/cgroup/cgroup.controllers
```

If that prints nothing, add `cgroup_enable=memory cgroup_memory=1` to the
kernel command line and reboot. Edit whichever file your image uses —
`/boot/firmware/cmdline.txt` on current Raspberry Pi OS, `/boot/cmdline.txt` on
older layouts (the installer checks the first and falls back to the second).
Everything must stay on a single line.

This changes the failure mode from "the board becomes unreachable" to "the
display service restarts". It is a safety net, not a fix.

**2. Run fewer plugins.** This is the actual remedy. Every enabled plugin costs
memory permanently — its module, its parsed config, and its cached API
responses. On a 512 MB or 1 GB board, keep the enabled set small and prefer
plugins that poll infrequently.

**3. Lower the cache ceiling.** The in-memory cache is sized from total RAM
(150 entries at 1 GB and below, up to 1500 at 8 GB). To go lower still:

```ini
# /etc/systemd/system/ledmatrix.service.d/override.conf
[Service]
Environment=LEDMATRIX_CACHE_MAX_ENTRIES=75
```

Writing the file does not change the running service. Reload systemd and
restart it:

```bash
sudo systemctl daemon-reload
sudo systemctl restart ledmatrix
```

Fewer entries means more API calls, so lower this only while you are actually
short of memory.

**4. Consider `MemoryHigh`.** `MemoryMax` kills and restarts. `MemoryHigh`
throttles and reclaims instead, which is gentler — but on a board where the
process genuinely wants more than the limit, sustained reclaim can stall the
render loop and show as visible stutter on the panel. Add it only if you prefer
degraded output to a restart:

```ini
[Service]
MemoryHigh=70%
```

## Keep your logs

These images default to volatile journald storage, so every reboot destroys the
logs — including the ones explaining why the board rebooted. `first_time_install.sh`
enables persistent storage capped at 64 MB. To confirm:

```bash
journalctl --list-boots
```

More than one boot listed means logs are surviving reboots. If only one is
listed, journald is still writing to `/run` (tmpfs).
