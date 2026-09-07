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
def client():
    from web_interface.app import app
    app.config['TESTING'] = True
    with app.test_client() as c:
        yield c


@pytest.fixture
def offline_repository():
    """Stand in for TronbyteRepository so the store routes stay offline.

    The browse and categories routes call list_all_apps_cached(), which on a
    cold server-side cache fetches the app index from GitHub. That would make
    the suite slow, network-dependent and rate-limitable -- and because these
    tests only assert "not a 404", a rate-limited 500 would pass and hide it.
    """
    repo = MagicMock()
    repo.list_all_apps_cached.return_value = {
        'apps': [], 'categories': [], 'authors': [], 'count': 0, 'cached': True,
    }
    with patch('web_interface.blueprints.api_v3._get_tronbyte_repository_class',
               return_value=lambda *a, **kw: repo):
        yield repo


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

    def test_browse_does_not_404(self, client, offline_repository):
        resp = client.get('/api/v3/starlark/repository/browse')
        assert resp.status_code != 404, "the store cannot list anything"
        assert resp.get_json().get('message') != 'Resource not found'

    def test_categories_does_not_404(self, client, offline_repository):
        resp = client.get('/api/v3/starlark/repository/categories')
        assert resp.status_code != 404
        assert resp.get_json().get('message') != 'Resource not found'

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
        """The id here is the one _starlark_virtual_plugins publishes.

        It used to be re-slugified on the way in -- lowercased, with every
        character outside [a-z0-9_] replaced -- so 'My-App' was listed as
        'starlark:My-App' and looked up as 'my_app', and toggling an app the
        page had just drawn answered 404.
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
    def starlark_dir(self, tmp_path, monkeypatch):
        from web_interface.blueprints import api_v3 as module
        apps_dir = tmp_path / "starlark-apps"
        apps_dir.mkdir()
        monkeypatch.setattr(module, '_STARLARK_APPS_DIR', apps_dir)
        monkeypatch.setattr(module, '_STARLARK_MANIFEST_FILE', apps_dir / 'manifest.json')
        return apps_dir

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

    def test_the_installed_star_file_is_recorded_by_name(self, starlark_dir, tmp_path):
        """Readers join this to the app's own directory and default to a bare
        '<app_id>.star'. An absolute value gave the key two meanings and, since
        Path.__truediv__ discards the left side when the right is absolute,
        pinned the manifest to the PROJECT_ROOT it was installed under."""
        from web_interface.blueprints import api_v3 as module

        source = tmp_path / "source.star"
        source.write_text("# app")
        with patch.object(module, '_get_pixlet_renderer_class',
                          side_effect=ImportError("no pixlet here")):
            assert module._install_star_file('demo', str(source), {'name': 'Demo'})

        entry = json.loads((starlark_dir / 'manifest.json').read_text())['apps']['demo']
        assert entry['star_file'] == 'demo.star'
        assert not os.path.isabs(entry['star_file'])


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
            with patch('importlib.util.module_from_spec') as from_spec:
                from_spec.return_value = types.ModuleType(key)
                with patch('importlib.util.spec_from_file_location') as spec_from:
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
