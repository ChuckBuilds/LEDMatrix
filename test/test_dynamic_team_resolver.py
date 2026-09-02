"""
Tests for src/dynamic_team_resolver.py (DynamicTeamResolver).

Covers dynamic team expansion (AP_TOP_5/10/25), order-preserving dedup,
unknown dynamic-name dropping, rankings parsing, the fixed genuinely
class-shared rankings cache (fetch and clear_cache write through
DynamicTeamResolver._rankings_cache / _cache_timestamp), TTL expiry,
network-failure resilience, and the resolve_dynamic_teams module function.

No real network: src.dynamic_team_resolver.requests.get is always patched.
"""

import types
from unittest.mock import MagicMock, patch

import pytest
import requests

import src.dynamic_team_resolver as dtr_module
from src.dynamic_team_resolver import DynamicTeamResolver, resolve_dynamic_teams


TOP_TEAMS = ['UGA', 'MICH', 'OSU', 'TEX', 'ALA', 'ORE', 'PSU', 'ND', 'FSU', 'OU']


def _rankings_payload(teams=None):
    teams = TOP_TEAMS if teams is None else teams
    return {
        'rankings': [{
            'name': 'AP Top 25',
            'ranks': [
                {'current': i + 1, 'team': {'abbreviation': abbr}}
                for i, abbr in enumerate(teams)
            ],
        }]
    }


def _make_response(payload):
    response = MagicMock()
    response.json.return_value = payload
    response.raise_for_status.return_value = None
    return response


@pytest.fixture(autouse=True)
def reset_class_cache():
    """Reset the CLASS-level shared cache between tests."""
    DynamicTeamResolver._rankings_cache = {}
    DynamicTeamResolver._cache_timestamp = 0
    yield
    DynamicTeamResolver._rankings_cache = {}
    DynamicTeamResolver._cache_timestamp = 0


@pytest.fixture
def mock_get():
    with patch('src.dynamic_team_resolver.requests.get') as m:
        m.return_value = _make_response(_rankings_payload())
        yield m


@pytest.fixture
def resolver():
    return DynamicTeamResolver()


# ---------------------------------------------------------------------------
# resolve_teams basics
# ---------------------------------------------------------------------------

class TestResolveTeamsBasics:
    def test_empty_list_returns_empty_no_http(self, resolver, mock_get):
        assert resolver.resolve_teams([]) == []
        mock_get.assert_not_called()

    def test_no_dynamic_names_passthrough_no_http(self, resolver, mock_get):
        assert resolver.resolve_teams(['UGA', 'AUB', 'LSU']) == [
            'UGA', 'AUB', 'LSU']
        mock_get.assert_not_called()

    def test_expansion_inserted_in_place_order_preserved(
            self, resolver, mock_get):
        result = resolver.resolve_teams(['UGA', 'AP_TOP_5', 'AUB'])

        # UGA is also ranked #1, so dedup keeps its first occurrence; the
        # top-5 expansion lands where AP_TOP_5 appeared, AUB stays after.
        assert result == ['UGA', 'MICH', 'OSU', 'TEX', 'ALA', 'AUB']

    def test_order_preserving_dedup(self, resolver, mock_get):
        result = resolver.resolve_teams(['UGA', 'AP_TOP_5', 'UGA'])

        assert result == ['UGA', 'MICH', 'OSU', 'TEX', 'ALA']
        assert result.count('UGA') == 1
        assert result[0] == 'UGA'


# ---------------------------------------------------------------------------
# AP_TOP_N slicing
# ---------------------------------------------------------------------------

class TestSlicing:
    def test_top_n_counts_and_order(self, resolver, mock_get):
        teams_25 = [f'T{i:02d}' for i in range(1, 26)]
        mock_get.return_value = _make_response(_rankings_payload(teams_25))

        top5 = resolver.resolve_teams(['AP_TOP_5'])
        top10 = resolver.resolve_teams(['AP_TOP_10'])
        top25 = resolver.resolve_teams(['AP_TOP_25'])

        assert top5 == teams_25[:5]
        assert top10 == teams_25[:10]
        assert top25 == teams_25


# ---------------------------------------------------------------------------
# Unknown dynamic-looking names
# ---------------------------------------------------------------------------

class TestUnknownDynamicNames:
    def test_unknown_dynamic_looking_names_dropped(self, resolver, mock_get):
        result = resolver.resolve_teams(
            ['AP_TOP_100', 'TOP_10', 'RANKED_ALL', 'PLAYOFF_TEAMS'])

        assert result == []
        mock_get.assert_not_called()

    def test_top_substring_hazard(self, resolver, mock_get):
        # Hazard pin: _is_potential_dynamic_team matches the substring
        # 'TOP_' anywhere in the (upper-cased) name, so a team literally
        # named 'TOP_GUN' is dropped as an unknown dynamic team too.
        assert resolver.resolve_teams(['TOP_GUN']) == []


# ---------------------------------------------------------------------------
# Rankings parsing
# ---------------------------------------------------------------------------

class TestRankingsParsing:
    def test_drops_zero_rank_and_empty_abbreviation_sorts_ascending(
            self, resolver, mock_get):
        payload = {
            'rankings': [{
                'name': 'AP Top 25',
                'ranks': [
                    {'current': 3, 'team': {'abbreviation': 'C3'}},
                    {'current': 1, 'team': {'abbreviation': 'A1'}},
                    {'current': 0, 'team': {'abbreviation': 'ZERO'}},
                    {'current': 4, 'team': {'abbreviation': ''}},
                    {'current': 2, 'team': {'abbreviation': 'B2'}},
                ],
            }]
        }
        mock_get.return_value = _make_response(payload)

        rankings = resolver._fetch_ncaa_fb_rankings()

        assert list(rankings.keys()) == ['A1', 'B2', 'C3']
        assert list(rankings.values()) == [1, 2, 3]

    def test_empty_rankings_returns_empty_and_caches_nothing(
            self, resolver, mock_get):
        mock_get.return_value = _make_response({'rankings': []})

        assert resolver._fetch_ncaa_fb_rankings() == {}
        # Nothing was cached, so the next call hits HTTP again.
        assert resolver._fetch_ncaa_fb_rankings() == {}
        assert mock_get.call_count == 2


# ---------------------------------------------------------------------------
# Shared class cache (fixed behavior)
# ---------------------------------------------------------------------------

class TestSharedCache:
    def test_cache_shared_across_instances(self, mock_get):
        resolver1 = DynamicTeamResolver()
        resolver1.resolve_teams(['AP_TOP_5'])
        assert mock_get.call_count == 1

        resolver2 = DynamicTeamResolver()
        result = resolver2.resolve_teams(['AP_TOP_5'])

        # Post-fix: the class-level cache serves the second instance with
        # ZERO additional HTTP calls.
        assert result == TOP_TEAMS[:5]
        assert mock_get.call_count == 1

    def test_ttl_expiry_refetches(self, resolver, mock_get, monkeypatch):
        resolver.resolve_teams(['AP_TOP_5'])
        assert mock_get.call_count == 1

        stamp = DynamicTeamResolver._cache_timestamp
        monkeypatch.setattr(
            dtr_module, 'time', types.SimpleNamespace(time=lambda: stamp + 3601))

        resolver.resolve_teams(['AP_TOP_5'])
        assert mock_get.call_count == 2

    def test_clear_cache_through_one_instance_affects_all(self, mock_get):
        resolver1 = DynamicTeamResolver()
        resolver1.resolve_teams(['AP_TOP_5'])
        assert mock_get.call_count == 1

        resolver2 = DynamicTeamResolver()
        resolver2.clear_cache()

        # Post-fix: clear_cache writes through the class, so resolver1
        # must refetch even though resolver2 did the clearing.
        resolver1.resolve_teams(['AP_TOP_5'])
        assert mock_get.call_count == 2

    def test_module_function_benefits_from_class_cache(self, mock_get):
        # resolve_dynamic_teams constructs a fresh resolver per call, but
        # the class-shared cache means only the first call hits HTTP.
        first = resolve_dynamic_teams(['AP_TOP_5'])
        second = resolve_dynamic_teams(['AP_TOP_5'])

        assert first == second == TOP_TEAMS[:5]
        assert mock_get.call_count == 1


# ---------------------------------------------------------------------------
# Failure handling
# ---------------------------------------------------------------------------

class TestFailureHandling:
    def test_network_failure_drops_dynamic_keeps_static_caches_nothing(
            self, resolver, mock_get):
        mock_get.side_effect = [
            requests.exceptions.RequestException('boom'),
            _make_response(_rankings_payload()),
        ]

        result = resolver.resolve_teams(['UGA', 'AP_TOP_5'])

        # Dynamic name silently dropped, static name kept, nothing raises.
        assert result == ['UGA']

        # Nothing was cached on failure: a subsequent call refetches and
        # succeeds.
        result = resolver.resolve_teams(['UGA', 'AP_TOP_5'])
        assert result == ['UGA', 'MICH', 'OSU', 'TEX', 'ALA']
        assert mock_get.call_count == 2


# ---------------------------------------------------------------------------
# sport argument
# ---------------------------------------------------------------------------

class TestSportArgument:
    def test_sport_arg_ignored_for_expansion(self, resolver, mock_get):
        # Pin: the sport argument is effectively ignored — each pattern
        # carries its own sport ('ncaa_fb'), so passing sport='nfl' still
        # expands from the college-football rankings.
        result = resolver.resolve_teams(['AP_TOP_5'], sport='nfl')

        assert result == TOP_TEAMS[:5]
        assert mock_get.call_count == 1
