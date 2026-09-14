"""The automatic update's health check and rollback (scripts/utils/auto_update_verify.py).

This is what stands between an unattended update and a device that no longer
works, so each way an update can fail is exercised against a real git repo:
the new commit is checked out, the services are "restarted", and whether they
come up healthy depends on which commit they were restarted onto. Only
systemd, sudo and the HTTP check are faked.
"""
import importlib.util
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_spec = importlib.util.spec_from_file_location(
    'auto_update_verify', ROOT / 'scripts' / 'utils' / 'auto_update_verify.py')
av = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(av)

pytestmark = pytest.mark.skipif(shutil.which('git') is None, reason='git not installed')


def git(cwd, *args):
    result = subprocess.run(['git', '-c', 'user.name=t', '-c', 'user.email=t@example.com', *args],
                            cwd=str(cwd), capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def done(args, stdout='', rc=0):
    return subprocess.CompletedProcess(args, rc, stdout=stdout, stderr='' if rc == 0 else 'failed')


def updated_repo(tmp_path, new_requirements=False):
    """A checkout on a new commit, with the commit it came from."""
    repo = tmp_path / 'LEDMatrix'
    repo.mkdir()
    git(repo, 'init', '-q', '-b', 'main')
    (repo / 'app.py').write_text('v = 1\n')
    (repo / 'requirements.txt').write_text('requests\n')
    git(repo, 'add', '.')
    git(repo, 'commit', '-qm', 'old')
    old = git(repo, 'rev-parse', 'HEAD')
    (repo / 'app.py').write_text('v = 2\n')
    if new_requirements:
        (repo / 'requirements.txt').write_text('requests\nnewthing\n')
    git(repo, 'commit', '-qam', 'new')
    return repo, old, git(repo, 'rev-parse', 'HEAD')


class FakeHost:
    """systemd, sudo and the web interface.

    Services run whatever commit was checked out when they were last
    restarted; ``failure`` says how they misbehave on the new commit
    ("display_down", "web_down", "crash_loop") or on any commit ("always").
    """

    def __init__(self, repo, bad_head, failure=None, pip_ok=True, restart_failures=0, count_readable=True):
        self.repo, self.bad_head, self.failure, self.pip_ok = repo, bad_head, failure, pip_ok
        self.restart_failures = restart_failures  # how many restart commands fail, first to last
        self.count_readable = count_readable
        self.running_head = None  # not restarted yet: still the old, healthy code
        self.restarts = []        # (unit, commit it was restarted onto)
        self.pip_installs = []
        self.now = 0.0
        self.nrestarts = 0

    def broken(self, kind):
        if self.running_head is None:
            return False
        return self.failure == 'always' or (self.running_head == self.bad_head and self.failure == kind)

    def run(self, args, **kwargs):
        if args[0] == 'git':
            return subprocess.run(args, **kwargs)
        if args[:2] == ['systemctl', 'is-active']:
            return done(args, 'failed\n' if self.broken('display_down') else 'active\n')
        if args[:2] == ['systemctl', 'show']:
            if not self.count_readable:
                return done(args, rc=1)
            if self.broken('crash_loop'):
                self.nrestarts += 1
            return done(args, f'{self.nrestarts}\n')
        if args[0] == 'sudo' and any(a.endswith('safe_pip_install.sh') for a in args):
            self.pip_installs.append(args[-1])
            return done(args, rc=0 if self.pip_ok else 1)
        if args[:4] == ['sudo', '-n', 'systemctl', 'restart']:
            if self.restart_failures:
                self.restart_failures -= 1
                return done(args, rc=1)  # the old process keeps running
            self.running_head = git(self.repo, 'rev-parse', 'HEAD')
            self.restarts.append((args[4], self.running_head))
            return done(args)
        raise AssertionError(f'unexpected command: {args}')

    def web_responds(self):
        return not self.broken('web_down')

    def sleep(self, seconds):
        self.now += seconds

    def verifier(self):
        return av.Verifier(self.repo, run=self.run, sleep=self.sleep, clock=lambda: self.now,
                           web_responds=self.web_responds, log=lambda msg: None)


def check(tmp_path, failure=None, new_requirements=False, pip_ok=True, restart_failures=0,
          count_readable=True, **pending):
    repo, old, new = updated_repo(tmp_path, new_requirements)
    fields = {'status': 'pending', 'old_head': old, 'new_head': new,
              'display_was_active': True, 'dependency_failures': []}
    fields.update(pending)
    av.write_pending(av.pending_path(repo), fields)
    host = FakeHost(repo, new, failure, pip_ok, restart_failures, count_readable)
    code = host.verifier().verify()
    result = av.read_pending(av.pending_path(repo))
    return code, result, host, git(repo, 'rev-parse', 'HEAD'), old, new


def test_a_healthy_update_is_kept(tmp_path):
    code, result, host, head, old, new = check(tmp_path)
    assert code == 0 and result['status'] == 'success'
    assert head == new
    assert host.restarts == [('ledmatrix.service', new), ('ledmatrix-web.service', new)]


@pytest.mark.parametrize('failure, reason', [
    ('display_down', 'the display service did not stay running'),
    ('web_down', 'the web interface did not respond'),
    ('crash_loop', 'the display service kept restarting'),
])
def test_an_unhealthy_update_is_rolled_back(tmp_path, failure, reason):
    code, result, host, head, old, new = check(tmp_path, failure)
    assert code == 0
    assert result['status'] == 'rolled_back' and result['reason'] == reason
    assert head == old
    assert host.restarts[-2:] == [('ledmatrix.service', old), ('ledmatrix-web.service', old)]


def test_a_stopped_display_is_not_started(tmp_path):
    code, result, host, head, old, new = check(tmp_path, display_was_active=False)
    assert result['status'] == 'success'
    assert [unit for unit, _ in host.restarts] == ['ledmatrix-web.service']


def test_failed_dependencies_roll_back_before_anything_restarts_onto_them(tmp_path):
    code, result, host, head, old, new = check(tmp_path, dependency_failures=['requirements.txt'])
    assert result['status'] == 'rolled_back'
    assert 'requirements.txt' in result['reason']
    assert head == old
    assert all(commit == old for _, commit in host.restarts), host.restarts


def test_rollback_reinstalls_the_previous_dependencies(tmp_path):
    code, result, host, head, old, new = check(tmp_path, 'display_down', new_requirements=True)
    assert result['status'] == 'rolled_back' and result['detail'] is None
    assert host.pip_installs == [str(tmp_path / 'LEDMatrix' / 'requirements.txt')]


def test_a_failed_dependency_reinstall_is_reported(tmp_path):
    code, result, host, head, old, new = check(tmp_path, 'display_down', new_requirements=True,
                                               pip_ok=False)
    assert result['status'] == 'rolled_back' and head == old
    assert 'Install Base Requirements' in result['detail']


def test_still_broken_after_rolling_back_is_rollback_failed(tmp_path):
    code, result, host, head, old, new = check(tmp_path, 'always')
    assert code == 1 and result['status'] == 'rollback_failed'
    assert 'still unhealthy after rolling back' in result['detail']


def test_a_failed_restart_is_not_mistaken_for_a_healthy_update(tmp_path):
    """When the restart command itself fails the old process keeps answering,
    so checking it would pass an update that never started."""
    code, result, host, head, old, new = check(tmp_path, restart_failures=1)
    assert result['status'] == 'rolled_back'
    assert result['reason'] == 'restarting the services failed'
    assert head == old


def test_restarts_that_keep_failing_after_rollback_are_rollback_failed(tmp_path):
    code, result, host, head, old, new = check(tmp_path, restart_failures=100)
    assert code == 1 and result['status'] == 'rollback_failed'
    assert 'restarting the services failed' in result['detail']


def test_an_unreadable_restart_count_is_never_called_stable(tmp_path):
    """With no restart count a crash loop looks healthy between attempts."""
    code, result, host, head, old, new = check(tmp_path, count_readable=False)
    assert result['status'] != 'success'
    assert 'restart count could not be read' in result['reason']


def test_nothing_pending_does_nothing(tmp_path):
    repo, _, _ = updated_repo(tmp_path)
    host = FakeHost(repo, None)
    assert host.verifier().verify() == 0
    assert host.restarts == []


def test_a_crash_is_recorded_not_left_as_running(tmp_path, monkeypatch):
    repo, old, new = updated_repo(tmp_path)
    av.write_pending(av.pending_path(repo), {'status': 'pending', 'old_head': old, 'new_head': new})

    def boom(self):
        raise RuntimeError('boom')
    monkeypatch.setattr(av.Verifier, 'verify', boom)
    assert av.main(['auto_update_verify.py', str(repo)]) == 1
    result = av.read_pending(av.pending_path(repo))
    assert result['status'] == 'rollback_failed' and result['detail'] == 'boom'


def test_units_installers_and_updater_agree():
    """The request file, the copied verifier, the unit names and the places
    that install them must all line up, or the health check silently never
    starts on real devices -- and code updates stay paused."""
    from web_interface import auto_update as au
    from src import auto_update_setup as aus
    from src.startup_validator import StartupValidator

    service = (ROOT / 'systemd' / aus.SERVICE_UNIT).read_text(encoding='utf-8')
    path = (ROOT / 'systemd' / aus.PATH_UNIT).read_text(encoding='utf-8')
    request = f'__PROJECT_ROOT_DIR__/{au.REQUEST_REL.as_posix()}'
    assert f'PathExists={request}' in path
    assert f'Unit={aus.SERVICE_UNIT}' in path
    assert f'ExecStartPre=/bin/rm -f "{request}"' in service, "the request must be consumed or the path unit re-fires"
    assert f'"__PROJECT_ROOT_DIR__/{au.VERIFIER_COPY_REL.as_posix()}" "__PROJECT_ROOT_DIR__"' in service
    assert au.PENDING_REL.name == av.PENDING_NAME
    assert au.PATH_UNIT == aus.PATH_UNIT and au.SETUP_RESULT_REL == aus.RESULT_REL

    for installer in ('scripts/install/install_web_service.sh', 'scripts/install/install_service.sh'):
        text = (ROOT / installer).read_text(encoding='utf-8')
        assert 'ledmatrix-update-verify.service ledmatrix-update-verify.path' in text, installer
        assert 'enable --now ledmatrix-update-verify.path' in text, installer
    for unit in aus.UNITS:
        assert (f'systemd/{unit}', f'/etc/systemd/system/{unit}') in StartupValidator._UNITS

    # Triggering takes no privilege any more; no sudoers rule should linger.
    for sudoers in ('scripts/install/configure_web_sudo.sh', 'first_time_install.sh'):
        assert not re.search(r'NOPASSWD:.*update-verify', (ROOT / sudoers).read_text(encoding='utf-8')), sudoers
