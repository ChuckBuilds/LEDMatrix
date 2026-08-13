"""
Shared scaffolding for api_v3 blueprint tests.

Not a test module (the leading underscore keeps pytest from collecting
it). It is the pytest-fixture equivalent of ``_make_client()`` in
test_uninstall_and_reconcile_endpoint.py, which is unittest-style and
requires ``self.addCleanup``.

The api_v3 blueprint keeps its managers as attributes on a module-level
singleton, not in Flask app state, so replacing them with mocks leaks
into every later test that imports api_v3 unless the originals are put
back. ``api_v3_client`` snapshots and restores them around each test.
"""

from unittest.mock import MagicMock

import pytest
from flask import Flask


# Every manager attribute the blueprint reads. Anything missing here keeps
# whatever a previously-run test left on the singleton.
API_V3_MANAGER_ATTRS = (
    'config_manager', 'plugin_manager', 'plugin_store_manager',
    'plugin_state_manager', 'saved_repositories_manager', 'schema_manager',
    'operation_queue', 'operation_history', 'cache_manager',
)

_SENTINEL = object()


def build_app(blueprint):
    app = Flask(__name__)
    app.config['TESTING'] = True
    app.config['SECRET_KEY'] = 'test'
    app.register_blueprint(blueprint, url_prefix='/api/v3')
    return app


@pytest.fixture
def api_v3_module():
    """The api_v3 module with every manager replaced by a MagicMock.

    Restores the original attributes afterwards. Tests point individual
    managers at real objects (a ConfigManager over tmp_path, say) or set
    them to None to exercise the not-initialized branches.
    """
    from web_interface.blueprints import api_v3 as module

    originals = {
        name: getattr(module.api_v3, name, _SENTINEL)
        for name in API_V3_MANAGER_ATTRS
    }
    for name in API_V3_MANAGER_ATTRS:
        setattr(module.api_v3, name, MagicMock())
    # Default to the direct path; queue tests opt in explicitly.
    module.api_v3.operation_queue = None

    yield module

    for name, original in originals.items():
        if original is _SENTINEL:
            if hasattr(module.api_v3, name):
                try:
                    delattr(module.api_v3, name)
                except AttributeError:
                    pass
        else:
            setattr(module.api_v3, name, original)


@pytest.fixture
def api_v3_client(api_v3_module):
    """Flask test client wired to the mocked blueprint."""
    return build_app(api_v3_module.api_v3).test_client()
