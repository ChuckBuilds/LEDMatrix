"""display_manager's logger must follow the level run.py configures.

The module pinned its own logger to INFO at import, which overrides the root
level, so ``run.py -d`` never showed a single DEBUG line from the display
manager -- the dirty-tracking, fallback-mode and scrolling-state traces were
unreachable without editing the source.
"""

import logging
import os

os.environ.setdefault("EMULATOR", "true")

import src.display_manager as dm


def test_the_module_sets_no_level_of_its_own():
    assert dm.logger.level == logging.NOTSET


def test_scrolling_state_logs_only_when_it_changes(caplog):
    # Vegas and every scrolling plugin set the state on each frame, so a line
    # per call was ~120 DEBUG lines a second once debug output was visible.
    dm_obj = object.__new__(dm.DisplayManager)
    dm_obj._frame_hold = 1
    dm_obj._scrolling_state = {'is_scrolling': False, 'last_scroll_activity': 0}

    with caplog.at_level(logging.DEBUG, logger='src.display_manager'):
        for _ in range(5):
            dm_obj.set_scrolling_state(True, frame_hold=2)
        dm_obj.set_scrolling_state(False)
        dm_obj.set_scrolling_state(False)

    lines = [r.getMessage() for r in caplog.records
             if r.getMessage().startswith('Scrolling state set to')]
    assert lines == ['Scrolling state set to: True', 'Scrolling state set to: False']
    # The state itself still updates on every call.
    assert dm_obj._scrolling_state['is_scrolling'] is False
    assert dm_obj._frame_hold == 1


def test_debug_output_appears_when_the_root_is_at_debug():
    root = logging.getLogger()
    previous = root.level
    root.setLevel(logging.DEBUG)
    try:
        assert dm.logger.isEnabledFor(logging.DEBUG)
    finally:
        root.setLevel(previous)
