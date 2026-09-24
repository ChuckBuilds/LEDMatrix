"""scripts/fix_perms/fix_web_permissions.sh must not undo the installer's hardening.

The script chowns the whole project to the web user. That used to include the
two helpers /etc/sudoers.d/ledmatrix_web lets the web user run as root
(safe_plugin_rm.sh, safe_pip_install.sh) -- a helper the web user owns is a
root shell for anyone who can edit it -- and config_secrets.json, which lost
the ledmatrix group first_time_install.sh gives it. After the chown the script
now puts both back the way the installer's Steps 11 and 11.1 leave them.

The behavioural test runs the real script against a scratch copy of the
project with `sudo`, `getent` and `journalctl` stubbed, and checks the order
of what it asked sudo to do.
"""
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "fix_perms" / "fix_web_permissions.sh"
LIB = ROOT / "scripts" / "install" / "lib_sudoers.sh"


def _text(path):
    return path.read_text(encoding="utf-8", errors="replace").replace("\r\n", "\n")


def _granted_helpers():
    helpers = set(re.findall(r"scripts/fix_perms/([\w.-]+\.sh) \*", _text(LIB)))
    assert helpers, "no fix_perms helper grant found in lib_sudoers.sh"
    return helpers


def test_every_granted_helper_is_rehardened_after_the_chown():
    text = _text(SCRIPT)
    chown = text.index('sudo chown -R "$WEB_USER:$WEB_USER" "$PROJECT_DIR"')
    loop = re.search(r"for helper in ([^;]+); do\n(.*?)\ndone", text, re.S)
    assert loop, "no helper-hardening loop in fix_web_permissions.sh"
    assert "sudo chown root:root" in loop.group(2) and "sudo chmod 755" in loop.group(2)
    assert loop.start() > chown, "helpers are hardened before the chown that undoes it"
    assert _granted_helpers() <= set(loop.group(1).split())


def test_no_longer_claims_to_configure_sudoers():
    text = _text(SCRIPT)
    assert "Configure sudoers for passwordless access" not in text
    assert "./configure_web_sudo.sh" not in text.replace("scripts/install/configure_web_sudo.sh", "")


_STUB_SUDO = """#!/bin/bash
printf '%s\\n' "$*" >> "$SUDO_LOG"
# `sudo -n ...` probes and `sudo -u ...` tests: report failure, run nothing.
case "$1" in -n|-u) exit 1 ;; esac
exit 0
"""


@pytest.mark.skipif(sys.platform == "win32" or shutil.which("bash") is None,
                    reason="needs a POSIX bash")
def test_script_rehardens_helpers_and_secrets(tmp_path):
    project = tmp_path / "LED Matrix"
    (project / "scripts" / "fix_perms").mkdir(parents=True)
    (project / "config").mkdir()
    script = project / "scripts" / "fix_perms" / "fix_web_permissions.sh"
    script.write_text(_text(SCRIPT), encoding="utf-8")
    for helper in ("safe_plugin_rm.sh", "safe_pip_install.sh"):
        (project / "scripts" / "fix_perms" / helper).write_text("#!/bin/bash\n")
    (project / "config" / "config_secrets.json").write_text("{}\n")

    stubs = tmp_path / "stubs"
    stubs.mkdir()
    for name, body in (("sudo", _STUB_SUDO),
                       ("getent", "#!/bin/sh\nexit 0\n"),
                       ("journalctl", "#!/bin/sh\nexit 1\n")):
        (stubs / name).write_text(body)
        (stubs / name).chmod(0o755)
    log = tmp_path / "sudo.log"
    env = dict(os.environ, SUDO_LOG=str(log),
               PATH=os.pathsep.join([str(stubs), os.environ.get("PATH", "")]))

    result = subprocess.run(["bash", str(script)], input="y", env=env,
                            capture_output=True, text=True)
    if os.geteuid() == 0:
        # The script refuses to run as root; that refusal is the whole test.
        assert result.returncode == 1 and "should not be run as root" in result.stdout
        return
    assert result.returncode == 0, result.stdout + result.stderr

    calls = log.read_text().splitlines()
    user = subprocess.run(["whoami"], capture_output=True, text=True).stdout.strip()
    chown_all = calls.index(f"chown -R {user}:{user} {project}")
    for helper in ("safe_plugin_rm.sh", "safe_pip_install.sh"):
        path = project / "scripts" / "fix_perms" / helper
        assert calls.index(f"chown root:root {path}") > chown_all, calls
        assert calls.index(f"chmod 755 {path}") > chown_all, calls
    secrets = project / "config" / "config_secrets.json"
    # The owner is the installed web unit's User= when there is one.
    owner = user
    unit = Path("/etc/systemd/system/ledmatrix-web.service")
    if unit.is_file():
        m = re.search(r"^User=(.*)$", unit.read_text(), re.M)
        if m and m.group(1):
            owner = m.group(1)
    assert calls.index(f"chown {owner}:ledmatrix {secrets}") > chown_all, calls
    assert calls.index(f"chmod 640 {secrets}") > chown_all, calls
