"""The installer accepts Raspberry Pi OS Bookworm and Trixie, and nothing else.

Bookworm (Debian 12) ships Python 3.11 and Trixie (Debian 13) Python 3.13.
The rules live in scripts/install/lib_os.sh, which first_time_install.sh and
scripts/check_system_compatibility.sh both source. These tests feed the real
scripts a fake /etc/os-release (LM_OS_RELEASE_FILE) and stub python3,
systemctl and dpkg, so they need a Linux bash; the installer itself cannot
run end to end off a Pi.
"""

import importlib.util
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
LIB = ROOT / "scripts" / "install" / "lib_os.sh"
FIRST_TIME = ROOT / "first_time_install.sh"
COMPAT = ROOT / "scripts" / "check_system_compatibility.sh"

needs_bash = pytest.mark.skipif(
    sys.platform == "win32" or shutil.which("bash") is None,
    reason="runs the installer's shell code; needs a Linux bash",
)

OS_RELEASES = {
    # Raspberry Pi OS 64-bit reports ID=debian, 32-bit ID=raspbian.
    "trixie": 'PRETTY_NAME="Debian GNU/Linux 13 (trixie)"\nNAME="Debian GNU/Linux"\n'
              'VERSION_ID="13"\nVERSION="13 (trixie)"\nVERSION_CODENAME=trixie\nID=debian\n',
    "bookworm": 'PRETTY_NAME="Raspbian GNU/Linux 12 (bookworm)"\nNAME="Raspbian GNU/Linux"\n'
                'VERSION_ID="12"\nVERSION="12 (bookworm)"\nVERSION_CODENAME=bookworm\nID=raspbian\n'
                'ID_LIKE=debian\n',
    "bookworm64": 'PRETTY_NAME="Debian GNU/Linux 12 (bookworm)"\nVERSION_ID="12"\n'
                  "VERSION_CODENAME=bookworm\nID=debian\n",
    "bullseye": 'PRETTY_NAME="Raspbian GNU/Linux 11 (bullseye)"\nVERSION_ID="11"\n'
                "VERSION_CODENAME=bullseye\nID=raspbian\n",
    "ubuntu": 'PRETTY_NAME="Ubuntu 24.04 LTS"\nVERSION_ID="24.04"\nVERSION_CODENAME=noble\n'
              "ID=ubuntu\nID_LIKE=debian\n",
    "no-version-id": "PRETTY_NAME='Debian GNU/Linux trixie'\nVERSION_CODENAME=trixie\nID=debian\n",
}


def _stub(bin_dir: Path, name: str, body: str) -> None:
    path = bin_dir / name
    path.write_text("#!/bin/sh\n" + body, encoding="utf-8", newline="\n")
    path.chmod(0o755)


def _stubs(tmp_path: Path, python_version="3.11", network="NetworkManager") -> Path:
    """python3 reports ``python_version`` (None: not installed); systemctl
    reports ``network`` as the only active unit; dpkg lists no desktop."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    if python_version is None:
        # Shadows any real python3 further down PATH.
        _stub(bin_dir, "python3", "exit 127\n")
    else:
        _stub(bin_dir, "python3", f'case "$*" in *"%d.%d.%d"*) echo "{python_version}.1" ;; '
                                  f'*) echo "{python_version}" ;; esac\n')
    _stub(bin_dir, "systemctl",
          f'case "$*" in *"is-active --quiet {network}") exit 0 ;; esac\nexit 3\n')
    _stub(bin_dir, "dpkg", "exit 0\n")
    _stub(bin_dir, "dpkg-query", "exit 1\n")
    _stub(bin_dir, "ping", "exit 0\n")
    return bin_dir


def _env(tmp_path: Path, release: str, bin_dir: Path) -> dict:
    os_release = tmp_path / "os-release"
    os_release.write_text(OS_RELEASES[release], encoding="utf-8", newline="\n")
    return {
        "PATH": f"{bin_dir}:/usr/bin:/bin:/usr/sbin:/sbin",
        "LM_OS_RELEASE_FILE": str(os_release),
        "HOME": str(tmp_path),
    }


def lib(snippet: str, env: dict) -> subprocess.CompletedProcess:
    return subprocess.run(["bash", "-c", f"set -Eeuo pipefail\n. '{LIB}'\n{snippet}"],
                          capture_output=True, text=True, env=env)


# --- lib_os.sh -----------------------------------------------------------------

@needs_bash
class TestLibrary:
    def test_library_is_syntactically_valid(self):
        result = subprocess.run(["bash", "-n", str(LIB)], capture_output=True, text=True)
        assert result.returncode == 0, result.stderr

    @pytest.mark.parametrize("release,expected", [
        ("trixie", "trixie"),
        ("bookworm", "bookworm"),
        ("bookworm64", "bookworm"),
        ("no-version-id", "trixie"),
    ])
    def test_supported_releases_are_recognised(self, tmp_path, release, expected):
        result = lib("lm_os_release", _env(tmp_path, release, _stubs(tmp_path)))
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == expected

    @pytest.mark.parametrize("release", ["bullseye", "ubuntu"])
    def test_other_systems_are_refused(self, tmp_path, release):
        result = lib("lm_os_release", _env(tmp_path, release, _stubs(tmp_path)))
        assert result.returncode != 0
        assert result.stdout.strip() == ""

    def test_missing_os_release_is_refused_not_fatal_to_the_caller(self, tmp_path):
        env = _env(tmp_path, "trixie", _stubs(tmp_path))
        env["LM_OS_RELEASE_FILE"] = str(tmp_path / "absent")
        result = lib('if lm_os_release; then echo yes; else echo no; fi', env)
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "no"

    def test_quoted_fields_are_unquoted(self, tmp_path):
        env = _env(tmp_path, "no-version-id", _stubs(tmp_path))
        result = lib("lm_os_field PRETTY_NAME; lm_os_field VERSION_ID", env)
        assert result.stdout == "Debian GNU/Linux trixie\n"

    @pytest.mark.parametrize("release,python", [("bookworm", "3.11"), ("trixie", "3.13")])
    def test_each_release_names_the_python_it_ships(self, tmp_path, release, python):
        result = lib(f"lm_release_python {release}", _env(tmp_path, "trixie", _stubs(tmp_path)))
        assert result.stdout.strip() == python

    @pytest.mark.parametrize("version,verdict", [
        ("3.11", "ok"), ("3.12", "ok"), ("3.13", "ok"),
        ("3.10", "too-old"), ("3.9", "too-old"), ("2.7", "too-old"),
        ("3.14", "too-new"), ("4.0", "too-new"),
        ("", "unknown"), ("garbage", "unknown"),
    ])
    def test_python_versions(self, tmp_path, version, verdict):
        result = lib(f'lm_python_check "{version}"', _env(tmp_path, "trixie", _stubs(tmp_path)))
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == verdict

    @pytest.mark.parametrize("active,expected", [
        ("NetworkManager", "networkmanager"),
        ("dhcpcd", "dhcpcd"),
        ("nothing", "unknown"),
    ])
    def test_network_stack(self, tmp_path, active, expected):
        env = _env(tmp_path, "bookworm", _stubs(tmp_path, network=active))
        assert lib("lm_network_stack", env).stdout.strip() == expected


# --- first_time_install.sh's OS check ------------------------------------------

def _os_check_section() -> str:
    """first_time_install.sh from the OS check up to the next section, with
    the desktop-marker directories pointed somewhere that cannot exist."""
    text = FIRST_TIME.read_text(encoding="utf-8").replace("\r\n", "\n")
    start = text.index("# Check OS version")
    end = text.index("# The user who ran the installer")
    section = text[start:end]
    for marker in ("/usr/share/raspberrypi-ui-mods", "/usr/share/xsessions"):
        assert marker in section
        section = section.replace(marker, "/nonexistent" + marker)
    return section


def run_os_check(tmp_path: Path, release: str, **stub_args) -> subprocess.CompletedProcess:
    """Run the OS check as the installer would, from a copy of the project
    layout so ``$(dirname "$0")/scripts/install/lib_os.sh`` resolves."""
    project = tmp_path / "project"
    (project / "scripts" / "install").mkdir(parents=True)
    shutil.copy(LIB, project / "scripts" / "install" / "lib_os.sh")
    script = project / "first_time_install.sh"
    script.write_text("set -Eeuo pipefail\n"
                      "trap 'echo ERR-TRAP line $LINENO >&2; exit 99' ERR\n"
                      + _os_check_section() + '\necho "SECTION-DONE"\n',
                      encoding="utf-8", newline="\n")
    env = _env(tmp_path, release, _stubs(tmp_path, **stub_args))
    return subprocess.run(["bash", str(script)], capture_output=True, text=True, env=env)


@needs_bash
class TestInstallerOsCheck:
    @pytest.mark.parametrize("release,python,label", [
        ("bookworm", "3.11", "Debian 12 (Bookworm)"),
        ("bookworm64", "3.11", "Debian 12 (Bookworm)"),
        ("trixie", "3.13", "Debian 13 (Trixie)"),
    ])
    def test_supported_release_passes(self, tmp_path, release, python, label):
        result = run_os_check(tmp_path, release, python_version=python)
        assert result.returncode == 0, result.stdout + result.stderr
        assert f"✓ {label} detected" in result.stdout
        assert f"✓ Python {python} detected" in result.stdout
        assert "✓ OS requirements met" in result.stdout
        assert "SECTION-DONE" in result.stdout

    @pytest.mark.parametrize("release,reason", [
        ("bullseye", "This version of Raspberry Pi OS is not supported"),
        ("ubuntu", "This script requires Raspberry Pi OS"),
    ])
    def test_unsupported_system_stops_with_directions(self, tmp_path, release, reason):
        result = run_os_check(tmp_path, release)
        assert result.returncode == 1, result.stdout + result.stderr
        assert reason in result.stdout
        assert "Installation cannot continue." in result.stdout
        assert "Trixie (Debian 13) or Bookworm (Debian 12)" in result.stdout
        assert "SECTION-DONE" not in result.stdout

    def test_python_older_than_the_rgbmatrix_floor_stops(self, tmp_path):
        result = run_os_check(tmp_path, "bookworm", python_version="3.10")
        assert result.returncode == 1, result.stdout + result.stderr
        assert "needs Python 3.11 or newer" in result.stdout
        assert "ships Python 3.11" in result.stdout

    def test_untested_newer_python_warns_and_continues(self, tmp_path):
        result = run_os_check(tmp_path, "trixie", python_version="3.14")
        assert result.returncode == 0, result.stdout + result.stderr
        assert "has not been tested with" in result.stdout

    def test_missing_python_is_left_to_step_1(self, tmp_path):
        result = run_os_check(tmp_path, "trixie", python_version=None)
        assert result.returncode == 0, result.stdout + result.stderr
        assert "Step 1 installs it" in result.stdout

    def test_dhcpcd_is_explained_but_not_fatal(self, tmp_path):
        result = run_os_check(tmp_path, "bookworm", network="dhcpcd")
        assert result.returncode == 0, result.stdout + result.stderr
        assert "dhcpcd, not NetworkManager" in result.stdout
        assert "Network Config" in result.stdout

    def test_networkmanager_is_confirmed(self, tmp_path):
        result = run_os_check(tmp_path, "trixie", python_version="3.13")
        assert "✓ NetworkManager is managing the network" in result.stdout


# --- check_system_compatibility.sh ---------------------------------------------

def run_compat(tmp_path: Path, release: str, **stub_args) -> subprocess.CompletedProcess:
    env = _env(tmp_path, release, _stubs(tmp_path, **stub_args))
    return subprocess.run(["bash", str(COMPAT)], capture_output=True, text=True, env=env)


@needs_bash
class TestCompatibilityCheck:
    @pytest.mark.parametrize("release,python,label", [
        ("bookworm", "3.11", "Debian 12 (Bookworm)"),
        ("trixie", "3.13", "Debian 13 (Trixie)"),
    ])
    def test_supported_release_is_reported_supported(self, tmp_path, release, python, label):
        out = run_compat(tmp_path, release, python_version=python).stdout
        assert f"Detected {label} - supported" in out
        assert "Python version is supported (3.11-3.13)" in out
        assert "not supported" not in out
        assert "ships Python" not in out

    @pytest.mark.parametrize("release", ["bullseye", "ubuntu"])
    def test_unsupported_release_is_an_error(self, tmp_path, release):
        result = run_compat(tmp_path, release)
        assert result.returncode == 1
        assert "is not supported - the installer requires Raspberry Pi OS Lite" in result.stdout

    def test_python_below_the_floor_is_an_error(self, tmp_path):
        result = run_compat(tmp_path, "bookworm", python_version="3.10")
        assert result.returncode == 1
        assert "Python 3.10 is too old - Python 3.11+ is required" in result.stdout

    def test_dhcpcd_is_a_warning(self, tmp_path):
        out = run_compat(tmp_path, "bookworm", network="dhcpcd").stdout
        assert "dhcpcd manages the network" in out
        assert "NetworkManager manages the network" not in out

    def test_python_that_is_not_the_releases_own_is_flagged(self, tmp_path):
        out = run_compat(tmp_path, "trixie", python_version="3.11").stdout
        assert "Debian 13 (Trixie) ships Python 3.13, but python3 runs 3.11" in out


# --- things that must hold for both Python versions ----------------------------

def test_installer_scripts_do_not_hard_code_a_python_minor_version():
    """The services run /usr/bin/python3, which is 3.11 on Bookworm and 3.13
    on Trixie; naming either one in an installer or a unit breaks the other."""
    paths = [FIRST_TIME, *sorted((ROOT / "scripts" / "install").glob("*.sh")),
             *sorted((ROOT / "systemd").glob("*.service"))]
    offenders = []
    for path in paths:
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if line.lstrip().startswith("#"):
                continue
            if re.search(r"python3\.1[0-9]", line):
                offenders.append(f"{path.relative_to(ROOT)}:{n}: {line.strip()}")
    assert not offenders, "\n".join(offenders)


def _load_apt_installer():
    spec = importlib.util.spec_from_file_location(
        "install_dependencies_apt", ROOT / "scripts" / "install_dependencies_apt.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestAptFallbackRespectsThePins:
    """Step 7's apt-first fallback must not accept the releases' older apt
    copies (Bookworm: Flask 2.2.2, Pillow 9.4; Trixie: Flask 3.1.1)."""

    def test_floors_come_from_the_web_requirements(self):
        mod = _load_apt_installer()
        for name in ("flask", "werkzeug", "pillow", "requests", "psutil", "pytz", "freetype-py"):
            assert mod.MIN_VERSIONS.get(name), f"no floor read for {name}"
        assert mod.MIN_VERSIONS["freetype-py"] == (2, 5, 1)

    @pytest.mark.parametrize("package,apt_version", [
        ("flask", (2, 2, 2)),       # Bookworm
        ("flask", (3, 1, 1)),       # Trixie
        ("PIL", (9, 4, 0)),         # Bookworm python3-pil
        ("werkzeug", (2, 2, 2)),
        ("freetype-py", (2, 3, 0)),
    ])
    def test_an_apt_copy_below_the_pin_does_not_count(self, monkeypatch, package, apt_version):
        mod = _load_apt_installer()
        monkeypatch.setattr(mod, "_installed_version_tuple", lambda dist: apt_version)
        monkeypatch.setitem(sys.modules, mod.IMPORT_NAME_MAP.get(package, package), object())
        assert mod.check_package_installed(package) is False

    def test_a_version_at_the_pin_counts(self, monkeypatch):
        mod = _load_apt_installer()
        floor = mod.MIN_VERSIONS["flask"]
        monkeypatch.setattr(mod, "_installed_version_tuple", lambda dist: floor)
        monkeypatch.setitem(sys.modules, "flask", object())
        assert mod.check_package_installed("flask") is True

    def test_the_pillow_version_is_looked_up_under_its_distribution_name(self, monkeypatch):
        mod = _load_apt_installer()
        seen = []
        monkeypatch.setattr(mod, "_installed_version_tuple", lambda dist: seen.append(dist) or (99,))
        monkeypatch.setitem(sys.modules, "PIL", object())
        assert mod.check_package_installed("PIL") is True
        assert seen == ["Pillow"]

    def test_pip_is_asked_for_pillow_not_pil(self, monkeypatch):
        mod = _load_apt_installer()
        calls = []
        monkeypatch.setattr(mod, "_run", lambda cmd: calls.append(cmd) or (True, ""))
        assert mod.install_via_pip("PIL") == (True, "")
        assert calls[0][-1] == "Pillow"
