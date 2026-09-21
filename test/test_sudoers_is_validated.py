"""The generated sudoers rules must parse before they reach /etc/sudoers.d.

A malformed drop-in there makes sudo refuse every command for every user. On a
headless Pi that is unrecoverable without pulling the SD card, so both
installers run `visudo -c` on the file they generated before installing it.

The render test also gives us the check neither installer had: that the rules
they actually emit are valid sudoers syntax on a real Linux box.
"""

import os
import shutil
import subprocess
import sys
import tempfile

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIRST_TIME = os.path.join(REPO_ROOT, "first_time_install.sh")
CONFIGURE = os.path.join(REPO_ROOT, "scripts", "install", "configure_web_sudo.sh")

VISUDO = shutil.which("visudo") or (
    "/usr/sbin/visudo" if os.path.exists("/usr/sbin/visudo") else None
)


def _read(path):
    with open(path, "r", encoding="utf-8") as handle:
        return handle.read()


def test_first_time_install_validates_before_installing():
    body = _read(FIRST_TIME)
    assert 'visudo -c -f "$SUDOERS_TMP"' in body
    install = body.index('cp "$SUDOERS_TMP" "$SUDOERS_FILE"')
    validate = body.index('visudo -c -f "$SUDOERS_TMP"')
    assert validate < install, "the rules must be checked before they are installed"


def test_the_install_is_gated_on_the_check():
    """Checking and then installing anyway would be worse than not checking."""
    body = _read(FIRST_TIME)
    assert "SUDOERS_VALID=0" in body
    gate = body.index('if [ "$SUDOERS_VALID" = "0" ]')
    install = body.index('cp "$SUDOERS_TMP" "$SUDOERS_FILE"')
    assert gate < install


def test_first_time_install_does_not_use_a_predictable_temp_file():
    body = _read(FIRST_TIME)
    assert "mktemp" in body
    assert "> /tmp/ledmatrix_web_sudoers" not in body
    assert ">> /tmp/ledmatrix_web_sudoers" not in body


def test_configure_web_sudo_validates_before_installing():
    body = _read(CONFIGURE)
    assert 'visudo -c -f "$TEMP_SUDOERS"' in body
    install = body.index('cp "$TEMP_SUDOERS" /etc/sudoers.d/ledmatrix_web')
    validate = body.index('visudo -c -f "$TEMP_SUDOERS"')
    assert validate < install, "the rules must be checked before they are installed"


def _render_first_time_sudoers(project_root, user):
    """Run the installer's own sudoers heredoc with realistic values."""
    body = _read(FIRST_TIME)
    start = body.index("# Create sudoers content")
    end = body.index("# Never install rules we have not parsed.")
    block = body[start:end]
    out = os.path.join(project_root, "rendered")
    script = "\n".join(
        [
            "set -euo pipefail",
            f"ACTUAL_USER={user}",
            f"PROJECT_ROOT_DIR={project_root}",
            'SUDOERS_TMP="$(mktemp)"',
            "PYTHON_PATH=$(which python3)",
            "SYSTEMCTL_PATH=/usr/bin/systemctl",
            "REBOOT_PATH=/usr/sbin/reboot",
            "POWEROFF_PATH=/usr/sbin/poweroff",
            "BASH_PATH=$(which bash)",
            "JOURNALCTL_PATH=/usr/bin/journalctl",
            block,
            f'cp "$SUDOERS_TMP" {out}',
        ]
    )
    subprocess.run(["bash", "-c", script], check=True)
    return out


@pytest.mark.skipif(sys.platform == "win32", reason="visudo is POSIX only")
@pytest.mark.skipif(VISUDO is None, reason="visudo not installed")
def test_the_rules_the_installer_emits_actually_parse():
    with tempfile.TemporaryDirectory() as tmp:
        rendered = _render_first_time_sudoers(tmp, "ledmatrix")
        os.chmod(rendered, 0o440)
        result = subprocess.run(
            [VISUDO, "-c", "-f", rendered], capture_output=True, text=True
        )
        assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.skipif(sys.platform == "win32", reason="visudo is POSIX only")
@pytest.mark.skipif(VISUDO is None, reason="visudo not installed")
def test_a_broken_rule_is_caught_rather_than_installed():
    """The guard is only worth having if visudo rejects what it should."""
    with tempfile.TemporaryDirectory() as tmp:
        rendered = _render_first_time_sudoers(tmp, "ledmatrix")
        with open(rendered, "r", encoding="utf-8") as handle:
            good = handle.read()
        broken = os.path.join(tmp, "broken")
        with open(broken, "w", encoding="utf-8") as handle:
            # An empty command path is what an unset $BASH_PATH would produce.
            handle.write(good + "\nledmatrix ALL=(ALL) NOPASSWD:\n")
        os.chmod(broken, 0o440)
        result = subprocess.run(
            [VISUDO, "-c", "-f", broken], capture_output=True, text=True
        )
        assert result.returncode != 0
