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
WIFI = os.path.join(REPO_ROOT, "scripts", "install", "configure_wifi_permissions.sh")

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


def test_configure_web_sudo_does_not_use_a_predictable_temp_file():
    body = _read(CONFIGURE)
    assert 'TEMP_SUDOERS=$(mktemp' in body
    assert "/tmp/ledmatrix_web_sudoers_$$" not in body
    assert "trap 'rm -f \"$TEMP_SUDOERS\"' EXIT" in body


def test_configure_web_sudo_installs_mode_440():
    body = _read(CONFIGURE)
    install = body.index('cp "$TEMP_SUDOERS" /etc/sudoers.d/ledmatrix_web')
    assert body.index("chmod 440 /etc/sudoers.d/ledmatrix_web") > install


def test_configure_wifi_permissions_validates_before_installing():
    """The third sudoers writer. It installed its rules unchecked."""
    body = _read(WIFI)
    # The check itself, as a condition -- not merely the command appearing in
    # the error report that follows it.
    validate = body.index('if ! visudo -c -f "$TEMP_SUDOERS"')
    install = body.index('sudo cp "$TEMP_SUDOERS" "$SUDOERS_FILE"')
    assert validate < install, "the rules must be checked before they are installed"
    # ...and a failed check stops the script before the copy.
    assert "exit 1" in body[validate:install]
    assert "TEMP_SUDOERS=$(mktemp" in body


@pytest.mark.skipif(sys.platform == "win32", reason="visudo is POSIX only")
@pytest.mark.skipif(VISUDO is None, reason="visudo not installed")
def test_the_wifi_rules_actually_parse(tmp_path):
    """Render configure_wifi_permissions.sh's heredoc with realistic paths."""
    body = _read(WIFI)
    opener = 'cat > "$TEMP_SUDOERS" << EOF\n'
    start = body.index(opener) + len(opener)
    end = body.index("\nEOF\n", start)
    out = tmp_path / "wifi"
    script = "\n".join([
        "WEB_USER=ledmatrix", "NMCLI_PATH=/usr/bin/nmcli",
        "SYSTEMCTL_PATH=/usr/bin/systemctl", "SYSCTL_PATH=/usr/sbin/sysctl",
        "NFT_PATH=/usr/sbin/nft", "RFKILL_PATH=/usr/sbin/rfkill",
        "MKDIR_PATH=/usr/bin/mkdir",
        f"cat > '{out}' << EOF", body[start:end], "EOF",
    ])
    subprocess.run(["bash", "-c", script], check=True)
    os.chmod(out, 0o440)
    result = subprocess.run([VISUDO, "-c", "-f", str(out)], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr


def test_a_missing_rules_library_installs_nothing():
    """If lib_sudoers.sh is missing, nothing is generated -- and an empty file
    would pass `visudo -c` -- so that branch must set the flag the install is
    gated on."""
    body = _read(FIRST_TIME)
    missing = body.index('if [ -f "$SUDOERS_LIB" ]; then')
    flagged = body.index("SUDOERS_VALID=0", missing)
    validate = body.index('visudo -c -f "$SUDOERS_TMP"')
    install = body.index('cp "$SUDOERS_TMP" "$SUDOERS_FILE"')
    gate = body.rindex('if [ "$SUDOERS_VALID" = "0" ]; then', 0, install)
    assert missing < flagged < validate < gate < install


def _step10_generation(body):
    """first_time_install.sh's own Step 10 code that writes $SUDOERS_TMP."""
    start = body.index("# The rules themselves live in scripts/install/lib_sudoers.sh")
    end = body.index("# Never install rules we have not parsed.")
    return body[start:end]


def _run_step10_generation(project_root, user, out):
    """Run the installer's Step 10 generation with realistic values.

    Returns the SUDOERS_VALID it leaves behind."""
    script = "\n".join(
        [
            "set -Eeuo pipefail",
            f"ACTUAL_USER={user}",
            f"PROJECT_ROOT_DIR='{project_root}'",
            f"SUDOERS_TMP='{out}'",
            "SUDOERS_FILE=/etc/sudoers.d/ledmatrix_web",
            "SYSTEMCTL_PATH=/usr/bin/systemctl",
            "REBOOT_PATH=/usr/sbin/reboot",
            "POWEROFF_PATH=/usr/sbin/poweroff",
            "BASH_PATH=$(which bash)",
            "JOURNALCTL_PATH=/usr/bin/journalctl",
            _step10_generation(_read(FIRST_TIME)),
            'printf %s "$SUDOERS_VALID"',
        ]
    )
    return subprocess.run(
        ["bash", "-c", script], check=True, capture_output=True, text=True
    ).stdout


def _render_first_time_sudoers(tmp, user):
    """The rules first_time_install.sh generates, via the shared library."""
    out = os.path.join(tmp, "rendered")
    assert _run_step10_generation(REPO_ROOT, user, out) == "1"
    return out


def _run_step10(tmp, project_root, visudo_ok, existing=None):
    """Run all of Step 10 against a sudoers file in `tmp`, never /etc.

    systemctl, reboot, poweroff, journalctl and visudo are stubs, so the
    outcome does not depend on the machine running the test."""
    body = _read(FIRST_TIME)
    step = body[body.index('CURRENT_STEP="Configure passwordless sudo access"'):
                body.index('CURRENT_STEP="Configure WiFi management permissions"')]
    target = os.path.join(tmp, "ledmatrix_web")
    real = 'SUDOERS_FILE="/etc/sudoers.d/ledmatrix_web"'
    assert step.count(real) == 1
    step = step.replace(real, f"SUDOERS_FILE='{target}'")
    stubs = os.path.join(tmp, "stubs")
    os.mkdir(stubs)
    for name, code in (("systemctl", 0), ("reboot", 0), ("poweroff", 0),
                       ("journalctl", 0), ("visudo", 0 if visudo_ok else 1)):
        path = os.path.join(stubs, name)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(f"#!/bin/sh\nexit {code}\n")
        os.chmod(path, 0o755)
    if existing is not None:
        with open(target, "w", encoding="utf-8") as handle:
            handle.write(existing)
    env = dict(os.environ, TMPDIR=tmp,
               PATH=os.pathsep.join([stubs, os.path.dirname(sys.executable),
                                     "/usr/bin", "/bin"]))
    script = "\n".join(["set -Eeuo pipefail", "ACTUAL_USER=ledmatrix",
                          f"PROJECT_ROOT_DIR='{project_root}'", step])
    result = subprocess.run(["bash", "-c", script], env=env,
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    return target, stubs, result


_POSIX_STEP10 = pytest.mark.skipif(
    sys.platform == "win32" or shutil.which("which") is None,
    reason="needs a POSIX bash and which")


@_POSIX_STEP10
def test_step10_installs_the_generated_rules():
    with tempfile.TemporaryDirectory() as tmp:
        target, stubs, _ = _run_step10(tmp, REPO_ROOT, visudo_ok=True)
        assert oct(os.stat(target).st_mode & 0o777) == "0o440"
        with open(target, encoding="utf-8") as handle:
            installed = handle.read()
        lib = os.path.join(REPO_ROOT, "scripts", "install", "lib_sudoers.sh")
        expected = subprocess.run(
            ["bash", "-c", '. "$1"; web_sudoers_rules ledmatrix "$2" "$3/systemctl" '
             '"$(command -v bash)" "$3/reboot" "$3/poweroff" "$3/journalctl"',
             "_", lib, REPO_ROOT, stubs],
            check=True, capture_output=True, text=True,
            env=dict(os.environ, PATH=os.pathsep.join([stubs, "/usr/bin", "/bin"])),
        ).stdout
        assert installed == expected
        assert not [f for f in os.listdir(tmp) if f.startswith("ledmatrix_web_sudoers.")]


@_POSIX_STEP10
def test_step10_without_the_library_keeps_the_existing_file():
    with tempfile.TemporaryDirectory() as tmp:
        target, _, result = _run_step10(tmp, tmp, visudo_ok=True, existing="keep\n")
        with open(target, encoding="utf-8") as handle:
            assert handle.read() == "keep\n"
        assert "lib_sudoers.sh not found" in result.stderr
        assert "Passwordless sudo access configured" not in result.stdout


@_POSIX_STEP10
def test_step10_keeps_the_existing_file_when_the_rules_do_not_parse():
    with tempfile.TemporaryDirectory() as tmp:
        target, _, result = _run_step10(tmp, REPO_ROOT, visudo_ok=False, existing="keep\n")
        with open(target, encoding="utf-8") as handle:
            assert handle.read() == "keep\n"
        assert "did not parse" in result.stderr


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
