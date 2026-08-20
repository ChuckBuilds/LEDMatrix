"""The captive portal's fixed-argument sudo calls must be granted.

The installers write two allow-lists, /etc/sudoers.d/ledmatrix_web and
ledmatrix_wifi. A sudo call absent from both needs a password, which a service
cannot supply, so it fails.

Four such calls were ungranted, all of them captive-portal teardown/setup:

    sysctl -w net.ipv4.ip_forward=0|1     wifi_manager.py:788, 883
    nft add|delete table ip ledmatrix     wifi_manager.py:835, 895
    rfkill unblock wifi                   wifi_manager.py:1811
    mkdir -p .../dnsmasq-shared.d         wifi_manager.py:922

It goes unnoticed because a stock Raspberry Pi image ships
/etc/sudoers.d/010_pi-nopasswd granting the default user
`ALL=(ALL) NOPASSWD: ALL`, which satisfies every gap in both files. It only
bites once that blanket rule is removed or the service runs as another user.

Scope, deliberately narrow: this pins the four commands above, each of which
can be written out literally. The portal makes further sudo calls whose
arguments are built at runtime -- iptables and nft rules carrying an interface
name and a port, `ip addr`, `ip link` -- and those cannot be granted safely
here. A rule covering them needs a trailing wildcard, and
`iptables --modprobe=/path/to/anything` runs that path as root, so
`NOPASSWD: iptables *` is a root shell for the web user by another name.
Closing that half needs a privileged helper that builds the rules itself and
takes only an interface and a port, granted the way safe_plugin_rm.sh already
is. That is a design decision, not a one-line grant, and belongs in its own
change.
"""
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
INSTALLERS = (
    ROOT / "first_time_install.sh",
    ROOT / "scripts" / "install" / "configure_wifi_permissions.sh",
)

#: Commands this change grants, each fully literal in the source.
REQUIRED = (
    ("sysctl", "-w", "net.ipv4.ip_forward=0"),
    ("sysctl", "-w", "net.ipv4.ip_forward=1"),
    ("nft", "add", "table", "ip", "ledmatrix"),
    ("nft", "delete", "table", "ip", "ledmatrix"),
    ("rfkill", "unblock", "wifi"),
    ("mkdir", "-p", "/etc/NetworkManager/dnsmasq-shared.d"),
)

#: Tools with an option that executes a program of the caller's choosing.
#: A trailing wildcard on any of these is a privilege escalation.
EXEC_CAPABLE = ("iptables", "ip6tables", "nft", "tcpdump", "find", "awk",
                "sed", "perl", "python", "python3", "env")


def _grant_lines():
    lines = []
    for installer in INSTALLERS:
        if not installer.is_file():
            continue
        for line in installer.read_text(encoding="utf-8", errors="replace").splitlines():
            if "NOPASSWD:" in line:
                lines.append(line.split("NOPASSWD:", 1)[1])
    return lines


def _normalised_grants():
    """Grants with binary-path variables reduced to tool names.

    Rules are written as `$SYSCTL_PATH -w ...`, so matching the literal
    "sysctl" finds nothing and every rule looks absent -- which is exactly how
    an earlier version of this test reported six gaps that did not exist.
    Only NOPASSWD lines are considered, because taking the whole script let a
    variable definition such as NFT_PATH=$(command -v nft) satisfy the check on
    its own while the grant itself had been deleted.
    """
    text = "\n".join(_grant_lines())
    text = re.sub(r"\$\{?([A-Z][A-Z0-9_]*)_PATH\}?", lambda m: m.group(1).lower(), text)
    return re.sub(r"/usr/(?:s?bin)/", "", text)


def test_the_installers_are_present():
    missing = [str(p.relative_to(ROOT)) for p in INSTALLERS if not p.is_file()]
    assert not missing, f"installer(s) missing: {missing}"


@pytest.mark.parametrize("command", REQUIRED, ids=lambda c: " ".join(c))
def test_the_command_is_granted(command):
    """Whole command, not just the binary.

    Checking only the binary made this far weaker than it looked: with
    `sysctl` present anywhere, deleting the ip_forward=0 grant still passed,
    and the portal would then be unable to restore forwarding on teardown.
    """
    pattern = r"\s+".join(re.escape(word) for word in command)
    assert re.search(pattern, _normalised_grants()), (
        f"no installer grants `{' '.join(command)}`")


def test_no_wildcard_on_a_tool_that_can_exec():
    """`NOPASSWD: iptables *` hands the web user root.

    iptables --modprobe=/path runs that path as root. This caught a grant added
    in this very change, which is why it is here.
    """
    offenders = []
    for rule in _grant_lines():
        rule = rule.strip()
        if not rule.endswith("*"):
            continue
        haystack = rule.replace("_PATH", "").lower()
        for tool in EXEC_CAPABLE:
            if re.search(rf"(^|/|\s|\$){tool}(\s|$)", haystack):
                offenders.append(rule)
                break
    assert not offenders, (
        "wildcard grant on a tool that can execute another program:\n  "
        + "\n  ".join(offenders))
