#!/usr/bin/env python3
"""
Alternative dependency installer that tries apt packages first,
then falls back to pip with --break-system-packages
"""

import re
import subprocess
import sys
import tempfile
import warnings
from collections import deque
from pathlib import Path
from typing import Dict, List, Tuple

# How many trailing lines of a failed command's output to keep for the
# end-of-run failure summary. Keeps the root cause near the end of the log,
# which is where first_time_install.sh's error handler tails from.
ERROR_TAIL_LINES = 15


def _run(cmd: List[str]) -> Tuple[bool, str]:
    """Run a command, streaming combined stdout/stderr to a temp file.

    Returns (success, output) instead of raising, so callers can report
    *why* a command failed rather than just that it failed. `output` is
    bounded to the last ERROR_TAIL_LINES lines so failures from very
    chatty commands (e.g. pip build logs) don't get buffered in memory.
    """
    with tempfile.TemporaryFile(mode='w+b') as f:
        result = subprocess.run(cmd, stdout=f, stderr=subprocess.STDOUT)  # nosec B603 B607 - hardcoded apt/pip args  # nosemgrep
        f.seek(0)
        # Stream line-by-line so only the last ERROR_TAIL_LINES are ever held
        # in memory, regardless of how much output the command produced.
        tail = deque(
            (line.decode('utf-8', errors='replace').rstrip('\n') for line in f),
            maxlen=ERROR_TAIL_LINES,
        )
    return result.returncode == 0, '\n'.join(tail)


def install_via_apt(package_name: str) -> Tuple[bool, str]:
    """Try to install a package via apt. Returns (success, output)."""
    # Map pip package names to apt package names
    apt_package_map = {
        'flask': 'python3-flask',
        'PIL': 'python3-pil',
        'freetype-py': 'python3-freetype',
        'psutil': 'python3-psutil',
        'werkzeug': 'python3-werkzeug',
        'numpy': 'python3-numpy',
        'requests': 'python3-requests',
        'pytz': 'python3-tz'
    }

    apt_package = apt_package_map.get(package_name, f'python3-{package_name}')

    print(f"Trying to install {apt_package} via apt...")
    success, output = _run(['sudo', 'apt-get', '-o', 'DPkg::Lock::Timeout=180', 'install', '-y', apt_package])
    if success:
        print(f"Successfully installed {apt_package} via apt")
        return True, ""

    print(f"Failed to install {apt_package} via apt, will try pip")
    return False, output


def install_via_pip(package_name: str) -> Tuple[bool, str]:
    """Install a package via pip with --break-system-packages and --prefer-binary.

    --break-system-packages allows pip to install into the system Python on
    Debian/Ubuntu-based systems without a virtual environment.
    --prefer-binary prefers pre-built wheels over source distributions to avoid
    exhausting /tmp space during compilation.
    --ignore-installed stops pip from trying to *uninstall* packages that were
    installed by apt (e.g. python3-requests). Those Debian packages ship no
    pip RECORD file, so an uninstall attempt fails with "uninstall-no-record-file"
    and aborts the whole install. With --ignore-installed, pip lays the new
    version down in /usr/local where it shadows the apt copy instead of removing
    it. This matters when a pip dependency needs to upgrade an apt-managed
    package (e.g. a package that pulls a newer requests).

    Returns (success, output).
    """
    # pip knows PIL as Pillow; the others are asked for by their own name.
    package_name = _dist_name(package_name)
    print(f"Installing {package_name} via pip...")
    success, output = _run([
        sys.executable, '-m', 'pip', 'install',
        '--break-system-packages', '--prefer-binary', '--ignore-installed', package_name
    ])
    if success:
        print(f"Successfully installed {package_name} via pip")
        return True, ""

    print(f"Failed to install {package_name} via pip (see failure summary at end of log)")
    return False, output


# Distribution (pip/apt) names whose importable module name differs.
IMPORT_NAME_MAP = {
    'freetype-py': 'freetype',
}

# The packages above are keyed by what main() lists; these are the ones whose
# pip distribution name differs from that key.
DIST_NAME_MAP = {
    'PIL': 'Pillow',
}

REQUIREMENTS_FILE = Path(__file__).resolve().parent.parent / 'web_interface' / 'requirements.txt'


def _version_tuple(text: str) -> tuple:
    parts = []
    for part in text.split('.'):
        digits = ''.join(ch for ch in part if ch.isdigit())
        if not digits:
            break
        parts.append(int(digits))
    return tuple(parts)


def _requirement_floors(path: Path = REQUIREMENTS_FILE) -> Dict[str, tuple]:
    """``>=`` floors from a requirements file, keyed by lower-cased name.

    The apt copies of these packages are older than the pins on both
    supported releases -- Bookworm ships Flask and Werkzeug 2.2.2, Pillow 9.4,
    requests 2.28, psutil 5.9, pytz 2022.7 and freetype-py 2.3; Trixie ships
    Flask 3.1.1, Werkzeug 3.1.3, Pillow 11.1 and requests 2.32 --
    so a package that merely imports is not enough. Read from the file rather
    than copied here so the two cannot drift.
    """
    floors: Dict[str, tuple] = {}
    try:
        lines = path.read_text(encoding='utf-8').splitlines()
    except OSError:
        return floors
    for line in lines:
        match = re.match(r'\s*([A-Za-z0-9][A-Za-z0-9._-]*)[^#]*?>=\s*([0-9][0-9.]*)', line)
        if match:
            floors[match.group(1).lower()] = _version_tuple(match.group(2))
    return floors


def _dist_name(package_name: str) -> str:
    return DIST_NAME_MAP.get(package_name, package_name)


def _minimum_version(package_name: str) -> tuple:
    """The required floor for ``package_name``, or () when there is none."""
    return MIN_VERSIONS.get(_dist_name(package_name).lower(), ())


# Minimum versions that must be met for an already-installed package to count
# as satisfied.
MIN_VERSIONS = _requirement_floors()


def _installed_version_tuple(dist_name: str) -> tuple:
    """Return the installed distribution version as an int tuple, or () if unknown."""
    try:
        from importlib.metadata import version
        return _version_tuple(version(dist_name))
    except Exception:
        return ()


def check_package_installed(package_name: str) -> bool:
    """Check if a package is already installed (and meets any minimum version)."""
    import_name = IMPORT_NAME_MAP.get(package_name, package_name)
    # Suppress deprecation warnings when checking if packages are installed
    # (we're just checking, not using them)
    with warnings.catch_warnings():
        warnings.filterwarnings('ignore', category=DeprecationWarning)
        try:
            __import__(import_name)
        except ImportError:
            return False
    minimum = _minimum_version(package_name)
    if minimum:
        installed = _installed_version_tuple(_dist_name(package_name))
        if not installed or installed < minimum:
            print(f"{package_name} is installed but below the required "
                  f"{'.'.join(map(str, minimum))}; will upgrade via pip")
            return False
    return True


def print_failure_summary(failed_packages: List[str], failure_details: dict) -> None:
    print("\n" + "=" * 60)
    print("DEPENDENCY INSTALLATION FAILURES - DETAILS")
    print("=" * 60)
    for package in failed_packages:
        print(f"\nPackage: {package}")
        print("-" * 40)
        output = failure_details.get(package, "").strip()
        if not output:
            print("  (no output captured)")
            continue
        for line in output.splitlines()[-ERROR_TAIL_LINES:]:
            print(f"  {line}")
    print("=" * 60)


def main():
    """Main installation function."""
    print("Installing dependencies for LED Matrix Web Interface V2...")

    print("Refreshing apt package index...")
    _run(['sudo', 'apt', 'update'])  # best-effort; individual installs surface their own errors

    # List of required packages
    required_packages = [
        'flask',
        'PIL',
        'freetype-py',
        'psutil',
        'werkzeug',
        'numpy',
        'requests',
        'pytz'
    ]

    failed_packages = []
    failure_details = {}

    for package in required_packages:
        if check_package_installed(package):
            print(f"{package} is already installed")
            continue

        # Try apt first, then pip. An apt install only counts if it also
        # satisfies the requirements floor (the apt copies of most of these
        # are older than the pins on both Bookworm and Trixie), otherwise
        # fall through to pip.
        ok, apt_output = install_via_apt(package)
        if ok and _minimum_version(package) and not check_package_installed(package):
            ok = False
            apt_output = f"apt version of {package} is below the required minimum"
        if not ok:
            ok, pip_output = install_via_pip(package)
            if not ok:
                failed_packages.append(package)
                failure_details[package] = pip_output or apt_output

    # Install packages that don't have apt equivalents
    # Packages without apt equivalents. Plugin-specific dependencies
    # (timezonefinder, google-api stack, icalevents, socketio, ...) are
    # no longer installed here — store plugins declare their own
    # requirements.txt, which the plugin store installs.
    special_packages = [
        'spotipy',
    ]

    for package in special_packages:
        ok, pip_output = install_via_pip(package)
        if not ok:
            failed_packages.append(package)
            failure_details[package] = pip_output

    # Install rgbmatrix module from local source (optional - may already be installed in Step 6)
    # Check if already installed first
    if check_package_installed('rgbmatrix'):
        print("rgbmatrix module already installed, skipping...")
    else:
        print("Installing rgbmatrix module from local source...")
        # Get project root (parent of scripts directory)
        PROJECT_ROOT = Path(__file__).parent.parent
        rgbmatrix_path = PROJECT_ROOT / 'rpi-rgb-led-matrix-master' / 'bindings' / 'python'
        if rgbmatrix_path.exists():
            # Check if the module has been built (look for setup.py)
            setup_py = rgbmatrix_path / 'setup.py'
            if setup_py.exists():
                # Try installing - use regular install, not editable mode
                # This is optional for web interface and should already be installed in Step 6
                ok, output = _run([sys.executable, '-m', 'pip', 'install', '--break-system-packages', str(rgbmatrix_path)])
                if ok:
                    print("rgbmatrix module installed successfully")
                else:
                    # Don't fail the whole installation - rgbmatrix is optional for web interface
                    # and should be installed in Step 6 of first_time_install.sh
                    print("Warning: Failed to install rgbmatrix module:")
                    for line in output.strip().splitlines()[-ERROR_TAIL_LINES:]:
                        print(f"  {line}")
                    print("  This is normal if rgbmatrix hasn't been built yet (Step 6).")
                    print("  The web interface will work without it.")
            else:
                print("Warning: rgbmatrix setup.py not found, module may need to be built first")
                print("  This is normal if Step 6 hasn't completed yet.")
        else:
            print("Warning: rgbmatrix source not found (this is normal if Step 6 hasn't run yet)")

    if failed_packages:
        print(f"\nFailed to install the following packages: {failed_packages}")
        print("You may need to install them manually or check your system configuration.")
        print_failure_summary(failed_packages, failure_details)
        return False
    else:
        print("\nAll dependencies installed successfully!")
        return True

if __name__ == '__main__':
    success = main()
    sys.exit(0 if success else 1)
