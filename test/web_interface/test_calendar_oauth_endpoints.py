"""Tests the calendar plugin's OAuth and calendar-listing endpoints.

The plugin's config UI advertised a three-step setup, but only step 1 existed
on the server. Step 3's picker fetched /api/v3/plugins/calendar/list-calendars,
which was never registered, so Flask fell through to the global 404 handler and
the user saw "Resource not found" — with nothing to say which resource. Step 2
had no endpoint either, and no field in the schema at all, even though the
plugin ships calendar_registration.py written expressly for a web-driven
two-step flow.

These cover the two new routes: that they exist, that they fail with something
actionable rather than a bare 404, and that the shapes the widgets consume are
what the server actually sends.
"""

import json
import pickle
import sys
from pathlib import Path

import pytest

project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

from web_interface.blueprints import api_v3 as mod  # noqa: E402


@pytest.fixture
def client(monkeypatch, tmp_path):
    """A test client whose calendar plugin lives in tmp_path."""
    from flask import Flask

    plugin_dir = tmp_path / 'calendar'
    plugin_dir.mkdir()

    app = Flask(__name__)
    app.register_blueprint(mod.api_v3, url_prefix='/api/v3')
    app.config['TESTING'] = True
    monkeypatch.setattr(mod, '_calendar_plugin_dir', lambda: plugin_dir)
    with app.test_client() as c:
        c.plugin_dir = plugin_dir
        yield c


@pytest.fixture
def uninstalled(monkeypatch):
    from flask import Flask

    app = Flask(__name__)
    app.register_blueprint(mod.api_v3, url_prefix='/api/v3')
    app.config['TESTING'] = True
    monkeypatch.setattr(mod, '_calendar_plugin_dir', lambda: None)
    with app.test_client() as c:
        yield c


class TestTheRoutesExistAtAll:
    """The original bug: the URLs the widgets call were not registered."""

    def test_list_calendars_is_routed(self, client):
        response = client.get('/api/v3/plugins/calendar/list-calendars')
        # Reaching the handler is the whole point; what it then says about
        # missing setup is TestItSaysWhatIsWrong's business.
        assert response.status_code != 404, "still unrouted"
        assert response.get_json()['message'] != 'Resource not found'

    def test_authenticate_is_routed(self, client):
        response = client.post('/api/v3/plugins/calendar/authenticate', json={})
        assert response.status_code != 404, "still unrouted"
        assert response.get_json()['message'] != 'Resource not found'

    def test_both_urls_match_what_the_widgets_request(self):
        # The widgets hardcode these; a rename on either side reintroduces the
        # original bug silently.
        picker = Path(project_root) / 'web_interface/static/v3/js/widgets/google-calendar-picker.js'
        oauth = Path(project_root) / 'web_interface/static/v3/js/widgets/google-oauth.js'
        assert '/api/v3/plugins/calendar/list-calendars' in picker.read_text(encoding='utf-8')
        assert '/api/v3/plugins/calendar/authenticate' in oauth.read_text(encoding='utf-8')
        source = (Path(project_root) / 'web_interface/blueprints/api_v3.py').read_text(encoding='utf-8')
        assert "'/plugins/calendar/list-calendars'" in source
        assert "'/plugins/calendar/authenticate'" in source


class TestItSaysWhatIsWrong:
    def test_listing_without_a_token_asks_for_step_2(self, client):
        response = client.get('/api/v3/plugins/calendar/list-calendars')
        assert response.status_code == 400
        body = response.get_json()
        assert body['status'] == 'error'
        assert 'step 2' in body['message'].lower(), body['message']

    def test_authenticating_without_credentials_asks_for_step_1(self, client):
        response = client.post('/api/v3/plugins/calendar/authenticate', json={})
        assert response.status_code == 400
        assert 'step 1' in response.get_json()['message'].lower()

    def test_an_uninstalled_plugin_says_so(self, uninstalled):
        for response in (
            uninstalled.get('/api/v3/plugins/calendar/list-calendars'),
            uninstalled.post('/api/v3/plugins/calendar/authenticate', json={}),
        ):
            assert response.status_code == 404
            # A 404 here is honest -- but it must name the plugin, not read as
            # the generic "Resource not found" that started this.
            assert 'not installed' in response.get_json()['message'].lower()


class TestTheScriptRunner:
    def test_it_returns_the_json_the_script_prints(self, tmp_path):
        script = tmp_path / 'calendar_registration.py'
        script.write_text(
            'print(\'{"status": "success", "auth_url": "https://x"}\')\n',
            encoding='utf-8')
        payload, error = mod._run_calendar_registration(tmp_path, '')
        assert error is None
        assert payload['auth_url'] == 'https://x'

    def test_it_ignores_noise_before_the_json(self, tmp_path):
        # An import warning or a library writing to stdout would otherwise
        # make the last-line parse fail.
        script = tmp_path / 'calendar_registration.py'
        script.write_text(
            'print("some library warning")\n'
            'print(\'{"status": "success"}\')\n', encoding='utf-8')
        payload, error = mod._run_calendar_registration(tmp_path, '')
        assert error is None and payload['status'] == 'success'

    def test_it_passes_stdin_through(self, tmp_path):
        script = tmp_path / 'calendar_registration.py'
        script.write_text(
            'import sys, json\n'
            'print(json.dumps({"status": "success", "got": sys.stdin.read().strip()}))\n',
            encoding='utf-8')
        payload, _ = mod._run_calendar_registration(tmp_path, 'http://127.0.0.1/?code=abc')
        assert payload['got'] == 'http://127.0.0.1/?code=abc'

    def test_a_missing_script_is_reported(self, tmp_path):
        payload, error = mod._run_calendar_registration(tmp_path, '')
        assert payload is None
        assert 'script not found' in error.lower()

    def test_output_that_is_not_json_is_reported_with_context(self, tmp_path):
        script = tmp_path / 'calendar_registration.py'
        script.write_text('import sys\nsys.stderr.write("boom\\n")\n', encoding='utf-8')
        payload, error = mod._run_calendar_registration(tmp_path, '')
        assert payload is None
        assert 'no result' in error.lower()
        assert 'boom' in error


class TestListingShape:
    """The picker reads cal.id, cal.summary and cal.primary."""

    def _authenticate(self, client, monkeypatch, items):
        creds = type('C', (), {'expired': False, 'refresh_token': None, 'valid': True})()
        (client.plugin_dir / 'token.pickle').write_bytes(pickle.dumps({'x': 1}))
        monkeypatch.setattr(mod.pickle if hasattr(mod, 'pickle') else pickle,
                            'loads', lambda *a, **k: creds, raising=False)

        import types
        fake_pickle = types.SimpleNamespace(load=lambda f: creds, dump=lambda *a: None)
        fake_build = lambda *a, **k: types.SimpleNamespace(
            calendarList=lambda: types.SimpleNamespace(
                list=lambda: types.SimpleNamespace(
                    execute=lambda: {'items': items})))

        real_import = __builtins__['__import__'] if isinstance(__builtins__, dict) \
            else __builtins__.__import__

        def fake_import(name, *args, **kwargs):
            if name == 'pickle':
                return fake_pickle
            if name == 'google.auth.transport.requests':
                return types.SimpleNamespace(Request=object)
            if name == 'googleapiclient.discovery':
                return types.SimpleNamespace(build=fake_build)
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr('builtins.__import__', fake_import)

    def test_it_returns_id_summary_and_primary(self, client, monkeypatch):
        self._authenticate(client, monkeypatch, [
            {'id': 'b@x', 'summary': 'Work'},
            {'id': 'a@x', 'summary': 'Personal', 'primary': True},
        ])
        body = client.get('/api/v3/plugins/calendar/list-calendars').get_json()
        assert body['status'] == 'success'
        assert {c['id'] for c in body['calendars']} == {'a@x', 'b@x'}
        assert all(set(c) == {'id', 'summary', 'primary'} for c in body['calendars'])

    def test_the_primary_calendar_comes_first(self, client, monkeypatch):
        # Short list, but the one the user wants is almost always their own.
        self._authenticate(client, monkeypatch, [
            {'id': 'z@x', 'summary': 'Aardvarks'},
            {'id': 'a@x', 'summary': 'Zebras', 'primary': True},
        ])
        body = client.get('/api/v3/plugins/calendar/list-calendars').get_json()
        assert body['calendars'][0]['id'] == 'a@x'
        assert body['calendars'][0]['primary'] is True

    def test_a_calendar_without_a_name_still_lists(self, client, monkeypatch):
        self._authenticate(client, monkeypatch, [{'id': 'noname@x'}])
        body = client.get('/api/v3/plugins/calendar/list-calendars').get_json()
        assert body['calendars'][0]['summary'] == 'noname@x'

    def test_entries_without_an_id_are_dropped(self, client, monkeypatch):
        # Nothing could be selected by such a row, and the checkbox value
        # would be undefined.
        self._authenticate(client, monkeypatch, [{'summary': 'ghost'}, {'id': 'real@x'}])
        body = client.get('/api/v3/plugins/calendar/list-calendars').get_json()
        assert [c['id'] for c in body['calendars']] == ['real@x']
