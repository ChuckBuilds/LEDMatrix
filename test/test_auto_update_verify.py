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
    ("display_down", "web_down", "crash_loop", "frozen": active but the
    render loop stuck after its first frame) or on any commit ("always").

    ``heartbeat`` is whether the display writes one: never (``None``, code
    from before the heartbeat), or on every commit (``"always"``).
    """

    def __init__(self, repo, bad_head, failure=None, pip_ok=True, restart_failures=0, count_readable=True,
                 heartbeat=None):
        self.repo, self.bad_head, self.failure, self.pip_ok = repo, bad_head, failure, pip_ok
        self.restart_failures = restart_failures  # how many restart commands fail, first to last
        self.count_readable = count_readable
        self.running_head = None  # not restarted yet: still the old, healthy code
        self.restarts = []        # (unit, commit it was restarted onto)
        self.pip_installs = []
        self.pip_timeouts = []
        self.pip = None  # optional (args, host) -> result, or raises, instead of pip_ok
        self.now = 0.0
        self.nrestarts = 0
        self.heartbeat = heartbeat
        self.display_started_at = -1000.0  # the pre-update display, long running
        self.unit_restores = []  # (argv, commit checked out, restarts so far)
        self.restore_ok = True

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
            self.pip_timeouts.append(kwargs.get('timeout'))
            if self.pip:
                return self.pip(args, self)
            return done(args, rc=0 if self.pip_ok else 1)
        if args[:3] == ['sudo', '-n', av.REFRESH_UNITS_PATH]:
            self.unit_restores.append((list(args), git(self.repo, 'rev-parse', 'HEAD'),
                                       len(self.restarts)))
            return done(args, rc=0 if self.restore_ok else 1)
        if args[:4] == ['sudo', '-n', 'systemctl', 'restart']:
            if self.restart_failures:
                self.restart_failures -= 1
                return done(args, rc=1)  # the old process keeps running
            self.running_head = git(self.repo, 'rev-parse', 'HEAD')
            self.restarts.append((args[4], self.running_head))
            if args[4] == 'ledmatrix.service':
                self.display_started_at = self.now
            return done(args)
        raise AssertionError(f'unexpected command: {args}')

    def web_responds(self):
        return not self.broken('web_down')

    def read_heartbeat(self):
        if self.heartbeat is None:
            return None
        first_frame = self.display_started_at + 10  # plugins load, then it draws
        if self.now < first_frame:
            # Nothing from this process yet. A display whose unit predates
            # RuntimeDirectory= leaves its predecessor's file behind.
            return {'mono': self.display_started_at - 1}
        if self.broken('frozen'):
            return {'mono': first_frame}  # drew once, then stuck
        return {'mono': self.now}

    def sleep(self, seconds):
        self.now += seconds

    def verifier(self):
        return av.Verifier(self.repo, run=self.run, sleep=self.sleep, clock=lambda: self.now,
                           web_responds=self.web_responds, log=lambda msg: None,
                           read_heartbeat=self.read_heartbeat)


def check(tmp_path, failure=None, new_requirements=False, pip_ok=True, restart_failures=0,
          count_readable=True, heartbeat=None, **pending):
    repo, old, new = updated_repo(tmp_path, new_requirements)
    fields = {'status': 'pending', 'old_head': old, 'new_head': new,
              'display_was_active': True, 'dependency_failures': []}
    fields.update(pending)
    av.write_pending(av.pending_path(repo), fields)
    host = FakeHost(repo, new, failure, pip_ok, restart_failures, count_readable, heartbeat)
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


def _rollback_with_pip(tmp_path, pip, web_requirements_too=False):
    """Roll back an update that changed the requirements, with pip faked by ``pip``."""
    repo, old, new = updated_repo(tmp_path, new_requirements=True)
    if web_requirements_too:
        (repo / 'web_interface').mkdir()
        (repo / 'web_interface' / 'requirements.txt').write_text('flask\n')
        git(repo, 'add', '.')
        git(repo, 'commit', '-qm', 'web reqs')
        (repo / 'web_interface' / 'requirements.txt').write_text('flask\nnew\n')
        (repo / 'requirements.txt').write_text('requests\nnewer\n')
        git(repo, 'commit', '-qam', 'bump both')
        old, new = git(repo, 'rev-parse', 'HEAD~1'), git(repo, 'rev-parse', 'HEAD')
    host = FakeHost(repo, new)
    host.pip = pip
    ok, detail = host.verifier().rollback({'old_head': old, 'new_head': new})
    return ok, detail, host


def test_a_failed_pip_is_not_run_again_with_the_other_bash(tmp_path):
    """Retrying after pip itself ran only repeats it, and every repeat can
    take PIP_TIMEOUT_SECONDS of the unit's time limit."""
    ok, detail, host = _rollback_with_pip(
        tmp_path, lambda args, h: subprocess.CompletedProcess(args, 1, '', 'ERROR: No matching distribution'))
    assert ok and 'requirements.txt' in detail
    assert len(host.pip_installs) == 1


def test_a_pip_timeout_is_not_retried(tmp_path):
    def slow(args, h):
        h.now += h.pip_timeouts[-1]
        raise subprocess.TimeoutExpired(args, h.pip_timeouts[-1])
    ok, detail, host = _rollback_with_pip(tmp_path, slow)
    assert ok and 'Install Base Requirements' in detail
    assert len(host.pip_installs) == 1


def test_a_sudo_refusal_tries_the_next_bash(tmp_path):
    def refused_once(args, h):
        if len(h.pip_installs) == 1:
            return subprocess.CompletedProcess(args, 1, '', 'sudo: a password is required')
        return done(args)
    ok, detail, host = _rollback_with_pip(tmp_path, refused_once)
    assert ok and detail == ''
    assert len(host.pip_installs) == 2


def test_reinstalls_share_one_time_budget(tmp_path):
    def hangs(args, h):
        h.now += h.pip_timeouts[-1]
        raise subprocess.TimeoutExpired(args, h.pip_timeouts[-1])
    ok, detail, host = _rollback_with_pip(tmp_path, hangs, web_requirements_too=True)
    assert ok and 'requirements.txt' in detail and 'web_interface/requirements.txt' in detail
    assert sum(host.pip_timeouts) <= av.PIP_BUDGET_SECONDS
    assert len(host.pip_installs) == 1, "the first file used the whole budget"


def test_the_worst_case_fits_the_unit_time_limit():
    """systemd kills the check at TimeoutStartSec, mid-rollback, and the update
    then sits in "verifying" until the web interface calls it lost."""
    from web_interface import auto_update as au
    service = (ROOT / 'systemd' / 'ledmatrix-update-verify.service').read_text(encoding='utf-8')
    minutes = int(re.search(r'^TimeoutStartSec=(\d+)min$', service, re.M).group(1))
    assert av.WORST_CASE_SECONDS < minutes * 60
    assert minutes * 60 < au.VERIFY_LOST_SECONDS, "the web UI must not call a running check lost"


def test_sudo_refusal_wording_matches_permission_utils():
    from src.common import permission_utils
    assert set(av.SUDO_REFUSAL_PHRASES) == set(permission_utils.SUDO_REFUSAL_PHRASES)


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
    for sudoers in ('scripts/install/configure_web_sudo.sh', 'first_time_install.sh',
                    'scripts/install/lib_sudoers.sh'):
        assert not re.search(r'NOPASSWD:.*update-verify', (ROOT / sudoers).read_text(encoding='utf-8')), sudoers


# -- the display's heartbeat -------------------------------------------------------
#
# "Service active" plus one HTTP 200 passed a panel frozen by a render loop
# stuck in a plugin. Where the display writes a heartbeat, the restarted
# display has to keep it fresh too.

FROZEN_REASON = ('the display service is running but its panel is not being drawn '
                 '(no fresh heartbeat)')


def test_a_display_that_keeps_drawing_passes(tmp_path):
    code, result, host, head, old, new = check(tmp_path, heartbeat='always')
    assert result['status'] == 'success' and head == new


def test_a_frozen_panel_is_rolled_back(tmp_path):
    code, result, host, head, old, new = check(tmp_path, 'frozen', heartbeat='always')
    assert result['status'] == 'rolled_back' and result['reason'] == FROZEN_REASON
    assert head == old


def test_the_previous_processs_heartbeat_does_not_count(tmp_path):
    """Under a unit without RuntimeDirectory= the old file outlives the old
    process; a restarted display that never draws must not pass on it."""
    repo, old, new = updated_repo(tmp_path)
    host = FakeHost(repo, new, heartbeat='always')
    verifier = host.verifier()
    verifier.expect_heartbeat = True
    host.display_started_at = host.now = 100.0
    verifier.display_restarted_at = 100.0
    host.now = 101.0  # the new process has not drawn yet
    assert verifier.display_drawing() is False
    host.now = 115.0
    assert verifier.display_drawing() is True


def test_without_a_heartbeat_the_check_is_what_it_was(tmp_path):
    """Code from before the heartbeat (or a display that cannot write one)
    never wrote one, so it cannot be asked for -- a frozen panel then passes,
    exactly as it did."""
    code, result, host, head, old, new = check(tmp_path, 'frozen', heartbeat=None)
    assert result['status'] == 'success'


def test_a_stopped_display_is_not_asked_for_a_heartbeat(tmp_path):
    code, result, host, head, old, new = check(tmp_path, 'frozen', heartbeat='always',
                                               display_was_active=False)
    assert result['status'] == 'success'


def test_the_heartbeat_location_and_freshness_match_the_display():
    """A copy, not an import: the verifier must not depend on the code it checks."""
    from src import display_watchdog
    assert av.HEARTBEAT_PATH == display_watchdog.HEARTBEAT_PATH
    # A display frozen right after its first frame must go stale inside the
    # window it has to stay healthy for.
    assert av.HEARTBEAT_FRESH_SECONDS + av.POLL_SECONDS < av.STABLE_SECONDS
    assert av.HEARTBEAT_FRESH_SECONDS > display_watchdog.BEAT_INTERVAL_SECONDS * 2


# -- systemd units the update installed ------------------------------------------

def test_a_rollback_restores_the_units_the_update_installed(tmp_path):
    """The update installed new units (web_interface/unit_refresh.py); the
    rollback puts the old ones back before restarting onto the old code."""
    code, result, host, head, old, new = check(tmp_path, 'display_down', units_refreshed=True)
    assert result['status'] == 'rolled_back' and head == old
    assert len(host.unit_restores) == 1
    argv, commit, restarts_before = host.unit_restores[0]
    assert argv == ['sudo', '-n', av.REFRESH_UNITS_PATH, '--restore']
    assert commit == old, 'restored after the code was rolled back'
    assert restarts_before == 2, 'restored before the services restart onto the old code'
    assert host.restarts[-2:] == [('ledmatrix.service', old), ('ledmatrix-web.service', old)]
    assert result['detail'] is None


@pytest.mark.parametrize('pending', [{}, {'units_refreshed': False}])
def test_a_rollback_leaves_units_alone_when_the_update_did_not_change_them(tmp_path, pending):
    # {} is what an updater from before this change writes.
    code, result, host, head, old, new = check(tmp_path, 'display_down', **pending)
    assert result['status'] == 'rolled_back' and host.unit_restores == []


def test_a_healthy_update_keeps_its_new_units(tmp_path):
    code, result, host, head, old, new = check(tmp_path, units_refreshed=True)
    assert result['status'] == 'success' and host.unit_restores == []


def test_a_failed_unit_restore_is_reported_but_the_rollback_stands(tmp_path):
    repo, old, new = updated_repo(tmp_path)
    av.write_pending(av.pending_path(repo), {'status': 'pending', 'old_head': old, 'new_head': new,
                                             'display_was_active': True, 'dependency_failures': [],
                                             'units_refreshed': True})
    host = FakeHost(repo, new, 'display_down')
    host.restore_ok = False
    host.verifier().verify()
    result = av.read_pending(av.pending_path(repo))
    assert result['status'] == 'rolled_back'
    assert git(repo, 'rev-parse', 'HEAD') == old
    assert 'install_service.sh' in result['detail']
