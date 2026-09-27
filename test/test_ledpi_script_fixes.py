"""Script and logging fixes found testing on a real Pi (ledpi).

- configure_web_sudo.sh runs as the web user, whose PATH lacks /usr/sbin, so
  `command -v reboot` failed and the reboot/poweroff rules were dropped.
- check_system_compatibility.sh ran `dpkg -l | grep -q` under pipefail, which
  reported installed packages as missing.
- A network failure fetching GitHub repo info is a WARNING, not an ERROR.
"""

import logging
import re
import shutil
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pytest
import requests

ROOT = Path(__file__).resolve().parent.parent
WEB_SUDO = ROOT / "scripts" / "install" / "configure_web_sudo.sh"
COMPAT = ROOT / "scripts" / "check_system_compatibility.sh"


def _function_source(script: Path, name: str) -> str:
    text = script.read_text(encoding="utf-8")
    m = re.search(rf"^{name}\(\) \{{\n.*?^\}}\n", text, re.S | re.M)
    assert m, f"{name}() not found in {script.name}"
    return m.group(0)


needs_linux_bash = pytest.mark.skipif(
    sys.platform == "win32" or not shutil.which("bash") or not Path("/bin/sh").exists(),
    reason="runs the script's shell function; needs a Linux bash",
)


@needs_linux_bash
def test_find_command_looks_outside_path():
    """With a PATH that has none of the standard dirs, a command in one of
    them is still found (reboot and poweroff live in /usr/sbin)."""
    target = next((d for d in ("/usr/sbin", "/sbin", "/usr/bin", "/bin")
                   if Path(d, "sh").exists() or Path(d, "reboot").exists()), None)
    name = "reboot" if Path(target, "reboot").exists() else "sh"
    script = _function_source(WEB_SUDO, "find_command") + f'find_command {name}\n'
    # Absolute bash: with PATH=/nonexistent, "bash" itself would not be found.
    out = subprocess.run([shutil.which("bash"), "-c", script], env={"PATH": "/nonexistent"},
                         capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip().endswith("/" + name)


def test_web_sudo_resolves_reboot_and_poweroff_through_find_command():
    text = WEB_SUDO.read_text(encoding="utf-8")
    for var, cmd in (("REBOOT_PATH", "reboot"), ("POWEROFF_PATH", "poweroff")):
        assert re.search(rf"^{var}=\$\(find_command {cmd}\)", text, re.M), var
    assert "/usr/sbin" in _function_source(WEB_SUDO, "find_command")


def test_compat_check_does_not_pipe_dpkg_into_grep_q():
    # grep -q exits on its first match; dpkg then dies of SIGPIPE and, under
    # `set -o pipefail`, the check fails for an installed package.
    text = COMPAT.read_text(encoding="utf-8")
    code = "\n".join(l for l in text.splitlines() if not l.lstrip().startswith("#"))
    assert "pipefail" in code
    assert not re.search(r"dpkg -l\s*\|\s*grep -q", code)
    assert "dpkg-query -W" in code


@needs_linux_bash
def test_compat_package_check_reports_an_installed_package_as_installed():
    """Run the script's package test against a stub dpkg-query."""
    line = next(l for l in COMPAT.read_text(encoding="utf-8").splitlines()
                if "dpkg-query -W" in l).strip()
    with TemporaryDirectory() as tmp:
        stub = Path(tmp, "dpkg-query")
        # The package name is the last argument: dpkg-query -W -f=... <pkg>.
        stub.write_text('#!/bin/sh\nfor a; do last=$a; done\n'
                        '[ "$last" = "git" ] && printf "install ok installed"\nexit 0\n')
        stub.chmod(0o755)
        script = ("set -Eeuo pipefail\n"
                  f"for pkg in git notthere; do {line} echo \"$pkg:yes\"; else echo \"$pkg:no\"; fi; done\n")
        out = subprocess.run(["bash", "-c", script],
                             env={"PATH": f"{tmp}:/usr/bin:/bin"}, capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    assert out.stdout.split() == ["git:yes", "notthere:no"]


def test_github_network_failure_logs_a_warning_not_an_error(caplog):
    from src.plugin_system.store_manager import PluginStoreManager
    with TemporaryDirectory() as tmp:
        sm = PluginStoreManager(plugins_dir=tmp)
        with patch("src.plugin_system.store_manager.requests.get",
                   side_effect=requests.ConnectionError("offline")), \
                caplog.at_level(logging.WARNING):
            info = sm._get_github_repo_info("https://github.com/owner/repo")
    assert info == dict(PluginStoreManager._EMPTY_REPO_INFO)
    ours = [r for r in caplog.records if "owner/repo" in r.getMessage()]
    assert ours, "nothing logged for the failed fetch"
    assert all(r.levelno == logging.WARNING for r in ours), [(r.levelname, r.getMessage()) for r in ours]
