"""Guards that every privileged systemctl call the web interface makes is
covered by a passwordless-sudo grant in configure_web_sudo.sh.

The web interface runs headless (no TTY), so any `sudo` call that is not
matched by a NOPASSWD rule in /etc/sudoers.d/ledmatrix_web falls back to a
password prompt and fails with:

    sudo: a terminal is required to read the password

sudo matches the command line by exact string, so `systemctl start ledmatrix`
and `systemctl start ledmatrix.service` are NOT interchangeable. This test
parses both the production blueprint and the sudoers-generator script and
asserts the (verb, unit) pairs line up, catching the suffix-mismatch class of
bug before it ships.
"""

import ast
import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
API_V3 = PROJECT_ROOT / "web_interface" / "blueprints" / "api_v3.py"
SUDOERS_SCRIPT = PROJECT_ROOT / "scripts" / "install" / "configure_web_sudo.sh"


def _sudo_systemctl_calls(source: str) -> set[tuple[str, str]]:
    """Return (verb, unit) for every list literal beginning with
    ['sudo', 'systemctl', ...] passed to a subprocess call in the source."""
    calls: set[tuple[str, str]] = set()
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.List):
            continue
        elts = node.elts
        if len(elts) < 4:
            continue
        if not all(isinstance(e, ast.Constant) and isinstance(e.value, str) for e in elts[:4]):
            continue
        if elts[0].value == "sudo" and elts[1].value == "systemctl":
            calls.add((elts[2].value, elts[3].value))
    return calls


def _granted_systemctl_rules(script: str) -> set[tuple[str, str]]:
    """Return (verb, unit) for each `$SYSTEMCTL_PATH <verb> <unit>` NOPASSWD
    grant emitted by the sudoers-generator script."""
    rules: set[tuple[str, str]] = set()
    for match in re.finditer(r"\$SYSTEMCTL_PATH\s+(\S+)\s+(\S+)", script):
        verb, unit = match.group(1), match.group(2).rstrip('"')
        rules.add((verb, unit))
    return rules


def test_every_sudo_systemctl_call_is_granted() -> None:
    calls = _sudo_systemctl_calls(API_V3.read_text())
    rules = _granted_systemctl_rules(SUDOERS_SCRIPT.read_text())

    assert calls, "expected to find sudo systemctl calls in api_v3.py"

    uncovered = {c for c in calls if c not in rules}
    assert not uncovered, (
        "These sudo systemctl calls have no matching NOPASSWD grant in "
        "configure_web_sudo.sh; they will fail headless with "
        "'sudo: a terminal is required to read the password': "
        + ", ".join(f"systemctl {v} {u}" for v, u in sorted(uncovered))
    )


def test_units_are_fully_qualified() -> None:
    """Privileged systemctl calls must name the unit as <name>.service so they
    match the sudoers grants, which use the fully-qualified unit name."""
    calls = _sudo_systemctl_calls(API_V3.read_text())
    unqualified = {(v, u) for v, u in calls if not u.endswith(".service")}
    assert not unqualified, (
        "sudo systemctl calls must use fully-qualified .service unit names: "
        + ", ".join(f"systemctl {v} {u}" for v, u in sorted(unqualified))
    )
