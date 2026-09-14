"""Install the automatic-update health check, from the display service.

The weekly updater (web_interface/auto_update.py) will not update LEDMatrix
code unless ledmatrix-update-verify.path and .service are installed: they
restart the services after an update and roll it back if the device is
unhealthy. Installing units takes root and the web interface is not root, and
"SSH in and run an installer" means most people never get updates with a
safety net.

The display service already runs this repository's code as root, so it
installs them -- but only while the user has automatic updates turned on, only
these two units, rendered from the repository's templates for the web
interface's own user, and it reports what happened in
data/auto_update_setup.json for the General tab. It grants nothing new: the
units run as the web user, who can already change the code this process runs.

Called at display startup; the web interface restarts the display service
when the toggle is switched on, so setup happens straight away. Refreshing a
unit whose template changed happens the same way, which is why this compares
content rather than only checking that the files exist.
"""
import json
import logging
import os
import re
import subprocess
import tempfile
import time
from pathlib import Path

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SYSTEMD_DIR = Path('/etc/systemd/system')
SERVICE_UNIT = 'ledmatrix-update-verify.service'
PATH_UNIT = 'ledmatrix-update-verify.path'
UNITS = (SERVICE_UNIT, PATH_UNIT)
WEB_UNIT = 'ledmatrix-web.service'
RESULT_REL = Path('data') / 'auto_update_setup.json'
_USER_RE = re.compile(r'^[a-z_][a-z0-9_-]{0,31}$')


class SetupError(Exception):
    """A reason setup cannot proceed, worded for the General tab."""


def _is_root():
    return hasattr(os, 'geteuid') and os.geteuid() == 0


def _lookup_ids(user):
    try:
        import pwd
        entry = pwd.getpwnam(user)
        return entry.pw_uid, entry.pw_gid
    except (ImportError, KeyError):
        return None


def _directive(text, key):
    match = re.search(rf'^{key}=(.*)$', text or '', re.M)
    return match.group(1).strip() if match else None


def _read(path):
    try:
        return Path(path).read_text(encoding='utf-8')
    except OSError:
        return None


def is_enabled(config):
    return bool((config.get('auto_update') or {}).get('enabled', False))


class UpdateHelperSetup:
    def __init__(self, project_root=PROJECT_ROOT, systemd_dir=SYSTEMD_DIR, run=subprocess.run,
                 is_root=_is_root, lookup_ids=_lookup_ids, clock=time.time):
        self.project_root = Path(project_root)
        self.systemd_dir = Path(systemd_dir)
        self.run = run
        self.is_root = is_root
        self.lookup_ids = lookup_ids
        self.clock = clock
        self.result_file = self.project_root / RESULT_REL
        self._web_ids = None

    def _systemctl(self, *args):
        return self.run(['systemctl', *args], capture_output=True, text=True, timeout=60)

    def _check(self, result, what):
        if result.returncode != 0:
            raise SetupError(f'"{what}" failed: {(result.stderr or result.stdout or "").strip()}')

    def path_active(self):
        try:
            return self._systemctl('is-active', PATH_UNIT).stdout.strip() == 'active'
        except (subprocess.SubprocessError, OSError):
            return False

    def ensure(self, config):
        """Install or refresh the units while automatic updates are on.

        Returns the result recorded for the General tab, or None when there
        was nothing to do (updates off, or not a systemd host at all).
        """
        if not is_enabled(config) or not self.systemd_dir.is_dir():
            return None
        try:
            changed = self._install()
        except SetupError as e:
            return self._report('failed', str(e))
        except (OSError, subprocess.SubprocessError) as e:
            return self._report('failed', f'Could not install the update health check: {e}')
        if changed:
            return self._report('installed', 'Installed the update health check.')
        return self._report('installed', 'The update health check is installed.', quiet=True)

    def _install(self):
        if not self.is_root():
            raise SetupError('The display service is not running as root, so it cannot install the '
                             'update health check. Run "sudo ./scripts/install/install_web_service.sh" once.')

        web_text = _read(self.systemd_dir / WEB_UNIT)
        if web_text is None:
            raise SetupError('The web interface service (ledmatrix-web.service) is not installed.')
        user = _directive(web_text, 'User') or 'root'
        ids = self.lookup_ids(user) if _USER_RE.match(user) else None
        if ids is None:
            raise SetupError(f'The web interface runs as "{user}", which is not a usable account.')
        self._web_ids = ids
        workdir = _directive(web_text, 'WorkingDirectory')
        if not workdir or Path(workdir).resolve() != self.project_root.resolve():
            raise SetupError(f'The web interface service runs from {workdir or "an unknown folder"}, '
                             f'not {self.project_root}.')

        rendered = {}
        for name in UNITS:
            template = _read(self.project_root / 'systemd' / name)
            if template is None:
                raise SetupError(f'The unit template systemd/{name} is missing.')
            rendered[name] = (template.replace('__PROJECT_ROOT_DIR__', str(self.project_root))
                              .replace('__USER__', user))
        # The templates are ordinary repository files. Whatever they say,
        # this root process only installs a service that runs as the web user
        # and a path unit that starts exactly that service.
        if _directive(rendered[SERVICE_UNIT], 'User') != user:
            raise SetupError(f'systemd/{SERVICE_UNIT} does not run as the web interface user; '
                             'refusing to install it.')
        if _directive(rendered[PATH_UNIT], 'Unit') != SERVICE_UNIT:
            raise SetupError(f'systemd/{PATH_UNIT} does not start {SERVICE_UNIT}; refusing to install it.')

        changed = [name for name in UNITS if _read(self.systemd_dir / name) != rendered[name]]
        for name in changed:
            self._write_unit(self.systemd_dir / name, rendered[name])
        if changed:
            self._check(self._systemctl('daemon-reload'), 'systemctl daemon-reload')
            self._check(self._systemctl('enable', PATH_UNIT), f'systemctl enable {PATH_UNIT}')
            self._check(self._systemctl('restart', PATH_UNIT), f'systemctl restart {PATH_UNIT}')
        elif not self.path_active():
            self._check(self._systemctl('enable', '--now', PATH_UNIT), f'systemctl enable --now {PATH_UNIT}')
            changed = [PATH_UNIT]
        if not self.path_active():
            raise SetupError(f'{PATH_UNIT} did not start; see "journalctl -u {PATH_UNIT}".')
        return bool(changed)

    def _write_unit(self, path, text):
        fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=f'.{path.name}.')
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                f.write(text)
            os.chmod(tmp, 0o644)
            os.replace(tmp, path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    def _report(self, status, message, quiet=False):
        previous = None
        try:
            previous = json.loads(_read(self.result_file) or 'null')
        except ValueError:
            pass
        if quiet and isinstance(previous, dict) and previous.get('status') == status:
            return previous  # nothing new; don't rewrite it on every boot
        result = {'status': status, 'message': message, 'at': self.clock()}
        (logger.info if status == 'installed' else logger.warning)("Automatic update setup: %s", message)
        try:
            self.result_file.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=str(self.result_file.parent), prefix='.auto_update_setup_')
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                json.dump(result, f, indent=2)
            os.chmod(tmp, 0o644)
            if self._web_ids and hasattr(os, 'chown'):
                os.chown(tmp, *self._web_ids)
            os.replace(tmp, self.result_file)
        except OSError as e:
            logger.warning("Could not record automatic update setup result: %s", e)
        return result


def ensure_update_helper(config):
    return UpdateHelperSetup().ensure(config)
