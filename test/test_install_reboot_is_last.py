"""first_time_install.sh prints its completion summary before it reboots.

With -y (and so with the one-shot `curl | bash` installer, which always
passes -y) the reboot used to be issued ~180 lines before the "Installation
Complete / Web UI Access" summary. `reboot` returns at once and the script
carried on printing while the system went down, so the SSH session usually
dropped before the user saw the web UI address.

first_time_install.sh exits on anything but Raspberry Pi OS Trixie before it
parses its arguments, so the behavioural test runs only the tail of the
script -- from the summary to the end -- with systemctl, nmcli, hostname, ip
and reboot stubbed.
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
FIRST_TIME = ROOT / "first_time_install.sh"
SUMMARY_START = 'echo "Installation Complete!"'


def _text():
    return FIRST_TIME.read_text(encoding="utf-8").replace("\r\n", "\n")


def test_every_reboot_comes_after_the_summary():
    text = _text()
    summary = text.index(SUMMARY_START)
    lines = text.splitlines()
    reboots = [i for i, line in enumerate(lines) if line.strip() == "reboot"]
    assert reboots, "no reboot call found"
    summary_line = text[:summary].count("\n")
    enjoy_line = text[:text.index('echo "Enjoy your LED Matrix display!"')].count("\n")
    assert all(i > enjoy_line > summary_line for i in reboots), (
        f"reboot at line(s) {[i + 1 for i in reboots]} runs before the summary "
        f"(line {summary_line + 1}) has finished printing")


def _tail():
    """The script from the summary header to the end, header rule included."""
    text = _text()
    start = text.rindex('echo "=========================================="', 0,
                        text.index(SUMMARY_START))
    return text[start:]


_POSIX = pytest.mark.skipif(sys.platform == "win32" or shutil.which("bash") is None,
                            reason="needs a POSIX bash")


def _run(tmp_path, env_extra, nmcli_active_line=True, stdin=""):
    stubs = tmp_path / "stubs"
    stubs.mkdir()
    log = tmp_path / "calls.log"
    active = 'echo "yes:HomeNet"' if nmcli_active_line else ":"
    bodies = {
        "reboot": f'#!/bin/sh\necho REBOOT-CALLED\necho reboot >> "{log}"\n',
        "systemctl": "#!/bin/sh\nexit 3\n",
        "hostname": '#!/bin/sh\necho "192.168.1.50 fe80::1"\n',
        "ip": "#!/bin/sh\nexit 1\n",
        # device status -> one connected wifi device; device wifi -> active line
        "nmcli": ('#!/bin/sh\ncase "$*" in\n'
                  '  *"device status"*) echo "wlan0:wifi:connected" ;;\n'
                  f'  *"device wifi"*) {active} ;;\n'
                  "esac\n"),
    }
    for name, body in bodies.items():
        (stubs / name).write_text(body)
        (stubs / name).chmod(0o755)
    script = "\n".join([
        "set -Eeuo pipefail",
        "on_error() { echo \"ERR-TRAP line $1\" >&2; exit 1; }",
        "trap 'on_error $LINENO' ERR",
        "PROJECT_ROOT_DIR=/home/pi/LEDMatrix",
        "ASSUME_YES=${ASSUME_YES:-0}",
        "SKIP_REBOOT_PROMPT=${SKIP_REBOOT_PROMPT:-0}",
        _tail(),
    ])
    env = dict(os.environ, PATH=os.pathsep.join([str(stubs), "/usr/bin", "/bin"]), **env_extra)
    result = subprocess.run(["bash", "-c", script], env=env, input=stdin,
                            capture_output=True, text=True)
    calls = log.read_text().splitlines() if log.exists() else []
    return result, calls


@_POSIX
@pytest.mark.parametrize("nmcli_active_line", [True, False], ids=["ssid", "no-ssid"])
def test_assume_yes_prints_the_summary_then_reboots(tmp_path, nmcli_active_line):
    result, calls = _run(tmp_path, {"ASSUME_YES": "1"}, nmcli_active_line)
    out = result.stdout
    assert result.returncode == 0, out + result.stderr
    assert calls == ["reboot"]
    for text in ("Installation Complete!", "Web UI Access:", "http://192.168.1.50:5000",
                 "Enjoy your LED Matrix display!"):
        assert out.index(text) < out.index("REBOOT-CALLED"), text
    assert "Password: ledmatrix123" not in out


@_POSIX
def test_no_reboot_prompt_prints_the_summary_and_does_not_reboot(tmp_path):
    result, calls = _run(tmp_path, {"ASSUME_YES": "1", "SKIP_REBOOT_PROMPT": "1"})
    assert result.returncode == 0, result.stdout + result.stderr
    assert calls == []
    assert "Enjoy your LED Matrix display!" in result.stdout
    assert "Skipping reboot prompt" in result.stdout


@_POSIX
@pytest.mark.parametrize("answer,expected", [("y", ["reboot"]), ("n", [])])
def test_interactive_prompt_comes_after_the_summary(tmp_path, answer, expected):
    result, calls = _run(tmp_path, {}, stdin=answer)
    assert result.returncode == 0, result.stdout + result.stderr
    assert calls == expected
    out = result.stdout
    assert "Enjoy your LED Matrix display!" in out
    if expected:
        assert out.index("Enjoy your LED Matrix display!") < out.index("REBOOT-CALLED")
