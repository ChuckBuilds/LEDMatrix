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


@pytest.mark.parametrize("bad", [
    {"call_count": "not a number"},
    {"memory_mb": None},
    {"execution_time": {"nested": "junk"}},
    {"min_execution_time": ["a", "list"]},
])
def test_values_of_the_wrong_type_fall_back_to_usable_defaults(bad):
    """isinstance() alone was not enough.

    A dataclass does not enforce its annotations, so the bad value was simply
    stored and the old assertion passed -- then monitor_call() raised
    "can only concatenate str (not \"int\") to str" on the next call. The
    metrics must come back *usable*, not merely constructed.
    """
    monitor = _monitor(bad)
    metrics = monitor.get_metrics("plugin-f")
    assert isinstance(metrics, ResourceMetrics)

    field_name = next(iter(bad))
    assert isinstance(getattr(metrics, field_name), (int, float)), \
        f"{field_name} came back as {getattr(metrics, field_name)!r}"

    # The real proof: arithmetic on the loaded metrics must not explode.
    metrics.call_count += 1
    metrics.total_execution_time += 0.5
    metrics.update_average_execution_time()


def test_a_numeric_string_is_accepted_rather_than_discarded():
    """JSON round-trips can widen an int to a string; that is recoverable."""
    metrics = _monitor({"call_count": "7"}).get_metrics("plugin-g")
    assert metrics.call_count == 7
