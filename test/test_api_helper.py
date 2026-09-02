"""
Tests for src/common/api_helper.py (APIHelper).

Covers rate limiting, cached GETs, ESPN URL/cache-key construction,
session header defaults and per-call merging, the retry adapter, and the
fixed clear_cache() behavior (real CacheManager surface: clear_cache /
delete / list_cache_files, with safe no-ops elsewhere).

No real network: helper.session.get/post are always replaced with mocks.
"""

import types
from unittest.mock import MagicMock, Mock

import pytest
import requests
from freezegun import freeze_time

import src.common.api_helper as api_helper_module
from src.common.api_helper import APIHelper


def _make_response(payload):
    response = MagicMock()
    response.json.return_value = payload
    response.raise_for_status.return_value = None
    return response


@pytest.fixture
def cache():
    cache = MagicMock()
    cache.get.return_value = None
    return cache


@pytest.fixture
def helper(cache):
    helper = APIHelper(cache_manager=cache)
    # Default min interval is 1.0s and would really sleep between requests.
    helper.set_rate_limit(0)
    return helper


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------

class TestRateLimiting:
    def test_sleeps_for_remaining_interval(self, helper, monkeypatch):
        fake_time = MagicMock()
        fake_time.time.side_effect = [102.0, 105.0]
        monkeypatch.setattr(api_helper_module, 'time', fake_time)

        helper.set_rate_limit(5)
        helper._last_request_time = 100.0
        helper._enforce_rate_limit()

        # 2s elapsed of a 5s interval -> sleep the remaining 3s.
        fake_time.sleep.assert_called_once()
        assert fake_time.sleep.call_args[0][0] == pytest.approx(3.0)
        assert helper._last_request_time == 105.0

    def test_no_sleep_when_interval_elapsed(self, helper, monkeypatch):
        fake_time = MagicMock()
        fake_time.time.side_effect = [200.0, 201.0]
        monkeypatch.setattr(api_helper_module, 'time', fake_time)

        helper.set_rate_limit(5)
        helper._last_request_time = 100.0
        helper._enforce_rate_limit()

        fake_time.sleep.assert_not_called()
        assert helper._last_request_time == 201.0


# ---------------------------------------------------------------------------
# get()
# ---------------------------------------------------------------------------

class TestGet:
    def test_cache_hit_skips_request_and_rate_limit(self, helper, cache):
        cache.get.return_value = {'cached': True}
        helper.session.get = Mock()
        rate_spy = Mock()
        helper._enforce_rate_limit = rate_spy

        result = helper.get('https://example.com/api', cache_key='k')

        assert result == {'cached': True}
        helper.session.get.assert_not_called()
        rate_spy.assert_not_called()

    def test_cache_miss_fetches_and_caches_without_ttl(self, helper, cache):
        cache.get.return_value = None
        helper.session.get = Mock(return_value=_make_response({'a': 1}))

        result = helper.get('https://example.com/api', cache_key='k',
                            cache_ttl=999)

        assert result == {'a': 1}
        # Pin the ttl-dropped contract: CacheManager.set is called with
        # (key, data) only — the cache_ttl argument is discarded.
        cache.set.assert_called_once_with('k', {'a': 1})

    def test_request_exception_returns_none_and_caches_nothing(
            self, helper, cache):
        helper.session.get = Mock(
            side_effect=requests.exceptions.RequestException('boom'))

        result = helper.get('https://example.com/api', cache_key='k')

        assert result is None
        cache.set.assert_not_called()

    def test_timeout_zero_falls_back_to_default(self, helper):
        # Quirk pin: `timeout or self.default_timeout` treats an explicit
        # timeout=0 as falsy, so the default (30) is used instead.
        helper.session.get = Mock(return_value=_make_response({}))

        helper.get('https://example.com/api', timeout=0)

        assert helper.session.get.call_args.kwargs['timeout'] == 30

    def test_per_call_headers_merge_over_session_headers(self, helper):
        helper.session.get = Mock(return_value=_make_response({}))

        helper.get('https://example.com/api', headers={'X-Custom': 'yes'})

        sent = helper.session.get.call_args.kwargs['headers']
        # Merged, not replaced: session defaults survive alongside the
        # per-call header.
        assert sent['X-Custom'] == 'yes'
        assert sent['User-Agent'] == (
            'LEDMatrix/1.0 (+https://github.com/ChuckBuilds/LEDMatrix)')
        assert sent['Accept'] == 'application/json'
        # The session's own headers are not polluted by the per-call ones.
        assert 'X-Custom' not in helper.session.headers


# ---------------------------------------------------------------------------
# ESPN helpers
# ---------------------------------------------------------------------------

class TestEspnHelpers:
    @freeze_time('2026-08-07')
    def test_fetch_espn_scoreboard_url_params_and_cache_key(self, helper):
        helper.get = Mock(return_value={'ok': 1})

        result = helper.fetch_espn_scoreboard('football', 'nfl')

        assert result == {'ok': 1}
        helper.get.assert_called_once_with(
            'https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard',
            params={'dates': '20260807', 'limit': 1000},
            cache_key='espn_football_nfl_20260807',
            cache_ttl=300,
        )

    def test_fetch_espn_scoreboard_explicit_date(self, helper):
        helper.get = Mock(return_value=None)

        helper.fetch_espn_scoreboard('basketball', 'nba', date='20250115')

        kwargs = helper.get.call_args.kwargs
        assert kwargs['params'] == {'dates': '20250115', 'limit': 1000}
        assert kwargs['cache_key'] == 'espn_basketball_nba_20250115'

    def test_fetch_espn_standings_url_and_cache_key(self, helper):
        helper.get = Mock(return_value={'ok': 1})

        helper.fetch_espn_standings('football', 'nfl')

        helper.get.assert_called_once_with(
            'https://site.api.espn.com/apis/site/v2/sports/football/nfl/standings',
            cache_key='espn_standings_football_nfl',
            cache_ttl=3600,
        )

    def test_fetch_espn_rankings_url_and_cache_key(self, helper):
        helper.get = Mock(return_value={'ok': 1})

        helper.fetch_espn_rankings('football', 'college-football')

        helper.get.assert_called_once_with(
            'https://site.api.espn.com/apis/site/v2/sports/football/college-football/rankings',
            cache_key='espn_rankings_football_college-football',
            cache_ttl=3600,
        )


# ---------------------------------------------------------------------------
# Session setup
# ---------------------------------------------------------------------------

class TestSessionSetup:
    def test_user_agent_exact(self, helper):
        # Regression guard: ESPN began 403ing other user agents; this exact
        # string must be sent on every request.
        assert helper.session.headers['User-Agent'] == (
            'LEDMatrix/1.0 (+https://github.com/ChuckBuilds/LEDMatrix)')

    def test_retry_adapter_configuration(self):
        helper = APIHelper(cache_manager=None, max_retries=7)

        retries = helper.session.get_adapter('https://x').max_retries
        assert retries.total == 7
        assert {429, 500, 502, 503, 504} <= set(retries.status_forcelist)


# ---------------------------------------------------------------------------
# clear_cache (fixed behavior: real CacheManager surface)
# ---------------------------------------------------------------------------

class TestClearCache:
    def test_no_pattern_uses_clear_cache_method(self):
        manager = types.SimpleNamespace(clear_cache=Mock())
        helper = APIHelper(cache_manager=manager)
        helper.set_rate_limit(0)

        helper.clear_cache()

        manager.clear_cache.assert_called_once_with()

    def test_no_pattern_falls_back_to_clear(self):
        manager = types.SimpleNamespace(clear=Mock())
        helper = APIHelper(cache_manager=manager)
        helper.set_rate_limit(0)

        helper.clear_cache()

        manager.clear.assert_called_once_with()

    def test_no_pattern_manager_without_any_clear_is_noop(self):
        helper = APIHelper(cache_manager=object())
        helper.set_rate_limit(0)

        helper.clear_cache()  # must not raise

    def test_pattern_deletes_only_matching_keys(self):
        manager = types.SimpleNamespace(
            list_cache_files=Mock(return_value=[
                {'key': 'espn_nfl_x'},
                {'key': 'other'},
            ]),
            delete=Mock(),
        )
        helper = APIHelper(cache_manager=manager)
        helper.set_rate_limit(0)

        helper.clear_cache(pattern='espn')

        manager.delete.assert_called_once_with('espn_nfl_x')

    def test_pattern_manager_without_list_cache_files_is_noop(self):
        helper = APIHelper(cache_manager=object())
        helper.set_rate_limit(0)

        helper.clear_cache(pattern='espn')  # must not raise


# ---------------------------------------------------------------------------
# No cache manager
# ---------------------------------------------------------------------------

class TestNoCacheManager:
    def test_all_cache_operations_safe_without_manager(self):
        helper = APIHelper(cache_manager=None)
        helper.set_rate_limit(0)

        assert helper.get_cache('k') is None
        assert helper._get_from_cache('k') is None
        assert helper.set_cache('k', {'a': 1}) is None
        assert helper.clear_cache() is None
        assert helper.clear_cache(pattern='espn') is None
