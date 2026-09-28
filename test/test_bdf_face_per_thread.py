"""BDF faces must not be shared between threads.

src/common/bdf_font.load_bdf_face caches ``freetype.Face`` objects per thread
because FreeType does not allow two threads to use one face at once
(``load_char`` rewrites its glyph slot). FontManager.font_cache and
element_style._font_cache are process-wide, and used to cache the returned
face for every thread -- so the display thread and a plugin update thread
drew through the same Face.
"""

import threading

import freetype
import pytest

from src import element_style
from src.font_manager import FontManager


def _on_other_thread(fn):
    box = {}

    def run():
        box['value'] = fn()

    t = threading.Thread(target=run)
    t.start()
    t.join(timeout=10)
    assert not t.is_alive()
    return box['value']


def test_font_manager_gives_each_thread_its_own_bdf_face():
    fm = FontManager({})
    here = fm.get_font("five_by_seven", 7)
    there = _on_other_thread(lambda: fm.get_font("five_by_seven", 7))
    assert isinstance(here, freetype.Face) and isinstance(there, freetype.Face)
    assert here is not there
    # Within one thread the face is still reused.
    assert fm.get_font("five_by_seven", 7) is here


def test_font_manager_still_caches_ttf():
    fm = FontManager({})
    here = fm.get_font("press_start", 8)
    assert _on_other_thread(lambda: fm.get_font("press_start", 8)) is here


def test_element_style_gives_each_thread_its_own_bdf_face():
    element_style._font_cache.clear()
    try:
        here = element_style.load_font("5x7.bdf", 7)
        there = _on_other_thread(lambda: element_style.load_font("5x7.bdf", 7))
        assert isinstance(here, freetype.Face) and isinstance(there, freetype.Face)
        assert here is not there
        assert element_style.load_font("5x7.bdf", 7) is here
    finally:
        element_style._font_cache.clear()


def test_element_style_cache_survives_concurrent_eviction(monkeypatch):
    """get() then move_to_end() on the shared OrderedDict raised KeyError when
    another thread evicted the key in between. Forced deterministically: the
    first get() hands the cache to a second thread that fills it past the
    bound before get() returns."""
    from collections import OrderedDict

    owner = threading.get_ident()
    state = {'worker': None}

    class RacingCache(OrderedDict):
        def get(self, key, default=None):
            value = super().get(key, default)
            if state['worker'] is None and threading.get_ident() == owner:
                state['worker'] = threading.Thread(
                    target=lambda: [element_style.load_font(
                        "PressStart2P-Regular.ttf", s) for s in (20, 21, 22)],
                    daemon=True)
                state['worker'].start()
                # Unguarded, the worker evicts `key` now; guarded, it waits
                # for the lock this thread holds.
                state['worker'].join(timeout=0.5)
            return value

    monkeypatch.setattr(element_style, "_FONT_CACHE_MAX", 2)
    cache = RacingCache()
    monkeypatch.setattr(element_style, "_font_cache", cache)
    element_style.load_font("PressStart2P-Regular.ttf", 8)  # populate
    state['worker'] = None
    element_style.load_font("PressStart2P-Regular.ttf", 8)  # hit, raced
    state['worker'].join(timeout=10)
    assert not state['worker'].is_alive()
    assert len(cache) <= 2
