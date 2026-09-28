"""POST /api/v3/starlark/apps/<app_id>/toggle shares _toggle_starlark_app.

The route carried its own copy of the toggle with the bugs the shared helper
had already fixed: it changed the loaded app before the disk write (so a
failed save left the two disagreeing), indexed ``manifest['apps'][app_id]``
(a KeyError, answered as a 500, for a loaded app with no on-disk entry), and
stored ``"false"`` -- a truthy string -- as the new state.
"""

import contextlib
from unittest.mock import MagicMock, patch

import pytest

PKG = 'web_interface.blueprints.api_v3'


@pytest.fixture
def client():
    from web_interface.app import app
    app.config['TESTING'] = True
    with app.test_client() as c:
        yield c


def _loaded_plugin(enabled=True, save_ok=True, disk=None):
    app = MagicMock()
    app.manifest = {'enabled': enabled}
    app.is_enabled.return_value = enabled
    plugin = MagicMock()
    plugin.apps = {'clock': app}
    disk = {'apps': {}} if disk is None else disk

    def _update(fn):
        if not save_ok:
            return False
        fn(disk)
        return True
    plugin._update_manifest_safe.side_effect = _update
    return plugin, app, disk


def _post(client, body):
    return client.post('/api/v3/starlark/apps/clock/toggle', json=body)


class TestLoadedApp:
    def test_a_missing_disk_entry_is_created_not_a_500(self, client):
        plugin, app, disk = _loaded_plugin(enabled=True)
        with patch(f'{PKG}._get_starlark_plugin', return_value=plugin):
            resp = _post(client, {'enabled': False})
        body = resp.get_json()
        assert resp.status_code == 200, body
        assert body == {'status': 'success', 'message': body['message'], 'enabled': False}
        assert disk['apps']['clock']['enabled'] is False
        assert app.manifest['enabled'] is False

    def test_a_failed_save_leaves_the_loaded_app_alone(self, client):
        plugin, app, _ = _loaded_plugin(enabled=True, save_ok=False)
        with patch(f'{PKG}._get_starlark_plugin', return_value=plugin):
            resp = _post(client, {'enabled': False})
        assert resp.status_code == 500
        assert resp.get_json()['status'] == 'error'
        assert app.manifest['enabled'] is True

    @pytest.mark.parametrize('sent', ['false', 'False', 0, '0'])
    def test_a_false_string_or_zero_disables(self, client, sent):
        plugin, app, disk = _loaded_plugin(enabled=True)
        with patch(f'{PKG}._get_starlark_plugin', return_value=plugin):
            resp = _post(client, {'enabled': sent})
        assert resp.get_json()['enabled'] is False
        assert disk['apps']['clock']['enabled'] is False

    def test_an_unrecognised_value_is_refused(self, client):
        plugin, app, disk = _loaded_plugin(enabled=True)
        with patch(f'{PKG}._get_starlark_plugin', return_value=plugin):
            resp = _post(client, {'enabled': 'maybe'})
        assert resp.status_code == 400
        assert disk == {'apps': {}}

    def test_no_enabled_flips_the_current_state(self, client):
        plugin, app, disk = _loaded_plugin(enabled=True)
        with patch(f'{PKG}._get_starlark_plugin', return_value=plugin):
            resp = _post(client, {})
        assert resp.get_json()['enabled'] is False


class TestStandalone:
    def _run(self, client, body, manifest):
        written = {}
        # The real lock is fcntl-based (Linux only); the toggle logic is what
        # is under test here.
        with patch(f'{PKG}._get_starlark_plugin', return_value=None), \
             patch(f'{PKG}._starlark_manifest_lock', contextlib.nullcontext), \
             patch(f'{PKG}._read_starlark_manifest', return_value=manifest), \
             patch(f'{PKG}._write_starlark_manifest',
                   side_effect=lambda m: written.update(m) or True):
            resp = _post(client, body)
        return resp, written

    def test_toggle_is_written(self, client):
        resp, written = self._run(client, {'enabled': 'false'},
                                  {'apps': {'clock': {'enabled': True}}})
        assert resp.get_json()['enabled'] is False
        assert written['apps']['clock']['enabled'] is False

    def test_no_enabled_flips_the_manifest_state(self, client):
        resp, _ = self._run(client, {}, {'apps': {'clock': {'enabled': False}}})
        assert resp.get_json()['enabled'] is True

    @pytest.mark.parametrize('body', [{}, {'enabled': True}])
    def test_an_unknown_app_is_404(self, client, body):
        resp, written = self._run(client, body, {'apps': {}})
        assert resp.status_code == 404
        assert 'clock' in resp.get_json()['message']
        assert written == {}
