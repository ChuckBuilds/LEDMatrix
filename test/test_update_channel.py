"""Stable and beta update channels (web_interface/update_channel.py).

stable follows the newest vX.Y.Z release tag; beta follows main, as every
device did before channels existed. What these pin down:

* which tags count as releases (not pre-releases, not anything else);
* which channel is in effect, including configs written before channels
  existed, which follow main until the newest release contains their commit
  and then move to stable and say so in config.json;
* that nothing ever moves a device to an older commit than the one it runs;
* that the real update paths (Update Code, the weekly updater and its
  rollback) handle a detached release checkout and switching back and forth.

Git runs for real against a throwaway origin and device clone.
"""
import importlib.util
import shutil
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from flask import Flask

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from web_interface import update_channel as uc  # noqa: E402
from web_interface import auto_update as au  # noqa: E402

needs_git = pytest.mark.skipif(shutil.which('git') is None, reason='git not installed')

_spec = importlib.util.spec_from_file_location(
    'auto_update_verify_for_channels', ROOT / 'scripts' / 'utils' / 'auto_update_verify.py')
av = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(av)


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
        (self.seed / 'notes.txt').write_text('notes\n')
        git(self.seed, 'add', '.')
        git(self.seed, 'commit', '-qm', 'one')
        self.origin = tmp / 'origin.git'
        git(tmp, 'clone', '-q', '--bare', str(self.seed), str(self.origin))
        git(self.seed, 'remote', 'add', 'origin', str(self.origin))
        self.device = tmp / 'device'
        git(tmp, 'clone', '-q', str(self.origin), str(self.device))

    def head(self):
        return git(self.device, 'rev-parse', 'HEAD')

    def branch(self):
        return uc.current_branch(self.device)

    def publish(self, text):
        (self.seed / 'app.py').write_text(text)
        git(self.seed, 'commit', '-qam', text.strip())
        git(self.seed, 'push', '-q', 'origin', 'main')
        return git(self.seed, 'rev-parse', 'HEAD')

    def tag(self, name, annotated=False):
        if annotated:
            git(self.seed, 'tag', '-a', name, '-m', name)
        else:
            git(self.seed, 'tag', name)
        git(self.seed, 'push', '-q', 'origin', name)
        return git(self.seed, 'rev-parse', f'{name}^{{commit}}')

    def fetch(self):
        assert uc.fetch(self.device).returncode == 0

    def resolve(self, channel=None):
        self.fetch()
        config = {'auto_update': {'channel': channel}} if channel else {}
        return uc.resolve(self.device, config)


STABLE = {'auto_update': {'channel': 'stable'}}
BETA = {'auto_update': {'channel': 'beta'}}


# -- tag parsing ---------------------------------------------------------------

class TestReleaseTags:
    @pytest.mark.parametrize('name,expected', [
        ('v3.7.0', (3, 7, 0)),
        ('v0.0.1', (0, 0, 1)),
        ('v10.20.30', (10, 20, 30)),
        (' v3.7.0\n', (3, 7, 0)),
    ])
    def test_releases_parse(self, name, expected):
        assert uc.parse_release_tag(name) == expected

    @pytest.mark.parametrize('name', [
        'v3.8.0-rc1', 'v3.8.0-beta.2', 'v3.8.0+build5',   # pre-releases, build metadata
        '3.7.0', 'v3.7', 'v3', 'v3.7.0.1', 'V3.7.0',       # not vX.Y.Z
        'v03.7.0', 'v3.07.0',                              # leading zeros are not semver
        'release-3.7', 'latest', 'stable', '', None,
    ])
    def test_everything_else_is_ignored(self, name):
        assert uc.parse_release_tag(name) is None

    def test_newest_is_by_semver_not_by_string(self):
        # String order would pick v3.9.0; v3.10.0 is newer.
        assert uc.newest_release_tag(['v3.9.0', 'v3.10.0', 'v3.2.1']) == 'v3.10.0'

    def test_a_newer_pre_release_or_junk_tag_is_not_newest(self):
        assert uc.newest_release_tag(['v3.7.0', 'v3.8.0-rc1', 'v99', 'nightly']) == 'v3.7.0'

    def test_no_releases(self):
        assert uc.newest_release_tag(['v3.8.0-rc1', 'foo']) is None
        assert uc.newest_release_tag([]) is None


class TestConfiguredChannel:
    def test_reads_auto_update_channel(self):
        assert uc.configured_channel(STABLE) == 'stable'
        assert uc.configured_channel(BETA) == 'beta'
        assert uc.configured_channel({'auto_update': {'channel': ' Beta '}}) == 'beta'

    @pytest.mark.parametrize('config', [
        {}, {'auto_update': {}}, {'auto_update': True}, {'auto_update': {'channel': 'nightly'}},
        {'auto_update': {'channel': 3}}, None,
    ])
    def test_missing_or_nonsense_is_none(self, config):
        assert uc.configured_channel(config) is None

    def test_template_default_is_stable(self):
        import json
        template = json.loads((ROOT / 'config' / 'config.template.json').read_text(encoding='utf-8'))
        assert template['auto_update']['channel'] == 'stable'

    def test_set_channel_rejects_nonsense(self):
        with pytest.raises(ValueError):
            uc.set_channel(MagicMock(), 'nightly')


# -- channel selection, migration and no-downgrade -------------------------------

@needs_git
class TestResolve:
    def test_beta_on_a_branch_pulls(self, tmp_path):
        repo = Repo(tmp_path)
        repo.tag('v1.0.0')
        status = repo.resolve('beta')
        assert (status.channel, status.action) == ('beta', uc.ACTION_PULL)

    def test_no_releases_updates_as_before(self, tmp_path):
        repo = Repo(tmp_path)
        repo.publish('v = 2\n')
        for channel in (None, 'stable'):
            status = repo.resolve(channel)
            assert (status.channel, status.action, status.waiting) == ('beta', uc.ACTION_PULL, True)
            assert not status.migrate

    def test_legacy_config_moves_to_a_release_that_contains_its_commit(self, tmp_path):
        repo = Repo(tmp_path)
        repo.publish('v = 2\n')
        sha = repo.tag('v1.0.0')
        status = repo.resolve(None)
        assert status.channel == 'stable'
        assert status.action == uc.ACTION_CHECKOUT_TAG
        assert status.target_sha == sha and status.newest_release == 'v1.0.0'
        assert status.migrate, "a config without a channel must be written as stable when it moves"

    def test_legacy_config_ahead_of_the_newest_release_stays_on_main(self, tmp_path):
        repo = Repo(tmp_path)
        repo.tag('v1.0.0')
        repo.publish('v = 2\n')
        git(repo.device, 'pull', '-q')        # device now newer than v1.0.0
        status = repo.resolve(None)
        assert (status.channel, status.action) == ('beta', uc.ACTION_PULL)
        assert status.waiting and not status.migrate

    def test_legacy_config_on_the_release_commit_is_migrated_without_moving(self, tmp_path):
        repo = Repo(tmp_path)
        repo.tag('v1.0.0')
        status = repo.resolve(None)
        assert (status.channel, status.action) == ('stable', uc.ACTION_NONE)
        assert status.migrate and status.current_release == 'v1.0.0'

    def test_stable_never_picks_an_older_release(self, tmp_path):
        """Switching beta -> stable on a device ahead of the newest release."""
        repo = Repo(tmp_path)
        old_release = repo.tag('v1.0.0')
        repo.publish('v = 2\n')
        git(repo.device, 'pull', '-q')
        status = repo.resolve('stable')
        assert status.action != uc.ACTION_CHECKOUT_TAG
        assert status.target_sha != old_release
        assert status.waiting and 'newer than the newest release' in status.message

    def test_stable_waiting_while_detached_stays_put(self, tmp_path):
        repo = Repo(tmp_path)
        repo.tag('v1.0.0')
        repo.publish('v = 2\n')
        git(repo.device, 'fetch', '-q')
        git(repo.device, 'checkout', '-q', '--detach', 'origin/main')
        status = repo.resolve('stable')
        assert (status.action, status.waiting) == (uc.ACTION_NONE, True)

    def test_a_newer_release_is_taken_once_it_contains_the_commit(self, tmp_path):
        repo = Repo(tmp_path)
        repo.tag('v1.0.0')
        repo.publish('v = 2\n')
        git(repo.device, 'pull', '-q')
        repo.publish('v = 3\n')
        new = repo.tag('v1.1.0', annotated=True)   # annotated tags resolve to their commit
        status = repo.resolve('stable')
        assert (status.action, status.newest_release, status.target_sha) == (
            uc.ACTION_CHECKOUT_TAG, 'v1.1.0', new)

    def test_a_newer_pre_release_is_not_followed(self, tmp_path):
        repo = Repo(tmp_path)
        repo.tag('v1.0.0')
        repo.publish('v = 2\n')
        repo.tag('v1.1.0-rc1')
        status = repo.resolve('stable')
        assert (status.action, status.newest_release) == (uc.ACTION_NONE, 'v1.0.0')

    def test_beta_on_a_detached_release_switches_back_to_main(self, tmp_path):
        repo = Repo(tmp_path)
        repo.tag('v1.0.0')
        git(repo.device, 'fetch', '-q', '--tags')
        git(repo.device, 'checkout', '-q', '--detach', 'v1.0.0')
        status = repo.resolve('beta')
        assert (status.channel, status.action) == ('beta', uc.ACTION_SWITCH_TO_BETA)


@needs_git
class TestCheckout:
    def test_refuses_to_move_backwards(self, tmp_path):
        repo = Repo(tmp_path)
        repo.tag('v1.0.0')
        repo.publish('v = 2\n')
        git(repo.device, 'pull', '-q')
        repo.fetch()
        head = repo.head()
        result, _ = uc.checkout_release(repo.device, 'v1.0.0')
        assert result.returncode != 0 and 'backwards' in result.stderr
        assert repo.head() == head

    def test_refuses_a_non_release_name(self, tmp_path):
        with pytest.raises(ValueError):
            uc.checkout_release(tmp_path, '--orphan')

    def test_local_edits_are_carried_across(self, tmp_path):
        repo = Repo(tmp_path)
        repo.publish('v = 2\n')
        repo.tag('v1.0.0')
        repo.fetch()
        (repo.device / 'notes.txt').write_text('my notes\n')
        result, note = uc.checkout_release(repo.device, 'v1.0.0')
        assert result.returncode == 0, result.stderr
        assert (repo.device / 'app.py').read_text() == 'v = 2\n'
        assert (repo.device / 'notes.txt').read_text() == 'my notes\n'
        assert note == '' and git(repo.device, 'stash', 'list') == ''

    def test_edits_that_no_longer_apply_are_kept_in_the_stash(self, tmp_path):
        repo = Repo(tmp_path)
        repo.publish('v = 2\n')
        repo.tag('v1.0.0')
        repo.fetch()
        (repo.device / 'app.py').write_text('v = "mine"\n')    # conflicts with v = 2
        result, note = uc.checkout_release(repo.device, 'v1.0.0')
        assert result.returncode == 0
        assert 'stash' in note
        assert (repo.device / 'app.py').read_text() == 'v = 2\n'
        assert git(repo.device, 'status', '--porcelain', '--untracked-files=no') == ''
        assert 'LEDMatrix autostash' in git(repo.device, 'stash', 'list')
        assert git(repo.device, 'stash', 'show', '-p', 'stash@{0}').count('mine') == 1


# -- Update Code (perform_core_update) against a real clone --------------------------

class FakeConfigManager:
    def __init__(self, config):
        self.config = config
        self.saves = 0

    def load_config(self):
        import copy
        return copy.deepcopy(self.config)

    def save_config(self, config):
        self.config = config
        self.saves += 1


@pytest.fixture
def update_code(monkeypatch):
    """The real perform_core_update, pointed at a test clone and a config."""
    from web_interface.blueprints import api_v3 as pkg
    from web_interface.blueprints.api_v3 import system

    def point_at(device, config):
        cm = FakeConfigManager(config)
        monkeypatch.setattr(system, 'PROJECT_ROOT', device)
        monkeypatch.setattr(pkg.api_v3, 'plugin_store_manager', None, raising=False)
        monkeypatch.setattr(pkg.api_v3, 'config_manager', cm, raising=False)
        monkeypatch.setattr(system, '_pip_install_requirements',
                            lambda *a, **k: pytest.fail('no requirements changed'))
        return system.perform_core_update, cm
    return point_at


@needs_git
class TestUpdateCode:
    def test_stable_checks_out_the_newest_release(self, tmp_path, update_code):
        repo = Repo(tmp_path)
        repo.publish('v = 2\n')
        release = repo.tag('v1.0.0')
        repo.publish('v = 3 (unreleased)\n')
        update, cm = update_code(repo.device, dict(STABLE))
        result = update()
        assert result['status'] == 'success', result['message']
        assert repo.head() == release and repo.branch() == ''
        assert 'v1.0.0' in result['message'] and result['restart_required']
        assert result['channel'] == 'stable'
        assert update()['restart_required'] is False, "second run is already up to date"

    def test_legacy_config_is_migrated_and_saved(self, tmp_path, update_code):
        repo = Repo(tmp_path)
        repo.publish('v = 2\n')
        release = repo.tag('v1.0.0')
        update, cm = update_code(repo.device, {'auto_update': {'enabled': False}})
        assert update()['status'] == 'success'
        assert repo.head() == release
        assert cm.config['auto_update'] == {'enabled': False, 'channel': 'stable'}

    def test_legacy_config_ahead_of_the_release_keeps_pulling_main(self, tmp_path, update_code):
        repo = Repo(tmp_path)
        repo.tag('v1.0.0')
        repo.publish('v = 2\n')
        git(repo.device, 'pull', '-q')
        newest = repo.publish('v = 3\n')
        update, cm = update_code(repo.device, {'auto_update': {'enabled': True}})
        result = update()
        assert result['status'] == 'success', result['message']
        assert repo.head() == newest and repo.branch() == 'main'
        assert 'channel' not in cm.config['auto_update'], "not migrated while ahead of the release"
        # The next release contains the device's commit: now it moves, forward.
        release = repo.tag('v1.1.0')
        assert update()['status'] == 'success'
        assert repo.head() == release
        assert cm.config['auto_update']['channel'] == 'stable'

    def test_stable_beta_stable_round_trip_never_goes_backwards(self, tmp_path, update_code):
        repo = Repo(tmp_path)
        repo.publish('v = 2\n')
        r1 = repo.tag('v1.0.0')
        tip = repo.publish('v = 3\n')
        update, cm = update_code(repo.device, dict(STABLE))

        assert update()['status'] == 'success'
        assert (repo.head(), repo.branch()) == (r1, '')

        cm.config = dict(BETA)
        result = update()
        assert result['status'] == 'success', result['message']
        assert (repo.head(), repo.branch()) == (tip, 'main')
        assert git(repo.device, 'rev-parse', '--abbrev-ref', '@{u}') == 'origin/main'

        cm.config = dict(STABLE)
        result = update()
        assert result['status'] == 'success'
        assert (repo.head(), repo.branch()) == (tip, 'main'), \
            "switching to stable ahead of the newest release must not check out v1.0.0"
        assert 'newer than the newest release' in result['message']

        # A release on exactly this commit: already there, nothing moves.
        assert repo.tag('v1.1.0') == tip
        assert update()['status'] == 'success'
        assert (repo.head(), repo.branch()) == (tip, 'main')
        # The next one is newer: now it moves to the release.
        repo.publish('v = 4\n')
        r3 = repo.tag('v1.2.0')
        assert update()['status'] == 'success'
        assert (repo.head(), repo.branch()) == (r3, '')

    def test_beta_from_a_release_keeps_plugin_edits(self, tmp_path, update_code):
        repo = Repo(tmp_path)
        (repo.seed / 'plugin-repos').mkdir()
        (repo.seed / 'plugin-repos' / 'p.py').write_text('x = 1\n')
        git(repo.seed, 'add', '.')
        git(repo.seed, 'commit', '-qm', 'plugin')
        git(repo.seed, 'push', '-q', 'origin', 'main')
        repo.tag('v1.0.0')
        tip = repo.publish('v = 2\n')
        git(repo.device, 'fetch', '-q', '--tags')
        git(repo.device, 'checkout', '-q', '--detach', 'v1.0.0')
        (repo.device / 'plugin-repos' / 'p.py').write_text('x = 2  # store update\n')
        update, _ = update_code(repo.device, dict(BETA))
        result = update(stash_local_changes=False)
        assert result['status'] == 'success', result['message']
        assert (repo.head(), repo.branch()) == (tip, 'main')
        assert 'store update' in (repo.device / 'plugin-repos' / 'p.py').read_text()

    def test_a_failed_fetch_reports_and_changes_nothing(self, tmp_path, update_code):
        repo = Repo(tmp_path)
        head = repo.head()
        git(repo.device, 'remote', 'set-url', 'origin', str(tmp_path / 'gone.git'))
        update, _ = update_code(repo.device, dict(STABLE))
        result = update()
        assert result['status'] == 'error' and result['message'].startswith('Update failed')
        assert repo.head() == head


# -- the weekly updater and its rollback --------------------------------------------

def _updater(repo, config, pickup=True):
    """An AutoUpdater on the clone whose core update is the real Update Code path."""
    from web_interface.blueprints import api_v3 as pkg
    from web_interface.blueprints.api_v3 import system
    cm = FakeConfigManager(config)
    state = {}

    def run(args, **kwargs):
        if args[0] == 'sudo':
            return subprocess.CompletedProcess(args, 0, stdout='', stderr='')
        return subprocess.run(args, **kwargs)

    def sleep(seconds):
        updater = state['updater']
        if pickup and updater.request_file.exists():
            updater.request_file.unlink()
            pending = au._read_json(updater.pending_file)
            pending['status'] = 'verifying'
            au._write_json(updater.pending_file, pending)

    updater = au.AutoUpdater(
        config_manager=cm, core_update=system.perform_core_update,
        project_root=repo.device, clock=lambda: 0, restart=lambda unit: None, run=run,
        service_active=lambda unit: True, helper_ready=lambda: True,
        disk_free=lambda path: 10 ** 12, sleep=sleep)
    state['updater'] = updater
    return updater, cm


@needs_git
class TestWeeklyUpdateAndRollback:
    @pytest.fixture(autouse=True)
    def wire(self, monkeypatch):
        from web_interface.blueprints import api_v3 as pkg
        from web_interface.blueprints.api_v3 import system
        self.monkeypatch = monkeypatch
        monkeypatch.setattr(pkg.api_v3, 'plugin_store_manager', None, raising=False)
        monkeypatch.setattr(system, '_pip_install_requirements',
                            lambda *a, **k: pytest.fail('no requirements changed'))

    def _point(self, repo, cm):
        from web_interface.blueprints import api_v3 as pkg
        from web_interface.blueprints.api_v3 import system
        self.monkeypatch.setattr(system, 'PROJECT_ROOT', repo.device)
        self.monkeypatch.setattr(pkg.api_v3, 'config_manager', cm, raising=False)

    def test_preflight_targets_the_release_and_records_the_branch(self, tmp_path):
        repo = Repo(tmp_path)
        repo.publish('v = 2\n')
        release = repo.tag('v1.0.0')
        updater, cm = _updater(repo, dict(STABLE))
        outcome, _, info = updater.preflight({})
        assert outcome == 'ready'
        assert info['upstream_head'] == release and info['old_ref'] == 'main'
        assert info['release'] == 'v1.0.0'

    def test_preflight_on_the_newest_release_is_up_to_date_and_migrates(self, tmp_path):
        repo = Repo(tmp_path)
        repo.tag('v1.0.0')
        updater, cm = _updater(repo, {'auto_update': {'enabled': True}})
        outcome, message, _ = updater.preflight({})
        assert outcome == 'up_to_date' and 'v1.0.0' in message
        assert cm.config['auto_update']['channel'] == 'stable'

    def test_a_release_that_was_rolled_back_is_not_retried(self, tmp_path):
        repo = Repo(tmp_path)
        repo.publish('v = 2\n')
        release = repo.tag('v1.0.0')
        updater, _ = _updater(repo, dict(STABLE))
        outcome, message, _ = updater.preflight({'rolled_back_head': release})
        assert outcome == 'up_to_date' and 'v1.0.0' in message

    def test_move_to_a_release_then_roll_back_returns_to_main(self, tmp_path):
        repo = Repo(tmp_path)
        before = repo.head()
        repo.publish('v = 2\n')
        release = repo.tag('v1.0.0')
        updater, cm = _updater(repo, dict(STABLE))
        self._point(repo, cm)
        result = updater.run()
        assert result['core_outcome'] == 'verifying', result['core_message']
        assert 'v1.0.0' in result['core_message']
        pending = au._read_json(updater.pending_file)
        assert (pending['old_ref'], pending['new_head'], pending['release']) == ('main', release, 'v1.0.0')
        assert (repo.head(), repo.branch()) == (release, '')

        ok, detail = av.Verifier(repo.device).rollback(pending)
        assert ok, detail
        assert (repo.head(), repo.branch()) == (before, 'main'), \
            "rollback must put HEAD back on main, not leave it detached"
        assert git(repo.device, 'rev-parse', 'main') == before

    def test_back_to_main_then_roll_back_returns_to_the_release(self, tmp_path):
        repo = Repo(tmp_path)
        release = repo.tag('v1.0.0')
        tip = repo.publish('v = 2\n')
        git(repo.device, 'fetch', '-q', '--tags')
        git(repo.device, 'checkout', '-q', '--detach', 'v1.0.0')
        updater, cm = _updater(repo, dict(BETA))
        self._point(repo, cm)
        result = updater.run()
        assert result['core_outcome'] == 'verifying', result['core_message']
        pending = au._read_json(updater.pending_file)
        assert pending['old_ref'] == '' and (repo.head(), repo.branch()) == (tip, 'main')

        ok, detail = av.Verifier(repo.device).rollback(pending)
        assert ok, detail
        assert (repo.head(), repo.branch()) == (release, '')
        assert git(repo.device, 'rev-parse', 'main') == tip, \
            "rolling back from main to a release must not drag main backwards"

    def test_rollback_without_old_ref_behaves_as_before(self, tmp_path):
        repo = Repo(tmp_path)
        old = repo.head()
        new = repo.publish('v = 2\n')
        git(repo.device, 'pull', '-q')
        ok, _ = av.Verifier(repo.device).rollback({'old_head': old, 'new_head': new})
        assert ok and (repo.head(), repo.branch()) == (old, 'main')


# -- API and the General tab ---------------------------------------------------------

@pytest.fixture
def client(monkeypatch, tmp_path):
    from web_interface.blueprints import api_v3 as pkg
    from web_interface.blueprints.api_v3 import api_v3, system
    app = Flask(__name__)
    app.config['TESTING'] = True
    app.register_blueprint(api_v3, url_prefix='/api/v3')
    cm = FakeConfigManager({'auto_update': {'enabled': False}})
    monkeypatch.setattr(pkg.api_v3, 'config_manager', cm, raising=False)
    monkeypatch.setattr(pkg.api_v3, 'plugin_manager', None, raising=False)
    repo = Repo(tmp_path)
    monkeypatch.setattr(system, 'PROJECT_ROOT', repo.device)
    c = app.test_client()
    c.cm, c.repo = cm, repo
    return c


@needs_git
class TestChannelApi:
    def test_rejects_an_unknown_channel(self, client):
        for body in ({'channel': 'nightly'}, {}, ['stable']):
            r = client.post('/api/v3/system/update-channel', json=body)
            assert r.status_code == 400
        assert 'channel' not in client.cm.config['auto_update']

    def test_switch_to_beta_and_back(self, client):
        r = client.post('/api/v3/system/update-channel', json={'channel': 'beta'})
        assert r.status_code == 200 and client.cm.config['auto_update']['channel'] == 'beta'
        assert r.get_json()['data']['channel'] == 'beta'

        client.repo.tag('v1.0.0')
        client.repo.publish('v = 2\n')
        git(client.repo.device, 'pull', '-q')
        client.repo.fetch()
        r = client.post('/api/v3/system/update-channel', json={'channel': 'stable'}).get_json()
        assert client.cm.config['auto_update']['channel'] == 'stable'
        assert r['data']['waiting'] is True
        assert 'newer than the newest release' in r['message'], \
            "switching to stable ahead of a release must say it keeps following main"

    def test_get_reports_the_plan(self, client):
        client.repo.publish('v = 2\n')
        client.repo.tag('v1.0.0')
        data = client.get('/api/v3/system/update-channel?fetch=1').get_json()['data']
        assert data['newest_release'] == 'v1.0.0' and data['action'] == uc.ACTION_CHECKOUT_TAG
        assert data['configured'] is None and data['channel'] == 'stable'

    def test_check_update_compares_release_tags_on_stable(self, client, monkeypatch):
        from web_interface.blueprints import api_v3 as pkg
        monkeypatch.setitem(pkg._update_check_cache, 'result', None)
        client.cm.config = dict(STABLE)
        client.repo.tag('v1.0.0')
        client.repo.publish('v = 2 (unreleased)\n')
        client.repo.fetch()
        git(client.repo.device, 'checkout', '-q', '--detach', 'v1.0.0')
        data = client.get('/api/v3/system/check-update').get_json()
        assert data['update_available'] is False, "main being ahead is not an update on stable"
        assert data['channel'] == 'stable' and data['current_release'] == 'v1.0.0'

        client.repo.publish('v = 3\n')
        client.repo.tag('v1.1.0')
        monkeypatch.setitem(pkg._update_check_cache, 'result', None)
        data = client.get('/api/v3/system/check-update').get_json()
        assert data['update_available'] is True and data['target_version'] == 'v1.1.0'
        assert data['commits_behind'] == 2

    def test_general_form_saves_the_channel(self, client, monkeypatch):
        r = client.post('/api/v3/config/main', json={'auto_update_channel': 'beta'})
        assert r.status_code == 200, r.get_json()
        assert client.cm.config['auto_update']['channel'] == 'beta'
        r = client.post('/api/v3/config/main', json={'auto_update_channel': 'weekly'})
        assert r.status_code == 400
        assert client.cm.config['auto_update']['channel'] == 'beta'
