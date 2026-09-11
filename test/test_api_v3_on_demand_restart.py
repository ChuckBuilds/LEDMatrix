"""Regression test: POST /display/on-demand/start restarting a running
service must not import a name that does not exist.

display.py has `import web_interface.blueprints.api_v3 as _pkg` and reads
mutable, test-patched attributes back through it (`_pkg.time.time()`,
`_pkg._get_starlark_plugin()`, ...) rather than binding them by value, per
the package's own docstring. One spot went further and wrote a genuine
`import` *statement* against that alias --

    import _pkg.time as time_module

-- but `_pkg` is a local name bound by `import ... as _pkg` in this module,
not a real top-level package, so `import _pkg.time` is not something Python
can resolve; it raises ModuleNotFoundError. That line only runs when the
display service is already running and the caller also asked to (re)start
it, so this endpoint failed on exactly the restart path -- the one where a
cache write recording the new on-demand request had already happened.

The route wraps its body in `except Exception`, so the failure reached the
caller as a handled 500 with a generic message, not an unhandled crash --
but a 500 all the same on a request that should have restarted the service
and reported success.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from test._api_v3_test_helpers import api_v3_client, api_v3_module  # noqa: F401,E402

URL = "/api/v3/display/on-demand/start"


@pytest.fixture
def restart_path(api_v3_module):
    """Force the `service_was_running and start_service` branch.

    plugin_manager and config_manager are set to None so the route takes
    the simplest path to that branch rather than tripping over unrelated
    MagicMock plumbing; _ensure_cache_manager, _get_display_service_status,
    _stop_display_service and _ensure_display_service_running are bound by
    value in display.py (see its own docstring), so they are patched on
    that submodule rather than on the package.
    """
    api_v3_module.api_v3.plugin_manager = None
    api_v3_module.api_v3.config_manager = None

    with patch("web_interface.blueprints.api_v3.display._ensure_cache_manager") as ensure_cache, \
         patch("web_interface.blueprints.api_v3.display._get_display_service_status") as get_status, \
         patch("web_interface.blueprints.api_v3.display._stop_display_service") as stop_service, \
         patch("web_interface.blueprints.api_v3.display._ensure_display_service_running") as ensure_running:
        ensure_cache.return_value = MagicMock()
        # Active before the request: service_was_running becomes True.
        get_status.return_value = {"active": True}
        ensure_running.return_value = {"active": True}
        yield {
            "ensure_cache": ensure_cache,
            "get_status": get_status,
            "stop_service": stop_service,
            "ensure_running": ensure_running,
        }


class TestRestartingARunningService:
    def test_it_does_not_500(self, api_v3_client, restart_path):
        response = api_v3_client.post(
            URL, json={"plugin_id": "weather", "start_service": True})
        body = response.get_json()
        assert response.status_code == 200, body
        assert body["status"] == "success", body

    def test_the_service_is_actually_stopped_and_restarted(
            self, api_v3_client, restart_path):
        api_v3_client.post(
            URL, json={"plugin_id": "weather", "start_service": True})
        restart_path["stop_service"].assert_called_once()
        restart_path["ensure_running"].assert_called_once()

    def test_a_service_that_was_not_running_is_not_stopped_first(
            self, api_v3_client, restart_path):
        # The buggy import sits inside `if service_was_running and
        # start_service`, so it only ever fired on the restart path --
        # this is the other side of that branch, unaffected either way,
        # kept here so the branch condition itself stays covered.
        restart_path["get_status"].return_value = {"active": False}
        response = api_v3_client.post(
            URL, json={"plugin_id": "weather", "start_service": True})
        assert response.status_code == 200, response.get_json()
        restart_path["stop_service"].assert_not_called()
