"""Opt-in capabilities for the sports scoreboards.

Each module here is a feature that only *some* sports want. They are composed
by inheritance (mixins) or selected by name (strategies) — never enabled by an
``if self.<feature>_enabled:`` branch inside the base classes.

The distinction matters: hockey has no celebrations, so ``HockeyLive`` does not
inherit :class:`~.celebrations.CelebrationMixin` and the celebration code is not
in hockey's MRO at all. A bug in it cannot reach a plugin that never opted in.

See ``docs/SPORTS_UNIFICATION.md`` for the full rationale.
"""

from .celebrations import CelebrationMixin
from .rotation import (
    RotationStrategy,
    SimpleRotation,
    SmoothWeightedRotation,
    WeightedCycleRotation,
    get_rotation_strategy,
    register_rotation_strategy,
)

__all__ = [
    "CelebrationMixin",
    "RotationStrategy",
    "SimpleRotation",
    "SmoothWeightedRotation",
    "WeightedCycleRotation",
    "get_rotation_strategy",
    "register_rotation_strategy",
]
