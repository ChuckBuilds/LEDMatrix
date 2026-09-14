"""The two installers that write /etc/sudoers.d/ledmatrix_web must agree.

first_time_install.sh (Step 10, a heredoc) and scripts/install/configure_web_sudo.sh
(a block of echo lines) each generate the web user's sudo allow-list. They
drifted: configure_web_sudo.sh granted scripts/fix_perms/safe_pip_install.sh
but first_time_install.sh did not, so on a device set up only by the first-time
installer permission_utils.install_requirements_file could not use the root
wrapper and fell back to a user-level install that root-run ledmatrix.service
may not see (and the auto-update rollback reported its reinstall as failed).

This compares the granted command sets after normalising the spellings that
differ between the files but expand identically at install time:
$WEB_USER/$ACTUAL_USER, $PROJECT_ROOT/$PROJECT_ROOT_DIR, and the helper-path
variables configure_web_sudo.sh defines ($SAFE_RM_PATH, ...).

It also checks that every fix_perms helper granted via sudo is hardened to
root:root in both scripts -- and, in first_time_install.sh, after Step 11's
project-wide chown to the user, which would otherwise undo it.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FIRST_TIME = ROOT / "first_time_install.sh"
CONFIGURE = ROOT / "scripts" / "install" / "configure_web_sudo.sh"

#: Grants that intentionally exist in only one installer, as normalised
#: commands. There are none today; add one here with a reason rather than
#: loosening the comparison.
ONLY_IN_FIRST_TIME = frozenset()
ONLY_IN_CONFIGURE = frozenset()


def _text(path):
    return path.read_text(encoding="utf-8", errors="replace")


def _web_sudoers_section(path):
    """The part of the script that writes the ledmatrix_web allow-list.

    first_time_install.sh also writes other files later (WiFi permissions are
    delegated to a separate script, but keep this robust against future
    additions), so restrict it to Step 10.
    """
    text = _text(path)
    if path == FIRST_TIME:
        start = text.index('CURRENT_STEP="Configure passwordless sudo access"')
        end = text.index('CURRENT_STEP="Configure WiFi management permissions"')
        return text[start:end]
    return text


def _variables(text):
    """Simple NAME="..." assignments, so $SAFE_RM_PATH can be expanded."""
    return {m.group(1): m.group(2)
            for m in re.finditer(r'^\s*([A-Z_]+)="([^"$]*\$[^"]*)"\s*$', text, re.M)}


def _normalise(command, variables):
    for _ in range(3):  # helper paths reference $PROJECT_ROOT
        command = re.sub(r"\$\{?([A-Z][A-Z0-9_]*)\}?",
                         lambda m: variables.get(m.group(1), m.group(0)), command)
    command = command.replace("$PROJECT_ROOT_DIR", "$PROJECT_ROOT")
    return " ".join(command.split())


def _grants(path):
    """{(tags, command)} for every ledmatrix_web rule the script writes."""
    section = _web_sudoers_section(path)
    variables = _variables(_text(path))
    grants = set()
    for line in section.splitlines():
        m = re.search(r'\$(?:WEB_USER|ACTUAL_USER) ALL=\(ALL\) (NOPASSWD:(?:NOEXEC:)?)\s*(.*)$',
                      line)
        if not m:
            continue
        command = m.group(2).rstrip().rstrip('"').rstrip()
        grants.add((m.group(1), _normalise(command, variables)))
    return grants


def test_both_installers_generate_rules():
    # Guards against the parser silently matching nothing in either file.
    assert len(_grants(FIRST_TIME)) >= 15
    assert len(_grants(CONFIGURE)) >= 15


def test_installers_grant_the_same_commands():
    first = _grants(FIRST_TIME)
    configure = _grants(CONFIGURE)
    only_first = {c for c in first - configure if c[1] not in ONLY_IN_FIRST_TIME}
    only_configure = {c for c in configure - first if c[1] not in ONLY_IN_CONFIGURE}
    assert not only_first and not only_configure, (
        "ledmatrix_web sudoers drift between installers:\n"
        f"  only in first_time_install.sh: {sorted(only_first)}\n"
        f"  only in configure_web_sudo.sh: {sorted(only_configure)}")


def test_pip_install_helper_is_granted():
    wanted = ("NOPASSWD:", "$BASH_PATH $PROJECT_ROOT/scripts/fix_perms/safe_pip_install.sh *")
    assert wanted in _grants(FIRST_TIME)
    assert wanted in _grants(CONFIGURE)


def _granted_helpers():
    helpers = set()
    for _, command in _grants(FIRST_TIME) | _grants(CONFIGURE):
        m = re.search(r"scripts/fix_perms/([\w.-]+\.sh)", command)
        if m:
            helpers.add(m.group(1))
    return helpers


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
