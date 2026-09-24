"""GET /plugins/health/<id> and /plugins/metrics/<id> read the display
service's latest state, not the web process's first snapshot.

The display service writes health and metrics to the shared cache; the web
process only reads them. Its tracker and monitor keep what they read first in
memory, so without ``force_reload`` the per-plugin routes kept answering with
that first read while the list routes (which pass it) moved on.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.plugin_system.plugin_health import PluginHealthTracker  # noqa: E402
from src.plugin_system.resource_monitor import PluginResourceMonitor  # noqa: E402
from test._api_v3_test_helpers import api_v3_client, api_v3_module  # noqa: F401,E402


class SharedCache:
    """The on-disk cache both processes see, reduced to a dict."""

    def __init__(self):
        self.entries = {}

    def get(self, key, max_age=None, memory_ttl=None):
        return self.entries.get(key)

    def set(self, key, value, *args, **kwargs):
        self.entries[key] = value


@pytest.fixture
def shared_cache(api_v3_module):
    cache = SharedCache()
    pm = api_v3_module.api_v3.plugin_manager
    pm.health_tracker = PluginHealthTracker(cache)
    pm.resource_monitor = PluginResourceMonitor(cache)
    return cache


def test_health_reflects_failures_recorded_after_the_first_read(api_v3_client, shared_cache):
    first = api_v3_client.get("/api/v3/plugins/health/weather").get_json()["data"]
    assert first["total_failures"] == 0

    display_side = PluginHealthTracker(shared_cache)
    display_side.record_failure("weather", RuntimeError("api down"))
    display_side.record_failure("weather", RuntimeError("api down"))

    later = api_v3_client.get("/api/v3/plugins/health/weather").get_json()["data"]
    assert later["total_failures"] == 2


def test_metrics_reflect_calls_recorded_after_the_first_read(api_v3_client, shared_cache):
    first = api_v3_client.get("/api/v3/plugins/metrics/weather").get_json()["data"]
    assert first["call_count"] == 0

    shared_cache.set("plugin_metrics:weather", {
        "memory_mb": 12.5, "cpu_percent": 3.0, "execution_time": 0.2,
        "call_count": 40, "total_execution_time": 8.0,
        "max_execution_time": 0.5, "min_execution_time": 0.1,
        "last_update_time": 1000.0,
    })

    later = api_v3_client.get("/api/v3/plugins/metrics/weather").get_json()["data"]
    assert later["call_count"] == 40
