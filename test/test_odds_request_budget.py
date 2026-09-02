"""Tests that a slow ESPN cannot take a whole plugin update with it.

Odds are fetched per live game from inside SportsLive.update(), with show_odds
defaulting on, and the plugin executor kills an operation at 30s. The odds
request timeout was also 30s, so one stalled request consumed the entire budget
and the update carrying every game's score was killed:

    00:43:43  ERROR  plugin football-scoreboard operation timed out after 30.0s
    01:43:43  ERROR  plugin football-scoreboard operation timed out after 30.0s

Invisible out of season -- preseason week 1 returns a single game -- and a
Sunday slate is around sixteen.

The request now goes through a session that identifies the caller, so the
tests patch `manager.session.get` rather than the module's `requests.get`.
"""

from unittest.mock import Mock

import requests

from src.base_odds_manager import BaseOddsManager

PLUGIN_BUDGET = 30.0   # PluginExecutor(default_timeout=30.0)


def _manager(cache=None):
    cache = cache or Mock()
    cache.get_with_auto_strategy.return_value = None
    return BaseOddsManager(cache_manager=cache, config_manager=None)


def _timing_out(manager):
    """Point the manager's session at a request that always times out."""
    manager.session.get = Mock(side_effect=requests.exceptions.Timeout("x"))
    return manager.session.get


def _returning(manager, payload):
    resp = Mock()
    resp.json.return_value = payload
    resp.raise_for_status.return_value = None
    manager.session.get = Mock(return_value=resp)
    return manager.session.get


class TestRequestTimeout:
    def test_leaves_room_in_the_operation_budget(self):
        assert _manager().request_timeout < PLUGIN_BUDGET / 2

    def test_the_timeout_is_the_one_actually_used(self):
        m = _manager()
        get = _timing_out(m)
        m.get_odds("football", "nfl", "401")
        assert get.call_args.kwargs["timeout"] == m.request_timeout


class TestIdentifiesItselfToEspn:
    """ESPN 403s python-requests' default agent, and bare custom tokens.

    What it accepts is a token carrying a URL that says who is calling. This
    path used a bare requests.get and so sent the default -- the one thing
    known to be rejected. Everything else in the tree that talks to ESPN
    already sends the header below.
    """

    def test_the_user_agent_names_the_project_and_links_to_it(self):
        ua = _manager().session.headers["User-Agent"]
        assert "python-requests" not in ua
        assert "LEDMatrix" in ua
        assert "github.com/ChuckBuilds/LEDMatrix" in ua

    def test_it_is_the_same_agent_the_rest_of_the_tree_sends(self):
        # Compared against the live value rather than a copied literal, so the
        # two cannot drift apart the next time ESPN moves the goalposts.
        from src.common.api_helper import APIHelper
        assert (_manager().session.headers["User-Agent"]
                == APIHelper().session.headers["User-Agent"])

    def test_the_header_reaches_the_request(self):
        m = _manager()
        get = _returning(m, {})
        m._extract_espn_data = Mock(return_value=None)
        m.get_odds("football", "nfl", "401")
        # Sent via the session, so it applies without being passed per-call.
        assert get.call_count == 1
        assert "User-Agent" in m.session.headers

    def test_no_retry_adapter_multiplies_the_timeout(self):
        # api_helper mounts a retrying adapter; this path must not, or a 5s
        # timeout becomes 15s and the budget fix is undone.
        m = _manager()
        for adapter in m.session.adapters.values():
            retries = getattr(adapter, "max_retries", None)
            assert getattr(retries, "total", 0) in (0, None), (
                "odds session mounts a retrying adapter (total=%r); retries "
                "multiply request_timeout" % getattr(retries, "total", None))


class TestSlowEspnCannotKillTheUpdate:
    def test_one_failure_stops_the_rest_of_the_slate_hitting_the_network(self):
        m = _manager()
        get = _timing_out(m)
        for i in range(16):          # a full slate, one game at a time
            m.get_odds("football", "nfl", "4018730%02d" % i)

        assert get.call_count == 1, (
            "%d games each paid the timeout; the breaker should have stopped "
            "after the first" % get.call_count)

    def test_worst_case_slate_stays_inside_the_budget(self):
        m = _manager()
        assert m.request_timeout * 1 < PLUGIN_BUDGET

    def test_recovery_is_automatic(self):
        m = _manager()
        import src.base_odds_manager as mod
        real_monotonic = mod.time.monotonic
        clock = {"t": 1000.0}
        try:
            mod.time.monotonic = lambda: clock["t"]
            get = _timing_out(m)
            m.get_odds("football", "nfl", "401")
            assert m._skip_network_until > clock["t"], "breaker did not open"

            clock["t"] += 1
            before = get.call_count
            m.get_odds("football", "nfl", "402")
            assert get.call_count == before, "should not have retried"

            clock["t"] += m._FAILURE_COOLDOWN
            m.get_odds("football", "nfl", "403")
            assert get.call_count > before, "never retried"
        finally:
            mod.time.monotonic = real_monotonic

    def test_a_healthy_fetch_clears_the_breaker(self):
        m = _manager()
        m._skip_network_until = 0.0
        m._extract_espn_data = Mock(return_value=None)
        _returning(m, {})
        m.get_odds("football", "nfl", "401")
        assert m._skip_network_until == 0.0

    def test_a_403_opens_the_breaker_rather_than_hammering(self):
        # raise_for_status raises HTTPError, a RequestException -- so a wrong
        # or missing agent backs off instead of 403ing once per game.
        m = _manager()
        resp = Mock()
        resp.raise_for_status.side_effect = requests.exceptions.HTTPError("403")
        m.session.get = Mock(return_value=resp)
        m.get_odds("football", "nfl", "401")
        assert m._skip_network_until > 0.0

    def test_the_stale_cache_fallback_still_works(self):
        # The failing request must still hand back whatever was cached; only
        # the *subsequent* games skip the network.
        cache = Mock()
        cache.get_with_auto_strategy.side_effect = [None, {"details": "stale"}]
        m = BaseOddsManager(cache_manager=cache, config_manager=None)
        _timing_out(m)
        assert m.get_odds("football", "nfl", "401") == {"details": "stale"}
