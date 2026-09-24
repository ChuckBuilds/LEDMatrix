"""GET /api/v3/health: the plugin count is real, and a failed check is logged.

The plugin check counted ``plugin_manager.get_available_plugins()``, which
PluginManager does not have; a hasattr guard turned that into a permanent 0.
Each check that fails answers "see logs for details", so it has to log.
"""

import logging
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from test._api_v3_test_helpers import api_v3_client, api_v3_module  # noqa: F401,E402

URL = "/api/v3/health"


@pytest.fixture(autouse=True)
def _no_systemctl(monkeypatch):
    monkeypatch.setattr("web_interface.blueprints.api_v3.misc._get_display_service_status",
                        lambda: {"active": True})


def _checks(client):
    response = client.get(URL)
    assert response.status_code == 200, response.get_json()
    return response.get_json()["data"]["checks"]


def test_plugin_count_is_the_number_of_discovered_plugins(api_v3_client, api_v3_module):
    api_v3_module.api_v3.plugin_manager.plugin_manifests = {
        "clock": {"id": "clock"}, "weather": {"id": "weather"}, "stocks": {"id": "stocks"},
    }

    check = _checks(api_v3_client)["plugin_system"]

    assert check == {"status": "operational", "plugin_count": 3}


def test_plugin_count_discovers_when_nothing_is_discovered_yet(api_v3_client, api_v3_module):
    pm = api_v3_module.api_v3.plugin_manager
    pm.plugin_manifests = {}

    def discover():
        pm.plugin_manifests = {"clock": {"id": "clock"}}
    pm.discover_plugins.side_effect = discover

    assert _checks(api_v3_client)["plugin_system"]["plugin_count"] == 1


def test_a_failed_config_check_is_logged(api_v3_client, api_v3_module, caplog):
    api_v3_module.api_v3.config_manager.load_config.side_effect = OSError("disk gone")

    with caplog.at_level(logging.WARNING):
        check = _checks(api_v3_client)["config_file"]

    assert check["error"] == "see logs for details"
    logged = [r for r in caplog.records if "config file" in r.getMessage()]
    assert logged and logged[0].exc_info and "disk gone" in str(logged[0].exc_info[1])


def test_a_failed_plugin_check_is_logged(api_v3_client, api_v3_module, caplog, monkeypatch):
    def boom():
        raise RuntimeError("manifests unreadable")
    monkeypatch.setattr("web_interface.blueprints.api_v3.misc._discovered_plugin_manifests", boom)

    with caplog.at_level(logging.WARNING):
        check = _checks(api_v3_client)["plugin_system"]

    assert check["status"] == "error"
    logged = [r for r in caplog.records if "count plugins" in r.getMessage()]
    assert logged and logged[0].exc_info


def test_a_failed_hardware_check_is_logged(api_v3_client, api_v3_module, caplog, monkeypatch):
    def getmtime(_path):
        raise PermissionError("denied")
    fake_os = SimpleNamespace(path=SimpleNamespace(exists=lambda _p: True, getmtime=getmtime))
    monkeypatch.setattr("web_interface.blueprints.api_v3.misc.os", fake_os)

    with caplog.at_level(logging.WARNING):
        check = _checks(api_v3_client)["hardware"]

    assert check["status"] == "unknown"
    logged = [r for r in caplog.records if "snapshot" in r.getMessage()]
    assert logged and logged[0].exc_info
