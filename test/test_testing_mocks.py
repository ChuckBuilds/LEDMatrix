"""
Unit tests for src/plugin_system/testing/mocks.py.

MockCacheManager/MockPluginManager stand in for the real production
managers under the plugin safety harness -- a missing method here isn't a
harness bug in the abstract, it's a plugin silently failing to render
under test (confirmed on ledmatrix-leaderboard, which calls
get_cached_data_with_strategy() and previously hit an AttributeError that
its own broad except swallowed, producing an empty-but-green render).
"""

from src.plugin_system.testing.mocks import MockCacheManager


class TestMockCacheManagerStrategyMethod:
    def test_get_cached_data_with_strategy_returns_cached_value(self):
        cm = MockCacheManager()
        cm.set("standings_nfl", {"teams": ["KC", "BUF"]})
        result = cm.get_cached_data_with_strategy("standings_nfl", "sports_live")
        assert result == {"teams": ["KC", "BUF"]}

    def test_get_cached_data_with_strategy_returns_none_when_missing(self):
        cm = MockCacheManager()
        assert cm.get_cached_data_with_strategy("missing_key") is None

    def test_get_cached_data_with_strategy_defaults_data_type(self):
        cm = MockCacheManager()
        cm.set("k", "v")
        assert cm.get_cached_data_with_strategy("k") == "v"

    def test_calls_are_tracked(self):
        cm = MockCacheManager()
        cm.get_cached_data_with_strategy("k", "sports_live")
        assert cm.get_cached_data_with_strategy_calls == [{"key": "k", "data_type": "sports_live"}]

    def test_save_cache_is_readable_via_strategy_lookup(self):
        cm = MockCacheManager()
        cm.save_cache("standings_nfl", {"teams": ["KC", "BUF"]})
        assert cm.get_cached_data_with_strategy("standings_nfl") == {"teams": ["KC", "BUF"]}

    def test_reset_clears_strategy_call_tracking(self):
        cm = MockCacheManager()
        cm.get_cached_data_with_strategy("k", "sports_live")
        cm.reset()
        assert cm.get_cached_data_with_strategy_calls == []
