"""DisplayManager's deferred-update queue across threads.

defer_update() is called from plugin update() on the update worker thread
while process_deferred_updates() runs on the render thread. Both rebuild the
queue list (TTL filter, [n:] slice) and assign it back, so an append that
landed between one side's read and its assignment used to be dropped.

The race is forced deterministically: _scrolling_state is swapped for a dict
whose first store of 'deferred_updates' (made by the render side) first lets
a worker-thread defer_update() run. Unlocked, the worker appends to the list
the render side is about to overwrite; locked, the worker waits and appends
to the list that was stored.
"""

import os
import sys
import threading

os.environ["EMULATOR"] = "true"

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


@pytest.fixture(scope="module")
def dm():
    from src.display_manager import DisplayManager
    DisplayManager._instance = None
    manager = DisplayManager({
        "display": {
            "hardware": {"rows": 32, "cols": 64, "chain_length": 1,
                         "parallel": 1, "brightness": 90},
            "runtime": {"gpio_slowdown": 0},
        },
    }, suppress_test_pattern=True)
    yield manager
    DisplayManager._instance = None


class _InterleavingState(dict):
    """On the first 'deferred_updates' store from the owning thread, run
    ``interleave`` on another thread before the store happens."""

    def __init__(self, base, interleave):
        super().__init__(base)
        dict.__setitem__(self, 'deferred_updates', [])
        self._owner = threading.get_ident()
        self._interleave = interleave
        self.worker = None

    def __setitem__(self, key, value):
        if (key == 'deferred_updates' and self.worker is None
                and threading.get_ident() == self._owner):
            self.worker = threading.Thread(target=self._interleave, daemon=True)
            self.worker.start()
            # Unlocked code lets the worker finish here; locked code blocks
            # it on the lock this thread holds, so give up waiting quickly.
            self.worker.join(timeout=0.5)
        super().__setitem__(key, value)


def test_defer_from_another_thread_is_not_lost(dm):
    original = dm._scrolling_state
    ran = []  # the callables never run here; they only need to exist
    try:
        state = _InterleavingState(
            original, lambda: dm.defer_update(lambda: ran.append('late')))
        dm._scrolling_state = state
        dm.defer_update(lambda: ran.append('first'))
        state.worker.join(timeout=5)
        assert not state.worker.is_alive()
        assert len(state['deferred_updates']) == 2, (
            "a defer_update() from another thread was lost")
    finally:
        dm._scrolling_state = original
        original['deferred_updates'] = []


def test_process_during_defer_keeps_both(dm):
    original = dm._scrolling_state
    try:
        state = _InterleavingState(
            original, lambda: dm.defer_update(lambda: None))
        dict.__setitem__(state, 'is_scrolling', True)
        dict.__setitem__(state, 'last_scroll_activity', 1e18)  # stays "scrolling"
        dm._scrolling_state = state
        # Render side: only the TTL cleanup runs while scrolling.
        dm.process_deferred_updates()
        state.worker.join(timeout=5)
        assert not state.worker.is_alive()
        assert len(state['deferred_updates']) == 1, (
            "a defer_update() during the render-side cleanup was lost")
    finally:
        dm._scrolling_state = original
        original['deferred_updates'] = []


def test_queued_callable_may_defer_again(dm):
    """The callables run outside the lock, so one that re-defers (a plugin
    retrying later) must not deadlock the render thread."""
    dm._scrolling_state['is_scrolling'] = False
    try:
        dm.defer_update(lambda: dm.defer_update(lambda: None))
        t = threading.Thread(target=dm.process_deferred_updates, daemon=True)
        t.start()
        t.join(timeout=5)
        assert not t.is_alive(), "process_deferred_updates deadlocked"
        assert len(dm._scrolling_state['deferred_updates']) == 1
    finally:
        dm._scrolling_state['deferred_updates'] = []
