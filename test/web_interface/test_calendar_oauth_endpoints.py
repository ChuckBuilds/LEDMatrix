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

    def test_the_oauth_widget_is_dispatched_not_rendered_as_a_text_box(self):
        # The string branch of the config template dispatches on an allow-list
        # of widget names; anything missing from it silently falls through to a
        # plain <input type="text">. That produced two boxes on the calendar
        # page -- the widget's own, and a stray one for the same field -- and
        # no way to tell which to paste into.
        template = (Path(project_root)
                    / 'web_interface/templates/v3/partials/plugin_config.html'
                    ).read_text(encoding='utf-8')
        allow_list_line = [ln for ln in template.splitlines()
                           if "str_widget in [" in ln]
        assert allow_list_line, "the string widget allow-list moved"
        assert "'google-oauth'" in allow_list_line[0], allow_list_line[0]

    def test_the_widget_script_is_served(self):
        base = (Path(project_root) / 'web_interface/templates/v3/base.html'
                ).read_text(encoding='utf-8')
        assert 'widgets/google-oauth.js' in base

    def test_the_status_line_is_announced(self):
        # Every message the widget gives arrives after an async call, so a
        # screen reader hears nothing unless the element is a live region.
        widget = (Path(project_root)
                  / 'web_interface/static/v3/js/widgets/google-oauth.js'
                  ).read_text(encoding='utf-8')
        assert "role', 'status'" in widget or 'role="status"' in widget
        assert 'aria-live' in widget

    def test_the_paste_box_has_an_accessible_name(self):
        # A visible label is not enough on its own: without the association the
        # input's only name is a placeholder, which vanishes on focus -- which
        # is exactly when the value is being pasted.
        widget = (Path(project_root)
                  / 'web_interface/static/v3/js/widgets/google-oauth.js'
                  ).read_text(encoding='utf-8')
        assert "codeLabel.setAttribute('for'" in widget
        assert 'codeInput.id = ' in widget

    def test_the_failed_page_is_called_out_loudly(self):
        # The loopback redirect lands on a browser error page at exactly the
        # moment the user has to act. In small grey text it gets missed and the
        # flow reads as broken while it is working.
        widget = (Path(project_root)
                  / 'web_interface/static/v3/js/widgets/google-oauth.js'
                  ).read_text(encoding='utf-8')
        assert 'expected' in widget.lower()
        assert 'amber' in widget, "the warning is not visually distinguished"


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
        # Callers pass a flat list of calendars; the API returns them wrapped
        # in a page. One page is all these cases need -- TestPagination builds
        # its own multi-page sequences.
        pages = [{'items': items}]

        state = {'i': 0}

        def fake_list(**kwargs):
            page = pages[min(state['i'], len(pages) - 1)]
            state['i'] += 1
            return types.SimpleNamespace(execute=lambda: page)

        def fake_build(*args, **kwargs):
            return types.SimpleNamespace(
                calendarList=lambda: types.SimpleNamespace(list=fake_list))

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


class TestPagination:
    """calendarList.list pages at 250 and defaults to 100."""

    def _paged(self, client, monkeypatch, pages):
        import types
        creds = type('C', (), {'expired': False, 'refresh_token': None, 'valid': True})()
        (client.plugin_dir / 'token.pickle').write_bytes(b'x')
        state = {'i': 0}
        seen = []

        def fake_list(**kwargs):
            seen.append(kwargs)
            page = pages[min(state['i'], len(pages) - 1)]
            state['i'] += 1
            return types.SimpleNamespace(execute=lambda: page)

        def fake_build(*args, **kwargs):
            return types.SimpleNamespace(
                calendarList=lambda: types.SimpleNamespace(list=fake_list))

        real_import = __builtins__['__import__'] if isinstance(__builtins__, dict) \
            else __builtins__.__import__

        def fake_import(name, *args, **kwargs):
            if name == 'pickle':
                return types.SimpleNamespace(load=lambda f: creds, dump=lambda *a: None)
            if name == 'google.auth.transport.requests':
                return types.SimpleNamespace(Request=object)
            if name == 'googleapiclient.discovery':
                return types.SimpleNamespace(build=fake_build)
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr('builtins.__import__', fake_import)
        return seen

    def test_every_page_is_collected(self, client, monkeypatch):
        # Taking only the first page would hide calendars from the picker with
        # nothing to say the list was cut short.
        self._paged(client, monkeypatch, [
            {'items': [{'id': 'a@x', 'summary': 'A'}], 'nextPageToken': 't1'},
            {'items': [{'id': 'b@x', 'summary': 'B'}], 'nextPageToken': 't2'},
            {'items': [{'id': 'c@x', 'summary': 'C'}]},
        ])
        body = client.get('/api/v3/plugins/calendar/list-calendars').get_json()
        assert [c['id'] for c in body['calendars']] == ['a@x', 'b@x', 'c@x']

    def test_the_page_token_is_passed_back(self, client, monkeypatch):
        seen = self._paged(client, monkeypatch, [
            {'items': [{'id': 'a@x', 'summary': 'A'}], 'nextPageToken': 'tok'},
            {'items': [{'id': 'b@x', 'summary': 'B'}]},
        ])
        client.get('/api/v3/plugins/calendar/list-calendars')
        assert seen[0]['pageToken'] is None
        assert seen[1]['pageToken'] == 'tok'
        assert all(k['maxResults'] == 250 for k in seen)

    def test_a_looping_token_cannot_spin_forever(self, client, monkeypatch):
        # Every page claims another follows.
        self._paged(client, monkeypatch, [
            {'items': [{'id': 'a@x', 'summary': 'A'}], 'nextPageToken': 'same'},
        ])
        body = client.get('/api/v3/plugins/calendar/list-calendars').get_json()
        assert body['status'] == 'success'
        assert len(body['calendars']) <= mod._CALENDAR_LIST_MAX_PAGES


class TestDiagnosticsAreRedacted:
    def test_script_stderr_is_redacted_on_the_way_out(self, tmp_path):
        script = tmp_path / 'calendar_registration.py'
        script.write_text(
            'import sys\n'
            'sys.stderr.write("boom client_secret=hunter2 more\\n")\n',
            encoding='utf-8')
        payload, error = mod._run_calendar_registration(tmp_path, '')
        assert payload is None
        assert 'hunter2' not in error, error
        assert '<redacted>' in error, error

    def test_a_failing_script_payload_is_redacted(self, client):
        (client.plugin_dir / 'credentials.json').write_text('{}', encoding='utf-8')
        (client.plugin_dir / 'calendar_registration.py').write_text(
            'import json\n'
            'print(json.dumps({"status": "error", '
            '"message": "Failed: client_secret=topsecret"}))\n',
            encoding='utf-8')
        body = client.post('/api/v3/plugins/calendar/authenticate',
                           json={}).get_json()
        assert body['status'] == 'error'
        assert 'topsecret' not in json.dumps(body), body
        assert '<redacted>' in body['message'], body

    def test_an_unrunnable_script_is_reported_without_raw_exception_text(self,
                                                                        tmp_path,
                                                                        monkeypatch):
        # OSError from the spawn carries the interpreter path and whatever the
        # OS chose to say; it reaches the client through the redactor like
        # everything else.
        script = tmp_path / 'calendar_registration.py'
        script.write_text('', encoding='utf-8')

        def boom(*a, **k):
            raise OSError("Exec format error: token=abcd1234 /usr/bin/python3")

        monkeypatch.setattr(mod.subprocess, 'run', boom)
        payload, error = mod._run_calendar_registration(tmp_path, '')
        assert payload is None
        assert 'abcd1234' not in error, error
        assert 'OSError' in error, error

    def test_a_missing_google_library_is_reported_without_raw_exception_text(
            self, client, monkeypatch):
        (client.plugin_dir / 'token.pickle').write_bytes(b'x')
        real_import = __builtins__['__import__'] if isinstance(__builtins__, dict) \
            else __builtins__.__import__

        def fake_import(name, *args, **kwargs):
            if name.startswith('google'):
                raise ImportError("No module named 'google' password=hunter2")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr('builtins.__import__', fake_import)
        body = client.get('/api/v3/plugins/calendar/list-calendars').get_json()
        assert 'hunter2' not in json.dumps(body), body
        assert 'requirements.txt' in body['message']
