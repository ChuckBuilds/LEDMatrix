"""Sports scoreboard base classes.

Formerly the single module ``src/base_classes/sports.py``; now a package so
capabilities can be composed instead of accumulating in one class. See
docs/SPORTS_UNIFICATION.md for the architecture. The import path is
unchanged: ``from src.base_classes.sports import SportsCore`` still works.
"""

from .core import SportsCore
from .modes import SportsLive, SportsRecent, SportsUpcoming

__all__ = [
    "SportsCore",
    "SportsUpcoming",
    "SportsRecent",
    "SportsLive",
]
