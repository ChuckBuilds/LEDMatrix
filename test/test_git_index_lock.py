"""A stale .git/index.lock must not block Update Code forever.

subprocess.run's timeout SIGKILLs git, and a killed git cannot remove its
index.lock. Every later update then failed with "Unable to create
'.../.git/index.lock': File exists" until someone removed the file over SSH.
The update now removes the lock when no git process can still own it.
"""
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
import contextlib
from unittest.mock import patch

import pytest
from flask import Flask

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from web_interface import git_index_lock as gil  # noqa: E402
from web_interface.blueprints import api_v3 as mod  # noqa: E402
from web_interface.blueprints.api_v3 import api_v3  # noqa: E402

needs_git = pytest.mark.skipif(shutil.which('git') is None, reason='git not installed')

OLD = 1_000_000.0


@contextlib.contextmanager
def _patched(fake):
    """Fake git for both the plain subprocess calls and the timeout-safe runner."""
    with patch.object(mod.subprocess, 'run', fake), patch.object(mod.git_index_lock, 'run_git', fake):
        yield


def _checkout(tmp_path, lock_age=None):
    repo = tmp_path / 'LEDMatrix'
    (repo / '.git').mkdir(parents=True)
    if lock_age is not None:
        lock = repo / '.git' / 'index.lock'
        lock.write_text('')
        os.utime(lock, (OLD, OLD))
    return repo


def _proc(tmp_path, *procs):
    """A fake /proc: ``procs`` are (comm, cwd[, argv]); cwd None means unreadable."""
    root = tmp_path / 'proc'
    root.mkdir()
    (root / 'self').mkdir()  # non-numeric entries are skipped
    for pid, (comm, cwd, *argv) in enumerate(procs, start=100):
        entry = root / str(pid)
        entry.mkdir()
        (entry / 'comm').write_text(comm + '\n')
        (entry / 'cmdline').write_bytes(b'\0'.join(a.encode() for a in [comm, *argv]) + b'\0')
        if cwd is not None:
            (entry / 'cwd').symlink_to(cwd)
        else:
            (entry / 'cwd').mkdir()  # readlink on a directory fails: unreadable
    return root


class TestClearStaleIndexLock:
    def test_no_lock_is_left_alone(self, tmp_path):
        repo = _checkout(tmp_path)
        assert gil.clear_stale_index_lock(repo, proc_root=_proc(tmp_path)) == gil.ABSENT

    def test_a_stale_lock_is_removed(self, tmp_path):
        repo = _checkout(tmp_path, lock_age=60)
        proc = _proc(tmp_path, ('python3', str(repo)), ('git', str(tmp_path / 'elsewhere')))
        assert gil.clear_stale_index_lock(repo, proc_root=proc, now=OLD + 60) == gil.REMOVED
        assert not (repo / '.git' / 'index.lock').exists()

    @pytest.mark.parametrize('comm', ['git', 'git-remote-http'])
    def test_a_lock_held_by_a_running_git_is_kept(self, tmp_path, comm):
        repo = _checkout(tmp_path, lock_age=60)
        proc = _proc(tmp_path, (comm, str(repo / 'src')))
        assert gil.clear_stale_index_lock(repo, proc_root=proc, now=OLD + 60) == gil.IN_USE
        assert (repo / '.git' / 'index.lock').exists()

    def test_a_git_pointed_at_the_checkout_from_elsewhere_is_running(self, tmp_path):
        repo = _checkout(tmp_path, lock_age=60)
        proc = _proc(tmp_path, ('git', str(tmp_path), f'--git-dir={repo.resolve()}/.git',
                                f'--work-tree={repo.resolve()}', 'checkout', 'v3.8.7'))
        assert gil.clear_stale_index_lock(repo, proc_root=proc, now=OLD + 60) == gil.IN_USE
        assert (repo / '.git' / 'index.lock').exists()

    @pytest.mark.parametrize('argv', [['-C', 'LEDMatrix', 'status'],
                                      ['--git-dir=LEDMatrix/.git', '--work-tree=LEDMatrix', 'add', '.']])
    def test_a_git_pointed_at_the_checkout_by_a_relative_path_is_running(self, tmp_path, argv):
        repo = _checkout(tmp_path, lock_age=60)
        proc = _proc(tmp_path, ('git', str(repo.resolve().parent), *argv))
        assert gil.clear_stale_index_lock(repo, proc_root=proc, now=OLD + 60) == gil.IN_USE

    def test_a_git_working_on_another_repository_is_not(self, tmp_path):
        repo = _checkout(tmp_path, lock_age=60)
        proc = _proc(tmp_path, ('git', str(tmp_path), '-C', 'other', 'pull'),
                     ('git', str(tmp_path), '--git-dir=LEDMatrix-backup/.git', 'fetch'))
        assert gil.clear_stale_index_lock(repo, proc_root=proc, now=OLD + 60) == gil.REMOVED

    def test_a_git_whose_cwd_cannot_be_read_counts_as_running(self, tmp_path):
        repo = _checkout(tmp_path, lock_age=60)
        proc = _proc(tmp_path, ('git', None))
        assert gil.clear_stale_index_lock(repo, proc_root=proc, now=OLD + 60) == gil.IN_USE
        assert (repo / '.git' / 'index.lock').exists()

    def test_a_brand_new_lock_is_kept(self, tmp_path):
        """A git may have created it between the process scan and the removal."""
        repo = _checkout(tmp_path, lock_age=0)
        assert gil.clear_stale_index_lock(repo, proc_root=_proc(tmp_path), now=OLD + 1) == gil.IN_USE
        assert (repo / '.git' / 'index.lock').exists()

    def test_without_proc_nothing_is_removed(self, tmp_path):
        repo = _checkout(tmp_path, lock_age=60)
        missing = tmp_path / 'no-proc'
        assert gil.clear_stale_index_lock(repo, proc_root=missing, now=OLD + 60) == gil.UNKNOWN
        assert (repo / '.git' / 'index.lock').exists()

    def test_a_checkout_without_a_git_directory_is_left_alone(self, tmp_path):
        assert gil.clear_stale_index_lock(tmp_path, proc_root=_proc(tmp_path)) == gil.ABSENT


@needs_git
@pytest.mark.skipif(not Path('/proc/self/comm').exists(), reason='needs /proc')
def test_git_works_again_after_a_real_stale_lock_is_cleared(tmp_path):
    repo = tmp_path / 'repo'
    env = {**os.environ, 'GIT_AUTHOR_NAME': 't', 'GIT_AUTHOR_EMAIL': 't@t',
           'GIT_COMMITTER_NAME': 't', 'GIT_COMMITTER_EMAIL': 't@t'}

    def git(*args):
        return subprocess.run(['git', *args], cwd=repo, env=env, capture_output=True, text=True)
    repo.mkdir()
    git('init', '-q')
    (repo / 'a.txt').write_text('a')
    lock = repo / '.git' / 'index.lock'
    lock.write_text('')
    old = time.time() - 60
    os.utime(lock, (old, old))
    assert 'index.lock' in git('add', 'a.txt').stderr

    assert gil.clear_stale_index_lock(repo) == gil.REMOVED
    assert git('add', 'a.txt').returncode == 0


# -- Update Code ----------------------------------------------------------------

@pytest.fixture
def client():
    app = Flask(__name__)
    app.config['TESTING'] = True
    app.register_blueprint(api_v3, url_prefix='/api/v3')
    api_v3.plugin_store_manager = None
    api_v3.config_manager = None
    return app.test_client()


def _fake_git(calls, pull_rc=0, pull_err=''):
    def run(args, **kwargs):
        calls.append(list(args))
        if args[:2] == ['git', 'rev-parse'] and args[-1] == 'HEAD':
            return subprocess.CompletedProcess(args, 0, stdout='aaa111\n', stderr='')
        if 'symbolic-full-name' in args or '@{u}' in args:
            return subprocess.CompletedProcess(args, 0, stdout='origin/main\n', stderr='')
        if args[:2] == ['git', 'pull']:
            return subprocess.CompletedProcess(args, pull_rc, stdout='', stderr=pull_err)
        return subprocess.CompletedProcess(args, 0, stdout='', stderr='')
    return run


def _update(client):
    return client.post('/api/v3/system/action', json={'action': 'git_pull'}).get_json()


def test_update_code_clears_the_lock_before_running_git(client):
    seen = []

    def clear(project_dir):
        seen.append(project_dir)
        return gil.REMOVED
    calls = []
    with patch.object(gil, 'clear_stale_index_lock', clear), \
            _patched(_fake_git(calls)):
        data = _update(client)
    assert seen == [str(mod.PROJECT_ROOT)]
    assert data['status'] == 'success'


def test_update_code_waits_for_a_git_that_is_still_running(client):
    calls = []
    with patch.object(gil, 'clear_stale_index_lock', lambda d: gil.IN_USE), \
            _patched(_fake_git(calls)):
        data = _update(client)
    assert data['status'] == 'error'
    assert 'Another git command is running' in data['message']
    assert calls == [], "nothing may touch the checkout while another git owns it"


def test_a_lock_it_could_not_clear_gets_the_manual_fix(client):
    err = "fatal: Unable to create '/home/ledpi/LEDMatrix/.git/index.lock': File exists.\n"
    calls = []
    with patch.object(gil, 'clear_stale_index_lock', lambda d: gil.UNKNOWN), \
            _patched(_fake_git(calls, pull_rc=128, pull_err=err)):
        data = _update(client)
    assert data['status'] == 'error'
    assert data['message'].startswith('Update failed: fatal: Unable to create')
    assert 'rm -f ' in data['message'] and 'index.lock' in data['message']


# -- run_git: a timed-out git must not leave a lock or an orphaned helper ------

def _lock_holder_script(tmp_path, lock):
    """A stand-in for a slow git: takes the lock, spawns a helper, then hangs."""
    script = tmp_path / 'slow.sh'
    script.write_text(f'#!/bin/sh\n: > "{lock}"\nsleep 60 &\necho $! > "{tmp_path}/helper.pid"\nwait\n')
    script.chmod(0o755)
    return script


def test_run_git_returns_a_completed_process(tmp_path):
    result = gil.run_git([sys.executable, '-c', 'print("hi")'], cwd=str(tmp_path), timeout=30)
    assert (result.returncode, result.stdout.strip()) == (0, 'hi')


@pytest.mark.skipif(not os.path.isdir('/proc'), reason='needs /proc')
def test_run_git_timeout_stops_the_group_and_clears_the_lock(tmp_path, monkeypatch):
    (tmp_path / '.git').mkdir()
    lock = tmp_path / '.git' / 'index.lock'
    script = _lock_holder_script(tmp_path, lock)
    monkeypatch.setattr(gil, 'MIN_STALE_AGE_SECONDS', 0)
    monkeypatch.setattr(gil, 'TERM_GRACE_SECONDS', 1)
    # The scan only sees processes named git, so the stand-in must not match;
    # the lock is then provably unowned once the group is gone.
    with pytest.raises(subprocess.TimeoutExpired):
        gil.run_git([str(script)], cwd=str(tmp_path), timeout=1)
    assert not lock.exists()
    helper = int((tmp_path / 'helper.pid').read_text())
    time.sleep(0.2)
    with pytest.raises(ProcessLookupError):
        os.kill(helper, 0)


def test_read_only_env_disables_optional_locks():
    assert gil.read_only_env()['GIT_OPTIONAL_LOCKS'] == '0'


def test_run_git_waits_out_the_freshness_window_before_giving_up_on_the_lock(tmp_path, monkeypatch):
    # The lock is created a moment before the timeout, so on age alone it is
    # still "in use" once git is stopped; it must not be left to block the retry.
    (tmp_path / '.git').mkdir()
    lock = tmp_path / '.git' / 'index.lock'
    script = tmp_path / 'slow.sh'
    script.write_text(f'#!/bin/sh\n: > "{lock}"\nexec sleep 60\n')
    script.chmod(0o755)
    monkeypatch.setattr(gil, 'MIN_STALE_AGE_SECONDS', 2)
    monkeypatch.setattr(gil, 'TERM_GRACE_SECONDS', 1)
    with pytest.raises(subprocess.TimeoutExpired):
        gil.run_git([str(script)], cwd=str(tmp_path), timeout=1)
    assert not lock.exists()


# -- a rebase in the checkout ----------------------------------------------------

def _rebase_checkout(tmp_path, monkeypatch):
    from web_interface.blueprints.api_v3 import system
    (tmp_path / '.git' / 'rebase-merge').mkdir(parents=True)
    monkeypatch.setattr(system, 'PROJECT_ROOT', tmp_path)
    return tmp_path / '.git' / 'rebase-merge'


def _rebase_git(calls, on_branch, quit_rc=0, remove_dir=None):
    base = _fake_git(calls)

    def run(args, **kwargs):
        if args[:2] == ['git', 'symbolic-ref']:
            calls.append(list(args))
            return subprocess.CompletedProcess(args, 0 if on_branch else 1, stdout='', stderr='')
        if args[:3] == ['git', 'rebase', '--quit']:
            calls.append(list(args))
            if quit_rc == 0 and remove_dir is not None:
                remove_dir.rmdir()
            return subprocess.CompletedProcess(args, quit_rc, stdout='', stderr='cannot quit' if quit_rc else '')
        return base(args, **kwargs)
    return run


def test_a_rebase_in_progress_is_left_alone(client, tmp_path, monkeypatch):
    _rebase_checkout(tmp_path, monkeypatch)
    calls = []
    with _patched(_rebase_git(calls, on_branch=False)):
        data = _update(client)
    assert data['status'] == 'error' and 'rebase is in progress' in data['message']
    assert not any(c[:2] in (['git', 'pull'], ['git', 'checkout']) or c[:3] == ['git', 'rebase', '--quit']
                   for c in calls), "a paused rebase holds the user's conflict work"


def test_an_abandoned_rebase_is_quit_before_the_pull(client, tmp_path, monkeypatch):
    rebase = _rebase_checkout(tmp_path, monkeypatch)
    calls = []
    with _patched(_rebase_git(calls, on_branch=True, remove_dir=rebase)):
        _update(client)
    names = [c[1] for c in calls]
    assert ['git', 'rebase', '--quit'] in calls
    assert names.index('rebase') < names.index('pull')


def test_a_rebase_that_cannot_be_cleared_stops_the_update(client, tmp_path, monkeypatch):
    _rebase_checkout(tmp_path, monkeypatch)
    calls = []
    with _patched(_rebase_git(calls, on_branch=True, quit_rc=1)):
        data = _update(client)
    assert data['status'] == 'error' and 'could not be cleared' in data['message']
    assert not any(c[:2] == ['git', 'pull'] for c in calls)


def test_the_automatic_status_check_is_read_only_whatever_the_runner(tmp_path):
    from web_interface import auto_update
    seen = {}

    def run(args, **kwargs):
        seen.update(kwargs)
        return subprocess.CompletedProcess(args, 0, stdout='', stderr='')
    auto_update.local_changes(tmp_path, run=run)
    assert seen['env']['GIT_OPTIONAL_LOCKS'] == '0'
