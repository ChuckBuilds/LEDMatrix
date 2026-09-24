"""Names two rarely-run api_v3 paths call must exist.

Both slipped through because nothing exercised them: the Pixlet editor's
stop route only restarts the display after a SIGKILL, and the Starlark
device-location resolver only builds a cache manager when the web app has
not set one. Either raised NameError when it finally ran.
"""

from unittest.mock import patch

from test._api_v3_test_helpers import api_v3_module  # noqa: F401


def test_the_editor_stop_route_can_restart_the_display():
    from web_interface.blueprints.api_v3 import starlark

    assert callable(starlark._run_systemctl_command)


def test_the_device_location_resolver_builds_without_a_cache_manager(api_v3_module):
    pkg = api_v3_module
    with patch.object(pkg.api_v3, 'cache_manager', None, create=True), \
            patch.object(pkg, '_starlark_device_location', None):
        resolver = pkg._get_starlark_device_location()
    assert resolver.cache_manager is None
