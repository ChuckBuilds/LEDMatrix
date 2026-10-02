"""Updates refresh the installed systemd units: the root helper and its callers.

An update moved the checkout, and with it systemd/*.service, but systemd runs
the copies in /etc/systemd/system, which only the installer wrote. So unit
settings added after a device was installed (#687's render-loop watchdog)
never reached it. scripts/install/ledmatrix_refresh_units.py, installed
root-owned as /usr/local/sbin/ledmatrix-refresh-units and granted to the web
user by exact command line, now installs changed units after an update, and
puts the previous ones back when the automatic update rolls back.

The helper runs as root on input the web user can edit (the templates), so
most of these are about what it refuses.
"""
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_spec = importlib.util.spec_from_file_location(
    'ledmatrix_refresh_units', ROOT / 'scripts' / 'install' / 'ledmatrix_refresh_units.py')
ru = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ru)

from web_interface import unit_refresh  # noqa: E402

try:
    import pwd
    # The web side checks the account exists, so use one that does.
    WEB_USER = pwd.getpwuid(os.getuid()).pw_name
except ImportError:  # Windows
    WEB_USER = 'ledpi'


class Host:
    """A project checkout, an /etc/systemd/system, and a fake systemctl."""

    def __init__(self, tmp_path, web_user=WEB_USER):
        self.project = tmp_path / 'LEDMatrix'
        shutil.copytree(ROOT / 'systemd', self.project / 'systemd')
        self.systemd = tmp_path / 'etc-systemd-system'
        self.systemd.mkdir()
        self.backup = tmp_path / 'var-lib-ledmatrix' / 'unit-backup'
        self.web_user = web_user
        self.calls = []
        self.path_active = True
        self.root = True
        for name in ru.UNITS:
            self.install(name)

    def template(self, name):
        return (self.project / 'systemd' / name).read_text(encoding='utf-8')

    def set_template(self, name, text):
        (self.project / 'systemd' / name).write_text(text, encoding='utf-8', newline='\n')

    def rendered(self, name, text=None):
        user = 'root' if name == ru.DISPLAY_UNIT else self.web_user
        return ru.render(text if text is not None else self.template(name), str(self.project), user)

    def install(self, name, text=None):
        (self.systemd / name).write_text(self.rendered(name, text), encoding='utf-8', newline='\n')

    def installed(self, name):
        path = self.systemd / name
        return path.read_text(encoding='utf-8') if path.exists() else None

    def run(self, args, **kwargs):
        self.calls.append(list(args))
        if args[:2] == ['systemctl', 'is-active']:
            out = 'active\n' if self.path_active else 'inactive\n'
            return subprocess.CompletedProcess(args, 0, stdout=out, stderr='')
        return subprocess.CompletedProcess(args, 0, stdout='', stderr='')

    def refresher(self, log=None):
        return ru.Refresher(systemd_dir=str(self.systemd), backup_dir=str(self.backup), run=self.run,
                            is_root=lambda: self.root, user_exists=lambda user: True,
                            log=log or (lambda m: None))

    @property
    def reloads(self):
        return self.calls.count(['systemctl', 'daemon-reload'])


def watchdog_added(text):
    """The kind of change #687 made: a new directive in [Service]."""
    return text.replace('[Service]\n', '[Service]\nWatchdogSec=60\n', 1)


@pytest.fixture
def host(tmp_path):
    return Host(tmp_path)


# -- refresh ------------------------------------------------------------------

def test_units_that_match_are_left_alone(host):
    assert host.refresher().refresh() == []
    assert host.reloads == 0


def test_a_changed_template_is_installed_and_systemd_reloaded(host):
    old = host.installed(ru.DISPLAY_UNIT)
    host.set_template(ru.DISPLAY_UNIT, watchdog_added(host.template(ru.DISPLAY_UNIT)))

    assert host.refresher().refresh() == [ru.DISPLAY_UNIT]
    assert 'WatchdogSec=60' in host.installed(ru.DISPLAY_UNIT)
    assert host.installed(ru.DISPLAY_UNIT) == host.rendered(ru.DISPLAY_UNIT)
    assert host.reloads == 1
    # Only the unit that changed is replaced, and the one it replaced is kept.
    assert (host.backup / ru.DISPLAY_UNIT).read_text(encoding='utf-8') == old
    assert json.loads((host.backup / ru.MANIFEST).read_text()) == {'units': [ru.DISPLAY_UNIT]}


def test_the_web_unit_keeps_the_web_users_account(host):
    host.set_template(ru.WEB_UNIT, watchdog_added(host.template(ru.WEB_UNIT)))
    host.refresher().refresh()
    assert ru.directive_values(host.installed(ru.WEB_UNIT), 'User') == [WEB_USER]
    assert ru.directive_values(host.installed(ru.DISPLAY_UNIT), 'User') == ['root']


def test_comment_only_changes_are_not_a_refresh(host):
    host.set_template(ru.DISPLAY_UNIT, '# a new comment\n\n' + host.template(ru.DISPLAY_UNIT))
    assert host.refresher().refresh() == []
    assert host.reloads == 0


def test_a_unit_that_was_never_installed_is_not_installed(host):
    (host.systemd / ru.VERIFY_SERVICE).unlink()
    (host.systemd / ru.VERIFY_PATH).unlink()
    host.set_template(ru.VERIFY_SERVICE, watchdog_added(host.template(ru.VERIFY_SERVICE)))
    host.refresher().refresh()
    assert host.installed(ru.VERIFY_SERVICE) is None


def test_a_changed_path_unit_is_restarted_so_it_watches_the_new_path(host):
    host.set_template(ru.VERIFY_PATH, host.template(ru.VERIFY_PATH).replace(
        '[Path]\n', '[Path]\nMakeDirectory=yes\n'))
    host.refresher().refresh()
    assert ['systemctl', 'restart', ru.VERIFY_PATH] in host.calls


def test_it_must_run_as_root(host):
    host.root = False
    host.set_template(ru.DISPLAY_UNIT, watchdog_added(host.template(ru.DISPLAY_UNIT)))
    with pytest.raises(ru.RefreshError, match='root'):
        host.refresher().refresh()
    assert 'WatchdogSec=60' not in host.installed(ru.DISPLAY_UNIT)


# -- what it refuses ------------------------------------------------------------

@pytest.mark.parametrize('unit, edit', [
    # The web interface's unit switched to root by a template edit.
    (ru.WEB_UNIT, lambda t: t.replace('User=__USER__', 'User=root')),
    # The display's unit switched to another account.
    (ru.DISPLAY_UNIT, lambda t: t.replace('User=root', 'User=nobody')),
    # A second User= line.
    (ru.VERIFY_SERVICE, lambda t: t.replace('[Service]\n', '[Service]\nUser=root\n', 1)),
    # Run from somewhere else.
    (ru.DISPLAY_UNIT, lambda t: t.replace('WorkingDirectory=__PROJECT_ROOT_DIR__', 'WorkingDirectory=/tmp')),
    # A second User= written with spaces, which systemd accepts (last one wins).
    # Placed in [Service] (before [Install]), where it is not a layout problem.
    (ru.WEB_UNIT, lambda t: t.replace('\n[Install]', 'User = root\n\n[Install]')),
    # The web user's User= moved to [Unit], where systemd ignores it (so root).
    (ru.WEB_UNIT, lambda t: t.replace('User=__USER__\n', '').replace('[Unit]\n', '[Unit]\nUser=__USER__\n')),
    # The User= line hidden inside a continued line, where systemd does not see it.
    (ru.WEB_UNIT, lambda t: t.replace('User=__USER__\n', '').replace(
        'Description=LED Matrix Web Interface Service\n',
        'Description=LED Matrix Web Interface Service \\\\\nUser=__USER__\n')),
    # A path unit that starts something else.
    (ru.VERIFY_PATH, lambda t: t.replace('Unit=ledmatrix-update-verify.service', 'Unit=ledmatrix.service')),
])
def test_a_template_that_changes_who_or_where_is_refused_and_nothing_changes(host, unit, edit):
    before = {name: host.installed(name) for name in ru.UNITS}
    # A legitimate change alongside, which must not go in either.
    host.set_template(ru.DISPLAY_UNIT, watchdog_added(host.template(ru.DISPLAY_UNIT)))
    host.set_template(unit, edit(host.template(unit)))
    with pytest.raises(ru.RefreshError):
        host.refresher().refresh()
    assert {name: host.installed(name) for name in ru.UNITS} == before
    assert host.reloads == 0


def test_the_project_folder_comes_from_the_installed_unit_not_the_caller(host, tmp_path):
    # The installed display unit names the project; a WorkingDirectory that
    # is not an existing absolute folder is refused before any template is read.
    host.install(ru.DISPLAY_UNIT, host.template(ru.DISPLAY_UNIT).replace(
        'WorkingDirectory=__PROJECT_ROOT_DIR__', 'WorkingDirectory=relative/path'))
    with pytest.raises(ru.RefreshError, match='cannot be used'):
        host.refresher().plan()


def test_without_the_display_unit_installed_nothing_is_done(host):
    (host.systemd / ru.DISPLAY_UNIT).unlink()
    with pytest.raises(ru.RefreshError, match='not installed'):
        host.refresher().plan()


@pytest.mark.skipif(not hasattr(os, 'O_NOFOLLOW'), reason='POSIX only')
def test_a_template_symlink_is_not_followed(host, tmp_path):
    secret = tmp_path / 'secret'
    secret.write_text(host.template(ru.DISPLAY_UNIT) + 'Environment=SECRET=1\n', encoding='utf-8')
    target = host.project / 'systemd' / ru.DISPLAY_UNIT
    target.unlink()
    target.symlink_to(secret)
    with pytest.raises(ru.RefreshError):
        host.refresher().plan()


@pytest.mark.skipif(not hasattr(os, 'O_NOFOLLOW'), reason='POSIX only')
def test_a_symlinked_systemd_folder_is_not_followed(host, tmp_path):
    elsewhere = tmp_path / 'elsewhere'
    shutil.move(str(host.project / 'systemd'), str(elsewhere))
    (host.project / 'systemd').symlink_to(elsewhere, target_is_directory=True)
    with pytest.raises(ru.RefreshError):
        host.refresher().plan()


def test_an_oversized_template_is_refused(host):
    host.set_template(ru.DISPLAY_UNIT, host.template(ru.DISPLAY_UNIT) + '#' * (ru.MAX_TEMPLATE_BYTES + 1))
    with pytest.raises(ru.RefreshError, match='larger'):
        host.refresher().plan()


def test_main_leaves_the_callers_environment_alone(host):
    """main() runs in-process in these tests; pinning PATH belongs to the installed program."""
    before = os.environ.get('PATH')
    ru.main(['ledmatrix-refresh-units', '--check'], refresher=host.refresher())
    assert os.environ.get('PATH') == before


@pytest.mark.parametrize('argv', [
    ['--restore', 'x'], ['--refresh'], ['/etc/passwd'], ['--check', '--restore'], ['']])
def test_any_other_command_line_is_refused(argv):
    class Boom:
        def __getattr__(self, name):
            raise AssertionError('must not run')
    assert ru.main(['ledmatrix-refresh-units', *argv], refresher=Boom()) == ru.EXIT_USAGE


def test_main_reports_a_refusal_as_a_failure(host, capsys):
    host.set_template(ru.WEB_UNIT, host.template(ru.WEB_UNIT).replace('User=__USER__', 'User=root'))
    assert ru.main(['ledmatrix-refresh-units'], refresher=host.refresher()) == ru.EXIT_FAILED
    assert 'refusing' in capsys.readouterr().err


# -- restore ------------------------------------------------------------------

def test_restore_puts_back_exactly_what_the_refresh_replaced(host):
    # A hand-edited installed unit: the rollback must give back this file,
    # not a rendering of the old template.
    hand_edited = host.installed(ru.DISPLAY_UNIT) + '# edited by hand\n'
    (host.systemd / ru.DISPLAY_UNIT).write_text(hand_edited, encoding='utf-8', newline='\n')
    untouched = host.installed(ru.WEB_UNIT)
    host.set_template(ru.DISPLAY_UNIT, watchdog_added(host.template(ru.DISPLAY_UNIT)))
    host.refresher().refresh()

    assert host.refresher().restore() == [ru.DISPLAY_UNIT]
    assert host.installed(ru.DISPLAY_UNIT) == hand_edited
    assert host.installed(ru.WEB_UNIT) == untouched
    assert host.reloads == 2
    assert not (host.backup / ru.MANIFEST).exists(), 'a second restore must not repeat it'
    assert host.refresher().restore() == []


def test_restore_after_an_update_that_changed_no_units_restores_nothing(host):
    host.set_template(ru.DISPLAY_UNIT, watchdog_added(host.template(ru.DISPLAY_UNIT)))
    host.refresher().refresh()          # an earlier update...
    newer = host.installed(ru.DISPLAY_UNIT)
    host.refresher().refresh()          # ...then one that changed no units
    assert host.refresher().restore() == []
    assert host.installed(ru.DISPLAY_UNIT) == newer


def test_restore_must_run_as_root(host):
    host.root = False
    with pytest.raises(ru.RefreshError, match='root'):
        host.refresher().restore()


# -- the real templates and install_service.sh ----------------------------------

def test_every_shipped_template_passes_the_helpers_checks(tmp_path):
    host = Host(tmp_path)
    for name in ru.UNITS:
        host.set_template(name, watchdog_added(host.template(name)) if name.endswith('.service')
                          else host.template(name))
    assert host.refresher().refresh() == sorted(n for n in ru.UNITS if n.endswith('.service'))


@pytest.mark.skipif(not sys.platform.startswith('linux'), reason='runs sed as install_service.sh does')
def test_rendering_matches_install_service_sh(tmp_path):
    """Same text as the installer's sed, including characters sed treats specially."""
    lib = ROOT / 'scripts' / 'install' / 'lib_systemd_render.sh'
    for project in ('/home/pi/LEDMatrix', '/opt/led matrix&co'):
        for name in ru.UNITS:
            user = 'root' if name == ru.DISPLAY_UNIT else 'pi'
            script = (f'source "{lib}"; R=$(sed_escape_replacement "$1"); U=$(sed_escape_replacement "$2"); '
                      f'sed "s|__PROJECT_ROOT_DIR__|$R|g; s|__USER__|$U|g" "$3"')
            out = subprocess.run(['bash', '-c', script, 'x', project, user, str(ROOT / 'systemd' / name)],
                                 capture_output=True, text=True, check=True).stdout
            template = (ROOT / 'systemd' / name).read_text(encoding='utf-8')
            assert ru.render(template, project, user) == out, name


def test_install_service_installs_the_helper_root_owned_at_the_granted_path():
    text = (ROOT / 'scripts' / 'install' / 'install_service.sh').read_text(encoding='utf-8')
    assert 'scripts/install/ledmatrix_refresh_units.py' in text
    m = re.search(r'install -D -o root -g root -m 0755 "\$REFRESH_UNITS_SRC" "\$REFRESH_UNITS_DEST"', text)
    assert m, 'install_service.sh must install the helper root:root 0755'
    assert f'REFRESH_UNITS_DEST={ru.INSTALLED_PATH}' in text


def test_every_caller_names_the_same_helper_path():
    lib = (ROOT / 'scripts' / 'install' / 'lib_sudoers.sh').read_text(encoding='utf-8')
    verifier = (ROOT / 'scripts' / 'utils' / 'auto_update_verify.py').read_text(encoding='utf-8')
    assert f'LEDMATRIX_REFRESH_UNITS_PATH={ru.INSTALLED_PATH}' in lib
    assert unit_refresh.HELPER_PATH == ru.INSTALLED_PATH
    assert f"REFRESH_UNITS_PATH = '{ru.INSTALLED_PATH}'" in verifier
    assert ru.INSTALLED_PATH.startswith('/usr/local/sbin/'), 'must live outside the user-owned checkout'


def test_the_helper_imports_nothing_from_the_checkout():
    source = (ROOT / 'scripts' / 'install' / 'ledmatrix_refresh_units.py').read_text(encoding='utf-8')
    imports = re.findall(r'^\s*(?:from|import)\s+([\w.]+)', source, re.M)
    assert not [m for m in imports if m.split('.')[0] in ('src', 'web_interface', 'scripts')]
    assert source.startswith('#!/usr/bin/python3 -I\n'), 'isolated mode: no PYTHON* env, no user site'


# -- the web interface's side (web_interface/unit_refresh.py) ---------------------

class Sudo:
    """sudo: refuses (``rc``/``stderr``), or runs the real helper as root against ``host``."""

    def __init__(self, host=None, rc=0, stderr=''):
        self.host, self.rc, self.stderr, self.calls = host, rc, stderr, []
        self.as_root = False

    def __call__(self, args, **kwargs):
        self.calls.append(list(args))
        if self.rc or self.host is None:
            return subprocess.CompletedProcess(args, self.rc, stdout='', stderr=self.stderr)
        lines = []
        self.as_root = True
        try:
            rc = ru.main(['ledmatrix-refresh-units', *args[3:]], refresher=self.host.refresher(lines.append))
        finally:
            self.as_root = False
        return subprocess.CompletedProcess(args, rc, stdout='\n'.join(lines) + '\n', stderr='')


def _web(host, tmp_path, sudo, helper_installed=True):
    helper = tmp_path / 'usr-local-sbin' / 'ledmatrix-refresh-units'
    if helper_installed:
        helper.parent.mkdir(exist_ok=True)
        helper.write_text('#!/bin/true\n')
    return unit_refresh.refresh_after_update(run=sudo, systemd_dir=str(host.systemd),
                                             helper_path=str(helper))


def _stale(host):
    # The web side compares the installed units with the checkout's templates.
    host.set_template(ru.DISPLAY_UNIT, watchdog_added(host.template(ru.DISPLAY_UNIT)))


def test_web_side_does_nothing_when_the_units_match(host, tmp_path):
    sudo = Sudo(host)
    result = _web(host, tmp_path, sudo)
    assert result['status'] == unit_refresh.CURRENT and sudo.calls == []
    assert result['message'] == ''


def test_web_side_runs_the_helper_through_sudo_with_no_arguments(host, tmp_path):
    _stale(host)
    sudo = Sudo(host)
    result = _web(host, tmp_path, sudo)
    assert result['status'] == unit_refresh.REFRESHED
    assert result['units'] == [ru.DISPLAY_UNIT]
    assert len(sudo.calls) == 1 and sudo.calls[0][:2] == ['sudo', '-n'] and len(sudo.calls[0]) == 3
    assert 'ledmatrix.service' in result['message']
    assert 'WatchdogSec=60' in host.installed(ru.DISPLAY_UNIT)


@pytest.fixture
def unreadable(monkeypatch, host):
    """Installed units only root can read (install_service.sh used to leave them 0600)."""
    real = ru._read_installed
    sudo = Sudo(host)

    def read(systemd_dir, name):
        if not sudo.as_root:
            raise ru.UnitsUnreadable(f'cannot read the installed {name}: Permission denied')
        return real(systemd_dir, name)
    monkeypatch.setattr(ru, '_read_installed', read)
    monkeypatch.setattr(unit_refresh, '_load_helper', lambda path=None: ru)
    return sudo


def test_web_side_lets_the_helper_decide_when_it_cannot_read_the_units(host, tmp_path, unreadable):
    _stale(host)
    result = _web(host, tmp_path, unreadable)
    assert len(unreadable.calls) == 1
    assert result['status'] == unit_refresh.REFRESHED and result['units'] == [ru.DISPLAY_UNIT]


def test_web_side_unreadable_and_already_current_is_current(host, tmp_path, unreadable):
    result = _web(host, tmp_path, unreadable)
    assert result['status'] == unit_refresh.CURRENT and result['message'] == ''


def test_web_side_unreadable_without_the_rule_asks_for_a_reinstall(host, tmp_path, unreadable, caplog):
    unreadable.rc, unreadable.stderr = 1, 'sudo: a password is required'
    result = _web(host, tmp_path, unreadable)
    assert result['status'] == unit_refresh.NEEDS_REINSTALL
    assert 'could not be checked' in result['message']


def test_web_side_trusts_the_helper_about_what_changed(host, tmp_path):
    """An older installed helper that renders differently changed nothing: nothing to roll back."""
    _stale(host)

    def older_helper(args, **kwargs):
        return subprocess.CompletedProcess(args, 0, stdout='units: up to date\n', stderr='')
    result = _web(host, tmp_path, older_helper)
    assert result['status'] == unit_refresh.CURRENT


def test_web_side_without_the_helper_asks_for_a_reinstall(host, tmp_path, caplog):
    _stale(host)
    sudo = Sudo()
    result = _web(host, tmp_path, sudo, helper_installed=False)
    assert result['status'] == unit_refresh.NEEDS_REINSTALL and sudo.calls == []
    assert 'first_time_install.sh' in result['message']
    assert 'reinstall' in caplog.text


@pytest.mark.parametrize('stderr', [
    'sudo: a password is required',
    'Sorry, user ledpi is not allowed to run \'/usr/local/sbin/ledmatrix-refresh-units\' as root on ledpi.',
])
def test_web_side_without_the_sudo_rule_asks_for_a_reinstall(host, tmp_path, stderr, caplog):
    _stale(host)
    result = _web(host, tmp_path, Sudo(rc=1, stderr=stderr))
    assert result['status'] == unit_refresh.NEEDS_REINSTALL
    assert 'configure_web_sudo.sh' in result['message']
    assert 'no sudo rule' in caplog.text


def test_web_side_reports_a_helper_refusal_as_a_failure(host, tmp_path):
    _stale(host)
    result = _web(host, tmp_path, Sudo(rc=1, stderr='ledmatrix-refresh-units: systemd/x refusing'))
    assert result['status'] == unit_refresh.FAILED
    assert 'refusing' in result['message']


def test_web_side_on_a_machine_without_the_units_does_nothing(tmp_path):
    sudo = Sudo()
    result = unit_refresh.refresh_after_update(run=sudo, systemd_dir=str(tmp_path))
    assert result['status'] == unit_refresh.SKIPPED and sudo.calls == []


def test_web_side_never_raises_on_a_broken_template(host, tmp_path):
    host.set_template(ru.WEB_UNIT, host.template(ru.WEB_UNIT).replace('User=__USER__', 'User=root'))
    sudo = Sudo()
    result = _web(host, tmp_path, sudo)
    assert result['status'] == unit_refresh.FAILED and sudo.calls == []
