"""The General tab's "Start Display on Boot" toggle reads systemd's state.

ledmatrix.service enabled -> checked, disabled -> unchecked, unreadable (no
systemctl, unit not installed) -> no toggle. The toggle itself posts the
existing enable_autostart / disable_autostart system actions, whose sudo
grants test_systemctl_sudoers_alignment.py already checks.
"""
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from test.test_web_settings_ui import client  # noqa: F401,E402
from web_interface.blueprints import pages_v3  # noqa: E402


REAL_RUN = subprocess.run
UNIT_QUERY = ['systemctl', 'is-enabled', 'ledmatrix.service']


def _fake_systemctl(monkeypatch, answer):
    """Answer the unit query with `answer` (stdout, or an exception to
    raise); every other command (the update channel's git) runs for real."""
    calls = []

    def run(cmd, *args, **kwargs):
        if cmd != UNIT_QUERY:
            return REAL_RUN(cmd, *args, **kwargs)
        calls.append(cmd)
        if isinstance(answer, Exception):
            raise answer
        return subprocess.CompletedProcess(cmd, 0, stdout=answer, stderr='')
    monkeypatch.setattr(pages_v3.subprocess, 'run', run)
    return calls


def _toggle(body):
    start = body.find('data-action="display-autostart"')
    return None if start < 0 else body[start:body.find('>', start)]


@pytest.mark.parametrize('stdout, checked', [('enabled\n', True), ('disabled\n', False)])
def test_toggle_reflects_the_unit(client, monkeypatch, stdout, checked):
    calls = _fake_systemctl(monkeypatch, stdout)
    tag = _toggle(client.get('/v3/partials/general').get_data(as_text=True))
    assert calls == [UNIT_QUERY]
    assert tag is not None
    assert ('checked' in tag) is checked


@pytest.mark.parametrize('outcome', [FileNotFoundError(), 'not-found\n'])
def test_toggle_hidden_when_state_unreadable(client, monkeypatch, outcome):
    _fake_systemctl(monkeypatch, outcome)
    body = client.get('/v3/partials/general').get_data(as_text=True)
    assert _toggle(body) is None
    assert 'name="plugins_directory"' in body  # rest of the tab still renders
