"""A pull that changed nothing on the running system is not an applied update.

git_pull replaces files on disk and restarts nothing -- there is no systemctl
call anywhere in the handler. The display and web services keep running the
code they loaded at boot, so the user is told "Code updated successfully" and
sees no change until they happen to reboot. The response now says whether a
restart is owed, and the UI raises the existing restart-pending banner.
"""
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from flask import Flask

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from web_interface.blueprints import api_v3 as mod  # noqa: E402
from web_interface.blueprints.api_v3 import api_v3  # noqa: E402


@pytest.fixture
def client():
    app = Flask(__name__)
    app.config['TESTING'] = True
    app.register_blueprint(api_v3, url_prefix='/api/v3')
    # The handler consults these after a successful pull; None is the
    # "not wired up" case it already guards for.
    api_v3.plugin_store_manager = None
    api_v3.config_manager = None
    return app.test_client()


def _git(heads, pull_rc=0, pull_out='Updating a1b2c3..d4e5f6\n'):
    """Fake git. `heads` are the successive answers to rev-parse HEAD."""
    seq = list(heads)

    def run(args, **kwargs):
        def ok(stdout='', rc=0, b=False):
            return subprocess.CompletedProcess(
                args, rc, stdout=(stdout.encode() if b else stdout),
                stderr=(b'' if b else ''))
        if args[:2] == ['git', 'rev-parse'] and args[-1] == 'HEAD':
            return ok(seq.pop(0) + '\n' if seq else 'deadbeef\n')
        if 'symbolic-full-name' in args or '@{u}' in args:
            return ok('origin/main\n')
        if args[:2] == ['git', 'status']:
            return ok('')
        if args[:2] == ['git', 'diff']:
            return ok('')
        if args[:2] == ['git', 'pull']:
            return ok(pull_out, pull_rc)
        return ok('')
    return run


def _pull(client):
    return client.post('/api/v3/system/action',
                       json={'action': 'git_pull'}).get_json()


class TestRestartIsRequestedWhenCodeChanged:
    def test_a_pull_that_moved_head_asks_for_a_restart(self, client):
        with patch.object(mod.subprocess, 'run', _git(['aaa111', 'bbb222'])):
            data = _pull(client)
        assert data['status'] == 'success'
        assert data['restart_required'] is True, (
            "new code on disk, services still running the old code, and "
            "nothing told the user to restart")

    def test_already_up_to_date_does_not(self, client):
        with patch.object(mod.subprocess, 'run',
                          _git(['aaa111', 'aaa111'], pull_out='Already up to date.\n')):
            data = _pull(client)
        assert data['status'] == 'success'
        assert data['restart_required'] is False, (
            "prompting after a no-op update trains users to ignore the prompt")

    def test_a_failed_pull_does_not(self, client):
        with patch.object(mod.subprocess, 'run', _git(['aaa111'], pull_rc=1)):
            data = _pull(client)
        assert data['status'] == 'error'
        assert data['restart_required'] is False
