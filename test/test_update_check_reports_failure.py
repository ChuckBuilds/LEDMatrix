"""A check that could not run must not be reported as "up to date".

check-update returned update_available=False whenever git failed. The banner
is the only route to the update button, so a checkout git refuses to touch
looked exactly like a current one -- permanently, and with nothing for the
user to act on. The usual cause is an install performed as root, after which
every git command fails with "detected dubious ownership".
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

DUBIOUS = ("fatal: detected dubious ownership in repository at "
           "'/home/pi/LEDMatrix'\nTo add an exception for this directory, call:\n"
           "\tgit config --global --add safe.directory /home/pi/LEDMatrix\n")


@pytest.fixture
def client():
    app = Flask(__name__)
    app.config['TESTING'] = True
    app.register_blueprint(api_v3, url_prefix='/api/v3')
    mod._update_check_cache['result'] = None
    mod._update_check_cache['ts'] = 0
    return app.test_client()


def _fetch_fails(stderr: bytes):
    def fake_run(args, **kwargs):
        if args[:2] == ['git', 'fetch']:
            return subprocess.CompletedProcess(args, 1, stdout=b'', stderr=stderr)
        return subprocess.CompletedProcess(args, 0, stdout='', stderr='')
    return fake_run


class TestFailedCheckIsNotSilence:
    def test_dubious_ownership_is_reported_not_swallowed(self, client):
        with patch.object(mod.subprocess, 'run', _fetch_fails(DUBIOUS.encode())):
            data = client.get('/api/v3/system/check-update').get_json()
        assert data['check_failed'] is True, (
            "a git failure was reported as a successful 'no update' check")
        assert data['update_available'] is False

    def test_the_message_tells_the_user_what_to_do(self, client):
        with patch.object(mod.subprocess, 'run', _fetch_fails(DUBIOUS.encode())):
            data = client.get('/api/v3/system/check-update').get_json()
        assert 'chown' in data['error'], (
            "dubious ownership is unactionable without the fix command")
        assert 'root' in data['error']

    def test_an_ordinary_git_failure_still_surfaces(self, client):
        with patch.object(mod.subprocess, 'run',
                          _fetch_fails(b'fatal: some other git problem\n')):
            data = client.get('/api/v3/system/check-update').get_json()
        assert data['check_failed'] is True
        assert 'some other git problem' in data['error']

    def test_offline_reads_as_offline(self, client):
        with patch.object(mod.subprocess, 'run',
                          _fetch_fails(b'fatal: could not resolve host: github.com\n')):
            data = client.get('/api/v3/system/check-update').get_json()
        assert 'Could not reach GitHub' in data['error']


class TestSuccessPathUnchanged:
    def test_up_to_date_carries_no_failure_flag(self, client):
        def fake_run(args, **kwargs):
            if args[:2] == ['git', 'fetch']:
                return subprocess.CompletedProcess(args, 0, stdout=b'', stderr=b'')
            if args[:2] == ['git', 'rev-parse']:
                return subprocess.CompletedProcess(args, 0, stdout='abc123\n', stderr='')
            return subprocess.CompletedProcess(args, 0, stdout='0\n', stderr='')
        with patch.object(mod.subprocess, 'run', fake_run):
            data = client.get('/api/v3/system/check-update').get_json()
        assert data['update_available'] is False
        assert not data.get('check_failed'), "a healthy check must not look like a failure"
