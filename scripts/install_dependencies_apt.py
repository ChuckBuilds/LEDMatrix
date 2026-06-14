#!/usr/bin/env python3
"""
Alternative dependency installer that tries apt packages first,
then falls back to pip with --break-system-packages
"""

import subprocess
import sys
import tempfile
import warnings
from collections import deque
from pathlib import Path

# How many trailing lines of a failed command's output to keep for the
# end-of-run failure summary. Keeps the root cause near the end of the log,
# which is where first_time_install.sh's error handler tails from.
ERROR_TAIL_LINES = 15


def _run(cmd):
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


def install_via_apt(package_name):
    """Try to install a package via apt. Returns (success, output)."""
    # Map pip package names to apt package names
    apt_package_map = {
        'flask': 'python3-flask',
        'PIL': 'python3-pil',
        'freetype': 'python3-freetype',
        'psutil': 'python3-psutil',
        'werkzeug': 'python3-werkzeug',
        'numpy': 'python3-numpy',
        'requests': 'python3-requests',
        'python-dateutil': 'python3-dateutil',
        'pytz': 'python3-tz',
        'geopy': 'python3-geopy',
        'unidecode': 'python3-unidecode',
        'websockets': 'python3-websockets',
        'websocket-client': 'python3-websocket-client'
    }

    apt_package = apt_package_map.get(package_name, f'python3-{package_name}')

    print(f"Trying to install {apt_package} via apt...")
    success, output = _run(['sudo', 'apt', 'install', '-y', apt_package])
    if success:
        print(f"Successfully installed {apt_package} via apt")
        return True, ""

    print(f"Failed to install {apt_package} via apt, will try pip")
    return False, output


def install_via_pip(package_name):
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
    it. This matters when a pip dependency (google-api-python-client pulls a
    newer requests) needs to upgrade an apt-managed package.

    Returns (success, output).
    """
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
    'python-dateutil': 'dateutil',
    'websocket-client': 'websocket',
}


def check_package_installed(package_name):
    """Check if a package is already installed."""
    import_name = IMPORT_NAME_MAP.get(package_name, package_name)
    # Suppress deprecation warnings when checking if packages are installed
    # (we're just checking, not using them)
    with warnings.catch_warnings():
        warnings.filterwarnings('ignore', category=DeprecationWarning)
        try:
            __import__(import_name)
            return True
        except ImportError:
            return False


def print_failure_summary(failed_packages, failure_details):
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
        'freetype',
        'psutil',
        'werkzeug',
        'numpy',
        'requests',
        'python-dateutil',
        'pytz',
        'geopy',
        'unidecode',
        'websockets',
        'websocket-client'
    ]

    failed_packages = []
    failure_details = {}

    for package in required_packages:
        if check_package_installed(package):
            print(f"{package} is already installed")
            continue

        # Try apt first, then pip
        ok, apt_output = install_via_apt(package)
        if not ok:
            ok, pip_output = install_via_pip(package)
            if not ok:
                failed_packages.append(package)
                failure_details[package] = pip_output or apt_output

    # Install packages that don't have apt equivalents
    special_packages = [
        'timezonefinder>=6.5.0,<7.0.0',
        'google-auth-oauthlib>=1.2.0,<2.0.0',
        'google-auth-httplib2>=0.2.0,<1.0.0',
        'google-api-python-client>=2.147.0,<3.0.0',
        'spotipy',
        'icalevents',
        'python-socketio>=5.11.0,<6.0.0',
        'python-engineio>=4.9.0,<5.0.0'
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
                ok, output = _run([sys.executable, '-m', 'pip', 'install', '--break-system-packages', '--ignore-installed', str(rgbmatrix_path)])
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
