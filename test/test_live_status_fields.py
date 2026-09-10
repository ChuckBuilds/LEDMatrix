"""Tests for the metrics the live status stream reports.

Two gaps made a struggling 1GB Pi look healthy on the dashboard:

* ``disk_used_percent`` was the literal ``0`` in the SSE generator, so every
  consumer of the live stream displayed 0% disk no matter how full the card was.
  /api/v3/system/status computed it properly, but the live view -- the one people
  actually watch -- did not.

* ``memory_available_mb`` was absent. /api/v3/system/status carries it with a
  comment explaining that MemAvailable, not used%, is what separates a board
  that is fine from one about to fail fork(). The live stream omitted the one
  number that predicts the failure.

A wrong number is worse than a missing one, so an unreadable disk now reports
None (the UI renders '--') rather than a confident 0.

These import web_interface.system_metrics rather than web_interface.app on
purpose: importing the app constructs a Flask application and a CacheManager,
and the latter claims the cache directory with a cleanup thread -- which broke
test_cache_cleanup_thread_ownership and the starlark route tests when this file
first reached for the generator directly.
"""

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from web_interface.system_metrics import collect_system_metrics  # noqa: E402


@pytest.fixture
def metrics(monkeypatch):
    """collect_system_metrics() with psutil's readings under our control."""
    def _collect(disk_percent=12.0, available_bytes=512 * 1024 * 1024,
                 used_percent=67.7, raise_disk=False):
        import psutil
        if raise_disk:
            monkeypatch.setattr(psutil, "disk_usage",
                                lambda _p: (_ for _ in ()).throw(OSError("no such mount")))
        else:
            monkeypatch.setattr(psutil, "disk_usage",
                                lambda _p: SimpleNamespace(percent=disk_percent))
        monkeypatch.setattr(psutil, "virtual_memory",
                            lambda: SimpleNamespace(percent=used_percent,
                                                    available=available_bytes))
        return collect_system_metrics()
    return _collect


class TestDiskUsageIsReal:
    def test_reported_value_comes_from_the_filesystem(self, metrics):
        # The regression: this field was the literal 0.
        assert metrics(disk_percent=91.4)["disk_used_percent"] == 91.4

    def test_a_full_disk_is_not_reported_as_zero(self, metrics):
        assert metrics(disk_percent=99.9)["disk_used_percent"] != 0

    def test_unreadable_disk_is_null_not_a_confident_zero(self, metrics):
        # null renders as '--'; 0 would read as "plenty of room".
        assert metrics(raise_disk=True)["disk_used_percent"] is None


class TestMemAvailableIsReported:
    def test_mem_available_is_present_and_in_mb(self, metrics):
        got = metrics(available_bytes=292 * 1024 * 1024)["memory_available_mb"]
        assert got == pytest.approx(292.0, abs=0.1)

    def test_the_1gb_danger_case_is_visible(self, metrics):
        """67% used can be fine or nearly out of memory; only MemAvailable says."""
        m = metrics(used_percent=67.7, available_bytes=40 * 1024 * 1024)
        assert m["memory_used_percent"] == 67.7
        assert m["memory_available_mb"] == pytest.approx(40.0, abs=0.1)


class TestEveryPredictiveFieldIsPresent:
    def test_nothing_the_dashboard_needs_is_missing(self, metrics):
        m = metrics()
        for field in ("cpu_percent", "cpu_temp", "memory_used_percent",
                      "memory_available_mb", "disk_used_percent"):
            assert field in m, f"{field} missing from collected metrics"

    def test_absent_psutil_still_yields_every_key(self, monkeypatch):
        """The no-psutil fallback must not drop fields the UI reads."""
        import builtins
        real_import = builtins.__import__

        def _no_psutil(name, *args, **kwargs):
            if name == "psutil":
                raise ImportError("psutil missing")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", _no_psutil)
        m = collect_system_metrics()
        for field in ("cpu_percent", "cpu_temp", "memory_used_percent",
                      "memory_available_mb", "disk_used_percent"):
            assert field in m
        assert m["disk_used_percent"] is None
