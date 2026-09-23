"""POST /plugins/toggle reports what actually went wrong.

Every failure used to be mapped to PLUGIN_OPERATION_CONFLICT, so the user was
told "A plugin operation is already in progress" when, say, the config could
not be read.
"""

import json

from test._api_v3_test_helpers import api_v3_client, api_v3_module  # noqa: F401


def test_failure_is_not_reported_as_a_conflict(api_v3_client, api_v3_module, monkeypatch):
    monkeypatch.setattr(api_v3_module, '_discovered_plugin_manifests',
                        lambda *a, **k: {'clock': {}})
    api_v3_module.api_v3.config_manager.load_config.side_effect = OSError('disk gone')

    resp = api_v3_client.post('/api/v3/plugins/toggle',
                              data=json.dumps({'plugin_id': 'clock', 'enabled': True}),
                              content_type='application/json')

    assert resp.status_code == 500
    body = resp.get_json()
    assert body['error_code'] != 'PLUGIN_OPERATION_CONFLICT'
    assert 'already in progress' not in body['message']
    assert body['message'] == 'Failed to enable plugin clock'
    history = api_v3_module.api_v3.operation_history.record_operation
    history.assert_called_once()
    assert history.call_args.kwargs['plugin_id'] == 'clock'
