"""Asking a scoreboard whether it has a strip must not build its PIL image.

SportsScrollDisplay.display_scroll_frame (every frame) and has_cached_content
asked ``scroll_helper.cached_image``, a property that builds the strip's PIL
image from ``cached_array`` when the helper deferred it (after a patch,
append or trim) and keeps it, so the strip ends up held twice. They now ask
``has_strip()``, which answers from the helper's bookkeeping. These pin down
that the answer is the same as ``bool(cached_image)`` in every state, and
that the image is not built.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.modules.setdefault("rgbmatrix", MagicMock())

from src.common.sports_scroll import SportsScrollDisplay  # noqa: E402

W, H = 128, 32


class _Panel:
    matrix = None
    refresh_hz = 100.0

    def __init__(self):
        self.width, self.height = W, H
        self.image = None
        self.frames = 0

    def set_scrolling_state(self, scrolling, frame_hold=1):
        pass

    def update_display(self):
        self.frames += 1


class _Display(SportsScrollDisplay):
    SCROLL_LEAGUE_KEYS = ("nfl",)

    def prepare_scroll_content(self, games, game_type, leagues, rankings_cache=None):
        cards = [Image.new("RGB", (40, H), (200, 0, 0)) for _ in games]
        self.scroll_helper.create_scrolling_image(cards, item_gap=8, element_gap=0)
        return bool(cards)


@pytest.fixture
def display():
    return _Display(_Panel(), {})


def _deferred(display):
    """A strip whose PIL image the helper has dropped, to build on read."""
    display.prepare_scroll_content([1, 2, 3], "recent", ["nfl"])
    helper = display.scroll_helper
    helper.patch_columns(W, np.zeros((H, 4, 3), dtype=np.uint8))
    assert helper.__dict__.get("_cached_image") is None
    assert helper.__dict__.get("_image_source") is not None
    return helper


class TestTheImageIsNotBuilt:
    def test_has_cached_content(self, display):
        helper = _deferred(display)
        assert display.has_cached_content() is True
        assert helper.__dict__.get("_cached_image") is None

    def test_display_scroll_frame(self, display):
        helper = _deferred(display)
        for _ in range(5):
            assert display.display_scroll_frame() is True
        assert display.display_manager.frames == 5
        assert helper.__dict__.get("_cached_image") is None


def _states():
    """(name, set-up) for every state a helper's strip can be in."""
    def none(d):
        pass

    def placeholder(d):  # create_scrolling_image([]): one blank panel
        d.scroll_helper.create_scrolling_image([])

    def built(d):
        d.prepare_scroll_content([1, 2], "recent", ["nfl"])

    def set_image(d):
        d.scroll_helper.set_scrolling_image(Image.new("RGB", (300, H)))

    def deferred(d):
        _deferred(d)

    def cleared(d):
        built(d)
        d.scroll_helper.clear_cache()

    def assigned_none(d):
        built(d)
        d.scroll_helper.cached_image = None

    return [none, placeholder, built, set_image, deferred, cleared, assigned_none]


@pytest.mark.parametrize("setup", _states(), ids=lambda f: f.__name__)
def test_same_answer_as_reading_the_image(display, setup):
    setup(display)
    answer = display.has_cached_content()
    assert answer is bool(display.scroll_helper.cached_image)


def test_a_helper_without_has_strip_is_asked_the_old_way(display):
    """A plugin's own helper or a test double: cached_image decides."""
    class _OldHelper:
        cached_image = None

    display.scroll_helper = _OldHelper()
    assert display.has_cached_content() is False
    assert display.display_scroll_frame() is False
    display.scroll_helper.cached_image = Image.new("RGB", (10, H))
    assert display.has_cached_content() is True
