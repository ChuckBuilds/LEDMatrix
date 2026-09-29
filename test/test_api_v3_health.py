"""GET /api/v3/health: the plugin count is real, and a failed check is logged.

The plugin check counted ``plugin_manager.get_available_plugins()``, which
PluginManager does not have; a hasattr guard turned that into a permanent 0.
Each check that fails answers "see logs for details", so it has to log.
"""

import json
import logging
import sys
import time
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


@pytest.fixture(autouse=True)
def heartbeat(tmp_path, monkeypatch):
    """The display's heartbeat file, somewhere private; absent until written."""
    from src import display_watchdog
    path = tmp_path / "display-heartbeat.json"
    monkeypatch.setattr(display_watchdog, "HEARTBEAT_PATH", str(path))

    def write(age):
        path.write_text(json.dumps({"pid": 1, "mono": time.monotonic() - age,
                                    "wall": time.time() - age}))
    return write


@pytest.fixture
def fresh_preview(tmp_path, monkeypatch):
    """A just-written preview frame, so only the heartbeat decides the verdict."""
    from web_interface import display_preview
    snapshot = tmp_path / "preview.png"
    snapshot.write_bytes(b"png")
    monkeypatch.setattr(display_preview, "SNAPSHOT_PATH", str(snapshot))


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


# -- the display's render-loop heartbeat -----------------------------------------
#
# The preview frame's age said nothing about a panel frozen by a render thread
# stuck in a plugin; the heartbeat is written by that thread itself.

def _health(client):
    response = client.get(URL)
    assert response.status_code == 200, response.get_json()
    return response.get_json()["data"]


def test_a_fresh_heartbeat_is_a_running_display_loop(api_v3_client, heartbeat, fresh_preview):
    heartbeat(age=3)

    data = _health(api_v3_client)

    assert data["checks"]["display_loop"]["status"] == "running"
    assert 2 <= data["checks"]["display_loop"]["heartbeat_age_seconds"] < 10
    assert data["status"] == "healthy"


def test_a_stale_heartbeat_is_a_stalled_display_loop(api_v3_client, heartbeat, fresh_preview):
    """Service active, preview recent, and still the panel is frozen."""
    heartbeat(age=300)

    data = _health(api_v3_client)

    assert data["checks"]["display_loop"]["status"] == "stalled"
    assert data["checks"]["display_loop"]["heartbeat_age_seconds"] >= 299
    assert data["status"] == "degraded"


def test_no_heartbeat_falls_back_to_the_older_checks(api_v3_client, fresh_preview):
    """The dev server, the emulator, Windows, or a display without the
    feature: absence is not a failure, and the verdict is what it was."""
    data = _health(api_v3_client)

    assert data["checks"]["display_loop"]["status"] == "not_reported"
    assert data["checks"]["hardware"]["status"] == "connected"
    assert data["status"] == "healthy"


def test_an_unreadable_heartbeat_is_reported_not_raised(api_v3_client, monkeypatch, caplog):
    from src import display_watchdog

    def boom(_path):
        raise RuntimeError("bad heartbeat")
    monkeypatch.setattr(display_watchdog, "read_heartbeat", boom)

    with caplog.at_level(logging.WARNING):
        check = _checks(api_v3_client)["display_loop"]

    assert check["status"] == "unknown"
    assert any("heartbeat" in r.getMessage() for r in caplog.records)
