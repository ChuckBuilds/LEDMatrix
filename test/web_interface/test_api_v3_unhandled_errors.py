"""An exception no api_v3 route catches is answered once, by the blueprint.

Fifty-three routes used to end in a copy of the same catch-all:

    except Exception as e:
        logger.error(..., exc_info=True)
        return jsonify({'status': 'error',
                        'message': 'An error occurred; see logs for details',
                        'details': describe_exception(e)}), 500

They were removed in favour of one errorhandler on the api_v3 blueprint. These
tests pin that the answer did not change: the same status, exactly the same
keys and values, credentials still redacted, and the traceback still logged.

The exact-equality matters. web_interface/app.py's global handler answers with
an extra `error_code: UNKNOWN_ERROR`, and the plugin API client routes a body
that carries an error_code to a different UI path (api_client.js) -- so
"falls through to the global handler" would not have been the same answer.
"""

import logging

import pytest
from flask import Flask
from werkzeug.exceptions import UnsupportedMediaType

from src.web_interface.error_handler import describe_exception
from web_interface.blueprints.api_v3 import api_v3

# A credential in three of the forms describe_exception redacts.
FORCED = RuntimeError(
    "forced failure token=SECRET123 at https://u:pw1@example.com/x?api_key=K1")

# What every removed catch-all returned, written out rather than imported so
# a change to the shared payload cannot also change the expectation.
EXPECTED = {
    'status': 'error',
    'message': 'An error occurred; see logs for details',
    'details': describe_exception(FORCED),
}

MANAGERS = ("config_manager", "plugin_manager", "plugin_store_manager",
            "saved_repositories_manager", "schema_manager", "operation_queue",
            "plugin_state_manager", "operation_history", "cache_manager")


class Boom:
    """A manager that fails on any use -- attribute, truthiness, call."""

    def _raise(self, *args, **kwargs):
        raise FORCED

    __getattr__ = _raise
    __bool__ = _raise
    __call__ = _raise
    __iter__ = _raise
    __len__ = _raise


@pytest.fixture
def exploding_managers(monkeypatch):
    for name in MANAGERS:
        monkeypatch.setattr(api_v3, name, Boom(), raising=False)
    # The WiFi routes build their own manager rather than using one above.
    import src.wifi_manager
    monkeypatch.setattr(src.wifi_manager, "WiFiManager", Boom())


@pytest.fixture
def client(exploding_managers):
    """The blueprint alone, on an app with no error handlers of its own."""
    app = Flask(__name__)
    app.register_blueprint(api_v3, url_prefix="/api/v3")
    return app.test_client()


# A sample of routes whose catch-all was removed, across every module that
# lost one. Each reaches a manager (or WiFiManager) inside what used to be
# the try block.
REMOVED_CATCH_ALLS = [
    ("GET", "/api/v3/config/main", None),
    ("GET", "/api/v3/config/secrets", None),
    ("GET", "/api/v3/display/modes", None),
    ("POST", "/api/v3/display/on-demand/stop", {}),
    ("GET", "/api/v3/cache/list", None),
    ("GET", "/api/v3/plugins/installed", None),
    ("GET", "/api/v3/plugins/health", None),
    ("GET", "/api/v3/plugins/metrics/some-plugin", None),
    ("GET", "/api/v3/plugins/schema?plugin_id=some-plugin", None),
    ("GET", "/api/v3/plugins/store/list", None),
    ("GET", "/api/v3/plugins/saved-repositories", None),
    ("POST", "/api/v3/plugins/install", {"plugin_id": "some-plugin"}),
    ("POST", "/api/v3/plugins/config/reset?plugin_id=some-plugin", {}),
    ("GET", "/api/v3/plugins/limits/some-plugin", None),
    ("GET", "/api/v3/wifi/status", None),
    ("POST", "/api/v3/wifi/disconnect", {}),
]


@pytest.mark.parametrize("method,url,body", REMOVED_CATCH_ALLS,
                         ids=[f"{m} {u}" for m, u, _ in REMOVED_CATCH_ALLS])
def test_the_answer_is_what_the_catch_all_returned(client, caplog, method, url, body):
    with caplog.at_level(logging.ERROR, logger="web_interface.blueprints.api_v3"):
        resp = client.open(url, method=method, json=body)

    assert resp.status_code == 500
    assert resp.get_json() == EXPECTED

    # The promise in the message: the traceback is in the log.
    records = [r for r in caplog.records
               if r.name == "web_interface.blueprints.api_v3"
               and r.levelno >= logging.ERROR and r.exc_info]
    assert records, "the unhandled exception was not logged with its traceback"
    assert records[-1].exc_info[1] is FORCED


def test_credentials_are_redacted_from_the_detail(client):
    body = client.get("/api/v3/plugins/installed").get_json()
    for secret in ("SECRET123", "pw1", "K1"):
        assert secret not in body["details"]
    assert "<redacted>" in body["details"]
    assert body["details"].startswith("RuntimeError: forced failure")


def test_a_client_error_keeps_its_own_status(client):
    """HTTPExceptions subclass Exception; a 415 must not become a 500."""
    resp = client.post("/api/v3/plugins/assets/delete", data="not json",
                       content_type="text/plain")
    assert resp.status_code == 415
    body = resp.get_json()
    assert body == {
        'status': 'error',
        'error_code': 'UNSUPPORTED_MEDIA_TYPE',
        'message': body['message'],
    }
    assert "Content-Type" in body['message']


class TestInTheRealApp:
    """Mounted in web_interface/app.py, beside its global handlers."""

    @pytest.fixture
    def web_app(self):
        import web_interface.app as web_app
        return web_app

    def test_the_blueprint_handler_answers_not_the_global_one(
            self, web_app, exploding_managers):
        resp = web_app.app.test_client().get("/api/v3/plugins/installed")
        assert resp.status_code == 500
        assert resp.get_json() == EXPECTED

    def test_client_errors_read_the_same_as_the_global_handler(
            self, web_app, exploding_managers):
        """The blueprint's 4xx shape must not drift from app.py's."""
        resp = web_app.app.test_client().post(
            "/api/v3/plugins/assets/delete", data="not json",
            content_type="text/plain")
        with web_app.app.test_request_context():
            global_resp, global_status = web_app.handle_exception(
                UnsupportedMediaType(description=resp.get_json()['message']))
        assert resp.status_code == global_status == 415
        assert resp.get_json() == global_resp.get_json()

    def test_global_handler_shape(self, web_app):
        """Everything outside api_v3 still gets app.py's answer."""
        with web_app.app.test_request_context("/somewhere"):
            resp, status = web_app.handle_exception(FORCED)
        body = resp.get_json()
        assert status == 500
        assert body == {
            'status': 'error',
            'error_code': 'UNKNOWN_ERROR',
            'message': 'An error occurred; see logs for details',
            'details': describe_exception(FORCED),
        }
        assert "SECRET123" not in body["details"]


class TestPluginActionStep1:
    """execute_plugin_action's OAuth step-1 handler reports the script's error.

    The route bound a local `logger` in its JSON-parsing arm, which made
    `logger` local to the whole function; every other `logger.error` in it
    then raised UnboundLocalError. The step-1 handler therefore answered
    "UnboundLocalError: cannot access local variable 'logger'" instead of
    whatever the plugin's auth script actually raised.
    """

    @pytest.fixture
    def plugin_dir(self, tmp_path):
        import json
        d = tmp_path / "demo-plugin"
        d.mkdir()
        (d / "manifest.json").write_text(json.dumps({
            "id": "demo-plugin",
            "web_ui_actions": [{"id": "auth", "type": "script",
                                "script": "auth.py", "oauth_flow": True}],
        }), encoding="utf-8")
        (d / "auth.py").write_text(
            "def get_auth_url():\n"
            "    raise RuntimeError('the auth script failed')\n",
            encoding="utf-8")
        return d

    def test_the_script_error_reaches_the_response(self, plugin_dir, monkeypatch):
        from unittest.mock import MagicMock
        manager = MagicMock()
        manager.get_plugin_directory.return_value = str(plugin_dir)
        monkeypatch.setattr(api_v3, "plugin_manager", manager, raising=False)
        app = Flask(__name__)
        app.register_blueprint(api_v3, url_prefix="/api/v3")

        resp = app.test_client().post(
            "/api/v3/plugins/action",
            json={"plugin_id": "demo-plugin", "action_id": "auth"})

        assert resp.status_code == 500
        body = resp.get_json()
        assert body["details"] == "RuntimeError: the auth script failed"
        assert body["message"] == 'An error occurred; see logs for details'
