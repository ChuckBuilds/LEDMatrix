"""Regression tests for the transactional uninstall helper and the
``/plugins/state/reconcile`` endpoint's payload handling.

Bug 1: the original uninstall flow caught
``cleanup_plugin_config`` exceptions and only logged a warning before
proceeding to file deletion. A failure there would leave the plugin
files on disk with no config entry (orphan). The fix is a
``_do_transactional_uninstall`` helper that (a) aborts before touching
the filesystem if cleanup fails, and (b) restores the config+secrets
snapshot if file removal fails after cleanup succeeded.

Bug 2: the reconcile endpoint did ``payload.get('force', False)`` after
``request.get_json(silent=True) or {}``, which raises AttributeError if
the client sent a non-object JSON body (e.g. a bare string or array).
Additionally, ``bool("false")`` is ``True``, so string-encoded booleans
were mis-handled. The fix is an ``isinstance(payload, dict)`` guard plus
routing the value through ``_coerce_to_bool``.
"""

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from flask import Flask


def _make_client():
    """Minimal Flask app + mocked deps that exercises the api_v3 blueprint."""
    from web_interface.blueprints import api_v3 as api_v3_module
    from web_interface.blueprints.api_v3 import api_v3

    # Mocks for all the bits the reconcile / uninstall endpoints touch.
    api_v3.config_manager = MagicMock()
    api_v3.config_manager.get_raw_file_content.return_value = {}
    api_v3.config_manager.secrets_path = "/tmp/nonexistent_secrets.json"
    api_v3.plugin_manager = MagicMock()
    api_v3.plugin_manager.plugins = {}
    api_v3.plugin_manager.plugins_dir = "/tmp"
    api_v3.plugin_store_manager = MagicMock()
    api_v3.plugin_state_manager = MagicMock()
    api_v3.plugin_state_manager.get_all_states.return_value = {}
    api_v3.saved_repositories_manager = MagicMock()
    api_v3.schema_manager = MagicMock()
    api_v3.operation_queue = None  # force the direct (non-queue) path
    api_v3.operation_history = MagicMock()
    api_v3.cache_manager = MagicMock()

    app = Flask(__name__)
    app.config['TESTING'] = True
    app.config['SECRET_KEY'] = 'test'
    app.register_blueprint(api_v3, url_prefix='/api/v3')
    return app.test_client(), api_v3_module


class TestTransactionalUninstall(unittest.TestCase):
    """Exercises ``_do_transactional_uninstall`` directly.

    Using the direct (non-queue) code path via the Flask client gives us
    the full uninstall endpoint behavior end-to-end, including the
    rollback on mid-flight failures.
    """

    def setUp(self):
        self.client, self.mod = _make_client()
        self.api_v3 = self.mod.api_v3

    def test_cleanup_failure_aborts_before_file_removal(self):
        """If cleanup_plugin_config raises, uninstall_plugin must NOT run."""
        self.api_v3.config_manager.cleanup_plugin_config.side_effect = RuntimeError("disk full")

        response = self.client.post(
            '/api/v3/plugins/uninstall',
            data=json.dumps({'plugin_id': 'thing'}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 500)
        # File removal must NOT have been attempted — otherwise we'd have
        # deleted the plugin after failing to clean its config, leaving
        # the reconciler to potentially resurrect it later.
        self.api_v3.plugin_store_manager.uninstall_plugin.assert_not_called()

    def test_file_removal_failure_restores_snapshot(self):
        """If uninstall_plugin returns False after cleanup, snapshot must be restored."""
        # Start with the plugin in main config and in secrets.
        stored_main = {'thing': {'enabled': True, 'custom': 'stuff'}}
        stored_secrets = {'thing': {'api_key': 'secret'}}

        # get_raw_file_content is called twice during snapshot (main +
        # secrets) and then again during restore. We track writes through
        # save_raw_file_content so we can assert the restore happened.
        def raw_get(file_type):
            if file_type == 'main':
                return dict(stored_main)
            if file_type == 'secrets':
                return dict(stored_secrets)
            return {}

        self.api_v3.config_manager.get_raw_file_content.side_effect = raw_get
        self.api_v3.config_manager.secrets_path = __file__  # any existing file
        self.api_v3.config_manager.cleanup_plugin_config.return_value = None
        self.api_v3.plugin_store_manager.uninstall_plugin.return_value = False

        response = self.client.post(
            '/api/v3/plugins/uninstall',
            data=json.dumps({'plugin_id': 'thing'}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 500)
        # After the file removal returned False, the helper must have
        # written the snapshot back. Inspect save_raw_file_content calls.
        calls = self.api_v3.config_manager.save_raw_file_content.call_args_list
        file_types_written = [c.args[0] for c in calls]
        self.assertIn('main', file_types_written,
                      f"main config was not restored after uninstall failure; calls={calls}")
        # Find the main restore call and confirm our snapshot entry is present.
        for c in calls:
            if c.args[0] == 'main':
                written = c.args[1]
                self.assertIn('thing', written,
                              "main config was written without the restored snapshot entry")
                self.assertEqual(written['thing'], stored_main['thing'])
                break

    def test_file_removal_raising_also_restores_snapshot(self):
        """Same restore path, but triggered by an exception instead of False."""
        stored_main = {'thing': {'enabled': False}}

        def raw_get(file_type):
            if file_type == 'main':
                return dict(stored_main)
            return {}

        self.api_v3.config_manager.get_raw_file_content.side_effect = raw_get
        self.api_v3.config_manager.cleanup_plugin_config.return_value = None
        self.api_v3.plugin_store_manager.uninstall_plugin.side_effect = OSError("rm failed")

        response = self.client.post(
            '/api/v3/plugins/uninstall',
            data=json.dumps({'plugin_id': 'thing'}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 500)
        calls = self.api_v3.config_manager.save_raw_file_content.call_args_list
        self.assertTrue(
            any(c.args[0] == 'main' for c in calls),
            "main config was not restored after uninstall raised",
        )

    def test_happy_path_succeeds(self):
        """Sanity: the transactional rework did not break the happy path."""
        self.api_v3.config_manager.get_raw_file_content.return_value = {}
        self.api_v3.config_manager.cleanup_plugin_config.return_value = None
        self.api_v3.plugin_store_manager.uninstall_plugin.return_value = True

        response = self.client.post(
            '/api/v3/plugins/uninstall',
            data=json.dumps({'plugin_id': 'thing'}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        self.api_v3.plugin_store_manager.uninstall_plugin.assert_called_once_with('thing')


class TestReconcileEndpointPayload(unittest.TestCase):
    """``/plugins/state/reconcile`` must handle weird JSON payloads without
    crashing, and must accept string booleans for ``force``.
    """

    def setUp(self):
        self.client, self.mod = _make_client()
        self.api_v3 = self.mod.api_v3
        # Stub the reconciler so we only test the payload plumbing, not
        # the full reconciliation. We patch StateReconciliation at the
        # module level where the endpoint imports it lazily.
        self._reconciler_instance = MagicMock()
        self._reconciler_instance.reconcile_state.return_value = MagicMock(
            inconsistencies_found=[],
            inconsistencies_fixed=[],
            inconsistencies_manual=[],
            message="ok",
        )
        # Patch the StateReconciliation class where it's imported inside
        # the reconcile endpoint.
        self._patcher = patch(
            'src.plugin_system.state_reconciliation.StateReconciliation',
            return_value=self._reconciler_instance,
        )
        self._patcher.start()
        self.addCleanup(self._patcher.stop)

    def _post(self, body, content_type='application/json'):
        return self.client.post(
            '/api/v3/plugins/state/reconcile',
            data=body,
            content_type=content_type,
        )

    def test_non_object_json_body_does_not_crash(self):
        """A bare string JSON body must not raise AttributeError."""
        response = self._post('"just a string"')
        self.assertEqual(response.status_code, 200)
        # force must default to False.
        self._reconciler_instance.reconcile_state.assert_called_once_with(force=False)

    def test_array_json_body_does_not_crash(self):
        response = self._post('[1, 2, 3]')
        self.assertEqual(response.status_code, 200)
        self._reconciler_instance.reconcile_state.assert_called_once_with(force=False)

    def test_null_json_body_does_not_crash(self):
        response = self._post('null')
        self.assertEqual(response.status_code, 200)
        self._reconciler_instance.reconcile_state.assert_called_once_with(force=False)

    def test_missing_force_key_defaults_to_false(self):
        response = self._post('{}')
        self.assertEqual(response.status_code, 200)
        self._reconciler_instance.reconcile_state.assert_called_once_with(force=False)

    def test_force_true_boolean(self):
        response = self._post(json.dumps({'force': True}))
        self.assertEqual(response.status_code, 200)
        self._reconciler_instance.reconcile_state.assert_called_once_with(force=True)

    def test_force_false_boolean(self):
        response = self._post(json.dumps({'force': False}))
        self.assertEqual(response.status_code, 200)
        self._reconciler_instance.reconcile_state.assert_called_once_with(force=False)

    def test_force_string_false_coerced_correctly(self):
        """``bool("false")`` is ``True`` — _coerce_to_bool must fix that."""
        response = self._post(json.dumps({'force': 'false'}))
        self.assertEqual(response.status_code, 200)
        self._reconciler_instance.reconcile_state.assert_called_once_with(force=False)

    def test_force_string_true_coerced_correctly(self):
        response = self._post(json.dumps({'force': 'true'}))
        self.assertEqual(response.status_code, 200)
        self._reconciler_instance.reconcile_state.assert_called_once_with(force=True)


if __name__ == '__main__':
    unittest.main()
