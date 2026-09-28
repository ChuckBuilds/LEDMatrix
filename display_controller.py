#!/usr/bin/env python3
"""Legacy entry point: runs ``run.py``, which is the one to use.

``python3 run.py`` (``-e`` for the emulator, ``-d`` for debug logging) is how
the display service and the docs start LEDMatrix. This file used to import
``src.display_controller.main`` directly, which skipped what run.py sets up
first -- ``sys.dont_write_bytecode`` (root-owned ``__pycache__`` in plugin
directories blocks the web service from updating them), the ``-e``/``-d``
flags, and the logging configuration. It now runs run.py exactly as
``python3 run.py`` would, with the same arguments.
"""

import os
import runpy

if __name__ == "__main__":
    runpy.run_path(
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "run.py"),
        run_name="__main__",
    )
