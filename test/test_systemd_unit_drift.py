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


def test_reordered_directives_are_not_drift():
    """systemd does not care about order within a section, so neither should this."""
    a = "[Service]\nExecStart=/x\nRestart=always\n"
    b = "[Service]\nRestart=always\nExecStart=/x\n"
    assert StartupValidator._unit_body(a) == StartupValidator._unit_body(b)


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
    # Same directives, stripped of comments and blank lines and reordered.
    directives = sorted(line.strip() for line in substituted.splitlines()
                        if line.strip() and not line.strip().startswith("#"))
    installed = tmp_path / "ledmatrix.service"
    installed.write_text("\n".join(reversed(directives)) + "\n", encoding="utf-8")

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
