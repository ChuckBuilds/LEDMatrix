"""One generator writes /etc/sudoers.d/ledmatrix_web, and both installers use it.

first_time_install.sh (Step 10) and scripts/install/configure_web_sudo.sh each
used to carry their own copy of the web user's sudo allow-list -- a heredoc in
one, a block of echo lines in the other -- and the copies drifted:
configure_web_sudo.sh granted scripts/fix_perms/safe_pip_install.sh but
first_time_install.sh did not, so on a device set up only by the first-time
installer permission_utils.install_requirements_file could not use the root
wrapper and fell back to a user-level install that root-run ledmatrix.service
may not see (and the auto-update rollback reported its reinstall as failed).

The rules now live once, in web_sudoers_rules() in
scripts/install/lib_sudoers.sh. What keeps them from drifting again:

* neither installer writes a rule line of its own, and each writes the
  generator's output to the very file it then validates and installs;
* each passes its variables to the generator in the right positions -- checked
  by running the installer's own call line with distinct values;
* the generator's grants are pinned to an explicit list below, so dropping,
  adding or re-pathing a grant is a deliberate edit to this file.

It also checks that every fix_perms helper granted via sudo is hardened to
root:root in both installers -- and, in first_time_install.sh, after Step 11's
project-wide chown to the user, which would otherwise undo it.
"""
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
FIRST_TIME = ROOT / "first_time_install.sh"
CONFIGURE = ROOT / "scripts" / "install" / "configure_web_sudo.sh"
LIB = ROOT / "scripts" / "install" / "lib_sudoers.sh"

#: Every grant web_sudoers_rules() writes, as (tags, command) with the
#: generator's own variable names. Changing the allow-list means changing this.
EXPECTED_GRANTS = frozenset({
    ("NOPASSWD:", "$REBOOT_PATH"),
    ("NOPASSWD:", "$POWEROFF_PATH"),
    ("NOPASSWD:", "$SYSTEMCTL_PATH start ledmatrix.service"),
    ("NOPASSWD:", "$SYSTEMCTL_PATH stop ledmatrix.service"),
    ("NOPASSWD:", "$SYSTEMCTL_PATH restart ledmatrix.service"),
    ("NOPASSWD:", "$SYSTEMCTL_PATH enable ledmatrix.service"),
    ("NOPASSWD:", "$SYSTEMCTL_PATH disable ledmatrix.service"),
    ("NOPASSWD:", "$SYSTEMCTL_PATH status ledmatrix.service"),
    ("NOPASSWD:", "$SYSTEMCTL_PATH is-active ledmatrix"),
    ("NOPASSWD:", "$SYSTEMCTL_PATH is-active ledmatrix.service"),
    ("NOPASSWD:", "$SYSTEMCTL_PATH start ledmatrix-web.service"),
    ("NOPASSWD:", "$SYSTEMCTL_PATH stop ledmatrix-web.service"),
    ("NOPASSWD:", "$SYSTEMCTL_PATH restart ledmatrix-web.service"),
    ("NOPASSWD:", "$BASH_PATH $PROJECT_ROOT/scripts/fix_perms/safe_plugin_rm.sh *"),
    ("NOPASSWD:", "$BASH_PATH $PROJECT_ROOT/scripts/fix_perms/safe_pip_install.sh *"),
    ("NOPASSWD:NOEXEC:", "$JOURNALCTL_PATH -u ledmatrix.service *"),
    ("NOPASSWD:NOEXEC:", "$JOURNALCTL_PATH -u ledmatrix *"),
    ("NOPASSWD:NOEXEC:", "$JOURNALCTL_PATH -t ledmatrix *"),
})

#: The call each installer makes: its own names for the generator's arguments,
#: in order, and the file it writes the rules to.
CALLERS = {
    FIRST_TIME: (("$ACTUAL_USER", "$PROJECT_ROOT_DIR", "$SYSTEMCTL_PATH", "$BASH_PATH",
                  "$REBOOT_PATH", "$POWEROFF_PATH", "$JOURNALCTL_PATH"), "$SUDOERS_TMP"),
    CONFIGURE: (("$WEB_USER", "$PROJECT_ROOT", "$SYSTEMCTL_PATH", "$BASH_PATH",
                 "$REBOOT_PATH", "$POWEROFF_PATH", "$JOURNALCTL_PATH"), "$TEMP_SUDOERS"),
}

RULE = re.compile(r'(\S+) ALL=\(ALL\) (NOPASSWD:(?:NOEXEC:)?)\s*(.*?)"?$')


def _text(path):
    return path.read_text(encoding="utf-8", errors="replace").replace("\r\n", "\n")


def _generator_grants():
    """{(tags, command)} for every rule line in lib_sudoers.sh."""
    grants = set()
    for line in _text(LIB).splitlines():
        m = RULE.search(line.strip())
        if m and m.group(1).endswith("$WEB_USER"):
            grants.add((m.group(2), " ".join(m.group(3).split())))
    return grants


def _call(path):
    """The installer's web_sudoers_rules statement, continuation lines joined."""
    text = _text(path)
    calls = re.findall(r"^[ \t]*web_sudoers_rules\b(?:[^\n]*\\\n)*[^\n]*$", text, re.M)
    assert len(calls) == 1, f"{path.name}: expected one web_sudoers_rules call, found {calls}"
    return calls[0]


def test_generator_grants_exactly_the_expected_rules():
    grants = _generator_grants()
    assert grants == EXPECTED_GRANTS, (
        f"lib_sudoers.sh grants changed:\n  added: {sorted(grants - EXPECTED_GRANTS)}\n"
        f"  removed: {sorted(EXPECTED_GRANTS - grants)}")


@pytest.mark.parametrize("installer", [FIRST_TIME, CONFIGURE], ids=lambda p: p.name)
def test_installer_writes_no_rules_of_its_own(installer):
    """A rule added to one installer only is how they drifted last time."""
    own = [line for line in _text(installer).splitlines()
           if "NOPASSWD" in line and not line.lstrip().startswith("#")]
    assert not own, f"{installer.name} writes sudoers rules itself: {own}"


@pytest.mark.parametrize("installer", [FIRST_TIME, CONFIGURE], ids=lambda p: p.name)
def test_installer_sources_the_generator_and_writes_what_it_validates(installer):
    text = _text(installer)
    assert "lib_sudoers.sh" in text, f"{installer.name} does not source lib_sudoers.sh"
    args, target = CALLERS[installer]
    call = _call(installer)
    words = call.replace("\\\n", " ").split()
    assert words[0] == "web_sudoers_rules"
    assert tuple(w.strip('"') for w in words[1:8]) == args, (
        f"{installer.name} passes the generator's arguments out of order: {call}")
    assert words[8:] == [">", f'"{target}"'], call
    # ...and that file is the one it runs visudo on.
    assert f'visudo -c -f "{target}"' in text


@pytest.mark.skipif(sys.platform == "win32" or shutil.which("bash") is None,
                    reason="needs a POSIX bash")
@pytest.mark.parametrize("installer", [FIRST_TIME, CONFIGURE], ids=lambda p: p.name)
def test_installer_call_renders_the_expected_rules(installer, tmp_path):
    """Run the installer's own call line, with a distinct value per argument."""
    args, target = CALLERS[installer]
    values = {
        args[0]: "webuser", args[1]: "/srv/led root", args[2]: "/x/systemctl",
        args[3]: "/x/bash", args[4]: "/x/reboot", args[5]: "/x/poweroff",
        args[6]: "/x/journalctl", target: str(tmp_path / "out"),
    }
    assigns = "\n".join(f"{name[1:]}='{value}'" for name, value in values.items())
    script = f"set -euo pipefail\n. '{LIB}'\n{assigns}\n{_call(installer)}\n"
    subprocess.run(["bash", "-c", script], check=True)
    rendered = set()
    for line in (tmp_path / "out").read_text(encoding="utf-8").splitlines():
        m = RULE.match(line)
        if m:
            assert m.group(1) == "webuser", line
            rendered.add((m.group(2), m.group(3)))
    subst = {"$SYSTEMCTL_PATH": "/x/systemctl", "$BASH_PATH": "/x/bash",
             "$REBOOT_PATH": "/x/reboot", "$POWEROFF_PATH": "/x/poweroff",
             "$JOURNALCTL_PATH": "/x/journalctl", "$PROJECT_ROOT": "/srv/led root"}
    expected = set()
    for tags, command in EXPECTED_GRANTS:
        for var, value in subst.items():
            command = command.replace(var, value)
        expected.add((tags, command))
    assert rendered == expected


@pytest.mark.skipif(sys.platform == "win32" or shutil.which("bash") is None,
                    reason="needs a POSIX bash")
def test_optional_tools_are_left_out_when_absent(tmp_path):
    """configure_web_sudo.sh passes "" for a missing reboot/poweroff/journalctl.

    An empty path would otherwise leave `user ALL=(ALL) NOPASSWD: ` behind,
    which visudo rejects, and the whole file would not be installed.
    """
    out = subprocess.run(
        ["bash", "-c", f". '{LIB}'; web_sudoers_rules u /p /bin/systemctl /bin/bash '' '' ''"],
        check=True, capture_output=True, text=True).stdout
    rules = [line for line in out.splitlines() if RULE.match(line)]
    assert len(rules) == len(EXPECTED_GRANTS) - 5
    assert not [r for r in rules if r.rstrip().endswith("NOPASSWD:")]
    assert "journalctl" not in out


def test_pip_install_helper_is_granted():
    wanted = ("NOPASSWD:", "$BASH_PATH $PROJECT_ROOT/scripts/fix_perms/safe_pip_install.sh *")
    assert wanted in _generator_grants()


def _granted_helpers():
    helpers = set()
    for _, command in _generator_grants():
        m = re.search(r"scripts/fix_perms/([\w.-]+\.sh)", command)
        if m:
            helpers.add(m.group(1))
    assert helpers, "no fix_perms helper grant found; the parser matched nothing"
    return helpers


def _variables(text):
    """Simple NAME="..." assignments, so $SAFE_RM_PATH can be expanded."""
    return {m.group(1): m.group(2)
            for m in re.finditer(r'^\s*([A-Z_]+)="([^"$]*\$[^"]*)"\s*$', text, re.M)}


def _normalise(command, variables):
    for _ in range(3):  # helper paths reference $PROJECT_ROOT
        command = re.sub(r"\$\{?([A-Z][A-Z0-9_]*)\}?",
                         lambda m: variables.get(m.group(1), m.group(0)), command)
    return " ".join(command.split())


def test_every_granted_helper_is_hardened_in_configure_web_sudo():
    text = _text(CONFIGURE)
    variables = _variables(text)
    hardened = {Path(_normalise(m.group(2), variables)).name
                for m in re.finditer(r"sudo (chown root:root|chmod 755) \"?([^\"\s]+)", text)
                if m.group(1).startswith("chown")}
    assert _granted_helpers() <= hardened, _granted_helpers() - hardened


def test_every_granted_helper_is_hardened_in_first_time_install_after_chown():
    text = _text(FIRST_TIME)
    project_chown = text.index('-exec chown -h "$ACTUAL_USER:$ACTUAL_USER"')
    loop = re.search(r"for helper in ([^;]+); do\n(.*?)\ndone", text, re.S)
    assert loop, "no helper-hardening loop in first_time_install.sh"
    assert "chown root:root" in loop.group(2) and "chmod 755" in loop.group(2)
    assert loop.start() > project_chown, (
        "helper hardening runs before Step 11's project-wide chown, which undoes it")
    assert _granted_helpers() <= set(loop.group(1).split())


def test_no_grant_runs_a_file_the_web_user_can_edit():
    """Every project file granted as root must be a fix_perms helper, which
    both installers chown root:root (checked above). Anything else under the
    project root is owned by the user after Step 11's chown, so a NOPASSWD
    rule for it lets the web user rewrite the file and run it as root. The
    grants for display_controller.py, start_display.sh and stop_display.sh
    were exactly that, and nothing ever ran them through sudo."""
    for _, command in _generator_grants():
        for token in command.split():
            if token.startswith("$PROJECT_ROOT/"):
                assert token.startswith("$PROJECT_ROOT/scripts/fix_perms/"), (
                    f"lib_sudoers.sh grants root on a user-owned file: {command}")
