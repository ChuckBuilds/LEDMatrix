"""Tests that SportsCore's decoded-logo cache is LRU-bounded.

``self._logo_cache`` was a plain dict keyed by team abbreviation, with no
eviction. The entries are decoded RGBA thumbnails sized to display*1.5 -- about
36KB on a 256x64 panel, more for wide wordmarks -- and
assets/sports/ncaa_logos ships 307 of them. A plugin that walked a full league
therefore held the whole league in memory: roughly 11-18MB per manager
instance, and a league runs three of them (live/recent/upcoming) each with its
own cache. On a 1GB Pi 3B+ with ~290MB available that is worth recovering.

The bound has to be LRU rather than "clear when full": the logos on screen
right now are exactly the ones that must not be thrown away.
"""

import logging
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from PIL import Image

# src.base_classes.sports transitively imports the hardware matrix driver.
sys.modules.setdefault("rgbmatrix", MagicMock())

from src.base_classes.sports import SportsCore  # noqa: E402

LOGGER = logging.getLogger("test_sports_logo_cache_bounded")


class _StubSports(SportsCore):
    def _fetch_data(self):
        return None

    def _extract_game_details(self, game_event):
        return None


@pytest.fixture
def host(monkeypatch, tmp_path):
    monkeypatch.setattr(
        SportsCore, "_initialize_logo_dir", lambda self, configured: tmp_path)
    monkeypatch.setattr(
        "src.base_classes.sports.core.get_background_service",
        lambda *args, **kwargs: MagicMock())
    # Never reach for the network: every logo these tests ask for exists.
    monkeypatch.setattr(
        "src.base_classes.sports.core.download_missing_logo",
        lambda *a, **k: None)

    display_manager = MagicMock()
    display_manager.matrix.width = 128
    display_manager.matrix.height = 32
    display_manager.width = 128
    display_manager.height = 32
    display_manager.image = Image.new("RGB", (128, 32))
    cache_manager = MagicMock()
    cache_manager.cache_dir = str(tmp_path)
    instance = _StubSports({"timezone": "UTC"}, display_manager,
                           cache_manager, LOGGER, "nhl")
    instance._logo_dir = tmp_path
    return instance, tmp_path


def _load(host, abbrev):
    """Create a real logo file for `abbrev` and load it through the cache."""
    instance, tmp_path = host
    path = tmp_path / f"{abbrev}.png"
    if not path.exists():
        Image.new("RGBA", (64, 64), (1, 2, 3, 255)).save(path)
    return instance._load_and_resize_logo(abbrev, abbrev, path, None)


class TestTheCacheIsBounded:
    def test_it_never_exceeds_the_limit(self, host):
        instance, _ = host
        limit = instance._LOGO_CACHE_MAX
        for i in range(limit + 40):
            assert _load(host, f"T{i}") is not None
        # The regression: this grew to limit + 40, and for NCAA to 307.
        assert len(instance._logo_cache) == limit

    def test_the_limit_is_smaller_than_a_real_league(self, host):
        """ncaa_logos ships 307 files; the cap has to be well under that."""
        instance, _ = host
        assert instance._LOGO_CACHE_MAX < 307


class TestEvictionIsLRUNotArbitrary:
    def test_the_least_recently_used_goes_first(self, host):
        instance, _ = host
        limit = instance._LOGO_CACHE_MAX
        for i in range(limit):
            _load(host, f"T{i}")
        _load(host, "NEW")
        assert "T0" not in instance._logo_cache, "oldest should have been evicted"
        assert "NEW" in instance._logo_cache

    def test_touching_an_entry_saves_it(self, host):
        instance, _ = host
        limit = instance._LOGO_CACHE_MAX
        for i in range(limit):
            _load(host, f"T{i}")
        _load(host, "T0")               # a cache hit -- T0 is on screen again
        _load(host, "NEW")              # forces one eviction

        assert "T0" in instance._logo_cache, "a logo in use was thrown away"
        assert "T1" not in instance._logo_cache, "T1 was the true LRU entry"


class TestHitsStillAvoidDiskWork:
    def test_a_second_request_returns_the_cached_object(self, host, monkeypatch):
        instance, _ = host
        first = _load(host, "TB")

        def _boom(*a, **k):
            raise AssertionError("cache hit re-opened the file from disk")

        monkeypatch.setattr(Image, "open", _boom)
        assert _load(host, "TB") is first
