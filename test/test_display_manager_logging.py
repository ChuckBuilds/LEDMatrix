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


def test_debug_output_appears_when_the_root_is_at_debug():
    root = logging.getLogger()
    previous = root.level
    root.setLevel(logging.DEBUG)
    try:
        assert dm.logger.isEnabledFor(logging.DEBUG)
    finally:
        root.setLevel(previous)
