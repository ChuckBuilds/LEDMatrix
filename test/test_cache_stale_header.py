"""A stale cache record is recognised from its header, without parsing it.

The sports plugins cache whole season schedules -- 53MB for MLB, 18MB for NHL.
When one expired, DiskCache.get parsed all of it (~1.8s of orjson.loads on a
Pi 4, GIL held, the whole display frozen) only to find the timestamp too old
and throw the result away. CacheManager.set now writes timestamp and ttl ahead
of the data, and DiskCache.get reads them from the first bytes of the file.
"""

import json
import time
from types import SimpleNamespace

import pytest

from src.cache import disk_cache as disk_cache_module
from src.cache.disk_cache import DiskCache, _stale_from_head
from src.common import json_body


@pytest.fixture
def disk(tmp_path):
    return DiskCache(cache_dir=str(tmp_path))


@pytest.fixture
def parses(monkeypatch):
    """Count full parses of cache files."""
    calls = []
    real = disk_cache_module._loads

    def counting(raw):
        calls.append(len(raw))
        return real(raw)

    monkeypatch.setattr(disk_cache_module, "_loads", counting)
    return calls


def _header_first(age=0.0, ttl=None, events=100):
    record = {"timestamp": time.time() - age}
    if ttl is not None:
        record["ttl"] = ttl
    record["data"] = {"events": [{"id": n, "name": "x" * 50} for n in range(events)]}
    return record


def test_cache_manager_writes_the_header_first(monkeypatch):
    from src.cache_manager import CacheManager
    written = {}
    manager = CacheManager.__new__(CacheManager)
    monkeypatch.setattr(manager, "save_cache",
                        lambda key, record: written.update({key: record}),
                        raising=False)
    CacheManager.set(manager, "k", {"events": []}, ttl=60)
    assert list(written["k"]) == ["timestamp", "ttl", "data"]
    CacheManager.set(manager, "k", {"events": []})
    assert list(written["k"]) == ["timestamp", "data"]


def test_a_stale_record_is_not_parsed(disk, parses):
    disk.set("season", _header_first(age=600))
    assert disk.get("season", max_age=300) is None
    assert parses == []


def test_a_fresh_record_is_parsed_and_returned(disk, parses):
    disk.set("season", _header_first(age=10))
    record = disk.get("season", max_age=300)
    assert record["data"]["events"][0]["id"] == 0
    assert len(parses) == 1


def test_the_entry_ttl_wins_over_max_age(disk, parses):
    disk.set("long", _header_first(age=600, ttl=3600))
    assert disk.get("long", max_age=300) is not None     # ttl says fresh
    disk.set("short", _header_first(age=60, ttl=30))
    parses.clear()
    assert disk.get("short", max_age=300) is None        # ttl says stale
    assert parses == []


def test_no_limit_means_never_stale(disk):
    disk.set("forever", _header_first(age=10 ** 7))
    assert disk.get("forever", max_age=None) is not None


def test_older_files_with_data_first_still_work(disk, parses):
    # Records written before the header moved: parsed in full, as before.
    disk.set("legacy_fresh", {"data": {"v": 1}, "timestamp": time.time()})
    disk.set("legacy_stale", {"data": {"v": 1}, "timestamp": time.time() - 600})
    assert disk.get("legacy_fresh", max_age=300)["data"] == {"v": 1}
    assert disk.get("legacy_stale", max_age=300) is None
    assert len(parses) == 2


@pytest.mark.parametrize("head, stale", [
    (b'{"timestamp":100.0,"data":{}}', True),
    (b'{"timestamp": 100.0, "ttl": 1000, "data": {}}', False),   # stdlib spacing
    (b'{"timestamp":1e2,"ttl":5,"data":1}', True),
    (b'{"timestamp":100.0}', True),
    (b'{"data":{},"timestamp":100.0}', False),                  # unknown layout
    (b'{"timestamp":"100.0","data":{}}', False),                # string: parse it
    (b'', False),
])
def test_reading_the_header(head, stale):
    assert _stale_from_head(head, 300, now=1000.0) is stale


def test_response_json_prefers_orjson_and_falls_back():
    payload = {"events": [1, 2, 3]}
    response = SimpleNamespace(content=json.dumps(payload).encode(),
                               json=lambda: pytest.fail("used the slow path"))
    if json_body.orjson is None:
        pytest.skip("orjson not installed")
    assert json_body.response_json(response) == payload
    # A response object without bytes content (a test double) still works.
    assert json_body.response_json(SimpleNamespace(json=lambda: payload)) == payload
