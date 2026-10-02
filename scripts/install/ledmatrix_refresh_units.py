#!/usr/bin/python3 -I
"""Refresh the installed LEDMatrix systemd units from the checkout's templates.

Installed by scripts/install/install_service.sh as a root-owned copy,
/usr/local/sbin/ledmatrix-refresh-units, and granted to the web interface's
user by /etc/sudoers.d/ledmatrix_web (scripts/install/lib_sudoers.sh) with
exactly two command lines:

    ledmatrix-refresh-units              (no arguments)
    ledmatrix-refresh-units --restore

An update (Update Code, or the weekly automatic update) pulls new unit
templates into systemd/, but the units systemd runs are the copies in
/etc/systemd/system, which only the installer used to write. So a setting
added to a template -- the render-loop watchdog, a memory limit -- never
reached a device that was already installed. After an update the web
interface runs this, and the next restart picks the new units up.

* **No arguments:** render each installed unit from systemd/<unit> exactly as
  install_service.sh does (__PROJECT_ROOT_DIR__ and __USER__ replaced
  literally), and install the ones whose content differs (comments and blank
  lines aside, as src/startup_validator.py compares them), then
  ``systemctl daemon-reload``. The units replaced are saved first, so the
  automatic update's rollback can put them back.
* ``--restore``: put back the units the last refresh replaced, and
  daemon-reload. Nothing saved means nothing to do.
* ``--check``: print the units that would change, one per line. Needs no
  root and changes nothing.

What it trusts, and why. It takes no other input: the project directory and
the web interface's user come from the installed, root-owned
ledmatrix.service and ledmatrix-web.service, not from the caller, and sudo
strips the caller's environment (``-I`` ignores the PYTHON* variables too).
It only replaces units that are already installed, only the four
install_service.sh installs, and only with a rendering that keeps each unit's
User= (root for the display, the web user for the others) and
WorkingDirectory=. The templates are files the web user can edit -- but so is
run.py, which ledmatrix.service already runs as root, so a template grants
nothing that user did not have; the checks keep a damaged or hostile template
from changing who a unit runs as, and keep this from reading anything but a
regular file under the checkout's systemd/ folder.

Standard library only, and no imports from the checkout: the installed copy
must not run code the web user can change.
"""
import json
import os
import re
import stat
import subprocess  # nosec B404 - fixed argv, no shell  # nosemgrep
import sys
import tempfile

SYSTEMD_DIR = '/etc/systemd/system'
#: Root-only: the units the last refresh replaced, for --restore.
BACKUP_DIR = '/var/lib/ledmatrix/unit-backup'
MANIFEST = 'manifest.json'
INSTALLED_PATH = '/usr/local/sbin/ledmatrix-refresh-units'

DISPLAY_UNIT = 'ledmatrix.service'
WEB_UNIT = 'ledmatrix-web.service'
VERIFY_SERVICE = 'ledmatrix-update-verify.service'
VERIFY_PATH = 'ledmatrix-update-verify.path'
#: What install_service.sh installs, in its order. Nothing else is touched.
UNITS = (DISPLAY_UNIT, WEB_UNIT, VERIFY_SERVICE, VERIFY_PATH)

MAX_TEMPLATE_BYTES = 64 * 1024
_USER_RE = re.compile(r'^[a-z_][a-z0-9_-]{0,31}$')
#: systemd expands % specifiers, and a quote, backslash or line break would
#: be reinterpreted in a unit file (src/auto_update_setup.py refuses the same).
#: (On Windows, where the tests also run, a backslash is the path separator.)
_UNSAFE_PATH_CHARS = set('%"') | ({'\\'} if os.sep == '/' else set())

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_USAGE = 2


class RefreshError(Exception):
    """Why the units were left alone, in words for the web interface's log."""


class UnitsUnreadable(RefreshError):
    """An installed unit is not readable by this (unprivileged) user.

    install_service.sh used to leave units mode 0600 (first_time_install.sh's
    Step 8.1 makes them 0644), so ``--check`` as the web user cannot always
    tell; the root helper itself can.
    """


def directive_values(text, key):
    """Every value of ``key=`` in a unit's text, in order (systemd allows spaces around ``=``)."""
    return [m.group(1).strip() for m in re.finditer(rf'^[ \t]*{key}[ \t]*=(.*)$', text or '', re.M)]


def layout_problem(text, section, keys):
    """What would make ``directive_values`` misread the unit as systemd reads it, or None.

    A ``User=`` inside a backslash-continued line is part of the line before,
    and one under [Unit] is ignored, so either could pass a check that systemd
    then does not apply. Neither appears in the shipped templates.
    """
    current = None
    for raw in (text or '').splitlines():
        line = raw.strip()
        if not line or line.startswith(('#', ';')):
            continue
        if line.endswith('\\'):
            return 'continues a line with a backslash'
        if line.startswith('[') and line.endswith(']'):
            current = line[1:-1]
            continue
        key = line.split('=', 1)[0].strip()
        if key in keys and current != section:
            return f'sets {key}= outside [{section}]'
    return None


def unit_body(text):
    """A unit's meaningful lines in order: no comments, no blank lines.

    The same comparison src/startup_validator.py uses for its drift warning,
    so what this refreshes is exactly what that warns about.
    """
    lines = []
    for line in (text or '').splitlines():
        line = line.strip()
        if line and not line.startswith('#'):
            lines.append(line)
    return '\n'.join(lines)


def render(template, project_root, user):
    """install_service.sh's ``sed "s|__PROJECT_ROOT_DIR__|...|g; s|__USER__|...|g"``."""
    return template.replace('__PROJECT_ROOT_DIR__', project_root).replace('__USER__', user)


def _read_regular(path, limit=MAX_TEMPLATE_BYTES, dir_fd=None):
    """A regular file's text, never through a symlink, a FIFO or a device."""
    flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0)
    kwargs = {'dir_fd': dir_fd} if dir_fd is not None else {}
    fd = os.open(path, flags, **kwargs)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise RefreshError(f'{path} is not a regular file')
        if info.st_size > limit:
            raise RefreshError(f'{path} is larger than {limit} bytes')
        data = b''
        while True:
            chunk = os.read(fd, limit + 1 - len(data))
            if not chunk:
                break
            data += chunk
            if len(data) > limit:
                raise RefreshError(f'{path} is larger than {limit} bytes')
    finally:
        os.close(fd)
    if b'\0' in data:
        raise RefreshError(f'{path} is not a text file')
    try:
        return data.decode('utf-8')
    except UnicodeDecodeError as e:
        raise RefreshError(f'{path} is not UTF-8') from e


def _read_installed(systemd_dir, name):
    path = os.path.join(systemd_dir, name)
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return f.read()
    except FileNotFoundError:
        return None
    except PermissionError as e:
        raise UnitsUnreadable(f'cannot read the installed {name}: {e}') from e
    except (OSError, UnicodeDecodeError) as e:
        raise RefreshError(f'cannot read the installed {name}: {e}') from e


def _lookup_user(user):
    try:
        import pwd
    except ImportError:  # not a POSIX host (the tests on Windows)
        return True
    try:
        pwd.getpwnam(user)
        return True
    except KeyError:
        return False


class Refresher:
    def __init__(self, systemd_dir=SYSTEMD_DIR, backup_dir=BACKUP_DIR, run=subprocess.run,
                 is_root=None, user_exists=_lookup_user, log=None):
        self.systemd_dir = systemd_dir
        self.backup_dir = backup_dir
        self.run = run
        self.is_root = is_root or (lambda: hasattr(os, 'geteuid') and os.geteuid() == 0)
        self.user_exists = user_exists
        self.log = log or (lambda msg: print(msg, flush=True))

    # -- what the installed units say -------------------------------------

    def context(self, installed):
        """(project root, web user) from the installed, root-owned units."""
        display = installed.get(DISPLAY_UNIT)
        if display is None:
            raise RefreshError(f'{DISPLAY_UNIT} is not installed; run scripts/install/install_service.sh')
        roots = directive_values(display, 'WorkingDirectory')
        if len(roots) != 1:
            raise RefreshError(f'the installed {DISPLAY_UNIT} does not name one WorkingDirectory')
        root = roots[0]
        if (not os.path.isabs(root) or any(ch in _UNSAFE_PATH_CHARS or ord(ch) < 32 for ch in root)
                or os.path.normpath(root) != root):
            raise RefreshError(f'the installed {DISPLAY_UNIT} runs from {root!r}, which cannot be used')
        if not os.path.isdir(root):
            raise RefreshError(f'{root} (the installed {DISPLAY_UNIT} WorkingDirectory) does not exist')

        user = None
        web = installed.get(WEB_UNIT)
        if web is not None:
            users = directive_values(web, 'User')
            user = users[0] if len(users) == 1 else ('root' if not users else None)
            if user is None or not _USER_RE.match(user) or not self.user_exists(user):
                raise RefreshError(f'the installed {WEB_UNIT} runs as an account that cannot be used')
            if directive_values(web, 'WorkingDirectory') != [root]:
                raise RefreshError(f'the installed {WEB_UNIT} and {DISPLAY_UNIT} run from different folders')
        return root, user

    @staticmethod
    def expected_user(name, web_user):
        return 'root' if name == DISPLAY_UNIT else web_user

    def _template(self, root, name):
        """systemd/<name> under the checkout, as a regular file, never via a symlink."""
        dir_flags = os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0) | getattr(os, 'O_NOFOLLOW', 0)
        if os.open in getattr(os, 'supports_dir_fd', set()):
            try:
                dfd = os.open(os.path.join(root, 'systemd'), dir_flags)
            except OSError as e:
                raise RefreshError(f'cannot open {root}/systemd: {e}') from e
            try:
                return _read_regular(name, dir_fd=dfd)
            except FileNotFoundError:
                return None
            except OSError as e:
                raise RefreshError(f'cannot read systemd/{name}: {e}') from e
            finally:
                os.close(dfd)
        path = os.path.join(root, 'systemd', name)
        if os.path.islink(os.path.join(root, 'systemd')):
            raise RefreshError(f'{root}/systemd is a symlink')
        try:
            return _read_regular(path)
        except FileNotFoundError:
            return None
        except OSError as e:
            raise RefreshError(f'cannot read systemd/{name}: {e}') from e

    def _validate(self, name, rendered, root, user):
        problem = layout_problem(rendered, 'Service', ('User', 'WorkingDirectory'))
        if problem:
            raise RefreshError(f'systemd/{name} {problem}; refusing to install it')
        if directive_values(rendered, 'User') != [user]:
            raise RefreshError(f'systemd/{name} would not run as {user}; refusing to install it')
        if directive_values(rendered, 'WorkingDirectory') != [root]:
            raise RefreshError(f'systemd/{name} would not run from {root}; refusing to install it')

    def plan(self):
        """{unit: (installed text, new text)} for every installed unit that would change.

        Raises RefreshError, and so changes nothing, if any unit cannot be
        rendered safely: four units refreshed as a set or not at all.
        """
        installed = {name: _read_installed(self.systemd_dir, name) for name in UNITS}
        root, web_user = self.context(installed)
        changes = {}
        for name in UNITS:
            current = installed[name]
            if current is None:
                continue  # never installed here: installing is the installer's job
            user = self.expected_user(name, web_user)
            if user is None:
                continue  # the web unit is not installed, so neither is its user
            template = self._template(root, name)
            if template is None:
                continue  # a version without this unit leaves the installed one alone
            rendered = render(template, root, user)
            # A path unit runs nothing itself; what matters is what it starts.
            if name.endswith('.service'):
                self._validate(name, rendered, root, user)
            else:
                self._validate_path(name, rendered)
            if unit_body(rendered) != unit_body(current):
                changes[name] = (current, rendered)
        return changes

    def _validate_path(self, name, rendered):
        problem = layout_problem(rendered, 'Path', ('Unit',))
        if problem:
            raise RefreshError(f'systemd/{name} {problem}; refusing to install it')
        if directive_values(rendered, 'Unit') != [VERIFY_SERVICE]:
            raise RefreshError(f'systemd/{name} does not start {VERIFY_SERVICE}; refusing to install it')
        if directive_values(rendered, 'User'):
            raise RefreshError(f'systemd/{name} sets User=; refusing to install it')

    # -- writing ------------------------------------------------------------

    def _write_unit(self, name, text):
        fd, tmp = tempfile.mkstemp(dir=self.systemd_dir, prefix=f'.{name}.')
        try:
            with os.fdopen(fd, 'w', encoding='utf-8', newline='\n') as f:
                f.write(text)
            os.chmod(tmp, 0o644)
            os.replace(tmp, os.path.join(self.systemd_dir, name))
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    def _backup_dir(self):
        """The backup folder, created root-only; refused if it is not a plain folder."""
        os.makedirs(os.path.dirname(self.backup_dir), mode=0o755, exist_ok=True)
        try:
            os.mkdir(self.backup_dir, 0o700)
        except FileExistsError:
            pass
        info = os.lstat(self.backup_dir)
        if not stat.S_ISDIR(info.st_mode):
            raise RefreshError(f'{self.backup_dir} is not a folder')
        if hasattr(os, 'geteuid') and info.st_uid != os.geteuid():
            raise RefreshError(f'{self.backup_dir} is not owned by root')
        return self.backup_dir

    def _clear_backup(self, folder):
        for entry in os.listdir(folder):
            path = os.path.join(folder, entry)
            if os.path.isfile(path) or os.path.islink(path):
                os.unlink(path)

    def _systemctl(self, *args):
        result = self.run(['systemctl', *args], capture_output=True, text=True, timeout=60)
        if result.returncode != 0:
            raise RefreshError(f'"systemctl {" ".join(args)}" failed: '
                               f'{(result.stderr or result.stdout or "").strip()}')

    def _restart_path_unit_if_active(self, names):
        """A rewritten path unit watches the old path until it is restarted."""
        if VERIFY_PATH not in names:
            return
        state = self.run(['systemctl', 'is-active', VERIFY_PATH], capture_output=True, text=True, timeout=30)
        if (state.stdout or '').strip() == 'active':
            self._systemctl('restart', VERIFY_PATH)

    def refresh(self):
        if not self.is_root():
            raise RefreshError('must run as root (sudo)')
        changes = self.plan()
        folder = self._backup_dir()
        # Always reset: the backup belongs to this refresh, so a --restore
        # after an update that changed nothing restores nothing.
        self._clear_backup(folder)
        if not changes:
            self.log('units: up to date')
            return []
        for name, (current, _) in changes.items():
            with open(os.path.join(folder, name), 'w', encoding='utf-8', newline='\n') as f:
                f.write(current)
        with open(os.path.join(folder, MANIFEST), 'w', encoding='utf-8') as f:
            json.dump({'units': sorted(changes)}, f)
        for name, (_, rendered) in changes.items():
            self._write_unit(name, rendered)
        self._systemctl('daemon-reload')
        self._restart_path_unit_if_active(changes)
        self.log('units refreshed: ' + ' '.join(sorted(changes)))
        return sorted(changes)

    def restore(self):
        if not self.is_root():
            raise RefreshError('must run as root (sudo)')
        folder = self._backup_dir()
        try:
            manifest = json.loads(_read_regular(os.path.join(folder, MANIFEST)))
        except FileNotFoundError:
            self.log('units: nothing to restore')
            return []
        names = [n for n in (manifest or {}).get('units', []) if n in UNITS]
        for name in names:
            self._write_unit(name, _read_regular(os.path.join(folder, name)))
        self._systemctl('daemon-reload')
        self._restart_path_unit_if_active(names)
        self._clear_backup(folder)
        self.log('units restored: ' + ' '.join(names))
        return names


def main(argv, refresher=None):
    args = argv[1:]
    if args not in ([], ['--restore'], ['--check']):
        print('usage: ledmatrix-refresh-units [--restore | --check]', file=sys.stderr)
        return EXIT_USAGE
    refresher = refresher or Refresher()
    try:
        if args == ['--check']:
            for name in sorted(refresher.plan()):
                print(name)
        elif args == ['--restore']:
            refresher.restore()
        else:
            refresher.refresh()
    except (RefreshError, OSError, subprocess.SubprocessError, ValueError) as e:
        print(f'ledmatrix-refresh-units: {e}', file=sys.stderr)
        return EXIT_FAILED
    return EXIT_OK


if __name__ == '__main__':
    # Only as the installed program: sudo already sets a secure PATH, and
    # this pins the one systemctl comes from. (Not in main(), which the
    # tests call in-process.)
    os.environ['PATH'] = '/usr/sbin:/usr/bin:/sbin:/bin'
    sys.exit(main(sys.argv))
