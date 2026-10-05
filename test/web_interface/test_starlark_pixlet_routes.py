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
import sys
import tempfile
import threading
import types
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def client(monkeypatch):
    from web_interface import app as web_app
    from web_interface.app import app
    # The captive-portal before_request hook shells out to systemctl/nmcli
    # whenever its 30s cache is cold, so on a Linux host whether a request
    # here runs subprocess depended on how long ago the previous one was --
    # and several tests below stub subprocess. Pin it: no test in this file
    # is about AP mode.
    monkeypatch.setattr(web_app, 'is_ap_mode_active', lambda: False)
    # GET /plugins/installed looks up each plugin's registry entry, and on a
    # cold cache that fetches plugins.json from GitHub -- so without a
    # connection those tests sat in the HTTP retry loop. No test in this
    # file is about the registry.
    monkeypatch.setattr(web_app.plugin_store_manager, 'fetch_registry',
                        lambda *args, **kwargs: {'plugins': []})
    app.config['TESTING'] = True
    with app.test_client() as c:
        yield c


@pytest.fixture(autouse=True)
def starlark_apps_dir(tmp_path, monkeypatch):
    """Point every Starlark storage path at tmp_path for every test.

    The manifest, its directory and the lock file are three separate module
    constants. Fixtures that redirected the first two but not the lock left
    the lock pointing at the repo, and on Linux (where the lock is taken)
    each test created starlark-apps/manifest.json.lock in the checkout. The
    directory is not created here; tests that need it make it.
    """
    from web_interface.blueprints import api_v3 as module
    apps_dir = tmp_path / "starlark-apps"
    monkeypatch.setattr(module, '_STARLARK_APPS_DIR', apps_dir)
    monkeypatch.setattr(module, '_STARLARK_MANIFEST_FILE', apps_dir / 'manifest.json')
    monkeypatch.setattr(module, '_STARLARK_MANIFEST_LOCK_FILE', apps_dir / 'manifest.json.lock')
    return apps_dir


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
        with patch('web_interface.blueprints.api_v3.starlark.subprocess.run') as run:
            run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")
            resp = client.post('/api/v3/starlark/install-pixlet')
        assert resp.status_code != 404, "the route is still missing"
        assert resp.get_json().get('message') != 'Resource not found'

    def test_success_is_reported_in_the_shape_the_button_reads(self, client):
        with patch('web_interface.blueprints.api_v3.starlark.subprocess.run') as run:
            run.return_value = MagicMock(returncode=0, stdout="done", stderr="")
            resp = client.post('/api/v3/starlark/install-pixlet')
        body = resp.get_json()
        assert body['status'] == 'success', body
        assert 'message' in body, "the JS shows data.message on success"

    def test_a_failed_download_says_why(self, client):
        with patch('web_interface.blueprints.api_v3.starlark.subprocess.run') as run:
            run.return_value = MagicMock(returncode=1, stdout="", stderr="no such release")
            resp = client.post('/api/v3/starlark/install-pixlet')
        body = resp.get_json()
        assert body['status'] == 'error'
        assert 'no such release' in body['message'], \
            "the installer's own stderr is what tells the user what went wrong"

    def test_a_timeout_is_reported_rather_than_hanging(self, client):
        import subprocess as sp
        with patch('web_interface.blueprints.api_v3.starlark.subprocess.run',
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

    def test_a_failed_manifest_write_is_not_reported_as_success(self, client):
        """The toggle answered 200 while the change was never persisted."""
        manifest = {'apps': {'demo': {'name': 'Demo', 'enabled': False}}}
        with patch('web_interface.blueprints.api_v3._get_starlark_plugin', return_value=None), \
             patch('web_interface.blueprints.api_v3._read_starlark_manifest',
                   return_value=manifest), \
             patch('web_interface.blueprints.api_v3._write_starlark_manifest',
                   return_value=False):
            resp = client.post('/api/v3/plugins/toggle',
                               json={'plugin_id': 'starlark:demo', 'enabled': True})
        assert resp.status_code == 500

    def test_a_loaded_app_is_not_flipped_when_the_manifest_write_fails(self, client):
        """Persist first, then update memory -- otherwise the UI shows a
        toggle that silently reverts on the next restart."""
        app = MagicMock()
        app.manifest = {'enabled': False}
        plugin = MagicMock()
        plugin.apps = {'demo': app}
        plugin._update_manifest_safe.return_value = False

        with patch('web_interface.blueprints.api_v3._get_starlark_plugin', return_value=plugin):
            resp = client.post('/api/v3/plugins/toggle',
                               json={'plugin_id': 'starlark:demo', 'enabled': True})
        assert resp.status_code == 500
        assert app.manifest['enabled'] is False, "in-memory state changed without being saved"


class TestTheManifestSurvivesConcurrentWriters:
    """Flask serves requests concurrently and five routes write this file.

    The atomic-write pattern used one fixed temp name, `manifest.tmp`, shared
    by every writer: two of them opened it, interleaved their json.dump output,
    and both renamed. The rename is atomic; the content it published was the
    mixture, which the next read could not parse.
    """

    @pytest.fixture
    def starlark_dir(self, starlark_apps_dir):
        starlark_apps_dir.mkdir()
        return starlark_apps_dir

    def test_each_writer_gets_its_own_temp_file(self, starlark_dir):
        from web_interface.blueprints import api_v3 as module

        seen = []
        real_mkstemp = tempfile.mkstemp

        def recording_mkstemp(*args, **kwargs):
            fd, name = real_mkstemp(*args, **kwargs)
            seen.append(name)
            return fd, name

        with patch.object(module.tempfile, 'mkstemp', side_effect=recording_mkstemp):
            for i in range(5):
                assert module._write_starlark_manifest({'apps': {f'app{i}': {}}})

        assert len(set(seen)) == 5, f"writers shared a temp file: {seen}"

    def test_concurrent_writes_leave_readable_json(self, starlark_dir):
        from web_interface.blueprints import api_v3 as module

        manifests = [{'apps': {f'app{i}': {'name': 'x' * 400}}} for i in range(8)]
        errors = []

        def write(m):
            try:
                module._write_starlark_manifest(m)
            except Exception as exc:  # noqa: BLE001 - surfaced by the assert below
                errors.append(exc)

        threads = [threading.Thread(target=write, args=(m,)) for m in manifests]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        loaded = json.loads((starlark_dir / 'manifest.json').read_text())
        assert loaded in manifests, "the published manifest was a mix of two writers"

    def test_no_temp_files_are_left_behind(self, starlark_dir):
        from web_interface.blueprints import api_v3 as module
        module._write_starlark_manifest({'apps': {}})
        assert list(starlark_dir.glob('*.tmp')) == []


class TestATransientImportFailureIsNotPermanent:
    """Both importers insert into sys.modules before executing the module.

    That order is required -- a module has to be findable while it runs -- but
    a failure left the half-initialised object cached, so every later call took
    the cache branch and raised AttributeError on the missing class instead of
    retrying. One transient failure disabled the repository or the renderer for
    the life of the process.
    """

    @pytest.mark.parametrize("getter,key", [
        ('_get_tronbyte_repository_class', 'tronbyte_repository'),
        ('_get_pixlet_renderer_class', 'pixlet_renderer'),
    ])
    def test_a_failed_import_leaves_no_entry_behind(self, getter, key):
        from web_interface.blueprints import api_v3 as module

        original = sys.modules.pop(key, None)
        try:
            with patch('importlib.util.module_from_spec') as from_spec, \
                 patch('importlib.util.spec_from_file_location') as spec_from, \
                 patch.object(module.Path, 'exists', return_value=True):
                from_spec.return_value = types.ModuleType(key)
                spec = MagicMock()
                spec.loader.exec_module.side_effect = RuntimeError("network down")
                spec_from.return_value = spec

                with pytest.raises(RuntimeError):
                    getattr(module, getter)()

            assert key not in sys.modules, "a half-initialised module stayed cached"
        finally:
            if original is not None:
                sys.modules[key] = original
            else:
                sys.modules.pop(key, None)


class TestConfigIsNotAppliedUntilItIsSaved:
    """save_config() returning False answers 500, but the loaded app kept the
    new values -- so GET config reported settings that were never written and
    the plugin rendered with them until a restart silently reverted them."""

    def _plugin_with_app(self, save_ok):
        app = MagicMock()
        app.config = {'city': 'Philadelphia'}
        app.manifest = {'render_interval': 300, 'display_duration': 15}
        app.save_config.return_value = save_ok
        plugin = MagicMock()
        plugin.apps = {'demo': app}
        plugin._update_manifest_safe.return_value = True
        return plugin, app

    def test_a_failed_save_leaves_the_config_untouched(self, client):
        plugin, app = self._plugin_with_app(save_ok=False)
        with patch('web_interface.blueprints.api_v3._get_starlark_plugin', return_value=plugin):
            resp = client.put('/api/v3/starlark/apps/demo/config',
                              json={'city': 'Pittsburgh'})
        assert resp.status_code == 500
        assert app.config == {'city': 'Philadelphia'}

    def test_a_failed_save_leaves_the_timing_untouched(self, client):
        plugin, app = self._plugin_with_app(save_ok=False)
        with patch('web_interface.blueprints.api_v3._get_starlark_plugin', return_value=plugin):
            resp = client.put('/api/v3/starlark/apps/demo/config',
                              json={'render_interval': 60})
        assert resp.status_code == 500
        assert app.manifest['render_interval'] == 300

    def test_a_failed_save_does_not_re_render_with_values_it_did_not_keep(self, client):
        plugin, _ = self._plugin_with_app(save_ok=False)
        with patch('web_interface.blueprints.api_v3._get_starlark_plugin', return_value=plugin):
            client.put('/api/v3/starlark/apps/demo/config', json={'city': 'Pittsburgh'})
        plugin._render_app.assert_not_called()

    def test_a_successful_save_still_applies(self, client):
        plugin, app = self._plugin_with_app(save_ok=True)
        with patch('web_interface.blueprints.api_v3._get_starlark_plugin', return_value=plugin):
            resp = client.put('/api/v3/starlark/apps/demo/config',
                              json={'city': 'Pittsburgh', 'render_interval': 60})
        assert resp.status_code == 200
        assert app.config['city'] == 'Pittsburgh'
        assert app.manifest['render_interval'] == 60
        plugin._render_app.assert_called_once()

    def test_an_unsaved_timing_change_is_logged(self, client, caplog):
        """_update_manifest_safe answers False rather than raising, so the
        except branch alone never saw a failed timing write."""
        plugin, _ = self._plugin_with_app(save_ok=True)
        plugin._update_manifest_safe.return_value = False
        with patch('web_interface.blueprints.api_v3._get_starlark_plugin', return_value=plugin), \
             caplog.at_level('WARNING'):
            resp = client.put('/api/v3/starlark/apps/demo/config',
                              json={'render_interval': 60})
        assert resp.status_code == 200
        assert any('not persisted' in r.getMessage() for r in caplog.records)


class TestTheManifestStaysRelocatable:
    """`star_file` is joined to the app's own directory by its readers.

    _standalone_render_starlark_app does `app_dir / app_data.get('star_file',
    f'{app_id}.star')`, so the key's default is a bare filename. Storing an
    absolute path gave it a second meaning, and Path.__truediv__ discards the
    left side when the right is absolute -- which pinned the manifest to the
    PROJECT_ROOT that installed it.
    """

    @pytest.fixture
    def starlark_dir(self, starlark_apps_dir):
        starlark_apps_dir.mkdir()
        return starlark_apps_dir

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


class TestManifestLockPreventsLostUpdates:
    """The standalone manifest fallback (no plugin instance loaded) reads,
    mutates and writes manifest.json with no coordination across requests.
    Each write is atomic on its own (temp file + rename), but two concurrent
    read-modify-write cycles can still race: both read the same starting
    manifest, and the second write silently discards whatever the first one
    added. _starlark_manifest_lock closes that window -- mirrors
    StarlarkAppsPlugin._update_manifest_safe, which already does this when
    the plugin instance is loaded.
    """

    @pytest.fixture
    def starlark_dir(self, starlark_apps_dir):
        from web_interface.blueprints import api_v3 as module
        apps_dir = starlark_apps_dir
        apps_dir.mkdir()
        module._write_starlark_manifest({'apps': {}})
        return apps_dir

    def test_the_locked_file_survives_a_manifest_write(self, starlark_dir):
        """_write_starlark_manifest replaces manifest.json with a fresh inode
        on every write (temp file + rename). If the lock were taken on that
        same file, a second locker's fresh os.open() right after the rename
        would land on the new inode -- unguarded, because only the old,
        now-orphaned inode was ever locked -- and two writers could race
        despite each believing it "held the lock" (see the docstring on
        _starlark_manifest_lock). Locking a sidecar path that no write ever
        touches or renames over closes that: the inode identity of what gets
        locked must not change across writes.
        """
        import os

        from web_interface.blueprints import api_v3 as module

        with module._starlark_manifest_lock():
            manifest = module._read_starlark_manifest()
        lock_ino_before = os.stat(module._STARLARK_MANIFEST_LOCK_FILE).st_ino

        for app_id in ('one', 'two', 'three'):
            with module._starlark_manifest_lock():
                manifest = module._read_starlark_manifest()
                manifest.setdefault('apps', {})[app_id] = {'enabled': True}
                assert module._write_starlark_manifest(manifest)

        lock_ino_after = os.stat(module._STARLARK_MANIFEST_LOCK_FILE).st_ino
        assert lock_ino_after == lock_ino_before, (
            "the locked file's inode changed across writes -- a locker that "
            "opened it before this write and one that opens it after would "
            "no longer contend for the same lock")

    def test_two_concurrent_updates_are_both_kept(self, starlark_dir):
        import threading
        import time as _time

        from web_interface.blueprints import api_v3 as module

        def add_app(app_id):
            with module._starlark_manifest_lock():
                manifest = module._read_starlark_manifest()
                # Widen the window between read and write. Without the lock
                # both threads read here before either writes, and whichever
                # writes second overwrites the other's addition; with the
                # lock, the second thread cannot even start its read until
                # the first has written and released.
                _time.sleep(0.05)
                manifest.setdefault('apps', {})[app_id] = {'enabled': True}
                module._write_starlark_manifest(manifest)

        threads = [threading.Thread(target=add_app, args=(app_id,))
                   for app_id in ('a', 'b')]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        manifest = module._read_starlark_manifest()
        assert set(manifest['apps']) == {'a', 'b'}, (
            "a concurrent update was lost: %r" % (manifest,))

    def test_the_lock_is_reentrant_safe_across_sequential_calls(self, starlark_dir):
        """Not reentrant within one thread -- just that using it twice in a
        row (the ordinary case: one request, then the next) works cleanly
        and does not leak the lock file descriptor or leave it locked."""
        from web_interface.blueprints import api_v3 as module

        for app_id in ('first', 'second'):
            with module._starlark_manifest_lock():
                manifest = module._read_starlark_manifest()
                manifest.setdefault('apps', {})[app_id] = {'enabled': True}
                module._write_starlark_manifest(manifest)

        manifest = module._read_starlark_manifest()
        assert set(manifest['apps']) == {'first', 'second'}


class TestConfigAndManifestStayInSync:
    """Standalone-mode PUT /starlark/apps/<id>/config (no plugin instance
    loaded) writes config.json and then the manifest. If the manifest write
    fails after config.json was already written, the two disagree about
    what was saved unless config.json is rolled back.
    """

    @pytest.fixture
    def app_dir(self, starlark_apps_dir):
        from web_interface.blueprints import api_v3 as module
        apps_dir = starlark_apps_dir
        apps_dir.mkdir()
        one_app_dir = apps_dir / 'demo'
        one_app_dir.mkdir()
        module._write_starlark_manifest({'apps': {'demo': {'name': 'Demo', 'enabled': True}}})
        return one_app_dir

    def test_manifest_write_failure_rolls_back_an_existing_config_json(self, client, app_dir):
        config_file = app_dir / 'config.json'
        config_file.write_text(json.dumps({'existing': 'value'}))

        with patch('web_interface.blueprints.api_v3._get_starlark_plugin', return_value=None), \
             patch('web_interface.blueprints.api_v3._write_starlark_manifest', return_value=False):
            resp = client.put('/api/v3/starlark/apps/demo/config',
                              json={'new_field': 'x'})

        assert resp.status_code == 500
        assert json.loads(config_file.read_text()) == {'existing': 'value'}, (
            "config.json kept the new value even though the manifest write "
            "that was supposed to follow it failed")

    def test_manifest_write_failure_removes_a_freshly_created_config_json(self, client, app_dir):
        config_file = app_dir / 'config.json'
        assert not config_file.exists()

        with patch('web_interface.blueprints.api_v3._get_starlark_plugin', return_value=None), \
             patch('web_interface.blueprints.api_v3._write_starlark_manifest', return_value=False):
            resp = client.put('/api/v3/starlark/apps/demo/config',
                              json={'new_field': 'x'})

        assert resp.status_code == 500
        assert not config_file.exists(), (
            "config.json was left behind even though the manifest write "
            "that was supposed to follow it failed")

    def test_success_updates_both_config_and_manifest(self, client, app_dir):
        from web_interface.blueprints import api_v3 as module

        with patch('web_interface.blueprints.api_v3._get_starlark_plugin', return_value=None):
            resp = client.put('/api/v3/starlark/apps/demo/config',
                              json={'new_field': 'x'})

        assert resp.status_code == 200, resp.get_json()
        assert json.loads((app_dir / 'config.json').read_text())['new_field'] == 'x'
        manifest = json.loads(module._STARLARK_MANIFEST_FILE.read_text())
        assert manifest['apps']['demo']['config']['new_field'] == 'x'


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

    The managers are attributes web_interface/app.py hangs on the blueprint
    when it is imported, so they exist only once some earlier test has
    imported the app. Every patch here passes create=True: these tests must
    not depend on which test ran before them.
    """

    def test_the_store_managers_token_is_used(self):
        from web_interface.blueprints import api_v3 as mod

        with patch.object(mod.api_v3, 'plugin_store_manager',
                          MagicMock(github_token='ghp_configured'), create=True):
            assert mod._starlark_github_token() == 'ghp_configured'

    def test_a_hand_edited_config_key_still_works(self):
        from web_interface.blueprints import api_v3 as mod

        cfg = MagicMock()
        cfg.load_config.return_value = {'github_token': 'ghp_by_hand'}
        with patch.object(mod.api_v3, 'plugin_store_manager',
                          MagicMock(github_token=None), create=True), \
             patch.object(mod.api_v3, 'config_manager', cfg, create=True):
            assert mod._starlark_github_token() == 'ghp_by_hand'

    def test_no_token_anywhere_is_not_an_error(self):
        from web_interface.blueprints import api_v3 as mod

        cfg = MagicMock()
        cfg.load_config.return_value = {}
        with patch.object(mod.api_v3, 'plugin_store_manager',
                          MagicMock(github_token=None), create=True), \
             patch.object(mod.api_v3, 'config_manager', cfg, create=True):
            assert mod._starlark_github_token() is None

    def test_an_unreadable_config_does_not_take_the_store_down(self):
        from web_interface.blueprints import api_v3 as mod

        cfg = MagicMock()
        cfg.load_config.side_effect = OSError("config.json is unreadable")
        with patch.object(mod.api_v3, 'plugin_store_manager',
                          MagicMock(github_token=None), create=True), \
             patch.object(mod.api_v3, 'config_manager', cfg, create=True):
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
                          MagicMock(github_token='ghp_configured'), create=True), \
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
        from web_interface.blueprints.api_v3 import starlark as mod

        script = tmp_path / "pixlet_config_editor.sh"
        script.write_text("#!/bin/bash\n")
        state_file = tmp_path / "pixlet_editor_state.json"
        captured = {}

        class FakeProcess:
            pid = 424242

        def fake_popen(cmd, *args, env=None, **kwargs):
            if env is not None:
                captured['env'] = env
            return FakeProcess()

        # Swap the route module's own ``subprocess`` binding, not the shared
        # ``subprocess.Popen``: patching the attribute on the real module is
        # process-wide, and the app's before_request hook (the captive-portal
        # check) runs ``subprocess.run`` -- ``with Popen(...)`` -- whenever its
        # 30s AP-mode cache is cold on a host with systemctl. On the Linux CI
        # runner that handed it this FakeProcess and 500'd the request, but
        # only when the previous request was more than 30s earlier.
        fake_subprocess = types.ModuleType('subprocess')
        fake_subprocess.__dict__.update(mod.subprocess.__dict__)
        fake_subprocess.Popen = fake_popen

        with patch.object(mod, '_validate_starlark_app_path',
                          return_value=(app_dir, None)), \
             patch.object(mod, '_PIXLET_EDITOR_SCRIPT', script), \
             patch.object(mod, '_PIXLET_EDITOR_STATE', state_file), \
             patch.object(mod, '_find_pixlet_binary', return_value='/usr/bin/pixlet'), \
             patch.object(mod, 'subprocess', fake_subprocess), \
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


class TestStandaloneRenderUsesTheDeviceLocation:
    """The web-service render (plugin not loaded) fills a blank Location field
    the same way the display plugin does -- see test/test_device_location.py.
    """

    SCHEMA = {"schema": [{"typeOf": "location", "id": "location"}]}

    @pytest.fixture
    def app_dir(self, starlark_apps_dir, monkeypatch):
        from web_interface.blueprints import api_v3 as module
        apps_dir = starlark_apps_dir
        app_dir = apps_dir / "weather"
        app_dir.mkdir(parents=True)
        (app_dir / "weather.star").write_text("# app")
        (app_dir / "schema.json").write_text(json.dumps(self.SCHEMA))
        (apps_dir / 'manifest.json').write_text(json.dumps(
            {'apps': {'weather': {'star_file': 'weather.star'}}}))
        config_manager = MagicMock()
        config_manager.load_config.return_value = {
            'timezone': 'America/New_York',
            'location': {'city': 'Charlotte', 'state': 'North Carolina', 'country': 'US'},
        }
        monkeypatch.setattr(module.api_v3, 'config_manager', config_manager, raising=False)
        monkeypatch.setattr(module, '_find_pixlet_binary', lambda _p=None: '/usr/bin/pixlet')
        from src.device_location import DeviceLocationResolver
        geocoder = MagicMock(return_value={'lat': 35.22709, 'lng': -80.84313,
                                           'timezone': 'America/New_York'})
        monkeypatch.setattr(module, '_starlark_device_location',
                            DeviceLocationResolver(None, MagicMock(), geocoder))
        return app_dir

    def _render_args(self, app_dir):
        from web_interface.blueprints import api_v3 as module

        def fake_run(cmd, **kwargs):
            (app_dir / 'cached_render.webp').write_bytes(b'webp')
            return MagicMock(returncode=0, stderr='')

        with patch.object(module.subprocess, 'run', side_effect=fake_run) as run:
            ok, status, err = module._standalone_render_starlark_app('weather')
        assert ok, err
        return [a for a in run.call_args.args[0] if a.startswith('location=')]

    def test_a_blank_location_renders_at_the_device_city(self, app_dir):
        (app_dir / 'config.json').write_text(json.dumps({'location': ''}))
        [arg] = self._render_args(app_dir)
        assert json.loads(arg[len('location='):])['lat'] == '35.2271'

    def test_a_saved_location_wins(self, app_dir):
        saved = json.dumps({'lat': '40.6782', 'lng': '-73.9442'})
        (app_dir / 'config.json').write_text(json.dumps({'location': saved}))
        assert self._render_args(app_dir) == [f'location={saved}']
