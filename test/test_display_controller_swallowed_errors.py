"""Errors DisplayController deliberately swallows must still leave a trace.

Several ``except Exception: pass`` blocks hid plugin and filesystem faults
completely; they now log at DEBUG with the traceback, and still don't raise.
"""

import logging
import os
from unittest.mock import MagicMock

os.environ.setdefault("EMULATOR", "true")

from src.display_controller import DisplayController  # noqa: E402


def test_has_live_content_failure_is_logged_not_raised(caplog):
    dc = object.__new__(DisplayController)
    broken = MagicMock()
    broken.has_live_content.side_effect = RuntimeError("feed parse failed")
    dc.plugin_display_modes = {"nfl": ["nfl_live", "nfl_recent"]}
    dc.mode_to_plugin_id = {}
    dc.plugin_modes = {"nfl_live": broken, "nfl_recent": MagicMock()}

    with caplog.at_level(logging.DEBUG, logger="src.display_controller"):
        modes = dc._on_demand_modes_for_plugin("nfl")

    # Behaviour unchanged: the raising live mode counts as having no content.
    assert modes == ["nfl_recent"]
    records = [r for r in caplog.records
               if "has_live_content() failed for nfl_live" in r.getMessage()]
    assert len(records) == 1 and records[0].exc_info is not None
