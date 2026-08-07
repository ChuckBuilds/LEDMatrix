"""Guard: the update button works on branches without tracking information.

`git pull --rebase` fails outright on a branch with no upstream:

    There is no tracking information for the current branch.
    Please specify which branch you want to rebase against.

That is easy to land on — checking out a branch by name, restoring a
backup, or following a guide that names one — and the Tools tab reported it
as a bare "Update failed; check logs for details", which the user cannot act
on. resolve_pull_command() falls back to an explicit `origin <branch>` pull
when that remote branch exists, and returns an actionable message when it
does not.

These tests build real git repositories in a temp dir, so they exercise git's
actual behaviour rather than a mock of it.
"""
import subprocess
from pathlib import Path

import pytest

from web_interface.blueprints.api_v3 import (
    checkout_branch,
    is_valid_branch_name,
    resolve_pull_command,
)

pytestmark = pytest.mark.skipif(
    subprocess.run(['git', '--version'], capture_output=True).returncode != 0,
    reason='git not available',
)


def _git(*args, cwd):
    return subprocess.run(['git', *args], cwd=str(cwd),
                          capture_output=True, text=True, check=True)


@pytest.fixture()
def repos(tmp_path):
    """An 'origin' repo with a main branch, and a clone of it."""
    origin = tmp_path / 'origin'
    origin.mkdir()
    _git('init', '--initial-branch=main', '--bare', cwd=origin)

    work = tmp_path / 'work'
    _git('clone', str(origin), str(work), cwd=tmp_path)
    _git('config', 'user.email', 'test@example.com', cwd=work)
    _git('config', 'user.name', 'Test', cwd=work)
    (work / 'README.md').write_text('hello\n')
    _git('add', 'README.md', cwd=work)
    _git('commit', '-m', 'initial', cwd=work)
    _git('push', '-u', 'origin', 'main', cwd=work)
    return work


def test_branch_with_upstream_uses_a_plain_pull(repos):
    args, note, error = resolve_pull_command(str(repos))
    assert error is None
    assert args == ['git', 'pull', '--rebase']
    assert note == ''


def test_branch_without_upstream_falls_back_to_origin_branch(repos):
    """The reported bug: a local branch that also exists on origin."""
    _git('push', 'origin', 'main:audit', cwd=repos)
    _git('fetch', 'origin', cwd=repos)
    # A branch created this way has no tracking information.
    _git('checkout', '-b', 'audit', cwd=repos)
    assert subprocess.run(['git', 'rev-parse', '--abbrev-ref', '@{u}'],
                          cwd=str(repos), capture_output=True).returncode != 0

    args, note, error = resolve_pull_command(str(repos))
    assert error is None
    assert args == ['git', 'pull', '--rebase', 'origin', 'audit']
    assert 'audit' in note


def test_pull_fallback_actually_succeeds(repos):
    """The fallback command must work, not merely look right."""
    _git('push', 'origin', 'main:audit', cwd=repos)
    _git('fetch', 'origin', cwd=repos)
    _git('checkout', '-b', 'audit', cwd=repos)

    args, _, error = resolve_pull_command(str(repos))
    assert error is None
    done = subprocess.run(args, cwd=str(repos), capture_output=True, text=True)
    assert done.returncode == 0, done.stderr


def test_local_only_branch_reports_an_actionable_message(repos):
    """No upstream and no origin/<branch>: say so, don't just fail."""
    _git('checkout', '-b', 'local-experiment', cwd=repos)
    args, _, error = resolve_pull_command(str(repos))
    assert args is None
    assert error and 'local-experiment' in error
    assert 'no origin/local-experiment' in error


def test_detached_head_reports_an_actionable_message(repos):
    head = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=str(repos),
                          capture_output=True, text=True).stdout.strip()
    _git('checkout', head, cwd=repos)
    args, _, error = resolve_pull_command(str(repos))
    assert args is None
    assert error and 'detached HEAD' in error


def test_missing_directory_does_not_raise(tmp_path):
    """A bad path must return an error, not blow up the request."""
    args, _, error = resolve_pull_command(str(tmp_path / 'nope'))
    assert args is None
    assert error


# ── branch switching ────────────────────────────────────────────────────────


@pytest.mark.parametrize('name', [
    'main', 'audit', 'feat/thing', 'release-1.2', 'a_b.c',
])
def test_valid_branch_names_accepted(name):
    assert is_valid_branch_name(name)


@pytest.mark.parametrize('name', [
    '', '   ', 'a b', 'a;rm -rf /', '--upload-pack=evil', '-x',
    'a..b', 'a\nb', 'x' * 201, 'branch$(whoami)', '../escape',
])
def test_unsafe_branch_names_rejected(name):
    """The value reaches a subprocess argument list, so refuse the exotic."""
    assert not is_valid_branch_name(name)


def test_switch_to_remote_only_branch_creates_it_with_tracking(repos):
    _git('push', 'origin', 'main:release', cwd=repos)
    _git('fetch', 'origin', cwd=repos)

    payload, code = checkout_branch(str(repos), 'release')
    assert code == 200 and payload['status'] == 'success', payload

    assert _git('branch', '--show-current', cwd=repos).stdout.strip() == 'release'
    upstream = subprocess.run(['git', 'rev-parse', '--abbrev-ref', '@{u}'],
                              cwd=str(repos), capture_output=True, text=True)
    assert upstream.stdout.strip() == 'origin/release'


def test_switching_attaches_tracking_so_pull_needs_no_fallback(repos):
    """The whole point: after switching, a plain `git pull` works."""
    _git('push', 'origin', 'main:audit', cwd=repos)
    _git('fetch', 'origin', cwd=repos)
    payload, _ = checkout_branch(str(repos), 'audit')
    assert payload['status'] == 'success'

    args, note, error = resolve_pull_command(str(repos))
    assert error is None
    assert args == ['git', 'pull', '--rebase']
    assert note == ''


def test_unknown_branch_is_reported_not_created(repos):
    payload, code = checkout_branch(str(repos), 'does-not-exist')
    assert code == 404
    assert 'does-not-exist' in payload['message']
    assert _git('branch', '--show-current', cwd=repos).stdout.strip() == 'main'


def test_local_edits_block_the_switch_and_name_the_files(repos):
    _git('push', 'origin', 'main:other', cwd=repos)
    _git('fetch', 'origin', cwd=repos)
    _git('checkout', '-b', 'other', 'origin/other', cwd=repos)
    (repos / 'README.md').write_text('changed on other\n')
    _git('add', 'README.md', cwd=repos)
    _git('commit', '-m', 'diverge', cwd=repos)
    _git('checkout', 'main', cwd=repos)
    (repos / 'README.md').write_text('uncommitted local edit\n')

    payload, code = checkout_branch(str(repos), 'other')
    assert code == 200 and payload['status'] == 'error'
    assert payload['can_retry_with_stash'] is True
    assert 'README.md' in payload['detail']
    assert _git('branch', '--show-current', cwd=repos).stdout.strip() == 'main'


def test_stash_option_lets_the_switch_through_and_keeps_the_work(repos):
    """stash=True must switch *and* leave the edit recoverable."""
    _git('push', 'origin', 'main:other', cwd=repos)
    _git('fetch', 'origin', cwd=repos)
    _git('checkout', '-b', 'other', 'origin/other', cwd=repos)
    (repos / 'README.md').write_text('changed on other\n')
    _git('add', 'README.md', cwd=repos)
    _git('commit', '-m', 'diverge', cwd=repos)
    _git('checkout', 'main', cwd=repos)
    (repos / 'README.md').write_text('uncommitted local edit\n')

    payload, code = checkout_branch(str(repos), 'other', stash=True)
    assert code == 200 and payload['status'] == 'success', payload
    assert 'stashed' in payload['message']
    assert _git('branch', '--show-current', cwd=repos).stdout.strip() == 'other'
    # The edit is not lost — it is on the stash.
    assert 'switch to other' in _git('stash', 'list', cwd=repos).stdout
