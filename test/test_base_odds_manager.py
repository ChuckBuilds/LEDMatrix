"""
Tests for src/base_odds_manager.py (BaseOddsManager).

Covers get_odds validation/caching/URL construction, the null-safe
_extract_espn_data fix (ESPN sends explicit JSON nulls for absent sides),
the no_odds sentinel, stale-cache fallback on request failure,
is_odds_available's ML-blind truth table, the fixed format_odds_summary
gate (money-line-only odds now format), get_odds_for_games, and
configuration loading.

No real network: src.base_odds_manager.requests.get is always patched.
"""

from unittest.mock import MagicMock, patch

import pytest
import requests

from src.base_odds_manager import BaseOddsManager


FULL_ITEM = {
    'details': 'DAL -3.5',
    'overUnder': 47.5,
    'spread': -3.5,
    'homeTeamOdds': {'moneyLine': -150, 'current': {'pointSpread': {'value': -3.5}}},
    'awayTeamOdds': {'moneyLine': 130, 'current': {'pointSpread': {'value': 3.5}}},
}

FULL_EXTRACTED = {
    'details': 'DAL -3.5',
    'over_under': 47.5,
    'spread': -3.5,
    'home_team_odds': {'money_line': -150, 'spread_odds': -3.5},
    'away_team_odds': {'money_line': 130, 'spread_odds': 3.5},
}


def _make_response(payload):
    response = MagicMock()
    response.json.return_value = payload
    response.raise_for_status.return_value = None
    return response


@pytest.fixture
def cache_manager():
    cm = MagicMock()
    # A bare MagicMock returns truthy Mocks from every call, so every
    # get_odds() would look like a cache hit. Explicitly wire a miss.
    cm.get_with_auto_strategy.return_value = None
    return cm


@pytest.fixture
def manager(cache_manager):
    return BaseOddsManager(cache_manager)


@pytest.fixture
def mock_get():
    with patch('src.base_odds_manager.requests.get') as m:
        m.return_value = _make_response({'items': [dict(FULL_ITEM)]})
        yield m


# ---------------------------------------------------------------------------
# get_odds
# ---------------------------------------------------------------------------

class TestGetOdds:
    def test_none_sport_raises(self, manager):
        with pytest.raises(ValueError):
            manager.get_odds(None, 'nfl', '1')

    def test_none_league_raises(self, manager):
        with pytest.raises(ValueError):
            manager.get_odds('football', None, '1')

    def test_cache_key_and_url(self, manager, cache_manager, mock_get):
        manager.get_odds('football', 'nfl', '401')

        cache_manager.get_with_auto_strategy.assert_called_once_with(
            'odds_espn_football_nfl_401')
        url = mock_get.call_args[0][0]
        # Event id appears twice: /events/<id>/competitions/<id>/odds
        assert '/events/401/competitions/401/odds' in url
        assert url == ('https://sports.core.api.espn.com/v2/sports/football/'
                       'leagues/nfl/events/401/competitions/401/odds')
        # The number matters less than the property: a single stalled request
        # must not be able to consume the plugin executor's 30s operation
        # budget, since odds are fetched per live game inside update().
        assert mock_get.call_args.kwargs['timeout'] == 5
        assert mock_get.call_args.kwargs['timeout'] < 30

    def test_ncaa_fb_maps_to_college_football(self, manager, mock_get):
        manager.get_odds('football', 'ncaa_fb', '401')

        url = mock_get.call_args[0][0]
        assert '/leagues/college-football/' in url

    def test_unknown_league_passes_through(self, manager, mock_get):
        manager.get_odds('football', 'xfl', '401')

        assert '/leagues/xfl/' in mock_get.call_args[0][0]

    def test_cache_hit_skips_http(self, manager, cache_manager, mock_get):
        cache_manager.get_with_auto_strategy.return_value = {'spread': -3.0}

        result = manager.get_odds('football', 'nfl', '401')

        assert result == {'spread': -3.0}
        mock_get.assert_not_called()

    def test_cached_no_odds_sentinel_returned_verbatim(
            self, manager, cache_manager, mock_get):
        cache_manager.get_with_auto_strategy.return_value = {'no_odds': True}

        result = manager.get_odds('football', 'nfl', '401')

        assert result == {'no_odds': True}
        mock_get.assert_not_called()
        assert manager.is_odds_available(result) is False

    def test_success_caches_extracted_data_with_interval_ttl(
            self, manager, cache_manager, mock_get):
        result = manager.get_odds('football', 'nfl', '401',
                                  update_interval_seconds=100)

        assert result == FULL_EXTRACTED
        cache_manager.set.assert_called_once_with(
            'odds_espn_football_nfl_401', FULL_EXTRACTED, ttl=100)

    def test_no_odds_caches_sentinel(self, manager, cache_manager, mock_get):
        mock_get.return_value = _make_response({'count': 0, 'items': []})

        result = manager.get_odds('football', 'nfl', '401')

        assert result is None
        cache_manager.set.assert_called_once_with(
            'odds_espn_football_nfl_401', {'no_odds': True}, ttl=3600)

    def test_zero_interval_falls_back_to_default(
            self, manager, cache_manager, mock_get):
        # Quirk pin: `update_interval_seconds or self.update_interval`
        # treats an explicit 0 as falsy, so the 3600 default wins.
        manager.get_odds('football', 'nfl', '401', update_interval_seconds=0)

        assert cache_manager.set.call_args.kwargs['ttl'] == 3600

    def test_request_exception_falls_back_to_stale_cache(
            self, manager, cache_manager, mock_get):
        cache_manager.get_with_auto_strategy.side_effect = [
            None, {'stale': True}]
        mock_get.side_effect = requests.exceptions.RequestException('boom')

        result = manager.get_odds('football', 'nfl', '401')

        assert result == {'stale': True}
        assert cache_manager.get_with_auto_strategy.call_count == 2


# ---------------------------------------------------------------------------
# _extract_espn_data
# ---------------------------------------------------------------------------

class TestExtractEspnData:
    def test_full_item_extracts_all_fields(self, manager):
        result = manager._extract_espn_data({'items': [dict(FULL_ITEM)]})
        assert result == FULL_EXTRACTED

    def test_explicit_nulls_do_not_raise(self, manager):
        # Post-fix: ESPN sends explicit JSON nulls for absent sides
        # ("homeTeamOdds": null, "current": null); extraction must not
        # raise and yields None fields.
        payload = {'items': [{
            'homeTeamOdds': None,
            'awayTeamOdds': {'moneyLine': 150, 'current': None},
        }]}

        result = manager._extract_espn_data(payload)

        assert result is not None
        assert result['home_team_odds']['money_line'] is None
        assert result['home_team_odds']['spread_odds'] is None
        assert result['away_team_odds']['money_line'] == 150
        assert result['away_team_odds']['spread_odds'] is None

    def test_valid_empty_response_returns_none(self, manager):
        assert manager._extract_espn_data({'count': 0, 'items': []}) is None

    def test_unexpected_structure_returns_none(self, manager):
        assert manager._extract_espn_data({'unexpected': True}) is None

    def test_item_without_odds_fields_cached_as_data_not_sentinel(
            self, manager, cache_manager, mock_get):
        # Characterization pin: an item with no odds fields still extracts
        # to a truthy dict of all-None values, so get_odds caches it as
        # real data (NOT the no_odds sentinel) — but is_odds_available
        # correctly reports False for it.
        mock_get.return_value = _make_response({'items': [{}]})

        result = manager.get_odds('football', 'nfl', '401')

        assert result == {
            'details': None,
            'over_under': None,
            'spread': None,
            'home_team_odds': {'money_line': None, 'spread_odds': None},
            'away_team_odds': {'money_line': None, 'spread_odds': None},
        }
        cache_manager.set.assert_called_once_with(
            'odds_espn_football_nfl_401', result, ttl=3600)
        assert manager.is_odds_available(result) is False


# ---------------------------------------------------------------------------
# is_odds_available
# ---------------------------------------------------------------------------

class TestIsOddsAvailable:
    def test_none_is_false(self, manager):
        assert manager.is_odds_available(None) is False

    def test_empty_dict_is_false(self, manager):
        assert manager.is_odds_available({}) is False

    def test_no_odds_sentinel_is_false(self, manager):
        assert manager.is_odds_available({'no_odds': True}) is False

    def test_spread_is_true(self, manager):
        assert manager.is_odds_available({'spread': -3.5}) is True

    def test_over_under_is_true(self, manager):
        assert manager.is_odds_available({'over_under': 47.5}) is True

    def test_nested_home_spread_odds_is_true(self, manager):
        assert manager.is_odds_available(
            {'home_team_odds': {'spread_odds': -3.5}}) is True

    def test_nested_away_spread_odds_is_true(self, manager):
        assert manager.is_odds_available(
            {'away_team_odds': {'spread_odds': 3.5}}) is True

    def test_moneyline_only_is_false(self, manager):
        # Pinned ML-blind contract: is_odds_available ignores money lines
        # (its callers decide whether to render an odds widget). Note that
        # format_odds_summary deliberately uses a DIFFERENT gate — it will
        # still format money-line-only odds (see TestFormatOddsSummary).
        ml_only = {
            'home_team_odds': {'money_line': -120},
            'away_team_odds': {'money_line': 100},
        }
        assert manager.is_odds_available(ml_only) is False


# ---------------------------------------------------------------------------
# format_odds_summary (fixed gate: empty / no_odds only)
# ---------------------------------------------------------------------------

class TestFormatOddsSummary:
    def test_moneyline_only_formats(self, manager):
        result = manager.format_odds_summary({
            'home_team_odds': {'money_line': -120},
            'away_team_odds': {'money_line': 100},
        })
        assert result == 'Home ML: -120 | Away ML: 100'

    def test_full_data_formats_all_parts(self, manager):
        result = manager.format_odds_summary(FULL_EXTRACTED)
        assert result == 'Spread: -3.5 | O/U: 47.5 | Home ML: -150 | Away ML: 130'

    def test_none_is_no_odds(self, manager):
        assert manager.format_odds_summary(None) == 'No odds available'

    def test_empty_dict_is_no_odds(self, manager):
        assert manager.format_odds_summary({}) == 'No odds available'

    def test_no_odds_sentinel_is_no_odds(self, manager):
        assert manager.format_odds_summary(
            {'no_odds': True}) == 'No odds available'


# ---------------------------------------------------------------------------
# get_odds_for_games
# ---------------------------------------------------------------------------

class TestGetOddsForGames:
    def test_missing_fields_get_none_odds_without_http(self, manager, mock_get):
        games = [
            {'sport': 'football'},
            {'league': 'nfl'},
            {'id': '9'},
            {},
        ]

        result = manager.get_odds_for_games(games)

        assert all(g['odds'] is None for g in result)
        mock_get.assert_not_called()

    def test_per_game_exception_continues_loop(self, manager, monkeypatch):
        def fake_get_odds(sport, league, event_id,
                          update_interval_seconds=None):
            if event_id == 'bad':
                raise RuntimeError('boom')
            return {'spread': -1.0}

        monkeypatch.setattr(manager, 'get_odds', fake_get_odds)
        games = [
            {'sport': 'football', 'league': 'nfl', 'id': 'bad'},
            {'sport': 'football', 'league': 'nfl', 'id': 'ok'},
        ]

        result = manager.get_odds_for_games(games)

        assert len(result) == 2
        assert result[0]['odds'] is None
        assert result[1]['odds'] == {'spread': -1.0}

    def test_input_dicts_mutated_in_place_and_returned(self, manager, mock_get):
        # Pin: get_odds_for_games mutates the caller's game dicts in place
        # and returns the same objects, not copies.
        game = {'sport': 'football', 'league': 'nfl', 'id': '401'}

        result = manager.get_odds_for_games([game])

        assert result[0] is game
        assert game['odds'] == FULL_EXTRACTED


# ---------------------------------------------------------------------------
# _load_configuration
# ---------------------------------------------------------------------------

class TestLoadConfiguration:
    def test_loads_values_from_config(self, cache_manager):
        config_manager = MagicMock()
        config_manager.get_config.return_value = {
            'base_odds_manager': {
                'update_interval': 100,
                'timeout': 5,
                'cache_ttl': 42,
            }
        }

        manager = BaseOddsManager(cache_manager, config_manager=config_manager)

        assert manager.update_interval == 100
        # Key/attr mismatch pin: the config key is 'timeout' but the
        # attribute is request_timeout.
        assert manager.request_timeout == 5
        assert manager.cache_ttl == 42

    def test_get_config_raising_keeps_defaults(self, cache_manager):
        config_manager = MagicMock()
        config_manager.get_config.side_effect = RuntimeError('boom')

        manager = BaseOddsManager(cache_manager, config_manager=config_manager)

        assert manager.update_interval == 3600
        assert manager.request_timeout == 5
        assert manager.cache_ttl == 1800
