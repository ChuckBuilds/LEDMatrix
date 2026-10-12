"""src.common.sports_live_fetch: the live poll's request and the odds queue.

The cases follow ledmatrix-plugins' ``scripts/test_live_fetch_odds.py`` (the
tables the family 9 reconcile was checked against); the expected values are
its columns, which agree across all nine scoreboards. The host is the mixin
over core's ``SportsFetchMixin`` (``_needs_previous_day``), with a session
answering like ESPN did on 2026-10-12 (a single date is 200, a range 400) and
a cache that ages entries on a test clock. Nothing reaches the network. The
odds endpoint is held on an event, so "update() never waits" is shown by the
call returning while the request is still open.
"""

import ast
import json
import logging
import threading
import time
from datetime import datetime
from pathlib import Path

import pytest
import requests

from src.common import espn_dates, sports_live_fetch
from src.common import fetch_service as fs
from src.common.fetch_service import FetchService
from src.common.sports_fetch import SportsFetchMixin
from src.common.sports_live_fetch import SportsLiveFetchMixin

LOG = logging.getLogger("test_sports_live_fetch")
SITE = "https://site.api.espn.com/apis/site/v2/sports/"


def _response(url, answer):
    resp = requests.Response()
    resp.url = url
    resp.status_code = answer if isinstance(answer, int) else 200
    resp._content = answer if isinstance(answer, bytes) else json.dumps(
        {} if isinstance(answer, int) else answer).encode()
    return resp


class Espn(requests.Session):
    """Two games a day for a single date; a range is 400, as ESPN answers."""

    def __init__(self, answer=None):
        super().__init__()
        self.answer, self.asked = answer, []

    def get(self, url, params=None, **_kwargs):
        dates = (params or {}).get("dates")
        self.asked.append((url[len(SITE):], dates))
        if dates and "-" in dates:
            return _response(url, 400)
        if isinstance(self.answer, type):
            raise self.answer("refused by the test")
        if self.answer is not None:
            return _response(url, self.answer)
        return _response(url, {"events": [{"id": dates[4:] + s} for s in "ab"]})


class Cache:
    """``get(key, max_age)`` against entries stamped on ``clock``."""

    def __init__(self, clock):
        self.clock, self.store, self.reads = clock, {}, []

    def get(self, key, max_age=None):
        self.reads.append((key, max_age))
        if key not in self.store:
            return None
        data, stamp = self.store[key]
        return data if max_age is None or self.clock() - stamp <= max_age else None

    def set(self, key, data, ttl=None):
        self.store[key] = (data, self.clock())


class Clock:
    def __init__(self):
        self.now = 100_000.0

    def __call__(self):
        return self.now


class Frozen(datetime):
    at = datetime(2026, 10, 12, 10, 0)

    @classmethod
    def now(cls, tz=None):
        return tz.localize(cls.at) if tz is not None else cls.at


class Host(SportsLiveFetchMixin, SportsFetchMixin):
    """A scoreboard SportsCore as far as the family reaches."""

    sport, league, sport_key = "basketball", "nba", "nba"

    def __init__(self, session=None, cache=None, odds=None, show_odds=True):
        self.session, self.headers, self.logger = session or Espn(), {}, LOG
        self.cache_manager = cache
        self.odds_manager, self.show_odds = odds, show_odds
        self.mode_config = {}
        self._games_lock = threading.RLock()


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    clock = Clock()
    monkeypatch.setattr(fs, "_service", FetchService({"rate_limits": {}}))
    monkeypatch.setattr(espn_dates, "_ranges_rejected_until", 0.0)
    monkeypatch.setattr(sports_live_fetch, "datetime", Frozen)
    monkeypatch.setattr(time, "time", clock)
    Frozen.at = datetime(2026, 10, 12, 10, 0)
    return clock


# ---------------------------------------------------------------------------
# The live poll's request
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("hour, asked, key, ids", [
    (10, [("20261012",)], "espn_scoreboard_basketball_nba_20261012", ["1012a", "1012b"]),
    (2, [("20261011-20261012",), ("20261011",), ("20261012",)],
     "espn_scoreboard_basketball_nba_20261011-20261012",
     ["1011a", "1011b", "1012a", "1012b"]),
])
def test_what_the_poll_asks_and_keeps(offline, hour, asked, key, ids):
    Frozen.at = datetime(2026, 10, 12, hour, 0)
    espn, cache = Espn(), Cache(offline)
    data = Host(espn, cache)._fetch_todays_games()
    assert [(d,) for _, d in espn.asked] == asked
    assert {path for path, _ in espn.asked} == {"basketball/nba/scoreboard"}
    assert cache.reads == [(key, 1)]
    assert list(cache.store) == [key]
    assert [e["id"] for e in data["events"]] == ids


def test_college_basketball_is_asked_by_date(offline):
    espn, host = Espn(), Host()
    host.session, host.league = espn, "mens-college-basketball"
    host._fetch_todays_games()
    assert espn.asked == [("basketball/mens-college-basketball/scoreboard", "20261012")]


@pytest.mark.parametrize("seed_age, later, requests_made", [
    (None, 20, [1, 1]),       # every poll asks ESPN; nothing is held 30 s
    (None, 0.5, [1, 0]),      # a copy from this second is the same answer
    (0, None, [0]),           # another reader's copy from this second
    (2, None, [1]),           # ... and one two seconds old is asked again
])
def test_every_poll_is_fresh(offline, seed_age, later, requests_made):
    espn, cache = Espn(), Cache(offline)
    if seed_age is not None:
        cache.store["espn_scoreboard_basketball_nba_20261012"] = (
            {"events": [{"id": "seed"}]}, offline.now - seed_age)
    host, made = Host(espn, cache), []
    for step in ([0] if later is None else [0, later]):
        offline.now += step
        before = len(espn.asked)
        host._fetch_todays_games()
        made.append(len(espn.asked) - before)
    assert made == requests_made


def test_the_old_per_plugin_key_is_not_read(offline):
    cache = Cache(offline)
    cache.store["nba_scoreboard_current"] = ({"events": [{"id": "old"}]}, offline.now)
    data = Host(Espn(), cache)._fetch_todays_games()
    assert [e["id"] for e in data["events"]] == ["1012a", "1012b"]
    assert "nba_scoreboard_current" not in {k for k, _ in cache.reads}


@pytest.mark.parametrize("answer, result, cached", [
    ({}, {"events": []}, False),
    ({"events": None}, {"events": []}, False),
    ({"events": []}, {"events": []}, True),
    (404, None, False),
    (500, None, False),
    (requests.ConnectionError, None, False),
    (requests.Timeout, None, False),
    (b"<html>", None, False),
])
def test_what_a_failed_or_malformed_answer_becomes(offline, answer, result, cached):
    cache = Cache(offline)
    assert Host(Espn(answer), cache)._fetch_todays_games() == result
    assert bool(cache.store) is cached


# ---------------------------------------------------------------------------
# Odds
# ---------------------------------------------------------------------------

class Odds:
    """An odds endpoint held open until ``release()``, counting what is in
    flight and how many odds workers are alive while it answers."""

    def __init__(self, answer="odds", held=True):
        self.answer, self.calls, self.gate = answer, [], threading.Event()
        if not held:
            self.gate.set()
        self.lock, self.active, self.most_active, self.most_workers = threading.Lock(), 0, 0, 0

    def get_odds(self, sport, league, event_id, update_interval_seconds=None):
        workers = sum(t.name == "nba-odds" and t.is_alive() for t in threading.enumerate())
        with self.lock:
            self.calls.append((event_id, update_interval_seconds))
            self.active += 1
            self.most_active = max(self.most_active, self.active)
            self.most_workers = max(self.most_workers, workers)
        self.gate.wait(10)
        with self.lock:
            self.active -= 1
        if isinstance(self.answer, type):
            raise self.answer("odds endpoint failed")
        return {"details": f"{event_id} -3.5"} if self.answer else None

    def release(self):
        self.gate.set()


def settle():
    for thread in threading.enumerate():
        if thread.name == "nba-odds":
            thread.join(10)


def game(gid="g1", **flags):
    return {"id": gid, **flags}


@pytest.mark.parametrize("flags, interval", [({"is_live": True}, 60), ({"is_upcoming": True}, 3600),
                                             ({"is_final": True}, 3600)])
def test_the_call_never_waits_and_the_line_lands_later(flags, interval):
    odds = Odds()
    host, g = Host(odds=odds), game(**flags)
    host._fetch_odds(g)                       # returns while the request is held open
    assert "odds" not in g
    odds.release()
    settle()
    assert g["odds"] == {"details": "g1 -3.5"}
    assert odds.calls == [("g1", interval)]


def test_the_intervals_come_from_the_mode_config():
    odds = Odds(held=False)
    host = Host(odds=odds)
    host.mode_config = {"live_odds_update_interval": 30, "odds_update_interval": 900}
    host._fetch_odds(game("l", is_live=True))
    settle()
    host._fetch_odds(game("u"))
    settle()
    assert odds.calls == [("l", 30), ("u", 900)]


@pytest.mark.parametrize("answer", [None, RuntimeError])
def test_no_line_or_a_failed_request_leaves_the_game_bare(caplog, answer):
    odds = Odds(answer, held=False)
    host, g = Host(odds=odds), game()
    with caplog.at_level(logging.DEBUG, logger=LOG.name):
        host._fetch_odds(g)
        settle()
    assert "odds" not in g and host._odds_worker_busy is False
    assert [r for r in caplog.records if r.levelno >= logging.WARNING] == []


@pytest.mark.parametrize("host", [
    Host(odds=Odds(held=False), show_odds=False),
    Host(odds=None),
], ids=["show_odds off", "no odds manager"])
def test_nothing_is_asked(caplog, host):
    with caplog.at_level(logging.DEBUG, logger=LOG.name):
        host._fetch_odds(game())
    assert not [t for t in threading.enumerate() if t.name == "nba-odds"]
    assert [r.levelname for r in caplog.records] in ([], ["DEBUG"])


def test_a_game_without_an_id_is_not_asked():
    odds = Odds(held=False)
    Host(odds=odds)._fetch_odds({"is_live": True})
    settle()
    assert odds.calls == []


def test_one_batch_per_manager_and_every_dict_gets_the_answer():
    odds = Odds()
    host = Host(odds=odds)
    first, again, other = game("g1"), game("g1"), game("g2")
    with host._games_lock:                   # queued before the worker takes one
        for g in (first, again, other):      # update() rebuilds the dicts
            host._fetch_odds(g)
    odds.release()
    settle()
    assert odds.calls == [("g1", 3600), ("g2", 3600)]
    assert odds.most_workers == 1
    assert first["odds"] == again["odds"] == {"details": "g1 -3.5"}


def test_a_thread_that_will_not_start_is_tried_again(monkeypatch):
    class NoThread:
        def __init__(self, *args, **kwargs):
            pass

        def start(self):
            raise RuntimeError("can't start new thread")

    odds = Odds(held=False)
    host = Host(odds=odds)
    monkeypatch.setattr(sports_live_fetch.threading, "Thread", NoThread)
    host._fetch_odds(game("g1"))
    assert host._odds_worker_busy is False
    monkeypatch.undo()
    host._fetch_odds(game("g2"))
    settle()
    assert [c[0] for c in odds.calls] == ["g1", "g2"]


def test_update_and_display_on_two_threads():
    """Two updaters queueing fresh dicts while a reader walks the games under
    the lock, as display() does: no exception, one worker, one request at a
    time, and every dict ends with its line."""
    odds = Odds(held=False)
    odds.get_odds = _slowed(odds.get_odds)
    host, shown, raised = Host(odds=odds), [], []

    def updater():
        try:
            for n in range(30):
                g = game(f"g{n % 4}")
                with host._games_lock:
                    shown.append(g)
                host._fetch_odds(g)
        except Exception as exc:              # noqa: BLE001
            raised.append(exc)

    def display():
        try:
            for _ in range(300):
                with host._games_lock:
                    [g.get("odds") for g in shown]
        except Exception as exc:              # noqa: BLE001
            raised.append(exc)

    threads = [threading.Thread(target=updater) for _ in range(2)]
    threads.append(threading.Thread(target=display))
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    settle()
    assert raised == []
    assert odds.most_active == 1 and odds.most_workers == 1
    assert all(g.get("odds") for g in shown)


def _slowed(get_odds):
    def slow(**kwargs):
        time.sleep(0.005)
        return get_odds(**kwargs)
    return slow


# ---------------------------------------------------------------------------
# Carrier and host contract
# ---------------------------------------------------------------------------

MEMBERS = ["_drain_odds_queue", "_fetch_odds", "_fetch_todays_games"]


def _self_reads():
    tree = ast.parse(Path(sports_live_fetch.__file__).read_text(encoding="utf-8"))
    reads = {node.attr for node in ast.walk(tree)
             if isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Load)
             and isinstance(node.value, ast.Name) and node.value.id == "self"}
    gets = {node.args[1].value for node in ast.walk(tree)
            if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "getattr"
            and len(node.args) >= 2 and isinstance(node.args[1], ast.Constant)}
    return reads | gets


class TestHostContract:
    def test_the_mixin_carries_only_the_family(self):
        assert sorted(n for n in vars(SportsLiveFetchMixin) if not n.startswith("__")) == MEMBERS

    def test_every_host_read_is_documented(self):
        undocumented = sorted(n for n in _self_reads() - set(MEMBERS)
                              if f"``{n}``" not in sports_live_fetch.__doc__)
        assert undocumented == [], f"read but not in the host contract: {undocumented}"

    def test_no_other_shared_mixin_defines_these(self):
        from src.common import (sports_display_rules, sports_favorites, sports_helpers,
                                sports_rankings, sports_rotation, sports_shared)
        others = [sports_shared.SportsCoreSharedMixin, sports_shared.SportsRecentSharedMixin,
                  sports_shared.SportsLiveSharedMixin, sports_helpers.SportsHelpersMixin,
                  sports_favorites.SportsFavoritesMixin, sports_rotation.SportsRotationMixin,
                  sports_rankings.SportsRankingsMixin, SportsFetchMixin,
                  sports_display_rules.SportsGameRulesMixin]
        assert [m for m in MEMBERS if any(m in vars(o) for o in others)] == []
