"""Tests that a slow ESPN cannot take a whole plugin update with it.

Odds are fetched per live game from inside SportsLive.update(), with show_odds
defaulting on, and the plugin executor kills an operation at 30s. The odds
request timeout was also 30s, so one stalled request consumed the entire budget
and the update carrying every game's score was killed:

    00:43:43  ERROR  plugin football-scoreboard operation timed out after 30.0s
    01:43:43  ERROR  plugin football-scoreboard operation timed out after 30.0s

Invisible out of season -- preseason week 1 returns a single game -- and a
Sunday slate is around sixteen.
"""

from unittest.mock import Mock

from src.base_odds_manager import BaseOddsManager

PLUGIN_BUDGET = 30.0   # PluginExecutor(default_timeout=30.0)


def _manager(cache=None):
    cache = cache or Mock()
    cache.get_with_auto_strategy.return_value = None
    return BaseOddsManager(cache_manager=cache, config_manager=None)


class TestRequestTimeout:
    def test_leaves_room_in_the_operation_budget(self):
        assert _manager().request_timeout < PLUGIN_BUDGET / 2

    def test_the_timeout_is_the_one_actually_used(self):
        m = _manager()
        import src.base_odds_manager as mod
        real = mod.requests.get
        try:
            mod.requests.get = Mock(side_effect=mod.requests.exceptions.Timeout("x"))
            m.get_odds("football", "nfl", "401")
            assert mod.requests.get.call_args.kwargs["timeout"] == m.request_timeout
        finally:
            mod.requests.get = real


class TestSlowEspnCannotKillTheUpdate:
    def test_one_failure_stops_the_rest_of_the_slate_hitting_the_network(self):
        m = _manager()
        import src.base_odds_manager as mod
        real = mod.requests.get
        calls = {"n": 0}

        def timeout(*a, **k):
            calls["n"] += 1
            raise mod.requests.exceptions.Timeout("timed out")

        try:
            mod.requests.get = timeout
            for i in range(16):          # a full slate, one game at a time
                m.get_odds("football", "nfl", "4018730%02d" % i)
        finally:
            mod.requests.get = real

        assert calls["n"] == 1, (
            "%d games each paid the timeout; the breaker should have stopped "
            "after the first" % calls["n"])

    def test_worst_case_slate_stays_inside_the_budget(self):
        m = _manager()
        assert m.request_timeout * 1 < PLUGIN_BUDGET

    def test_recovery_is_automatic(self):
        m = _manager()
        import src.base_odds_manager as mod
        real_get, real_monotonic = mod.requests.get, mod.time.monotonic
        clock = {"t": 1000.0}
        try:
            mod.time.monotonic = lambda: clock["t"]
            mod.requests.get = Mock(
                side_effect=mod.requests.exceptions.Timeout("timed out"))
            m.get_odds("football", "nfl", "401")
            assert m._skip_network_until > clock["t"], "breaker did not open"

            clock["t"] += 1
            before = mod.requests.get.call_count
            m.get_odds("football", "nfl", "402")
            assert mod.requests.get.call_count == before, "should not have retried"

            clock["t"] += m._FAILURE_COOLDOWN
            m.get_odds("football", "nfl", "403")
            assert mod.requests.get.call_count > before, "never retried"
        finally:
            mod.requests.get, mod.time.monotonic = real_get, real_monotonic

    def test_a_healthy_fetch_clears_the_breaker(self):
        m = _manager()
        m._skip_network_until = 0.0
        m._extract_espn_data = Mock(return_value=None)
        import src.base_odds_manager as mod
        real = mod.requests.get
        try:
            resp = Mock()
            resp.json.return_value = {}
            resp.raise_for_status.return_value = None
            mod.requests.get = Mock(return_value=resp)
            m.get_odds("football", "nfl", "401")
        finally:
            mod.requests.get = real
        assert m._skip_network_until == 0.0

    def test_the_stale_cache_fallback_still_works(self):
        # The failing request must still hand back whatever was cached; only
        # the *subsequent* games skip the network.
        cache = Mock()
        cache.get_with_auto_strategy.side_effect = [None, {"details": "stale"}]
        m = BaseOddsManager(cache_manager=cache, config_manager=None)
        import src.base_odds_manager as mod
        real = mod.requests.get
        try:
            mod.requests.get = Mock(
                side_effect=mod.requests.exceptions.Timeout("timed out"))
            assert m.get_odds("football", "nfl", "401") == {"details": "stale"}
        finally:
            mod.requests.get = real
