"""An installed unit that no longer matches the repo's must be reported.

Nothing re-applies systemd units after the first install. `git pull` -- what
the web UI's update button runs -- brings a new template into the checkout, but
no code in web_interface/ or src/ copies it to /etc/systemd/system or runs
`systemctl daemon-reload`. The unit that actually runs is whatever
first_time_install.sh wrote on day one.

So every hardening added to a unit is inert on existing installs. Measured on a
live rig: the installed unit was dated 2026-08-06 and the repo's 2026-08-19,
and they differed -- with the result that a MemoryMax=85% present in the repo's
template was not being enforced at all. `systemctl show` reported
MemoryMax=infinity.

This is a warning, not an error, and deliberately not a silent rewrite:
editing files under /etc and restarting services is the installer's job, not
something a display process should do to a machine while it boots.
"""
import logging
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.startup_validator import StartupValidator


@pytest.fixture
def validator():
    v = StartupValidator(config_manager=MagicMock())
    v.logger = logging.getLogger("test")
    v.warnings = []
    v.errors = []
    return v


def test_a_matching_unit_produces_no_warning(validator, tmp_path):
    """The installed unit, substituted exactly as the installer would."""
    project_root = Path("src/startup_validator.py").resolve().parent.parent
    template_rel = "systemd/ledmatrix.service"
    template = project_root / template_rel
    if not template.is_file():
        pytest.skip("repo unit template not present")

    installed = tmp_path / "ledmatrix.service"
    installed.write_text(
        template.read_text(encoding="utf-8")
        .replace("__PROJECT_ROOT_DIR__", str(project_root))
        .replace("__USER__", "root"),
        encoding="utf-8")

    validator._UNITS = ((template_rel, str(installed)),)
    validator._validate_systemd_units()
    assert not validator.warnings, f"a matching unit warned: {validator.warnings}"
    assert not validator.errors


def test_comments_and_blank_lines_are_not_drift():
    """Otherwise every comment the repo adds would look like a changed unit."""
    a = "[Service]\n# explains a setting\nExecStart=/x\nRestart=always\n"
    b = "[Service]\nExecStart=/x\n\nRestart=always\n"
    assert StartupValidator._unit_body(a) == StartupValidator._unit_body(b)


def test_a_changed_directive_is_drift():
    a = "[Service]\nExecStart=/x\nMemoryMax=85%\n"
    b = "[Service]\nExecStart=/x\n"
    assert StartupValidator._unit_body(a) != StartupValidator._unit_body(b)


def test_reordered_directives_are_drift():
    """Order is not noise in a systemd unit.

    Repeated directives -- ExecStartPre=, ExecStartPost= -- run in the order
    they appear, and a directive that moves between [Unit], [Service] and
    [Install] means something different, or nothing, where it lands. This
    check used to sort the lines before comparing, which reported no drift for
    a unit that had genuinely changed.
    """
    a = "[Service]\nExecStartPre=/first\nExecStartPre=/second\n"
    b = "[Service]\nExecStartPre=/second\nExecStartPre=/first\n"
    assert StartupValidator._unit_body(a) != StartupValidator._unit_body(b), (
        "swapping two ExecStartPre= lines changes what runs first, and was "
        "being normalised away")


def test_a_directive_moved_between_sections_is_drift():
    a = "[Unit]\nDescription=x\n[Service]\nExecStart=/x\n"
    b = "[Unit]\nDescription=x\nExecStart=/x\n[Service]\n"
    assert StartupValidator._unit_body(a) != StartupValidator._unit_body(b), (
        "ExecStart= in [Unit] is not the same unit, and sorting hid it")


def test_cosmetic_differences_do_not_warn(validator, tmp_path):
    """Through the real comparison, not the helper.

    The repo's template carries explanatory comments the installed copy may not
    have, and the installer does not preserve ordering or blank lines. If those
    counted as drift, every boot would warn and the warning would be ignored.
    Asserting this on _unit_body alone would not catch a comparison that stopped
    calling it -- which is exactly what a careless edit does.
    """
    project_root = Path("src/startup_validator.py").resolve().parent.parent
    template_rel = "systemd/ledmatrix.service"
    template = project_root / template_rel
    if not template.is_file():
        pytest.skip("repo unit template not present")

    substituted = (template.read_text(encoding="utf-8")
                   .replace("__PROJECT_ROOT_DIR__", str(project_root))
                   .replace("__USER__", "root"))
    # Cosmetic means comments, blank lines and stray indentation -- the things
    # the installer really does drop. Not reordering: that changes the unit,
    # and is asserted as drift above.
    directives = [line.strip() for line in substituted.splitlines()
                  if line.strip() and not line.strip().startswith("#")]
    installed = tmp_path / "ledmatrix.service"
    installed.write_text(
        "\n\n".join("   " + d for d in directives) + "\n", encoding="utf-8")

    validator._UNITS = ((template_rel, str(installed)),)
    validator._validate_systemd_units()
    assert not validator.warnings, (
        f"cosmetic-only difference reported as drift: {validator.warnings}")


def test_drift_is_reported_as_a_warning(validator, tmp_path):
    """The whole point: a real difference must surface, and only as a warning."""
    installed = tmp_path / "ledmatrix.service"
    installed.write_text("[Service]\nExecStart=/usr/bin/python3 /x/run.py\n")

    project_root = Path("src/startup_validator.py").resolve().parent.parent
    template_rel = "systemd/ledmatrix.service"
    template = project_root / template_rel
    if not template.is_file():
        pytest.skip("repo unit template not present")

    validator._UNITS = ((template_rel, str(installed)),)
    validator._validate_systemd_units()

    assert validator.warnings, "a differing unit produced no warning"
    assert "install_service.sh" in validator.warnings[0], (
        "the warning does not tell the user how to fix it")
    assert not validator.errors, "drift must not be fatal at startup"


def test_a_missing_installed_unit_is_silent(validator, tmp_path):
    """Development checkouts have no /etc/systemd unit; that is not drift."""
    validator._UNITS = (("systemd/ledmatrix.service", str(tmp_path / "absent.service")),)
    validator._validate_systemd_units()
    assert not validator.warnings
    assert not validator.errors


# --- the web unit: one template, three copies, and a warning that never cleared ---
#
# install_service.sh and install_web_service.sh each carried their own inline
# heredoc of ledmatrix-web.service. install_service.sh's had drifted -- no
# Wants=network-online.target, RestartSec, SyslogIdentifier or CacheDirectory --
# and that is what was installed on real rigs. The validator correctly reported
# the drift and told the user to re-run install_service.sh, which reinstalled the
# same stale copy, so the warning could never clear. Separately, the template
# hardcoded User=root while the installers write whoever ran them, so even the
# *correct* installer produced a permanent warning on any non-root install.


def _render(template_text, project_root, user):
    """Exactly what the installers' sed does."""
    return (template_text
            .replace("__PROJECT_ROOT_DIR__", str(project_root))
            .replace("__USER__", user))


def test_the_web_unit_installed_as_a_non_root_user_is_not_drift(validator, tmp_path):
    """The web interface runs as whoever installed it, not as root.

    This is the case that warned forever: nothing the user could do would make
    an installed `User=pi` match a template that said `User=root`.
    """
    project_root = Path("src/startup_validator.py").resolve().parent.parent
    template_rel = "systemd/ledmatrix-web.service"
    template = project_root / template_rel
    if not template.is_file():
        pytest.skip("repo unit template not present")

    installed = tmp_path / "ledmatrix-web.service"
    installed.write_text(
        _render(template.read_text(encoding="utf-8"), project_root, "hdpi"),
        encoding="utf-8")

    validator._UNITS = ((template_rel, str(installed)),)
    validator._validate_systemd_units()
    assert not validator.warnings, (
        f"a correctly installed non-root web unit warned: {validator.warnings}")


def test_the_web_unit_still_reports_a_real_changed_directive(validator, tmp_path):
    """Ignoring User= must not make the check blind to everything else."""
    project_root = Path("src/startup_validator.py").resolve().parent.parent
    template_rel = "systemd/ledmatrix-web.service"
    template = project_root / template_rel
    if not template.is_file():
        pytest.skip("repo unit template not present")

    rendered = _render(template.read_text(encoding="utf-8"), project_root, "hdpi")
    # Drop RestartSec -- one of the directives the stale heredoc was missing.
    stale = "\n".join(line for line in rendered.splitlines() if not line.startswith("RestartSec="))
    installed = tmp_path / "ledmatrix-web.service"
    installed.write_text(stale + "\n", encoding="utf-8")

    validator._UNITS = ((template_rel, str(installed)),)
    validator._validate_systemd_units()
    assert validator.warnings, "a web unit missing RestartSec= produced no warning"
    assert not validator.errors


def test_installed_user_falls_back_to_root():
    """systemd defaults a system unit with no User= to root, so we must too."""
    assert StartupValidator._installed_user("[Service]\nExecStart=/x\n") == "root"
    assert StartupValidator._installed_user("[Service]\nUser=pi\n") == "pi"
    assert StartupValidator._installed_user("[Service]\n  User=hdpi  \n") == "hdpi"


def test_no_installer_carries_its_own_copy_of_a_unit():
    """The regression guard.

    Both installers used to inline the unit as a heredoc, and the two copies
    drifted from the template and from each other. A unit body in a shell script
    is the bug, so assert there isn't one rather than asserting the current
    contents match -- matching contents is exactly what silently stops being
    true.
    """
    project_root = Path("src/startup_validator.py").resolve().parent.parent
    offenders = []
    for script in sorted((project_root / "scripts" / "install").glob("*.sh")):
        text = script.read_text(encoding="utf-8", errors="replace")
        if "[Unit]" in text and "Description=" in text:
            offenders.append(script.name)
    assert not offenders, (
        f"{offenders} contain an inline systemd unit; render "
        f"systemd/*.service instead so there is one source of truth")
