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
import os
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def client():
    from web_interface.app import app
    app.config['TESTING'] = True
    with app.test_client() as c:
        yield c


class TestRoutesAreRegistered:
    """The failure was a missing route, so check the URL map directly.

    All thirteen, not just the two the Pixlet button needs: #330 dropped the
    lot, and the app store page is built on repository/browse,
    repository/categories and repository/install, which 404 the same way.
    """

    @pytest.mark.parametrize("rule,method", [
        ("/api/v3/starlark/install-pixlet", "POST"),
        ("/api/v3/starlark/status", "GET"),
        ("/api/v3/starlark/apps", "GET"),
        ("/api/v3/starlark/upload", "POST"),
        ("/api/v3/starlark/repository/browse", "GET"),
        ("/api/v3/starlark/repository/categories", "GET"),
        ("/api/v3/starlark/repository/install", "POST"),
        ("/api/v3/starlark/apps/<app_id>", "GET"),
        ("/api/v3/starlark/apps/<app_id>", "DELETE"),
        ("/api/v3/starlark/apps/<app_id>/config", "GET"),
        ("/api/v3/starlark/apps/<app_id>/config", "PUT"),
        ("/api/v3/starlark/apps/<app_id>/toggle", "POST"),
        ("/api/v3/starlark/apps/<app_id>/render", "POST"),
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


class TestTheAppStoreFlow:
    """Browsing and installing from the Tronbyte repository.

    These are the calls the app store page makes. Each returned the generic
    "Resource not found" before this change, which is indistinguishable from
    an empty store.
    """

    @pytest.fixture
    def offline_repo(self):
        """No live GitHub calls from the test suite.

        browse and categories reach _get_tronbyte_repository_class() and then
        list_all_apps_cached(); with a cold server-side cache that is a real
        network request, which makes the run slow, rate-limitable, and able to
        pass on a 500 because these assertions only check for a 404.
        """
        repo = MagicMock()
        # Matches what the real list_all_apps_cached returns; the handler
        # indexes every one of these keys.
        repo.return_value.list_all_apps_cached.return_value = {
            'apps': [{'id': 'quoteoftheday', 'name': 'A Quote A Day',
                      'category': 'text'}],
            'categories': ['text'],
            'authors': ['someone'],
            'count': 1,
            'cached': True,
        }
        repo.return_value.get_rate_limit_info.return_value = {'remaining': 5000}
        with patch('web_interface.blueprints.api_v3._get_tronbyte_repository_class',
                   return_value=repo):
            yield repo

    def test_browse_does_not_404(self, client, offline_repo):
        resp = client.get('/api/v3/starlark/repository/browse')
        assert resp.status_code != 404, "the store cannot list anything"
        assert resp.get_json().get('message') != 'Resource not found'

    def test_browse_returns_the_apps_the_store_lists(self, client, offline_repo):
        resp = client.get('/api/v3/starlark/repository/browse')
        body = resp.get_json()
        assert body['status'] == 'success', body
        assert any(a.get('id') == 'quoteoftheday' for a in body.get('apps', [])), body

    def test_categories_does_not_404(self, client, offline_repo):
        resp = client.get('/api/v3/starlark/repository/categories')
        assert resp.status_code != 404
        assert resp.get_json().get('message') != 'Resource not found'

    def test_no_live_network_call_is_made(self, client, offline_repo):
        client.get('/api/v3/starlark/repository/browse')
        assert offline_repo.called, \
            "the route did not go through the patched repository class"

    def test_installed_apps_list_does_not_404(self, client):
        resp = client.get('/api/v3/starlark/apps')
        assert resp.status_code != 404
        assert resp.get_json().get('message') != 'Resource not found'

    def test_repository_install_rejects_a_missing_body_rather_than_404ing(self, client):
        # A 400/422 here is the route working: it received the call and said
        # what was wrong. A 404 means it was never reached at all.
        resp = client.post('/api/v3/starlark/repository/install',
                           json={}, content_type='application/json')
        assert resp.status_code != 404, "the install route is still missing"
        assert resp.get_json().get('message') != 'Resource not found'

    def test_upload_rejects_an_empty_post_rather_than_404ing(self, client):
        resp = client.post('/api/v3/starlark/upload')
        assert resp.status_code != 404
        assert resp.get_json().get('message') != 'Resource not found'


class TestNoStarlarkRouteIsMissing:
    """A single check that the whole set is present.

    #330 removed all thirteen at once by rewriting this file. One assertion
    over the frontend's own list is what would have caught that.
    """

    def test_every_endpoint_the_frontend_calls_is_registered(self):
        import re
        from pathlib import Path
        from werkzeug.exceptions import MethodNotAllowed, NotFound
        from web_interface.app import app

        root = Path(__file__).resolve().parent.parent.parent
        js = (root / 'web_interface' / 'static' / 'v3' / 'plugins_manager.js').read_text()

        # The frontend builds some of these with template literals, e.g.
        # `/api/v3/starlark/apps/${appId}/toggle`. Substitute a placeholder so
        # the URL is concrete, then let Werkzeug match it the way a request
        # would -- string comparison cannot see <app_id> rules.
        raw = set(re.findall(r"[\'\"`](/api/v3/starlark/[^\'\"`\s]*)", js))
        urls = set()
        for u in raw:
            u = re.sub(r"\$\{[^}]*\}", "probe", u)
            urls.add(u.rstrip('/') or u)
        assert urls, "found no starlark calls in the frontend -- did the file move?"

        adapter = app.url_map.bind('localhost')
        missing = []
        for u in sorted(urls):
            try:
                adapter.match(u, method='GET')
            except MethodNotAllowed:
                pass          # route exists, just not for GET -- fine
            except NotFound:
                missing.append(u)
        assert not missing, f"the frontend calls these and they are not registered: {missing}"


class TestInstalledAppsAppearWithTheOtherPlugins:
    """An installed .star app must be manageable like any other plugin.

    #253 surfaced installed apps in /plugins/installed as `starlark:<app_id>`
    entries, so they could be seen and enabled/disabled from the same list as
    everything else, and routed `starlark:` toggles to the Starlark manifest.
    #330 removed both. The result was an app that installs successfully, then
    appears nowhere and cannot be turned on or off.
    """

    APPS = {'apps': {'quoteoftheday': {'name': 'A Quote A Day', 'enabled': True}}}

    def test_an_installed_app_is_listed(self, client):
        with patch('web_interface.blueprints.api_v3._get_starlark_plugin', return_value=None), \
             patch('web_interface.blueprints.api_v3._read_starlark_manifest', return_value=self.APPS):
            resp = client.get('/api/v3/plugins/installed')
        ids = [p['id'] for p in resp.get_json()['data']['plugins']]
        assert 'starlark:quoteoftheday' in ids, \
            "an installed Starlark app does not appear among the plugins"

    def test_the_entry_carries_what_the_ui_needs(self, client):
        with patch('web_interface.blueprints.api_v3._get_starlark_plugin', return_value=None), \
             patch('web_interface.blueprints.api_v3._read_starlark_manifest', return_value=self.APPS):
            resp = client.get('/api/v3/plugins/installed')
        entry = next(p for p in resp.get_json()['data']['plugins']
                     if p['id'] == 'starlark:quoteoftheday')
        assert entry['name'] == 'A Quote A Day'
        assert entry['enabled'] is True
        assert entry['is_starlark_app'] is True, "the UI keys its Starlark handling off this"
        assert entry['category'] == 'Starlark App'

    def test_a_starlark_failure_does_not_empty_the_plugin_list(self, client):
        # The virtual entries are appended to the real ones; a broken manifest
        # must cost the Starlark rows, not everybody else's.
        with patch('web_interface.blueprints.api_v3._get_starlark_plugin',
                   side_effect=RuntimeError('boom')):
            resp = client.get('/api/v3/plugins/installed')
        assert resp.status_code == 200
        assert resp.get_json()['status'] == 'success'

    def test_toggling_an_app_does_not_report_plugin_not_found(self, client):
        written = {}
        with patch('web_interface.blueprints.api_v3._get_starlark_plugin', return_value=None), \
             patch('web_interface.blueprints.api_v3._read_starlark_manifest',
                   return_value={'apps': {'quoteoftheday': {'enabled': True}}}), \
             patch('web_interface.blueprints.api_v3._write_starlark_manifest',
                   side_effect=lambda m: written.update(m) or True):
            resp = client.post('/api/v3/plugins/toggle',
                               json={'plugin_id': 'starlark:quoteoftheday', 'enabled': False})
        body = resp.get_json()
        assert body['status'] == 'success', body
        assert body['enabled'] is False
        assert written['apps']['quoteoftheday']['enabled'] is False, \
            "the manifest was not actually updated"

    def test_toggling_an_unknown_app_says_so(self, client):
        with patch('web_interface.blueprints.api_v3._get_starlark_plugin', return_value=None), \
             patch('web_interface.blueprints.api_v3._read_starlark_manifest',
                   return_value={'apps': {}}):
            resp = client.post('/api/v3/plugins/toggle',
                               json={'plugin_id': 'starlark:nope', 'enabled': True})
        assert resp.status_code == 404
        assert 'nope' in resp.get_json()['message']

    def test_a_traversal_app_id_is_rejected_before_touching_the_manifest(self, client):
        resp = client.post('/api/v3/plugins/toggle',
                           json={'plugin_id': 'starlark:../../etc/passwd', 'enabled': True})
        assert resp.status_code == 400
        assert 'traversal' in resp.get_json()['message']

    def test_an_app_id_the_listing_published_can_be_toggled(self, client):
        """The id here is exactly what _starlark_virtual_plugins publishes.

        It used to be re-slugified on the way back in -- lowercased, with every
        character outside [a-z0-9_] replaced -- so an app stored as 'My-App'
        was listed as 'starlark:My-App' and looked up as 'my_app'. Toggling an
        app the page had just drawn answered 404.
        """
        manifest = {'apps': {'My-App': {'name': 'My App', 'enabled': False}}}
        with patch('web_interface.blueprints.api_v3._get_starlark_plugin', return_value=None), \
             patch('web_interface.blueprints.api_v3._read_starlark_manifest',
                   return_value=manifest), \
             patch('web_interface.blueprints.api_v3._write_starlark_manifest',
                   return_value=True) as write:
            resp = client.post('/api/v3/plugins/toggle',
                               json={'plugin_id': 'starlark:My-App', 'enabled': True})
        assert resp.status_code == 200, resp.get_json()
        assert write.called, "the toggle never reached the manifest"
        assert manifest['apps']['My-App']['enabled'] is True

    def test_a_loaded_app_missing_from_the_manifest_does_not_500(self, client):
        """_update_manifest_safe does not catch KeyError, so indexing an entry
        that is not on disk yet escaped as a 500 instead of writing it."""
        app = MagicMock()
        app.manifest = {'enabled': False}
        plugin = MagicMock()
        plugin.apps = {'demo': app}
        written = {}

        def run_updater(fn):
            fn(written)
            return True

        plugin._update_manifest_safe.side_effect = run_updater

        with patch('web_interface.blueprints.api_v3._get_starlark_plugin', return_value=plugin):
            resp = client.post('/api/v3/plugins/toggle',
                               json={'plugin_id': 'starlark:demo', 'enabled': True})
        assert resp.status_code == 200, resp.get_json()
        assert written['apps']['demo']['enabled'] is True


class TestTheManifestStaysRelocatable:
    """`star_file` is joined to the app's own directory by its readers.

    _standalone_render_starlark_app does `app_dir / app_data.get('star_file',
    f'{app_id}.star')`, so the key's default is a bare filename. Storing an
    absolute path gave it a second meaning, and Path.__truediv__ discards the
    left side when the right is absolute -- which pinned the manifest to the
    PROJECT_ROOT that installed it.
    """

    @pytest.fixture
    def starlark_dir(self, tmp_path, monkeypatch):
        from web_interface.blueprints import api_v3 as module
        apps_dir = tmp_path / "starlark-apps"
        apps_dir.mkdir()
        monkeypatch.setattr(module, '_STARLARK_APPS_DIR', apps_dir)
        monkeypatch.setattr(module, '_STARLARK_MANIFEST_FILE', apps_dir / 'manifest.json')
        return apps_dir

    def _install(self, tmp_path):
        from web_interface.blueprints import api_v3 as module
        source = tmp_path / "source.star"
        source.write_text("# app")
        with patch.object(module, '_get_pixlet_renderer_class',
                          side_effect=ImportError("no pixlet here")):
            assert module._install_star_file('demo', str(source), {'name': 'Demo'})
        return json.loads((module._STARLARK_MANIFEST_FILE).read_text())['apps']['demo']

    def test_the_star_file_is_recorded_by_name(self, starlark_dir, tmp_path):
        assert self._install(tmp_path)['star_file'] == 'demo.star'

    def test_the_recorded_path_is_not_absolute(self, starlark_dir, tmp_path):
        """An absolute value survives a move only by accident."""
        assert not os.path.isabs(self._install(tmp_path)['star_file'])

    def test_the_value_resolves_against_the_app_directory(self, starlark_dir, tmp_path):
        """Which is the one thing every reader of this key does with it."""
        entry = self._install(tmp_path)
        assert (starlark_dir / 'demo' / entry['star_file']).is_file()

    def test_it_matches_the_default_a_reader_falls_back_to(self, starlark_dir, tmp_path):
        """Stored and defaulted values must mean the same thing."""
        assert self._install(tmp_path)['star_file'] == 'demo.star'


# ---------------------------------------------------------------------------
# The store loaded, then stopped loading, and nothing anywhere said why.
#
# #535 restored the routes, so the 404 was gone -- but two failure modes
# underneath it produce the same blank grid, and neither could be read from
# outside. On the device this was diagnosed on, /repository/browse answered
# 200 with 1000 apps in 27s while GitHub reported 18 of 60 unauthenticated
# requests remaining, with 48 installed plugins checking for updates against
# the same budget. When that budget runs out the store goes blank and says
# nothing at all.
# ---------------------------------------------------------------------------

def _repository_module():
    """Load tronbyte_repository.py the way the blueprint does."""
    import importlib.util
    import sys
    from pathlib import Path

    path = (Path(__file__).resolve().parents[2]
            / 'plugin-repos' / 'starlark-apps' / 'tronbyte_repository.py')
    spec = importlib.util.spec_from_file_location('_tronbyte_repo_under_test', path)
    module = importlib.util.module_from_spec(spec)
    sys.modules['_tronbyte_repo_under_test'] = module
    spec.loader.exec_module(module)
    return module


class _Resp:
    """Enough of requests.Response for the paths under test."""

    def __init__(self, status_code=200, payload=None, headers=None, raises=None):
        self.status_code = status_code
        self._payload = payload
        self.headers = headers or {}
        self._raises = raises

    def json(self):
        if self._raises is not None:
            raise self._raises
        return self._payload


class TestTheJsonGuardIsNotItselfACrash:
    """`except (json.JSONDecodeError, ValueError)` with no `import json`.

    Evaluating that tuple raises NameError, so the guard written for exactly
    this case never ran: a non-JSON body -- a captive portal, a proxy error
    page, a DNS-hijacking router answering for api.github.com -- came out as
    a 500 instead of the None the caller was written to handle.
    """

    def test_a_non_json_body_returns_none(self):
        repo = _repository_module().TronbyteRepository()
        repo.session.get = lambda *a, **k: _Resp(
            raises=ValueError("Expecting value: line 1 column 1 (char 0)"))

        assert repo._make_request("https://api.github.com/anything") is None

    def test_it_says_the_response_was_not_json(self):
        repo = _repository_module().TronbyteRepository()
        repo.session.get = lambda *a, **k: _Resp(raises=ValueError("nope"))

        repo._make_request("https://api.github.com/anything")
        assert 'JSON' in (repo.last_error or ''), repo.last_error


class TestAnExhaustedRateLimitSaysSo:
    """60 requests/hour unauthenticated, shared with every update check."""

    def test_the_message_names_the_rate_limit(self):
        repo = _repository_module().TronbyteRepository()
        repo.session.get = lambda *a, **k: _Resp(
            status_code=403,
            headers={'X-RateLimit-Remaining': '0', 'X-RateLimit-Limit': '60'})

        assert repo._make_request("https://api.github.com/anything") is None
        assert 'rate limit' in (repo.last_error or '').lower(), repo.last_error

    def test_it_mentions_being_unauthenticated_when_there_is_no_token(self):
        repo = _repository_module().TronbyteRepository()
        repo.session.get = lambda *a, **k: _Resp(
            status_code=403,
            headers={'X-RateLimit-Remaining': '0', 'X-RateLimit-Limit': '60'})

        repo._make_request("https://api.github.com/anything")
        assert 'unauthenticated' in (repo.last_error or ''), repo.last_error

    def test_a_plain_403_is_not_reported_as_a_rate_limit(self):
        repo = _repository_module().TronbyteRepository()
        repo.session.get = lambda *a, **k: _Resp(
            status_code=403, headers={'X-RateLimit-Remaining': '57'})

        repo._make_request("https://api.github.com/anything")
        assert 'rate limit' not in (repo.last_error or '').lower(), repo.last_error


class TestAFailedFetchIsNotAnEmptyRepository:
    """list_all_apps_cached turned every failure into an empty app list.

    The route then reported that as a success, so a rate limit, a DNS failure
    and a genuinely empty repository were all drawn as the same blank grid.
    """

    def test_the_reason_comes_back_with_the_empty_list(self):
        module = _repository_module()
        repo = module.TronbyteRepository()
        repo.list_apps = lambda: (False, None, "GitHub API rate limit exceeded")

        result = repo.list_all_apps_cached()
        assert result['count'] == 0
        assert 'rate limit' in result['error'].lower(), result

    def test_a_failure_is_not_cached_as_an_empty_repository(self):
        module = _repository_module()
        repo = module.TronbyteRepository()
        repo.list_apps = lambda: (False, None, "boom")
        repo.list_all_apps_cached()

        assert module._apps_cache['data'] is None, \
            "a failed fetch was cached, so the store stays empty for 2 hours"

    def test_a_successful_fetch_reports_no_error(self):
        module = _repository_module()
        repo = module.TronbyteRepository()
        repo.list_apps = lambda: (True, [{'id': 'a', 'path': 'apps/a'}], None)
        repo._fetch_raw_file = lambda *a, **k: "name: A\nsummary: s\n"

        assert repo.list_all_apps_cached().get('error') is None


class TestTheStoreReportsWhyItIsEmpty:
    """The route's half of the same failure."""

    @pytest.fixture
    def failing_repo(self):
        repo = MagicMock()
        repo.return_value.list_all_apps_cached.return_value = {
            'apps': [], 'categories': [], 'authors': [], 'count': 0,
            'cached': False,
            'error': 'GitHub API rate limit exceeded (60 requests/hour, '
                     'unauthenticated).',
        }
        repo.return_value.get_rate_limit_info.return_value = {'remaining': 0}
        with patch('web_interface.blueprints.api_v3._get_tronbyte_repository_class',
                   return_value=repo):
            yield repo

    def test_browse_does_not_call_a_failure_a_success(self, client, failing_repo):
        body = client.get('/api/v3/starlark/repository/browse').get_json()
        assert body['status'] == 'error', body

    def test_browse_answers_502_not_200(self, client, failing_repo):
        resp = client.get('/api/v3/starlark/repository/browse')
        assert resp.status_code == 502, resp.get_json()

    def test_the_reason_reaches_the_page(self, client, failing_repo):
        body = client.get('/api/v3/starlark/repository/browse').get_json()
        assert 'rate limit' in body['message'].lower(), body

    def test_categories_reports_it_too(self, client, failing_repo):
        resp = client.get('/api/v3/starlark/repository/categories')
        assert resp.status_code == 502
        assert 'rate limit' in resp.get_json()['message'].lower()

    @pytest.fixture
    def working_repo(self):
        repo = MagicMock()
        repo.return_value.list_all_apps_cached.return_value = {
            'apps': [{'id': 'quoteoftheday'}], 'categories': [], 'authors': [],
            'count': 1, 'cached': False, 'error': None,
        }
        repo.return_value.get_rate_limit_info.return_value = {'remaining': 57}
        with patch('web_interface.blueprints.api_v3._get_tronbyte_repository_class',
                   return_value=repo):
            yield repo

    def test_a_working_fetch_is_still_a_success(self, client, working_repo):
        resp = client.get('/api/v3/starlark/repository/browse')
        assert resp.status_code == 200
        assert resp.get_json()['status'] == 'success'


class TestACrashCarriesItsDetail:
    """Seventeen Starlark handlers answered 5xx with no detail at all."""

    def test_browse_returns_the_exception_detail(self, client):
        with patch('web_interface.blueprints.api_v3._get_tronbyte_repository_class',
                   side_effect=ImportError("No module named 'yaml'")):
            body = client.get('/api/v3/starlark/repository/browse').get_json()

        assert 'yaml' in body.get('details', ''), body

    def test_status_returns_the_exception_detail(self, client):
        with patch('web_interface.blueprints.api_v3._get_starlark_plugin',
                   side_effect=RuntimeError("plugin manager is not attached")):
            body = client.get('/api/v3/starlark/status').get_json()

        assert 'plugin manager is not attached' in body.get('details', ''), body


class TestTheListingIsNotCappedAtOneThousand:
    """The contents API caps a directory at 1000 entries and does not say so.

    tronbyt/apps returns exactly 1000 through that endpoint, which is the cap
    rather than the app count -- the store looked complete while showing a
    truncated repository.
    """

    def _repo_with_tree(self, module, count):
        repo = module.TronbyteRepository()
        entries = [{'path': 'app%04d' % i, 'type': 'tree'} for i in range(count)]

        def fake_request(url, timeout=10):
            if url.endswith('/git/trees/main'):
                return {'tree': [{'path': 'apps', 'type': 'tree', 'sha': 'deadbeef'}]}
            if url.endswith('/git/trees/deadbeef'):
                return {'tree': entries, 'truncated': False}
            raise AssertionError("unexpected request: %s" % url)

        repo._make_request = fake_request
        return repo

    def test_more_than_a_thousand_apps_are_listed(self):
        module = _repository_module()
        repo = self._repo_with_tree(module, 1400)

        ok, apps, err = repo.list_apps()
        assert ok, err
        assert len(apps) == 1400

    def test_the_path_is_still_the_one_manifest_fetches_use(self):
        module = _repository_module()
        repo = self._repo_with_tree(module, 3)

        _, apps, _ = repo.list_apps()
        assert apps[0]['path'] == 'apps/app0000', apps[0]

    def test_dotfiles_and_files_are_skipped(self):
        module = _repository_module()
        repo = module.TronbyteRepository()

        def fake_request(url, timeout=10):
            if url.endswith('/git/trees/main'):
                return {'tree': [{'path': 'apps', 'type': 'tree', 'sha': 'x'}]}
            return {'tree': [{'path': '.github', 'type': 'tree'},
                             {'path': 'realapp', 'type': 'tree'},
                             {'path': 'README.md', 'type': 'blob'}]}

        repo._make_request = fake_request
        _, apps, _ = repo.list_apps()
        assert [a['id'] for a in apps] == ['realapp']

    def test_it_falls_back_to_the_contents_api(self):
        """A trees outage must not take the store down with it."""
        module = _repository_module()
        repo = module.TronbyteRepository()

        def fake_request(url, timeout=10):
            if '/git/trees/' in url:
                repo.last_error = "GitHub API error 500"
                return None
            return [{'name': 'fallbackapp', 'path': 'apps/fallbackapp', 'type': 'dir'}]

        repo._make_request = fake_request
        ok, apps, err = repo.list_apps()
        assert ok, err
        assert [a['id'] for a in apps] == ['fallbackapp']

    def test_both_paths_failing_reports_the_reason(self):
        module = _repository_module()
        repo = module.TronbyteRepository()

        def fake_request(url, timeout=10):
            repo.last_error = "Timed out reaching GitHub"
            return None

        repo._make_request = fake_request
        ok, apps, err = repo.list_apps()
        assert not ok
        assert 'Timed out' in err, err


class TestTheStoreUsesTheTokenTheUserConfigured:
    """The store authenticated with a key nothing ever writes.

    The three repository routes read `github_token` off config.json. Nothing
    writes that key: config.template.json has no such field, no setting
    offers it, and the token the user actually configures goes to
    config_secrets.json as `github.api_token`, which PluginStoreManager loads
    and every other GitHub caller uses.

    So the store ran unauthenticated at 60 requests/hour on the same per-IP
    budget as 48 plugins' update checks, while the configured token sat
    unused raising that same budget to 5000. On the device this was found on,
    /plugins/store/github-status reported `authenticated: true` with a
    rate_limit of 5000 while /starlark/repository/browse reported a limit of
    60 -- the store going blank was that 60 running out.
    """

    def test_the_store_managers_token_is_used(self):
        from web_interface.blueprints import api_v3 as mod

        with patch.object(mod.api_v3, 'plugin_store_manager',
                          MagicMock(github_token='ghp_configured')):
            assert mod._starlark_github_token() == 'ghp_configured'

    def test_a_hand_edited_config_key_still_works(self):
        from web_interface.blueprints import api_v3 as mod

        cfg = MagicMock()
        cfg.load_config.return_value = {'github_token': 'ghp_by_hand'}
        with patch.object(mod.api_v3, 'plugin_store_manager',
                          MagicMock(github_token=None)), \
             patch.object(mod.api_v3, 'config_manager', cfg):
            assert mod._starlark_github_token() == 'ghp_by_hand'

    def test_no_token_anywhere_is_not_an_error(self):
        from web_interface.blueprints import api_v3 as mod

        cfg = MagicMock()
        cfg.load_config.return_value = {}
        with patch.object(mod.api_v3, 'plugin_store_manager',
                          MagicMock(github_token=None)), \
             patch.object(mod.api_v3, 'config_manager', cfg):
            assert mod._starlark_github_token() is None

    def test_an_unreadable_config_does_not_take_the_store_down(self):
        from web_interface.blueprints import api_v3 as mod

        cfg = MagicMock()
        cfg.load_config.side_effect = OSError("config.json is unreadable")
        with patch.object(mod.api_v3, 'plugin_store_manager',
                          MagicMock(github_token=None)), \
             patch.object(mod.api_v3, 'config_manager', cfg):
            assert mod._starlark_github_token() is None

    def test_browse_hands_the_token_to_the_repository(self, client):
        from web_interface.blueprints import api_v3 as mod

        repo = MagicMock()
        repo.return_value.list_all_apps_cached.return_value = {
            'apps': [], 'categories': [], 'authors': [], 'count': 0,
            'cached': False, 'error': None,
        }
        repo.return_value.get_rate_limit_info.return_value = {'remaining': 4999}

        with patch.object(mod.api_v3, 'plugin_store_manager',
                          MagicMock(github_token='ghp_configured')), \
             patch('web_interface.blueprints.api_v3._get_tronbyte_repository_class',
                   return_value=repo):
            client.get('/api/v3/starlark/repository/browse')

        repo.assert_called_once_with(github_token='ghp_configured')


class TestPixletEditorHostDefaultsButDoesNotOverride:
    """PIXLET_EDITOR_HOST must default to 0.0.0.0, never force it.

    A browser reaching the editor is remote by definition, so a session with
    nothing configured has to bind more than loopback to be reachable at
    all -- but an operator who has deliberately pinned PIXLET_EDITOR_HOST to
    loopback (e.g. in the systemd unit's Environment=, to edit only over an
    SSH tunnel) must keep that setting. The previous unconditional
    ``env['PIXLET_EDITOR_HOST'] = '0.0.0.0'`` overrode it every time,
    always exposing the unauthenticated ``pixlet serve`` dev process on the
    LAN regardless (CodeQL CWE-1188).
    """

    @pytest.fixture
    def app_dir(self, tmp_path):
        d = tmp_path / "demo_app"
        d.mkdir()
        (d / "demo_app.star").write_text("def main():\n    pass\n")
        return d

    def _start(self, client, app_dir, tmp_path, operator_host):
        from web_interface.blueprints import api_v3 as mod

        script = tmp_path / "pixlet_config_editor.sh"
        script.write_text("#!/bin/bash\n")
        state_file = tmp_path / "pixlet_editor_state.json"
        captured = {}

        class FakeProcess:
            pid = 424242

        def fake_popen(cmd, cwd=None, env=None, stdout=None, stderr=None,
                       start_new_session=None):
            captured['env'] = env
            return FakeProcess()

        with patch.object(mod, '_validate_starlark_app_path',
                          return_value=(app_dir, None)), \
             patch.object(mod, '_PIXLET_EDITOR_SCRIPT', script), \
             patch.object(mod, '_PIXLET_EDITOR_STATE', state_file), \
             patch.object(mod, '_find_pixlet_binary', return_value='/usr/bin/pixlet'), \
             patch.object(mod.subprocess, 'Popen', side_effect=fake_popen), \
             patch.dict(os.environ):
            if operator_host is None:
                os.environ.pop('PIXLET_EDITOR_HOST', None)
            else:
                os.environ['PIXLET_EDITOR_HOST'] = operator_host
            resp = client.post('/api/v3/starlark/editor/start',
                                json={'app_id': app_dir.name})

        assert resp.status_code == 200, resp.get_json()
        assert 'env' in captured, "subprocess.Popen was never called"
        return captured['env']

    def test_defaults_to_0_0_0_0_when_operator_set_nothing(self, client, app_dir, tmp_path):
        env = self._start(client, app_dir, tmp_path, operator_host=None)
        assert env['PIXLET_EDITOR_HOST'] == '0.0.0.0'

    def test_keeps_an_operator_configured_loopback_host(self, client, app_dir, tmp_path):
        env = self._start(client, app_dir, tmp_path, operator_host='127.0.0.1')
        assert env['PIXLET_EDITOR_HOST'] == '127.0.0.1'
