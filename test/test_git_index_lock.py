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
from unittest.mock import patch

import pytest
from flask import Flask

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from web_interface import git_index_lock as gil  # noqa: E402
from web_interface.blueprints import api_v3 as mod  # noqa: E402
from web_interface.blueprints.api_v3 import api_v3  # noqa: E402

needs_git = pytest.mark.skipif(shutil.which('git') is None, reason='git not installed')

OLD = 1_000_000.0


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
            patch.object(mod.subprocess, 'run', _fake_git(calls)):
        data = _update(client)
    assert seen == [str(mod.PROJECT_ROOT)]
    assert data['status'] == 'success'


def test_update_code_waits_for_a_git_that_is_still_running(client):
    calls = []
    with patch.object(gil, 'clear_stale_index_lock', lambda d: gil.IN_USE), \
            patch.object(mod.subprocess, 'run', _fake_git(calls)):
        data = _update(client)
    assert data['status'] == 'error'
    assert 'Another git command is running' in data['message']
    assert calls == [], "nothing may touch the checkout while another git owns it"


def test_a_lock_it_could_not_clear_gets_the_manual_fix(client):
    err = "fatal: Unable to create '/home/ledpi/LEDMatrix/.git/index.lock': File exists.\n"
    calls = []
    with patch.object(gil, 'clear_stale_index_lock', lambda d: gil.UNKNOWN), \
            patch.object(mod.subprocess, 'run', _fake_git(calls, pull_rc=128, pull_err=err)):
        data = _update(client)
    assert data['status'] == 'error'
    assert data['message'].startswith('Update failed: fatal: Unable to create')
    assert 'rm -f ' in data['message'] and 'index.lock' in data['message']
