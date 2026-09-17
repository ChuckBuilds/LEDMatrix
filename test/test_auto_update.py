"""Weekly automatic updates (web_interface/auto_update.py).

The updater pulls code and restarts services unattended. These pin down that
it runs only when asked and due, refuses to touch a checkout it could damage,
never leaves new code running without a health check, and reports every
failure instead of logging it and moving on.

Git runs for real against a throwaway origin/device clone pair; systemd, sudo
and the health check service are faked.
"""
import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from web_interface import auto_update as au  # noqa: E402

# Local time is pinned to UTC below, so 03:00 is inside the quiet hours.
NIGHT = datetime(2026, 9, 14, 3, 0, tzinfo=timezone.utc).timestamp()
NOON = datetime(2026, 9, 14, 12, 0, tzinfo=timezone.utc).timestamp()
ON = {'auto_update': {'enabled': True}}
OFF = {'auto_update': {'enabled': False}}

needs_git = pytest.mark.skipif(shutil.which('git') is None, reason='git not installed')


@pytest.fixture(autouse=True)
def local_is_utc(monkeypatch):
    """Pin 'local time' so quiet-hours checks don't depend on the host zone."""
    monkeypatch.setattr(au, '_zone', lambda name: timezone.utc)


def git(cwd, *args):
    result = subprocess.run(['git', '-c', 'user.name=t', '-c', 'user.email=t@example.com', *args],
                            cwd=str(cwd), capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


class Repo:
    """An origin, a checkout that publishes to it, and the device's clone."""

    def __init__(self, tmp):
        self.seed = tmp / 'seed'
        self.seed.mkdir()
        git(self.seed, 'init', '-q', '-b', 'main')
        (self.seed / 'app.py').write_text('v = 1\n')
        git(self.seed, 'add', '.')
        git(self.seed, 'commit', '-qm', 'one')
        self.origin = tmp / 'origin.git'
        git(tmp, 'clone', '-q', '--bare', str(self.seed), str(self.origin))
        git(self.seed, 'remote', 'add', 'origin', str(self.origin))
        self.device = tmp / 'device'
        git(tmp, 'clone', '-q', str(self.origin), str(self.device))

    def head(self):
        return git(self.device, 'rev-parse', 'HEAD')

    def publish(self, text='v = 2\n'):
        (self.seed / 'app.py').write_text(text)
        git(self.seed, 'commit', '-qam', 'next')
        git(self.seed, 'push', '-q', 'origin', 'main')
        return git(self.seed, 'rev-parse', 'HEAD')


def real_pull(device, **extra):
    def core_update():
        before = git(device, 'rev-parse', 'HEAD')
        r = subprocess.run(['git', 'pull', '-q', '--rebase', '--autostash'],
                           cwd=str(device), capture_output=True, text=True)
        after = git(device, 'rev-parse', 'HEAD')
        result = {'status': 'success' if r.returncode == 0 else 'error',
                  'message': 'Code updated successfully.' if r.returncode == 0 else r.stderr,
                  'restart_required': before != after, 'dependency_failures': []}
        result.update(extra)
        return result
    return core_update


class FakeStore:
    def __init__(self, plugins_dir, versions, bump=(), fail=()):
        self.plugins_dir = str(plugins_dir)
        self.bump, self.fail = set(bump), set(fail)
        for pid, version in versions.items():
            d = Path(plugins_dir) / pid
            d.mkdir(parents=True)
            (d / 'manifest.json').write_text(json.dumps({'id': pid, 'version': version}))
        self.updated_calls = []

    def list_installed_plugins(self):
        return [p.name for p in Path(self.plugins_dir).iterdir()]

    def _get_local_git_info(self, path):
        return None

    def update_plugin(self, pid):
        self.updated_calls.append(pid)
        if pid in self.fail:
            return False
        if pid in self.bump:
            path = Path(self.plugins_dir) / pid / 'manifest.json'
            m = json.loads(path.read_text())
            m['version'] = '9.9.9'
            path.write_text(json.dumps(m))
        return True


class Harness:
    """An AutoUpdater on a real clone.

    ``helper`` is whether the health check's path unit is active; ``pickup``
    whether the health check actually starts when asked. Starting it is
    simulated the way systemd does it: the request file is consumed
    (ExecStartPre) and the verifier marks the pending update "verifying".
    """

    def __init__(self, tmp, repo, *, helper=True, pickup=True, core_update=None, store=None,
                 disk_free=10 ** 12, display_active=True, enabled=True, clock=NIGHT):
        self.sudo, self.restarts, self.now = [], [], clock
        self.state_at_handoff = None

        def run(args, **kwargs):
            if args[0] == 'sudo':
                self.sudo.append(list(args))
                return subprocess.CompletedProcess(args, 0, stdout='', stderr='')
            return subprocess.run(args, **kwargs)

        def sleep(seconds):
            updater = self.updater
            if self.state_at_handoff is None:
                self.state_at_handoff = au.load_state(updater.state_file)
            if pickup and updater.request_file.exists():
                updater.request_file.unlink()
                pending = au._read_json(updater.pending_file)
                pending['status'] = 'verifying'
                au._write_json(updater.pending_file, pending)

        self.config = {'auto_update': {'enabled': enabled}, 'timezone': 'UTC'}
        cm = MagicMock()
        cm.load_config.return_value = self.config
        self.updater = au.AutoUpdater(
            config_manager=cm, core_update=core_update or real_pull(repo.device),
            store_manager=store, project_root=repo.device, clock=lambda: self.now,
            restart=self.restarts.append, run=run,
            service_active=lambda unit: display_active, helper_ready=lambda: helper,
            disk_free=lambda path: disk_free, sleep=sleep)

    @property
    def state(self):
        return au.load_state(self.updater.state_file)

    @property
    def pending(self):
        return au._read_json(self.updater.pending_file)

    def write_pending(self, **fields):
        au._write_json(self.updater.pending_file, fields)


def stub_updater(tmp_path, outcome='up_to_date', message='ok', store=None, enabled=True, clock=NIGHT):
    """For scheduling and plugin tests: the core update is stubbed out."""
    cm = MagicMock()
    cm.load_config.return_value = {'auto_update': {'enabled': enabled}, 'timezone': 'UTC'}
    restarts = []
    updater = au.AutoUpdater(config_manager=cm, core_update=MagicMock(), store_manager=store,
                             project_root=tmp_path, clock=lambda: clock,
                             restart=restarts.append)
    updater.update_core = MagicMock(return_value={'outcome': outcome, 'message': message})
    return updater, restarts


# -- scheduling ---------------------------------------------------------------

class TestIsDue:
    def test_not_before_next_due(self):
        assert not au.is_due(100, 200, 3)

    def test_in_quiet_hours_once_due(self):
        assert au.is_due(200, 200, 3)

    def test_waits_for_quiet_hours(self):
        assert not au.is_due(200, 100, 12)

    def test_gives_up_waiting_after_grace(self):
        assert au.is_due(100 + au.QUIET_HOURS_GRACE_SECONDS, 100, 12)


class TestTick:
    def test_disabled_never_runs(self, tmp_path):
        updater, _ = stub_updater(tmp_path, enabled=False)
        assert updater.tick() is False
        updater.update_core.assert_not_called()

    def test_enabling_at_noon_waits_for_the_night(self, tmp_path):
        updater, _ = stub_updater(tmp_path, clock=NOON)
        assert updater.tick() is False
        updater.update_core.assert_not_called()
        assert au.load_state(updater.state_file)['next_due'] == NOON

    def test_runs_when_due_and_schedules_a_week_out(self, tmp_path):
        updater, _ = stub_updater(tmp_path)
        assert updater.tick() is True
        state = au.load_state(updater.state_file)
        assert state['next_due'] == NIGHT + au.UPDATE_INTERVAL_SECONDS
        assert updater.tick() is False, "ran twice in one night"

    def test_transient_error_retries_next_day(self, tmp_path):
        updater, _ = stub_updater(tmp_path, outcome='error', message='no network')
        updater.tick()
        state = au.load_state(updater.state_file)
        assert state['next_due'] == NIGHT + au.RETRY_AFTER_FAILURE_SECONDS
        assert state['alert']['message'] == 'no network'

    def test_blocked_keeps_weekly_cadence_but_alerts(self, tmp_path):
        updater, _ = stub_updater(tmp_path, outcome='blocked', message='local edits')
        updater.tick()
        state = au.load_state(updater.state_file)
        assert state['next_due'] == NIGHT + au.UPDATE_INTERVAL_SECONDS
        assert 'local edits' in state['alert']['message']


# -- checks before touching the checkout ---------------------------------------

@needs_git
class TestPreflightRefuses:
    def test_nothing_new_is_up_to_date(self, tmp_path):
        repo = Repo(tmp_path)
        h = Harness(tmp_path, repo)
        result = h.updater.run()
        assert result['core_outcome'] == 'up_to_date'
        assert result['status'] == 'success'
        assert h.sudo == [] and 'alert' not in h.state

    def test_without_the_health_check_code_is_not_touched(self, tmp_path):
        repo = Repo(tmp_path)
        old = repo.head()
        repo.publish()
        h = Harness(tmp_path, repo, helper=False)
        result = h.updater.run()
        assert result['core_outcome'] == 'blocked'
        assert repo.head() == old
        assert 'health check is set up' in result['core_message']
        assert h.state['alert']

    def test_local_edits_are_never_stashed(self, tmp_path):
        repo = Repo(tmp_path)
        old = repo.head()
        repo.publish()
        (repo.device / 'app.py').write_text('mine\n')
        h = Harness(tmp_path, repo)
        result = h.updater.run()
        assert result['core_outcome'] == 'blocked'
        assert 'app.py' in result['core_message']
        assert repo.head() == old
        assert (repo.device / 'app.py').read_text() == 'mine\n'
        assert git(repo.device, 'stash', 'list') == ''

    def test_local_commits_block(self, tmp_path):
        repo = Repo(tmp_path)
        repo.publish()
        (repo.device / 'extra.txt').write_text('x\n')
        git(repo.device, 'add', 'extra.txt')
        git(repo.device, 'commit', '-qm', 'local')
        mine = repo.head()
        h = Harness(tmp_path, repo)
        result = h.updater.run()
        assert result['core_outcome'] == 'blocked'
        assert 'local commit' in result['core_message']
        assert repo.head() == mine

    def test_a_rebase_really_in_progress_blocks(self, tmp_path):
        repo = Repo(tmp_path)
        repo.publish('v = 2\n')
        (repo.device / 'app.py').write_text('v = mine\n')
        git(repo.device, 'commit', '-qam', 'local')
        git(repo.device, 'fetch', '-q')
        stopped = subprocess.run(['git', '-c', 'user.name=t', '-c', 'user.email=t@example.com',
                                  'rebase', 'origin/main'], cwd=str(repo.device), capture_output=True, text=True)
        assert stopped.returncode != 0, "the rebase should stop on the conflict"
        result = Harness(tmp_path, repo).updater.run()
        assert result['core_outcome'] == 'blocked'
        assert 'rebase is in progress' in result['core_message']
        assert (repo.device / '.git' / 'rebase-merge').exists(), "a live rebase must be left alone"

    def test_an_abandoned_rebase_is_cleared_and_the_update_runs(self, tmp_path):
        """Found on a real device: a pull stopped mid-rebase a week earlier,
        HEAD was later checked out to a branch, and the leftover rebase
        directory would have blocked every automatic update forever."""
        repo = Repo(tmp_path)
        leftover = repo.device / '.git' / 'rebase-merge'
        leftover.mkdir()
        (leftover / 'head-name').write_text('refs/heads/main\n')
        new = repo.publish()
        result = Harness(tmp_path, repo).updater.run()
        assert not leftover.exists()
        assert result['core_outcome'] == 'verifying'
        assert repo.head() == new

    def test_a_merge_in_progress_blocks(self, tmp_path):
        repo = Repo(tmp_path)
        repo.publish()
        (repo.device / '.git' / 'MERGE_HEAD').write_text(repo.head() + '\n')
        result = Harness(tmp_path, repo).updater.run()
        assert result['core_outcome'] == 'blocked'
        assert 'merge is in progress' in result['core_message']

    def test_low_disk_blocks(self, tmp_path):
        repo = Repo(tmp_path)
        repo.publish()
        result = Harness(tmp_path, repo, disk_free=10 * 1024 * 1024).updater.run()
        assert result['core_outcome'] == 'blocked'
        assert 'disk space' in result['core_message']

    def test_unreachable_origin_retries_tomorrow(self, tmp_path):
        repo = Repo(tmp_path)
        git(repo.device, 'remote', 'set-url', 'origin', str(tmp_path / 'nowhere'))
        h = Harness(tmp_path, repo)
        result = h.updater.run()
        assert result['core_outcome'] == 'error'
        assert h.state['next_due'] == NIGHT + au.RETRY_AFTER_FAILURE_SECONDS

    def test_version_already_rolled_back_is_not_retried(self, tmp_path):
        repo = Repo(tmp_path)
        old = repo.head()
        bad = repo.publish()
        h = Harness(tmp_path, repo)
        au.save_state({'rolled_back_head': bad}, h.updater.state_file)
        result = h.updater.run()
        assert result['core_outcome'] == 'up_to_date'
        assert 'rolled back before' in result['core_message']
        assert repo.head() == old and h.sudo == []


# -- the update and its hand-off to the health check ---------------------------

@needs_git
class TestUpdateIsVerified:
    def test_pull_hands_off_to_the_health_check(self, tmp_path):
        repo = Repo(tmp_path)
        old = repo.head()
        new = repo.publish()
        store = FakeStore(tmp_path / 'plugins', {'weather': '1.0.0'}, bump={'weather'})
        h = Harness(tmp_path, repo, store=store)
        result = h.updater.run()

        assert result['core_outcome'] == 'verifying' and result['status'] == 'pending'
        assert repo.head() == new
        assert h.pending == {**h.pending, 'status': 'verifying', 'old_head': old, 'new_head': new,
                             'display_was_active': True, 'dependency_failures': []}
        assert not h.updater.request_file.exists(), "the request is consumed when the check starts"
        assert h.sudo == [], "triggering the health check needs no privilege"
        assert h.restarts == [], "restarts belong to the health check, not the web process"
        assert store.updated_calls == [], "plugins must wait until the new code is verified"
        assert h.state['plugins_pending'] is True
        assert h.updater.verifier_copy.read_text() == au.VERIFIER_SOURCE.read_text()

    def test_state_is_saved_before_the_health_check_restarts_this_process(self, tmp_path):
        repo = Repo(tmp_path)
        repo.publish()
        h = Harness(tmp_path, repo)
        h.updater.run()
        assert h.state_at_handoff['last_result']['core_outcome'] == 'verifying'
        assert h.state_at_handoff['plugins_pending'] is True

    def test_dependency_failures_reach_the_health_check(self, tmp_path):
        repo = Repo(tmp_path)
        repo.publish()
        h = Harness(tmp_path, repo,
                    core_update=real_pull(repo.device, dependency_failures=['requirements.txt']))
        h.updater.run()
        assert h.pending['dependency_failures'] == ['requirements.txt']

    def test_a_health_check_that_never_starts_means_the_update_is_undone(self, tmp_path):
        repo = Repo(tmp_path)
        old = repo.head()
        repo.publish()
        h = Harness(tmp_path, repo, pickup=False)
        result = h.updater.run()
        assert repo.head() == old
        assert result['core_outcome'] == 'blocked'
        assert 'did not start' in result['core_message']
        assert h.pending is None and not h.updater.request_file.exists()
        assert h.state['alert']

    def test_failed_pull_retries_tomorrow(self, tmp_path):
        repo = Repo(tmp_path)
        old = repo.head()
        repo.publish()
        h = Harness(tmp_path, repo,
                    core_update=lambda: {'status': 'error', 'message': 'Update failed: network'})
        result = h.updater.run()
        assert result['core_outcome'] == 'error'
        assert repo.head() == old
        assert h.state['next_due'] == NIGHT + au.RETRY_AFTER_FAILURE_SECONDS

    def test_failed_pull_that_moved_head_is_rolled_back(self, tmp_path):
        repo = Repo(tmp_path)
        old = repo.head()
        repo.publish()
        pull = real_pull(repo.device)

        def half_failed():
            pull()
            return {'status': 'error', 'message': 'Update failed: interrupted'}
        result = Harness(tmp_path, repo, core_update=half_failed).updater.run()
        assert repo.head() == old
        assert 'rolled back' in result['core_message']


# -- reporting the health check's outcome --------------------------------------

@needs_git
class TestHealthCheckOutcome:
    def _after_update(self, tmp_path, **pending):
        repo = Repo(tmp_path)
        store = FakeStore(tmp_path / 'plugins', {'weather': '1.0.0'}, bump={'weather'})
        h = Harness(tmp_path, repo, store=store)
        au.save_state({'plugins_pending': True, 'next_due': NIGHT + au.UPDATE_INTERVAL_SECONDS,
                       'last_result': {'core_outcome': 'verifying', 'status': 'pending',
                                       'plugins_deferred': True}}, h.updater.state_file)
        h.write_pending(old_head='a' * 40, new_head='b' * 40, created_at=NIGHT, **pending)
        return h, store

    def test_waits_while_the_check_runs(self, tmp_path):
        h, store = self._after_update(tmp_path, status='verifying')
        h.now = NIGHT + 60
        assert h.updater.tick() is False
        assert store.updated_calls == [] and h.pending

    def test_success_then_runs_the_deferred_plugins(self, tmp_path):
        h, store = self._after_update(tmp_path, status='success')
        assert h.updater.tick() is True
        result = h.state['last_result']
        assert result['core_outcome'] == 'updated' and result['status'] == 'success'
        assert result['plugins_updated'] == ['weather']
        assert h.restarts == ['ledmatrix']
        assert 'alert' not in h.state and h.pending is None

    def test_rollback_alerts_and_remembers_the_bad_commit(self, tmp_path):
        h, store = self._after_update(tmp_path, status='rolled_back',
                                      reason='the web interface did not respond')
        h.updater.tick()
        assert 'rolled back' in h.state['alert']['message']
        assert 'the web interface did not respond' in h.state['alert']['message']
        assert h.state['rolled_back_head'] == 'b' * 40
        assert store.updated_calls == ['weather'], "the old code is healthy; plugins may update"

    def test_failed_rollback_alerts_and_skips_plugins(self, tmp_path):
        h, store = self._after_update(tmp_path, status='rollback_failed',
                                      reason='display down', detail='reset failed')
        h.updater.tick()
        assert f'git reset --hard {"a" * 40}' in h.state['alert']['message']
        assert store.updated_calls == []
        assert h.state['plugins_pending'] is False

    def test_a_check_that_never_reports_back_alerts(self, tmp_path):
        h, _ = self._after_update(tmp_path, status='verifying')
        h.now = NIGHT + au.VERIFY_LOST_SECONDS + 1
        h.updater.tick()
        assert h.state['last_result']['core_outcome'] == 'lost'
        assert 'never reported back' in h.state['alert']['message']

    def test_dismissed_alert_stays_hidden_until_a_new_one(self, tmp_path):
        h, _ = self._after_update(tmp_path, status='rolled_back', reason='x')
        h.updater.tick()
        alert_id = h.state['alert']['id']

        def status():
            return au.describe_status(h.config, state_file=h.updater.state_file,
                                      pending_file=h.updater.pending_file,
                                      setup_file=tmp_path / 'no-setup.json', helper=lambda: True)
        assert status()['alert_id'] == alert_id
        au.dismiss_alert(alert_id, h.updater.state_file)
        assert status()['alert'] is None
        state = h.state
        state['alert'] = {'id': 'newer', 'message': 'again'}
        au.save_state(state, h.updater.state_file)
        assert status()['alert'] == 'again'


def test_describe_status_reports_setup(tmp_path):
    setup = tmp_path / 'setup.json'
    setup.write_text(json.dumps({'status': 'failed', 'message': 'not root'}))
    kwargs = dict(state={}, state_file=tmp_path / 's.json', pending_file=tmp_path / 'p.json',
                  setup_file=setup)
    on = au.describe_status(ON, helper=lambda: False, **kwargs)
    assert on['verifier_installed'] is False
    assert (on['setup_status'], on['setup_message']) == ('failed', 'not root')

    def never(): raise AssertionError("systemctl asked while updates are off")
    assert au.describe_status(OFF, helper=never, **kwargs)['verifier_installed'] is None


# -- setting it up from the web UI ----------------------------------------------

class TestSetupFromTheWebUI:
    def _host(self, monkeypatch, ready=False, active=True, restart_ok=True):
        restarts = []
        monkeypatch.setattr(au, 'helper_ready', lambda: ready)
        monkeypatch.setattr(au, '_service_active', lambda unit: active)
        monkeypatch.setattr(au, 'restart_service', lambda unit: restarts.append(unit) or restart_ok)
        return restarts

    def test_switching_on_restarts_the_display_to_finish_setup(self, monkeypatch):
        restarts = self._host(monkeypatch)
        note = au.start_setup_if_needed(False, ON)
        assert restarts == ['ledmatrix']
        assert 'display is restarting' in note

    @pytest.mark.parametrize('was_enabled, config, ready', [
        (True, ON, False),   # already on: an unrelated save must not restart the display
        (False, OFF, False),  # still off
        (False, ON, True),   # already set up
    ])
    def test_nothing_to_do(self, monkeypatch, was_enabled, config, ready):
        restarts = self._host(monkeypatch, ready=ready)
        assert au.start_setup_if_needed(was_enabled, config) is None
        assert restarts == []

    def test_a_stopped_display_is_not_started(self, monkeypatch):
        restarts = self._host(monkeypatch, active=False)
        assert 'next time the display service starts' in au.start_setup_if_needed(False, ON)
        assert restarts == []

    def test_a_failed_restart_says_so(self, monkeypatch):
        self._host(monkeypatch, restart_ok=False)
        assert 'Could not restart' in au.start_setup_if_needed(False, ON)


# -- plugins --------------------------------------------------------------------

class TestPlugins:
    def test_nothing_changed_restarts_nothing(self, tmp_path):
        store = FakeStore(tmp_path / 'plugins', {'clock': '1.0.0'})
        updater, restarts = stub_updater(tmp_path, store=store)
        updater.run()
        assert store.updated_calls == ['clock']
        assert restarts == []

    def test_plugin_update_restarts_display_only(self, tmp_path):
        store = FakeStore(tmp_path / 'plugins', {'clock': '1.0.0', 'weather': '1.0.0'},
                          bump={'weather'})
        updater, restarts = stub_updater(tmp_path, store=store)
        result = updater.run()
        assert result['plugins_updated'] == ['weather']
        assert restarts == ['ledmatrix']

    def test_plugin_failure_alerts_but_keeps_weekly_cadence(self, tmp_path):
        store = FakeStore(tmp_path / 'plugins', {'zip-only': '1.0.0'}, fail={'zip-only'})
        updater, _ = stub_updater(tmp_path, store=store)
        result = updater.run()
        assert result['plugins_failed'] == ['zip-only'] and result['status'] == 'error'
        state = au.load_state(updater.state_file)
        assert 'zip-only' in state['alert']['message']
        assert state['next_due'] == NIGHT + au.UPDATE_INTERVAL_SECONDS

    def test_local_only_plugins_are_left_alone(self, tmp_path):
        store = FakeStore(tmp_path / 'plugins', {'mine': '1.0.0'})
        path = tmp_path / 'plugins' / 'mine' / 'manifest.json'
        path.write_text(json.dumps({'id': 'mine', 'version': '1.0.0', 'local_only': True}))
        updater, _ = stub_updater(tmp_path, store=store)
        updater.run()
        assert store.updated_calls == []


def test_restart_service_skips_a_stopped_unit(monkeypatch):
    calls = []

    def run(args, **kwargs):
        calls.append(args)
        return MagicMock(stdout='inactive\n', returncode=3, stderr='')
    monkeypatch.setattr(au.subprocess, 'run', run)
    assert au.restart_service('ledmatrix') is False
    assert calls == [['systemctl', 'is-active', 'ledmatrix']], (
        "a display the user stopped must not be started by an update")


def test_core_update_names_the_requirement_files_that_failed():
    """The health check refuses code whose dependencies did not install, so
    the failure has to arrive as data, not only as a sentence in the message."""
    from web_interface.blueprints import api_v3 as pkg
    from web_interface.blueprints.api_v3 import system

    heads = iter(['aaa111\n', 'bbb222\n'])

    def run(args, **kwargs):
        if args[:2] == ['git', 'rev-parse'] and args[-1] == 'HEAD':
            out = next(heads, 'bbb222\n')
        elif args[:2] == ['git', 'diff']:
            out = 'requirements.txt\n'
        elif '@{u}' in args:
            out = 'origin/main\n'
        else:
            out = ''
        return subprocess.CompletedProcess(args, 0, stdout=out, stderr='')

    pkg.api_v3.plugin_store_manager = None
    failed_install = subprocess.CompletedProcess([], 1, stdout='', stderr='boom')
    with patch.object(pkg.subprocess, 'run', run), \
            patch.object(system, '_pip_install_requirements', return_value=failed_install):
        result = system.perform_core_update()
    assert result['status'] == 'success'
    assert result['dependency_failures'] == ['requirements.txt']


# -- web routes -------------------------------------------------------------------

@pytest.fixture
def api_client(monkeypatch):
    from flask import Flask
    from web_interface.blueprints.api_v3 import api_v3
    app = Flask(__name__)
    app.register_blueprint(api_v3, url_prefix='/api/v3')
    cm = MagicMock()
    cm.load_config.return_value = {'timezone': 'UTC', 'auto_update': {'enabled': True}}
    cm.get_raw_file_content.return_value = {}
    cm.save_config_atomic.return_value = MagicMock(status=MagicMock(value='success'), message=None)
    api_v3.config_manager = cm
    api_v3.plugin_manager = MagicMock(plugins={})
    # Never restart a real display from a test run.
    setup_calls = []
    monkeypatch.setattr(au, 'start_setup_if_needed',
                        lambda was_enabled, config: setup_calls.append(was_enabled) or None)
    return app.test_client(), cm, setup_calls


class TestStatusRoutes:
    def test_status_carries_the_alert(self, api_client, monkeypatch):
        client, _, _ = api_client
        monkeypatch.setattr(au, 'describe_status', lambda config: {'alert': 'rolled back', 'alert_id': '7'})
        data = client.get('/api/v3/system/auto-update').get_json()['data']
        assert data == {'alert': 'rolled back', 'alert_id': '7'}

    @pytest.mark.parametrize('body', [{}, [1], 'x', 5])
    def test_dismiss_needs_an_id_in_a_json_object(self, api_client, body):
        client, _, _ = api_client
        assert client.post('/api/v3/system/auto-update/dismiss', json=body).status_code == 400

    def test_dismiss(self, api_client, monkeypatch):
        client, _, _ = api_client
        calls = []
        monkeypatch.setattr(au, 'dismiss_alert', calls.append)
        assert client.post('/api/v3/system/auto-update/dismiss', json={'alert_id': '7'}).status_code == 200
        assert calls == ['7']


class TestSettingsSave:
    def _saved(self, cm):
        return cm.save_config_atomic.call_args[0][0]

    def test_checked_toggle_saves_enabled(self, api_client):
        client, cm, setup_calls = api_client
        cm.load_config.return_value = {'timezone': 'UTC'}
        resp = client.post('/api/v3/config/main', json={'timezone': 'UTC', 'auto_update_enabled': 'true'})
        assert resp.status_code == 200
        saved = self._saved(cm)
        assert saved['auto_update'] == {'enabled': True}
        assert 'auto_update_enabled' not in saved, "the form field must not leak into config.json"
        assert setup_calls == [False], "setup is told the toggle was previously off"

    def test_unchecked_toggle_on_general_save_disables(self, api_client):
        client, cm, setup_calls = api_client
        # The General form marks its posts; an unchecked box is then absent.
        resp = client.post('/api/v3/config/main',
                           json={'__form_section': 'general', 'timezone': 'UTC'})
        assert resp.status_code == 200
        assert self._saved(cm)['auto_update'] == {'enabled': False}
        assert setup_calls == [True]

    def test_setup_note_reaches_the_user(self, api_client, monkeypatch):
        client, cm, _ = api_client
        cm.load_config.return_value = {'timezone': 'UTC'}
        monkeypatch.setattr(au, 'start_setup_if_needed',
                            lambda was_enabled, config: 'Finishing automatic update setup: the display is restarting.')
        body = client.post('/api/v3/config/main',
                           json={'timezone': 'UTC', 'auto_update_enabled': 'true'}).get_json()
        assert 'the display is restarting' in body['message']
