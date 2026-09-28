"""
Unit tests for src/plugin_system/testing/mocks.py.

MockCacheManager/MockPluginManager stand in for the real production
managers under the plugin safety harness -- a missing method here isn't a
harness bug in the abstract, it's a plugin silently failing to render
under test (confirmed on ledmatrix-leaderboard, which calls
get_cached_data_with_strategy() and previously hit an AttributeError that
its own broad except swallowed, producing an empty-but-green render).
"""

import pytest

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


class TestDisplayDoublesMatchTheRealDisplayManager:
    """The real DisplayManager has no draw_image(); the doubles keep it so
    existing plugin test suites still pass, but warn, because a plugin that
    calls it passes its tests and then raises AttributeError on the Pi."""

    @staticmethod
    def _real_display_manager(monkeypatch):
        # Without EMULATOR the import needs the hardware rgbmatrix module.
        monkeypatch.setenv("EMULATOR", "true")
        from src.display_manager import DisplayManager
        return DisplayManager

    def test_real_display_manager_has_no_draw_image(self, monkeypatch):
        DisplayManager = self._real_display_manager(monkeypatch)
        assert not hasattr(DisplayManager, 'draw_image')

    def test_mock_draw_image_warns_and_still_records(self):
        from PIL import Image
        from src.plugin_system.testing.mocks import MockDisplayManager

        dm = MockDisplayManager()
        with pytest.warns(DeprecationWarning, match=r"image\.paste\(img, \(x, y\)\)"):
            dm.draw_image(Image.new('RGB', (4, 4)), 1, 2)
        assert dm.draw_calls[-1]['type'] == 'image'

    @pytest.mark.parametrize("cls_name", ["VisualTestDisplayManager", "BoundsCheckingDisplayManager"])
    def test_visual_draw_image_warns_and_still_pastes(self, cls_name):
        from PIL import Image
        import src.plugin_system.testing as testing

        dm = getattr(testing, cls_name)(width=16, height=8)
        with pytest.warns(DeprecationWarning, match="DisplayManager has no such method"):
            dm.draw_image(Image.new('RGB', (2, 2), (0, 0, 255)), 3, 3)
        assert dm.image.getpixel((3, 3)) == (0, 0, 255)

    def test_mock_draw_text_accepts_the_real_signature(self, monkeypatch):
        import inspect
        DisplayManager = self._real_display_manager(monkeypatch)
        from src.plugin_system.testing.mocks import MockDisplayManager

        real = inspect.signature(DisplayManager.draw_text).parameters
        mock = inspect.signature(MockDisplayManager.draw_text).parameters
        for name, param in real.items():
            assert name in mock, name
            assert mock[name].default == param.default, name

        dm = MockDisplayManager()
        dm.draw_text("hi", small_font=True, centered=True)
        assert dm.draw_calls[-1]['text'] == "hi"

    def test_visual_draw_failure_is_logged_at_warning(self, caplog):
        import logging
        from src.plugin_system.testing import VisualTestDisplayManager

        dm = VisualTestDisplayManager(width=16, height=8)
        with caplog.at_level(logging.WARNING):
            dm.draw_image(object(), 0, 0)  # not an image: paste raises
        assert any(r.levelno == logging.WARNING and "Error drawing image" in r.getMessage()
                   for r in caplog.records)
