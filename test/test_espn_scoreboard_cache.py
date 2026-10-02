"""One cache key per ESPN scoreboard (fetch service stage 2).

- every core caller names a scoreboard with the same key;
- the keys each consumer used before are read as a fallback;
- a shared entry is never returned older than the reader's own max_age,
  whoever wrote it and whatever ttl they stored (checked against a real
  CacheManager, whose stored ttl otherwise wins over the reader's);
- two consumers of one scoreboard cost one request;
- the counters see it.

No network: sessions are fakes, and the fetch service is a fresh one per test.
"""

import json
import logging
import threading
import time
from datetime import date, datetime

import pytest
import requests
from requests.structures import CaseInsensitiveDict

from src.common import espn_dates
from src.common import fetch_service as fs
from src.common.espn_dates import (
    ESPN_MAX_LIMIT,
    espn_scoreboard_cache_key,
    espn_scoreboard_cache_key_for_url,
    espn_scoreboard_url,
    get_espn_scoreboard,
    read_espn_scoreboard_cache,
    store_espn_scoreboard_cache,
)
from src.common.fetch_service import FetchService, plugin_scope
from src.common.sports_fetch import SportsFetchMixin

NFL_URL = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard"


# --- fakes ---------------------------------------------------------------------------

class Clock:
    def __init__(self):
        self.t = 1000.0

    def now(self):
        return self.t


def _response(body, url, max_age=None):
    response = requests.Response()
    response.status_code = 200
    response._content = json.dumps(body).encode()
    response.headers = CaseInsensitiveDict(
        {"Cache-Control": f"max-age={max_age}"} if max_age else {})
    response.url = url
    response.encoding = "utf-8"
    return response


class Espn(requests.Session):
    """A Session answering every scoreboard GET with ``{"events": [<dates>]}``."""

    def __init__(self, max_age=None, fail=None):
        super().__init__()
        self.calls = []
        self.max_age = max_age
        self.fail = fail
        self._lock = threading.Lock()

    def get(self, url, **kwargs):
        with self._lock:
            self.calls.append((url, dict(kwargs.get("params") or {})))
        if self.fail:
            raise self.fail
        dates = (kwargs.get("params") or {}).get("dates", "current")
        return _response({"events": [{"id": dates}]}, url, self.max_age)


class RecordCache:
    """The CacheManager surface these helpers use, records and all."""

    def __init__(self):
        self.records = {}
        self.deleted = []

    def set(self, key, data, ttl=None):
        record = {"timestamp": time.time()}
        if ttl is not None:
            record["ttl"] = ttl
        record["data"] = data
        self.records[key] = record

    def put(self, key, data, age, ttl=None):
        self.set(key, data, ttl)
        self.records[key]["timestamp"] -= age

    def get_cached_data(self, key, max_age=300, memory_ttl=None):
        return self.records.get(key)

    def get(self, key, max_age=300, memory_ttl=None):
        record = self.records.get(key)
        return record["data"] if record else None

    def delete(self, key):
        self.deleted.append(key)
        self.records.pop(key, None)


@pytest.fixture
def service(monkeypatch):
    svc = FetchService({"rate_limits": {}})
    monkeypatch.setattr(fs, "_service", svc)
    return svc


def _totals(svc):
    return svc.snapshot()["totals"]


# --- the key -------------------------------------------------------------------------

class TestCanonicalKey:

    def test_one_spelling_for_one_scoreboard(self):
        key = "espn_scoreboard_football_nfl_20261004"
        assert espn_scoreboard_cache_key("football", "nfl", "20261004") == key
        assert espn_scoreboard_cache_key(" Football ", "NFL", "20261004") == key
        assert espn_scoreboard_cache_key("football", "nfl", date(2026, 10, 4)) == key
        assert espn_scoreboard_cache_key("football", "nfl", datetime(2026, 10, 4, 23, 59)) == key
        assert espn_scoreboard_cache_key("football", "nfl", ("20261004", date(2026, 10, 4))) == key
        assert espn_scoreboard_cache_key_for_url(NFL_URL, "20261004") == key
        assert espn_scoreboard_cache_key_for_url(NFL_URL + "?limit=500", "20261004") == key

    @pytest.mark.parametrize("dates,suffix", [
        (None, "current"),
        ("", "current"),
        ("2026", "2026"),
        ("202610", "202610"),
        ("20260925-20261016", "20260925-20261016"),
        ((date(2026, 9, 25), date(2026, 10, 16)), "20260925-20261016"),
    ])
    def test_every_dates_form_espn_takes(self, dates, suffix):
        assert espn_scoreboard_cache_key("soccer", "eng.1", dates) == f"espn_scoreboard_soccer_eng.1_{suffix}"

    @pytest.mark.parametrize("sport,league,dates", [
        ("football", "nfl", "2026-10-04"),
        ("football", "nfl", "tomorrow"),
        ("football", "nfl", ("20261004",)),
        ("football", "nfl", True),
        ("football", "", "20261004"),
        ("foot ball", "nfl", "20261004"),
        ("football", "nfl/../x", "20261004"),
    ])
    def test_anything_else_is_refused_not_guessed(self, sport, league, dates):
        with pytest.raises(ValueError):
            espn_scoreboard_cache_key(sport, league, dates)

    def test_a_url_that_is_not_a_scoreboard_has_no_key(self):
        assert espn_scoreboard_cache_key_for_url(
            "https://site.api.espn.com/apis/site/v2/sports/football/nfl/teams") is None

    def test_the_url_it_names(self):
        assert espn_scoreboard_url("Football", "college-football") == (
            "https://site.api.espn.com/apis/site/v2/sports/football/college-football/scoreboard")


class Host(SportsFetchMixin):
    def __init__(self, session, cache):
        self.session = session
        self.headers = {"User-Agent": "test"}
        self.cache_manager = cache
        self.logger = logging.getLogger("test.espn_cache")
        self._games_lock = threading.RLock()
        self.sport = "football"
        self.league = "nfl"


class TestEveryCallerUsesTheKey:
    """The core helpers that cache a scoreboard all write the same key."""

    DAY = "20261004"
    KEY = "espn_scoreboard_football_nfl_20261004"

    def test_get_espn_scoreboard(self, service):
        cache = RecordCache()
        get_espn_scoreboard(Espn(), "football", "nfl", self.DAY, cache_manager=cache)
        assert list(cache.records) == [self.KEY]

    def test_api_helper(self, service):
        from src.common.api_helper import APIHelper

        cache = RecordCache()
        helper = APIHelper(cache_manager=cache)
        helper.set_rate_limit(0)
        helper.session = Espn()
        helper.fetch_espn_scoreboard("football", "nfl", date=self.DAY)
        assert list(cache.records) == [self.KEY]

    def test_the_scoreboard_mixin(self, service):
        cache = RecordCache()
        host = Host(Espn(), cache)
        assert host._schedule_cache_key(self.DAY) == self.KEY
        host._fetch_season_directly(NFL_URL, self.DAY, None, "today")
        assert list(cache.records) == [self.KEY]

    def test_the_mixin_without_a_scoreboard_url(self, service):
        cache = RecordCache()
        Host(Espn(), cache)._fetch_season_directly(
            "https://example.test/feed", self.DAY, None, "today")
        assert list(cache.records) == [self.KEY]

    def test_an_explicit_key_is_still_the_callers(self, service):
        cache = RecordCache()
        Host(Espn(), cache)._fetch_season_directly(NFL_URL, self.DAY, "mine", "today")
        assert list(cache.records) == ["mine"]


# --- reading ---------------------------------------------------------------------------

class TestRead:

    KEY = "espn_scoreboard_football_nfl_20261004"
    OLD = "scoreboard_data_football_nfl_20261004"

    def test_a_fresh_canonical_entry(self, service):
        cache = RecordCache()
        cache.put(self.KEY, {"events": ["new"]}, age=10)
        assert read_espn_scoreboard_cache(cache, self.KEY, 30) == {"events": ["new"]}
        assert (_totals(service)["cache_hits"], _totals(service)["legacy_cache_hits"]) == (1, 0)

    def test_the_old_key_is_read_after_an_upgrade(self, service):
        cache = RecordCache()
        cache.put(self.OLD, {"events": ["old"]}, age=10)
        assert read_espn_scoreboard_cache(cache, self.KEY, 30, [self.OLD]) == {"events": ["old"]}
        assert (_totals(service)["cache_hits"], _totals(service)["legacy_cache_hits"]) == (1, 1)

    def test_the_canonical_key_wins_over_an_old_one(self, service):
        cache = RecordCache()
        cache.put(self.OLD, {"events": ["old"]}, age=1)
        cache.put(self.KEY, {"events": ["new"]}, age=20)
        assert read_espn_scoreboard_cache(cache, self.KEY, 30, [self.OLD]) == {"events": ["new"]}

    def test_a_stale_old_key_is_a_miss_too(self, service):
        cache = RecordCache()
        cache.put(self.OLD, {"events": ["old"]}, age=31)
        assert read_espn_scoreboard_cache(cache, self.KEY, 30, [self.OLD]) is None
        assert _totals(service)["cache_hits"] == 0

    def test_age_is_judged_against_an_injected_clock(self, service):
        cache = RecordCache()
        cache.records[self.KEY] = {"timestamp": 5000.0, "data": {"events": []}}
        assert read_espn_scoreboard_cache(cache, self.KEY, 30, now=5030.0) == {"events": []}
        assert read_espn_scoreboard_cache(cache, self.KEY, 30, now=5030.5) is None

    def test_never_older_than_the_readers_ttl_whatever_the_writer_stored(self, service):
        # A writer that stored ttl=3600 must not make a 30 s reader take an
        # entry a minute old (CacheManager itself would let the ttl win).
        cache = RecordCache()
        cache.put(self.KEY, {"events": []}, age=60, ttl=3600)
        assert read_espn_scoreboard_cache(cache, self.KEY, 30) is None
        assert read_espn_scoreboard_cache(cache, self.KEY, 90) == {"events": []}

    @pytest.mark.parametrize("max_age", [0, -5])
    def test_no_max_age_no_read(self, service, max_age):
        cache = RecordCache()
        cache.put(self.KEY, {"events": []}, age=0)
        assert read_espn_scoreboard_cache(cache, self.KEY, max_age) is None

    def test_a_broken_cache_is_a_miss(self, service):
        class Broken:
            def get_cached_data(self, *a, **k):
                raise OSError("disk gone")
        assert read_espn_scoreboard_cache(Broken(), self.KEY, 30) is None
        assert read_espn_scoreboard_cache(None, self.KEY, 30) is None

    def test_a_cache_without_records(self, service):
        class Plain:
            def get(self, key, max_age=None):
                return {"events": [key, max_age]}
        assert read_espn_scoreboard_cache(Plain(), self.KEY, 29.5) == {"events": [self.KEY, 30]}


class TestWithARealCacheManager:
    """The same promise against CacheManager, memory and disk tiers."""

    KEY = "espn_scoreboard_football_nfl_20261004"

    @pytest.fixture
    def cm(self, tmp_path):
        from src.cache.disk_cache import DiskCache
        from src.cache.memory_cache import MemoryCache
        from src.cache_manager import CacheManager

        cm = CacheManager()
        cm._disk_cache_component = DiskCache(cache_dir=str(tmp_path))
        cm._memory_cache_component = MemoryCache()
        return cm

    def _age_on_disk(self, cm, seconds):
        path = cm._disk_cache_component.get_cache_path(self.KEY)
        with open(path, encoding="utf-8") as fh:
            record = json.load(fh)
        record["timestamp"] = time.time() - seconds
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(record, fh)
        cm._memory_cache_component.clear()

    def test_a_writers_long_ttl_does_not_outlast_the_readers(self, cm, service):
        cm.set(self.KEY, {"events": [1]}, ttl=3600)
        self._age_on_disk(cm, 60)
        assert cm.get(self.KEY, max_age=30) == {"events": [1]}  # what a plain read does
        assert read_espn_scoreboard_cache(cm, self.KEY, 30) is None
        assert read_espn_scoreboard_cache(cm, self.KEY, 120) == {"events": [1]}

    def test_a_copy_loaded_into_memory_keeps_its_real_age(self, cm, service):
        store_espn_scoreboard_cache(cm, self.KEY, {"events": [1]})
        self._age_on_disk(cm, 60)
        assert read_espn_scoreboard_cache(cm, self.KEY, 120) == {"events": [1]}  # now in memory
        assert read_espn_scoreboard_cache(cm, self.KEY, 30) is None

    def test_the_canonical_entry_stores_no_ttl(self, cm, service):
        store_espn_scoreboard_cache(cm, self.KEY, {"events": []})
        path = cm._disk_cache_component.get_cache_path(self.KEY)
        with open(path, encoding="utf-8") as fh:
            assert "ttl" not in json.load(fh)


# --- fetching through the cache ----------------------------------------------------------------

class TestGetEspnScoreboard:

    def test_a_miss_fetches_a_whole_page_and_caches_it(self, service):
        cache, espn = RecordCache(), Espn()
        data = get_espn_scoreboard(espn, "football", "nfl", "20261004", cache_manager=cache)
        assert data == {"events": [{"id": "20261004"}]}
        assert espn.calls == [(NFL_URL, {"dates": "20261004", "limit": ESPN_MAX_LIMIT})]
        assert "ttl" not in cache.records["espn_scoreboard_football_nfl_20261004"]

    def test_a_hit_sends_nothing(self, service):
        cache, espn = RecordCache(), Espn()
        get_espn_scoreboard(espn, "football", "nfl", "20261004", cache_manager=cache)
        get_espn_scoreboard(espn, "football", "nfl", "20261004", cache_manager=cache, max_age=60)
        assert len(espn.calls) == 1
        assert _totals(service)["cache_hits"] == 1

    def test_max_age_zero_always_fetches_and_still_caches(self, service):
        cache, espn = RecordCache(), Espn()
        get_espn_scoreboard(espn, "football", "nfl", "20261004", cache_manager=cache, max_age=0)
        get_espn_scoreboard(espn, "football", "nfl", "20261004", cache_manager=cache, max_age=0)
        assert len(espn.calls) == 2
        assert "espn_scoreboard_football_nfl_20261004" in cache.records

    def test_the_current_scoreboard_sends_no_dates(self, service):
        espn = Espn()
        get_espn_scoreboard(espn, "football", "nfl", cache_manager=RecordCache())
        assert espn.calls == [(NFL_URL, {"limit": ESPN_MAX_LIMIT})]

    def test_a_range_is_fetched_in_chunks_once_espn_rejects_it(self, service, monkeypatch):
        monkeypatch.setattr(espn_dates, "_ranges_rejected_until", time.monotonic() + 60)
        cache, espn = RecordCache(), Espn()
        data = get_espn_scoreboard(espn, "football", "nfl", "20261003-20261004", cache_manager=cache)
        assert [e["id"] for e in data["events"]] == ["20261003", "20261004"]
        assert list(cache.records) == ["espn_scoreboard_football_nfl_20261003-20261004"]

    def test_a_failure_raises_and_caches_nothing(self, service):
        cache = RecordCache()
        with pytest.raises(requests.ConnectionError):
            get_espn_scoreboard(Espn(fail=requests.ConnectionError("down")), "football", "nfl",
                                "20261004", cache_manager=cache)
        assert cache.records == {}

    def test_no_cache_manager_just_fetches(self, service):
        espn = Espn()
        get_espn_scoreboard(espn, "football", "nfl", "20261004")
        get_espn_scoreboard(espn, "football", "nfl", "20261004")
        assert len(espn.calls) == 2

    def test_the_response_cache_is_bounded_by_the_callers_ttl(self, monkeypatch):
        clock = Clock()
        svc = FetchService({"rate_limits": {}}, clock=clock.now)
        monkeypatch.setattr(fs, "_service", svc)
        espn = Espn(max_age=450)
        get_espn_scoreboard(espn, "football", "nfl", "20261004", max_age=0)
        clock.t += 20
        get_espn_scoreboard(espn, "football", "nfl", "20261004", max_age=10)
        assert len(espn.calls) == 2              # 20 s old > the caller's 10
        clock.t += 5
        get_espn_scoreboard(espn, "football", "nfl", "20261004", max_age=10)
        assert len(espn.calls) == 2              # 5 s old, and ESPN said 450
        assert svc.snapshot()["totals"]["memo_hits"] == 1


class TestTwoConsumersOneFetch:
    """A scoreboard's live poll and odds-ticker asking for the same day."""

    def test_the_second_consumer_is_served_from_the_first_ones_copy(self, service):
        cache, espn = RecordCache(), Espn()
        with plugin_scope("football-scoreboard"):
            live = get_espn_scoreboard(espn, "football", "nfl", "20261004",
                                       cache_manager=cache, max_age=0)
        with plugin_scope("odds-ticker"):
            ticker = get_espn_scoreboard(
                espn, "football", "nfl", "20261004", cache_manager=cache, max_age=300,
                legacy_keys=["scoreboard_data_football_nfl_20261004"])
        assert ticker == live
        assert len(espn.calls) == 1
        snap = service.snapshot()
        assert snap["plugins"]["odds-ticker"]["cache_hits"] == 1
        assert snap["plugins"]["odds-ticker"]["requests"] == 0
        assert snap["plugins"]["football-scoreboard"]["requests"] == 1

    def test_concurrent_misses_go_to_espn_once(self, service):
        gate = threading.Event()

        class Slow(Espn):
            def get(self, url, **kwargs):
                gate.wait(5)
                return super().get(url, **kwargs)

        espn, results = Slow(), []
        threads = [threading.Thread(target=lambda: results.append(get_espn_scoreboard(
            espn, "football", "nfl", "20261004", cache_manager=RecordCache(), max_age=60)))
            for _ in range(2)]
        for thread in threads:
            thread.start()
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            with service._lock:
                if any(f.waiters for f in service._inflight.values()):
                    break
            time.sleep(0.005)
        gate.set()
        for thread in threads:
            thread.join(5)
        assert len(espn.calls) == 1 and results[0] == results[1]


# --- the scoreboards' schedule window ----------------------------------------------------------

class TestCachedSchedule:

    KEY = "espn_scoreboard_football_nfl_20260925-20261016"
    YESTERDAY = "espn_scoreboard_football_nfl_20260924-20261015"
    OLD = "nfl_schedule_window_7_14"

    def test_the_canonical_copy(self, service):
        cache = RecordCache()
        cache.put(self.KEY, {"events": ["new"]}, age=0)
        assert Host(Espn(), cache)._cached_schedule(self.KEY, [self.OLD]) == {"events": ["new"]}
        assert cache.deleted == []

    def test_the_old_key_after_an_upgrade_counted_as_legacy(self, service):
        cache = RecordCache()
        cache.put(self.OLD, {"events": ["old"]}, age=0)
        assert Host(Espn(), cache)._cached_schedule(self.KEY, [self.OLD]) == {"events": ["old"]}
        totals = _totals(service)
        assert (totals["legacy_cache_hits"], totals["cache_hits"]) == (1, 0)

    def test_a_miss_retires_yesterdays_window(self, service):
        cache = RecordCache()
        cache.put(self.YESTERDAY, {"events": []}, age=0)
        assert Host(Espn(), cache)._cached_schedule(self.KEY) is None
        assert cache.deleted == [self.YESTERDAY]
        assert self.YESTERDAY not in cache.records

    def test_a_key_that_is_not_a_window_retires_nothing(self, service):
        cache = RecordCache()
        Host(Espn(), cache)._cached_schedule("espn_scoreboard_football_nfl_20261004")
        Host(Espn(), cache)._cached_schedule(self.OLD)
        assert cache.deleted == []

    def test_a_cache_without_delete(self, service):
        class NoDelete(RecordCache):
            delete = None
        assert Host(Espn(), NoDelete())._cached_schedule(self.KEY) is None
