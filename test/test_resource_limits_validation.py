"""Resource limits are validated before the monitor uses them.

POST /plugins/limits/<id> built ResourceLimits straight from the request
JSON, and get_limits() did ``ResourceLimits(**cached)``. A dataclass does not
check its annotations, so ``{"max_execution_time": "5"}`` was stored as a
string and every later monitored update() of that plugin raised TypeError
comparing a float with it -- and an unknown key in the cached record raised
TypeError on load.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.plugin_system.resource_monitor import (  # noqa: E402
    PluginResourceMonitor,
)
from test._api_v3_test_helpers import api_v3_client, api_v3_module  # noqa: F401,E402


class SharedCache:
    def __init__(self):
        self.entries = {}

    def get(self, key, max_age=None, memory_ttl=None):
        return self.entries.get(key)

    def set(self, key, value, *args, **kwargs):
        self.entries[key] = value

    def delete(self, key):
        self.entries.pop(key, None)


@pytest.fixture
def shared_cache(api_v3_module):
    cache = SharedCache()
    api_v3_module.api_v3.resource_monitor = PluginResourceMonitor(
        cache, enable_monitoring=False)
    return cache


@pytest.mark.parametrize("body", [
    {"max_execution_time": "5"},
    {"max_memory_mb": -1},
    {"max_cpu_percent": True},
    {"warning_threshold": "high"},
    {"max_execution_time": [1]},
])
def test_post_rejects_bad_limits_with_400(api_v3_client, shared_cache, body):
    resp = api_v3_client.post("/api/v3/plugins/limits/weather", json=body)
    assert resp.status_code == 400
    assert resp.get_json()["status"] == "error"
    assert "plugin_limits:weather" not in shared_cache.entries


def test_post_accepts_numbers_and_nulls(api_v3_client, shared_cache):
    resp = api_v3_client.post("/api/v3/plugins/limits/weather", json={
        "max_memory_mb": 50, "max_cpu_percent": None, "max_execution_time": 5.0})
    assert resp.status_code == 200
    data = api_v3_client.get("/api/v3/plugins/limits/weather").get_json()["data"]
    assert data == {"max_memory_mb": 50, "max_cpu_percent": None,
                    "max_execution_time": 5.0, "warning_threshold": 0.8}


@pytest.mark.parametrize("cached", [
    {"max_execution_time": "5"},
    {"max_execution_time": 5.0, "consecutive_failures": 3},
])
def test_bad_cached_limits_do_not_break_monitored_calls(cached):
    cache = MagicMock()
    cache.get.side_effect = lambda key, **kw: cached if key == "plugin_limits:p" else None
    mon = PluginResourceMonitor(cache, enable_monitoring=False)

    # Unknown keys are dropped; a malformed value means "no limits".
    assert mon.monitor_call("p", lambda: "ok") == "ok"
    assert mon.monitor_call("p", lambda: "ok") == "ok"
