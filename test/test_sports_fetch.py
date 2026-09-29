"""src.common.sports_fetch: behaviour and host contract.

Ported from the scoreboards' tests of the same methods (football's
test_live_odds_follow_the_rotation.py, test_lookback_only_when_it_can_matter.py
and test_espn_date_ranges.py) against a stub host carrying exactly the
documented contract.
"""

import ast
import logging
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from src.common import espn_dates, sports_fetch
from src.common.sports_fetch import SportsFetchMixin

ET = timezone(timedelta(hours=-5))


class _Response:
    status_code = 200
    content = None

    def __init__(self, data):
        self._data = data

    def json(self):
        return self._data

    def raise_for_status(self):
        pass


class _Session:
    def __init__(self, data=None, error=None):
        self.data, self.error, self.calls = data, error, []

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append((url, dict(params or {}), headers, timeout))
        if self.error:
            raise self.error
        return _Response(self.data)


class _Cache:
    def __init__(self):
        self.sets = []

    def set(self, key, data, **kwargs):
        self.sets.append((key, data, kwargs))


class Host(SportsFetchMixin):
    """The documented contract, and not one attribute more."""

    def __init__(self, session=None):
        self.session = session or _Session(data={"events": []})
        self.headers = {"User-Agent": "test"}
        self.cache_manager = _Cache()
        self.logger = logging.getLogger("test.sports_fetch")
        self._games_lock = threading.RLock()


class TestWantsLiveOdds:
    def test_cold_start_asks_for_every_game(self):
        assert Host()._wants_live_odds({"id": "a"}) is True

    def test_only_the_game_on_screen_and_the_next(self):
        host = Host()
        host.live_games = [{"id": i} for i in "abcd"]
        host.current_game_index = 1
        assert [host._wants_live_odds({"id": i}) for i in "abcd"] == [False, True, True, False]

    def test_the_rotation_schedule_is_followed_and_wraps(self):
        host = Host()
        host.live_games = [{"id": i} for i in "abcd"]
        host._rotation_schedule = ["d", "c", "b", "a"]
        host.current_game_index = 3
        assert [host._wants_live_odds({"id": i}) for i in "abcd"] == [True, False, False, True]

    def test_an_index_past_the_end_starts_at_the_front(self):
        host = Host()
        host.live_games = [{"id": i} for i in "abc"]
        host.current_game_index = 9
        assert [host._wants_live_odds({"id": i}) for i in "abc"] == [True, True, False]

    def test_the_lookahead_is_a_class_setting(self):
        class Wider(Host):
            _LIVE_ODDS_LOOKAHEAD = 2

        host = Wider()
        host.live_games = [{"id": i} for i in "abcd"]
        host.current_game_index = 0
        assert [host._wants_live_odds({"id": i}) for i in "abcd"] == [True, True, True, False]


class TestNeedsPreviousDay:
    def test_before_the_cutoff_yesterday_is_kept(self):
        assert Host()._needs_previous_day(datetime(2026, 1, 15, 5, 59, tzinfo=ET)) is True

    def test_after_it_with_nothing_live_it_is_dropped(self):
        assert Host()._needs_previous_day(datetime(2026, 1, 15, 6, 0, tzinfo=ET)) is False

    def test_a_live_game_from_yesterday_keeps_it(self):
        host = Host()
        host.live_games = [{"start_time_utc": datetime(2026, 1, 15, 3, 0, tzinfo=timezone.utc)}]
        assert host._needs_previous_day(datetime(2026, 1, 15, 12, 0, tzinfo=ET)) is True

    def test_todays_live_game_does_not(self):
        host = Host()
        host.live_games = [{"start_time_utc": datetime(2026, 1, 15, 18, 0, tzinfo=timezone.utc)}]
        assert host._needs_previous_day(datetime(2026, 1, 15, 12, 0, tzinfo=ET)) is False

    @pytest.mark.parametrize("game", [{}, {"start_time_utc": "2026-01-14"}, "not a game"])
    def test_unusable_start_times_are_skipped(self, game):
        host = Host()
        host.live_games = [game]
        assert host._needs_previous_day(datetime(2026, 1, 15, 12, 0, tzinfo=ET)) is False


class TestBackgroundFetchesEspnRanges:
    def test_no_service(self):
        assert Host()._background_fetches_espn_ranges() is False

    @pytest.mark.parametrize("flag,expected", [(True, True), (False, False), (None, False)])
    def test_follows_the_service(self, flag, expected):
        host = Host()
        host.background_service = type("S", (), {"handles_espn_date_ranges": flag})()
        assert host._background_fetches_espn_ranges() is expected

    def test_an_old_service_without_the_flag(self):
        host = Host()
        host.background_service = object()
        assert host._background_fetches_espn_ranges() is False


class TestFetchSeasonDirectly:
    def test_fetches_caches_and_returns(self):
        host = Host(_Session(data={"events": [1, 2]}))
        data = host._fetch_season_directly("http://espn/sb", "20260115", "k", "2026 season")
        assert data == {"events": [1, 2]}
        assert host.cache_manager.sets == [("k", data, {})]
        assert host.session.calls == [
            ("http://espn/sb", {"dates": "20260115", "limit": espn_dates.ESPN_MAX_LIMIT},
             {"User-Agent": "test"}, 30)]

    def test_a_ttl_reaches_the_cache(self):
        host = Host()
        host._fetch_season_directly("http://espn/sb", "20260115", "k", "x", ttl=60)
        assert host.cache_manager.sets[0][2] == {"ttl": 60}

    def test_a_failure_returns_none_and_caches_nothing(self, caplog):
        host = Host(_Session(error=OSError("down")))
        with caplog.at_level(logging.ERROR):
            assert host._fetch_season_directly("http://espn/sb", "20260115", "k", "2026 season") is None
        assert host.cache_manager.sets == []
        assert "Failed to fetch 2026 season schedule" in caplog.text


# ---------------------------------------------------------------------------
# Host contract
# ---------------------------------------------------------------------------

def _self_reads():
    """Every ``self.X`` / ``getattr(self, "X")`` the mixin reads, by parsing it."""
    tree = ast.parse(Path(sports_fetch.__file__).read_text(encoding="utf-8"))
    cls = next(n for n in tree.body
               if isinstance(n, ast.ClassDef) and n.name == "SportsFetchMixin")
    names = set()
    for node in ast.walk(cls):
        if (isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Load)
                and isinstance(node.value, ast.Name) and node.value.id == "self"):
            names.add(node.attr)
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "getattr" and len(node.args) >= 2
                and isinstance(node.args[0], ast.Name) and node.args[0].id == "self"
                and isinstance(node.args[1], ast.Constant)):
            names.add(node.args[1].value)
    return names


class TestHostContract:
    def test_every_host_read_is_documented(self):
        needed = _self_reads() - set(dir(SportsFetchMixin))
        undocumented = sorted(n for n in needed if f"``{n}``" not in sports_fetch.__doc__)
        assert undocumented == [], f"read but not in the host contract: {undocumented}"

    def test_the_mixin_creates_no_attributes_of_its_own(self):
        for name in ("session", "headers", "cache_manager", "logger", "_games_lock"):
            assert not hasattr(SportsFetchMixin, name)
