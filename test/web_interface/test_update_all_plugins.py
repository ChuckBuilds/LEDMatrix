"""'Check & Update All' must only send ids POST /plugins/update handles.

Seen on a device running core 3.4.0: update-all posted every entry from
/plugins/installed, including the virtual `starlark:<app_id>` entries that
list installed Starlark apps, and the route answered each with a 500
"Plugin update failed: plugin not found". A web-service restart during the
same run also cost stock-news its update: its request was refused while the
service came back, and update-all never sent it again.

The id selection and retry live in install_manager.js and are covered by
test/js/unit/test_update_all.js, which this module runs so CI sees it. The
route contract is tested here directly.
"""

import shutil
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

REPO = Path(__file__).resolve().parents[2]
JS_SUITE = REPO / 'test' / 'js' / 'unit' / 'test_update_all.js'


@pytest.fixture
def client():
    from web_interface.app import app
    app.config['TESTING'] = True
    with app.test_client() as c:
        yield c


@pytest.fixture
def store(tmp_path):
    """A store manager that records calls; no git, no network."""
    from web_interface.blueprints.api_v3 import api_v3
    sm = MagicMock()
    sm.plugins_dir = str(tmp_path)
    sm._get_local_git_info.return_value = None
    sm.get_plugin_info.return_value = None
    sm.update_plugin.return_value = True
    with patch.object(api_v3, 'plugin_store_manager', sm, create=True), \
         patch.object(api_v3, 'plugin_manager', None, create=True), \
         patch.object(api_v3, 'schema_manager', None, create=True), \
         patch.object(api_v3, 'plugin_state_manager', None, create=True), \
         patch.object(api_v3, 'operation_history', None, create=True):
        yield sm


class TestUpdateRouteRejectsStarlarkIds:

    @pytest.mark.parametrize('app_id', ['starlark:analogtime', 'starlark:analogclock'])
    def test_a_starlark_id_is_a_400_not_a_500(self, client, store, app_id):
        resp = client.post('/api/v3/plugins/update', json={'plugin_id': app_id})
        assert resp.status_code == 400, resp.get_json()
        body = resp.get_json()
        assert body['status'] == 'error'
        assert body['error_code'] == 'INVALID_INPUT'
        assert 'Starlark app' in body['message'], body
        assert 'not found' not in body['message'], \
            "the app is installed; 'not found' is the misleading message this replaces"

    def test_the_store_manager_is_never_asked(self, client, store):
        client.post('/api/v3/plugins/update', json={'plugin_id': 'starlark:analogtime'})
        store.update_plugin.assert_not_called()

    def test_form_encoded_starlark_id_is_rejected_the_same_way(self, client, store):
        resp = client.post('/api/v3/plugins/update', data={'plugin_id': 'starlark:analogtime'})
        assert resp.status_code == 400

    def test_a_plugin_id_still_reaches_the_updater(self, client, store, tmp_path):
        # The guard is on the 'starlark:' prefix only: the starlark-apps plugin
        # and a disabled plugin with an update are still updated.
        for pid in ('stock-news', 'starlark-apps'):
            (tmp_path / pid).mkdir()
            (tmp_path / pid / 'manifest.json').write_text('{"id": "%s"}' % pid, encoding='utf-8')
            resp = client.post('/api/v3/plugins/update', json={'plugin_id': pid})
            assert resp.status_code == 200, (pid, resp.get_json())
        assert [c.args[0] for c in store.update_plugin.call_args_list] == ['stock-news', 'starlark-apps']


class TestInstalledListContract:
    """What update-all filters on is what /plugins/installed publishes."""

    def test_starlark_entries_are_flagged_and_prefixed(self, client):
        apps = {'apps': {'analogtime': {'name': 'Analog Time', 'enabled': False}}}
        with patch('web_interface.blueprints.api_v3._get_starlark_plugin', return_value=None), \
             patch('web_interface.blueprints.api_v3._read_starlark_manifest', return_value=apps):
            resp = client.get('/api/v3/plugins/installed')
        plugins = resp.get_json()['data']['plugins']
        entry = next(p for p in plugins if p['id'] == 'starlark:analogtime')
        assert entry['is_starlark_app'] is True
        assert not any(p.get('is_starlark_app') for p in plugins
                       if not p['id'].startswith('starlark:')), \
            "a real plugin carries the Starlark flag and would be dropped from update-all"


@pytest.mark.skipif(shutil.which('node') is None, reason='node is not installed')
def test_update_all_js_selection_and_retry():
    node = shutil.which('node')
    result = subprocess.run([node, str(JS_SUITE)], capture_output=True, text=True,
                            timeout=120, cwd=str(JS_SUITE.parent))
    assert result.returncode == 0, result.stdout + result.stderr
