"""After an update, bring the installed systemd units in line with the new templates.

An update moves the checkout, and with it systemd/*.service, but systemd runs
the copies in /etc/systemd/system, which used to be written only by the
installer. Settings added to a template (the render-loop watchdog, a memory
limit) therefore never reached a device that was already installed.

Updates now run the root-owned helper /usr/local/sbin/ledmatrix-refresh-units
(scripts/install/ledmatrix_refresh_units.py) through sudo when the rendered
units differ from the installed ones. The services pick the new units up at
the restart that follows the update. The automatic update's rollback runs the
same helper with ``--restore`` (scripts/utils/auto_update_verify.py).

The sudo rule is written by the installer, so a device installed before it
existed cannot run the helper. That is reported, not fatal: the update
itself stands, and the message says to re-run the installer once, the same
remedy as the display's startup "unit drift" warning.
"""
import importlib.util
import logging
import os
import subprocess  # nosec B404 - list-form argv only, no shell  # nosemgrep
from pathlib import Path

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
HELPER_SOURCE = PROJECT_ROOT / 'scripts' / 'install' / 'ledmatrix_refresh_units.py'
#: The installed, root-owned copy that sudo is allowed to run.
HELPER_PATH = '/usr/local/sbin/ledmatrix-refresh-units'
SYSTEMD_DIR = '/etc/systemd/system'
TIMEOUT_SECONDS = 90

#: Outcomes (``result['status']``).
CURRENT = 'current'            # nothing differs
REFRESHED = 'refreshed'        # installed the new units
NEEDS_REINSTALL = 'needs_reinstall'  # the helper or its sudo rule is missing
FAILED = 'failed'              # the helper ran and refused or failed
SKIPPED = 'skipped'            # not a systemd install (dev machine, emulator)

REINSTALL_HINT = ('Run "sudo ./first_time_install.sh" in the LEDMatrix folder once '
                  '(or "sudo ./scripts/install/install_service.sh" followed by '
                  '"./scripts/install/configure_web_sudo.sh") so updates can apply them.')

#: sudo's words for "this command line is not allowed without a password".
_SUDO_REFUSED = ('a password is required', 'is not allowed to run', 'no tty present',
                 'a terminal is required', 'command not found')


def _load_helper(path=HELPER_SOURCE):
    spec = importlib.util.spec_from_file_location('ledmatrix_refresh_units', str(path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def stale_units(systemd_dir=None, helper_source=None):
    """Installed units whose rendering from the checkout differs. Needs no root.

    Returns a sorted list, or None when this is not a systemd install.
    Raises the helper's RefreshError when a unit cannot be rendered safely.
    """
    systemd_dir = systemd_dir or SYSTEMD_DIR
    helper_source = helper_source or HELPER_SOURCE
    if not os.path.isfile(os.path.join(systemd_dir, 'ledmatrix.service')):
        return None
    helper = _load_helper(helper_source)
    return sorted(helper.Refresher(systemd_dir=systemd_dir).plan())


def _result(status, message, units=()):
    return {'status': status, 'message': message, 'units': list(units)}


def refresh_after_update(run=None, systemd_dir=None, helper_source=None, helper_path=None):
    """Install the units an update changed. Never raises.

    Returns ``{'status', 'message', 'units'}``; ``message`` is '' when there
    is nothing to tell the user. The defaults are the module's constants,
    read at call time (the test suite points SYSTEMD_DIR away from the host).
    """
    run = run or subprocess.run
    helper_path = helper_path or HELPER_PATH
    try:
        stale = stale_units(systemd_dir, helper_source)
    except Exception as e:  # a broken template must not fail the update itself
        if type(e).__name__ != 'UnitsUnreadable':
            logger.warning("Could not compare the installed systemd units with the new templates: %s", e)
            return _result(FAILED, 'The service settings could not be checked; see logs for details.')
        # Units installed mode 0600 (install_service.sh run on its own, before
        # it set 0644): only root can compare them, so let the helper decide.
        stale = []
        unknown = True
    else:
        unknown = False
        if stale is None:
            return _result(SKIPPED, '')
        if not stale:
            return _result(CURRENT, '')

    names = ', '.join(stale) or 'the LEDMatrix units'
    changes = (f'This update changes service settings ({names}) that are not applied yet. ' if not unknown
               else 'Service settings this update may change could not be checked or applied. ')
    if not os.path.isfile(helper_path):
        logger.warning("Updates cannot install systemd unit changes (%s): %s is not installed; "
                       "they take effect only after a reinstall. %s", names, helper_path, REINSTALL_HINT)
        return _result(NEEDS_REINSTALL, changes + REINSTALL_HINT, stale)
    try:
        result = run(['sudo', '-n', helper_path], capture_output=True, text=True,
                     timeout=TIMEOUT_SECONDS)
    except (subprocess.SubprocessError, OSError) as e:
        logger.warning("Refreshing the systemd units failed: %s", e)
        return _result(FAILED, f'Updating the service settings ({names}) failed; see logs for details.', stale)
    if result.returncode == 0:
        # The helper says what it did: "units refreshed: a b" or "units: up to date".
        done = next((line.split(':', 1)[1].split() for line in (result.stdout or '').splitlines()
                     if line.startswith('units refreshed:')), None)
        if not done:
            # Nothing replaced, so nothing for a rollback to restore.
            if stale:
                logger.warning("The unit helper found nothing to change in %s; the installed "
                               "helper may be older than this version", names)
            return _result(CURRENT, '')
        logger.info("Refreshed systemd units after the update: %s", ', '.join(done))
        return _result(REFRESHED, f'Service settings updated ({", ".join(done)}).', done)
    detail = (result.stderr or result.stdout or '').strip()
    if any(phrase in detail.lower() for phrase in _SUDO_REFUSED):
        logger.warning("Updates cannot install systemd unit changes (%s): no sudo rule for %s; "
                       "they take effect only after a reinstall. %s", names, helper_path, REINSTALL_HINT)
        return _result(NEEDS_REINSTALL, changes + REINSTALL_HINT, stale)
    logger.warning("Refreshing the systemd units failed (exit %s): %s", result.returncode, detail)
    return _result(FAILED, f'Updating the service settings ({names}) failed: {detail or "unknown error"}.',
                   stale)
