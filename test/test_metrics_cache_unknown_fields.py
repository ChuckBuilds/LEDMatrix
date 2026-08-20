"""A malformed metrics cache entry must not take every plugin down with it.

`ResourceMetrics(**cached)` raises TypeError on a single unexpected key, and
that exception escapes into plugin_manager, which reports it per plugin as
"plugin <id> operation failed". Every plugin fails and the plugin system never
finishes initialising -- the health endpoint reports
`plugin_system: not_initialized` while the display itself keeps running.

Seen on a live rig, once per plugin, continuously:

    ERROR - src.plugin_system.plugin_manager - plugin geochron operation failed:
    ResourceMetrics.__init__() got an unexpected keyword argument
    'consecutive_failures'

`consecutive_failures` belongs to plugin_health, not to metrics. How a
health-shaped record came to sit under a plugin_metrics key on that machine is
not established -- a restored backup that mixed two machines' caches is the
likeliest explanation, and the same rig had one restored onto it -- but a
loader that turns one bad cache entry into a total outage is the part worth
fixing. plugin_health already repairs its own records field by field rather
than trusting what is on disk.
"""
import logging
from dataclasses import fields
from unittest.mock import MagicMock

import pytest

from src.plugin_system.resource_monitor import PluginResourceMonitor, ResourceMetrics


class _Cache:
    def __init__(self, payload=None):
        self.payload = payload

    def get(self, key, max_age=None, memory_ttl=None, **kwargs):
        return self.payload

    def set(self, key, data, ttl=None, **kwargs):
        pass


def _monitor(payload):
    m = PluginResourceMonitor(cache_manager=_Cache(payload))
    m.logger = logging.getLogger("test")
    return m


#: What the rig actually had under the metrics key.
HEALTH_SHAPED = {
    "consecutive_failures": 0, "circuit_state": "closed",
    "circuit_opened_time": None, "half_open_start_time": None,
    "last_error": None, "last_failure_time": None,
    "last_success_time": 1_700_000_000.0, "total_failures": 0,
    "total_successes": 42,
}


def test_a_health_record_under_the_metrics_key_does_not_raise():
    """The exact failure: it must degrade, not take the plugin system down."""
    monitor = _monitor(HEALTH_SHAPED)
    metrics = monitor.get_metrics(" plugin-a".strip())
    assert isinstance(metrics, ResourceMetrics)


def test_recognised_fields_in_a_mixed_record_are_kept():
    """Dropping the record wholesale would lose real history unnecessarily."""
    mixed = dict(HEALTH_SHAPED, call_count=7, memory_mb=12.5)
    metrics = _monitor(mixed).get_metrics("plugin-b")
    assert metrics.call_count == 7
    assert metrics.memory_mb == 12.5


def test_a_clean_record_still_loads_unchanged():
    clean = {f.name: 3 for f in fields(ResourceMetrics)}
    metrics = _monitor(clean).get_metrics("plugin-c")
    for name in (f.name for f in fields(ResourceMetrics)):
        assert getattr(metrics, name) == 3


def test_unknown_fields_are_named_in_the_log(caplog):
    """Silently discarding them would hide a real schema change."""
    with caplog.at_level(logging.WARNING):
        _monitor(HEALTH_SHAPED).get_metrics("plugin-d")
    # getMessage(), not .message: the latter is only populated once a handler
    # formats the record, so the obvious spelling silently never matches.
    assert any("consecutive_failures" in r.getMessage() for r in caplog.records), \
        caplog.text


@pytest.mark.parametrize("payload", ["a string", 42, ["a", "list"]])
def test_a_non_mapping_cache_entry_does_not_raise(payload):
    metrics = _monitor(payload).get_metrics("plugin-e")
    assert isinstance(metrics, ResourceMetrics)


def test_values_of_the_wrong_type_do_not_raise():
    """A dataclass will accept these, but a later float() on them would not."""
    metrics = _monitor({"call_count": "not a number"}).get_metrics("plugin-f")
    assert isinstance(metrics, ResourceMetrics)
