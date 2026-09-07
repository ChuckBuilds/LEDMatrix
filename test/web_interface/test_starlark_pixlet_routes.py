"""The Starlark routes the frontend calls must exist.

`plugins_manager.js` posts to /api/v3/starlark/install-pixlet and then reloads
/api/v3/starlark/status. Neither route existed: #330 rewrote api_v3.py and
dropped all thirteen Starlark routes that #253 had added, so both calls fell
through to Flask's 404 handler, which answers

    {"status": "error", "message": "Resource not found"}

and the button reported "Pixlet install failed: Resource not found" -- a
message that names neither the resource nor the cause.

These assert the routes are registered and answer in the shape the frontend
reads, so a future rewrite of this file cannot silently drop them again.
"""

import json
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def client():
    from web_interface.app import app
    app.config['TESTING'] = True
    with app.test_client() as c:
        yield c


class TestRoutesAreRegistered:
    """The failure was a missing route, so check the URL map directly."""

    @pytest.mark.parametrize("rule,method", [
        ("/api/v3/starlark/install-pixlet", "POST"),
        ("/api/v3/starlark/status", "GET"),
    ])
    def test_route_exists(self, rule, method):
        from web_interface.app import app
        matches = [r for r in app.url_map.iter_rules()
                   if r.rule == rule and method in r.methods]
        assert matches, (
            f"{method} {rule} is not registered; the frontend calls it and "
            "would get Flask's generic 'Resource not found'")


class TestInstallPixlet:
    def test_it_does_not_404(self, client):
        with patch('web_interface.blueprints.api_v3.subprocess.run') as run:
            run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")
            resp = client.post('/api/v3/starlark/install-pixlet')
        assert resp.status_code != 404, "the route is still missing"
        assert resp.get_json().get('message') != 'Resource not found'

    def test_success_is_reported_in_the_shape_the_button_reads(self, client):
        with patch('web_interface.blueprints.api_v3.subprocess.run') as run:
            run.return_value = MagicMock(returncode=0, stdout="done", stderr="")
            resp = client.post('/api/v3/starlark/install-pixlet')
        body = resp.get_json()
        assert body['status'] == 'success', body
        assert 'message' in body, "the JS shows data.message on success"

    def test_a_failed_download_says_why(self, client):
        with patch('web_interface.blueprints.api_v3.subprocess.run') as run:
            run.return_value = MagicMock(returncode=1, stdout="", stderr="no such release")
            resp = client.post('/api/v3/starlark/install-pixlet')
        body = resp.get_json()
        assert body['status'] == 'error'
        assert 'no such release' in body['message'], \
            "the installer's own stderr is what tells the user what went wrong"

    def test_a_timeout_is_reported_rather_than_hanging(self, client):
        import subprocess as sp
        with patch('web_interface.blueprints.api_v3.subprocess.run',
                   side_effect=sp.TimeoutExpired(cmd='x', timeout=300)):
            resp = client.post('/api/v3/starlark/install-pixlet')
        assert resp.get_json()['status'] == 'error'
        assert 'timed out' in resp.get_json()['message'].lower()


class TestStarlarkStatus:
    def test_it_does_not_404(self, client):
        resp = client.get('/api/v3/starlark/status')
        assert resp.status_code != 404, "the route is still missing"
        assert resp.get_json().get('message') != 'Resource not found'

    def test_it_reports_pixlet_availability_without_the_plugin_loaded(self, client):
        # The status call runs before install too -- it must answer even when
        # starlark-apps is not loaded, which is the state a user is in when
        # they press the install button for the first time.
        with patch('web_interface.blueprints.api_v3._get_starlark_plugin', return_value=None):
            resp = client.get('/api/v3/starlark/status')
        body = resp.get_json()
        assert body['status'] == 'success', body
        assert 'pixlet_available' in body
        assert body['plugin_loaded'] is False


class TestTheInstallerScriptIsActuallyThere:
    def test_download_pixlet_script_exists_and_is_executable(self):
        # install_pixlet chmods and runs this; a missing file is the one error
        # it reports as a 404 of its own, which would look identical to the
        # bug being fixed here.
        import os
        from pathlib import Path
        from web_interface.blueprints.api_v3 import PROJECT_ROOT
        script = Path(PROJECT_ROOT) / 'scripts' / 'download_pixlet.sh'
        assert script.is_file(), f"{script} is missing; install_pixlet would 404"
        assert os.access(script, os.R_OK)
