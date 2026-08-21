"""Wildcard grants to commands that start a pager must carry NOEXEC.

`journalctl` runs a pager when its output is a terminal, and from `less` a
`!sh` is a shell with the privileges journalctl was given. That is the standard
journalctl privilege escalation, and the installer's rules end in a wildcard:

    <user> ALL=(ALL) NOPASSWD: /usr/bin/journalctl -u ledmatrix *

The web interface always passes --no-pager -- both call sites do, in app.py and
api_v3.py -- so nothing the project runs needs the pager. But a sudoers rule
cannot require a flag that sits in the middle of the command line, and reasoning
about what a trailing `*` does or does not admit is exactly the kind of
subtlety that produces a hole.

sudo's NOEXEC tag stops the command executing another program at all, which
closes it without depending on that reasoning. It works by LD_PRELOAD, so it
applies to dynamically linked binaries; journalctl is one.

On a stock Raspberry Pi image none of this is reachable, because
/etc/sudoers.d/010_pi-nopasswd already grants the default user
`ALL=(ALL) NOPASSWD: ALL`. It matters on a hardened install, or where the
service runs as a user without that blanket rule.
"""
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
INSTALLERS = (
    ROOT / "first_time_install.sh",
    ROOT / "scripts" / "install" / "configure_wifi_permissions.sh",
    # Writes the same journalctl grants as first_time_install.sh. It was
    # missing here, and because of that this suite passed while three
    # ungranted wildcard rules sat in it.
    ROOT / "scripts" / "install" / "configure_web_sudo.sh",
)

#: Commands that will start another program of their own accord -- a pager, an
#: editor, a shell -- and so must not be granted the ability to do so.
SPAWNS_A_PROGRAM = ("journalctl", "systemctl", "less", "more", "man", "git")


def _grant_lines():
    lines = []
    for installer in INSTALLERS:
        if not installer.is_file():
            continue
        for line in installer.read_text(encoding="utf-8", errors="replace").splitlines():
            stripped = line.strip()
            if "NOPASSWD" not in stripped or stripped.startswith("#"):
                continue
            # Installers emit rules two ways: written literally into a heredoc,
            # or echoed into a file. An echoed rule ends in a quote, so the
            # trailing-wildcard check below would skip it and the rule would
            # never be examined at all.
            echoed = re.fullmatch(r"""echo\s+(['"])(.*)\1""", stripped)
            lines.append(echoed.group(2) if echoed else stripped)
    return lines


def test_the_installers_are_present():
    missing = [str(p.relative_to(ROOT)) for p in INSTALLERS if not p.is_file()]
    assert not missing, f"installer(s) missing: {missing}"


def test_wildcard_pager_grants_carry_noexec():
    offenders = []
    for rule in _grant_lines():
        command = rule.split("NOPASSWD", 1)[1]
        if not command.rstrip().endswith("*"):
            continue
        tool = command.replace("_PATH", "").replace("$", "").lower()
        for name in SPAWNS_A_PROGRAM:
            if re.search(rf"(^|/|\s){name}(\s|$)", tool):
                if "NOEXEC" not in rule:
                    offenders.append(rule)
                break
    assert not offenders, (
        "wildcard grant to a command that can start a pager or shell, without "
        "NOEXEC:\n  " + "\n  ".join(offenders))


def test_journalctl_is_granted_at_all():
    """Guard against 'fixing' the above by deleting the rules."""
    text = "\n".join(_grant_lines())
    assert "JOURNALCTL_PATH" in text or "journalctl" in text, (
        "no journalctl grant remains; the web interface reads logs through it")


@pytest.mark.parametrize("selector", ["-u ledmatrix.service", "-u ledmatrix",
                                      "-t ledmatrix"])
def test_each_journalctl_rule_is_tagged(selector):
    """Every selector, so removing one cannot pass by the others' presence."""
    matching = [r for r in _grant_lines()
                if "JOURNALCTL_PATH" in r and f"{selector} " in r]
    assert matching, f"no journalctl rule for {selector}"
    untagged = [r for r in matching if "NOEXEC" not in r]
    assert not untagged, f"untagged journalctl rule(s): {untagged}"
