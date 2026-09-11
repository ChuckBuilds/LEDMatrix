"""A privileged system action that cannot run must say why.

The web interface runs unprivileged. Its systemctl/reboot/journalctl calls only
work once scripts/install/configure_web_sudo.sh has granted NOPASSWD, and
first_time_install.sh never invokes that script -- so on a fresh device every
one of those actions fails. What the user got back was:

    {"message": "Action failed; see logs for details", "status": "error"}

...and the log viewer was broken for exactly the same reason, so "see logs" led
nowhere. This is the closed loop that src/web_interface/error_handler.py's
describe_exception() was written to break; /system/action's exception handler
was simply still discarding the cause.

Observed live: POSTing reboot_system to a Pi returned that bare sentence, and
pinning down "no passwordless sudo" took reading the installer instead of
reading the response.
"""

import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from flask import Flask

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from web_interface.blueprints.api_v3 import api_v3  # noqa: E402


@pytest.fixture
def client():
    app = Flask(__name__)
    app.config['TESTING'] = True
    app.register_blueprint(api_v3, url_prefix='/api/v3')
    return app.test_client()


def _act(client, action='stop_display'):
    return client.post('/api/v3/system/action', json={'action': action})


def _result(args, rc=0, stderr=''):
    return subprocess.CompletedProcess(args, rc, stdout='', stderr=stderr)


class TestSudoRefusalIsNamed:
    @pytest.mark.parametrize("stderr", [
        "sudo: a password is required",
        "sudo: no tty present and no askpass program specified",
        "sudo: a terminal is required to read the password",
    ])
    def test_every_sudo_refusal_wording_is_recognised(self, client, stderr):
        with patch('subprocess.run', side_effect=lambda a, **k: _result(a, 1, stderr)):
            body = _act(client).get_json()

        assert body['status'] == 'error'
        # The point: the response tells the user what to do about it.
        assert 'configure_web_sudo.sh' in body['message']

    def test_the_raw_stderr_is_still_returned(self, client):
        with patch('subprocess.run',
                   side_effect=lambda a, **k: _result(a, 1, "sudo: a password is required")):
            body = _act(client).get_json()
        assert 'a password is required' in body['stderr']


class TestUnrelatedFailuresAreNotMisattributed:
    def test_a_real_systemctl_error_keeps_the_generic_message(self, client):
        with patch('subprocess.run',
                   side_effect=lambda a, **k: _result(a, 5, "Unit ledmatrix.service not found.")):
            body = _act(client).get_json()

        assert 'configure_web_sudo.sh' not in body['message'], \
            "a missing unit was blamed on sudo"
        assert 'Unit ledmatrix.service not found.' in body['stderr']
        assert body['returncode'] == 5


class TestTheExceptionPathNamesTheCause:
    def test_the_exception_type_and_message_are_returned(self, client):
        with patch('subprocess.run', side_effect=FileNotFoundError(2, "sudo")):
            body = _act(client).get_json()

        # The regression: 'details' was absent and the cause was discarded.
        assert 'FileNotFoundError' in body['details']

    def test_a_sudo_shaped_exception_also_gets_the_hint(self, client):
        with patch('subprocess.run',
                   side_effect=PermissionError("sudo: a password is required")):
            body = _act(client).get_json()
        assert 'configure_web_sudo.sh' in body['message']


class TestSuccessIsUnchanged:
    def test_a_working_action_still_reports_success(self, client):
        with patch('subprocess.run', side_effect=lambda a, **k: _result(a, 0)):
            body = _act(client).get_json()
        assert body['status'] == 'success'
        assert 'configure_web_sudo.sh' not in body.get('message', '')
