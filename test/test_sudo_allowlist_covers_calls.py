"""Every sudo the code runs must be granted by an installer allow-list.

The installers write two files -- /etc/sudoers.d/ledmatrix_web and
/etc/sudoers.d/ledmatrix_wifi -- each an explicit allow-list. Anything the code
calls with sudo that is not in one of them needs a password, which a service
cannot supply, so the call fails.

That failure is invisible on a stock Raspberry Pi image, because
/etc/sudoers.d/010_pi-nopasswd grants the default user

    <user> ALL=(ALL) NOPASSWD: ALL

which masks every gap in both files. It only surfaces on a system where that
blanket rule has been removed, or where the service runs as a different user --
so a missing entry can sit there for a long time before anyone hits it.

One was: wifi_manager turns IP forwarding on while the captive portal's access
point is up and restores it afterwards, calling `sudo sysctl -w
net.ipv4.ip_forward=...`. Neither allow-list granted sysctl. On such a system
clients would associate to the AP and then fail to route.
"""
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
INSTALLERS = (
    ROOT / "first_time_install.sh",
    ROOT / "scripts" / "install" / "configure_wifi_permissions.sh",
)
SOURCES = (ROOT / "src", ROOT / "web_interface")

# argv-style sudo calls: ["sudo", <binary-or-var>, "arg", ...]
CALL = re.compile(r"""\[\s*["']sudo["']\s*,\s*(?P<rest>[^\]]+)\]""")


def _first_two_args(rest):
    """The binary and its first argument, as written."""
    parts = [p.strip() for p in rest.split(",")]
    # Drop sudo's own flags: `sudo -n <tool>` is a call to <tool>, and
    # treating "-n" as the binary reports a gap that does not exist.
    flag = re.compile(r'[\'"]-[a-zA-Z]+[\'"]')
    while parts and flag.fullmatch(parts[0]):
        parts = parts[1:]
    # Always two entries: `["sudo", "reboot"]` has no subcommand, and the
    # caller unpacks a fixed pair.
    out = []
    for part in (parts + ["", ""])[:2]:
        if not part:
            out.append("")
            continue
        literal = re.fullmatch(r"""["'](.+)["']""", part)
        if literal:
            out.append(literal.group(1))
        else:
            # A variable such as sysctl_bin: reduce to the tool it resolves to.
            out.append(part.split(".")[-1].replace("_bin", "").replace("_path", ""))
    return out


def _sudo_calls():
    found = {}
    for base in SOURCES:
        for path in base.rglob("*.py"):
            text = path.read_text(encoding="utf-8", errors="replace")
            for match in CALL.finditer(text):
                args = _first_two_args(match.group("rest"))
                if not args:
                    continue
                line = text[:match.start()].count("\n") + 1
                found.setdefault(tuple(args), f"{path.relative_to(ROOT)}:{line}")
    return found


def _allowlisted_text():
    """The allow-list rules, with binary-path variables reduced to tool names.

    The installers write rules like `$SYSTEMCTL_PATH enable ledmatrix.service`,
    so matching on the literal "systemctl" finds nothing and every systemctl
    rule looks absent. Normalise $FOO_PATH and /usr/bin/foo down to foo before
    comparing, or the check reports gaps that are not there -- which it did on
    the first run.
    """
    # NOPASSWD lines only. Taking the whole script would let a variable
    # definition such as NFT_PATH=$(command -v nft) satisfy the check on its
    # own, which is how an earlier version of this test passed while the grant
    # itself had been deleted.
    lines = []
    for installer in INSTALLERS:
        if not installer.is_file():
            continue
        for line in installer.read_text(encoding="utf-8", errors="replace").splitlines():
            if "NOPASSWD:" in line:
                lines.append(line.split("NOPASSWD:", 1)[1])
    text = "\n".join(lines)
    text = re.sub(r"\$\{?([A-Z][A-Z0-9_]*)_PATH\}?",
                  lambda m: m.group(1).lower(), text)
    text = re.sub(r"/usr/(?:s?bin)/", "", text)
    return text


def test_the_installers_are_present():
    missing = [str(p.relative_to(ROOT)) for p in INSTALLERS if not p.is_file()]
    assert not missing, f"installer(s) missing: {missing}"


def test_every_sudo_call_is_granted():
    allow = _allowlisted_text()
    calls = _sudo_calls()
    assert calls, "no sudo calls found; the matcher has stopped working"

    ungranted = []
    for (binary, first_arg), where in sorted(calls.items()):
        # A rule mentions the tool and, where it takes a subcommand, that too.
        if binary not in allow:
            ungranted.append(f"{binary} ({where})")
            continue
        if first_arg and not first_arg.startswith("-"):
            pattern = rf"{re.escape(binary)}\s+{re.escape(first_arg)}"
            if binary in ("systemctl",) and not re.search(pattern, allow):
                ungranted.append(f"{binary} {first_arg} ({where})")

    assert not ungranted, (
        "these run under sudo but no installer grants them; on a stock Pi the "
        "blanket 010_pi-nopasswd rule hides this:\n  " + "\n  ".join(ungranted))


@pytest.mark.parametrize("needle", ["ip_forward"])
def test_the_captive_portal_forwarding_rule_is_granted(needle):
    """The specific gap this test was written for."""
    assert needle in _allowlisted_text(), (
        "no allow-list entry for sysctl ip_forward; the captive portal enables "
        "forwarding while its AP is up and cannot without one")
